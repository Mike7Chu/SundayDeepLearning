"""텔레그램 주문 지시 — 등록된 chat_id에서만, 실주문은 '확인 N' 회신 필수.

명령:
  잔고            보유·평가·매수여력 요약
  상태            리스크 실드(MDD·현금·잠금) 상태
  후보            2단계 필터 통과 매수 리스트
  매수 코드 수량 [가격]   예) 매수 005930 10 313500   (가격 생략=현재가)
  매도 코드 수량 [가격]
  확인 N          대기 중인 주문 실행(2분 내). 실주문 4중 게이트 통과 시에만 체결
  주문취소 주문ID  미체결 주문 취소
  도움말          명령 목록
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

import httpx
import redis.asyncio as aioredis

from collector.stock.toss import TossClient
from engine.orders import cancel_gated_order, place_gated_order
from notifier.telegram import TelegramSender
from shared.redis_keys import (
    ENGINE_BUYLIST_KEY,
    ENGINE_RISK_KEY,
    STOCK_MARKET_KEY,
    STOCK_QUOTE_KEY,
    TG_OFFSET_KEY,
    TG_PENDING_KEY,
    TOSS_ACCOUNT_KEY,
    TOSS_HOLDINGS_KEY,
)
from shared.settings import settings

logger = logging.getLogger(__name__)

_PENDING_TTL = 120.0   # '확인' 유효시간(초)
_HELP = ("명령: 잔고 · 상태 · 후보 · 도움말\n"
         "매수 코드 수량 [가격] / 매도 코드 수량 [가격]\n"
         "→ 요약이 오면 2분 내 '확인 N' 회신 시 실주문\n"
         "주문취소 주문ID")


def parse_command(text: str) -> dict | None:
    """명령 문자열 → {cmd, ...} (순수 함수). 모르는 명령은 None."""
    t = (text or "").strip()
    if t in ("잔고", "상태", "후보", "도움말", "/start", "help"):
        return {"cmd": {"/start": "도움말", "help": "도움말"}.get(t, t)}
    m = re.fullmatch(r"(매수|매도)\s+(\d{6})\s+(\d+(?:\.\d+)?)(?:\s+(\d+))?", t)
    if m:
        return {"cmd": "order", "side": "BUY" if m.group(1) == "매수" else "SELL",
                "code": m.group(2), "qty": float(m.group(3)),
                "price": float(m.group(4)) if m.group(4) else None}
    m = re.fullmatch(r"확인\s+(\d+)", t)
    if m:
        return {"cmd": "confirm", "n": m.group(1)}
    m = re.fullmatch(r"주문취소\s+(\S+)", t)
    if m:
        return {"cmd": "cancel", "order_id": m.group(1)}
    return None


async def _jget(redis: aioredis.Redis, key: str) -> dict:
    raw = await redis.get(key)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


async def _cur_price(redis: aioredis.Redis, code: str) -> float | None:
    raw = (await redis.hget(STOCK_QUOTE_KEY, code)
           or await redis.hget(STOCK_MARKET_KEY, code))
    if not raw:
        return None
    try:
        return json.loads(raw).get("price")
    except (json.JSONDecodeError, TypeError):
        return None


async def _handle(redis: aioredis.Redis, toss: TossClient,
                  sender: TelegramSender, text: str) -> None:
    p = parse_command(text)
    if p is None:
        await sender.send("알 수 없는 명령이에요.\n" + _HELP)
        return
    cmd = p["cmd"]
    if cmd == "도움말":
        await sender.send(_HELP)
    elif cmd == "잔고":
        h = await _jget(redis, TOSS_HOLDINGS_KEY)
        a = await _jget(redis, TOSS_ACCOUNT_KEY)
        rows = h.get("holdings", [])
        lines = [f"· {r.get('name') or r.get('symbol')} {r.get('qty'):g}주 "
                 f"{(r.get('pnl_pct') or 0):+.1f}%" for r in rows[:10]]
        await sender.send(
            f"💼 보유 {len(rows)}종목 · 평가 {h.get('total_eval', 0):,.0f}원\n"
            f"매수여력 {a.get('buying_power') or 0:,.0f}원\n" + "\n".join(lines))
    elif cmd == "상태":
        r = await _jget(redis, ENGINE_RISK_KEY)
        await sender.send(
            f"🛡️ 리스크 실드 {'🔒매수잠금' if r.get('buy_lock') else '✅정상'}\n"
            f"MDD {r.get('mdd_pct')}% · 현금 {r.get('cash_pct')}% · "
            f"종목한도 {r.get('per_stock_cap') or 0:,.0f}원\n"
            + " / ".join(r.get("reasons", [])))
    elif cmd == "후보":
        b = await _jget(redis, ENGINE_BUYLIST_KEY)
        rows = b.get("rows", [])[:5]
        if not rows:
            await sender.send("2단계 필터 통과 종목 없음")
        else:
            await sender.send("🎯 매수 후보\n" + "\n".join(
                f"· {r['name']}({r['code']}) {r['final']:.0f}점 — "
                f"매수 {r.get('entry') or 0:,.0f} 손절 {r.get('stop') or 0:,.0f} "
                f"목표 {r.get('target') or 0:,.0f}" for r in rows))
    elif cmd == "order":
        price = p["price"] or await _cur_price(redis, p["code"])
        if not price:
            await sender.send(f"{p['code']} 가격을 몰라요 — 가격을 지정해 주세요")
            return
        n = str(int(time.time()) % 100000)
        await redis.hset(TG_PENDING_KEY, n, json.dumps(
            {**p, "price": price, "ts": time.time()}, ensure_ascii=False))
        side_kr = "매수" if p["side"] == "BUY" else "매도"
        await sender.send(
            f"⚠️ 실주문 확인 필요\n{side_kr} {p['code']} {p['qty']:g}주 "
            f"@{price:,.0f}원 (예상 {p['qty'] * price:,.0f}원)\n"
            f"→ 2분 내 '확인 {n}' 회신 시 실행")
    elif cmd == "confirm":
        raw = await redis.hget(TG_PENDING_KEY, p["n"])
        await redis.hdel(TG_PENDING_KEY, p["n"])
        if not raw:
            await sender.send("대기 중인 주문이 없어요(번호 확인)")
            return
        try:
            o = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            await sender.send("주문 정보 손상 — 다시 시도")
            return
        if time.time() - (o.get("ts") or 0) > _PENDING_TTL:
            await sender.send("⏰ 확인 시간 초과(2분) — 다시 주문해 주세요")
            return
        ok, msg = await place_gated_order(redis, toss, side=o["side"],
                                          code=o["code"], qty=o["qty"],
                                          price=o["price"])
        await sender.send(("✅ " if ok else "🚫 ") + msg)
    elif cmd == "cancel":
        ok, msg = await cancel_gated_order(redis, toss, p["order_id"])
        await sender.send(("✅ " if ok else "🚫 ") + msg)


async def command_loop(redis: aioredis.Redis, toss: TossClient) -> None:
    """텔레그램 getUpdates 롱폴링 — 등록된 chat_id의 메시지만 처리."""
    sender = TelegramSender()
    if not sender.enabled:
        logger.info("[tg] 텔레그램 미설정 → 명령 비활성")
        return
    logger.info("[tg] 주문지시 대기 (chat_id=%s)", sender.chat_id)
    url = f"https://api.telegram.org/bot{sender.token}/getUpdates"
    while True:
        try:
            offset = int(await redis.get(TG_OFFSET_KEY) or 0)
            async with httpx.AsyncClient(timeout=40) as client:
                r = await client.get(url, params={"offset": offset + 1, "timeout": 25})
                r.raise_for_status()
                updates = r.json().get("result", [])
            for u in updates:
                await redis.set(TG_OFFSET_KEY, u["update_id"])
                msg = u.get("message") or {}
                chat = str((msg.get("chat") or {}).get("id", ""))
                text = msg.get("text", "")
                if chat != str(sender.chat_id):
                    logger.warning("[tg] 미등록 chat 무시: %s", chat)
                    continue
                if text:
                    await _handle(redis, toss, sender, text)
        except Exception as exc:
            logger.warning("[tg] 폴링 오류(계속): %s", exc)
            await asyncio.sleep(5)
