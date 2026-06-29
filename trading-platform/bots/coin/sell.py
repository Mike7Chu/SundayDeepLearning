"""매도 페이퍼봇 — 김프(국내 프리미엄) 익절.

저김프/역프(premium ≤ buy_pct)에 가상 매수(국내 매집)하고, 김프가 매도 임계치
(premium ≥ sell_pct) 이상으로 오르면 가상 매도(프리미엄에 익절). 테더 기준 프리미엄 사용.
PnL(%) ≈ 매도 시 김프 − 매수 시 김프. 전부 dry-run.
"""
from __future__ import annotations

from api.services.premium import compute_premium
from bots.execution_gateway import ExecutionGateway
from bots.framework import BotBase


class SellPaperBot(BotBase):
    name = "sell"
    interval_sec = 5.0

    def __init__(self, redis, base="upbit", ref="binance", buy_pct=0.0, sell_pct=3.0):
        super().__init__(redis)
        self.base, self.ref = base, ref
        self.buy_pct, self.sell_pct = buy_pct, sell_pct
        self.gw = ExecutionGateway(redis, self.name, dry_run=True)

    async def step(self) -> None:
        cells = await compute_premium(self.redis, self.base, self.ref)
        held = await self.gw.position_coins()
        pos = {p["coin"]: p for p in await self.gw.positions()}
        for c in cells:
            p = c.premium_pct   # 테더 기준 김프
            if c.coin not in held and p <= self.buy_pct:
                await self.gw.open_paper(c.coin, {"entry_premium": round(p, 4),
                                                  "base": self.base, "ref": self.ref})
            elif c.coin in held and p >= self.sell_pct:
                entry = pos.get(c.coin, {}).get("entry_premium", p)
                await self.gw.close_paper(c.coin, p - entry, {"sell_premium": round(p, 4)})
