"""백테스트 엔진 — 비용 반영·룩어헤드 없는 순수 함수.

'과거 성과'를 실전에 쓸 수 있게: ①우리가 실제 추천하는 전략(추세 눌림목 진입 +
손절/목표/추세이탈 청산)을 손익/고저로 시뮬레이션 ②거래세·수수료·슬리피지 반영한
net ③CAGR·승률·손익비·기대값·MDD·시장노출·보유일수 ④단순보유(buy&hold) 대비.
전 함수 순수(closes/candles 입력) — 미래 미참조(각 시점은 그 시점까지만 사용).
"""
from __future__ import annotations

from api.services.cost_model import cost_drag_pct, round_trip
from api.services.stock_signal import rsi, sma

_YEAR = 252.0   # 연간 거래일(연율화 기준)


# ── 단순 규칙(포지션 0/1) — 참고용 벤치마크 ──────────────────────────────
def positions_sma_cross(closes, fast=20, slow=60):
    pos = []
    for i in range(len(closes)):
        f, s = sma(closes[: i + 1], fast), sma(closes[: i + 1], slow)
        pos.append(1 if (f is not None and s is not None and f > s) else 0)
    return pos


def positions_rsi_meanrev(closes, low=30, high=70):
    pos, holding = [], 0
    for i in range(len(closes)):
        r = rsi(closes[: i + 1])
        if r is not None:
            if r < low:
                holding = 1
            elif r > high:
                holding = 0
        pos.append(holding)
    return pos


def positions_momentum(closes, lookback=60):
    pos = []
    for i in range(len(closes)):
        if i >= lookback and closes[i - lookback] > 0:
            pos.append(1 if closes[i] / closes[i - lookback] - 1 > 0 else 0)
        else:
            pos.append(0)
    return pos


def positions_rsi2(closes, low=10, high=70):
    """Connors RSI(2): 장기 상승추세(200일선 위, 데이터 부족 시 100일선) + RSI2<low 매수,
    RSI2>high 매도(1=보유). 고승률·단기 평균회귀 — S7 전략의 백테스트 벤치마크."""
    pos, holding = [], 0
    for i in range(len(closes)):
        window = closes[: i + 1]
        r = rsi(window, 2)
        ma = sma(window, 200) if len(window) >= 200 else sma(window, 100)
        up = ma is not None and closes[i] > ma
        if r is not None:
            if holding:
                if r > high:
                    holding = 0                 # 과매도 해소 → 청산
            elif up and r < low:
                holding = 1                     # 상승추세 눌림 극단 과매도 → 진입
        pos.append(holding)
    return pos


STRATEGIES = {
    "strategy": "추세 눌림목(우리 전략)",   # 기본 — 아래 level 시뮬
    "sma": positions_sma_cross,
    "rsi": positions_rsi_meanrev,
    "rsi2": positions_rsi2,
    "momentum": positions_momentum,
}


def _sma_series(closes, p):
    out = [None] * len(closes)
    s = 0.0
    for i, c in enumerate(closes):
        s += c
        if i >= p:
            s -= closes[i - p]
        if i >= p - 1:
            out[i] = s / p
    return out


def _trades_from_positions(closes, positions):
    """포지션 0/1 → 왕복 [(entry, exit, hold_days)]. 진입/청산은 종가."""
    trades, ei = [], None
    for i in range(len(closes) - 1):
        if positions[i] == 1 and ei is None:
            ei = i
        elif positions[i] == 0 and ei is not None:
            trades.append((closes[ei], closes[i], i - ei))
            ei = None
    if ei is not None:
        trades.append((closes[ei], closes[-1], len(closes) - 1 - ei))
    return trades


def _trend_pullback_trades(candles, stop_pct=8.0, rr=2.0, fast=20, slow=60):
    """우리 전략: 상승추세(SMA20>SMA60)에서 종가가 SMA20 위로 재돌파 시 진입,
    손절(-stop_pct)·목표(+stop_pct×rr, 1:2)·추세이탈(SMA20<SMA60) 청산.

    손절/목표 도달은 당일 저가/고가로 판정(일봉 내 히트). 반환 [(entry, exit, days)].
    """
    closes = [c["close"] for c in candles]
    highs = [c.get("high") or c["close"] for c in candles]
    lows = [c.get("low") or c["close"] for c in candles]
    s20, s60 = _sma_series(closes, fast), _sma_series(closes, slow)
    trades, i, n = [], slow, len(candles)
    while i < n:
        if s20[i] is None or s60[i] is None or s20[i - 1] is None:
            i += 1
            continue
        entered = (s20[i] > s60[i] and closes[i] > s20[i]
                   and closes[i - 1] <= s20[i - 1])          # 눌림목 재돌파
        if not entered:
            i += 1
            continue
        entry = closes[i]
        stop, target = entry * (1 - stop_pct / 100), entry * (1 + stop_pct * rr / 100)
        j, exit_px = i + 1, None
        while j < n:
            if lows[j] <= stop:
                exit_px = stop
                break
            if highs[j] >= target:
                exit_px = target
                break
            if s20[j] is not None and s60[j] is not None and s20[j] < s60[j]:
                exit_px = closes[j]
                break
            j += 1
        if exit_px is None:               # 미청산 → 마지막 종가로 마크
            exit_px, j = closes[-1], n - 1
        trades.append((entry, exit_px, j - i))
        i = j + 1                         # 청산 다음 봉부터 재탐색
    return trades


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def summarize(candles, trades, kr=True):
    """왕복 리스트 → 비용 반영 성과 지표. buy&hold·CAGR·MDD·기대값·노출 포함."""
    closes = [c["close"] for c in candles]
    n = len(closes)
    bars = max(1, n - 1)
    bh = (closes[-1] / closes[0] - 1) * 100 if n >= 2 and closes[0] else 0.0
    if not trades:
        return {"trades": 0, "buy_hold_pct": round(bh, 2), "bars": n,
                "verdict": "거래 신호 없음 — 이 기간엔 진입 조건 불성립"}
    rows = [round_trip(e, x, 1, kr) for e, x, _ in trades]
    nets = [r["net_pct"] for r in rows]
    wins = [v for v in nets if v > 0]
    losses = [v for v in nets if v <= 0]
    equity = peak = 1.0
    mdd = 0.0
    for v in nets:                          # 거래 단위 복리 수익곡선(net)
        equity *= (1 + v / 100)
        peak = max(peak, equity)
        mdd = min(mdd, equity / peak - 1)
    net_ret = (equity - 1) * 100
    gross_eq = 1.0
    for r in rows:
        gross_eq *= (1 + r["gross_pct"] / 100)
    years = bars / _YEAR
    cagr = ((equity ** (1 / years) - 1) * 100) if years > 0 and equity > 0 else None
    days_in = sum(d for _, _, d in trades)
    wr = len(wins) / len(nets) * 100
    avg_w = round(_mean(wins), 2) if wins else None
    avg_l = round(_mean(losses), 2) if losses else None
    payoff = round(abs(avg_w / avg_l), 2) if (avg_w and avg_l) else None
    exp = round(_mean(nets), 2)             # 1거래당 net 기대수익%(비용 반영)
    out = {
        "trades": len(nets), "bars": n,
        "win_rate_pct": round(wr, 1),
        "net_return_pct": round(net_ret, 2),
        "gross_return_pct": round((gross_eq - 1) * 100, 2),
        "cagr_pct": round(cagr, 2) if cagr is not None else None,
        "buy_hold_pct": round(bh, 2),
        "max_drawdown_pct": round(mdd * 100, 2),
        "avg_win_pct": avg_w, "avg_loss_pct": avg_l, "payoff": payoff,
        "expectancy_pct": exp,
        "exposure_pct": round(days_in / bars * 100, 1),
        "avg_hold_days": round(days_in / len(nets), 1),
        "cost_drag_pct": cost_drag_pct(kr),
    }
    if out["trades"] < 8:
        out["verdict"] = f"표본 부족 — {out['trades']}거래(신뢰 낮음, 참고용)"
    elif exp > 0 and net_ret > bh:
        out["verdict"] = "엣지 있음 — 비용 반영 후에도 단순보유보다 우위"
    elif exp > 0:
        out["verdict"] = "약한 엣지 — 수익은 나지만 단순보유가 더 나음"
    else:
        out["verdict"] = "엣지 없음 — 비용 반영 시 기대값 마이너스"
    return out


def backtest(candles, strategy="strategy", *, kr=True, **params):
    """일봉(candles: [{close,high,low,...}]) + 전략 → 비용 반영 성과 지표.

    strategy='strategy'(우리 추세 눌림목) 기본. sma/rsi/momentum은 참고 벤치마크.
    """
    if not candles or len(candles) < 30:
        return {"strategy": strategy, "error": "데이터 부족(최소 30봉 필요)"}
    closes = [c["close"] for c in candles]
    if strategy == "strategy":
        trades = _trend_pullback_trades(candles, **params)
    elif strategy in STRATEGIES and callable(STRATEGIES[strategy]):
        trades = _trades_from_positions(closes, STRATEGIES[strategy](closes))
    else:
        return {"strategy": strategy, "error": "알 수 없는 전략"}
    label = STRATEGIES["strategy"] if strategy == "strategy" else strategy
    return {"strategy": strategy, "label": label, **summarize(candles, trades, kr)}
