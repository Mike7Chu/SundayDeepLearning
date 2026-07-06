"""매매 가격 가이드(trade_levels)·KRX 호가 단위 테스트(순수 함수)."""
from __future__ import annotations

from api.services.stock_signal import krx_tick, trade_levels


def test_krx_tick():
    assert krx_tick(1_234) == 1_234          # <2천: 1원
    assert krx_tick(12_344) == 12_340        # <2만: 10원
    assert krx_tick(12_346) == 12_350
    assert krx_tick(123_456) == 123_500      # <20만: 100원
    assert krx_tick(314_567) == 314_500      # <50만: 500원
    assert krx_tick(2_424_300) == 2_424_000  # ≥50만: 1000원


def test_trade_levels_uptrend_pullback():
    # 상승 추세(가격 > SMA20): 추천 매수가 = SMA20 눌림목(추격 매수 방지)
    closes = [1000 + i * 10 for i in range(80)]   # 우상향
    lv = trade_levels(closes)
    assert lv is not None
    assert lv["entry_basis"] == "SMA20 눌림목"
    assert lv["entry"] < closes[-1]               # 현재가보다 낮은 진입가
    assert lv["stop"] < lv["entry"] < lv["target"]
    assert lv["rr"] == 2.0
    assert lv["trend_ok"] is True
    # 손절 클램프: 진입 대비 -3%~-15%
    assert -15.0 <= lv["stop_pct"] <= -3.0
    # 목표는 손익비 1:2 → 상승률 = 손절률의 2배
    assert abs(lv["target_pct"] - 2 * (-lv["stop_pct"])) < 1.5  # 호가 반올림 오차 허용


def test_trade_levels_downtrend_flag():
    closes = [2000 - i * 10 for i in range(80)]   # 우하향
    lv = trade_levels(closes)
    assert lv["trend_ok"] is False                # 매수 보류 신호
    assert lv["entry_basis"] == "현재가"


def test_trade_levels_insufficient_data():
    assert trade_levels([1000] * 10) is None


def test_light_pillar():
    from api.services.stock_signal import light_pillar

    def bar(o, h, l, c, v):
        return {"open": o, "high": h, "low": l, "close": c, "volume": v}

    # 평소 10억대 거래대금 → 오늘 60억(6배) + 고가 마감 장대양봉 = 빛의기둥
    quiet1 = bar(10000, 10100, 9900, 10000, 100_000)     # ≈10억
    quiet2 = bar(10000, 10100, 9900, 10050, 100_000)
    pillar = bar(10000, 11100, 9950, 11000, 570_000)     # ≈60억, 몸통1000>윗꼬리100×1.2
    lp = light_pillar([quiet1, quiet2, pillar])
    assert lp["pillar"] is True
    assert lp["value_eok"] >= 20 and lp["surge_x"] >= 3

    # 윗꼬리 긴 음봉/평범한 거래대금이면 아님
    doji = bar(10000, 11000, 9900, 10050, 570_000)       # 몸통50 < 윗꼬리950
    assert light_pillar([quiet1, quiet2, doji])["pillar"] is False
    small = bar(10000, 10500, 9900, 10400, 120_000)      # 수급 급증 아님
    assert light_pillar([quiet1, quiet2, small])["pillar"] is False
    assert light_pillar([quiet1, pillar]) is None        # 봉 부족
