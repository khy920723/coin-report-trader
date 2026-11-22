"""식별과 상태의 차례(M3 명세 3.8.3, 제품 규칙 5.6절).

- 식별값: 주문마다 `uuid4`의 글이다. 내는 차례는 `app`이 한다: 식별값과 주문 값을 `orders`에
  `OPEN`, `sent` 0으로 쓴다 → 엔진(6단계는 거래소)에 낸다 → 결과로 `sent` 1과 상태를 쓴다.
- 상태 바꾸기: 걸림(`OPEN`)과 확인 안 됨(`UNCONFIRMED`)에서만 바뀐다. 체결, 취소, 보내지지 않음,
  실패는 끝난 상태이고 `closed_utc`를 쓴다.
- `UNCONFIRMED`는 "없음"이 `UNCONFIRMED_CLOSE_SECONDS`(600초) 동안 이어지면 `NOT_SENT`로 닫는다.
  손절 순서의 취소 확인 시간은 `CANCEL_CONFIRM_SECONDS`(10초)다. 모의매매에는 응답 없음이 없다.
- 켤 때 `sent` 0인 줄은 모의매매에서 보내지 않은 것으로 보고 `NOT_SENT`로 닫는다(묶음은
  `trading/paper/`의 `unlock_order`로 푼다).
"""

import uuid
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from types import MappingProxyType

from coin_report_trader.core.trade import (
    CANCEL_CONFIRM_SECONDS,
    UNCONFIRMED_CLOSE_SECONDS,
    OrderRecord,
    OrderState,
)

_CLOSED = frozenset(
    {OrderState.FILLED, OrderState.CANCELED, OrderState.NOT_SENT, OrderState.FAILED}
)

STATE_CHANGES: Mapping[OrderState, frozenset[OrderState]] = MappingProxyType(
    {
        OrderState.OPEN: frozenset({OrderState.UNCONFIRMED, *_CLOSED}),
        OrderState.UNCONFIRMED: frozenset({OrderState.OPEN, *_CLOSED}),
        OrderState.FILLED: frozenset(),
        OrderState.CANCELED: frozenset(),
        OrderState.NOT_SENT: frozenset(),
        OrderState.FAILED: frozenset(),
    }
)
"""상태마다 바뀔 수 있는 다음 상태."""


def new_uid() -> str:
    """주문의 식별값(`uuid4`의 글)."""
    return str(uuid.uuid4())


def can_change(current: OrderState, to: OrderState) -> bool:
    return to in STATE_CHANGES[current]


def change_state(
    order: OrderRecord, to: OrderState, now: datetime, *, error_name: str | None = None
) -> OrderRecord:
    """주문의 상태를 바꾼 줄. 끝난 상태로 가면 `closed_utc`가 `now`다.

    바꿀 수 없는 차례(끝난 주문을 다시 바꿈 등)는 `ValueError`다. `error_name`은 실패나 확인 안
    됨의 오류 이름이다(주지 않으면 지금 값을 둔다).
    """
    if now.utcoffset() is None:
        raise ValueError("시각은 시간대가 붙은 datetime이어야 한다")
    if not can_change(order.state, to):
        raise ValueError(f"주문 상태를 {order.state}에서 {to}로 바꿀 수 없다")
    closed = now if to in _CLOSED else None
    name = order.error_name if error_name is None else error_name
    return replace(order, state=to, closed_utc=closed, error_name=name)


def unconfirmed_expired(missing_since: datetime, now: datetime) -> bool:
    """확인 안 된 주문의 "없음"이 600초 동안 이어졌는지(그러면 `NOT_SENT`로 닫는다)."""
    return now - missing_since >= timedelta(seconds=UNCONFIRMED_CLOSE_SECONDS)


def cancel_wait_over(requested_utc: datetime, now: datetime) -> bool:
    """손절 순서에서 취소를 요청한 뒤 확인 시간(10초)이 지났는지."""
    return now - requested_utc >= timedelta(seconds=CANCEL_CONFIRM_SECONDS)
