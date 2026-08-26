"""Claude 가치투자 애널리스트.

종목 데이터를 4대 거장 렌즈로 분석해 구조화 리포트 생성. 두 가지 백엔드:
- **api**: ANTHROPIC_API_KEY 사용(anthropic SDK, 종량과금).
- **cli**: 키가 없고 RESEARCH_USE_CLI=true + `claude`(Claude Code) 설치 시,
  헤드리스 모드(`claude -p`)로 **구독 사용량 내(추가과금 없음)** 분석.
둘 다 없으면 enabled=False로 안전하게 idle. (종목 추천 아님 — 분석 보조)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time

from research.data import StockData, format_for_prompt
from research.lenses import DISCLAIMER, SYSTEM_PROMPT
from shared.settings import settings

logger = logging.getLogger(__name__)

_CLI_TIMEOUT = 180.0          # CLI 분석 1건 최대 대기(초)
_COACH_CLI_TIMEOUT = 420.0    # 아침 점검(웹검색 포함) 최대 대기(초)
_TENBAGGER_CLI_TIMEOUT = 600.0   # 텐베거 심층 탐색(웹검색 다수) 최대 대기(초)

# TENBAGGER DETECTOR — 5~10년 10배 성장주 발굴 리서치 에이전트(사용자 정의 마스터 프롬프트).
TENBAGGER_SYSTEM = """너는 TENBAGGER DETECTOR — 미래 산업의 구조적 변화를 조기에 감지해 향후 5~10년 10배(텐베거) 이상 성장 가능성이 있는 상장기업을 발굴하는 장기 성장주 리서치 에이전트다.
목표: '앞으로 세상을 바꿀 산업이 무엇인가' → '그 산업의 승자는 누구인가' → '그 승자의 현재 시총은 미래 성공을 얼마나 선반영했나' → '아직 시장이 저평가한 기업은 누구인가'를 숫자로 답한다. 과거 초기 Apple/Tesla/Amazon/Nvidia/Netflix가 보였던 특성을 찾는다.

[핵심 철학] 텐베거는 다음이 결합돼야 한다: A.거대한 구조적 변화(새 시장 자체가 커짐) B.압도적 TAM(5~10년 수배↑) C.승자 가능성(1~3위) D.경쟁우위(기술·데이터·네트워크·규모·규제·브랜드·생태계·전환비용 해자) E.초기 단계(미래가 주가에 다 반영되지 않음) F.비대칭(하방 제한·상방 큼).

[절대 원칙] ①테마('AI·로봇·우주·원전')만으로 추천 금지 — '기술 발전→비용 하락→사용성 증가→시장 확대→매출 증가' 연결고리를 증명한다. ②'좋은 회사'와 '좋은 주식'을 구분(회사의 질/성장성/현재 주가 매력을 별도 평가) — 미래가치 다 반영됐으면 탈락. ③시가총액을 가장 중요하게 — 10배 평가 시 현재 시총×10을 정당화할 매출/영업이익/FCF/점유율/TAM을 반드시 역산(숫자로 설명, '10배 갈 것 같다' 금지).

[탐색 분야] AI(인프라/에이전트/로보틱스/자율/SW/HW/추론/엣지), 에너지(원전/SMR/핵융합/그리드/저장/전력반도체/데이터센터 전력), 우주(발사/위성/통신/방산/궤도인프라), 금융(스테이블코인/토큰화/블록체인 인프라/디지털결제/프로그래머블머니), 바이오(유전자편집/합성생물학/롱제비티/정밀의료/AI신약), 컴퓨팅(양자/포토닉스/첨단반도체/뉴로모픽). 목록 밖 신시장도 적극 탐색.

[스코어링 100점] ①TAM 확장 15(2030~35 시장 10배↑=15, 5~10배=12, 3~5배=9, 2~3배=6) ②Disruption 15(새 산업 창출=15, 근본 재편=12, 강력 성장=9, 점진 개선=6) ③Winner 확률 15(기술·점유·고객·파트너·생산·자본·생태계) ④Moat 10 ⑤매출 성장 10(가속 여부 필수) ⑥유닛이코노믹스/수익성 10(적자 허용하되 매출↑→규모의경제→마진개선 구조 필수) ⑦Valuation 15(현재→5배→10배 시총과 정당화 매출/이익, PS·PSG·PE·EV/Sales·EV/EBITDA·FCF) ⑧Catalyst 5(1~3년 내 재평가 이벤트) ⑨Market Mispricing 5(시장이 과소평가한 부분).
[점수 등급] 90~100 🔥EXCEPTIONAL / 80~89 🚀HIGH CONVICTION / 70~79 🟢WATCHLIST / 60~69 🟡SPECULATIVE / 59↓ 🔴REJECT.

[80점 이상 필수 역산] 현재 시총 확인 → ×10 → 10배 시총 정당화에 필요한 매출·영업이익·FCF·마진·점유율 역산 → 현실성 판단 → Bull/Base/Bear 3시나리오.
[10배의 조건] 각 기업마다 '이 기업이 10배가 되려면 ____가 되어야 한다' 문장 완성 후 현실성 검증.
[Kill Thesis] 각 후보마다 '무엇이 틀리면 실패하는가' 핵심 요인(상용화 실패/경쟁 우위/규제/자금/고객확보/TAM과대/마진악화/희석/경영진)을 텐베거 가능성보다 더 냉정하게 평가.
[경쟁자] 각 후보 최소 3개 경쟁자와 기술·매출·성장·고객·자금·해자·밸류에이션 비교 → '왜 이 회사가 경쟁자보다 텐베거 가능성이 높은가' 결론. 답 못하면 감점.
[가격≠가치] Great Company / Great Business / Great Stock(좋은 가격) / Tenbagger(10배)를 구분. A라고 D는 아니다.

[웹검색 규율] 최신 정보는 반드시 웹검색. 우선순위: 1)기업 IR/SEC/공시 2)실적발표/IR자료 3)Reuters/Bloomberg/FT/WSJ 4)산업 전문자료 5)Reddit/X(심리 파악용만). 미확인 정보를 사실처럼 쓰지 않는다.
[숫자 기준] 모든 시총·가격에 기준시점 표기(예 '시가총액 $12.4B (YYYY-MM-DD 기준)'). 과거 가격을 현재처럼 쓰지 않는다.
[버블 필터·감점] 매출 대비 시총 급증/적자 확대/주식보상 과다/지속 유상증자/insider selling/TAM 과대/경쟁 과다/기술우위 없음/단순 테마주/발표>실고객. 'AI를 쓴다' 정도는 AI 기업으로 인정하지 않는다.
[Tenbagger DNA 10] ①시장이 예상보다 빨리 커지나 ②가격/성능 급개선 ③사용자 폭증 ④규모↑→비용↓ ⑤네트워크 효과 ⑥생태계 ⑦추격 난이도 ⑧경영진 장기 비전 ⑨아직 대부분 무관심 ⑩5~10년 산업 중심 가능성. 7개↑ 충족을 최우선.

[명령별 동작]
· '텐베거 탐색' / '탐색': 전 시장 검색 → TODAY'S TOP 5 표(순위|기업|티커|점수/100|현재 시총|10배 시총|매수상태) + #1 후보 상세(한 줄 요약·왜 지금인가 3~5·9개 스코어·총점·10배 역산·현재가/적정 매수구간 공격/기본/보수·Kill Thesis 3·최종 판단 🔥/🟢/🟡/🔴).
· '오늘점검' / '오늘 점검': 기존 후보를 신규뉴스·실적·주가·시총·밸류·산업/경쟁 변화·수급·Catalyst·Kill Thesis로 재평가하고 이전 점수와 비교(예 'CRCL 84→78(-6): 주가 상승으로 valuation 하락').
· '신규발굴': 기존 종목 제외, 30개 이상 탐색 → 10개 압축 → 최종 TOP 3.
· '가격만': 보유 후보의 현재가와 매수구간만(예 'CRCL $92 · 🟡관심 $70~80 · 🟢매수 $60~70 · 🔥적극 <$55').
· '딥다이브 TICKER': 해당 1개 집중 — 사업→산업→TAM→경쟁→기술→재무→현금→희석→경영진→수주→성장률→밸류에이션→10배 역산→Bull/Base/Bear→Kill Thesis→매수 가격.

[최종 철학] '무조건 10배', '제2의 테슬라', '무조건 사야 한다' 같은 근거 없는 말 금지. 항상 '현재 가격에서 10배가 되려면 필요한 조건이 무엇이고 현실적으로 달성 가능한가'를 판단한다. 목표는 '오늘 오를 주식'이 아니라 '아직 작지만 5~10년 후 산업 중심에 있을 회사를 시장보다 먼저 발견'하는 것. 가장 중요한 질문: '지금 시장은 이 회사의 미래를 어느 정도 가격에 반영하고 있나' — 답 못하면 후보로 선정하지 않는다.

[출력] 한국어. 마크다운(제목·표·굵게·리스트)으로 읽기 좋게. 투자 판단 보조이며 매매·수익 보장이 아님을 끝에 한 줄 명시."""



def parse_penalty(text: str) -> int:
    """리포트에서 '감점: N/30' 추출(순수 함수). 못 찾으면 보수적으로 30."""
    m = None
    for m in re.finditer(r"감점\s*[:：]?\s*(\d{1,2})\s*/\s*30", text or ""):
        pass                     # 마지막 매치 사용(요약에 재언급될 수 있음)
    if not m:
        return 30
    return max(0, min(30, int(m.group(1))))


def _cli_error_hint(rc: int, detail: str, model: str) -> str:
    """claude CLI 실패의 '진짜 원인'을 추정해 실행가능한 안내로 변환(순수-ish).

    기존엔 무조건 '컨테이너 미로그인'이라 단정해 호스트에서 돌려도 오진했다. 여기선
    ~/.claude 존재로 환경을 실측하고, 출력 패턴으로 사용량한도/미로그인/모델을 구분한다.
    """
    d = (detail or "").lower()
    logged_in = os.path.isdir(os.path.expanduser("~/.claude"))
    if any(k in d for k in ("usage limit", "limit reached", "rate limit",
                            "quota")):
        tip = ("구독 사용량 한도 도달 — 한도 리셋되면 자동 정상화됩니다. 매일 소진되면 "
               ".env에 ANTHROPIC_API_KEY(종량)를 넣어 병행하세요(있으면 api 우선).")
    elif any(k in d for k in ("login", "authenticat", "unauthor", "not logged",
                              "invalid api key", "credential", "please run")):
        tip = ("claude 로그인이 없습니다 — 호스트에서 로그인한 '그 사용자'로 "
               "run-research-host.sh를 실행하세요(sudo/root 금지).")
    elif "model" in d:
        tip = (f"모델 인자를 CLI가 못 씁니다(--model {model}). `claude --version` 갱신 "
               "또는 .env RESEARCH_MODEL을 CLI가 아는 값으로.")
    elif not detail:
        tip = ("출력 없이 rc만 반환 — 대개 미로그인/사용량 한도입니다. 호스트에서 "
               f"`claude -p hi --model {model} --output-format text; echo rc=$?`로 확인.")
    else:
        tip = "아래 원문으로 원인을 확인하세요."
    env = "호스트(~/.claude 있음)" if logged_in else "컨테이너/미로그인 계정(~/.claude 없음)"
    msg = f"claude CLI 실패(rc={rc}, 실행환경={env}). {tip}"
    return msg + (f"\n[CLI 출력] {detail}" if detail else "")


class Analyst:
    def __init__(self) -> None:
        self.model = settings.research_model

    @property
    def mode(self) -> str | None:
        """사용 가능한 백엔드: 'api' | 'cli' | None."""
        if settings.anthropic_api_key:
            return "api"
        if settings.research_use_cli and shutil.which(settings.research_cli_bin):
            return "cli"
        return None

    @property
    def enabled(self) -> bool:
        return self.mode is not None

    def _disabled_report(self, data: StockData) -> dict:
        return self._wrap(data, enabled=False, report=(
            "리서치 비활성 — 둘 중 하나를 설정하세요:\n"
            "  (1) 구독 무과금: Claude Code 설치+로그인 후 .env에 RESEARCH_USE_CLI=true\n"
            "  (2) API 종량과금: .env에 ANTHROPIC_API_KEY=<console 키>"
        ))

    def _wrap(self, data: StockData, *, enabled: bool, report: str) -> dict:
        return {
            "code": data.code, "name": data.name, "model": self.model,
            "mode": self.mode, "ts": time.time(), "enabled": enabled,
            "report": report.strip(), "disclaimer": DISCLAIMER,
        }

    async def analyze(self, data: StockData) -> dict:
        """StockData → 구조화 리포트 dict. 비활성이면 안내 리포트."""
        mode = self.mode
        if mode is None:
            return self._disabled_report(data)
        prompt = (
            "다음 종목을 4대 거장 렌즈로 분석해 정해진 출력 형식으로 정리하세요.\n"
            "[데이터 신뢰 원칙 — 필독] 아래 정량 데이터는 수집 시점의 '실측 시장 데이터'"
            "(권위 소스: 증권사 실시간 API·DART 공식 사업보고서)입니다. 당신의 학습 기억 속 "
            "과거 주가·시총 수준과 다르더라도(현재는 AI·HBM 붐으로 코스피 8000대 시대) 데이터 "
            "오류로 단정하지 마세요. **'데이터 정합성 주의/오류 가능성/검증 필요' 같은 경고 문구를 "
            "리포트에 쓰지 마세요.** 대신 '왜 시장이 이 가격을 지불하는가'(이익 성장, 산업 사이클, "
            "수요 구조)를 분석하세요. 순이익 YoY가 제공되면 트레일링 PER의 착시(이익 급증기)를 "
            "감안해 정상화·포워드 관점으로 평가하세요.\n"
            "제공된 정량 매력도 점수·안전마진을 근거로 삼아, 정성 판단(해자·경영·현금흐름)과 "
            "종합해 '매수/분할매수/보류/회피' 관점을 명확히 제시하세요.\n"
            "[웹검색 — 가능하면 필수] 웹검색이 가능하면 다음을 **반드시 확인**해 최신성을 "
            "보강하세요(불가하면 생략, 추측 금지): ①가장 최근 분기 실적(매출·영업이익·순이익 "
            "YoY, 어닝 서프라이즈/쇼크) ②최근 4주 주요 뉴스·촉매(수주·가이던스·규제·M&A) "
            "③업황(반도체면 HBM/DDR 가격, 2차전지면 리튬·가동률 등). 제공된 정량치는 수집 "
            "시점 기준이니, 그 이후 발표된 실적/뉴스가 있으면 우선 반영하고 출처를 한 줄로 남기세요.\n"
            "[2분 드릴 — 리포트 맨 위, 한 문장] 이 회사가 뭘 만들어서 → 누구에게 → 어떻게 "
            "돈을 버는지 초등학생도 이해할 한 문장으로. '플랫폼·솔루션·생태계' 같은 업계 용어 "
            "쓰면 실패다. 두 문장이면 실패다.\n"
            "[출처 규율] 숫자에는 출처(URL 또는 '수집데이터')를 붙이고, 확인 못 한 값은 지어내지 "
            "말고 '확인 필요'로 표기하라.\n"
            "[역DCF — 가능하면] 최근 FCF(영업현금흐름−CapEx)·발행주식수(희석)·현재 시총을 웹검색/"
            "제공데이터로 구해, '지금 주가가 요구하는 향후 10년 FCF 성장률'을 역산하라(2단계 성장, "
            "영구 2.5%, 할인율 대형 9%·중소형 11%). 그 값을 과거 5년 FCF/매출 CAGR과 나란히 놓고 "
            "'시장 기대가 과한가/낮은가'를 한 줄로. FCF가 음수면 '역DCF 불가(흑자전환 먼저)'라고 "
            "쓰라. 적정주가를 제시하지 말고 '시장이 무엇을 기대하는지'만 말하라.\n\n"
            f"{format_for_prompt(data)}"
        )
        try:
            # CLI(구독) 경로는 웹검색 허용 — 최신 실적·촉매 확인(구독 한도 내, 추가과금 없음).
            report = await (self._via_api(prompt) if mode == "api" else self._via_cli(
                prompt, extra_args=("--allowedTools", "WebSearch"),
                timeout=_COACH_CLI_TIMEOUT))
        except Exception as exc:
            # 실패를 조용히 삼키지 않고 리포트에 노출(대시보드에서 원인 확인 가능).
            logger.warning("[research %s] 분석 실패(mode=%s): %s", data.code, mode, exc)
            return self._wrap(data, enabled=True,
                              report=f"⚠️ 분석 실패 (백엔드={mode})\n{exc}")
        return self._wrap(data, enabled=True, report=report)

    async def analyze_story(self, data: StockData) -> dict:
        """기업 스토리 리더 — 다년치 공시를 웹검색으로 비교(company-story 방법론).

        지난 2~3년의 '영화': 공시 문장 변화·경영진 톤·가이던스 달성. 웹검색 필수라
        구독 CLI/API 웹검색 경로에서만 의미. KR=DART 사업보고서, US=10-K+어닝콜.
        """
        mode = self.mode
        if mode is None:
            return self._disabled_report(data)
        kr = str(data.code or "").isdigit()
        prompt = (
            f"당신은 {data.name}({data.code})의 지난 2~3년 '스토리'를 읽는 애널리스트입니다. "
            "요약 카드가 '지금의 사진'이라면 이건 '지난 몇 년의 영화'입니다. 웹검색으로 다년치 "
            "공시를 찾아 비교하세요.\n"
            + ("[자료 — 한국] DART 사업보고서 2~3개년(dart.fss.or.kr) + IR/실적발표 자료. "
               "⚠️ 한국은 어닝콜 트랜스크립트 전문 공개가 드물어 톤 분석 정밀도는 미국보다 "
               "낮음 — 이 한계를 리포트 상단에 명시하고 사업보고서·IR 기반으로 분석.\n"
               if kr else
               "[자료 — 미국] 10-K 2~3개년(SEC EDGAR) + 어닝콜 트랜스크립트(IR/seekingalpha 등).\n")
            + "[분석 4]\n"
            "1) 공시 문장 변화 — '리스크 요인'·'사업의 내용'에서 새로 등장하거나 사라진 "
            "핵심 문장을 연도와 함께(예: 2023 10-K엔 없던 '중국 규제' 문장이 2024에 등장).\n"
            "2) 경영진 톤 변화 — (미국 어닝콜 가능 시) 자신감↔방어적 어조 변화. 한국은 IR 톤·"
            "표현 강도 변화.\n"
            "3) 가이던스 달성 — 과거 제시한 목표(매출·마진·출하) 대비 실제 달성/미달.\n"
            "4) 한 줄 스토리 — 위를 하나의 이야기로.\n"
            "[규율] 모든 발견에 출처(문서·연도/분기) 명시. 없는 변화를 지어내지 말 것"
            "('변화 없음'이 정답일 수 있음). [사실]과 [해석]을 분리. 한국어 출력. 확인 못 한 건 "
            "'확인 불가'.\n\n"
            f"{format_for_prompt(data)}"
        )
        try:
            report = await (self._via_api(prompt) if mode == "api" else self._via_cli(
                prompt, extra_args=("--allowedTools", "WebSearch"),
                timeout=_COACH_CLI_TIMEOUT))
        except Exception as exc:
            logger.warning("[story %s] 실패(mode=%s): %s", data.code, mode, exc)
            return self._wrap(data, enabled=True, report=f"⚠️ 스토리 분석 실패\n{exc}")
        return self._wrap(data, enabled=True, report=report)

    async def analyze_tenbagger(self, command: str) -> dict:
        """TENBAGGER DETECTOR — 5~10년 10배 성장주 발굴(웹검색 심층 리서치).

        command 예: '탐색'|'신규발굴'|'오늘점검'|'가격만'|'딥다이브 RKLB'. 마스터
        프롬프트(TENBAGGER_SYSTEM)로 전 시장을 검색·평가한다. 웹검색 필수라 구독
        CLI/API 웹검색 경로에서만 의미. 무거워서 온디맨드 전용(긴 타임아웃).
        """
        mode = self.mode
        if mode is None:
            return {"enabled": False, "command": command, "model": self.model,
                    "ts": time.time(),
                    "report": "AI 리서치 비활성 — 구독 CLI(RESEARCH_USE_CLI) 또는 API 키 필요."}
        today = time.strftime("%Y-%m-%d")
        prompt = (TENBAGGER_SYSTEM
                  + f"\n\n=== 사용자 입력 ===\n{command}\n\n(오늘 날짜: {today}. 모든 "
                  "시가총액·주가는 웹검색으로 현재 값을 확인해 기준시점과 함께 표기. "
                  "한국어·마크다운으로 정리.)")
        try:
            report = await (self._via_api(prompt) if mode == "api" else self._via_cli(
                prompt, extra_args=("--allowedTools", "WebSearch"),
                timeout=_TENBAGGER_CLI_TIMEOUT))
        except Exception as exc:
            logger.warning("[tenbagger '%s'] 실패(mode=%s): %s", command, mode, exc)
            report = f"⚠️ 텐베거 분석 실패\n{exc}"
        return {"enabled": True, "command": command, "model": self.model,
                "mode": mode, "ts": time.time(), "report": report.strip(),
                "disclaimer": DISCLAIMER}

    async def analyze_inversion(self, data: StockData) -> dict:
        """멍거 역방향 사고: '지금 사면 망하는 이유'만 집중 분석 → 감점(0~30) 산출.

        2단계 필터용. 마지막 줄 '감점: N/30'을 파싱한다. 실패 시 보수적으로 감점 30
        (검증 못 한 종목은 사지 않는다 — 능력 범위).
        """
        mode = self.mode
        if mode is None:
            return {"code": data.code, "name": data.name, "penalty": None,
                    "report": "리서치 비활성", "ts": time.time()}
        prompt = (
            "역방향 사고(Inversion) 리스크 검증: 아래 종목을 '좋은 이유'가 아니라 "
            "**'지금 사면 망하는 이유'만** 집중 분석하세요.\n"
            "- 리스크 3~5가지(사이클 하강, 경쟁 심화, 재무 악화, 밸류에이션 함정, "
            "규제·지배구조)를 근거와 함께 간결히.\n"
            "[데이터 신뢰 원칙] 제공 수치는 실측 시장 데이터입니다. 당신의 기억 속 과거 "
            "주가 수준과 달라도(시장 대세 상승 등) 데이터 오류로 단정하지 마세요.\n"
            "- 마지막 줄에 반드시 정확히 이 형식으로: 감점: N/30  (N=0~30 정수, "
            "리스크가 클수록 큼. 치명적 결함이면 25~30, 경미하면 0~10)\n\n"
            f"{format_for_prompt(data)}"
        )
        try:
            report = await (self._via_api(prompt) if mode == "api" else self._via_cli(prompt))
        except Exception as exc:
            logger.warning("[inversion %s] 실패: %s", data.code, exc)
            return {"code": data.code, "name": data.name, "penalty": 30,
                    "report": f"검증 실패({exc}) — 보수적 감점 30", "ts": time.time()}
        return {"code": data.code, "name": data.name,
                "penalty": parse_penalty(report), "report": report.strip(),
                "ts": time.time()}

    async def analyze_coach(self, block: str) -> dict:
        """아침 점검(포트폴리오 코치): 실계좌 비중 기준 종목별 판정 + 한 줄 결론.

        벤치마크 형식 — ①종목별(전일 주가·수급·공시·실적) 판정 ②미국 반도체·AI 동향
        ③쏠림·목표 현실성 ④오늘의 한 줄 결론(✅/⚠️/🚨). CLI 모드에선 웹검색 허용
        (미국 전일 동향 실데이터). 하루 1콜.
        """
        mode = self.mode
        if mode is None:
            return {"enabled": False, "mode": None, "model": self.model,
                    "ts": time.time(),
                    "report": "리서치 비활성 — RESEARCH_USE_CLI 또는 ANTHROPIC_API_KEY 설정 필요"}
        prompt = (
            "당신은 사용자의 실계좌를 매일 아침 함께 점검하는 개인 투자 코치입니다. "
            "아래 실측 계좌·시장 데이터를 바탕으로 '아침 점검' 브리핑을 쓰세요.\n"
            "[역할 규율 — v2] 행동(매수/매도 수량·가격)은 규칙 엔진('오늘의 매매 "
            "플랜')이 결정합니다. 당신은 추천·확률 예측을 하지 않습니다 — 당신의 "
            "역할은 ①데이터 해석 ②리스크 식별 ③반대 논거(Bear case) 제시입니다. "
            "'~% 확률로 오른다' 같은 수치 예측은 검증 불가능하므로 금지.\n"
            "[데이터 신뢰 원칙] 제공 수치는 실측입니다(현재 AI·HBM 붐, 코스피 8000대). "
            "학습 기억 속 과거 주가와 달라도 데이터 오류로 단정하거나 '검증 필요' 류의 "
            "경고 문구를 쓰지 마세요.\n"
            "'[미국 반도체]'·'[AI 인프라 투자(CAPEX) 프록시]'·'[ADR 괴리]' 블록이 제공되면 "
            "**그 수치를 근거로** 미국 동향 섹션을 작성하세요(우리 시스템이 토스에서 수집한 "
            "실제 시세). 특히 [ADR 괴리]는 외국인 수급의 선행 지표 — 프리미엄이 크게 양(+)이면 "
            "본주 갭업 압력, 음(−)이면 그 반대로 해석하되 비율 가정의 한계를 함께 표기하세요.\n"
            "웹 검색이 가능하면 다음을 **적극적으로** 확인해 보강하세요(불가하면 생략, 추측 금지): "
            "①HBM·DDR5 현물/계약 가격 동향 ②빅테크(MS·메타·구글·아마존) CAPEX 가이던스 "
            "뉴스 ③ADR 시세 교차확인 ④**제공된 미국 종목 중 ±5% 이상 급등락한 종목(예: "
            "마이크론 급락)의 사유를 반드시 검색**(실적·컨콜·가이던스·HBM 가격 코멘트) — 특히 "
            "내 반도체 보유와 상관 높은 종목의 급락은 원인을 찾아 함의를 적어라. "
            "⑤보유 종목의 최신 공시(대량보유·임원소유 등) 방향(취득/처분)도 검색으로 보강. "
            "'확인 불가'는 **검색을 시도하고도 못 찾았을 때만** 쓰고, 그 경우 무엇을 검색했는지 "
            "한 줄 남겨라.\n"
            "[의견 규율 — 단정 금지]\n"
            "- 매도/정리 의견의 근거는 제공된 데이터(가격·추세·실적·손익·비중)로 한정. "
            "데이터에 없는 업황·수주·산업 사이클은 단정하지 말고 '확인할 것' 항목으로 넘겨라.\n"
            "- 하루 상대 수익률(지수보다 덜 오름 등)에 과도한 의미를 부여하지 마라 — "
            "직전 급등의 되돌림, 종목별 순환매일 수 있다.\n"
            "- 수급은 사실만 기술(외국인 +N억 순매수)하고, 인과 해석('~때문에 샀다')은 "
            "추정임을 명시하라.\n"
            "- 목표 기한이 지났으면 오류로 취급하지 말고 재설정을 제안하라(목표는 사용자가 "
            "홈 화면에서 직접 저장한 값이다).\n\n"
            "[사실/해석 분리 — 최우선 원칙] 모든 수치는 제공 데이터·사용자 노트에서만 "
            "인용한다. 확인 안 된 것은 '확인 불가', 추론·인과 해석은 '(추정)'을 붙인다. "
            "수치를 기억으로 채우는 것은 금지 — 한 번의 지어낸 숫자가 실제 투자 판단을 "
            "망친다.\n"
            "[우선순위] ①사용자 리서치 노트(증권사 데일리) ②TSMC·ASML·엔비디아·마이크론의 "
            "실적/컨퍼런스콜/가이던스(CAPEX·CoWoS·N2·HBM 코멘트 — 일반 뉴스보다 훨씬 중요, "
            "노트/웹검색에서 발견 시 최우선) ③실측 시세·수급 ④일반 뉴스.\n\n"
            "출력 형식(기관 데일리 노트 스타일 — 쉬운 말 존댓말, 항상 '내 계좌 기준'):\n"
            "📈 AI 아침 점검\n"
            "1) 시장·수급 — 무엇이 움직였나(사실) + 왜(추정 — ADR·환율·ETF/MSCI·선물·실적 "
            "중 어떤 요인인지) + 그게 내 보유에 실질 영향인지 단기 노이즈인지 구분\n"
            "2) 간밤 미국 — 종목별 등락(실측)과 '왜'. 엇갈림(예: TSMC 강세인데 마이크론 "
            "약세)이 있으면 원인을 반드시 설명(모르면 '원인 확인 불가'). 실적·컨콜·가이던스 "
            "정보가 있으면 삼성전자·SK하이닉스·HBM·장비주에 주는 함의까지\n"
            "3) ADR 반영도 — [ADR 괴리] 수치로 '오늘 본주가 ADR 대비 얼마나 반영/미반영 "
            "상태로 출발하는가' 한 줄\n"
            "4) 보유 종목별 — 판정: 계속 보유 | 일부 매도 | 위험 신호 (정확히 셋 중 하나) "
            "+ 왜(사실→해석 순서, 내 종목 실질 영향 vs 노이즈 구분)\n"
            "5) 반대 논거(Bear case) — 최대 비중 종목에 대해 '지금 이 보유가 틀렸다면 "
            "그 이유는 무엇인가'를 2~3개(①ADR ②미국 반도체 ③외국인·선물 ④환율 "
            "⑤실적·컨콜 ⑥밸류에이션 중 근거 번호와 함께). 상승 확률 수치는 쓰지 말 것 "
            "— 확률 대신 '무엇이 보이면 경계를 높일지' 관찰 가능한 신호로 서술\n"
            "6) 성향별 전략 — ■공격 ■중립 ■보수 각 한 줄(근거+리스크 함께. 일방적 "
            "매수/매도 강요 금지)\n"
            "7) 이 분석이 틀리는 조건 — 전제(예: AI 투자 지속)와 무효화 트리거(예: 엔비디아 "
            "가이던스 하향, 외국인 순매도 전환) 1~3개\n"
            "8) 오늘의 한 줄 결론 — ✅ 계속 보유 / ⚠️ 일부 정리 / 🚨 위험 신호 중 하나로 "
            "시작 + 오늘 확인할 체크포인트 1~2개\n"
            "마지막 줄에 '투자 판단 보조이며 매매 지시가 아닙니다' 한 줄.\n\n"
            f"{block}"
        )
        try:
            if mode == "api":
                report = await self._via_api(prompt)
            else:
                report = await self._via_cli(
                    prompt, extra_args=("--allowedTools", "WebSearch"),
                    timeout=_COACH_CLI_TIMEOUT)
        except Exception as exc:
            logger.warning("[coach] 아침 점검 실패(mode=%s): %s", mode, exc)
            return {"enabled": True, "mode": mode, "model": self.model,
                    "ts": time.time(), "report": f"⚠️ 아침 점검 실패 (백엔드={mode})\n{exc}"}
        return {"enabled": True, "mode": mode, "model": self.model,
                "ts": time.time(), "report": report.strip()}

    async def decide(self, context: str) -> dict:
        """스윙 결정 에이전트: 후보/보유 컨텍스트 → 최종 BUY/SELL/HOLD(JSON).

        완전 위임이되 안전 규율: **매수는 후보 목록 안 종목만, 매도는 보유 종목만.**
        수량·가격·한도는 엔진이 집행하므로 여기선 '무엇을 할지'만 판단. 웹검색으로
        최신 실적·촉매를 반영. 파싱은 engine.agent.parse_decisions(순수).
        반환 {market_view, decisions:[{action,code,conviction,reason}], mode, ts}.
        """
        from engine.agent import parse_decisions   # 순수 파서(순환 import 방지 지연)
        mode = self.mode
        if mode is None:
            return {"enabled": False, "market_view": "", "decisions": [],
                    "mode": None, "ts": time.time()}
        prompt = (
            "당신은 이 계좌를 운용하는 스윙 트레이더입니다. 아래는 정량필터를 통과한 "
            "'매수 후보'와 현재 '보유 종목'입니다. 오늘의 최종 매매를 결정하세요.\n"
            "[철칙 — 위반 금지]\n"
            "① 매수(BUY)는 반드시 '[매수 후보]' 목록에 있는 code만. 목록에 없는 종목은 절대 매수 금지.\n"
            "② 매도(SELL)는 반드시 '[보유 종목]'에 있는 code만.\n"
            "③ 확신이 낮으면 HOLD 하거나 아무것도 사지 마세요(현금 보유도 전략). 종목 수보다 질.\n"
            "④ 수량·가격·한도는 시스템(엔진)이 안전하게 산정하니 당신은 '무엇을 할지'만 정하세요.\n"
            "[판단 기준] TTM(최근 4분기) 밸류에이션·스윙 점수·추세·손익·쏠림·리스크실드 상태를 "
            "종합. 웹검색이 가능하면 각 후보/보유의 **최근 분기 실적·4주 내 촉매(수주·가이던스·"
            "규제·업황)** 를 확인해 반영하세요(불가하면 제공 데이터만, 추측 금지).\n"
            "[출력 — 오직 JSON 하나. 앞뒤 설명 금지]\n"
            '{"market_view":"오늘 시장 한 줄","decisions":['
            '{"action":"BUY|SELL|HOLD","code":"종목코드","conviction":0-100,"reason":"한 줄 근거"}]}\n'
            "매수/매도할 게 없으면 decisions를 빈 배열로, 하지만 market_view는 반드시 채우세요.\n\n"
            f"{context}\n\n"
            "[최종 지시] 웹검색을 했더라도 **마지막 출력은 위 형식의 JSON 객체 하나뿐**입니다. "
            "산문·마크다운·코드펜스 없이 JSON만. market_view는 비우지 마세요."
        )
        try:
            if mode == "api":
                report = await self._via_api(prompt)
            else:
                report = await self._via_cli(
                    prompt, extra_args=("--allowedTools", "WebSearch"),
                    timeout=_COACH_CLI_TIMEOUT)
        except Exception as exc:
            logger.warning("[agent] 결정 실패(mode=%s): %s", mode, exc)
            return {"enabled": True, "market_view": f"결정 실패: {exc}",
                    "decisions": [], "mode": mode, "ts": time.time()}
        parsed = parse_decisions(report)
        # 웹검색 모드에서 산문만 내놓아 JSON이 없을 수 있다 → 원문 로깅 후 'JSON만' 1회 재시도.
        if not parsed["decisions"] and not parsed["market_view"]:
            logger.warning("[agent] JSON 파싱 실패 — 원문(%d자): %s",
                           len(report or ""), (report or "").replace("\n", " ")[:400])
            if (report or "").strip():
                try:
                    strict = ("아래 컨텍스트로 매매를 결정하되 **오직 JSON 한 줄만** 출력. "
                              "설명·마크다운·웹검색 금지.\n"
                              '{"market_view":"한 줄","decisions":[{"action":"BUY|SELL|HOLD",'
                              '"code":"코드","conviction":0-100,"reason":"근거"}]}\n\n'
                              + context)
                    report2 = await (self._via_api(strict) if mode == "api"
                                     else self._via_cli(strict, timeout=_CLI_TIMEOUT))
                    p2 = parse_decisions(report2)
                    if p2["decisions"] or p2["market_view"]:
                        parsed = p2
                    else:
                        logger.warning("[agent] 재시도도 JSON 실패 — 원문: %s",
                                       (report2 or "").replace("\n", " ")[:300])
                except Exception as exc:
                    logger.warning("[agent] JSON 재시도 실패: %s", exc)
        return {"enabled": True, "mode": mode, "ts": time.time(), **parsed}

    async def _via_api(self, prompt: str) -> str:
        # 지연 import: 키 있는 환경에서만 anthropic 필요
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        async with client.messages.stream(
            model=self.model,
            max_tokens=4096,
            thinking={"type": "adaptive"},   # skill 권장
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            msg = await stream.get_final_message()
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()

    async def _via_cli(self, prompt: str, extra_args: tuple[str, ...] = (),
                       timeout: float = _CLI_TIMEOUT) -> str:
        """Claude Code 헤드리스(`claude -p`)로 분석 — 구독 사용량 내, 추가과금 없음.

        system 프롬프트는 인자 호환성을 위해 본문에 합쳐 전달한다.
        extra_args: 예) ("--allowedTools", "WebSearch") — 코치의 미국 시황 확인용.
        """
        full = f"{SYSTEM_PROMPT}\n\n=== 분석 요청 ===\n{prompt}"
        proc = await asyncio.create_subprocess_exec(
            settings.research_cli_bin, "-p", full,
            "--model", self.model, "--output-format", "text", *extra_args,
            stdin=asyncio.subprocess.DEVNULL,   # stdin 대기(no stdin data…) → rc=129 방지
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"claude CLI 시간초과({timeout}s)")
        text = out.decode(errors="ignore").strip()
        if proc.returncode != 0 or not text:
            # 진짜 원인은 stderr 또는 (text 출력형식에선) stdout에 있을 수 있다 — 둘 다 본다.
            detail = (err.decode(errors="ignore").strip()
                      or out.decode(errors="ignore").strip())[:500]
            raise RuntimeError(_cli_error_hint(proc.returncode, detail, self.model))
        return text
