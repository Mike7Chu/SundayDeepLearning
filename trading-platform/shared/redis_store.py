"""Redis 저장 헬퍼."""
from __future__ import annotations

import redis.asyncio as aioredis


async def replace_hash(redis: aioredis.Redis, key: str, mapping: dict) -> None:
    """해시를 통째로 교체(DELETE+HSET 원자 트랜잭션).

    누적 HSET과 달리 이번 주기에 없는 필드(상폐/사라진 코인)를 제거한다.
    mapping이 비어 있으면 아무것도 하지 않음(거래소 일시 실패 시 기존값 보존).
    """
    if not mapping:
        return
    async with redis.pipeline(transaction=True) as pipe:
        pipe.delete(key)
        pipe.hset(key, mapping=mapping)
        await pipe.execute()
