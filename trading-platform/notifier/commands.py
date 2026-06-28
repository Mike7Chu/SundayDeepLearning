"""텔레그램 명령 처리 — 봇/알림을 Redis 컨트롤 플레인으로 제어(대시보드와 단일 진실원).

순수 디스패치(handle)라 테스트 용이. 실주문은 없고 페이퍼봇 on/off·킬스위치·알림 토글만.
지원: /help /status /bots /bot start|stop <name> /killswitch on|off /mute /unmute /alerts /brief
"""
from __future__ import annotations

import redis.asyncio as aioredis

from bots.registry import REGISTERED_BOTS
from shared.alert_settings import load_settings, save_settings
from shared.redis_keys import BOT_KILLSWITCH_KEY, bot_enabled_key, bot_state_key

HELP = (
    "🤖 명령어\n"
    "/status — 요약(봇·킬스위치·알림)\n"
    "/bots — 봇 목록/상태\n"
    "/bot start <이름> · /bot stop <이름>\n"
    "/killswitch on|off — 전 봇 정지\n"
    "/mute · /unmute — 알림 마스터\n"
    "/alerts — 알림 설정 요약\n"
    "/brief — 지금 주식 브리핑"
)


def parse_command(text: str) -> tuple[str, list[str]]:
    parts = (text or "").strip().split()
    if not parts:
        return "", []
    cmd = parts[0].lstrip("/").lower()
    cmd = cmd.split("@")[0]   # /cmd@botname 형태 정규화
    return cmd, parts[1:]


async def _bots_summary(redis: aioredis.Redis) -> str:
    killed = (await redis.get(BOT_KILLSWITCH_KEY)) == "1"
    lines = [f"킬스위치: {'🟥 ON(정지)' if killed else '⬜ off'}"]
    for name in REGISTERED_BOTS:
        on = (await redis.get(bot_enabled_key(name))) == "1"
        st = await redis.get(bot_state_key(name))
        lines.append(f"· {name}: {'▶ on' if on else '■ off'} ({st or 'stopped'})")
    return "\n".join(lines)


async def _alerts_summary(redis: aioredis.Redis) -> str:
    s = await load_settings(redis)
    t = s.types
    on = [k for k, v in
          {"김프": t.kimp_high, "역프": t.kimp_low, "현선": t.hyeonseon,
           "펀비과열": t.funding_apy, "펀비차": t.funding_spread}.items() if v]
    return (f"알림 마스터: {'🔔 ON' if s.enabled else '🔕 off'}\n"
            f"켜진 종류: {', '.join(on) or '없음'}\n"
            f"김프≥{s.premium_high_pct} 역프≤{s.premium_low_pct} 쿨다운 {s.cooldown_sec}s\n"
            f"최소거래대금 {s.min_volume_eokwon}억 · 제외 {len(s.exclude_coins)}개")


async def handle(redis: aioredis.Redis, text: str, brief_fn=None) -> str:
    """명령 문자열 → 응답 문자열. brief_fn: /brief용 async 콜백(없으면 안내)."""
    cmd, args = parse_command(text)
    if cmd in ("help", "start", "") and cmd != "bot":
        return HELP
    if cmd == "status":
        return "📋 상태\n" + await _bots_summary(redis) + "\n\n" + await _alerts_summary(redis)
    if cmd == "bots":
        return "🤖 봇\n" + await _bots_summary(redis)
    if cmd == "bot":
        if len(args) < 2 or args[0] not in ("start", "stop"):
            return "사용법: /bot start <이름> | /bot stop <이름>\n등록봇: " + ", ".join(REGISTERED_BOTS)
        action, name = args[0], args[1]
        if name not in REGISTERED_BOTS:
            return f"알 수 없는 봇: {name} (등록: {', '.join(REGISTERED_BOTS)})"
        await redis.set(bot_enabled_key(name), "1" if action == "start" else "0")
        return f"{name} → {'▶ 시작' if action == 'start' else '■ 중지'} (페이퍼)"
    if cmd == "killswitch":
        if not args or args[0] not in ("on", "off"):
            return "사용법: /killswitch on | off"
        await redis.set(BOT_KILLSWITCH_KEY, "1" if args[0] == "on" else "0")
        return f"킬스위치 {'🟥 ON — 전 봇 정지' if args[0] == 'on' else '⬜ off'}"
    if cmd in ("mute", "unmute"):
        await save_settings(redis, {"enabled": cmd == "unmute"})
        return "🔕 알림 음소거" if cmd == "mute" else "🔔 알림 재개"
    if cmd == "alerts":
        return "🔔 알림 설정\n" + await _alerts_summary(redis)
    if cmd == "brief":
        if brief_fn is None:
            return "브리핑 기능 미연결"
        sent = await brief_fn()
        return "📊 브리핑 발송됨" if sent else "브리핑 생략(데이터 없음)"
    return f"알 수 없는 명령: /{cmd}\n{HELP}"
