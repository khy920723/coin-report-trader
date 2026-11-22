"""한도(M3 명세 3.8.2, 제품 규칙 5.5절, 5.7절).

`check_buy`가 아래 차례대로 보고 처음 걸린 까닭을 돌려준다. 매수에만 건다(매도는 막지 않는다).
걸었다 지우기의 상한은 매수와 매도 모두의 판단의 주문에 거므로 `check_cancel_cap`이 따로 본다.
손절과 긴급 매도에는 둘 다 걸지 않는다. 계산만 하고, 값은 `app`이 저장된 기록에서 모아
`BuyContext`로 넘긴다.

| 차례 | 걸리는 것 | 까닭 |
|---|---|---|
| 1 | 하루 손실 한도에 닿았다 | "하루 손실 한도" |
| 2 | 유의·주의 표시가 있다 | "유의·주의" |
| 3 | 저장된 설정이 최소 주문 금액 검사에 걸린다 | "설정이 검사에 걸림" |
| 4 | 같은 코인의 마지막 프로그램 주문에서 주문 간격이 지나지 않았다 | "주문 간격" |
| 6 | 사면 코인 예산을 넘는다 | "코인 예산" |
| 7 | 사면 예산(고른 코인 전체의 상한)을 넘는다 | "예산" |
| 8 | 가상 원화가 모자란다 | "가상 원화가 모자람" |
| 9 | 확인 안 된 주문이나 취소가 확인되지 않은 주문이 있다 | "확인 안 된 주문" |

- 예산의 셈: 매수 원가(평균 매수가 × 보유 수량) + 걸린 매수 주문 금액(가격 × 수량) + 이번 금액이
  상한보다 크면 넘는다. 코인 예산은 그 코인의 것, 예산은 고른 코인들의 합이다. 수수료는 넣지
  않는다. 남은 금액에 맞춰 줄여서 사지 않는다(REQ-RISK-002).
- 가상 원화: `krw − locked_krw < 가격 × 수량 × (1 + f)`이면 모자란다(f는 `PAPER_FEE_RATE`).
- 하루 손실 한도: 그날 프로그램이 판 체결의 실현 손익 합이 −하루 손실 한도 이하이면 닿은 것이다.
- 주문 간격: 마지막 프로그램 주문을 저장한 때 + 주문 간격 > 지금이면 걸린다.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from coin_report_trader.core.trade import (
    DAILY_CANCEL_LIMIT,
    PAPER_FEE_RATE,
    NoOrderReason,
    OrderKind,
    OrderPlan,
    OrderRecord,
    OrderRules,
    OrderSide,
    OrderState,
    PaperAccount,
    TradeSettings,
)

_ZERO = Decimal(0)


@dataclass(frozen=True)
class BuyContext:
    """`check_buy`에 넘기는 저장된 기록의 값(3.8.2). `app`이 모은다."""

    account: PaperAccount
    open_orders: tuple[OrderRecord, ...]
    """고른 코인 모두의 걸린 주문."""
    day_realized_pnl: Decimal
    """그날(한국 09:00 경계) 프로그램이 판 체결의 실현 손익 합."""
    caution: bool
    """그 코인의 유의·주의 표시."""
    settings_check_failed: bool
    """저장된 설정이 최소 주문 금액 검사에 걸린다(3.11.2의 마지막 줄)."""
    last_order_utc: datetime | None
    """그 코인의 마지막 프로그램 주문을 저장한 때(날짜와 상관없이 가장 늦은 것)."""
    unconfirmed: bool
    """그 코인에 `UNCONFIRMED` 주문이나 취소가 확인되지 않은 주문이 있다."""


def check_buy(
    plan: OrderPlan,
    ctx: BuyContext,
    settings: TradeSettings,
    rules: OrderRules,
    now: datetime,
) -> NoOrderReason | None:
    """매수 한도를 차례대로 보고 처음 걸린 까닭을 준다. 걸리지 않으면 `None`. 매도는 `None`이다.

    `rules`는 명세의 꼴(3.8.2)대로 받는다. 지금의 한도는 그 값을 쓰지 않는다(최소 주문 금액의
    검사는 `decide`와 `check_trade_settings`가 한다).
    """
    del rules
    if plan.side is not OrderSide.BUY:
        return None
    if plan.kind is not OrderKind.LIMIT or plan.price is None:
        raise ValueError("매수는 지정가다")
    if ctx.day_realized_pnl <= -settings.daily_loss_limit:
        return NoOrderReason.DAILY_LOSS_LIMIT
    if ctx.caution:
        return NoOrderReason.CAUTION
    if ctx.settings_check_failed:
        return NoOrderReason.SETTINGS_CHECK
    if ctx.last_order_utc is not None and (
        ctx.last_order_utc + timedelta(seconds=settings.order_interval_s) > now
    ):
        return NoOrderReason.ORDER_INTERVAL
    if _spent(ctx, (plan.market,)) + plan.amount > settings.coin_budget(plan.market):
        return NoOrderReason.COIN_BUDGET
    if _spent(ctx, settings.coin_ratios.keys()) + plan.amount > settings.budget:
        return NoOrderReason.BUDGET
    cost = plan.price * plan.qty * (1 + PAPER_FEE_RATE)
    if ctx.account.krw - ctx.account.locked_krw < cost:
        return NoOrderReason.NO_KRW
    if ctx.unconfirmed:
        return NoOrderReason.UNCONFIRMED_ORDER
    return None


def check_cancel_cap(cancels_today: int) -> NoOrderReason | None:
    """걸었다 지우기의 상한(5.7절): 그날 만든 그 코인의 판단의 주문 가운데 취소된 수가 48에 닿으면
    그날 그 코인에 판단의 주문을 새로 걸지 않는다(매수와 매도 모두. 알림 없음)."""
    if cancels_today < 0:
        raise ValueError("취소된 수는 0보다 작을 수 없다")
    if cancels_today >= DAILY_CANCEL_LIMIT:
        return NoOrderReason.DAILY_CANCEL_CAP
    return None


def _spent(ctx: BuyContext, markets: Iterable[str]) -> Decimal:
    """그 코인들의 매수 원가와 걸린 매수 주문 금액의 합(수수료 없음)."""
    chosen = set(markets)
    total = _ZERO
    for holding in ctx.account.holdings:
        if holding.market in chosen and holding.qty > 0 and holding.avg_price is not None:
            total += holding.avg_price * holding.qty
    for order in ctx.open_orders:
        if (
            order.market in chosen
            and order.side is OrderSide.BUY
            and order.state is OrderState.OPEN
            and order.price is not None
        ):
            total += order.price * order.qty
    return total
