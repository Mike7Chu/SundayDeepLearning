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


class DartQuotaExceeded(Exception):
    """DART 일일 호출한도 초과(status 020). 그날은 재시도 중단 신호."""


_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
_CORP_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
_ALOT_URL = "https://opendart.fss.or.kr/api/alotMatter.json"   # 배당에 관한 사항
_FNLTT_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"  # 단일회사 주요계정(재무)
_FNLTT_ALL_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"  # 전체 재무제표(현금흐름표 포함)


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


def _fnum(v) -> float | None:
    try:
        s = str(v).replace(",", "").strip()
        return float(s) if s and s != "-" else None
    except (TypeError, ValueError):
        return None


def _acct(rows: list, name: str, field: str = "thstrm_amount") -> float | None:
    """주요계정에서 account_nm이 name과 일치(공백 무시)하는 행의 금액. 연결 우선."""
    hit = [r for r in rows
           if (r.get("account_nm") or "").replace(" ", "") == name.replace(" ", "")]
    if not hit:
        return None
    pref = [r for r in hit if r.get("fs_div") == "CFS"] or hit
    return _fnum(pref[0].get(field))


def parse_financials(payload: dict) -> dict:
    """fnlttSinglAcnt(주요계정) → 재무 건전성·성장 지표 묶음(순수 함수).

    한 번의 호출로 부채비율·순이익/매출/영업이익 YoY를 함께 추출(연결 우선).
    반환 {debt_ratio, ni_yoy, rev_yoy, op_yoy} — 각 미상은 None.
    부채비율 = 부채총계/자본총계 ×100 (100 미만이면 무차입 우량 신호).
    """
    out = {"debt_ratio": None, "ni_yoy": None, "rev_yoy": None, "op_yoy": None,
           "equity": None, "ni": None, "ni_prev": None}
    if not isinstance(payload, dict) or payload.get("status") != "000":
        return out
    rows = payload.get("list") or []
    debt, equity = _acct(rows, "부채총계"), _acct(rows, "자본총계")
    out["equity"] = equity                             # 자본총계(원) — ROE·BPS 분모
    out["ni"] = _acct(rows, "당기순이익", "thstrm_amount")       # 당기(연간=연NI/분기=YTD누적)
    out["ni_prev"] = _acct(rows, "당기순이익", "frmtrm_amount")  # 전년 동기(분기면 전년 YTD)
    if debt is not None and equity and equity > 0:
        out["debt_ratio"] = round(debt / equity * 100, 1)

    def _yoy(name: str) -> float | None:
        cur = _acct(rows, name, "thstrm_amount")
        prev = _acct(rows, name, "frmtrm_amount")
        if cur is None or not prev:
            return None
        return round((cur - prev) / abs(prev) * 100, 1)

    out["ni_yoy"] = _yoy("당기순이익")
    out["rev_yoy"] = _yoy("매출액")
    out["op_yoy"] = _yoy("영업이익")
    return out


def ttm_fundamentals(annual_ni: float | None, q_cum_ni: float | None,
                     q_prev_cum_ni: float | None, equity: float | None,
                     market_cap_eok: float | None,
                     price: float | None) -> dict:
    """최근 4개 분기(TTM) 기준 EPS·ROE·PER·BPS 계산(순수 함수).

    KIS 시세의 EPS/BPS는 '직전 사업보고서(연간)' 기준이라 올해 분기 실적이 반영
    안 됨. DART 분기 누적으로 TTM 순이익을 이어붙여 '현재' 밸류에이션을 만든다:

        TTM 순이익 = 작년 연간 − 작년 동기 누적 + 올해 동기 누적
        발행주식수 = 시가총액(원) / 현재가
        EPS_ttm = TTM순이익/주식수,  ROE_ttm = TTM순이익/자본총계×100
        PER_ttm = 현재가/EPS_ttm,    BPS_ttm = 자본총계/주식수

    분기 데이터가 없으면(미공시) TTM 대신 연간값으로 폴백하도록 빈 dict 반환.
    반환 {eps_ttm, roe_ttm, per_ttm, bps_ttm, ni_ttm_eok} — 계산 가능한 것만.
    """
    if not (annual_ni is not None and q_cum_ni is not None
            and q_prev_cum_ni is not None):
        return {}
    ttm_ni = annual_ni - q_prev_cum_ni + q_cum_ni      # 원
    out: dict = {"ni_ttm_eok": round(ttm_ni / 1e8, 1)}
    shares = None
    if market_cap_eok and price and price > 0:
        shares = market_cap_eok * 1e8 / price          # 시총(억원→원)/현재가
    if shares and shares > 0:
        eps = ttm_ni / shares
        out["eps_ttm"] = round(eps, 1)
        if eps > 0 and price:
            out["per_ttm"] = round(price / eps, 2)     # 적자면 PER 무의미 → 생략
        if equity and equity > 0:
            out["bps_ttm"] = round(equity / shares, 1)
    if equity and equity > 0:
        out["roe_ttm"] = round(ttm_ni / equity * 100, 1)
    return out


# 현금흐름표 계정명 편차 대응(회사마다 표기 상이)
_OCF_NAMES = ("영업활동현금흐름", "영업활동으로인한현금흐름", "영업활동순현금흐름")
_CAPEX_NAMES = ("유형자산의취득", "유형자산의증가", "유형자산취득")


def parse_fcf(payload: dict) -> float | None:
    """fnlttSinglAcntAll(전체 재무제표) → 잉여현금흐름(FCF, 억원) 근사(순수 함수).

    FCF = 영업활동현금흐름 − 유형자산 취득(CAPEX 근사). 버핏의 '주주이익' 프록시.
    계정명 표기가 회사마다 달라 후보군으로 매칭, 못 찾으면 None(무리한 추정 금지).
    """
    if not isinstance(payload, dict) or payload.get("status") != "000":
        return None
    rows = payload.get("list") or []

    def _find(names) -> float | None:
        for r in rows:
            nm = (r.get("account_nm") or "").replace(" ", "")
            if any(nm == n for n in names):
                pref = _fnum(r.get("thstrm_amount"))
                if pref is not None:
                    return pref
        return None

    ocf = _find(_OCF_NAMES)
    capex = _find(_CAPEX_NAMES)
    if ocf is None:
        return None
    fcf = ocf - (capex or 0.0)
    return round(fcf / 1e8, 1)   # 원 → 억원


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


def parse_flash_figures(text: str) -> dict | None:
    """잠정실적 공시 본문 텍스트 → 전년동기 대비 증감율(순수 함수).

    표준 공정공시 표: 각 계정(매출액/영업이익/당기순이익) 행이
    [당해실적, 전기실적, 전기대비증감율, 전년동기실적, 전년동기대비증감율]
    순서 — 5번째 수치가 YoY%. 음수 표기 '△'/'-' 처리. 못 찾으면 None.
    """
    if not text:
        return None

    def yoy(keyword: str, stop: tuple[str, ...]) -> float | None:
        i = text.find(keyword)
        if i < 0:
            return None
        seg = text[i + len(keyword):]
        ends = [seg.find(s) for s in stop if seg.find(s) > 0]
        if ends:
            seg = seg[:min(ends)]
        toks = re.findall(r"[△\-–－]?\d[\d,]*(?:\.\d+)?", seg)
        if len(toks) < 5:
            return None
        t = toks[4].replace(",", "")
        neg = t[0] in "△-–－"
        try:
            v = float(t.lstrip("△-–－"))
        except ValueError:
            return None
        return round(-v if neg else v, 1)

    out = {
        "rev_yoy": yoy("매출액", ("영업이익",)),
        "op_yoy": yoy("영업이익", ("법인세", "당기순이익")),
        "ni_yoy": yoy("당기순이익", ("지배기업", "※", "정보제공")),
    }
    return out if any(v is not None for v in out.values()) else None


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
        try:
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                xml = z.read(z.namelist()[0])
        except zipfile.BadZipFile:
            # zip이 아니면 에러 XML(status/message) — 020=한도초과는 별도 신호로 올림.
            if b"<status>020</status>" in r.content:
                raise DartQuotaExceeded("DART 일일 호출한도 초과(020)")
            raise
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

    async def fetch_financials(self, client: httpx.AsyncClient,
                               corp_code: str, year: int,
                               reprt: str = "11011") -> dict:
        """주요계정 1회 호출로 부채비율·순이익/매출/영업이익 YoY 묶음. 타임아웃 5초."""
        params = {"crtfc_key": settings.dart_api_key, "corp_code": corp_code,
                  "bsns_year": str(year), "reprt_code": reprt}
        r = await client.get(_FNLTT_URL, params=params, timeout=5)
        r.raise_for_status()
        return parse_financials(r.json())

    async def fetch_fcf(self, client: httpx.AsyncClient, corp_code: str,
                        year: int) -> float | None:
        """사업보고서(전체 재무제표) 기준 잉여현금흐름(FCF, 억원). 타임아웃 8초."""
        params = {"crtfc_key": settings.dart_api_key, "corp_code": corp_code,
                  "bsns_year": str(year), "reprt_code": "11011", "fs_div": "CFS"}
        r = await client.get(_FNLTT_ALL_URL, params=params, timeout=8)
        r.raise_for_status()
        fcf = parse_fcf(r.json())
        if fcf is None:                      # 연결 없으면 별도(OFS) 재시도
            params["fs_div"] = "OFS"
            r = await client.get(_FNLTT_ALL_URL, params=params, timeout=8)
            r.raise_for_status()
            fcf = parse_fcf(r.json())
        return fcf

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
            payload = r.json()
            g = parse_net_income_growth(payload)
            if g is not None:
                fin = parse_financials(payload)   # 같은 payload에서 raw 금액(TTM 계산용)
                return {"growth": g, "label": label,
                        "ni_cum": fin.get("ni"), "ni_prev_cum": fin.get("ni_prev"),
                        "equity": fin.get("equity")}
        return None

    async def fetch_flash_figures(self, client: httpx.AsyncClient,
                                  rcept_no: str) -> dict | None:
        """잠정실적 공시 원문(document.xml zip)에서 YoY 수치 추출.

        정기보고서(45일 뒤)를 기다리지 않고 발표 당일 실적을 점수·AI에 반영.
        """
        r = await client.get("https://opendart.fss.or.kr/api/document.xml",
                             params={"crtfc_key": settings.dart_api_key,
                                     "rcept_no": rcept_no}, timeout=15)
        r.raise_for_status()
        try:
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                raw = b"".join(z.read(n) for n in z.namelist())
        except zipfile.BadZipFile:
            return None
        for enc in ("utf-8", "euc-kr", "cp949"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", errors="ignore")
        return parse_flash_figures(re.sub(r"<[^>]+>", " ", text))

    async def fetch_recent(self, client: httpx.AsyncClient, page_count: int = 100,
                           days_back: int = 3) -> list[dict]:
        """최근 공시(전 종목) 목록 — 최근 days_back일, 최신순 page_count건.

        오늘만 보면 재시작·장애 사이에 발표된 공시(예: 어제 잠정실적)를 영영
        놓친다 → 3일 범위로 조회(중복은 dart:seen이 걸러 알림 재발송 없음).
        """
        today = _dt.date.today()
        params = {
            "crtfc_key": settings.dart_api_key,
            "bgn_de": (today - _dt.timedelta(days=days_back)).strftime("%Y%m%d"),
            "end_de": today.strftime("%Y%m%d"),
            "page_no": 1, "page_count": page_count, "sort": "date", "sort_mth": "desc",
        }
        r = await client.get(_LIST_URL, params=params)
        r.raise_for_status()
        return parse_disclosure_list(r.json())


def format_disclosure(d: dict) -> str:
    """텔레그램 알림 문구."""
    name = d.get("corp_name") or d.get("stock_code") or ""
    return f"📢공시 {name}\n{d.get('report_nm','')}\n{d.get('url','')}"
