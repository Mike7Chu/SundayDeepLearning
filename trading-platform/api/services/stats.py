"""매매 성적표 — 매매일지(개별 체결)를 FIFO로 짝지어 실현 왕복손익·집계(순수 함수).

'전략이 맞냐'에 숫자로 답하는 토대. 반드시 gross(총손익)와 net(비용 차감)을 함께 내서,
모의가 실전을 과대평가하는 착시(특히 초단타)를 드러낸다. 집계는 순수 — 적재·조회는 API.
"""
from __future__ import annotations

from collections import defaultdict, deque

from api.services.cost_model import round_trip


def realized_trades(entries: list[dict]) -> list[dict]:
    """체결 리스트 → 실현된 왕복(매수→매도) FIFO 매칭. [{code,entry,exit,qty,kr}].

    entries: [{code, side(BUY/SELL), price, qty, ts}]. 미청산(보유 중) 매수는 제외.
    """
    lots: dict[str, deque] = defaultdict(deque)     # code → 매수 로트 큐 [가격, 잔량]
    trips: list[dict] = []
    for e in sorted(entries, key=lambda x: x.get("ts") or 0):
        code = e.get("code")
        side = (e.get("side") or "").upper()
        price, qty = e.get("price"), e.get("qty")
        if not code or not price or not qty or qty <= 0:
            continue
        kr = str(code).isdigit()
        if side == "BUY":
            lots[code].append([float(price), float(qty)])
        elif side == "SELL":
            remaining = float(qty)
            while remaining > 0 and lots[code]:
                lot = lots[code][0]
                match = min(remaining, lot[1])
                trips.append({"code": code, "entry": lot[0], "exit": float(price),
                              "qty": match, "kr": kr})
                lot[1] -= match
                remaining -= match
                if lot[1] <= 1e-9:
                    lots[code].popleft()
            # 매칭 매수 없는 매도(공매도/외부보유)는 성적에서 제외
    return trips


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def summarize(entries: list[dict]) -> dict:
    """체결 리스트 → 성적 요약. 승률·손익비·MDD·gross vs net·비용 총액.

    반환 {n, win_rate, gross, net, cost, avg_win_pct, avg_loss_pct, payoff, mdd, open}.
    open = 아직 안 팔린(보유 중) 로트 수(참고). 왕복 0건이면 안전 기본값.
    """
    trips = realized_trades(entries)
    results = [dict(t, **round_trip(t["entry"], t["exit"], t["qty"], t["kr"]))
               for t in trips]
    n = len(results)
    open_lots = sum(1 for e in entries if (e.get("side") or "").upper() == "BUY") \
        - sum(1 for t in trips)                     # 대략적 미청산 건수(참고)
    if not n:
        return {"n": 0, "win_rate": None, "gross": 0.0, "net": 0.0, "cost": 0.0,
                "avg_win_pct": None, "avg_loss_pct": None, "payoff": None,
                "mdd": 0.0, "open": max(0, open_lots)}
    wins = [r for r in results if r["net"] > 0]
    losses = [r for r in results if r["net"] <= 0]
    gross = round(sum(r["gross"] for r in results), 2)
    net = round(sum(r["net"] for r in results), 2)
    cost = round(sum(r["cost"] for r in results), 2)
    avg_win = round(_mean([r["net_pct"] for r in wins]), 2) if wins else None
    avg_loss = round(_mean([r["net_pct"] for r in losses]), 2) if losses else None
    payoff = round(abs(avg_win / avg_loss), 2) if (avg_win and avg_loss) else None
    cum = peak = mdd = 0.0
    for r in results:                               # 실현 순서대로 누적손익 MDD(net 기준)
        cum += r["net"]
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    return {"n": n, "win_rate": round(len(wins) / n * 100, 1),
            "gross": gross, "net": net, "cost": cost,
            "avg_win_pct": avg_win, "avg_loss_pct": avg_loss, "payoff": payoff,
            "mdd": round(mdd, 2), "open": max(0, open_lots)}
