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
        # 이중 앱키: 실전 조회키가 있으면 시세/재무/해외는 실전 도메인(안정, 모의 500 회피),
        # 주문은 별도(모의계좌면 모의 도메인+모의키). 실전키 없으면 기존 로직.
        self._has_real = bool(settings.kis_real_app_key and settings.kis_real_app_secret)
        if self._has_real:
            self.base = _REAL
        else:
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

    def _creds_for(self, base: str) -> tuple[str, str]:
        """도메인별 앱키/시크릿 — 실전 도메인은 실전 조회키(있으면), 그 외는 주문 앱키.

        앱키는 도메인 전용(실전키는 실전 도메인, 모의키는 모의 도메인에서만 토큰 발급).
        """
        if base == _REAL and self._has_real:
            return settings.kis_real_app_key, settings.kis_real_app_secret
        return settings.kis_app_key, settings.kis_app_secret

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
            key, secret = self._creds_for(base)
            r = await client.post(f"{base}/oauth2/tokenP", json={
                "grant_type": "client_credentials",
                "appkey": key, "appsecret": secret,
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

    def _headers(self, token: str, tr_id: str, base: str | None = None) -> dict:
        # appkey/시크릿은 토큰을 발급한 도메인과 반드시 일치해야 한다(도메인 전용).
        key, secret = self._creds_for(base or self.base)
        return {
            "authorization": f"Bearer {token}",
            "appkey": key,
            "appsecret": secret,
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

    # ---- 해외(미국) 시세 — 토스 대체(토큰 안정). 시세용 EXCD는 NAS/NYS/AMS ----
    async def fetch_overseas_price(self, client: httpx.AsyncClient, symbol: str,
                                   excd: str = "NAS") -> dict:
        """미국주식 현재가(HHDFS00000300). {price, change_pct, prev_close, ...}.

        시세는 실전 도메인 필요할 수 있음 — 모의 앱키만 있으면 KIS_QUOTE_REAL 참고.
        """
        body = await self._get(
            client, "/uapi/overseas-price/v1/quotations/price", "HHDFS00000300",
            {"AUTH": "", "EXCD": excd, "SYMB": symbol.upper()},
            f"해외현재가 {symbol}")
        return parse_overseas_price(body.get("output") or {})

    async def fetch_overseas_daily(self, client: httpx.AsyncClient, symbol: str,
                                   excd: str = "NAS") -> list[dict]:
        """미국주식 일봉(HHDFS76240000) → [{date,open,high,low,close,volume}] 오래된→최신."""
        body = await self._get(
            client, "/uapi/overseas-price/v1/quotations/dailyprice", "HHDFS76240000",
            {"AUTH": "", "EXCD": excd, "SYMB": symbol.upper(),
             "GUBN": "0", "BYMD": "", "MODP": "1"},   # GUBN 0=일, MODP 1=수정주가
            f"해외일봉 {symbol}")
        return parse_overseas_daily(body.get("output2") or [])

    # ---- 국내 재무(성장성·안정성) — DART 대체/보완. corp_code 불필요(종목코드 직접) ----
    async def fetch_finance_ratios(self, client: httpx.AsyncClient, code: str,
                                   annual: bool = False) -> dict:
        """성장성(매출·영익 YoY) + 안정성(부채비율) → {rev_yoy, op_yoy, debt_ratio}.

        DART 무료키 한도(2만/일) 압박 없이 국내 재무를 채운다. annual=False면 분기.
        """
        div = "0" if annual else "1"                 # 0=연간, 1=분기
        params = {"fid_input_iscd": code, "fid_div_cls_code": div,
                  "fid_cond_mrkt_div_code": "J"}
        out: dict = {}
        try:
            g = await self._get(
                client, "/uapi/domestic-stock/v1/finance/growth-ratio",
                "FHKST66430800", params, f"성장성 {code}")
            out.update(parse_growth_ratio(g.get("output")))
        except Exception:
            pass
        try:
            s = await self._get(
                client, "/uapi/domestic-stock/v1/finance/stability-ratio",
                "FHKST66430600", params, f"안정성 {code}")
            out.update(parse_stability_ratio(s.get("output")))
        except Exception:
            pass
        return out

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
            headers=self._headers(token, tr_id, self.order_base), json=body)
        r.raise_for_status()
        d = self._check_rt(r.json(), f"주문 {side} {code}")
        if d.get("rt_cd") != "0":
            raise RuntimeError(f"KIS 주문 거부: {d.get('msg1') or d.get('msg_cd')}")
        out = d.get("output", {}) or {}
        return {"order_id": out.get("ODNO", ""),
                "org_no": out.get("KRX_FWDG_ORD_ORGNO", ""),
                "time": out.get("ORD_TMD", ""), "paper": paper}

    async def fetch_balance(self, client: httpx.AsyncClient) -> dict:
        """국내 계좌 잔고 요약 → {total_eval(순자산), cash(예수금)}. kis_paper면 모의계좌.

        inquire-balance(모의 tr_id VTTC8434R / 실전 TTTC8434R). 자동매매 리스크
        실드를 '실제 주문이 나가는 계좌' 기준으로 계산하기 위함(토스 실계좌 아님).
        파라미터·필드는 KIS 문서 기준, 실계좌 검증은 Pi 모의 조회로 확정.
        """
        if "-" not in (settings.kis_account or ""):
            raise RuntimeError("KIS_ACCOUNT 미설정/형식 오류(예: 12345678-01)")
        cano, prdt = settings.kis_account.split("-", 1)
        tr_id = "VTTC8434R" if settings.kis_paper else "TTTC8434R"
        token = await self._token_value(client, self.order_base)
        params = {
            "CANO": cano.strip(), "ACNT_PRDT_CD": prdt.strip(),
            "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
            "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        }
        await self._throttle()
        r = await client.get(
            f"{self.order_base}/uapi/domestic-stock/v1/trading/inquire-balance",
            headers=self._headers(token, tr_id, self.order_base), params=params)
        r.raise_for_status()
        return parse_balance(self._check_rt(r.json(), "잔고조회"))

    async def place_overseas_order(self, client: httpx.AsyncClient, *, code: str,
                                   side: str, qty: int, price: float,
                                   exchange: str = "NASD") -> dict:
        """미국주식 지정가 주문. kis_paper=true면 해외 모의투자 주문(가짜 돈).

        KIS 해외주식 주문 API(/uapi/overseas-stock/v1/trading/order).
        exchange = OVRS_EXCG_CD: NASD(나스닥)·NYSE(뉴욕)·AMEX(아멕스).
        tr_id는 실전/모의 × 매수/매도로 분기(미국). 파라미터는 KIS 문서 기준이며
        실계좌 검증은 Pi 모의 테스트 주문으로 확정(rt_cd/msg1로 오류 즉시 노출).
        """
        if "-" not in (settings.kis_account or ""):
            raise RuntimeError("KIS_ACCOUNT 미설정/형식 오류(예: 12345678-01)")
        cano, prdt = settings.kis_account.split("-", 1)
        paper = settings.kis_paper
        # 미국: 실전 매수 TTTT1002U·매도 TTTT1006U / 모의 매수 VTTT1002U·매도 VTTT1001U
        if side.upper() == "BUY":
            tr_id = "VTTT1002U" if paper else "TTTT1002U"
        else:
            tr_id = "VTTT1001U" if paper else "TTTT1006U"
        token = await self._token_value(client, self.order_base)
        body = {
            "CANO": cano.strip(), "ACNT_PRDT_CD": prdt.strip(),
            "OVRS_EXCG_CD": exchange,                  # NASD/NYSE/AMEX
            "PDNO": code.upper(),
            "ORD_QTY": str(int(qty)),
            "OVRS_ORD_UNPR": f"{price:.2f}",           # 미국 지정가(소수 2자리)
            "CTAC_TLNO": "",                           # 연락처(공란 허용)
            "MGCO_APTM_ODNO": "",                      # 운용사지정주문번호(공란)
            "SLL_TYPE": "" if side.upper() == "BUY" else "00",  # 매수 공란·매도 00
            "ORD_SVR_DVSN_CD": "0",                    # (오타 수정: _CD) 주문서버구분
            "ORD_DVSN": "00",                          # 00=지정가
        }
        await self._throttle()
        r = await client.post(
            f"{self.order_base}/uapi/overseas-stock/v1/trading/order",
            headers=self._headers(token, tr_id, self.order_base), json=body)
        r.raise_for_status()
        d = self._check_rt(r.json(), f"해외주문 {side} {code}")
        if d.get("rt_cd") != "0":
            raise RuntimeError(f"KIS 해외주문 거부: {d.get('msg1') or d.get('msg_cd')}")
        out = d.get("output", {}) or {}
        return {"order_id": out.get("ODNO", ""),
                "org_no": out.get("KRX_FWDG_ORD_ORGNO", ""),
                "time": out.get("ORD_TMD", ""), "paper": paper, "exchange": exchange}


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


# 시세(quotation) 거래소코드 — 주문/잔고용(NASD/NYSE/AMEX)과 다르다.
_EXCD_QUOTE = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}


def quote_excd(order_excd: str) -> str:
    """주문용 거래소코드(NASD/NYSE/AMEX) → 시세용(NAS/NYS/AMS). 기본 NAS."""
    return _EXCD_QUOTE.get((order_excd or "").upper(), "NAS")


def parse_overseas_price(o: dict) -> dict:
    """해외 현재가(HHDFS00000300) output → 시세(순수 함수).

    표준 필드: last(현재가)·rate(등락율%)·base(전일종가)·tvol(거래량). 폴백 포함.
    """
    if not isinstance(o, dict):
        return {"price": None, "change_pct": None}
    return {
        "price": _f(o.get("last") or o.get("ovrs_prpr") or o.get("stck_prpr")),
        "change_pct": _f(o.get("rate") or o.get("prdy_ctrt")),
        "prev_close": _f(o.get("base")),
        "open": _f(o.get("open")), "high": _f(o.get("high")),
        "low": _f(o.get("low")), "volume": _f(o.get("tvol")),
    }


def parse_overseas_daily(rows: list) -> list[dict]:
    """해외 일봉(HHDFS76240000) output2 → [{date,open,high,low,close,volume}] 오래된→최신.

    필드: xymd(일자)·open·high·low·clos(종가)·tvol(거래량). 토스 캔들과 동일 형식.
    """
    out: list[dict] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        d = str(r.get("xymd") or "")
        close = _f(r.get("clos"))
        if not d or close is None:
            continue
        out.append({"date": d, "open": _f(r.get("open")), "high": _f(r.get("high")),
                    "low": _f(r.get("low")), "close": close,
                    "volume": _f(r.get("tvol"))})
    out.sort(key=lambda x: x["date"])
    return out


def _fin_latest(output) -> dict:
    """재무비율 output(기간 리스트/딕셔너리) → 최신 결산 행(stac_yymm 최대)."""
    if isinstance(output, list):
        rows = [r for r in output if isinstance(r, dict) and r.get("stac_yymm")]
        return max(rows, key=lambda r: str(r.get("stac_yymm") or ""), default={})
    return output if isinstance(output, dict) else {}


def parse_growth_ratio(output) -> dict:
    """성장성비율(FHKST66430800) → {rev_yoy(매출증가율), op_yoy(영익증가율), period}.

    필드: grs(매출액증가율)·bsop_prfi_inrt(영업이익증가율)·stac_yymm(결산년월).
    """
    r = _fin_latest(output)
    return {"rev_yoy": _f(r.get("grs")), "op_yoy": _f(r.get("bsop_prfi_inrt")),
            "period": r.get("stac_yymm")}


def parse_stability_ratio(output) -> dict:
    """안정성비율(FHKST66430600) → {debt_ratio(부채비율), period}. 필드: lblt_rate."""
    r = _fin_latest(output)
    return {"debt_ratio": _f(r.get("lblt_rate")), "period": r.get("stac_yymm")}


def parse_balance(payload: dict) -> dict:
    """inquire-balance 응답 → {total_eval(순자산), cash(예수금)}. 순수 함수.

    output2(계좌 요약)에서 nass_amt(순자산금액)=총자산, dnca_tot_amt(예수금총금액)=현금.
    순자산이 없으면 유가증권평가(scts_evlu_amt)+예수금으로 폴백. 값 없으면 None.
    """
    if not isinstance(payload, dict):
        return {"total_eval": None, "cash": None}
    out2 = payload.get("output2")
    if isinstance(out2, list):
        row = out2[0] if out2 else {}
    elif isinstance(out2, dict):
        row = out2
    else:
        row = {}

    def _n(key: str) -> float | None:
        v = row.get(key)
        if v in (None, ""):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    cash = _n("dnca_tot_amt")
    total = _n("nass_amt")
    if total is None:                              # 폴백: 유가증권평가 + 예수금
        se = _n("scts_evlu_amt")
        if se is not None or cash is not None:
            total = (se or 0.0) + (cash or 0.0)
    return {"total_eval": total, "cash": cash}


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
