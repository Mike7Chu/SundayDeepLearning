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

_CLI_TIMEOUT = 180.0   # CLI 분석 1건 최대 대기(초)


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
            "(권위 소스: 증권사 실시간 API)입니다. 당신의 학습 기억 속 과거 주가·시총 수준과 "
            "다르더라도(예: 시장 대세 상승, AI·HBM 붐으로 코스피 8000대) 데이터 오류로 "
            "단정하지 마세요. '가격 필드 오류 가능성' 같은 경고를 만들지 말고, 제공된 수치를 "
            "사실로 전제하여 분석하세요.\n"
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

    async def _via_cli(self, prompt: str) -> str:
        """Claude Code 헤드리스(`claude -p`)로 분석 — 구독 사용량 내, 추가과금 없음.

        system 프롬프트는 인자 호환성을 위해 본문에 합쳐 전달한다.
        """
        full = f"{SYSTEM_PROMPT}\n\n=== 분석 요청 ===\n{prompt}"
        proc = await asyncio.create_subprocess_exec(
            settings.research_cli_bin, "-p", full,
            "--model", self.model, "--output-format", "text",
            stdin=asyncio.subprocess.DEVNULL,   # stdin 대기(no stdin data…) → rc=129 방지
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=_CLI_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"claude CLI 시간초과({_CLI_TIMEOUT}s)")
        text = out.decode(errors="ignore").strip()
        if proc.returncode != 0 or not text:
            msg = err.decode(errors="ignore").strip()[:500] or f"(빈 출력, rc={proc.returncode})"
            raise RuntimeError(
                f"claude CLI 실패(rc={proc.returncode}). 컨테이너에는 호스트 구독 로그인이 "
                f"없어 실패합니다 → 호스트에서 run-research-host.sh 실행. stderr: {msg}")
        return text
