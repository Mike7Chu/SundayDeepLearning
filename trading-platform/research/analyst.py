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
import re
import shutil
import time

from research.data import StockData, format_for_prompt
from research.lenses import DISCLAIMER, SYSTEM_PROMPT
from shared.settings import settings

logger = logging.getLogger(__name__)

_CLI_TIMEOUT = 180.0          # CLI 분석 1건 최대 대기(초)
_COACH_CLI_TIMEOUT = 420.0    # 아침 점검(웹검색 포함) 최대 대기(초)


def parse_penalty(text: str) -> int:
    """리포트에서 '감점: N/30' 추출(순수 함수). 못 찾으면 보수적으로 30."""
    m = None
    for m in re.finditer(r"감점\s*[:：]?\s*(\d{1,2})\s*/\s*30", text or ""):
        pass                     # 마지막 매치 사용(요약에 재언급될 수 있음)
    if not m:
        return 30
    return max(0, min(30, int(m.group(1))))


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
            "종합해 '매수/분할매수/보류/회피' 관점을 명확히 제시하세요.\n\n"
            f"{format_for_prompt(data)}"
        )
        try:
            report = await (self._via_api(prompt) if mode == "api" else self._via_cli(prompt))
        except Exception as exc:
            # 실패를 조용히 삼키지 않고 리포트에 노출(대시보드에서 원인 확인 가능).
            logger.warning("[research %s] 분석 실패(mode=%s): %s", data.code, mode, exc)
            return self._wrap(data, enabled=True,
                              report=f"⚠️ 분석 실패 (백엔드={mode})\n{exc}")
        return self._wrap(data, enabled=True, report=report)

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
            "웹 검색이 가능하면 다음을 확인해 보강하세요(불가하면 생략, 추측 금지): "
            "①HBM·DDR5 현물/계약 가격 동향 ②빅테크(MS·메타·구글·아마존) CAPEX 가이던스 "
            "뉴스 ③ADR 시세 교차확인. 블록이 없고 검색도 불가할 때만 '확인 불가'로 표시.\n"
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
            msg = err.decode(errors="ignore").strip()[:500] or f"(빈 출력, rc={proc.returncode})"
            raise RuntimeError(
                f"claude CLI 실패(rc={proc.returncode}). 컨테이너에는 호스트 구독 로그인이 "
                f"없어 실패합니다 → 호스트에서 run-research-host.sh 실행. stderr: {msg}")
        return text
