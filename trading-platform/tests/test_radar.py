"""발굴 레이더 — 급등 전조 점수·후보군·시황 레짐 테스트(순수 함수)."""
from __future__ import annotations

from api.services.stock_radar import (
    market_regime,
    radar_pool,
    radar_score,
    turnover_surge,
)


def _candles(n=60, base=10000, vol=100000, last_vol=None, last_close=None):
    """상승 추세 캔들 n개. 마지막 봉 거래량/종가 커스텀(급등 시뮬)."""
    out = []
    for i in range(n):
        close = base + i * 50
        out.append({"open": close - 20, "high": close + 30, "low": close - 40,
                    "close": close, "volume": vol})
    if last_vol:
        out[-1]["volume"] = last_vol
    if last_close:
        c = last_close
        out[-1].update({"open": out[-2]["close"], "high": c, "close": c,
                        "low": out[-2]["close"] - 10})
    return out


def test_turnover_surge():
    c = _candles(60, vol=100000, last_vol=500000)   # 마지막 봉 거래량 5배
    eok, surge = turnover_surge(c)
    assert eok is not None and surge is not None
    assert surge >= 4                                # 평소 대비 급증 감지
    # 봉 부족이면 None
    assert turnover_surge(_candles(4)) == (None, None)


def test_radar_score_fires_on_breakout():
    # 실적 급증 + 신고가 돌파 + 거래대금 급증 + 장대양봉 → 높은 레이더 점수
    c = _candles(60, base=10000, vol=80000)
    # 마지막 봉: 큰 거래량 + 신고가 갱신 + 강한 양봉
    last_close = c[-1]["close"] + 2000
    c[-1] = {"open": c[-2]["close"], "high": last_close, "low": c[-2]["close"] - 20,
             "close": last_close, "volume": 900000}
    q = {"code": "119850", "name": "지앤씨에너지",
         "price": last_close, "change_pct": 18.0,
         "high_52w": last_close, "flash_ni_yoy": 90.0}
    r = radar_score(q, c, has_flash=True)
    assert r is not None
    assert r["radar"] >= 70
    joined = " ".join(r["signals"])
    assert "거래대금" in joined and "신고가" in joined and "실적" in joined


def test_radar_score_gates_out_weak():
    # 하락 종목 → None
    c = _candles(60)
    down = {"code": "000001", "price": c[-1]["close"], "change_pct": -2.0,
            "high_52w": c[-1]["close"] * 2}
    assert radar_score(down, c) is None
    # 거래대금 미미(30억 미만) → None
    thin = _candles(60, base=1000, vol=100)          # 초저거래대금
    q = {"code": "000002", "price": thin[-1]["close"], "change_pct": 5.0,
         "high_52w": thin[-1]["close"]}
    assert radar_score(q, thin) is None
    # 봉 부족 → None
    assert radar_score({"code": "x", "price": 100, "change_pct": 5}, _candles(10)) is None


def test_radar_pool_selection():
    quotes = [
        {"code": "111111", "price": 9500, "high_52w": 10000, "change_pct": 3.0},  # 신고가 근접+상승
        {"code": "222222", "price": 5000, "high_52w": 10000, "change_pct": 5.0},  # 신고가 멀어 제외
        {"code": "333333", "price": 500, "high_52w": 500, "change_pct": 9.0},     # 동전주 제외
        {"code": "NVDA", "price": 180, "high_52w": 180, "change_pct": 4.0},        # 미국 제외
    ]
    pool = radar_pool(quotes, ranking_codes=["444444"], flash_codes=["555555"],
                      held={"666666"})
    assert "444444" in pool and "555555" in pool     # 랭킹·촉매 우선 포함
    assert "111111" in pool                           # 신고가 근접+상승
    assert "222222" not in pool and "333333" not in pool and "NVDA" not in pool
    # 보유는 제외
    pool2 = radar_pool(quotes, ["666666"], [], held={"666666"})
    assert "666666" not in pool2


def test_radar_pool_cap():
    quotes = []
    codes = [f"{i:06d}" for i in range(100)]
    pool = radar_pool(quotes, codes, [], set(), cap=40)
    assert len(pool) == 40


def test_market_regime():
    on = market_regime({"kospi": {"change_pct": 0.5}, "kosdaq": {"change_pct": 1.2},
                        "investor": {"kosdaq": {"foreigner": 800}}})
    assert on["tone"] == "risk_on"
    off = market_regime({"kospi": {"change_pct": -1.0}, "kosdaq": {"change_pct": -1.5},
                         "investor": {"kosdaq": {"foreigner": -2000}}})
    assert off["tone"] == "risk_off"
    assert market_regime(None)["tone"] == "unknown"
