"""손절 판정(M3 명세 3.9, 제품 규칙 5.4절).

계산과 판정만 하고 DB와 시세를 부르지 않는다(3.2). 취소, 체결 판정, 계정 다시 읽기, 엔진에
매도를 내는 차례는 `app`이 한다: `stop_loss_check`가 `REACHED`이면 그 코인의 걸린 주문마다
취소 전 판정과 취소 → 계정을 다시 읽음 → `stop_loss_plan` → 식별값을 먼저 저장하고 엔진에 냄.

- 손절선: 평균 매수가 × (1 − 손절 비율 ÷ 100). 현재가 ≤ 손절선이면 닿은 것이다.
- 평균 매수가가 없거나 0이면 손절하지 않고 A-9다(`NO_AVG`).
- 손절 매도는 팔 수 있는 수량(`qty − locked_qty`)을 시장가로 판다. 그 평가 금액이 최소 주문
  금액보다 작으면 팔지 않고 A-3이다.
- 전량 매도(3.10.3)는 `stop_loss_plan`을 `reason=OrderReason.SELL_ALL`로 부른다.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from coin_report_trader.core.trade import (
    OrderKind,
    OrderPlan,
    OrderReason,
    OrderRules,
    OrderSide,
    PaperHolding,
    TradeSettings,
)

_ALERT_TOO_SMALL = "A-3"
_HUNDRED = Decimal(100)


class StopCheck(StrEnum):
    """손절 판정의 결과."""

    NO_HOLDING = "no_holding"
    """보유가 없다."""
    NO_AVG = "no_avg"
    """평균 매수가가 없거나 0이다(A-9)."""
    ABOVE = "above"
    """손절선보다 높다."""
    REACHED = "reached"
    """손절선에 닿았다."""


@dataclass(frozen=True)
class StopDecision:
    """`stop_loss_plan`의 결과."""

    plan: OrderPlan | None
    """시장가 매도. 팔지 않으면 `None`."""
    alert: str | None
    """`plan`이 `None`일 때의 알림 번호(A-3). 없으면 `None`."""


def stop_line(avg_price: Decimal | None, stop_ratio: Decimal) -> Decimal | None:
    """손절선. 평균 매수가가 없거나 0이면 `None`이다."""
    if avg_price is None or avg_price <= 0:
        return None
    return avg_price * (1 - stop_ratio / _HUNDRED)


def stop_loss_check(
    holding: PaperHolding | None, price: Decimal, settings: TradeSettings
) -> StopCheck:
    """현재가 `price`로 손절선에 닿았는지 본다."""
    if holding is None or holding.qty <= 0:
        return StopCheck.NO_HOLDING
    line = stop_line(holding.avg_price, settings.stop_ratio)
    if line is None:
        return StopCheck.NO_AVG
    return StopCheck.REACHED if price <= line else StopCheck.ABOVE


def stop_loss_plan(
    holding: PaperHolding,
    price: Decimal,
    settings: TradeSettings,
    rules: OrderRules,
    *,
    reason: OrderReason = OrderReason.STOP_LOSS,
) -> StopDecision:
    """다시 읽은 보유로 팔 수 있는 수량을 시장가로 파는 계획.

    `amount`는 `price` × 수량의 어림이다. 팔 수 있는 수량의 평가 금액이 `rules.min_order`보다
    작으면 `plan`이 `None`이고 A-3이다. `settings`는 명세의 꼴(3.9)대로 받는다(지금의 셈은 쓰지
    않는다).
    """
    del settings
    if reason is OrderReason.JUDGMENT:
        raise ValueError("손절 매도의 까닭은 손절이나 긴급 매도다")
    if price <= 0:
        raise ValueError("가격은 0보다 커야 한다")
    qty = holding.qty - holding.locked_qty
    amount = price * qty
    if qty <= 0 or amount < rules.min_order:
        return StopDecision(plan=None, alert=_ALERT_TOO_SMALL)
    plan = OrderPlan(
        market=holding.market,
        side=OrderSide.SELL,
        kind=OrderKind.MARKET,
        reason=reason,
        price=None,
        qty=qty,
        amount=amount,
    )
    return StopDecision(plan=plan, alert=None)
