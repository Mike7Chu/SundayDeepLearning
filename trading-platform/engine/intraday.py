"""장중(데이 트레이딩) 로직 — 1분봉 집계·진입 신호·장중 시간 판정(순수 함수).

스윙(며칠 보유)과 별개로 '분~시간 보유' 데이 스윙을 위한 신호. 실시간 시세를 1분봉으로
모아 EMA 정배열+양봉+거래량 급증에서 진입. 거래가 잦으므로 비용(거래세)에 민감 →
반드시 성적표 net(비용 차감)으로 검증. 예측 아님·판단 보조(면책).
"""
from __future__ import annotations

from datetime import datetime, time as _dtime


def add_tick(bars: list[dict], price: float, ts: float,
             bucket_sec: int = 60, cap: int = 120) -> list[dict]:
    """체결가 1건을 분봉에 반영(순수). 같은 버킷이면 갱신, 새 버킷이면 새 봉.

    bars: [{t(버킷시작), o,h,l,c,v}] 오래된→최신. v=버킷 내 갱신 횟수(거래강도 프록시).
    """
    if not price or price <= 0:
        return bars
    b = int(ts // bucket_sec) * bucket_sec
    out = list(bars)
    if out and out[-1]["t"] == b:
        cur = out[-1]
        cur["h"] = max(cur["h"], price)
        cur["l"] = min(cur["l"], price)
        cur["c"] = price
        cur["v"] = cur.get("v", 0) + 1
    else:
        out.append({"t": b, "o": price, "h": price, "l": price, "c": price, "v": 1})
    return out[-cap:]


def ema(vals: list[float], n: int) -> float | None:
    """지수이동평균(순수). 데이터 부족이면 None."""
    if len(vals) < n:
        return None
    k = 2 / (n + 1)
    e = sum(vals[:n]) / n
    for v in vals[n:]:
        e = v * k + e * (1 - k)
    return e


def intraday_signal(bars: list[dict], fast: int = 5, slow: int = 20) -> dict:
    """1분봉 → 장중 진입 신호(순수). {action:'buy'|'none', reason, ema_fast, ema_slow}.

    조건(모두): EMA_fast>EMA_slow(상승 정배열) · 현재가>EMA_slow · 최근봉 양봉 ·
    거래강도 급증(최근봉 v > 직전 5봉 평균). 하나라도 빠지면 none.
    """
    closes = [b["c"] for b in bars if b.get("c")]
    if len(closes) < slow + 1:
        return {"action": "none", "reason": "분봉 데이터 부족"}
    ef, es = ema(closes, fast), ema(closes, slow)
    price = closes[-1]
    last = bars[-1]
    up = bool(ef and es and ef > es and price > es)
    green = last.get("c", 0) >= last.get("o", 0)
    prev = [b.get("v", 0) for b in bars[-6:-1]]
    vsurge = last.get("v", 0) > (sum(prev) / len(prev) if prev else 0)
    ef_r = round(ef, 2) if ef else None
    es_r = round(es, 2) if es else None
    if up and green and vsurge:
        return {"action": "buy", "ema_fast": ef_r, "ema_slow": es_r,
                "reason": f"EMA{fast}>{slow} 정배열·양봉·거래강도↑"}
    miss = []
    if not up:
        miss.append("정배열 아님")
    if not green:
        miss.append("음봉")
    if not vsurge:
        miss.append("거래강도 약함")
    return {"action": "none", "ema_fast": ef_r, "ema_slow": es_r,
            "reason": " · ".join(miss) or "조건 미충족"}


def krx_intraday(now: datetime, open_pad_min: int = 5,
                 flatten_at: _dtime = _dtime(15, 15)) -> str:
    """KRX 장중 상태(순수) → 'entry'(진입 가능) / 'flatten'(청산 구간) / 'closed'.

    평일 09:00+pad ~ flatten_at 이전 = entry, flatten_at ~ 15:30 = flatten(신규진입 금지·
    보유 정리), 그 외 closed. now는 KST 기준 naive datetime을 받는다.
    """
    if now.weekday() >= 5:
        return "closed"
    t = now.time()
    open_t = _dtime(9, open_pad_min)          # 09:0X 이후 진입(개장 직후 변동성 회피)
    if t < open_t:
        return "closed"
    if t < flatten_at:
        return "entry"
    if t <= _dtime(15, 30):
        return "flatten"
    return "closed"
