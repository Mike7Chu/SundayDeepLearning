"""KIS 실시간 웹소켓 — 순수 함수(구독 빌더·틱 파서·장시간·등록 선정) 테스트."""
from __future__ import annotations

import datetime
import json

from collector.stock.kis_ws import (
    build_subscribe,
    is_krx_session,
    is_pingpong,
    kr_movers,
    parse_ticks,
    pick_subs,
)


def _tick_fields(code="005930", price="71900", sign="2", rate="0.14"):
    """H0STCNT0 레코드(46필드) 샘플 — 관심 인덱스만 채우고 나머지 0."""
    f = ["0"] * 46
    f[0], f[2], f[3], f[5], f[13] = code, price, sign, rate, "1234567"
    return f


def test_build_subscribe():
    d = json.loads(build_subscribe("KEY", "005930"))
    assert d["header"]["approval_key"] == "KEY"
    assert d["header"]["tr_type"] == "1"
    assert d["body"]["input"] == {"tr_id": "H0STCNT0", "tr_key": "005930"}
    # 해제는 tr_type=2
    assert json.loads(build_subscribe("KEY", "005930", False))["header"]["tr_type"] == "2"


def test_parse_ticks_single_and_sign():
    raw = "0|H0STCNT0|001|" + "^".join(_tick_fields())
    out = parse_ticks(raw)
    assert out == [{"code": "005930", "price": 71900.0, "change_pct": 0.14}]
    # 하락 부호(5) → 등락률 음수
    down = "0|H0STCNT0|001|" + "^".join(_tick_fields(sign="5", rate="1.20"))
    assert parse_ticks(down)[0]["change_pct"] == -1.20


def test_parse_ticks_multi_record():
    fields = _tick_fields() + _tick_fields("000660", "292000", "2", "4.87")
    raw = "0|H0STCNT0|002|" + "^".join(fields)
    out = parse_ticks(raw)
    assert [t["code"] for t in out] == ["005930", "000660"]
    assert out[1]["price"] == 292000.0


def test_parse_ticks_rejects_garbage():
    assert parse_ticks("") == []
    assert parse_ticks('{"header":{"tr_id":"H0STCNT0"}}') == []   # 제어 JSON
    assert parse_ticks("0|OTHER|001|a^b") == []                    # 다른 TR
    assert parse_ticks("0|H0STCNT0|xx|a^b") == []                  # 건수 오류
    assert parse_ticks("0|H0STCNT0|001|only^three^fields") == []   # 필드 부족


def test_is_pingpong():
    assert is_pingpong('{"header":{"tr_id":"PINGPONG","datetime":"2026"}}')
    assert not is_pingpong("0|H0STCNT0|001|x")
    assert not is_pingpong('{"header":{"tr_id":"H0STCNT0"}}')


def test_is_krx_session():
    kst = datetime.timezone(datetime.timedelta(hours=9))
    # 월요일 낮 — 장중
    assert is_krx_session(datetime.datetime(2026, 7, 20, 10, 0, tzinfo=kst))
    # 새벽·저녁 — 장외
    assert not is_krx_session(datetime.datetime(2026, 7, 20, 7, 0, tzinfo=kst))
    assert not is_krx_session(datetime.datetime(2026, 7, 20, 16, 0, tzinfo=kst))
    # 토요일 — 휴장
    assert not is_krx_session(datetime.datetime(2026, 7, 25, 10, 0, tzinfo=kst))


def test_pick_subs():
    # 보유 우선 + 중복 제거 + 미국 티커 제외 + 상한
    out = pick_subs(["005930", "NVDA", "000660"], ["042700", "005930"])
    assert out[:2] == ["042700", "005930"]       # 보유가 앞
    assert "NVDA" not in out and "000660" in out
    many = [f"{i:06d}" for i in range(60)]
    assert len(pick_subs(many, [])) == 41         # 연결당 등록 상한


def test_pick_subs_movers_priority():
    # 우선순위: 보유 → 급등주 → 관심(사용자 선택: 급등주 우선, 관심 뒤로)
    out = pick_subs(["005930"], ["042700"], ["035420", "000660"])
    assert out == ["042700", "035420", "000660", "005930"]
    # 상한 안에서 관심이 밀린다 — 급등주 41개면 관심은 진입 못함
    movers = [f"{i:06d}" for i in range(41)]
    out2 = pick_subs(["900000"], [], movers)      # 900000(관심)은 밀림
    assert len(out2) == 41 and "900000" not in out2


def test_kr_movers():
    rk = {"kr_gainers": [{"symbol": "035420", "name": "네이버"},
                         {"symbol": "NVDA"},          # 미국 티커 제외
                         {"symbol": "005930"}],
          "kr_amount": [{"symbol": "005930"},         # 중복 제거
                        {"symbol": "000660", "name": "SK하이닉스"}]}
    assert kr_movers(rk) == ["035420", "005930", "000660"]   # 급등 먼저·중복 제거
    assert kr_movers({}) == [] and kr_movers(None) == []


def test_kr_movers_excludes_leverage_inverse():
    # 레버리지·인버스 ETF는 자동매매 유니버스에서 제외(거래대금 상위 단골이지만 손실원)
    rk = {"kr_gainers": [{"symbol": "122630", "name": "KODEX 레버리지"},
                         {"symbol": "114800", "name": "KODEX 인버스"},
                         {"symbol": "035420", "name": "NAVER"}]}
    assert kr_movers(rk) == ["035420"]        # 파생 ETF 둘 다 빠지고 일반주만
