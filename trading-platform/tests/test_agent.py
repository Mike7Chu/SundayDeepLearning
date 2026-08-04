"""클로드 스윙 결정 에이전트 — 순수 로직 검증(스케줄·파싱·컨텍스트·시장)."""
from engine.agent import (
    build_context,
    due_slots,
    market_of,
    parse_decisions,
    parse_slots,
    tradeable_markets,
)


def test_market_of():
    assert market_of("005930") == "KR"
    assert market_of("AAPL") == "US"


def test_tradeable_markets_by_time():
    assert tradeable_markets("09:40") == {"KR"}      # 국장 개장
    assert tradeable_markets("14:30") == {"KR"}
    assert tradeable_markets("23:40") == {"US"}      # 미장 개장
    assert tradeable_markets("02:00") == {"US"}      # 자정 넘김
    assert tradeable_markets("18:00") == set()       # 둘 다 장외


def test_parse_slots():
    assert parse_slots("09:40,14:30") == ["09:40", "14:30"]
    assert parse_slots("9:40, 14:30 ,bad,25:00,14:30") == ["09:40", "14:30"]  # 정규화·중복·무효 제거
    assert parse_slots("") == []


def test_due_slots_fires_once_within_window():
    slots = ["09:40", "14:30"]
    # 09:45 → 09:40 슬롯 실행(9:40 지난 지 5분), 14:30은 아직
    assert due_slots("09:45", slots, {}, "2026-07-24") == ["09:40"]
    # 이미 오늘 실행됨 → 재실행 안 함
    assert due_slots("09:45", slots, {"09:40": "2026-07-24"}, "2026-07-24") == []
    # 어제 실행 → 오늘 다시 실행
    assert due_slots("09:45", slots, {"09:40": "2026-07-23"}, "2026-07-24") == ["09:40"]


def test_due_slots_window_expires():
    # 슬롯 90분 초과 지나면 몰아서 실행 안 함(재시작 시 과거 슬롯 방지)
    assert due_slots("11:30", ["09:40"], {}, "2026-07-24") == []
    assert due_slots("09:40", ["09:40"], {}, "2026-07-24") == ["09:40"]   # 정각 포함


def test_parse_decisions_from_fenced_json():
    text = ('생각: 후보 검토함.\n```json\n{"market_view":"위험선호",'
            '"decisions":[{"action":"buy","code":"005930","conviction":80,"reason":"저평가"},'
            '{"action":"SELL","code":"aapl","reason":"추세이탈"},'
            '{"action":"HOLD","code":"000660","reason":"관망"}]}\n```\n끝.')
    out = parse_decisions(text)
    assert out["market_view"] == "위험선호"
    assert len(out["decisions"]) == 3
    assert out["decisions"][0] == {"action": "BUY", "code": "005930",
                                   "reason": "저평가", "conviction": 80}
    assert out["decisions"][1]["action"] == "SELL"
    assert out["decisions"][1]["code"] == "AAPL"          # 대문자 정규화


def test_parse_decisions_bare_and_garbage():
    # 펜스 없는 순수 JSON + 잘못된 항목 필터
    text = '{"decisions":[{"action":"BUY","code":""},{"action":"NUKE","code":"005930"},{"action":"BUY","code":"035720","qty":3}]}'
    out = parse_decisions(text)
    assert len(out["decisions"]) == 1
    assert out["decisions"][0] == {"action": "BUY", "code": "035720", "reason": "", "qty": 3}
    assert parse_decisions("설명만 있고 JSON 없음")["decisions"] == []


def test_parse_decisions_prefers_object_with_keys():
    # 산문에 스키마 예시 중괄호가 먼저 나와도, 결정 키를 가진 '실제' 객체를 집는다.
    text = ('형식은 {"action":"BUY"} 처럼 씁니다. 웹검색 결과 삼성 저평가.\n'
            '최종: {"market_view":"강세","decisions":[{"action":"BUY","code":"005930",'
            '"conviction":70,"reason":"실적"}]}')
    out = parse_decisions(text)
    assert out["market_view"] == "강세" and len(out["decisions"]) == 1
    assert out["decisions"][0]["code"] == "005930"


def test_build_context_lists_candidates_and_holdings():
    plan = {"buys": [{"code": "005930", "name": "삼성전자", "price": 80000,
                      "swing": 90, "entry": 78000, "stop": 72000, "target": 90000,
                      "reasons": ["실적 +30%", "정배열"]}],
            "sells": [{"code": "000660", "name": "SK하이닉스", "pnl_pct": -8,
                       "severity": 5, "action": "손절 검토", "reasons": ["추세이탈"]}]}
    holdings = [{"symbol": "000660", "name": "SK하이닉스", "quantity": 10, "pnl_pct": -8}]
    ctx = build_context(plan, holdings, {"buy_lock": False, "mdd_pct": 5}, 1e7, 40)
    assert "삼성전자(005930)" in ctx and "매수 후보" in ctx
    assert "SK하이닉스(000660)" in ctx and "보유 종목" in ctx
    assert "40%" in ctx


def test_diagnose_autotrade():
    from engine.agent import diagnose_autotrade
    gates = {"auto_trade_enabled": True, "kis_trading_enabled": True,
             "kis_paper": True, "broker": "kis", "agent_enabled": True,
             "intraday_entry_enabled": False}
    regime = {"regime": "risk_off", "label": "위험 회피", "posture": "방어",
              "exposure_pct": 20, "strategies": ["S3"], "strategy_labels": ["저변동 방어"]}
    plan = {"buys": [], "sells": [{"code": "001440", "name": "대한전선", "hard": 3}]}
    agent_last = {"slot": "09:40", "n": 0, "acted": 0, "market_view": ""}
    out = "\n".join(diagnose_autotrade(gates, regime, plan, agent_last))
    assert "위험 회피" in out and "목표비중 20%" in out
    assert "매수 후보 0" in out                       # 확신 하한/관망
    assert "초단타 강등" in out                        # 신규진입 OFF
    assert "결정 0건" in out                           # 에이전트 파싱/후보 이슈 힌트
    assert "대한전선" in out                           # 하드 손절
    # 전역 게이트 OFF면 최상단 경고
    off = diagnose_autotrade({"auto_trade_enabled": False}, {}, {}, {})
    assert any("자동매매 OFF" in x for x in off)


def test_build_context_includes_regime():
    # 시황 국면이 있으면 라벨·권장전략·태세 가이드가 컨텍스트에 포함
    regime = {"regime": "risk_off", "label": "위험 회피", "posture": "방어",
              "strategies": ["S3"], "strategy_labels": ["저변동 방어"],
              "reasons": ["지수 200일선 아래 -4.0%", "변동성 확대"]}
    ctx = build_context({"buys": []}, [], {}, 1e7, 30, regime=regime)
    assert "위험 회피" in ctx and "저변동 방어" in ctx and "국면 태세" in ctx
    # unknown 국면은 렌더 안 함(노이즈 방지)
    ctx2 = build_context({"buys": []}, [], {}, 1e7, 30,
                         regime={"regime": "unknown", "label": "판정 불가"})
    assert "판정 불가" not in ctx2
