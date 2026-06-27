"""입출금 상태 어댑터 — 거래소별 코인 입금/출금 가능 여부.

ccxt fetch_currencies()로 코인별 deposit/withdraw 가능여부를 받는다. 거래소·버전에
따라 지원/필드가 다르므로 방어적으로 파싱하고, 미지원/실패 시 빈 결과를 반환한다.
느리게 변하므로 별도(긴) 주기로 수집한다.
"""
from __future__ import annotations

import logging

from shared.schemas import ExchangeConfig
from shared.symbols import is_leveraged_token

logger = logging.getLogger(__name__)


def _flag(value) -> bool | None:
    if value is None:
        return None
    return bool(value)


class WalletAdapter:
    def __init__(self, cfg: ExchangeConfig):
        import ccxt.async_support as ccxt  # 지연 임포트

        self.cfg = cfg
        klass = getattr(ccxt, cfg.ccxt_id)
        self.client = klass({"enableRateLimit": True})

    async def fetch(self) -> dict[str, dict]:
        """coin -> {deposit, withdraw}. 값이 None이면 unknown."""
        out: dict[str, dict] = {}
        if not self.client.has.get("fetchCurrencies"):
            return out
        try:
            currencies = await self.client.fetch_currencies()
        except Exception as exc:
            logger.warning("[%s wallet] fetch_currencies 실패: %s", self.cfg.name, exc)
            return out
        if not currencies:
            return out
        for code, info in currencies.items():
            if is_leveraged_token(code):
                continue
            out[code.upper()] = {
                "deposit": _flag(info.get("deposit")),
                "withdraw": _flag(info.get("withdraw")),
            }
        return out

    async def close(self) -> None:
        await self.client.close()
