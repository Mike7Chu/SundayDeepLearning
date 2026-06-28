"""한국투자증권(KIS) OpenAPI 클라이언트 — 국내주식 현재가.

키 미설정이면 비활성(enabled=False). 토큰은 캐시(만료 전 재사용).
모의투자(kis_paper=True) 도메인 기본. 키움 OCX와 달리 Linux/RPi에서 동작.
"""
from __future__ import annotations

import logging
import time
from functools import lru_cache
from pathlib import Path

import httpx
import yaml

from shared.settings import settings

logger = logging.getLogger(__name__)

_REAL = "https://openapi.koreainvestment.com:9443"
_PAPER = "https://openapivts.koreainvestment.com:29443"
_WATCHLIST = Path(__file__).resolve().parent.parent.parent / "config" / "stocks.yaml"


@lru_cache(maxsize=1)
def load_watchlist() -> list[dict]:
    return yaml.safe_load(_WATCHLIST.read_text()).get("watchlist", [])


class KISClient:
    def __init__(self):
        self.base = _PAPER if settings.kis_paper else _REAL
        self._token: str | None = None
        self._exp: float = 0.0

    @property
    def enabled(self) -> bool:
        return bool(settings.kis_app_key and settings.kis_app_secret)

    async def _token_value(self, client: httpx.AsyncClient) -> str:
        if self._token and time.time() < self._exp - 60:
            return self._token
        r = await client.post(f"{self.base}/oauth2/tokenP", json={
            "grant_type": "client_credentials",
            "appkey": settings.kis_app_key,
            "appsecret": settings.kis_app_secret,
        })
        r.raise_for_status()
        d = r.json()
        self._token = d["access_token"]
        self._exp = time.time() + int(d.get("expires_in", 86400))
        return self._token

    async def fetch_price(self, client: httpx.AsyncClient, code: str) -> dict:
        token = await self._token_value(client)
        headers = {
            "authorization": f"Bearer {token}",
            "appkey": settings.kis_app_key,
            "appsecret": settings.kis_app_secret,
            "tr_id": "FHKST01010100",
            "custtype": "P",
        }
        params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code}
        r = await client.get(
            f"{self.base}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=headers, params=params)
        r.raise_for_status()
        return parse_price(r.json().get("output", {}))


def _f(v) -> float | None:
    """문자열 숫자 → float (빈값/None은 None)."""
    try:
        if v in (None, "", "0"):
            return None if v in (None, "") else 0.0
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_price(o: dict) -> dict:
    """KIS inquire-price output → 시세 + 밸류에이션(순수 함수, 테스트 용이).

    inquire-price 응답엔 현재가/전일대비 외 per/pbr/eps/bps도 포함된다.
    """
    return {
        "price": float(o.get("stck_prpr") or 0),       # 현재가
        "change_pct": float(o.get("prdy_ctrt") or 0),  # 전일대비율(%)
        "per": _f(o.get("per")),                        # 주가수익비율
        "pbr": _f(o.get("pbr")),                        # 주가순자산비율
        "eps": _f(o.get("eps")),                        # 주당순이익
        "bps": _f(o.get("bps")),                        # 주당순자산
        "market_cap": _f(o.get("hts_avls")),            # 시가총액(억원)
        "high_52w": _f(o.get("w52_hgpr")),              # 52주 최고
        "low_52w": _f(o.get("w52_lwpr")),               # 52주 최저
    }
