"""AI 리서치 엔트리포인트 — 관심종목을 정기적으로 거장 렌즈로 분석.

ANTHROPIC_API_KEY가 없으면 비활성(idle). 각 분석 결과를 Redis(research:reports)에
저장하고 텔레그램으로 요약 브리핑. 실행: python -m research.main
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

import redis.asyncio as aioredis

from collector.stock.kis import effective_watchlist
from notifier.telegram import TelegramSender
from research.analyst import Analyst
from research.coach import gather_coach, should_run
from research.data import StockData, gather
from shared.redis_keys import (
    COACH_KEY,
    COACH_REQ_KEY,
    RESEARCH_HB_KEY,
    RESEARCH_INV_KEY,
    RESEARCH_INV_REQ_KEY,
    RESEARCH_KEY,
    RESEARCH_LOCK_KEY,
    RESEARCH_REQ_KEY,
    RESEARCH_STORY_KEY,
    RESEARCH_STORY_REQ_KEY,
)
from shared.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("research")


def brief(report: dict) -> str:
    """리포트 텔레그램 문구 — 전문 그대로(발송은 send_long이 잘리지 않게 분할)."""
    body = (report.get("report") or "").strip() or "(내용 없음)"
    return f"🧠가치투자 리서치 {report.get('name','')}({report.get('code','')})\n{body}"


async def run_one(redis: aioredis.Redis, analyst: Analyst, sender: TelegramSender,
                  item: dict) -> dict:
    """종목 1개 분석 → 저장 → 브리핑. 데이터 없으면 코드만으로 진행."""
    code, name = item["code"], item.get("name", "")
    data = await gather(redis, code) or StockData(code=code, name=name)
    if not data.name:
        data.name = name
    report = await analyst.analyze(data)
    await redis.hset(RESEARCH_KEY, code, json.dumps(report, ensure_ascii=False))
    if report.get("enabled"):
        await sender.send_long(brief(report), md=True)   # 전문 발송(마크다운 렌더)
    return report


_LOCK_TTL = 900   # 활성 락 수명(초) — 단일 Claude 호출(수 분)을 넉넉히 덮는다


async def acquire_active(redis: aioredis.Redis, token: str, ttl: int = _LOCK_TTL) -> bool:
    """단일 활성 락 획득/갱신 — 락이 비었거나 내가 쥔 것이면 True(발송 담당).

    도커 research 컨테이너 + 호스트 래퍼가 동시에 떠 있어도 락을 쥔 하나만
    큐 처리·발송하므로 텔레그램이 2·3중으로 오지 않는다. 홀더가 죽으면 TTL 만료
    후 대기 프로세스가 자동 승계.
    """
    if await redis.set(RESEARCH_LOCK_KEY, token, nx=True, ex=ttl):
        return True
    if await redis.get(RESEARCH_LOCK_KEY) == token:
        await redis.expire(RESEARCH_LOCK_KEY, ttl)   # 내 락 갱신
        return True
    return False


async def run() -> None:
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    analyst = Analyst()
    sender = TelegramSender()
    token = uuid.uuid4().hex          # 이 프로세스의 활성 락 토큰
    if not analyst.enabled:
        logger.info(
            "리서치 비활성 — 이 컨테이너는 호스트의 Claude Code 구독 로그인을 볼 수 없습니다. "
            "구독(무과금)은 호스트에서 실행하세요: bash deploy/set-anthropic.sh cli && "
            "nohup bash deploy/run-research-host.sh >/tmp/research.log 2>&1 & "
            "· 또는 종량과금 .env ANTHROPIC_API_KEY 설정.")
        # 비활성이어도 컨테이너는 살아 있게(키 입력 후 재시작) — 길게 대기
        try:
            while not analyst.enabled:
                await asyncio.sleep(3600)
        finally:
            await redis.aclose()
        return
    # 중복 실행 감지: 다른 research가 이미 생존 신호를 남기고 있으면 경고.
    # (sudo로 띄운 root 프로세스가 남아 있으면 — root엔 claude 로그인이 없어 —
    #  큐를 가로채 rc=129로 실패시킨다. `sudo pkill -f research.main`으로 정리)
    if await redis.get(RESEARCH_HB_KEY):
        logger.warning("⚠️ 다른 research 프로세스가 이미 구동 중인 듯 — 중복 실행은 "
                       "큐 경쟁·CLI 실패(rc=129)를 유발. "
                       "`pgrep -f research.main` 확인 후 하나만 남기세요(sudo 포함).")
    logger.info("research start (model=%s, interval=%ss)",
                analyst.model, settings.research_interval_sec)

    async def analyze_code(code: str, name: str = "") -> None:
        item = {"code": code, "name": name}
        try:
            await run_one(redis, analyst, sender, item)
            logger.info("[research] %s 분석 완료", code)
        except Exception as exc:
            logger.warning("[research %s] 실패: %s", code, exc)

    async def is_fresh(code: str) -> bool:
        """이미 최근(interval 이내) 리포트가 있으면 재분석 생략(재시작 시 토큰 낭비 방지)."""
        raw = await redis.hget(RESEARCH_KEY, code)
        if not raw:
            return False
        try:
            r = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return False
        return bool(r.get("enabled") and r.get("report")
                    and time.time() - (r.get("ts") or 0) < settings.research_interval_sec)

    async def inversion_code(code: str) -> None:
        """매매 엔진의 역방향(감점) 검증 요청 처리 → research:inversion 저장."""
        try:
            data = await gather(redis, code) or StockData(code=code)
            result = await analyst.analyze_inversion(data)
            await redis.hset(RESEARCH_INV_KEY, code,
                             json.dumps(result, ensure_ascii=False))
            logger.info("[inversion] %s 감점 %s/30", code, result.get("penalty"))
        except Exception as exc:
            logger.warning("[DATA_ERROR] %s 역방향 검증 실패: %s", code, exc)

    async def story_code(code: str) -> None:
        """기업 스토리(다년치 공시 diff) 요청 처리 → research:story 저장 + 텔레그램."""
        try:
            data = await gather(redis, code) or StockData(code=code)
            result = await analyst.analyze_story(data)
            await redis.hset(RESEARCH_STORY_KEY, code,
                             json.dumps(result, ensure_ascii=False))
            body = (result.get("report") or "").strip()
            if result.get("enabled") and body and not body.startswith("⚠️"):
                await sender.send_long(                       # 전문 발송(마크다운 렌더)
                    f"📖 기업 스토리 {data.name or ''}({code})\n{body}", md=True)
            logger.info("[story] %s 스토리 분석 완료", code)
        except Exception as exc:
            logger.warning("[DATA_ERROR] %s 스토리 분석 실패: %s", code, exc)

    coach_fail_ts = 0.0   # 정기 점검 실패 시 30분 쿨다운(15초 루프의 재시도 스팸 방지)

    async def run_coach(reason: str) -> None:
        """아침 점검(포트폴리오 코치) 1회: 수집→분석→저장→텔레그램.

        실패도 텔레그램으로 통보 — 온디맨드 요청이 소리 없이 사라지지 않게.
        어떤 예외도 프로세스를 죽이지 않는다(내부 처리 + 쿨다운 후 재시도).
        """
        nonlocal coach_fail_ts
        try:
            block = await gather_coach(redis)
            if block is None:
                logger.info("[coach] 보유 데이터 없음(토스 미연동?) — 점검 생략(%s)", reason)
                await sender.send("🧭 점검 불가 — 보유 데이터가 없어요. "
                                  "토스 연동(TOSS_CLIENT_ID/SECRET)과 collector 로그를 확인해 주세요.")
                return
            result = await analyst.analyze_coach(block)
            await redis.set(COACH_KEY, json.dumps(result, ensure_ascii=False))
            if result.get("enabled") and not result["report"].startswith("⚠️"):
                await sender.send_long(result["report"], md=True)   # 마크다운 렌더
            else:
                await sender.send(("🧭 아침 점검 실패 — 원인:\n"
                                   + result.get("report", "")[:500]
                                   + "\n(호스트에서 claude 로그인/네트워크 확인)"))
            logger.info("[coach] 아침 점검 완료(%s)", reason)
        except Exception as exc:
            coach_fail_ts = time.time()
            logger.warning("[coach] 점검 처리 오류(%s): %s — 30분 후 재시도", reason, exc)
            await sender.send(f"🧭 아침 점검 오류({reason}) — {str(exc)[:300]}\n"
                              "30분 후 자동 재시도합니다. 계속 실패하면 /tmp/research.log 확인.")

    async def coach_last_ts() -> float:
        raw = await redis.get(COACH_KEY)
        if not raw:
            return 0.0
        try:
            return float(json.loads(raw).get("ts") or 0)
        except (json.JSONDecodeError, TypeError, ValueError):
            return 0.0

    async def heartbeat() -> None:
        """생존 신호(TTL 180s) — 텔레그램 '점검'이 호스트 구동 여부를 즉시 판별.
        긴 정기 패스(종목 사이마다 호출) 중에도 활성 락을 갱신해 락 만료를 막는다."""
        try:
            await redis.set(RESEARCH_HB_KEY, str(time.time()), ex=180)
            if await redis.get(RESEARCH_LOCK_KEY) == token:
                await redis.expire(RESEARCH_LOCK_KEY, _LOCK_TTL)
        except Exception:
            pass

    async def coach_if_requested() -> None:
        """긴 정기 패스 중에도 점검 요청을 우선 처리(종목 사이마다 호출)."""
        if settings.coach_enabled and await redis.spop(COACH_REQ_KEY):
            await run_coach("요청")

    last_full = 0.0
    waiting_logged = False
    try:
        while True:
            # 단일 활성 락: 락을 못 쥐면(다른 research가 활성) 큐 처리·발송을 하지
            # 않고 대기 — 도커+호스트 중복 실행 시 텔레그램 2·3중 발송을 원천 차단.
            if not await acquire_active(redis, token):
                if not waiting_logged:
                    logger.warning("다른 research가 활성(락 보유) — 이 프로세스는 대기(발송 안 함). "
                                   "도커 컨테이너+호스트가 함께 떠 있어도 하나만 발송합니다.")
                    waiting_logged = True
                await asyncio.sleep(15)
                continue
            if waiting_logged:
                logger.info("활성 락 획득 — 이 프로세스가 발송 담당")
                waiting_logged = False
            # 사이클 전체를 방어 — Redis 순단·일시 오류가 프로세스를 죽여
            # '아침 점검 무소식'이 되는 일을 막는다(다음 사이클 재시도).
            try:
                await heartbeat()
                # 코치: 온디맨드('지금 점검' 버튼) + 매일 코치 시각(KST) 정기 1회
                if settings.coach_enabled:
                    if await redis.spop(COACH_REQ_KEY):
                        await run_coach("요청")
                    elif (time.time() - coach_fail_ts > 1800
                          and should_run(time.time(), await coach_last_ts(),
                                         settings.coach_hour_kst)):
                        await run_coach(f"정기 {settings.coach_hour_kst}시")
                # 0) 매매 엔진의 역방향 검증 요청(감점) — 매수 판단에 직결되므로 최우선
                inv = await redis.spop(RESEARCH_INV_REQ_KEY, 5)
                for code in (inv or []):
                    await heartbeat()
                    await coach_if_requested()   # 점검 요청은 긴 작업 사이에도 우선
                    await inversion_code(code)
                    await asyncio.sleep(2)
                # 1) 온디맨드 요청(대시보드 🧠 '다시 분석' → API가 큐에 넣음)은 무조건 처리
                reqs = await redis.spop(RESEARCH_REQ_KEY, 5)
                for code in (reqs or []):
                    await heartbeat()
                    await coach_if_requested()
                    await analyze_code(code)
                    await asyncio.sleep(2)
                # 1b) 스토리 분석 요청(📖 다년치 공시 diff — 무거워서 온디맨드 전용)
                stories = await redis.spop(RESEARCH_STORY_REQ_KEY, 3)
                for code in (stories or []):
                    await heartbeat()
                    await coach_if_requested()
                    await story_code(code)
                    await asyncio.sleep(2)
                # 2) 정기 전체 분석 — 최근 리포트가 있는 종목은 건너뜀(재시작해도 재분석 안 함)
                if time.time() - last_full >= 3600:   # 1시간마다 점검(신규/만료분만 분석)
                    for item in await effective_watchlist(redis):
                        await heartbeat()
                        await coach_if_requested()
                        if await is_fresh(item["code"]):
                            continue
                        await analyze_code(item["code"], item.get("name", ""))
                        await asyncio.sleep(2)
                    last_full = time.time()
            except Exception as exc:
                logger.warning("[research] 사이클 오류(프로세스는 계속 살아있음): %s", exc)
                await asyncio.sleep(30)
            await asyncio.sleep(15)   # 요청 큐 폴링 주기
    finally:
        try:                          # 종료 시 내 락 해제(대기 프로세스 즉시 승계)
            if await redis.get(RESEARCH_LOCK_KEY) == token:
                await redis.delete(RESEARCH_LOCK_KEY)
        except Exception:
            pass
        await redis.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("research stopped")
