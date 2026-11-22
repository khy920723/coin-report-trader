"""주문 규칙과 한도(M3 명세 3.5.2, 3.8, 3.11.2): 판단표, 허용 범위와 주문 값 만들기, 가격 안전
검사, 한도, 식별과 상태, 오류의 갈래, 설정의 받지 않는 값.

계산과 판정만 하고 DB를 열지 않고 시세를 부르지 않는다(3.2). `httpx`를 쓰지 않는다(3.3).

- `decide`: 판단표(3.5.2). `tolerance`: 허용 범위(3.8.5). `prices`: 가격과 수량(3.8.1).
- `limits`: 한도(3.8.2). `states`: 식별과 상태(3.8.3). `errors`: 오류의 세 갈래(3.8.4).
- `settings_check`: 설정의 받지 않는 값(3.11.2).

`__all__`에는 이 폴더에서 정의한 이름만 넣는다(`core/trade.py`의 타입을 다시 내보내지 않는다.
M3 명세 4절 3번).
"""

from coin_report_trader.trading.orders.decide import Decision, decide
from coin_report_trader.trading.orders.errors import (
    QUOTE_BLOCKING_NAMES,
    QUOTE_TRANSIENT_NAMES,
    error_branch,
)
from coin_report_trader.trading.orders.limits import BuyContext, check_buy, check_cancel_cap
from coin_report_trader.trading.orders.prices import buy_qty, price_guard_ok, round_to_tick
from coin_report_trader.trading.orders.settings_check import (
    check_trade_settings,
    coin_ratio_key,
    first_buy_floor,
)
from coin_report_trader.trading.orders.states import (
    STATE_CHANGES,
    can_change,
    cancel_wait_over,
    change_state,
    new_uid,
    unconfirmed_expired,
)
from coin_report_trader.trading.orders.tolerance import widen_price

__all__ = [
    "QUOTE_BLOCKING_NAMES",
    "QUOTE_TRANSIENT_NAMES",
    "STATE_CHANGES",
    "BuyContext",
    "Decision",
    "buy_qty",
    "can_change",
    "cancel_wait_over",
    "change_state",
    "check_buy",
    "check_cancel_cap",
    "check_trade_settings",
    "coin_ratio_key",
    "decide",
    "error_branch",
    "first_buy_floor",
    "new_uid",
    "price_guard_ok",
    "round_to_tick",
    "unconfirmed_expired",
    "widen_price",
]
