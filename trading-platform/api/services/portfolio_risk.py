"""포트폴리오 리스크 — 섹터 쏠림·단일 종목 비중·보유 종목 상관('사실상 1베팅').

자산이 커질수록 종목 선택보다 '한 방에 안 터지는 분산'이 중요해진다.
- 단일 종목 비중: 한 종목이 자산의 큰 비중이면 그 종목 사고에 전체가 흔들림.
- 섹터 집중: 3종목이라도 다 반도체면 반도체 하나에 베팅한 것.
- 상관관계: 종목이 달라도 같이 움직이면(r↑) 분산 효과가 없다.
전부 순수 함수 — 데이터 적재·조회는 API가 담당. 판단 보조(면책).
"""
from __future__ import annotations

# 섹터 매핑(큐레이션) — 없으면 이름 키워드 추정, 그래도 없으면 '기타'.
_SECTOR = {
    # 미국 반도체·AI 하드웨어
    "NVDA": "반도체", "AMD": "반도체", "AVGO": "반도체", "TSM": "반도체",
    "MU": "반도체", "INTC": "반도체", "QCOM": "반도체", "TXN": "반도체",
    "AMAT": "반도체", "ADI": "반도체", "LRCX": "반도체", "KLAC": "반도체",
    "MRVL": "반도체", "NXPI": "반도체", "ON": "반도체", "ASML": "반도체",
    "ARM": "반도체", "SMCI": "반도체", "DELL": "IT하드웨어",
    # 미국 빅테크·소프트웨어
    "AAPL": "빅테크", "MSFT": "빅테크", "GOOGL": "빅테크", "AMZN": "빅테크",
    "META": "빅테크", "NFLX": "빅테크", "ORCL": "소프트웨어", "CRM": "소프트웨어",
    "ADBE": "소프트웨어", "NOW": "소프트웨어", "PANW": "소프트웨어",
    "CRWD": "소프트웨어", "SNOW": "소프트웨어", "PLTR": "소프트웨어",
    "SHOP": "소프트웨어", "SPOT": "소프트웨어", "RBLX": "소프트웨어",
    "UBER": "인터넷", "ABNB": "인터넷", "IBM": "IT서비스", "CSCO": "IT하드웨어",
    "TSLA": "전기차", "RIVN": "전기차", "GM": "자동차", "F": "자동차",
    "COIN": "크립토", "MSTR": "크립토", "HOOD": "핀테크", "PYPL": "핀테크",
    # 미국 금융
    "JPM": "금융", "BAC": "금융", "WFC": "금융", "C": "금융", "GS": "금융",
    "MS": "금융", "SCHW": "금융", "BLK": "금융", "AXP": "금융", "V": "금융",
    "MA": "금융",
    # 미국 헬스케어·소비재·에너지·산업
    "LLY": "헬스케어", "UNH": "헬스케어", "JNJ": "헬스케어", "ABBV": "헬스케어",
    "MRK": "헬스케어", "PFE": "헬스케어", "TMO": "헬스케어", "ABT": "헬스케어",
    "AMGN": "헬스케어", "GILD": "헬스케어", "ISRG": "헬스케어", "NVO": "헬스케어",
    "KO": "소비재", "PEP": "소비재", "PG": "소비재", "WMT": "소비재",
    "COST": "소비재", "MCD": "소비재", "NKE": "소비재", "SBUX": "소비재",
    "PM": "소비재", "TGT": "소비재", "HD": "소비재", "DIS": "미디어",
    "XOM": "에너지", "CVX": "에너지", "COP": "에너지", "BA": "산업재",
    "CAT": "산업재", "GE": "산업재", "HON": "산업재", "LMT": "방산",
    "RTX": "방산", "DE": "산업재", "UPS": "물류", "FDX": "물류", "UNP": "물류",
    "T": "통신", "VZ": "통신", "TMUS": "통신", "SPCX": "우주",
    # 국내 주요
    "005930": "반도체", "000660": "반도체", "042700": "반도체",
    "005490": "소재", "051910": "소재", "006400": "2차전지",
    "373220": "2차전지", "247540": "2차전지", "066970": "2차전지",
    "035420": "인터넷", "035720": "인터넷", "323410": "핀테크",
    "005380": "자동차", "000270": "자동차", "012330": "자동차부품",
    "068270": "바이오", "207940": "바이오", "196170": "바이오",
    "105560": "금융", "055550": "금융", "086790": "금융",
    "015760": "전력", "034020": "산업재", "009540": "조선", "010140": "조선",
}

# 이름 키워드 → 섹터(매핑에 없을 때 추정)
_KEYWORDS = [
    ("반도체", "반도체"), ("전자", "전자"), ("바이오", "바이오"), ("제약", "바이오"),
    ("에너지", "에너지"), ("화학", "소재"), ("금융", "금융"), ("은행", "금융"),
    ("증권", "금융"), ("보험", "금융"), ("건설", "건설"), ("조선", "조선"),
    ("자동차", "자동차"), ("배터리", "2차전지"), ("2차전지", "2차전지"),
    ("게임", "게임"), ("엔터", "엔터"), ("통신", "통신"), ("전력", "전력"),
]


def sector_of(code: str, name: str = "") -> str:
    """종목 → 섹터(순수 함수). 매핑 우선, 없으면 이름 키워드, 그래도 없으면 기타."""
    c = (code or "").upper()
    if c in _SECTOR:
        return _SECTOR[c]
    for kw, sec in _KEYWORDS:
        if kw in (name or ""):
            return sec
    return "기타"


def _krw(h: dict, fx: float | None) -> float:
    """보유 1종목의 원화 평가액(미국은 환율 환산)."""
    v = h.get("eval_amount") or 0.0
    if (h.get("currency") == "USD") and fx:
        return v * fx
    return v


def _returns(closes: list[float]) -> list[float]:
    """일간 수익률 시퀀스(순수)."""
    out = []
    for i in range(1, len(closes)):
        if closes[i - 1]:
            out.append(closes[i] / closes[i - 1] - 1)
    return out


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = min(len(xs), len(ys))
    if n < 20:
        return None
    xs, ys = xs[-n:], ys[-n:]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return round(sxy / (sxx * syy) ** 0.5, 2)


def correlations(closes_map: dict[str, list[float]], names: dict[str, str],
                 threshold: float = 0.7) -> list[dict]:
    """보유 종목 쌍별 일간수익률 상관(순수). |r|≥threshold면 '동조'(분산 효과 약함)."""
    codes = [c for c, cl in closes_map.items() if len(cl) >= 21]
    rets = {c: _returns(closes_map[c]) for c in codes}
    out = []
    for i, a in enumerate(codes):
        for b in codes[i + 1:]:
            r = _pearson(rets[a], rets[b])
            if r is None:
                continue
            out.append({"a": a, "a_name": names.get(a, a),
                        "b": b, "b_name": names.get(b, b),
                        "r": r, "high": r >= threshold})
    out.sort(key=lambda x: x["r"], reverse=True)
    return out


def assess_risk(holdings: list[dict], closes_map: dict[str, list[float]],
                fx: float | None = None, single_warn: float = 30.0,
                sector_warn: float = 50.0, corr_warn: float = 0.7) -> dict:
    """포트폴리오 리스크 종합(순수 함수).

    반환 {total, weights[], sectors[], max_single, top_sector, hhi,
    correlations[], flags[], level}. level = 낮음/보통/높음.
    """
    names = {h.get("symbol"): (h.get("name") or h.get("symbol"))
             for h in holdings if h.get("symbol")}
    vals = {h["symbol"]: _krw(h, fx) for h in holdings if h.get("symbol")}
    total = sum(vals.values())
    if total <= 0:
        return {"total": 0, "weights": [], "sectors": [], "max_single": None,
                "top_sector": None, "hhi": None, "correlations": [],
                "flags": [], "level": None}
    weights = []
    sector_w: dict[str, float] = {}
    for h in holdings:
        code = h.get("symbol")
        if not code:
            continue
        w = round(vals[code] / total * 100, 1)
        sec = sector_of(code, names.get(code, ""))
        weights.append({"code": code, "name": names.get(code, code),
                        "weight": w, "sector": sec})
        sector_w[sec] = round(sector_w.get(sec, 0) + w, 1)
    weights.sort(key=lambda x: x["weight"], reverse=True)
    sectors = sorted(({"sector": s, "weight": w} for s, w in sector_w.items()),
                     key=lambda x: x["weight"], reverse=True)
    hhi = round(sum((w["weight"] / 100) ** 2 for w in weights), 3)  # 0~1(1=단일종목)
    corr = correlations(closes_map, names, corr_warn)

    flags = []
    max_single = weights[0] if weights else None
    if max_single and max_single["weight"] >= single_warn:
        flags.append(f"단일 종목 쏠림 — {max_single['name']} {max_single['weight']:.0f}% "
                     f"(권장 {single_warn:.0f}% 이하)")
    top_sector = sectors[0] if sectors else None
    if top_sector and top_sector["weight"] >= sector_warn and top_sector["sector"] != "기타":
        flags.append(f"섹터 쏠림 — {top_sector['sector']} {top_sector['weight']:.0f}% "
                     f"(권장 {sector_warn:.0f}% 이하)")
    for c in corr:
        if c["high"]:
            flags.append(f"동조 위험 — {c['a_name']}·{c['b_name']} 상관 {c['r']:.2f} "
                         "(같이 움직여 분산 효과 약함)")
    # 종합 등급: 플래그 수 + HHI 기반
    if hhi >= 0.5 or len(flags) >= 3:
        level = "높음"
    elif hhi >= 0.3 or flags:
        level = "보통"
    else:
        level = "낮음"
    return {"total": round(total, 0), "weights": weights, "sectors": sectors,
            "max_single": max_single, "top_sector": top_sector, "hhi": hhi,
            "correlations": corr[:8], "flags": flags, "level": level,
            "n": len(weights)}
