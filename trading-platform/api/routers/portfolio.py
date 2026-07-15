"""포트폴리오(토스증권) API — 실보유·매수여력·(게이트)실주문.

읽기(GET /portfolio, /portfolio/orders)는 항상 안전. 주문(POST)은 이중 게이트:
  1) settings.toss_trading_enabled=True  2) 예상금액 ≤ settings.toss_max_order_krw.
둘 중 하나라도 실패면 403. 기본값은 잠금(False) — 실수로 실매매 나가지 않게.
"""
from __future__ import annotations

import json

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.redis_client import get_redis
from api.services.cache import get_or_compute
from collector.stock.toss import TossClient, TossError
from engine.risk import order_allowed
from shared.redis_keys import (
    ENGINE_RISK_KEY,
    FX_USDKRW_KEY,
    TOSS_ACCOUNT_KEY,
    TOSS_HOLDINGS_KEY,
)
from shared.settings import settings

router = APIRouter()

_toss = TossClient()


@router.get("/portfolio")
async def portfolio() -> dict:
    """실보유 종목 + 총평가·손익·매수여력. 토스 키 없으면 idle 빈 응답."""
    redis = get_redis()
    h_raw = await redis.get(TOSS_HOLDINGS_KEY)
    a_raw = await redis.get(TOSS_ACCOUNT_KEY)
    f_raw = await redis.get(FX_USDKRW_KEY)
    snap = json.loads(h_raw) if h_raw else {}
    acc = json.loads(a_raw) if a_raw else {}
    fx = None
    if f_raw:
        try:
            fx = json.loads(f_raw).get("rate")
        except (json.JSONDecodeError, TypeError):
            fx = None
    return {
        "fx_usdkrw": fx,
        "enabled": _toss.enabled,
        "trading_enabled": settings.toss_trading_enabled,
        "max_order_krw": settings.toss_max_order_krw,
        "holdings": snap.get("holdings", []),
        "cash": snap.get("cash"),
        "total_eval": snap.get("total_eval"),
        "pnl": snap.get("pnl"),
        "pnl_pct": snap.get("pnl_pct"),
        "buying_power": acc.get("buying_power"),
        "ts": snap.get("ts"),
    }


@router.get("/portfolio/orders")
async def portfolio_orders() -> dict:
    """미체결 주문 조회(읽기). 토스 키 필요."""
    if not _toss.enabled:
        return {"rows": [], "enabled": False}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            account = await _toss.resolve_account_seq(client)
            if not account:
                return {"rows": [], "enabled": True}
            rows = await _toss.fetch_open_orders(client, account)
        return {"rows": rows, "enabled": True}
    except TossError as exc:
        raise HTTPException(502, f"토스 오류: {exc.message}")


class OrderRequest(BaseModel):
    symbol: str
    side: str                 # BUY | SELL
    quantity: float
    price: float              # 한도 검증 위해 지정가 필수(시장가는 금액 상한 불가)
    order_type: str = "LIMIT"


async def _assert_trading_allowed(est_amount: float, side: str = "BUY") -> None:
    """실주문 삼중 게이트(키·한도 + 멍거 리스크 실드). 실패 시 403."""
    if not _toss.enabled:
        raise HTTPException(403, "토스 키 미설정 (.env TOSS_CLIENT_ID/SECRET)")
    if not settings.toss_trading_enabled:
        raise HTTPException(
            403, "실매매 비활성 — .env TOSS_TRADING_ENABLED=true 로 명시적으로 켜야 함")
    if est_amount > settings.toss_max_order_krw:
        raise HTTPException(
            403, f"주문금액 {est_amount:,.0f}원 > 한도 {settings.toss_max_order_krw:,.0f}원 "
                 f"(.env TOSS_MAX_ORDER_KRW)")
    # 멍거 리스크 실드(엔진 가동 시): MDD 서킷브레이커·현금 바닥·종목당 5% 한도.
    raw = await get_redis().get(ENGINE_RISK_KEY)
    risk = {}
    if raw:
        try:
            risk = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            risk = {}
    ok, reason = order_allowed(risk, side, est_amount)
    if not ok:
        raise HTTPException(403, reason)


@router.post("/portfolio/order")
async def place_order(req: OrderRequest) -> dict:
    """지정가 주문(게이트). 실제 돈이 나감 — 이중 게이트 통과 시에만."""
    if req.side.upper() not in ("BUY", "SELL"):
        raise HTTPException(400, "side는 BUY 또는 SELL")
    if req.quantity <= 0 or req.price <= 0:
        raise HTTPException(400, "quantity·price는 양수")
    est = req.price * req.quantity
    if not (req.symbol.isdigit() and len(req.symbol) == 6):
        # 미국 티커: USD 금액 → 환율로 원화 환산해 한도 검증(환율 없으면 거부)
        f_raw = await get_redis().get(FX_USDKRW_KEY)
        rate = None
        if f_raw:
            try:
                rate = float(json.loads(f_raw).get("rate") or 0) or None
            except (json.JSONDecodeError, TypeError, ValueError):
                rate = None
        if not rate:
            raise HTTPException(503, "환율(USD/KRW) 미확보 — 잠시 후 재시도")
        est = est * rate
    await _assert_trading_allowed(est, req.side)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            account = await _toss.resolve_account_seq(client)
            if not account:
                raise HTTPException(502, "토스 계좌 조회 실패")
            res = await _toss.place_order(
                client, account, symbol=req.symbol, side=req.side,
                quantity=req.quantity, price=req.price, order_type=req.order_type)
        return {"ok": True, "order": res}
    except TossError as exc:
        raise HTTPException(502, f"토스 주문 오류: {exc.message}")


class OcoRequest(BaseModel):
    """자동 익절·손절(OCO): 목표가 도달 시 익절 매도 + 손절가 이탈 시 손절 매도.

    토스 서버가 실시간 감시 — 엔진 알림(10분 주기)보다 빠르고, 앱을 안 봐도 실행.
    """
    symbol: str
    quantity: float
    target: float             # 목표가(익절 매도 감시가) — 현재가보다 높아야 함
    stop: float               # 손절가(손절 매도 감시가) — 현재가보다 낮아야 함
    expire_date: str          # 만료일 YYYY-MM-DD


@router.post("/portfolio/oco")
async def place_oco(req: OcoRequest) -> dict:
    """OCO 조건주문 등록(게이트). 매도 전용이라 주문금액 한도는 미적용.

    (한도는 '돈이 나가는' 매수 보호 장치 — 보유분 매도 예약은 리스크를 줄이는
    방향이므로 키+실매매 플래그만 검증. 리스크 실드도 매도는 항상 허용.)
    """
    if not _toss.enabled:
        raise HTTPException(403, "토스 키 미설정 (.env TOSS_CLIENT_ID/SECRET)")
    if not settings.toss_trading_enabled:
        raise HTTPException(403, "실매매 비활성 — TOSS_TRADING_ENABLED=true 필요")
    if req.quantity <= 0 or req.stop <= 0 or req.target <= req.stop:
        raise HTTPException(400, "target > stop > 0, quantity > 0 이어야 함")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            account = await _toss.resolve_account_seq(client)
            if not account:
                raise HTTPException(502, "토스 계좌 조회 실패")
            res = await _toss.place_oco_order(
                client, account, symbol=req.symbol, quantity=req.quantity,
                target=req.target, stop=req.stop, expire_date=req.expire_date)
        return {"ok": True, **res}
    except TossError as exc:
        raise HTTPException(502, f"토스 거부: {exc.message}")


@router.get("/portfolio/oco")
async def list_oco(status: str = "OPEN") -> dict:
    """등록된 조건주문 조회(읽기 — 앱에서 등록한 것도 포함).

    대시보드 12초 자동갱신마다 토스를 때리지 않게 30초 캐시(외부 호출 절약).
    """
    if not _toss.enabled:
        return {"rows": [], "enabled": False}

    async def _build() -> dict:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                account = await _toss.resolve_account_seq(client)
                if not account:
                    return {"rows": [], "enabled": True}
                rows = await _toss.fetch_conditional_orders(client, account, status)
            return {"rows": rows, "enabled": True}
        except TossError as exc:
            return {"rows": [], "enabled": True, "error": exc.message}
    return await get_or_compute(f"oco:{status}", 30, _build)


@router.post("/portfolio/oco/{cond_id}/cancel")
async def cancel_oco(cond_id: str) -> dict:
    """조건주문 취소(게이트)."""
    if not settings.toss_trading_enabled:
        raise HTTPException(403, "실매매 비활성 — TOSS_TRADING_ENABLED=true 필요")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            account = await _toss.resolve_account_seq(client)
            if not account:
                raise HTTPException(502, "토스 계좌 조회 실패")
            await _toss.cancel_conditional_order(client, account, cond_id)
        return {"ok": True}
    except TossError as exc:
        raise HTTPException(502, f"토스 거부: {exc.message}")


@router.post("/portfolio/order/{order_id}/cancel")
async def cancel_order(order_id: str) -> dict:
    """주문 취소(게이트). 실매매 활성 상태에서만."""
    if not settings.toss_trading_enabled:
        raise HTTPException(403, "실매매 비활성 — 취소도 TOSS_TRADING_ENABLED=true 필요")
    if not _toss.enabled:
        raise HTTPException(403, "토스 키 미설정")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            account = await _toss.resolve_account_seq(client)
            if not account:
                raise HTTPException(502, "토스 계좌 조회 실패")
            res = await _toss.cancel_order(client, account, order_id)
        return {"ok": True, "order": res}
    except TossError as exc:
        raise HTTPException(502, f"토스 취소 오류: {exc.message}")
