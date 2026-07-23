"""100억 로드맵 — CAGR 역산·페이스·궤도 판정 테스트(순수 함수)."""
from __future__ import annotations

from api.services.roadmap import cagr, roadmap, years_to_target

_YEAR = 365.25 * 86400


def test_cagr():
    assert cagr(100, 200, 1) == 100.0        # 1년에 2배 = +100%
    assert cagr(100, 100, 5) == 0.0
    assert cagr(100, 121, 2) == 10.0         # 2년 복리 21% = 연 10%
    assert cagr(0, 100, 1) is None           # 무효 입력
    assert cagr(100, 100, 0) is None


def test_years_to_target():
    # 연 10% 복리로 10억 → 100억: log(10)/log(1.1) ≈ 24.2년
    y = years_to_target(1e9, 1e10, 10.0)
    assert 24 <= y <= 25
    assert years_to_target(1e10, 1e10, 10.0) == 0.0   # 이미 도달
    assert years_to_target(1e9, 1e10, 0.0) is None    # 성장 없으면 도달 불가


def test_roadmap_on_track():
    now = 1_800_000_000.0
    deadline = now + 10 * _YEAR                       # 10년 후
    # 1년간 5억→7.5억(연 +50% 페이스), 목표 100억
    hist = [{"ts": now - _YEAR, "eval": 5e8},
            {"ts": now - _YEAR / 2, "eval": 6e8}]
    rm = roadmap(6e8, 1e10, now, deadline, hist)
    assert rm["progress_pct"] == 6.0                  # 6억/100억
    assert rm["required_cagr"] is not None
    assert rm["pace_cagr"] is not None                # 30일 이상 히스토리
    assert rm["projected_years"] is not None
    assert rm["on_track"] is True                     # 연 20% 페이스 > 필요 CAGR
    assert rm["gap"] > 0


def test_roadmap_behind_and_short_history():
    now = 1_800_000_000.0
    deadline = now + 3 * _YEAR                         # 3년 후(빡빡)
    # 페이스가 거의 없음(1년간 5억→5.1억)
    hist = [{"ts": now - _YEAR, "eval": 5e8}]
    rm = roadmap(5.1e8, 1e10, now, deadline, hist)
    # 히스토리 1개(2점 미만) → pace None
    assert rm["pace_cagr"] is None and rm["on_track"] is None
    assert rm["required_cagr"] is not None            # 필요 CAGR은 계산됨
    # 자산 없으면 전부 None
    assert roadmap(None, 1e10, now, deadline, [])["progress_pct"] is None
    # 이미 목표 초과면 on_track True
    done = roadmap(1.2e10, 1e10, now, deadline, [])
    assert done["on_track"] is True and done["projected_years"] == 0.0
