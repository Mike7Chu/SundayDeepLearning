"""현선 페이퍼봇.

국내현물 vs 해외선물(perp) 역프가 진입 임계치 이하면 가상 진입(국내현물 매수 +
해외선물 숏), 갭이 청산 임계치 이상으로 회복되면 가상 청산하고 PnL을 기록한다.
PnL(%) ≈ 현재선물김프 − 진입선물김프 (역프가 0으로 수렴할수록 이익).
전부 dry-run(페이퍼) — 실제 주문 없음.
"""
from __future__ import annotations

from api.services.premium import compute_premium
from bots.execution_gateway import ExecutionGateway
from bots.framework import BotBase


class HyeonseonPaperBot(BotBase):
    name = "hyeonseon"
    interval_sec = 5.0

    def __init__(self, redis, base="upbit", ref="binance",
                 entry_pct=-1.0, exit_pct=0.0):
        super().__init__(redis)
        self.base, self.ref = base, ref
        self.entry_pct, self.exit_pct = entry_pct, exit_pct
        self.gw = ExecutionGateway(redis, self.name, dry_run=True)

    async def step(self) -> None:
        s = await self.get_settings({"base": self.base, "ref": self.ref,
                                     "entry_pct": self.entry_pct, "exit_pct": self.exit_pct})
        cells = await compute_premium(self.redis, s["base"], s["ref"])
        held = await self.gw.position_coins()
        pos = {p["coin"]: p for p in await self.gw.positions()}
        for c in cells:
            pp = c.premium_perp_pct
            if pp is None:
                continue
            if c.coin not in held and pp <= s["entry_pct"]:
                await self.gw.open_paper(c.coin, {
                    "entry_perp_pct": pp, "base": s["base"], "ref": s["ref"]})
            elif c.coin in held and pp >= s["exit_pct"]:
                entry = pos.get(c.coin, {}).get("entry_perp_pct", pp)
                pnl = pp - entry   # 역프(-)에서 0으로 회복 → 양(+) 이익
                await self.gw.close_paper(c.coin, pnl, {"exit_perp_pct": pp})
