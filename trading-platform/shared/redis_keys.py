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
# 호스트 research 생존 신호(str epoch, TTL 180s) — 점검 요청 시 구동 여부 즉시 판별
RESEARCH_HB_KEY = "research:heartbeat"

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
# SEC EDGAR 티커→CIK 매핑 캐시(json, 7일 TTL) — 미장 분기실적용
SEC_TICKER_KEY = "sec:tickermap"

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
# 매도 규율(트레일링 스탑) 상태(hash{code: json{peak, half_taken, ts}}) — 진입 후 고점 추적
ENGINE_TRAIL_KEY = "engine:trail"
# 자산 히스토리(list of json{ts, eval}, 하루 1스냅샷·~730일 보존) — 100억 로드맵 페이스 계산
ASSET_HIST_KEY = "asset:history"
# 매매 일지(list of json{id,ts,code,name,side,qty,price,note,judgment{...}}) — AI 복기 루프
JOURNAL_KEY = "journal:entries"
# 자동매매 실행 기록(hash{code: json{ts,order_id,qty,price}} — 재매수 쿨다운)
ENGINE_AUTO_KEY = "engine:auto_orders"
# 빛의기둥(수급 포착) 알림 기록(hash{code: "YYYY-MM-DD"} — 하루 1회)
ENGINE_PILLAR_KEY = "engine:pillar"
# 오늘의 매매 플랜(스윙 설문 맞춤). json{style, buys:[...3], sells:[...3], ts}
ENGINE_PLAN_KEY = "engine:plan"
# 텔레그램 명령: getUpdates 오프셋 / 확인 대기 주문(hash{n: json{side,code,qty,price,ts}})
TG_OFFSET_KEY = "tg:offset"
TG_PENDING_KEY = "tg:pending"
# 역방향(Inversion) AI 분석. inv_requests=set(요청 큐), inversion=hash{code: json{penalty,report,ts}}
RESEARCH_INV_REQ_KEY = "research:inv_requests"
RESEARCH_INV_KEY = "research:inversion"

# USD/KRW 환율(토스 exchange-rate, 포트폴리오 루프가 갱신). json{rate, ts}
FX_USDKRW_KEY = "fx:usdkrw"
# ADR 시세(hash{본주코드: json{us_symbol, usd, ratio, ts}}) — 괴리율 계산용
ADR_KEY = "adr:quotes"

# 시장 지표(토스 v1.2.2): 지수·수급. json{kospi:{price,change_pct}, kosdaq:{...},
#   investor:{kospi:{foreigner,institution,individual,date}, kosdaq:{...}}, ts}
MARKET_INDICATORS_KEY = "market:indicators"
# 시장 랭킹(토스 v1.2.2). json{kr_gainers:[...], us_gainers:[...], kr_amount:[...],
#   us_amount:[...], ts} — 각 항목 parse_rankings 형식
MARKET_RANKINGS_KEY = "market:rankings"

# AI 포트폴리오 코치(아침 점검). report=json{report,ts,...}, goal=json{target_pct,deadline,memo},
# requests=set(온디맨드 '지금 점검' 요청 — 호스트 research가 처리)
COACH_KEY = "coach:report"
COACH_GOAL_KEY = "coach:goal"
COACH_REQ_KEY = "coach:requests"
# 사용자 제공 리서치 노트(예: SK증권 반도체 데일리) — 텔레그램 '리포트 …'로 저장,
# 코치가 최우선 신뢰 입력으로 반영. TTL 36h(다음 날 아침 점검까지 유효)
COACH_NOTE_KEY = "coach:note"
# 아침 점검 미발송 감시견 — 오늘 경고를 보냈는지(str YYYY-MM-DD, 하루 1회 dedup)
COACH_WD_KEY = "coach:watchdog"


# 포워드 로그 마지막 실행일(str YYYY-MM-DD) — 하루 1회 스냅샷 dedup
FWD_DONE_KEY = "fwd:done"


def fwd_scores_key(date: str) -> str:
    """일별 점수 스냅샷(hash{code: json{s,p,v,q,g,m,t,c}}, 120일 보존).

    Validation First의 원료 — T+N 시점에 현재가와 비교해 점수의 실제
    예측력(캘리브레이션·축 IC)을 측정한다.
    """
    return f"fwd:scores:{date}"


def stock_ohlcv_key(code: str) -> str:
    """종목 일봉 시계열. value=json[{date, close, high, low, volume}] (오래된→최신)."""
    return f"stock:ohlcv:{code}"


def stock_intraday_key(code: str) -> str:
    """장중 분봉 시계열(데이 트레이딩). value=json[{t,o,h,l,c,v}] (오래된→최신)."""
    return f"stock:intraday:{code}"


DAY_POS_KEY = "engine:day_positions"     # 데이 포지션 스냅샷 {code:{entry,qty,ts,peak,scalp}}
