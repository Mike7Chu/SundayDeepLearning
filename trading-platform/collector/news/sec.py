"""SEC EDGAR 클라이언트 — 미국판 DART (공식·무료·키 불필요).

- companyfacts(XBRL): 분기 순이익(NetIncomeLoss) → 분기 YoY 성장(국내 DART와 동형)
- submissions: 최근 공시 목록 → 8-K Item 2.02(실적 발표) 감지 = 미국판 잠정실적 배지
- company_tickers.json: 티커 → CIK 매핑(국내 corp_code 매핑과 동형, 7일 캐시)
SEC 정책: User-Agent에 연락처 명시 권장, 초당 10요청 이하. 순수 파서는 오프라인 테스트.
"""
from __future__ import annotations

import datetime as _dt
import logging
import re

import httpx

from shared.settings import settings

logger = logging.getLogger(__name__)

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
_SUBS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

_FRAME_RE = re.compile(r"^CY(\d{4})Q([1-4])$")   # 분기 프레임(연간 CY2025, 순간 …I 제외)
# 순이익 태그 후보(회사별 표기 편차)
_NI_TAGS = ("NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic",
            "ProfitLoss")


def _headers() -> dict:
    return {"User-Agent": settings.sec_user_agent,
            "Accept-Encoding": "gzip, deflate"}


def parse_ticker_map(payload) -> dict[str, int]:
    """company_tickers.json → {티커: CIK}(순수 함수). 형식: {"0":{cik_str,ticker,...}}"""
    out: dict[str, int] = {}
    if not isinstance(payload, dict):
        return out
    for v in payload.values():
        if isinstance(v, dict) and v.get("ticker") and v.get("cik_str"):
            try:
                out[str(v["ticker"]).upper()] = int(v["cik_str"])
            except (TypeError, ValueError):
                continue
    return out


def parse_quarterly_net_income(facts: dict) -> dict | None:
    """companyfacts → 최신 분기 순이익 YoY(순수 함수). 반환 {"growth": %, "label"}.

    XBRL 'frame'(CY2026Q1 등)은 SEC가 중복 제거한 달력 분기 값 — 최신 분기와
    전년 동일 분기를 비교한다(국내 fetch_quarterly_growth와 동일 의미).
    """
    if not isinstance(facts, dict):
        return None
    gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    for tag in _NI_TAGS:
        units = ((gaap.get(tag) or {}).get("units") or {}).get("USD") or []
        frames: dict[tuple[int, int], float] = {}
        for it in units:
            m = _FRAME_RE.match(it.get("frame") or "")
            if not m or it.get("val") is None:
                continue
            frames[(int(m.group(1)), int(m.group(2)))] = float(it["val"])
        if not frames:
            continue
        y, q = max(frames)                      # 최신 분기
        cur, prev = frames[(y, q)], frames.get((y - 1, q))
        if prev is None or prev == 0:
            continue
        return {"growth": round((cur - prev) / abs(prev) * 100, 1),
                "label": f"{y}.{q}Q"}
    return None


def find_us_earnings_flash(subs: dict, today: _dt.date | None = None,
                           days: int = 10) -> dict | None:
    """submissions → 최근 실적 공시(순수 함수). 미국판 잠정실적 배지.

    8-K 중 Item 2.02(Results of Operations = 실적 발표)와 10-Q(분기보고서)를
    최근 days일 이내에서 찾는다. 반환 {"title","date","url"} 또는 None.
    """
    if not isinstance(subs, dict):
        return None
    today = today or _dt.date.today()
    recent = ((subs.get("filings") or {}).get("recent")) or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    items = recent.get("items") or []
    accnos = recent.get("accessionNumber") or []
    cik = subs.get("cik")
    for i, form in enumerate(forms):
        d = dates[i] if i < len(dates) else ""
        try:
            fdate = _dt.date.fromisoformat(d)
        except (TypeError, ValueError):
            continue
        if (today - fdate).days > days:
            break                                # 최신순 — 범위 벗어나면 종료
        item = items[i] if i < len(items) else ""
        is_earnings = (form == "10-Q"
                       or (form == "8-K" and "2.02" in (item or "")))
        if not is_earnings:
            continue
        acc = (accnos[i] if i < len(accnos) else "").replace("-", "")
        url = (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
               f"&CIK={cik}&type={form}&dateb=&owner=include&count=10")
        if acc and cik is not None:
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}"
        title = ("분기보고서(10-Q) 제출" if form == "10-Q"
                 else "실적 발표(8-K, Results of Operations)")
        return {"title": title, "date": d, "url": url}
    return None


class SecClient:
    """EDGAR HTTP 래퍼 — 호출부에서 페이싱(0.5s+) 유지."""

    async def fetch_ticker_map(self, client: httpx.AsyncClient) -> dict[str, int]:
        r = await client.get(_TICKERS_URL, headers=_headers(), timeout=20)
        r.raise_for_status()
        return parse_ticker_map(r.json())

    async def fetch_quarterly_growth(self, client: httpx.AsyncClient,
                                     cik: int) -> dict | None:
        r = await client.get(_FACTS_URL.format(cik=cik), headers=_headers(),
                             timeout=25)
        r.raise_for_status()
        return parse_quarterly_net_income(r.json())

    async def fetch_earnings_flash(self, client: httpx.AsyncClient,
                                   cik: int) -> dict | None:
        r = await client.get(_SUBS_URL.format(cik=cik), headers=_headers(),
                             timeout=20)
        r.raise_for_status()
        return find_us_earnings_flash(r.json())
