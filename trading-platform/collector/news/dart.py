"""DART 전자공시(opendart.fss.or.kr) 클라이언트 — 무료 공식 API.

최근 공시 목록(list.json)을 폴링해 관심종목(또는 전 종목) 공시를 빠르게 포착.
키 미설정이면 비활성(idle). 순수 파서(parse_disclosure_list)는 네트워크 없이 테스트.
"""
from __future__ import annotations

import datetime as _dt
import logging

import httpx

from shared.settings import settings

logger = logging.getLogger(__name__)

_LIST_URL = "https://opendart.fss.or.kr/api/list.json"


def parse_disclosure_list(payload: dict) -> list[dict]:
    """DART list.json 응답 → 공시 리스트(순수 함수). status '000'만 유효."""
    if not isinstance(payload, dict) or payload.get("status") != "000":
        return []
    out: list[dict] = []
    for it in payload.get("list", []) or []:
        rcept = it.get("rcept_no")
        if not rcept:
            continue
        out.append({
            "rcept_no": rcept,
            "corp_name": it.get("corp_name", ""),
            "stock_code": (it.get("stock_code") or "").strip(),
            "report_nm": it.get("report_nm", ""),
            "flr_nm": it.get("flr_nm", ""),                 # 공시제출인
            "rcept_dt": it.get("rcept_dt", ""),
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept}",
        })
    return out


class DartClient:
    @property
    def enabled(self) -> bool:
        return bool(settings.dart_api_key)

    async def fetch_recent(self, client: httpx.AsyncClient, page_count: int = 100) -> list[dict]:
        """오늘자 최근 공시(전 종목) 목록. 최신순으로 page_count건."""
        today = _dt.date.today().strftime("%Y%m%d")
        params = {
            "crtfc_key": settings.dart_api_key,
            "bgn_de": today, "end_de": today,
            "page_no": 1, "page_count": page_count, "sort": "date", "sort_mth": "desc",
        }
        r = await client.get(_LIST_URL, params=params)
        r.raise_for_status()
        return parse_disclosure_list(r.json())


def format_disclosure(d: dict) -> str:
    """텔레그램 알림 문구."""
    name = d.get("corp_name") or d.get("stock_code") or ""
    return f"📢공시 {name}\n{d.get('report_nm','')}\n{d.get('url','')}"
