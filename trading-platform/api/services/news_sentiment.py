"""뉴스·공시 제목 감성 점수 (순수 함수·규칙기반) — LLM 없이 토큰 0.

DART 공시/뉴스 제목에서 한국 증시 호재/악재 키워드를 세어 -1~+1 감성 스코어를 낸다.
정밀 감성(문맥·부정어)은 LLM이 낫지만, 매 종목 LLM은 토큰 과다 → 규칙기반으로 상시
무료 제공하고, 필요 시 온디맨드 LLM으로 심화한다. 보조 지표이며 매매 신호가 아니다(면책).
"""
from __future__ import annotations

# 한국 증시 자주 쓰이는 호재/악재 키워드(제목 기준). 순서 무관.
POS = [
    "최대실적", "역대 최대", "흑자전환", "흑자 전환", "실적개선", "실적 개선", "호실적",
    "수주", "계약", "공급계약", "공급", "납품", "증설", "신제품", "신규 개발", "승인", "허가",
    "특허", "자사주 소각", "소각", "배당", "주주환원", "주주 환원", "자사주 취득", "자사주 매입",
    "상향", "목표가 상향", "성장", "돌파", "신고가", "수출", "점유율 확대", "협력", "파트너십",
    "인수", "투자 유치", "무상증자", "편입", "선정", "수혜",
]
NEG = [
    "적자", "적자전환", "적자 전환", "실적악화", "실적 악화", "감익", "어닝쇼크",
    "하향", "목표가 하향", "소송", "리콜", "횡령", "배임", "분식",
    "유상증자", "전환사채", "신주인수권", "감자", "상장폐지", "상폐", "거래정지", "관리종목",
    "불성실공시", "해킹", "유출", "매각", "구조조정", "감원", "파업", "불매", "제재", "벌금",
    "과징금", "추징", "연기", "무산", "철회", "취소", "하락", "급락", "손상차손", "결손",
]


def _hits(title: str, words: list[str]) -> list[str]:
    return [w for w in words if w in title]


def news_sentiment(titles, cap: int = 25) -> dict | None:
    """제목 리스트 → 감성 스코어. 반환 {score,-1~1, label, pos, neg, n, samples} 또는 None.

    score = (호재-악재)/(호재+악재). 키워드 히트가 하나도 없으면 중립(0)으로 n>0이면 반환.
    """
    ts = [str(t) for t in (titles or []) if t][:cap]
    if not ts:
        return None
    pos = neg = 0
    samples: list[dict] = []
    for t in ts:
        ph, nh = _hits(t, POS), _hits(t, NEG)
        pos += len(ph)
        neg += len(nh)
        if (ph or nh) and len(samples) < 5:
            samples.append({"title": t[:60],
                            "tone": "호재" if len(ph) > len(nh)
                            else "악재" if len(nh) > len(ph) else "혼재"})
    tot = pos + neg
    score = round((pos - neg) / tot, 2) if tot else 0.0
    label = ("호재 우세" if score >= 0.25 else "악재 우세" if score <= -0.25
             else "중립·혼조")
    return {"score": score, "label": label, "pos": pos, "neg": neg,
            "n": len(ts), "samples": samples}
