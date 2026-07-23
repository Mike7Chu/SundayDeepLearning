"""API 응답 TTL 캐시(프로세스 메모리) — 전체 시장 스캔류 엔드포인트 가속.

대시보드가 12초마다 자동 갱신하는데, 전체 시장(3,600+종목) 파싱·스코어링을
매 요청 반복하면 라즈베리파이에서 화면이 수 초씩 걸린다. 원본 데이터 자체가
5분(가격 스윕)~30분(펀더멘털) 주기로만 바뀌므로 짧은 TTL 재사용이 무손실.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

_store: dict[str, tuple[float, Any]] = {}
_locks: dict[str, asyncio.Lock] = {}
_refreshing: set[str] = set()


async def get_or_compute(key: str, ttl: float,
                         factory: Callable[[], Awaitable[Any]]) -> Any:
    """키의 캐시가 신선하면 반환, 아니면 factory 실행 후 저장.

    동일 키 동시 요청은 Lock으로 1회만 계산(12초 자동갱신 + 수동 새로고침 중복 방지).
    """
    hit = _store.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < ttl:
        return hit[1]
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        hit = _store.get(key)                     # 락 대기 중 채워졌으면 재사용
        now = time.monotonic()
        if hit and now - hit[0] < ttl:
            return hit[1]
        val = await factory()
        _store[key] = (time.monotonic(), val)
        return val


async def _refresh(key: str, factory: Callable[[], Awaitable[Any]]) -> None:
    try:
        val = await factory()
        _store[key] = (time.monotonic(), val)
    except Exception:
        pass                                      # 실패 시 기존 캐시 유지(다음에 재시도)
    finally:
        _refreshing.discard(key)


async def get_or_swr(key: str, ttl: float,
                     factory: Callable[[], Awaitable[Any]]) -> Any:
    """Stale-While-Revalidate — 무거운(네트워크 다수) 계산을 요청 경로에서 뺀다.

    신선하면 그대로 반환. 오래됐지만 값이 있으면 **오래된 값을 즉시 반환**하고
    백그라운드로 갱신(요청은 절대 대기하지 않음). 값이 아예 없을 때(콜드)만 1회 대기.
    레이더처럼 Toss REST를 수십 번 부르는 엔드포인트의 체감 지연을 없앤다.
    """
    hit = _store.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < ttl:
        return hit[1]
    if hit is not None:                           # 오래된 값 존재 → 즉시 반환 + 백그라운드 갱신
        if key not in _refreshing:
            _refreshing.add(key)
            asyncio.create_task(_refresh(key, factory))
        return hit[1]
    # 콜드 스타트(값 없음) — 1회만 동기 계산(동시 요청은 Lock으로 병합)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        hit = _store.get(key)
        if hit is not None:
            return hit[1]
        val = await factory()
        _store[key] = (time.monotonic(), val)
        return val
