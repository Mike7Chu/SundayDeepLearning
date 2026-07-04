"""토스증권(Toss) Open API 클라이언트 — 실보유(잔고)·매수여력·시세·실주문.

KIS가 펀더멘털(PER/PBR/…)을 담당하는 것과 달리 토스는 **실제 계좌 보유·매매**를 담당.
공식 OpenAPI 3.1.0 (base https://openapi.tossinvest.com, 문서 developers.tossinvest.com/docs).

- 인증: OAuth2 client_credentials. POST /oauth2/token(form-urlencoded) → access_token/expires_in.
  갱신토큰 없음(만료 시 재발급). client당 1토큰.
- 호출: Authorization: Bearer <token>. 계좌/주문 API엔 X-Tossinvest-Account: <accountSeq> 헤더.
- 응답 봉투: 성공 {result}, 실패 {error:{requestId,code,message,data}}.
- 심볼: 국내 6자리(005930) / 미국 티커(AAPL). 통화 KRW/USD.

키(toss_client_id/secret) 미설정이면 enabled=False → 수집/주문 비활성.
주문은 settings.toss_trading_enabled(하드 게이트) + 금액 한도 검증을 통과한 경우에만 호출부에서 실행.
"""
from __future__ import annotations

import logging
import time

import httpx

from shared.settings import settings

logger = logging.getLogger(__name__)

_BASE = "https://openapi.tossinvest.com"


class TossError(RuntimeError):
    """토스 API가 {error} 봉투를 반환했을 때."""

    def __init__(self, code: str, message: str, request_id: str = ""):
        self.code = code
        self.message = message
        self.request_id = request_id
        super().__init__(f"toss error {code}: {message} (req={request_id})")


class TossClient:
    def __init__(self):
        self.base = _BASE
        self._token: str | None = None
        self._exp: float = 0.0

    @property
    def enabled(self) -> bool:
        return bool(settings.toss_client_id and settings.toss_client_secret)

    async def _token_value(self, client: httpx.AsyncClient) -> str:
        if self._token and time.time() < self._exp - 60:
            return self._token
        r = await client.post(
            f"{self.base}/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": settings.toss_client_id,
                "client_secret": settings.toss_client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        d = r.json()
        if d.get("error"):
            # OAuth2 표준 에러는 {"error": "invalid_client", "error_description": "..."}
            # (error가 문자열). BFF envelope({error:{code,message}})와 형식이 다름.
            err = d["error"]
            if isinstance(err, dict):
                code, msg, rid = err.get("code", ""), err.get("message", ""), err.get("requestId", "")
            else:
                code, msg, rid = str(err), d.get("error_description", ""), ""
            logger.warning("[toss] 토큰 발급 실패: %s %s", code, msg)
            raise TossError(code, msg, rid)
        r.raise_for_status()
        self._token = d.get("access_token") or d.get("result", {}).get("access_token")
        exp = d.get("expires_in") or d.get("result", {}).get("expires_in", 3600)
        self._exp = time.time() + int(exp)
        return self._token

    def _headers(self, token: str, account: str | None = None) -> dict:
        h = {"Authorization": f"Bearer {token}"}
        if account:
            h["X-Tossinvest-Account"] = str(account)
        return h

    async def _get(self, client: httpx.AsyncClient, path: str, *,
                   params: dict | None = None, account: str | None = None):
        token = await self._token_value(client)
        r = await client.get(f"{self.base}{path}",
                             headers=self._headers(token, account), params=params)
        return _unwrap(_json_or_raise(r))

    async def _post(self, client: httpx.AsyncClient, path: str, *,
                    json: dict | None = None, account: str | None = None):
        token = await self._token_value(client)
        r = await client.post(f"{self.base}{path}",
                             headers=self._headers(token, account), json=json)
        return _unwrap(_json_or_raise(r))

    # ---- 읽기 (읽기전용, 안전) -------------------------------------------
    async def fetch_accounts(self, client: httpx.AsyncClient) -> list[dict]:
        return parse_accounts(await self._get(client, "/api/v1/accounts"))

    async def resolve_account_seq(self, client: httpx.AsyncClient) -> str | None:
        """설정된 accountSeq 우선, 없으면 /accounts 대표계좌."""
        if settings.toss_account_seq:
            return settings.toss_account_seq
        accs = await self.fetch_accounts(client)
        return accs[0]["accountSeq"] if accs else None

    async def fetch_holdings(self, client: httpx.AsyncClient, account: str) -> dict:
        return parse_holdings(await self._get(
            client, "/api/v1/holdings", account=account))

    async def fetch_buying_power(self, client: httpx.AsyncClient, account: str,
                                 currency: str = "KRW") -> dict:
        # currency는 스펙상 필수 쿼리 파라미터(누락 시 400 invalid-request).
        return parse_buying_power(await self._get(
            client, "/api/v1/buying-power",
            params={"currency": currency}, account=account))

    async def fetch_prices(self, client: httpx.AsyncClient,
                           symbols: list[str]) -> list[dict]:
        # 최대 200개. 쉼표 구분.
        params = {"symbols": ",".join(symbols[:200])}
        return parse_prices(await self._get(client, "/api/v1/prices", params=params))

    async def fetch_candles(self, client: httpx.AsyncClient, symbol: str,
                            interval: str = "1d") -> list[dict]:
        params = {"symbol": symbol, "interval": interval, "count": 200}
        return parse_candles(await self._get(client, "/api/v1/candles", params=params))

    async def fetch_daily_history(self, client: httpx.AsyncClient, symbol: str,
                                  target: int = 260) -> list[dict]:
        """일봉 ~target개(최대 2페이지, nextBefore 페이지네이션). 52주·시그널용. 오래된→최신."""
        collected: list[dict] = []
        before: str | None = None
        for _ in range(2):
            params: dict = {"symbol": symbol, "interval": "1d", "count": 200}
            if before:
                params["before"] = before
            res = await self._get(client, "/api/v1/candles", params=params)
            collected.extend(parse_candles(res))
            before = res.get("nextBefore") if isinstance(res, dict) else None
            if not before or len(collected) >= target:
                break
        uniq = {c["date"]: c for c in collected}
        return sorted(uniq.values(), key=lambda c: c["date"])[-target:]

    async def fetch_stocks(self, client: httpx.AsyncClient,
                           symbols: list[str]) -> dict:
        params = {"symbols": ",".join(symbols[:200])}
        return parse_stocks(await self._get(client, "/api/v1/stocks", params=params))

    async def fetch_exchange_rate(self, client: httpx.AsyncClient,
                                  base: str = "USD", quote: str = "KRW") -> dict:
        # baseCurrency·quoteCurrency는 스펙상 필수(누락 시 400 invalid-request).
        return await self._get(client, "/api/v1/exchange-rate",
                             params={"baseCurrency": base, "quoteCurrency": quote})

    async def fetch_open_orders(self, client: httpx.AsyncClient, account: str,
                                status: str = "OPEN") -> list[dict]:
        res = await self._get(client, "/api/v1/orders",
                             params={"status": status}, account=account)
        return [parse_order(o) for o in _as_list(res, "orders")]

    async def fetch_sellable_quantity(self, client: httpx.AsyncClient, account: str,
                                      symbol: str) -> dict:
        return await self._get(client, "/api/v1/sellable-quantity",
                             params={"symbol": symbol}, account=account)

    # ---- 주문 (게이트 — 호출부에서 toss_trading_enabled + 한도 검증 후에만) ----
    async def place_order(self, client: httpx.AsyncClient, account: str, *,
                          symbol: str, side: str, quantity: float,
                          price: float | None = None,
                          order_type: str = "LIMIT") -> dict:
        body = {"symbol": symbol, "side": side.upper(),
                "quantity": quantity, "orderType": order_type.upper()}
        if price is not None:
            body["price"] = price
        return parse_order(await self._post(
            client, "/api/v1/orders", json=body, account=account))

    async def cancel_order(self, client: httpx.AsyncClient, account: str,
                           order_id: str) -> dict:
        return parse_order(await self._post(
            client, f"/api/v1/orders/{order_id}/cancel", account=account))

    async def modify_order(self, client: httpx.AsyncClient, account: str,
                           order_id: str, *, quantity: float | None = None,
                           price: float | None = None) -> dict:
        body: dict = {}
        if quantity is not None:
            body["quantity"] = quantity
        if price is not None:
            body["price"] = price
        return parse_order(await self._post(
            client, f"/api/v1/orders/{order_id}/modify", json=body, account=account))


# ===== 순수 함수 (네트워크 無 · 유닛테스트) =================================

def _json_or_raise(r: httpx.Response) -> dict:
    """응답 JSON을 얻되, {error} 봉투면 상태코드와 무관하게 TossError."""
    try:
        d = r.json()
    except ValueError:
        r.raise_for_status()
        raise
    if isinstance(d, dict) and d.get("error"):
        err = d["error"]
        logger.warning("[toss] API 오류: %s", err)
        raise TossError(err.get("code", ""), err.get("message", ""),
                        err.get("requestId", ""))
    return d


def _unwrap(payload):
    """{result} 봉투를 벗김. result 없으면 payload 그대로(관용)."""
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return payload


def _as_list(res, *keys) -> list:
    """result가 list거나 {key:[...]} 형태 모두 수용."""
    if isinstance(res, list):
        return res
    if isinstance(res, dict):
        for k in keys:
            v = res.get(k)
            if isinstance(v, list):
                return v
    return []


def _f(v) -> float | None:
    try:
        if v in (None, ""):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _first(o: dict, *keys):
    """여러 후보 키 중 처음으로 값이 있는 것(토스 필드명 편차 대비)."""
    for k in keys:
        if k in o and o[k] not in (None, ""):
            return o[k]
    return None


def parse_accounts(res) -> list[dict]:
    """계좌 목록 → [{accountSeq, name, ...}]. accountSeq는 계좌/주문 API 헤더에 필요."""
    out: list[dict] = []
    for a in _as_list(res, "accounts"):
        seq = _first(a, "accountSeq", "accountNumber", "accountNo", "id")
        if seq is None:
            continue
        out.append({
            "accountSeq": str(seq),
            "name": _first(a, "accountName", "name", "productName") or "",
            "currency": _first(a, "currency") or "KRW",
        })
    return out


def _dig(o, *path):
    """중첩 dict 경로 안전 조회. 중간에 dict 아니면 None."""
    for k in path:
        if not isinstance(o, dict):
            return None
        o = o.get(k)
    return o


def parse_holdings(res) -> dict:
    """보유 자산(HoldingsOverview) → {holdings:[...], total_eval_krw, total_eval_usd,
    pnl, pnl_pct, ts}.

    실제 응답(토스 OpenAPI): items[]에 quantity·lastPrice·averagePurchasePrice·currency,
    평가/손익은 중첩(marketValue.amount, profitLoss.amount, profitLoss.rate[소수]).
    요약은 최상위 marketValue.amount.{krw,usd}·profitLoss.amount.krw·profitLoss.rate.
    (원화 합산은 USD 환율 필요 → 수집 루프에서 환산; 여기선 통화별 분리 값만.)
    """
    if not isinstance(res, dict):
        res = {}
    holdings: list[dict] = []
    for h in _as_list(res, "items", "holdings"):
        qty = _f(_first(h, "quantity", "qty")) or 0.0
        avg = _f(_first(h, "averagePurchasePrice", "averagePrice", "avgPrice")) or 0.0
        cur = _f(_first(h, "lastPrice", "currentPrice", "price")) or 0.0
        eval_amt = _f(_dig(h, "marketValue", "amount"))
        if eval_amt is None:
            eval_amt = qty * cur
        pnl = _f(_dig(h, "profitLoss", "amount"))
        if pnl is None:
            pnl = (cur - avg) * qty
        rate = _f(_dig(h, "profitLoss", "rate"))     # 소수비율(0.1077=10.77%)
        cost = avg * qty
        pnl_pct = rate * 100.0 if rate is not None else ((pnl / cost * 100.0) if cost else 0.0)
        holdings.append({
            "symbol": str(_first(h, "symbol", "code", "ticker") or ""),
            "name": _first(h, "name", "symbolName") or "",
            "currency": _first(h, "currency") or "KRW",
            "qty": qty, "avg_price": avg, "cur_price": cur,
            "eval_amount": round(eval_amt, 2),
            "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
        })
    eval_krw = _f(_dig(res, "marketValue", "amount", "krw"))
    eval_usd = _f(_dig(res, "marketValue", "amount", "usd"))
    pnl_krw = _f(_dig(res, "profitLoss", "amount", "krw"))
    total_rate = _f(_dig(res, "profitLoss", "rate"))   # 전체 원화환산 수익률(소수)
    return {
        "holdings": holdings,
        "total_eval_krw": round(eval_krw, 2) if eval_krw is not None else 0.0,
        "total_eval_usd": round(eval_usd, 2) if eval_usd is not None else None,
        "pnl": round(pnl_krw, 2) if pnl_krw is not None else None,
        "pnl_pct": round(total_rate * 100.0, 2) if total_rate is not None else None,
        "ts": time.time(),
    }


def parse_buying_power(res) -> dict:
    """매수여력(BuyingPowerResponse) → {buying_power, currency}. 실제 필드 cashBuyingPower."""
    if not isinstance(res, dict):
        return {"buying_power": None, "currency": None}
    bp = _f(_first(res, "cashBuyingPower", "buyingPower", "cash", "availableAmount"))
    return {"buying_power": bp, "currency": _first(res, "currency")}


def parse_prices(res) -> list[dict]:
    """시세 다건 → [{symbol, price, change_pct}]."""
    out: list[dict] = []
    for p in _as_list(res, "prices", "items"):
        out.append({
            "symbol": str(_first(p, "symbol", "code", "ticker") or ""),
            "price": _f(_first(p, "lastPrice", "price", "currentPrice")),
            "change_pct": _f(_first(p, "changeRate", "changePercent", "fluctuationRate")),
        })
    return out


def parse_candles(res) -> list[dict]:
    """캔들 → [{date, open, high, low, close, volume}] (오래된→최신)."""
    rows: list[dict] = []
    for c in _as_list(res, "candles", "items"):
        close = _f(_first(c, "close", "closePrice"))
        if close is None:
            continue
        rows.append({
            "date": str(_first(c, "date", "time", "timestamp", "baseDate") or ""),
            "open": _f(_first(c, "open", "openPrice")),
            "high": _f(_first(c, "high", "highPrice")),
            "low": _f(_first(c, "low", "lowPrice")),
            "close": close,
            "volume": _f(_first(c, "volume", "tradingVolume", "accVolume")),
        })
    rows.sort(key=lambda r: r["date"])
    return rows


def parse_stocks(res) -> dict:
    """종목 기본정보(StockInfo[]) → {symbol: {name, shares, market, currency}}."""
    out: dict[str, dict] = {}
    rows = res if isinstance(res, list) else _as_list(res, "stocks", "items")
    for s in rows:
        sym = str(_first(s, "symbol", "code") or "")
        if not sym:
            continue
        out[sym] = {
            "name": _first(s, "name", "symbolName") or "",
            "shares": _f(_first(s, "sharesOutstanding", "shares")),
            "market": _first(s, "market") or "",
            "currency": _first(s, "currency") or "KRW",
        }
    return out


def candle_metrics(candles: list[dict]) -> dict:
    """일봉(오래된→최신) → {change_pct, high_52w, low_52w, prev_close, last_close}.

    change_pct = (마지막 종가 − 전일 종가)/전일 종가 ×100. 52주 = 보유 캔들의 고/저.
    prev_close(전일 종가)는 현재가 기반 등락률 재계산용.
    """
    closes = [c["close"] for c in candles if c.get("close")]
    highs = [c["high"] for c in candles if c.get("high") is not None]
    lows = [c["low"] for c in candles if c.get("low") is not None]
    prev = closes[-2] if len(closes) >= 2 else None
    last = closes[-1] if closes else None
    change = round((last - prev) / prev * 100, 2) if (last and prev) else None
    return {
        "change_pct": change,
        "high_52w": round(max(highs), 2) if highs else None,
        "low_52w": round(min(lows), 2) if lows else None,
        "prev_close": prev,
        "last_close": last,
    }


def parse_order(res) -> dict:
    """주문 결과/조회 → 표준화 dict."""
    if not isinstance(res, dict):
        return {}
    return {
        "order_id": str(_first(res, "orderId", "id", "orderNo") or ""),
        "symbol": str(_first(res, "symbol", "code", "ticker") or ""),
        "side": (_first(res, "side", "orderSide") or "").upper(),
        "quantity": _f(_first(res, "quantity", "qty", "orderQuantity")),
        "price": _f(_first(res, "price", "orderPrice")),
        "status": (_first(res, "status", "orderStatus") or "").upper(),
        "order_type": (_first(res, "orderType", "type") or "").upper(),
    }
