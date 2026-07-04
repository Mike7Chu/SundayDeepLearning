"""DART 전자공시(opendart.fss.or.kr) 클라이언트 — 무료 공식 API.

최근 공시 목록(list.json)을 폴링해 관심종목(또는 전 종목) 공시를 빠르게 포착.
키 미설정이면 비활성(idle). 순수 파서(parse_disclosure_list)는 네트워크 없이 테스트.
"""
from __future__ import annotations

import datetime as _dt
import io
import logging
import xml.etree.ElementTree as ET
import zipfile

import httpx

from shared.settings import settings

logger = logging.getLogger(__name__)

_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
_CORP_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
_ALOT_URL = "https://opendart.fss.or.kr/api/alotMatter.json"   # 배당에 관한 사항


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
    """
    if not isinstance(payload, dict) or payload.get("status") != "000":
        return []

    def _num(v) -> float | None:
        try:
            s = str(v).replace(",", "").strip()
            return float(s) if s and s != "-" else None
        except (TypeError, ValueError):
            return None

    rows = [r for r in payload.get("list", []) or []
            if "주당" in (r.get("se") or "") and "현금배당" in (r.get("se") or "")]
    if not rows:
        return []
    pref = [r for r in rows if "보통주" in (r.get("stock_knd") or "")]
    row = (pref or rows)[0]
    out: list[dict] = []
    for key, y in (("thstrm", year), ("frmtrm", year - 1), ("lwfr", year - 2)):
        v = _num(row.get(key))
        if v:
            out.append({"date": str(y), "per_share": v})
    return out


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
