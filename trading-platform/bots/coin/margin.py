"""마진 페이퍼봇 — 해외 거래소간 차익(비싼 쪽 마진 숏 + 싼 쪽 매수).

아비트라지 순스프레드(net_gap)가 진입 임계치 이상이고 **비싼(숏) 다리를 마진으로
숏 가능**(현물+마진 또는 선물)할 때 가상 진입, 갭이 청산 임계치 이하로 좁혀지면 청산.
PnL(%) ≈ 진입갭 − 현재갭 (갭 수렴 시 이익). 전부 dry-run.
"""
from __future__ import annotations

from api.services.arbitrage import compute_arbitrage
from bots.execution_gateway import ExecutionGateway
from bots.framework import BotBase


def _shortable(short_leg: dict) -> bool:
    """비싼 다리를 숏할 수 있나 — 선물이거나, 현물이면 마진 가능해야."""
    if short_leg.get("market") == "perp":
        return True
    return short_leg.get("market") == "spot" and short_leg.get("margin") is True


class MarginPaperBot(BotBase):
    name = "margin"
    interval_sec = 5.0

    def __init__(self, redis, entry_gap=1.0, exit_gap=0.2, min_volume=0.0, exchanges=None):
        super().__init__(redis)
        self.entry_gap, self.exit_gap, self.min_volume = entry_gap, exit_gap, min_volume
        self.exchanges = exchanges or []
        self.gw = ExecutionGateway(redis, self.name, dry_run=True)

    async def step(self) -> None:
        s = await self.get_settings({"entry_gap": self.entry_gap, "exit_gap": self.exit_gap,
                                     "min_volume": self.min_volume, "exchanges": self.exchanges})
        allow = set(s["exchanges"])
        d = await compute_arbitrage(self.redis, min_gap_pct=0.0, min_volume=s["min_volume"])
        held = await self.gw.position_coins()
        pos = {p["coin"]: p for p in await self.gw.positions()}
        for row in d["rows"]:
            coin = row["coin"]
            if allow and not ({row["long"]["exchange"], row["short"]["exchange"]} <= allow):
                continue   # 사용 거래소 제한
            net = row.get("net_gap_pct")
            if net is None:
                net = row["gap_pct"]
            if coin not in held and net >= s["entry_gap"] and _shortable(row["short"]):
                await self.gw.open_paper(coin, {
                    "entry_gap": round(net, 4),
                    "long": row["long"]["exchange"], "short": row["short"]["exchange"]})
            elif coin in held and net <= s["exit_gap"]:
                entry = pos.get(coin, {}).get("entry_gap", net)
                await self.gw.close_paper(coin, entry - net, {"exit_gap": round(net, 4)})
