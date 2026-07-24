"""매매 비용 모델 — 거래세·수수료·슬리피지를 반영한 '순손익(net)'.

모의(paper)에는 이런 비용이 없어서, 비용을 무시하면 특히 초단타 전략이 '되는 것처럼'
보인다(실전 가면 매 거래 비용이 수익을 갉아먹음). 성적표는 반드시 gross(총손익)와
net(비용 차감) 둘 다 보여줘 그 착시를 드러낸다. 전부 순수 함수(테스트 용이).

- 국내: 매도 시 증권거래세+농특세(kr_sell_tax_pct) + 위탁수수료(양방향) + 슬리피지.
- 미국: 매도 시 SEC/TAF(미미) + 위탁수수료(양방향) + 슬리피지. (거래세 없음)
비용률은 설정으로 조정(shared.settings). 판단 보조 — 실제 체결 비용과 다를 수 있음.
"""
from __future__ import annotations

from shared.settings import settings


def side_cost(price: float, qty: float, side: str, kr: bool = True) -> float:
    """한 방향(매수 또는 매도) 체결 비용(원/달러). 수수료·슬리피지 공통, 세금은 매도만.

    비용 = 체결금액 × (위탁수수료 + 슬리피지 + (매도면 거래세))/100.
    """
    if not price or price <= 0 or not qty or qty <= 0:
        return 0.0
    notional = price * qty
    rate = settings.brokerage_pct + settings.slippage_pct
    if side.upper() == "SELL":
        rate += settings.kr_sell_tax_pct if kr else settings.us_sell_fee_pct
    return notional * rate / 100.0


def round_trip(entry: float, exit_: float, qty: float,
               kr: bool = True) -> dict:
    """1회 왕복(매수→매도) 손익. {gross, cost, net, gross_pct, net_pct, ret_r}.

    gross = (매도가−매수가)×수량. cost = 매수비용+매도비용. net = gross−cost.
    *_pct는 투입금액(매수 체결금액) 대비 %. ret_r은 net을 투입 대비 배수(손익비 집계용).
    """
    if not entry or entry <= 0 or not qty or qty <= 0:
        return {"gross": 0.0, "cost": 0.0, "net": 0.0,
                "gross_pct": 0.0, "net_pct": 0.0}
    gross = (exit_ - entry) * qty
    cost = side_cost(entry, qty, "BUY", kr) + side_cost(exit_, qty, "SELL", kr)
    net = gross - cost
    invested = entry * qty
    return {
        "gross": round(gross, 2), "cost": round(cost, 2), "net": round(net, 2),
        "gross_pct": round(gross / invested * 100, 2),
        "net_pct": round(net / invested * 100, 2),
    }


def cost_drag_pct(kr: bool = True) -> float:
    """왕복 1회의 대략적 비용 부담(%) — '이만큼은 벌어야 본전'. 초단타 경고용.

    매수(수수료+슬리피지) + 매도(수수료+슬리피지+세금). 진입가≈청산가 가정 근사.
    """
    both = 2 * (settings.brokerage_pct + settings.slippage_pct)
    tax = settings.kr_sell_tax_pct if kr else settings.us_sell_fee_pct
    return round(both + tax, 3)
