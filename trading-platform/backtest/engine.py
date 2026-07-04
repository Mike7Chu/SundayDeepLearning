"""백테스트 엔진 + 전략(룩어헤드 없는 순수 함수).

전략은 종가 시계열 → 포지션 리스트(0=현금, 1=롱)를 만든다. position[i]는 'bar i에서
i+1로 넘어가는 동안' 보유 상태(따라서 closes[:i+1]만 사용 = 미래 미참조).
"""
from __future__ import annotations

from api.services.stock_signal import rsi, sma


def positions_sma_cross(closes: list[float], fast: int = 20, slow: int = 60) -> list[int]:
    pos = []
    for i in range(len(closes)):
        f, s = sma(closes[: i + 1], fast), sma(closes[: i + 1], slow)
        pos.append(1 if (f is not None and s is not None and f > s) else 0)
    return pos


def positions_rsi_meanrev(closes: list[float], low: float = 30, high: float = 70) -> list[int]:
    """RSI 과매도서 진입, 과매수서 청산(평균회귀)."""
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


def positions_momentum(closes: list[float], lookback: int = 60) -> list[int]:
    """N일 모멘텀이 양(+)이면 롱."""
    pos = []
    for i in range(len(closes)):
        if i >= lookback and closes[i - lookback] > 0:
            pos.append(1 if closes[i] / closes[i - lookback] - 1 > 0 else 0)
        else:
            pos.append(0)
    return pos


STRATEGIES = {
    "sma": positions_sma_cross,
    "rsi": positions_rsi_meanrev,
    "momentum": positions_momentum,
}


def run_backtest(closes: list[float], positions: list[int]) -> dict:
    """종가 + 포지션(0/1) → 성과 지표. 수수료/슬리피지는 1차 제외(룰 검증용)."""
    n = len(closes)
    if n < 2:
        return {"error": "데이터 부족"}
    equity, peak, mdd = 1.0, 1.0, 0.0
    trades: list[float] = []
    entry_equity = None
    for i in range(n - 1):
        ret = closes[i + 1] / closes[i] - 1
        if positions[i] == 1:
            if entry_equity is None:        # 진입
                entry_equity = equity
            equity *= (1 + ret)
        elif entry_equity is not None:      # 청산
            trades.append(equity / entry_equity - 1)
            entry_equity = None
        peak = max(peak, equity)
        mdd = min(mdd, equity / peak - 1)
    if entry_equity is not None:            # 미청산 포지션 마감
        trades.append(equity / entry_equity - 1)
    wins = [t for t in trades if t > 0]
    return {
        "bars": n,
        "total_return_pct": round((equity - 1) * 100, 2),
        "buy_hold_pct": round((closes[-1] / closes[0] - 1) * 100, 2),
        "trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if trades else None,
        "max_drawdown_pct": round(mdd * 100, 2),
    }


def backtest(closes: list[float], strategy: str = "sma", **params) -> dict:
    fn = STRATEGIES.get(strategy)
    if fn is None:
        return {"error": f"알 수 없는 전략: {strategy} (가능: {', '.join(STRATEGIES)})"}
    pos = fn(closes, **params)
    return {"strategy": strategy, **run_backtest(closes, pos)}
