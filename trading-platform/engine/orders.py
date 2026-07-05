"""게이트 주문 실행기 — 자동매매·텔레그램 명령 공용 (브로커: kis | toss).

역할 분리: 자동매매·주문지시 = 한투(KIS, 모의투자 리허설 가능) / 토스 = 수동 매매.
모든 주문은 게이트를 통과해야 실행된다(하나라도 실패 → 거부 사유 반환):
  1) 브로커 키 설정  2) 브로커 실매매 플래그(KIS_TRADING_ENABLED/TOSS_TRADING_ENABLED)
  3) 주문금액 ≤ 브로커 주문 한도  4) 멍거 리스크 실드(BUY_LOCK·종목당 5% — 매도는 허용)
"""
from __future__ import annotations

import json
import logging

import httpx
import redis.asyncio as aioredis

from collector.stock.kis import KISClient
from collector.stock.toss import TossClient, TossError
from engine.risk import order_allowed
from shared.redis_keys import ENGINE_RISK_KEY
from shared.settings import settings

logger = logging.getLogger(__name__)


async def _risk_state(redis: aioredis.Redis) -> dict:
    raw = await redis.get(ENGINE_RISK_KEY)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


async def place_gated_order(redis: aioredis.Redis, *, side: str, code: str,
                            qty: float, price: float, broker: str = "kis",
                            kis: KISClient | None = None,
                            toss: TossClient | None = None) -> tuple[bool, str]:
    """게이트 검증 후 지정가 주문. (성공여부, 사람이 읽을 메시지) 반환."""
    side = side.upper()
    if side not in ("BUY", "SELL"):
        return False, "side는 BUY/SELL"
    if qty <= 0 or price <= 0:
        return False, "수량·가격은 양수여야 함"
    est = qty * price
    if broker == "kis":
        if kis is None or not kis.enabled:
            return False, "한투 키 미설정(.env KIS_APP_KEY/SECRET)"
        if not settings.kis_trading_enabled:
            return False, "한투 매매 비활성 — .env KIS_TRADING_ENABLED=true 필요"
        if est > settings.kis_max_order_krw:
            return False, (f"주문금액 {est:,.0f}원 > 한투 한도 "
                           f"{settings.kis_max_order_krw:,.0f}원(KIS_MAX_ORDER_KRW)")
    else:
        if toss is None or not toss.enabled:
            return False, "토스 키 미설정(.env TOSS_CLIENT_ID/SECRET)"
        if not settings.toss_trading_enabled:
            return False, "토스 실매매 비활성 — .env TOSS_TRADING_ENABLED=true 필요"
        if est > settings.toss_max_order_krw:
            return False, (f"주문금액 {est:,.0f}원 > 토스 한도 "
                           f"{settings.toss_max_order_krw:,.0f}원(TOSS_MAX_ORDER_KRW)")
    ok, reason = order_allowed(await _risk_state(redis), side, est)
    if not ok:
        return False, reason
    label = "한투" + ("·모의" if settings.kis_paper else "") if broker == "kis" else "토스"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            if broker == "kis":
                res = await kis.place_order(client, code=code, side=side,
                                            qty=int(qty), price=int(price))
            else:
                account = await toss.resolve_account_seq(client)
                if not account:
                    return False, "토스 계좌 조회 실패"
                res = await toss.place_order(client, account, symbol=code, side=side,
                                             quantity=qty, price=price)
    except TossError as exc:
        return False, f"토스 거부: {exc.message}"
    except Exception as exc:
        return False, f"주문 실패: {exc}"
    oid = res.get("order_id") or "OK"
    logger.info("[order/%s] %s %s x%s @%s → %s", broker, side, code, qty, price, oid)
    return True, (f"[{label}] {'매수' if side == 'BUY' else '매도'} 접수 — {code} "
                  f"{qty:g}주 @{price:,.0f}원 (주문ID {oid})")


async def cancel_gated_order(redis: aioredis.Redis, toss: TossClient,
                             order_id: str) -> tuple[bool, str]:
    """토스 미체결 주문 취소. (한투 취소는 앱/HTS 사용 — 추후 지원)"""
    if not toss.enabled:
        return False, "토스 키 미설정"
    if not settings.toss_trading_enabled:
        return False, "토스 실매매 비활성 — TOSS_TRADING_ENABLED=true 필요"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            account = await toss.resolve_account_seq(client)
            if not account:
                return False, "토스 계좌 조회 실패"
            res = await toss.cancel_order(client, account, order_id)
    except TossError as exc:
        return False, f"토스 거부: {exc.message}"
    except Exception as exc:
        return False, f"취소 실패: {exc}"
    return True, f"취소 접수 (주문ID {res.get('order_id') or order_id})"
