"""DART 전자공시(opendart.fss.or.kr) 클라이언트 — 무료 공식 API.

최근 공시 목록(list.json)을 폴링해 관심종목(또는 전 종목) 공시를 빠르게 포착.
키 미설정이면 비활성(idle). 순수 파서(parse_disclosure_list)는 네트워크 없이 테스트.
"""
from __future__ import annotations

import datetime as _dt
import io
import logging
import re
import xml.etree.ElementTree as ET
import zipfile

import httpx

from shared.settings import settings

logger = logging.getLogger(__name__)

_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
_CORP_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
_ALOT_URL = "https://opendart.fss.or.kr/api/alotMatter.json"   # 배당에 관한 사항
_FNLTT_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"  # 단일회사 주요계정(재무)


def parse_disclosure_list(payload: dict) -> list[dict]:
    """DART list.json 응답 → 공시 리스트(순수 함수). status '000'만 유효."""
    if not isinstance(payload, dict) or payload.get("status") != "000":
        return []
    out: list[dict] = []
    for it in payload.get("list", []) or []:
        rcept = it.get("rcept_no")
        if not rcept:
            continue
        out.append({
            "rcept_no": rcept,
            "corp_name": it.get("corp_name", ""),
            "stock_code": (it.get("stock_code") or "").strip(),
            "report_nm": it.get("report_nm", ""),
            "flr_nm": it.get("flr_nm", ""),                 # 공시제출인
            "rcept_dt": it.get("rcept_dt", ""),
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept}",
        })
    return out


def parse_corp_map(xml_bytes: bytes) -> dict[str, str]:
    """DART CORPCODE.xml → {6자리 종목코드: corp_code} (상장사만, 순수 함수)."""
    out: dict[str, str] = {}
    root = ET.fromstring(xml_bytes)
    for el in root.iter("list"):
        stock = (el.findtext("stock_code") or "").strip()
        corp = (el.findtext("corp_code") or "").strip()
        if len(stock) == 6 and stock.isdigit() and corp:
            out[stock] = corp
    return out


def parse_alot_matter(payload: dict, year: int) -> list[dict]:
    """DART alotMatter(배당에 관한 사항) → 3개년 주당 현금배당 리스트(순수 함수).

    사업보고서 1건에 당기(thstrm)/전기(frmtrm)/전전기(lwfr) 3개년이 담긴다.
    보통주 행 우선. 값의 쉼표 제거. 반환: [{date:"2025", per_share:1444.0}, ...]

    **액면분할 보정**: 같은 보고서의 '주당액면가액' 행으로 연도별 액면가를 비교해,
    분할 전 연도의 주당배당금을 현재 액면 기준으로 환산한다.
    (예: 액면 5,000→500 분할이면 과거 배당 ÷10 — 미보정 시 수익률이 10배 부풀려짐)
    """
    if not isinstance(payload, dict) or payload.get("status") != "000":
        return []

    def _num(v) -> float | None:
        try:
            s = str(v).replace(",", "").strip()
            return float(s) if s and s != "-" else None
        except (TypeError, ValueError):
            return None

    lst = payload.get("list", []) or []
    rows = [r for r in lst
            if "주당" in (r.get("se") or "") and "현금배당" in (r.get("se") or "")]
    if not rows:
        return []
    pref = [r for r in rows if "보통주" in (r.get("stock_knd") or "")]
    row = (pref or rows)[0]
    par_rows = [r for r in lst if "액면가" in (r.get("se") or "")]
    par = par_rows[0] if par_rows else {}
    par_cur = _num(par.get("thstrm"))
    out: list[dict] = []
    for key, y in (("thstrm", year), ("frmtrm", year - 1), ("lwfr", year - 2)):
        v = _num(row.get(key))
        if not v:
            continue
        par_y = _num(par.get(key))
        if par_cur and par_y and par_y > 0 and par_cur != par_y:
            v = v * (par_cur / par_y)     # 액면분할/병합 환산(현재 액면 기준)
        out.append({"date": str(y), "per_share": round(v, 2)})
    return out


def parse_net_income_growth(payload: dict) -> float | None:
    """fnlttSinglAcnt → 순이익 YoY 성장률 %(순수 함수). 연결(CFS) 우선.

    성장 변곡점 판별용 — 트레일링 PER 함정(이익 급증기에 비싸 보임) 보정.
    """
    if not isinstance(payload, dict) or payload.get("status") != "000":
        return None

    def _num(v) -> float | None:
        try:
            s = str(v).replace(",", "").strip()
            return float(s) if s and s != "-" else None
        except (TypeError, ValueError):
            return None

    rows = [r for r in payload.get("list", []) or []
            if (r.get("account_nm") or "").strip() == "당기순이익"]
    if not rows:
        return None
    pref = [r for r in rows if r.get("fs_div") == "CFS"] or rows   # 연결 우선
    row = pref[0]
    cur, prev = _num(row.get("thstrm_amount")), _num(row.get("frmtrm_amount"))
    if cur is None or not prev:
        return None
    return round((cur - prev) / abs(prev) * 100, 1)


def quarter_candidates(today: _dt.date) -> list[tuple[str, int, str]]:
    """오늘 날짜 기준, 공시됐을 가장 최근 분기보고서 후보(최신→과거, 순수 함수).

    공시 마감: 1Q=5월중순, 반기=8월중순, 3Q=11월중순. 후보를 순서대로 조회해
    데이터 있는 첫 분기를 쓰므로(폴백 안전) 마감 달부터 공격적으로 시도 —
    예: 8월 중순 반기보고서가 뜨는 즉시 2Q 실적이 반영된다.
    reprt_code: 11013=1분기, 11012=반기, 11014=3분기.
    """
    y, m = today.year, today.month
    if m >= 11:
        return [("11014", y, f"{y}.3Q"), ("11012", y, f"{y}.2Q")]
    if m >= 8:
        return [("11012", y, f"{y}.2Q"), ("11013", y, f"{y}.1Q")]
    if m >= 5:
        return [("11013", y, f"{y}.1Q"), ("11014", y - 1, f"{y-1}.3Q")]
    return [("11014", y - 1, f"{y-1}.3Q"), ("11012", y - 1, f"{y-1}.2Q")]


# 잠정실적(공정공시) 제목 패턴 — 정기보고서(45일 뒤)보다 먼저 나오는 최신 실적 신호.
# 예: "연결재무제표기준영업(잠정)실적(공정공시)", "영업(잠정)실적(공정공시)"
_FLASH_RE = re.compile(r"잠정.{0,3}실적|실적.{0,3}잠정")


def find_earnings_flash(disclosures: list[dict], code: str) -> dict | None:
    """최근 공시 목록에서 해당 종목의 최신 '잠정실적' 공시 1건(순수 함수).

    실적발표 시즌엔 분기 마감 직후 잠정실적이 먼저 뜨므로, 정기보고서 기반
    분기 YoY보다 최신 신호로 AI 리서치·화면에 표시한다. 없으면 None.
    """
    for d in disclosures:   # dart:recent는 최신순
        if (d.get("stock_code") == code
                and _FLASH_RE.search(d.get("report_nm") or "")):
            return {"title": d.get("report_nm", ""), "date": d.get("rcept_dt", ""),
                    "url": d.get("url", "")}
    return None


class DartClient:
    @property
    def enabled(self) -> bool:
        return bool(settings.dart_api_key)

    async def fetch_corp_map(self, client: httpx.AsyncClient) -> dict[str, str]:
        """종목코드→corp_code 매핑(zip 다운로드, 주 1회 캐시 권장)."""
        r = await client.get(_CORP_URL, params={"crtfc_key": settings.dart_api_key},
                             timeout=15)
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            xml = z.read(z.namelist()[0])
        return parse_corp_map(xml)

    async def fetch_dividend_years(self, client: httpx.AsyncClient,
                                   corp_code: str, year: int) -> list[dict]:
        """사업보고서(11011) 기준 3개년 주당 현금배당. 타임아웃 5초(무한대기 금지)."""
        params = {"crtfc_key": settings.dart_api_key, "corp_code": corp_code,
                  "bsns_year": str(year), "reprt_code": "11011"}
        r = await client.get(_ALOT_URL, params=params, timeout=5)
        r.raise_for_status()
        return parse_alot_matter(r.json(), year)

    async def fetch_net_income_growth(self, client: httpx.AsyncClient,
                                      corp_code: str, year: int) -> float | None:
        """사업보고서 기준 순이익 YoY 성장률 %. 타임아웃 5초."""
        params = {"crtfc_key": settings.dart_api_key, "corp_code": corp_code,
                  "bsns_year": str(year), "reprt_code": "11011"}
        r = await client.get(_FNLTT_URL, params=params, timeout=5)
        r.raise_for_status()
        return parse_net_income_growth(r.json())

    async def fetch_quarterly_growth(self, client: httpx.AsyncClient,
                                     corp_code: str,
                                     today: _dt.date | None = None) -> dict | None:
        """가장 최근 분기보고서 기준 순이익 YoY(전년 동기 대비).

        연간(작년 사업보고서)보다 최신 실적을 반영 — 예: 2026.7월이면 2026.1Q.
        반환 {"growth": %, "label": "2026.1Q"} 또는 None(미공시/무데이터).
        """
        for reprt, y, label in quarter_candidates(today or _dt.date.today()):
            params = {"crtfc_key": settings.dart_api_key, "corp_code": corp_code,
                      "bsns_year": str(y), "reprt_code": reprt}
            r = await client.get(_FNLTT_URL, params=params, timeout=5)
            r.raise_for_status()
            g = parse_net_income_growth(r.json())
            if g is not None:
                return {"growth": g, "label": label}
        return None

    async def fetch_recent(self, client: httpx.AsyncClient, page_count: int = 100) -> list[dict]:
        """오늘자 최근 공시(전 종목) 목록. 최신순으로 page_count건."""
        today = _dt.date.today().strftime("%Y%m%d")
        params = {
            "crtfc_key": settings.dart_api_key,
            "bgn_de": today, "end_de": today,
            "page_no": 1, "page_count": page_count, "sort": "date", "sort_mth": "desc",
        }
        r = await client.get(_LIST_URL, params=params)
        r.raise_for_status()
        return parse_disclosure_list(r.json())


def format_disclosure(d: dict) -> str:
    """텔레그램 알림 문구."""
    name = d.get("corp_name") or d.get("stock_code") or ""
    return f"📢공시 {name}\n{d.get('report_nm','')}\n{d.get('url','')}"
