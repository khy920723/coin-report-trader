"""가격과 수량(M3 명세 3.8.1, 제품 규칙 5.1절).

계산만 한다. 맞추기와 검사는 허용 범위로 넓힌 가격(3.8.5)에 건다.

- 호가 단위 맞추기: 매수 가격은 호가 단위로 내림(`floor(p ÷ t) × t`), 매도 가격은 올림
  (`ceil(p ÷ t) × t`)이다.
- 수량: 매수는 금액 ÷ 가격을 소수 여덟째 자리에서 내림한다(`QTY_PLACES`). 매도는 팔 수 있는 수량
  그대로다.
- 가격 안전 검사(현재가 C): 매수 가격이 C × (1 + `SAFETY_UP`)보다 크거나 C × `SAFETY_DOWN`보다
  작으면, 매도 가격이 C × (1 − `SAFETY_UP`)보다 작거나 C × `SAFETY_SELL_UP`보다 크면 걸린다.
"""

from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Decimal

from coin_report_trader.core.trade import (
    QTY_PLACES,
    SAFETY_DOWN,
    SAFETY_SELL_UP,
    SAFETY_UP,
    OrderSide,
)

_QTY_STEP = Decimal(1).scaleb(-QTY_PLACES)


def round_to_tick(price: Decimal, side: OrderSide, tick_size: Decimal) -> Decimal:
    """호가 단위에 맞춘 가격. 매수는 내림, 매도는 올림이다."""
    if tick_size <= 0:
        raise ValueError("호가 단위는 0보다 커야 한다")
    rounding = ROUND_FLOOR if side is OrderSide.BUY else ROUND_CEILING
    return (price / tick_size).to_integral_value(rounding=rounding) * tick_size


def buy_qty(amount: Decimal, price: Decimal) -> Decimal:
    """매수 수량: 금액 ÷ 가격을 소수 여덟째 자리에서 내림한다."""
    if price <= 0:
        raise ValueError("가격은 0보다 커야 한다")
    if amount < 0:
        raise ValueError("금액은 0보다 작을 수 없다")
    return (amount / price).quantize(_QTY_STEP, rounding=ROUND_DOWN)


def price_guard_ok(price: Decimal, side: OrderSide, current: Decimal) -> bool:
    """가격 안전 검사를 지나는지. 걸리면 주문하지 않고 A-4다."""
    if current <= 0:
        raise ValueError("현재가는 0보다 커야 한다")
    if side is OrderSide.BUY:
        return current * SAFETY_DOWN <= price <= current * (1 + SAFETY_UP)
    return current * (1 - SAFETY_UP) <= price <= current * SAFETY_SELL_UP
