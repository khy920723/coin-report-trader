"""허용 범위(M3 명세 3.8.5, 제품 규칙 5.1절. 사용자 결정(2026-11-22)).

계산만 하고, `decide`(3.5.2)가 부른다.

- 허용 범위 t(%): 매수와 매도에 함께 쓰는 설정 하나다(`TradeSettings.tolerance`). 0이면 기준 가격
  그대로다.
- 넓힌 가격: 매수는 P × (1 + t ÷ 100)을 호가 단위로 내리고, 매도는 P × (1 − t ÷ 100)을 올린다.
  그래서 건 가격은 허용 범위 밖으로 나가지 않는다.
- 가격 안전 검사(3.8.1)는 넓힌 가격을 줄여 맞추지 않는다. 걸리면 주문하지 않는다.
"""

from decimal import Decimal

from coin_report_trader.core.trade import TOLERANCE_MAX, OrderSide
from coin_report_trader.trading.orders.prices import round_to_tick


def widen_price(base: Decimal, side: OrderSide, tolerance: Decimal, tick_size: Decimal) -> Decimal:
    """기준 가격 P를 허용 범위로 넓혀 호가 단위에 맞춘 가격."""
    if base <= 0:
        raise ValueError("기준 가격은 0보다 커야 한다")
    if not 0 <= tolerance <= TOLERANCE_MAX:
        raise ValueError("허용 범위는 0% 이상 2% 이하다")
    ratio = tolerance / Decimal(100)
    widened = base * (1 + ratio) if side is OrderSide.BUY else base * (1 - ratio)
    return round_to_tick(widened, side, tick_size)
