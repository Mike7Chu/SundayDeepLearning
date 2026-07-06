"""Redis 키 네이밍 (단일 진실원) — 주식 플랫폼."""
from __future__ import annotations

# 주식 시세 해시. field=종목코드, value=json{code,name,price,change_pct,per,pbr,...,ts}
STOCK_QUOTE_KEY = "stock:quote"

# 주식 배당 해시. field=종목코드, value=json{code, items:[{date, per_share, yield_pct}], ts}
STOCK_DIVIDEND_KEY = "stock:dividend"

# AI 리서치 리포트 해시. field=종목코드, value=json{code,name,report,model,ts,...}
RESEARCH_KEY = "research:reports"
# 온디맨드 분석 요청 큐(set of 종목코드). 컨테이너 API가 넣고 호스트 research가 처리.
RESEARCH_REQ_KEY = "research:requests"

# 관심종목 오버라이드(대시보드 편집). value=json[{code,name}]. 없으면 config/stocks.yaml.
WATCHLIST_KEY = "stock:watchlist"

# 전체 시장 스크리너: 유니버스(종목마스터) + 유니버스 펀더멘털
STOCK_UNIVERSE_KEY = "stock:universe"   # value=json[{code,name,market}]
STOCK_MARKET_KEY = "stock:market"       # field=code, value=json(quote+밸류에이션)

# DART 공시. dart:recent=list(json 최근공시), dart:seen=set(접수번호 rcept_no)
DART_RECENT_KEY = "dart:recent"
DART_SEEN_KEY = "dart:seen"
# DART 종목코드→corp_code 매핑 캐시(json, 7일 TTL)
DART_CORP_KEY = "dart:corpmap"

# 토스증권 포트폴리오. json{holdings:[{symbol,name,qty,avg_price,cur_price,eval_amount,pnl,pnl_pct}],
#   cash, total_eval, pnl, pnl_pct, ts}
TOSS_HOLDINGS_KEY = "toss:holdings"
# 토스 계좌 요약. json{accountSeq, buying_power, ts}
TOSS_ACCOUNT_KEY = "toss:account"
# 토스 미체결 주문 스냅샷. json{orders:[...], ts}
TOSS_ORDERS_KEY = "toss:orders"

# 매매 엔진(멍거 리스크 실드). peak=자산 최고점(float str), risk=json{buy_lock,mdd_pct,...},
# buylist=json{rows:[...], ts} — 2단계 필터 통과 종목(실주문 아님, 리스트만)
ENGINE_PEAK_KEY = "engine:peak_asset"
ENGINE_RISK_KEY = "engine:risk"
ENGINE_BUYLIST_KEY = "engine:buylist"
# 보유 종목 목표가/손절선 도달 알림 상태(hash{code: json{kind,ts}} — 중복 알림 방지)
ENGINE_ALERTS_KEY = "engine:alerts"
# 자동매매 실행 기록(hash{code: json{ts,order_id,qty,price}} — 재매수 쿨다운)
ENGINE_AUTO_KEY = "engine:auto_orders"
# 빛의기둥(수급 포착) 알림 기록(hash{code: "YYYY-MM-DD"} — 하루 1회)
ENGINE_PILLAR_KEY = "engine:pillar"
# 텔레그램 명령: getUpdates 오프셋 / 확인 대기 주문(hash{n: json{side,code,qty,price,ts}})
TG_OFFSET_KEY = "tg:offset"
TG_PENDING_KEY = "tg:pending"
# 역방향(Inversion) AI 분석. inv_requests=set(요청 큐), inversion=hash{code: json{penalty,report,ts}}
RESEARCH_INV_REQ_KEY = "research:inv_requests"
RESEARCH_INV_KEY = "research:inversion"


def stock_ohlcv_key(code: str) -> str:
    """종목 일봉 시계열. value=json[{date, close, high, low, volume}] (오래된→최신)."""
    return f"stock:ohlcv:{code}"
