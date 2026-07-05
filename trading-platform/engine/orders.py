"""게이트 주문 실행기 — 자동매매·텔레그램 명령 공용.

모든 주문은 4중 게이트를 통과해야 실행된다(하나라도 실패 → 거부 사유 반환):
  1) 토스 키 설정  2) TOSS_TRADING_ENABLED=true  3) 주문금액 ≤ TOSS_MAX_ORDER_KRW
  4) 멍거 리스크 실드(BUY_LOCK·종목당 자산 5% 한도 — 매도는 항상 허용)
"""
from __future__ import annotations

import json
import logging

import httpx
import redis.asyncio as aioredis

from collector.stock.toss import TossClient, TossError
from engine.risk import order_allowed
from shared.redis_keys import ENGINE_RISK_KEY
from shared.settings import settings

logger = logging.getLogger(__name__)


async def place_gated_order(redis: aioredis.Redis, toss: TossClient, *,
                            side: str, code: str, qty: float,
                            price: float) -> tuple[bool, str]:
    """4중 게이트 검증 후 지정가 주문. (성공여부, 사람이 읽을 메시지) 반환."""
    side = side.upper()
    if side not in ("BUY", "SELL"):
        return False, "side는 BUY/SELL"
    if qty <= 0 or price <= 0:
        return False, "수량·가격은 양수여야 함"
    if not toss.enabled:
        return False, "토스 키 미설정(.env TOSS_CLIENT_ID/SECRET)"
    if not settings.toss_trading_enabled:
        return False, "실매매 비활성 — .env TOSS_TRADING_ENABLED=true 필요"
    est = qty * price
    if est > settings.toss_max_order_krw:
        return False, (f"주문금액 {est:,.0f}원 > 한도 {settings.toss_max_order_krw:,.0f}원"
                       "(TOSS_MAX_ORDER_KRW)")
    risk = {}
    raw = await redis.get(ENGINE_RISK_KEY)
    if raw:
        try:
            risk = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            risk = {}
    ok, reason = order_allowed(risk, side, est)
    if not ok:
        return False, reason
    try:
        async with httpx.AsyncClient(timeout=15) as client:
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
    logger.info("[order] %s %s x%s @%s → %s", side, code, qty, price, oid)
    return True, (f"{'매수' if side == 'BUY' else '매도'} 접수 — {code} {qty:g}주 "
                  f"@{price:,.0f}원 (주문ID {oid})")


async def cancel_gated_order(redis: aioredis.Redis, toss: TossClient,
                             order_id: str) -> tuple[bool, str]:
    """미체결 주문 취소(실매매 활성 상태에서만)."""
    if not toss.enabled:
        return False, "토스 키 미설정"
    if not settings.toss_trading_enabled:
        return False, "실매매 비활성 — TOSS_TRADING_ENABLED=true 필요"
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
