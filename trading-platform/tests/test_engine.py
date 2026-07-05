"""매매 엔진 — 리스크 실드·정량 필터·감점 파서 테스트(순수 함수)."""
from __future__ import annotations

from engine.risk import evaluate_risk, order_allowed
from engine.screener import final_score, quant_filter
from engine.telegram_cmd import parse_command
from research.analyst import parse_penalty


# ---------- 리스크 실드 ----------
def test_mdd_circuit_breaker():
    # 최고점 1억 → 현재 8400만 = -16% → BUY_LOCK
    r = evaluate_risk(84_000_000, 100_000_000, 30_000_000)
    assert r["buy_lock"] is True
    assert r["mdd_pct"] == 16.0
    # -10%면 정상(현금 30%도 충족)
    r2 = evaluate_risk(90_000_000, 100_000_000, 30_000_000)
    assert r2["buy_lock"] is False


def test_cash_floor():
    # 현금 20% < 25% → 매수 잠금
    r = evaluate_risk(100_000_000, 100_000_000, 20_000_000)
    assert r["buy_lock"] is True
    assert any("현금" in s for s in r["reasons"])


def test_no_asset_data_locks():
    r = evaluate_risk(None, None, None)
    assert r["buy_lock"] is True     # 모르면 사지 않는다


def test_per_stock_cap_and_order_gate():
    r = evaluate_risk(100_000_000, 100_000_000, 40_000_000)
    assert r["per_stock_cap"] == 5_000_000        # 자산의 5%
    ok, _ = order_allowed(r, "BUY", 4_000_000)
    assert ok
    ok, reason = order_allowed(r, "BUY", 6_000_000)
    assert not ok and "한도" in reason
    ok, _ = order_allowed(r, "SELL", 999_999_999)  # 매도는 항상 허용
    assert ok
    locked = evaluate_risk(80_000_000, 100_000_000, 40_000_000)
    ok, reason = order_allowed(locked, "BUY", 1_000)
    assert not ok and "리스크 실드" in reason


# ---------- 정량 필터(능력 범위) ----------
def test_quant_filter_requires_complete_data():
    good = {"code": "A", "price": 10000, "per": 8, "pbr": 0.9,
            "eps": 1250, "bps": 12000}                     # ROE 10.4%
    missing = {"code": "B", "price": 10000, "per": 5, "pbr": 0.5,
               "eps": None, "bps": 12000}                   # EPS 누락 → 탈락
    expensive = {"code": "C", "price": 10000, "per": 20, "pbr": 0.9,
                 "eps": 500, "bps": 4000}                   # PER 20 → 탈락
    low_roe = {"code": "D", "price": 10000, "per": 10, "pbr": 1.0,
               "eps": 500, "bps": 10000}                    # ROE 5% → 탈락
    out = quant_filter([good, missing, expensive, low_roe])
    assert [r["code"] for r in out] == ["A"]
    assert out[0]["roe"] > 10


def test_final_score():
    assert final_score(85, 10) == 75.0
    assert final_score(85, 30) == 55.0
    assert final_score(85, None) is None    # 감점 검증 전 → 보류


# ---------- 게이트 주문 실행기(네트워크 전 단계 거부) ----------
def test_place_gated_order_gates():
    import asyncio

    from collector.stock.kis import KISClient
    from collector.stock.toss import TossClient
    from engine.orders import place_gated_order

    async def run():
        # 키 미설정 환경: 브로커별로 명확한 거부 사유(네트워크 접근 없이 반환)
        ok, msg = await place_gated_order(None, side="BUY", code="005930",
                                          qty=1, price=1000, broker="kis",
                                          kis=KISClient())
        assert not ok and "한투" in msg
        ok, msg = await place_gated_order(None, side="BUY", code="005930",
                                          qty=1, price=1000, broker="toss",
                                          toss=TossClient())
        assert not ok and "토스" in msg
        ok, msg = await place_gated_order(None, side="HOLD", code="005930",
                                          qty=1, price=1000)
        assert not ok and "BUY/SELL" in msg
        ok, msg = await place_gated_order(None, side="BUY", code="005930",
                                          qty=0, price=1000)
        assert not ok and "양수" in msg
    asyncio.run(run())


# ---------- 텔레그램 명령 파서 ----------
def test_parse_command_orders():
    assert parse_command("매수 005930 10 313500") == {
        "cmd": "order", "side": "BUY", "code": "005930", "qty": 10.0, "price": 313500.0}
    assert parse_command("매도 000660 2.5") == {
        "cmd": "order", "side": "SELL", "code": "000660", "qty": 2.5, "price": None}
    assert parse_command("확인 42") == {"cmd": "confirm", "n": "42"}
    assert parse_command("주문취소 abc-123") == {"cmd": "cancel", "order_id": "abc-123"}
    assert parse_command("잔고") == {"cmd": "잔고"}
    assert parse_command("/start") == {"cmd": "도움말"}


def test_parse_command_rejects_malformed():
    assert parse_command("매수 삼성전자 10") is None      # 코드는 6자리 숫자만
    assert parse_command("매수 005930") is None            # 수량 누락
    assert parse_command("전량매도") is None
    assert parse_command("") is None


# ---------- 감점 파서 ----------
def test_parse_penalty():
    assert parse_penalty("리스크 분석...\n감점: 12/30") == 12
    assert parse_penalty("본문 감점: 5/30 언급\n최종 감점: 22/30") == 22  # 마지막 매치
    assert parse_penalty("형식 없음") == 30       # 못 찾으면 보수적 30
    assert parse_penalty("감점: 99/30") == 30     # 상한 클램프
