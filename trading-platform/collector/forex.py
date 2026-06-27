"""USD/KRW 환율 수집 (김프 계산 기준).

무료 공개 API(open.er-api.com)를 사용하고, 실패 시 폴백값을 쓴다.
"""
from __future__ import annotations

import logging

import httpx

from shared.settings import settings

logger = logging.getLogger(__name__)

_FX_URL = "https://open.er-api.com/v6/latest/USD"


async def fetch_usdkrw() -> float:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_FX_URL)
            resp.raise_for_status()
            data = resp.json()
            rate = data["rates"]["KRW"]
            return float(rate)
    except Exception as exc:
        logger.warning("FX fetch failed (%s), using fallback %.1f",
                       exc, settings.fx_usdkrw_fallback)
        return settings.fx_usdkrw_fallback
