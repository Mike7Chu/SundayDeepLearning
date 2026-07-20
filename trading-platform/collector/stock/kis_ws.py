"""KIS 실시간 웹소켓(주식 체결가 H0STCNT0) — 관심∪보유 종목 진짜 실시간 시세.

REST 폴링(15초)과 별개로, 장중에는 체결이 일어날 때마다 가격·등락률이
Redis(stock:quote)에 즉시 반영된다. 연결당 등록 41건 제한 → 국내 관심종목과
보유 종목을 우선 등록(넘치면 보유 우선). 미국 종목은 토스 REST가 담당.

프로토콜(공식 문서 기준):
- 접속키: POST {REST}/oauth2/Approval (appkey+secretkey) → approval_key
- 접속: ws://ops.koreainvestment.com:21000 (실전) / :31000 (모의)
- 등록: JSON {header:{approval_key,custtype,tr_type:"1"}, body:{input:{tr_id,tr_key}}}
- 수신: 제어 메시지는 JSON(SUBSCRIBE SUCCESS·PINGPONG), 시세는 파이프 구분 평문
  "암호화|TR_ID|건수|데이터" — 데이터는 ^ 구분, 여러 건이면 필드가 이어붙음.
- PINGPONG은 받은 그대로 되돌려 보내야 연결 유지.
순수 파서(빌더·파싱·장시간 판정)는 네트워크 없이 테스트 가능하게 분리.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import time
from typing import Awaitable, Callable

import httpx

from shared.settings import settings

logger = logging.getLogger("collector.kis_ws")

WS_REAL = "ws://ops.koreainvestment.com:21000"
WS_PAPER = "ws://ops.koreainvestment.com:31000"
TR_TICK = "H0STCNT0"          # 국내주식 실시간 체결가
MAX_SUBS = 41                  # 실전 연결당 등록 상한(공식)

# H0STCNT0 응답 필드 인덱스(^ 구분): 0=종목코드 2=현재가 3=전일대비부호 5=전일대비율 13=누적거래량
_F_CODE, _F_PRICE, _F_SIGN, _F_RATE, _F_ACC_VOL = 0, 2, 3, 5, 13
_FIELDS_PER_TICK = 46          # 레코드당 필드 수(문서 기준) — 다건 수신 분리용


# ---------------- 순수 함수(테스트 대상) ----------------

def build_subscribe(approval_key: str, code: str, subscribe: bool = True,
                    tr_id: str = TR_TICK) -> str:
    """실시간 등록(tr_type=1)/해제(2) 요청 JSON."""
    return json.dumps({
        "header": {"approval_key": approval_key, "custtype": "P",
                   "tr_type": "1" if subscribe else "2",
                   "content-type": "utf-8"},
        "body": {"input": {"tr_id": tr_id, "tr_key": code}},
    })


def is_pingpong(raw: str) -> bool:
    """수신 원문이 PINGPONG 제어 메시지인지 — 그대로 회신해야 연결 유지."""
    return raw.startswith("{") and '"tr_id":"PINGPONG"' in raw.replace(" ", "")


def parse_ticks(raw: str) -> list[dict]:
    """파이프 평문 수신 → 체결 틱 리스트(순수 함수). 형식 오류는 빈 리스트.

    "0|H0STCNT0|001|005930^093012^71900^2^100^0.14^…" →
    [{"code","price","change_pct"}]. 부호(3): 1·2=상승, 4·5=하락, 3=보합.
    암호화(첫 필드 '1')는 미지원 — 체결가는 평문이라 해당 없음.
    """
    if not raw or raw.startswith("{"):
        return []
    parts = raw.split("|")
    if len(parts) < 4 or parts[0] != "0" or parts[1] != TR_TICK:
        return []
    try:
        count = int(parts[2])
    except ValueError:
        return []
    fields = parts[3].split("^")
    per = len(fields) // count if count > 0 else 0
    if per < _F_ACC_VOL + 1:
        return []
    out = []
    for i in range(count):
        f = fields[i * per:(i + 1) * per]
        try:
            price = float(f[_F_PRICE])
            rate = abs(float(f[_F_RATE]))
            if f[_F_SIGN] in ("4", "5"):
                rate = -rate
            out.append({"code": f[_F_CODE], "price": price,
                        "change_pct": round(rate, 2)})
        except (ValueError, IndexError):
            continue
    return out


def is_krx_session(now: datetime.datetime | None = None) -> bool:
    """KRX 정규장(±여유) 여부 — 평일 08:50~15:40 KST. 장외엔 웹소켓 접속 불필요."""
    kst = datetime.timezone(datetime.timedelta(hours=9))
    n = (now or datetime.datetime.now(tz=kst)).astimezone(kst)
    if n.weekday() >= 5:
        return False
    hm = n.hour * 100 + n.minute
    return 850 <= hm <= 1540


def pick_subs(watch_codes: list[str], held_codes: list[str],
              cap: int = MAX_SUBS) -> list[str]:
    """등록 대상 선정(순수): 보유 우선 + 관심, 국내 6자리만, 상한 cap."""
    out: list[str] = []
    for c in list(held_codes) + list(watch_codes):
        if c and c.isdigit() and len(c) == 6 and c not in out:
            out.append(c)
        if len(out) >= cap:
            break
    return out


# ---------------- 네트워크 루프 ----------------

async def _approval_key(client: httpx.AsyncClient, base: str) -> str:
    r = await client.post(f"{base}/oauth2/Approval", json={
        "grant_type": "client_credentials",
        "appkey": settings.kis_app_key,
        "secretkey": settings.kis_app_secret,
    })
    r.raise_for_status()
    key = r.json().get("approval_key")
    if not key:
        raise RuntimeError(f"approval_key 없음: {r.text[:200]}")
    return key


async def realtime_loop(redis, kis,
                        merge: Callable[..., Awaitable[None]],
                        desired_codes: Callable[[], Awaitable[list[str]]]) -> None:
    """장중 웹소켓 유지: 체결 틱 → merge(redis, code, "", fields) 즉시 반영.

    - merge: collector.main.merge_quote (병합 저장 — PER/PBR 재계산 포함)
    - desired_codes: 현재 등록해야 할 코드 목록(관심∪보유, 이미 cap 적용)
    - 종목당 1초 스로틀(체결 폭주 시 Redis 쓰기 보호)
    - 끊기면 10초 백오프 재접속, 장외엔 접속 안 함
    """
    if not kis.enabled or not settings.kis_ws_enabled:
        logger.info("KIS 웹소켓 비활성(키 없음 또는 KIS_WS_ENABLED=false) — REST 폴링만 사용")
        return
    try:
        import websockets
    except ImportError:
        logger.warning("websockets 패키지 없음 — pip install websockets 후 실시간 활성화")
        return
    url = WS_REAL if (settings.kis_quote_real or not settings.kis_paper) else WS_PAPER
    approval: tuple[str, float] | None = None       # (key, 발급시각)
    last_write: dict[str, float] = {}
    while True:
        if not is_krx_session():
            await asyncio.sleep(60)
            continue
        try:
            if approval is None or time.time() - approval[1] > 20 * 3600:
                async with httpx.AsyncClient(timeout=10) as hc:
                    approval = (await _approval_key(hc, kis.base), time.time())
            subs: set[str] = set()
            async with websockets.connect(url, ping_interval=None,
                                          close_timeout=5) as ws:
                logger.info("[ws] KIS 실시간 접속(%s)", url)
                last_sync = 0.0
                while is_krx_session():
                    # 등록 목록 동기화(60초마다 — 관심종목 편집·신규 매수 반영)
                    if time.time() - last_sync > 60:
                        want = set(await desired_codes())
                        for c in sorted(want - subs):
                            await ws.send(build_subscribe(approval[0], c, True))
                            await asyncio.sleep(0.05)
                        for c in sorted(subs - want):
                            await ws.send(build_subscribe(approval[0], c, False))
                            await asyncio.sleep(0.05)
                        if want != subs:
                            logger.info("[ws] 실시간 등록 %d종목", len(want))
                        subs = want
                        last_sync = time.time()
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=15)
                    except asyncio.TimeoutError:
                        continue                     # 조용한 구간 — 동기화 체크로 복귀
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", "ignore")
                    if is_pingpong(raw):
                        await ws.send(raw)           # 그대로 회신(연결 유지)
                        continue
                    for t in parse_ticks(raw):
                        now = time.time()
                        if now - last_write.get(t["code"], 0) < 1.0:
                            continue                 # 종목당 1초 스로틀
                        last_write[t["code"]] = now
                        await merge(redis, t["code"], "", {
                            "price": t["price"], "change_pct": t["change_pct"],
                            "currency": "KRW", "rt": True})
            logger.info("[ws] 장 마감 — 실시간 종료(다음 장 시작 시 재접속)")
        except Exception as exc:
            logger.warning("[ws] 실시간 연결 오류: %s — 10초 후 재접속", exc)
            await asyncio.sleep(10)
