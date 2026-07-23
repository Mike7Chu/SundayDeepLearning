"""미국 주요 종목 유니버스 — config/us_stocks.yaml 로드.

국내(KIS 종목마스터)와 달리 미국은 전 종목 마스터가 없으므로 주요 종목을
큐레이션 목록으로 관리(사용자 편집 가능). 토스 US 티커로 시세·일봉 수집.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_US = Path(__file__).resolve().parent.parent.parent / "config" / "us_stocks.yaml"


def parse_adr_map(s: str) -> list[dict]:
    """ADR 매핑 문자열 파싱(순수 함수).

    형식: "본주코드:후보티커1|후보티커2:비율, ..." 예) "000660:SKH|HXSCL:1"
    비율 = 1 ADR당 본주 주식 수(모르면 1로 두고 괴리율 표시에 '비율 확인' 주석).
    """
    out: list[dict] = []
    for item in (s or "").split(","):
        parts = [p.strip() for p in item.strip().split(":")]
        if len(parts) < 2 or not parts[0]:
            continue
        cands = [c.strip().upper() for c in parts[1].split("|") if c.strip()]
        if not cands:
            continue
        try:
            ratio = float(parts[2]) if len(parts) > 2 and parts[2] else 1.0
        except ValueError:
            ratio = 1.0
        out.append({"code": parts[0], "cands": cands, "ratio": ratio or 1.0})
    return out


# KIS 해외주식 주문의 OVRS_EXCG_CD — 티커별 상장 거래소. 기본 NASD, 예외만 명시.
# (뉴욕거래소 상장 / NYSE Arca ETF는 KIS에서 AMEX 코드로 주문.)
_NYSE = {
    "JPM", "BAC", "WFC", "GS", "MS", "C", "V", "MA", "AXP", "BLK", "SCHW",
    "KO", "PG", "WMT", "HD", "MCD", "NKE", "DIS", "XOM", "CVX", "COP",
    "JNJ", "LLY", "ABBV", "MRK", "PFE", "UNH", "TMO", "ABT", "CRM", "ORCL",
    "IBM", "GE", "BA", "CAT", "HON", "LMT", "RTX", "DE", "UPS", "FDX", "UNP",
    "T", "VZ", "NVO", "TSM", "SHOP", "UBER", "NOW", "SPOT", "RBLX", "GM",
    "F", "TGT", "DELL", "SNOW", "PM", "RIVN",
}
_AMEX = {                                            # NYSE Arca ETF 등
    "SPY", "VOO", "VTI", "DIA", "IWM", "SCHD", "JEPI", "JEPQ", "VYM",
    "SMH", "VUG", "ARKK", "TLT",
}


def kis_exchange(ticker: str, override: dict | None = None) -> str:
    """티커 → KIS OVRS_EXCG_CD(NASD/NYSE/AMEX). 미상은 NASD 기본(순수 함수).

    override(설정 KIS_US_EXCHANGE_MAP)가 있으면 최우선 — 거래소 오분류를
    코드 수정 없이 .env로 교정할 수 있다(모의 테스트에서 거부되면 여기 추가).
    """
    t = (ticker or "").upper()
    if override and t in override:
        return override[t]
    if t in _NYSE:
        return "NYSE"
    if t in _AMEX:
        return "AMEX"
    return "NASD"


def parse_exchange_override(s: str) -> dict:
    """"NVDA:NASD,JPM:NYSE" → {NVDA:NASD, JPM:NYSE} (순수 함수)."""
    out: dict = {}
    for item in (s or "").split(","):
        parts = [p.strip() for p in item.split(":")]
        if len(parts) == 2 and parts[0] and parts[1]:
            out[parts[0].upper()] = parts[1].upper()
    return out


@lru_cache(maxsize=1)
def load_us_universe() -> list[dict]:
    """[{code, name, market:"US"}] — 파일 없거나 손상이면 빈 리스트(안전)."""
    try:
        items = yaml.safe_load(_US.read_text()).get("us_stocks", [])
    except Exception:
        return []
    out: list[dict] = []
    for it in items or []:
        code = str((it or {}).get("code", "")).strip().upper()
        if code:
            out.append({"code": code, "name": (it.get("name") or "").strip(),
                        "market": "US"})
    return out
