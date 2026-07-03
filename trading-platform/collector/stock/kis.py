"""한국투자증권(KIS) OpenAPI 클라이언트 — 국내주식 현재가.

키 미설정이면 비활성(enabled=False). 토큰은 캐시(만료 전 재사용).
모의투자(kis_paper=True) 도메인 기본. 키움 OCX와 달리 Linux/RPi에서 동작.
"""
from __future__ import annotations

import asyncio
import json
import logging
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


def normalize_watch_item(code: str, name: str = "") -> dict | None:
    """관심종목 입력 정규화. 6자리 숫자 코드만 허용(아니면 None)."""
    code = (code or "").strip()
    if not (code.isdigit() and len(code) == 6):
        return None
    return {"code": code, "name": (name or "").strip()}


class KISClient:
    def __init__(self):
        self.base = _PAPER if settings.kis_paper else _REAL
        self._token: str | None = None
        self._exp: float = 0.0
        self._lock = asyncio.Lock()   # 토큰 발급 직렬화(동시 발급 → KIS 1분당 1회 제한 위반)
        self._retry_after: float = 0.0

    @property
    def enabled(self) -> bool:
        return bool(settings.kis_app_key and settings.kis_app_secret)

    async def _token_value(self, client: httpx.AsyncClient) -> str:
        if self._token and time.time() < self._exp - 60:
            return self._token
        # 여러 루프가 공유하는 클라이언트라, 락으로 한 번만 발급(나머지는 캐시 재사용).
        async with self._lock:
            if self._token and time.time() < self._exp - 60:
                return self._token
            now = time.time()
            if now < self._retry_after:
                # KIS는 토큰 발급을 1분당 1회로 제한 → 실패 후 60초는 재요청 금지(스팸 방지).
                raise RuntimeError("KIS 토큰 재발급 대기(1분당 1회 제한)")
            r = await client.post(f"{self.base}/oauth2/tokenP", json={
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
            self._token = d["access_token"]
            self._exp = now + int(d.get("expires_in", 86400))
            self._retry_after = 0.0
            return self._token

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

    async def fetch_price(self, client: httpx.AsyncClient, code: str) -> dict:
        token = await self._token_value(client)
        params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code}
        r = await client.get(
            f"{self.base}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=self._headers(token, "FHKST01010100"), params=params)
        r.raise_for_status()
        return parse_price(self._check_rt(r.json(), f"현재가 {code}").get("output", {}))

    async def fetch_daily(self, client: httpx.AsyncClient, code: str,
                          days: int = 120) -> list[dict]:
        """일봉 시계열(최근 days영업일). 시그널 계산용. 오래된→최신 순."""
        token = await self._token_value(client)
        import datetime as _dt
        end = _dt.date.today()
        start = end - _dt.timedelta(days=int(days * 1.6) + 10)  # 영업일 여유
        params = {
            "fid_cond_mrkt_div_code": "J", "fid_input_iscd": code,
            "fid_input_date_1": start.strftime("%Y%m%d"),
            "fid_input_date_2": end.strftime("%Y%m%d"),
            "fid_period_div_code": "D", "fid_org_adj_prc": "0",
        }
        r = await client.get(
            f"{self.base}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            headers=self._headers(token, "FHKST03010100"), params=params)
        r.raise_for_status()
        return parse_daily(self._check_rt(r.json(), f"일봉 {code}").get("output2", []))[-days:]

    async def fetch_dividend(self, client: httpx.AsyncClient, code: str) -> dict:
        """배당 일정/배당금. 실패/미지원이면 빈 items."""
        token = await self._token_value(client)
        import datetime as _dt
        today = _dt.date.today()
        params = {
            "cts": "", "gb1": "0",
            "f_dt": (today - _dt.timedelta(days=400)).strftime("%Y%m%d"),
            "t_dt": (today + _dt.timedelta(days=120)).strftime("%Y%m%d"),
            "sht_cd": code,
        }
        r = await client.get(
            f"{self.base}/uapi/domestic-stock/v1/ksdinfo/dividend",
            headers=self._headers(token, "HHKDB669102C0"), params=params)
        r.raise_for_status()
        return {"code": code, "items": parse_dividend(
            self._check_rt(r.json(), f"배당 {code}").get("output1", []))}


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
    }
