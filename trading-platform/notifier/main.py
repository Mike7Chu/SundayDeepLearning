"""알림봇 엔트리포인트.

매 주기 알림 설정(Redis 오버라이드 머지)을 로드해 김프/역프/현선/펀비를 평가하고,
마스터·종류·제외코인·임계치·쿨다운·최소유지시간(디바운스)을 적용해 텔레그램 발송.

실행: python -m notifier.main
"""
from __future__ import annotations

import asyncio
import logging
import time

import redis.asyncio as aioredis

from api.services.cross import compute_funding_matrix
from api.services.premium import compute_premium
from notifier.alerts import (
    AlertEvent,
    evaluate,
    evaluate_funding,
    evaluate_hyeonseon,
    format_message,
)
from notifier.config import Pair, load_alert_config
from notifier.telegram import TelegramSender
from shared.alert_settings import AlertSettings, load_settings
from shared.redis_keys import alert_hold_key
from shared.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger("notifier")


def _cooldown_key(event: AlertEvent) -> str:
    return f"alert:cooldown:{event.dedup_key}"


async def _held_long_enough(redis: aioredis.Redis, ev: AlertEvent, min_hold: int) -> bool:
    """조건이 min_hold초 이상 지속됐는지(디바운스). 0이면 항상 True."""
    if min_hold <= 0:
        return True
    key = alert_hold_key(ev.dedup_key)
    now = int(time.time())
    # 최초 충족 시각 기록(이미 있으면 유지), 조건 풀리면 TTL로 자동 만료
    await redis.set(key, now, nx=True, ex=min_hold + 60)
    first = await redis.get(key)
    return bool(first) and (now - int(first) >= min_hold)


async def _should_send(redis: aioredis.Redis, ev: AlertEvent, cooldown: int) -> bool:
    ok = await redis.set(_cooldown_key(ev), "1", nx=True, ex=cooldown)
    return bool(ok)


async def _dispatch(redis, sender, s: AlertSettings, events: list[AlertEvent]) -> None:
    min_vol_krw = s.min_volume_eokwon * 1e8
    for ev in events:
        if s.excluded(ev.coin):
            continue
        # 거래대금 필터(국내 KRW 기준 이벤트만; 펀비 등 volume 없는 건 통과)
        if min_vol_krw > 0 and ev.base_volume_krw is not None and ev.base_volume_krw < min_vol_krw:
            continue
        if not await _held_long_enough(redis, ev, s.min_hold_sec):
            continue
        if await _should_send(redis, ev, s.cooldown_sec):
            sent = await sender.send(format_message(ev))
            logger.info("ALERT %s %+.4f (sent=%s)", ev.dedup_key, ev.premium_pct, sent)


async def run() -> None:
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    sender = TelegramSender()
    poll = load_alert_config().poll_interval_sec
    logger.info("alert bot start (telegram=%s)", sender.enabled)
    try:
        while True:
            s = await load_settings(redis)
            if not s.enabled:                      # 마스터 off
                await asyncio.sleep(poll)
                continue
            t = s.types
            for p in s.pairs:
                pair = Pair(base=p["base"], ref=p["ref"])
                try:
                    cells = await compute_premium(redis, pair.base, pair.ref)
                except Exception as exc:
                    logger.warning("[%s] premium 실패: %s", pair.key, exc)
                    continue
                events: list[AlertEvent] = []
                if t.kimp_high or t.kimp_low:
                    for ev in evaluate(pair.key, cells, s.premium_high_pct, s.premium_low_pct):
                        if (ev.side == "high" and t.kimp_high) or (ev.side == "low" and t.kimp_low):
                            events.append(ev)
                if t.hyeonseon:
                    events += evaluate_hyeonseon(pair.key, cells, s.hyeonseon_low_pct)
                await _dispatch(redis, sender, s, events)

            if t.funding_apy or t.funding_spread:
                try:
                    matrix = await compute_funding_matrix(redis)
                    fev = [e for e in evaluate_funding(matrix, s.funding_apy_pct, s.funding_spread_pct)
                           if (e.side == "funding_apy" and t.funding_apy)
                           or (e.side == "funding_spread" and t.funding_spread)]
                    await _dispatch(redis, sender, s, fev)
                except Exception as exc:
                    logger.warning("펀비 알림 실패: %s", exc)

            await asyncio.sleep(poll)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("alert bot stopped")
