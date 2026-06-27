"""알림봇 엔트리포인트.

설정된 (국내, 해외) 쌍의 김프를 주기적으로 평가해, 임계치 초과 코인을
쿨다운을 지키며 텔레그램으로 발송한다.

실행: python -m notifier.main
"""
from __future__ import annotations

import asyncio
import logging

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
from notifier.config import load_alert_config
from notifier.telegram import TelegramSender
from shared.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger("notifier")


def _cooldown_key(event: AlertEvent) -> str:
    return f"alert:cooldown:{event.dedup_key}"


async def _should_send(redis: aioredis.Redis, event: AlertEvent, cooldown: int) -> bool:
    """쿨다운 미경과면 False. 통과 시 쿨다운 마킹(SET NX EX)."""
    ok = await redis.set(_cooldown_key(event), "1", nx=True, ex=cooldown)
    return bool(ok)


async def run() -> None:
    cfg = load_alert_config()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    sender = TelegramSender()
    logger.info(
        "alert bot start: %d pairs, high>=%.2f%% low<=%.2f%% cooldown=%ds telegram=%s",
        len(cfg.pairs), cfg.premium_high_pct, cfg.premium_low_pct,
        cfg.cooldown_sec, sender.enabled,
    )
    try:
        while True:
            for pair in cfg.pairs:
                try:
                    cells = await compute_premium(redis, pair.base, pair.ref)
                except Exception as exc:
                    logger.warning("[%s] premium 계산 실패: %s", pair.key, exc)
                    continue
                events = evaluate(
                    pair.key, cells, cfg.premium_high_pct, cfg.premium_low_pct
                )
                events += evaluate_hyeonseon(pair.key, cells, cfg.hyeonseon_low_pct)
                for ev in events:
                    if await _should_send(redis, ev, cfg.cooldown_sec):
                        sent = await sender.send(format_message(ev))
                        logger.info("ALERT %s %+.2f%% (sent=%s)",
                                    ev.dedup_key, ev.premium_pct, sent)

            # 펀비 알림(거래소쌍 무관, 매트릭스 1회)
            try:
                matrix = await compute_funding_matrix(redis)
                for ev in evaluate_funding(matrix, cfg.funding_apy_pct, cfg.funding_spread_pct):
                    if await _should_send(redis, ev, cfg.cooldown_sec):
                        sent = await sender.send(format_message(ev))
                        logger.info("ALERT %s %.4f (sent=%s)", ev.dedup_key, ev.premium_pct, sent)
            except Exception as exc:
                logger.warning("펀비 알림 평가 실패: %s", exc)

            await asyncio.sleep(cfg.poll_interval_sec)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("alert bot stopped")
