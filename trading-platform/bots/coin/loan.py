"""론(차입) 페이퍼봇 — 차입금으로 아비트라지 진입.

진입 자금을 차입(loan/마진)해 갭이 큰 기회에 진입한다고 가정. 마진봇과 달리 숏 다리
제약 없이 순스프레드(net_gap)만 보고, **차입 비용(borrow_cost_pct)**을 PnL에서 차감한다.
PnL(%) ≈ 진입갭 − 현재갭 − 차입비용. 전부 dry-run.
"""
from __future__ import annotations

from api.services.arbitrage import compute_arbitrage
from bots.execution_gateway import ExecutionGateway
from bots.framework import BotBase


class LoanPaperBot(BotBase):
    name = "loan"
    interval_sec = 5.0

    def __init__(self, redis, entry_gap=1.5, exit_gap=0.3, borrow_cost_pct=0.1,
                 min_volume=0.0, exchanges=None):
        super().__init__(redis)
        self.entry_gap, self.exit_gap = entry_gap, exit_gap
        self.borrow_cost_pct, self.min_volume = borrow_cost_pct, min_volume
        self.exchanges = exchanges or []
        self.gw = ExecutionGateway(redis, self.name, dry_run=True)

    async def step(self) -> None:
        s = await self.get_settings({"entry_gap": self.entry_gap, "exit_gap": self.exit_gap,
                                     "borrow_cost_pct": self.borrow_cost_pct,
                                     "min_volume": self.min_volume, "exchanges": self.exchanges})
        allow = set(s["exchanges"])
        d = await compute_arbitrage(self.redis, min_gap_pct=0.0, min_volume=s["min_volume"])
        held = await self.gw.position_coins()
        pos = {p["coin"]: p for p in await self.gw.positions()}
        for row in d["rows"]:
            coin = row["coin"]
            if allow and not ({row["long"]["exchange"], row["short"]["exchange"]} <= allow):
                continue
            net = row.get("net_gap_pct")
            if net is None:
                net = row["gap_pct"]
            if coin not in held and net >= s["entry_gap"]:
                await self.gw.open_paper(coin, {
                    "entry_gap": round(net, 4), "borrowed": True,
                    "long": row["long"]["exchange"], "short": row["short"]["exchange"]})
            elif coin in held and net <= s["exit_gap"]:
                entry = pos.get(coin, {}).get("entry_gap", net)
                pnl = entry - net - s["borrow_cost_pct"]   # 차입비용 차감
                await self.gw.close_paper(coin, pnl, {"exit_gap": round(net, 4)})
