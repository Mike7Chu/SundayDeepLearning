"""KIS 종목마스터 다운로드/파싱 — 전체 시장 스크리너용 유니버스(코스피/코스닥 전 종목).

KIS가 제공하는 코드마스터(.mst.zip)를 받아 종목코드/이름을 추출한다. 파일 포맷은 고정폭이라
후행 숫자블록 길이(trailing)만 맞추면 됨(코스피 228 / 코스닥 222 관례). 오프셋은 거래소/버전에
따라 미세차가 있어 **Pi에서 검증** 권장. 다운로드 실패/미검증이면 유니버스는 관심종목으로 폴백.
"""
from __future__ import annotations

import io
import logging
import zipfile

import httpx

logger = logging.getLogger(__name__)

_SOURCES = [
    ("KOSPI", "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip", "kospi_code.mst", 228),
    ("KOSDAQ", "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip", "kosdaq_code.mst", 222),
]


def parse_mst(content: str, trailing: int, market: str = "") -> list[dict]:
    """고정폭 .mst 텍스트 → [{code, name, market}] (순수 함수).

    각 줄: [단축코드 9][표준코드 12][한글명 …][후행 숫자블록 trailing]. code=앞 6~9자리 숫자.
    """
    out: list[dict] = []
    for line in content.splitlines():
        if len(line) < 21 + trailing:
            continue
        code = line[0:9].strip()
        name = line[21:len(line) - trailing].strip()
        # 정규 상장주식/ETF/ETN은 6자리 숫자 단축코드만. ELW·신주인수권 등
        # 9자리 영숫자 코드(F74701B9A 등)는 inquire-price에서 500을 유발하므로 제외.
        if code.isdigit() and len(code) == 6:
            out.append({"code": code, "name": name, "market": market})
    return out


async def fetch_universe(client: httpx.AsyncClient) -> list[dict]:
    """코스피+코스닥 종목마스터 다운로드·파싱. 실패한 시장은 건너뜀."""
    universe: list[dict] = []
    for market, url, member, trailing in _SOURCES:
        try:
            r = await client.get(url, timeout=30)
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                raw = z.read(member).decode("cp949", errors="ignore")
            rows = parse_mst(raw, trailing, market)
            universe += rows
            logger.info("[universe] %s %d종목", market, len(rows))
        except Exception as exc:
            logger.warning("[universe] %s 실패: %s", market, exc)
    return universe
