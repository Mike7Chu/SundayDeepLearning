"""Claude API 가치투자 애널리스트.

종목 데이터를 4대 거장 렌즈로 분석해 구조화 리포트 생성. ANTHROPIC_API_KEY가
없으면 비활성(enabled=False)으로 안전하게 idle. anthropic SDK는 호출 시 지연 import
(키/패키지 없는 환경에서도 모듈 로드·테스트 가능).
"""
from __future__ import annotations

import logging
import time

from research.data import StockData, format_for_prompt
from research.lenses import DISCLAIMER, SYSTEM_PROMPT
from shared.settings import settings

logger = logging.getLogger(__name__)


class Analyst:
    def __init__(self) -> None:
        self.model = settings.research_model

    @property
    def enabled(self) -> bool:
        return bool(settings.anthropic_api_key)

    async def analyze(self, data: StockData) -> dict:
        """StockData → 구조화 리포트 dict. 비활성이면 disabled 리포트."""
        if not self.enabled:
            return {
                "code": data.code,
                "name": data.name,
                "model": self.model,
                "ts": time.time(),
                "enabled": False,
                "report": "ANTHROPIC_API_KEY 미설정 → 리서치 비활성. .env에 키 입력 후 활성화됩니다.",
                "disclaimer": DISCLAIMER,
            }

        # 지연 import: 키 있는 환경에서만 anthropic 필요
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        prompt = (
            "다음 종목을 4대 거장 렌즈로 분석해 정해진 출력 형식으로 정리하세요.\n\n"
            f"{format_for_prompt(data)}"
        )
        # 긴 출력 대비 스트리밍 + 적응형 사고(skill 권장)
        async with client.messages.stream(
            model=self.model,
            max_tokens=4096,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            msg = await stream.get_final_message()

        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return {
            "code": data.code,
            "name": data.name,
            "model": self.model,
            "ts": time.time(),
            "enabled": True,
            "report": text.strip(),
            "disclaimer": DISCLAIMER,
        }
