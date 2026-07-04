"""AI 리서치 엔트리포인트 — 관심종목을 정기적으로 거장 렌즈로 분석.

ANTHROPIC_API_KEY가 없으면 비활성(idle). 각 분석 결과를 Redis(research:reports)에
저장하고 텔레그램으로 요약 브리핑. 실행: python -m research.main
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import redis.asyncio as aioredis

from collector.stock.kis import effective_watchlist
from notifier.telegram import TelegramSender
from research.analyst import Analyst
from research.data import StockData, gather
from shared.redis_keys import RESEARCH_KEY, RESEARCH_REQ_KEY
from shared.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("research")


def brief(report: dict) -> str:
    """리포트에서 텔레그램 브리핑 문구 추출(앞부분 발췌)."""
    head = (report.get("report") or "").strip().splitlines()
    snippet = "\n".join(head[:6]) if head else "(내용 없음)"
    return f"🧠가치투자 리서치 {report.get('name','')}({report.get('code','')})\n{snippet}"


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
        await sender.send(brief(report))
    return report


async def run() -> None:
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    analyst = Analyst()
    sender = TelegramSender()
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

    last_full = 0.0
    try:
        while True:
            # 1) 온디맨드 요청(대시보드 🧠 '다시 분석' → API가 큐에 넣음)은 무조건 처리
            reqs = await redis.spop(RESEARCH_REQ_KEY, 5)
            for code in (reqs or []):
                await analyze_code(code)
                await asyncio.sleep(2)
            # 2) 정기 전체 분석 — 최근 리포트가 있는 종목은 건너뜀(재시작해도 재분석 안 함)
            if time.time() - last_full >= 3600:   # 1시간마다 점검(신규/만료분만 분석)
                for item in await effective_watchlist(redis):
                    if await is_fresh(item["code"]):
                        continue
                    await analyze_code(item["code"], item.get("name", ""))
                    await asyncio.sleep(2)
                last_full = time.time()
            await asyncio.sleep(15)   # 요청 큐 폴링 주기
    finally:
        await redis.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("research stopped")
