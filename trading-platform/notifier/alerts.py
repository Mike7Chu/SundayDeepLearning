"""알림 평가 로직 (순수 함수 — 테스트 용이)."""
from __future__ import annotations

from pydantic import BaseModel

from shared.schemas import PremiumCell


class AlertEvent(BaseModel):
    pair_key: str           # "upbit->binance"
    coin: str
    side: str               # "high"(김프) | "low"(역프)
    premium_pct: float
    base_exchange: str
    ref_exchange: str
    base_volume_krw: float | None = None   # 국내 거래대금(볼륨 필터용)

    @property
    def dedup_key(self) -> str:
        return f"{self.pair_key}:{self.coin}:{self.side}"


def evaluate_hyeonseon(
    pair_key: str, cells: list[PremiumCell], low_pct: float
) -> list[AlertEvent]:
    """국내현물 vs 해외선물 역프가 임계치 이하면 현선(현물매수+선물숏) 기회 알림."""
    events: list[AlertEvent] = []
    for c in cells:
        pp = c.premium_perp_pct
        if pp is not None and pp <= low_pct:
            events.append(
                AlertEvent(
                    pair_key=pair_key, coin=c.coin, side="perp_low",
                    premium_pct=pp,
                    base_exchange=c.base_exchange, ref_exchange=c.ref_exchange,
                    base_volume_krw=c.base_volume_krw,
                )
            )
    return events


def evaluate(
    pair_key: str,
    cells: list[PremiumCell],
    high_pct: float,
    low_pct: float,
) -> list[AlertEvent]:
    """임계치를 넘은 코인들에 대한 알림 이벤트 목록."""
    events: list[AlertEvent] = []
    for c in cells:
        side: str | None = None
        if c.premium_pct >= high_pct:
            side = "high"
        elif c.premium_pct <= low_pct:
            side = "low"
        if side:
            events.append(
                AlertEvent(
                    pair_key=pair_key,
                    coin=c.coin,
                    side=side,
                    premium_pct=c.premium_pct,
                    base_exchange=c.base_exchange,
                    ref_exchange=c.ref_exchange,
                    base_volume_krw=c.base_volume_krw,
                )
            )
    return events


def evaluate_funding(
    matrix: dict, apy_pct: float, spread_pct: float
) -> list[AlertEvent]:
    """펀비 매트릭스에서 과열(APY)·거래소간 펀비차 알림 이벤트."""
    events: list[AlertEvent] = []
    for row in matrix.get("coins", []):
        coin = row["coin"]
        cells = list(row.get("by_ex", {}).items())  # [(ex, cell)]
        if not cells:
            continue
        # 과열: |APY| 최대인 거래소
        ex_max, cell_max = max(cells, key=lambda kv: abs(kv[1]["apy"]))
        if abs(cell_max["apy"]) >= apy_pct:
            events.append(AlertEvent(
                pair_key="funding", coin=coin, side="funding_apy",
                premium_pct=cell_max["apy"], base_exchange=ex_max, ref_exchange=""))
        # 거래소간 펀비차(차익)
        if len(cells) >= 2:
            hi = max(cells, key=lambda kv: kv[1]["rate_pct"])
            lo = min(cells, key=lambda kv: kv[1]["rate_pct"])
            spread = hi[1]["rate_pct"] - lo[1]["rate_pct"]
            if spread >= spread_pct:
                events.append(AlertEvent(
                    pair_key="funding", coin=coin, side="funding_spread",
                    premium_pct=round(spread, 4),
                    base_exchange=hi[0], ref_exchange=lo[0]))
    return events


def format_message(event: AlertEvent) -> str:
    if event.side == "funding_apy":
        return (f"💰펀비과열 {event.coin}  {event.base_exchange} APY {event.premium_pct:+.1f}%")
    if event.side == "funding_spread":
        return (f"💱펀비차 {event.coin}  {event.premium_pct:.4f}%p\n"
                f"{event.base_exchange} 숏(고) / {event.ref_exchange} 롱(저)")
    if event.side == "perp_low":
        return (
            f"🟢현선 {event.coin}  선물역프 {event.premium_pct:+.2f}%\n"
            f"{event.base_exchange} 현물매수 + {event.ref_exchange} 선물숏"
        )
    arrow = "🔺김프" if event.side == "high" else "🔻역프"
    return (
        f"{arrow} {event.coin}  {event.premium_pct:+.2f}%\n"
        f"{event.base_exchange} vs {event.ref_exchange}"
    )
