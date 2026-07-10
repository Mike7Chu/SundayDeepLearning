"""한국투자증권(KIS) OpenAPI 클라이언트 — 국내주식 현재가.

키 미설정이면 비활성(enabled=False). 토큰은 캐시(만료 전 재사용).
모의투자(kis_paper=True) 도메인 기본. 키움 OCX와 달리 Linux/RPi에서 동작.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from functools import lru_cache
from pathlib import Path

import httpx
import yaml

from shared.redis_keys import WATCHLIST_KEY
from shared.settings import settings

logger = logging.getLogger(__name__)

_REAL = "https://openapi.koreainvestment.com:9443"
_PAPER = "https://openapivts.koreainvestment.com:29443"
_WATCHLIST = Path(__file__).resolve().parent.parent.parent / "config" / "stocks.yaml"


@lru_cache(maxsize=1)
def load_watchlist() -> list[dict]:
    return yaml.safe_load(_WATCHLIST.read_text()).get("watchlist", [])


async def effective_watchlist(redis) -> list[dict]:
    """대시보드에서 편집한 관심종목(Redis stock:watchlist) 우선, 없으면 config/stocks.yaml.

    사용자가 UI에서 전부 삭제하면 빈 리스트로 저장되며, 그 경우 빈 리스트를 존중한다.
    """
    try:
        raw = await redis.get(WATCHLIST_KEY)
    except Exception:
        raw = None
    if raw is not None:
        try:
            items = json.loads(raw)
            if isinstance(items, list):
                return items
        except (ValueError, TypeError):
            pass
    return load_watchlist()


def is_kr_code(code: str) -> bool:
    """국내 6자리 종목코드 여부(아니면 미국 티커로 취급)."""
    return bool(code) and code.isdigit() and len(code) == 6


def normalize_watch_item(code: str, name: str = "") -> dict | None:
    """관심종목 입력 정규화: 국내 6자리 숫자 또는 미국 티커(영문 1~6자, 예: NVDA).

    미국 티커는 대문자 통일(BRK.B 같은 점 표기 허용). 그 외 형식은 None.
    """
    code = (code or "").strip()
    if is_kr_code(code):
        return {"code": code, "name": (name or "").strip()}
    if re.fullmatch(r"[A-Za-z]{1,6}(?:\.[A-Za-z]{1,2})?", code):
        return {"code": code.upper(), "name": (name or "").strip()}
    return None


class KISClient:
    def __init__(self):
        # 조회 전용이므로 kis_quote_real면 실전 도메인 고정(예탁원 배당 등 모의도메인 미제공 대응).
        self.base = _REAL if (settings.kis_quote_real or not settings.kis_paper) else _PAPER
        # 주문은 계좌 종류를 따라감: 모의계좌(kis_paper=true)→vts, 실전→real.
        self.order_base = _PAPER if settings.kis_paper else _REAL
        self._tokens: dict[str, tuple[str, float]] = {}   # base → (token, 만료시각)
        self._lock = asyncio.Lock()   # 토큰 발급 직렬화(동시 발급 → KIS 1분당 1회 제한 위반)
        self._retry_after: float = 0.0
        # 요청 레이트리밋(전 루프 공유): 버스트로 KIS가 500을 뱉는 것 방지.
        self._throttle_lock = asyncio.Lock()
        self._last_call: float = 0.0
        self._min_interval: float = 1.0 / max(1.0, settings.kis_rate_per_sec)
        self._http: httpx.AsyncClient | None = None

    def http(self) -> httpx.AsyncClient:
        """전 루프 공유 HTTP 클라이언트. KIS 실전 도메인은 동시 연결 수를 제한하므로
        루프마다 새 커넥션을 열지 않고 이 하나(커넥션 풀 2)를 재사용한다."""
        if self._http is None:
            # 커넥션 1개로 고정 — KIS 실전 도메인의 동시연결 제한('All connection attempts
            # failed')을 확실히 피한다(throttle이 요청을 직렬화하므로 1개면 충분).
            self._http = httpx.AsyncClient(
                timeout=20,
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=1))
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    @property
    def enabled(self) -> bool:
        return bool(settings.kis_app_key and settings.kis_app_secret)

    async def _token_value(self, client: httpx.AsyncClient,
                           base: str | None = None) -> str:
        """도메인별 토큰(조회=base, 주문=order_base가 다를 수 있어 base 단위 캐시)."""
        base = base or self.base
        cached = self._tokens.get(base)
        if cached and time.time() < cached[1] - 60:
            return cached[0]
        # 여러 루프가 공유하는 클라이언트라, 락으로 한 번만 발급(나머지는 캐시 재사용).
        async with self._lock:
            cached = self._tokens.get(base)
            if cached and time.time() < cached[1] - 60:
                return cached[0]
            now = time.time()
            if now < self._retry_after:
                # KIS는 토큰 발급을 1분당 1회로 제한 → 실패 후 60초는 재요청 금지(스팸 방지).
                raise RuntimeError("KIS 토큰 재발급 대기(1분당 1회 제한)")
            r = await client.post(f"{base}/oauth2/tokenP", json={
                "grant_type": "client_credentials",
                "appkey": settings.kis_app_key,
                "appsecret": settings.kis_app_secret,
            })
            d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if "access_token" not in d:
                # KIS 토큰 에러는 HTTP 200/403에 error_description(EGW…)로 옴. 60초 백오프.
                self._retry_after = now + 60
                logger.warning("KIS 토큰 발급 실패(60s 대기): %s",
                               d.get("error_description") or d.get("msg1") or r.status_code)
                raise RuntimeError("KIS 토큰 발급 실패")
            token = d["access_token"]
            self._tokens[base] = (token, now + int(d.get("expires_in", 86400)))
            self._retry_after = 0.0
            return token

    def _headers(self, token: str, tr_id: str) -> dict:
        return {
            "authorization": f"Bearer {token}",
            "appkey": settings.kis_app_key,
            "appsecret": settings.kis_app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    @staticmethod
    def _check_rt(body: dict, ctx: str) -> dict:
        """KIS는 HTTP 200에 rt_cd!='0'(업무에러)을 담아 조용히 실패한다.

        rt_cd가 '0'이 아니면 msg_cd/msg1을 경고 로그(권한·도메인·tr_id 즉시 진단).
        """
        if isinstance(body, dict) and body.get("rt_cd") not in (None, "0"):
            logger.warning("KIS %s 실패: rt_cd=%s msg_cd=%s msg=%s",
                           ctx, body.get("rt_cd"), body.get("msg_cd"),
                           body.get("msg1"))
        return body

    async def _throttle(self) -> None:
        """전 루프 공유 레이트리밋 — 요청 간 최소 간격 유지(버스트 500 방지)."""
        async with self._throttle_lock:
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()

    async def _get(self, client: httpx.AsyncClient, path: str, tr_id: str,
                   params: dict, ctx: str, retries: int = 3) -> dict:
        """throttle + GET + 일시적 5xx/연결오류 재시도(백오프) → _check_rt된 응답 dict."""
        token = await self._token_value(client)
        for attempt in range(retries + 1):
            await self._throttle()
            try:
                r = await client.get(f"{self.base}{path}",
                                     headers=self._headers(token, tr_id), params=params)
            except httpx.HTTPError as exc:
                # 연결 실패/타임아웃(실전 도메인 간헐) → 백오프 후 재시도
                if attempt < retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise RuntimeError(f"KIS {ctx} 연결 실패: {exc!r}") from exc
            if r.status_code >= 500 and attempt < retries:
                await asyncio.sleep(0.4 * (attempt + 1))   # KIS 일시적 500 → 재시도
                continue
            r.raise_for_status()
            try:
                body = r.json()
            except ValueError:
                # 비-JSON 응답(HTML 에러 페이지 등) → 스니펫 로깅 후 실패
                snippet = r.text[:200].replace("\n", " ")
                logger.warning("KIS %s 비-JSON 응답: %s", ctx, snippet)
                raise RuntimeError(f"KIS {ctx} 비-JSON 응답")
            return self._check_rt(body, ctx)
        raise RuntimeError(f"KIS {ctx} 반복 실패")

    async def fetch_price(self, client: httpx.AsyncClient, code: str) -> dict:
        body = await self._get(
            client, "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100", {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code},
            f"현재가 {code}")
        return parse_price(body.get("output", {}))

    async def fetch_daily(self, client: httpx.AsyncClient, code: str,
                          days: int = 120) -> list[dict]:
        """일봉 시계열(최근 days영업일). 시그널 계산용. 오래된→최신 순."""
        import datetime as _dt
        end = _dt.date.today()
        start = end - _dt.timedelta(days=int(days * 1.6) + 10)  # 영업일 여유
        params = {
            "fid_cond_mrkt_div_code": "J", "fid_input_iscd": code,
            "fid_input_date_1": start.strftime("%Y%m%d"),
            "fid_input_date_2": end.strftime("%Y%m%d"),
            "fid_period_div_code": "D", "fid_org_adj_prc": "0",
        }
        body = await self._get(
            client, "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            "FHKST03010100", params, f"일봉 {code}")
        return parse_daily(body.get("output2", []))[-days:]

    async def fetch_dividend(self, client: httpx.AsyncClient, code: str) -> dict:
        """배당 일정/배당금. 실패/미지원이면 빈 items."""
        import datetime as _dt
        today = _dt.date.today()
        params = {
            "cts": "", "gb1": "0", "high_gb": "",   # high_gb 누락 시 빈 응답 방지
            "f_dt": (today - _dt.timedelta(days=1200)).strftime("%Y%m%d"),  # ~3년 이력
            "t_dt": (today + _dt.timedelta(days=120)).strftime("%Y%m%d"),
            "sht_cd": code,
        }
        body = await self._get(
            client, "/uapi/domestic-stock/v1/ksdinfo/dividend",
            "HHKDB669102C0", params, f"배당 {code}", retries=5)
        out1 = body.get("output1", [])
        items = parse_dividend(out1)
        if not items:
            # 진단: rt_cd/msg1/output1 개수 확인(예탁원 응답 원인 파악용).
            sample = out1[0] if isinstance(out1, list) and out1 else None
            logger.info("[div %s] rt_cd=%s msg=%s output1=%s keys=%s",
                        code, body.get("rt_cd"), body.get("msg1"),
                        len(out1) if isinstance(out1, list) else type(out1).__name__,
                        list(sample.keys()) if isinstance(sample, dict) else None)
        return {"code": code, "items": items}

    # ---- 주문 (자동매매 전용 — 호출부에서 kis_trading_enabled 등 게이트 필수) ----
    async def place_order(self, client: httpx.AsyncClient, *, code: str,
                          side: str, qty: int, price: int) -> dict:
        """국내주식 현금 지정가 주문. kis_paper=true면 모의투자 주문(리허설).

        KIS_ACCOUNT 형식 '12345678-01'(종합계좌 8자리-상품코드 2자리).
        hashkey는 개인계좌 선택사항이라 생략. 실패는 rt_cd/msg1로 예외.
        """
        if "-" not in (settings.kis_account or ""):
            raise RuntimeError("KIS_ACCOUNT 미설정/형식 오류(예: 12345678-01)")
        cano, prdt = settings.kis_account.split("-", 1)
        paper = settings.kis_paper
        tr_id = (("VTTC0802U" if paper else "TTTC0802U") if side.upper() == "BUY"
                 else ("VTTC0801U" if paper else "TTTC0801U"))
        token = await self._token_value(client, self.order_base)
        body = {
            "CANO": cano.strip(), "ACNT_PRDT_CD": prdt.strip(),
            "PDNO": code, "ORD_DVSN": "00",            # 00=지정가
            "ORD_QTY": str(int(qty)), "ORD_UNPR": str(int(price)),
        }
        await self._throttle()
        r = await client.post(
            f"{self.order_base}/uapi/domestic-stock/v1/trading/order-cash",
            headers=self._headers(token, tr_id), json=body)
        r.raise_for_status()
        d = self._check_rt(r.json(), f"주문 {side} {code}")
        if d.get("rt_cd") != "0":
            raise RuntimeError(f"KIS 주문 거부: {d.get('msg1') or d.get('msg_cd')}")
        out = d.get("output", {}) or {}
        return {"order_id": out.get("ODNO", ""),
                "org_no": out.get("KRX_FWDG_ORD_ORGNO", ""),
                "time": out.get("ORD_TMD", ""), "paper": paper}


def _f(v) -> float | None:
    """문자열 숫자 → float (빈값/None은 None)."""
    try:
        if v in (None, "", "0"):
            return None if v in (None, "") else 0.0
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_daily(output2: list) -> list[dict]:
    """KIS inquire-daily-itemchartprice output2 → 일봉 리스트(오래된→최신).

    응답은 최신→과거 순이라 뒤집는다. 빈/0 종가 행은 제외(휴장 등).
    """
    rows: list[dict] = []
    for o in output2 or []:
        close = _f(o.get("stck_clpr"))
        if not close:
            continue
        rows.append({
            "date": o.get("stck_bsop_date", ""),
            "close": close,
            "high": _f(o.get("stck_hgpr")),
            "low": _f(o.get("stck_lwpr")),
            "volume": _f(o.get("acml_vol")),
        })
    rows.sort(key=lambda r: r["date"])   # 오래된→최신
    return rows


def parse_dividend(output1: list) -> list[dict]:
    """KIS ksdinfo/dividend output1 → 배당 항목 리스트."""
    items: list[dict] = []
    for o in output1 or []:
        per_share = _f(o.get("per_sto_divi_amt") or o.get("divi_amt"))
        if per_share is None:
            continue
        items.append({
            "date": o.get("record_date") or o.get("divi_base_dt") or "",
            "pay_date": o.get("divi_pay_dt") or "",
            "per_share": per_share,
            "kind": o.get("divi_kind") or o.get("divi_rate") or "",
        })
    items.sort(key=lambda r: r["date"])
    return items


def parse_price(o: dict) -> dict:
    """KIS inquire-price output → 시세 + 밸류에이션(순수 함수, 테스트 용이).

    inquire-price 응답엔 현재가/전일대비 외 per/pbr/eps/bps도 포함된다.
    """
    return {
        "price": float(o.get("stck_prpr") or 0),       # 현재가
        "change_pct": float(o.get("prdy_ctrt") or 0),  # 전일대비율(%)
        "per": _f(o.get("per")),                        # 주가수익비율
        "pbr": _f(o.get("pbr")),                        # 주가순자산비율
        "eps": _f(o.get("eps")),                        # 주당순이익
        "bps": _f(o.get("bps")),                        # 주당순자산
        "market_cap": _f(o.get("hts_avls")),            # 시가총액(억원)
        "high_52w": _f(o.get("w52_hgpr")),              # 52주 최고
        "low_52w": _f(o.get("w52_lwpr")),               # 52주 최저
        "open": _f(o.get("stck_oprc")),                 # 당일 시가
        "high": _f(o.get("stck_hgpr")),                 # 당일 고가
        "low": _f(o.get("stck_lwpr")),                  # 당일 저가
        # 당일 누적 거래대금(억원) — 빛의기둥 장중 감지용
        "value_eok": (round(_f(o.get("acml_tr_pbmn")) / 1e8, 1)
                      if _f(o.get("acml_tr_pbmn")) else None),
    }
