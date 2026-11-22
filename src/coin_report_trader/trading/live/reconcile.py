"""대조와 외부 변동의 셈(M5 명세 3.6.3, 3.6.5, 3.6.6. 실거래).

계산만 한다. 읽기(`open_orders`, `query`, `read_account`)와 쓰기(`orders`, `fills`,
`live_snapshots`, `external_changes`, 알림)는 `app`이 한다(15번). 계정 화면의 읽기는 여기에 들지
않는다(3.15.2).

- 대조(3.6.5): 고른 코인마다 체결 대기 주문 목록을 읽고, 저장한 걸림·확인 안 됨 주문마다 조회해
  상태를 맞춘다. 목록에 있는데 저장한 식별값이 없는 주문은 사용자가 직접 건 주문이고, 프로그램은
  취소하지 않는다(`direct_orders`).
- 조회한 뒤의 상태(`queried_state`): 있으면 거래소의 상태다. "없음"이면 확인 안 됨이고, 확인 안 됨이
  600초 동안 이어지면 보내지 않은 것으로 닫는다(3.6.3, M3 명세 3.8.3). 켤 때 `sent` 0인 줄도
  바로 닫지 않고 같은 차례다(실거래).
- 외부 변동(3.6.6, A-22): 코인마다(그리고 원화) (지금 − 앞) − 그 사이 프로그램의 체결로 바뀐 몫이
  0이 아니면 외부 변동이다. 수량은 소수 여덟째 자리(`QTY_PLACES`), 원화는 1원 단위로 맞춰 견준다
  (`ROUND_HALF_EVEN`). 앞의 값이 없는 코인(처음 읽음)은 견주지 않는다.
- A-6: 직접 건 주문에 묶인 몫 = 묶인 수량 − 프로그램 주문에 묶인 수량(0보다 작으면 0). 그 몫이 0에서
  0보다 크게 바뀔 때 한 번이다.
- A-7: 앞의 목록에 있던 직접 건 주문이 다음 목록에서 사라졌고, 그 사이 그 코인의 프로그램 주문이
  반대편으로 체결되었으면 그 주문이다. 그 밖에는 가려낼 수 없어 알리지 않는다.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Final

from coin_report_trader.core.exchange import ExchangeAccount, ExchangeHolding, OrderSnapshot
from coin_report_trader.core.trade import QTY_PLACES, OrderRecord, OrderSide, OrderState
from coin_report_trader.trading.orders.states import unconfirmed_expired

KRW_MARKET: Final = "KRW"
"""원화의 `live_snapshots.market`(3.9)."""

_QTY_STEP = Decimal(1).scaleb(-QTY_PLACES)
_WON_STEP = Decimal(1)
_ZERO = Decimal(0)


@dataclass(frozen=True)
class LiveSnapshot:
    """마지막으로 읽은 실제 계정의 한 줄(`live_snapshots`. 3.9). 원화는 `market`이 `KRW`다."""

    market: str
    balance: Decimal
    locked: Decimal
    direct_locked: Decimal
    """직접 건 주문에 묶인 몫(A-6)."""
    read_utc: datetime


@dataclass(frozen=True)
class ExternalChange:
    """외부 변동 한 줄(`external_changes`. 3.9): 코인(원화는 `KRW`)과 바뀐 수량(원화는 금액)."""

    market: str
    qty_delta: Decimal


def direct_orders(
    open_orders: Iterable[OrderSnapshot], stored_uids: Iterable[str]
) -> tuple[OrderSnapshot, ...]:
    """체결 대기 주문 목록 가운데 저장한 식별값이 없는 것(사용자가 직접 건 주문. 3.6.5)."""
    ours = set(stored_uids)
    return tuple(order for order in open_orders if order.uid not in ours)


def queried_state(
    order: OrderRecord,
    snapshot: OrderSnapshot | None,
    *,
    missing_since: datetime | None,
    now: datetime,
) -> OrderState:
    """조회한 뒤 그 주문이 가질 상태(3.6.3, 3.6.5).

    - 거래소에 있으면(`snapshot`) 그 상태다.
    - "없음"이면 확인 안 됨이다. `missing_since`(처음 "없음"을 본 때. 처음이면 `None`)에서 600초가
      지났으면 보내지 않은 것(`NOT_SENT`)으로 닫는다.
    """
    if snapshot is not None:
        if snapshot.uid != order.uid:
            raise ValueError("다른 식별값의 조회 결과다")
        return snapshot.state
    if missing_since is not None and unconfirmed_expired(missing_since, now):
        return OrderState.NOT_SENT
    return OrderState.UNCONFIRMED


def snapshot_rows(
    account: ExchangeAccount,
    markets: Iterable[str],
    program_locked: Mapping[str, Decimal],
) -> tuple[LiveSnapshot, ...]:
    """`live_snapshots`에 쓸 줄: 원화 한 줄과 고른 코인마다 한 줄(보유가 없으면 0)."""
    holdings = {holding.market: holding for holding in account.holdings}
    rows = [
        LiveSnapshot(
            market=KRW_MARKET,
            balance=account.krw,
            locked=account.krw_locked,
            direct_locked=_ZERO,
            read_utc=account.read_utc,
        )
    ]
    for market in markets:
        holding = holdings.get(market)
        rows.append(
            LiveSnapshot(
                market=market,
                balance=_ZERO if holding is None else holding.balance,
                locked=_ZERO if holding is None else holding.locked,
                direct_locked=direct_locked(holding, program_locked.get(market, _ZERO)),
                read_utc=account.read_utc,
            )
        )
    return tuple(rows)


def find_external_changes(
    before: Iterable[LiveSnapshot],
    account: ExchangeAccount,
    markets: Iterable[str],
    program_deltas: Mapping[str, Decimal],
) -> tuple[ExternalChange, ...]:
    """외부 변동(3.6.6): 원화와 고른 코인마다 (지금 − 앞) − 프로그램 몫이 0이 아닌 것.

    - `before`: 마지막으로 읽은 실제 계정(`live_snapshots`).
    - `program_deltas`: 그 사이 프로그램의 체결로 바뀐 몫(코인은 수량, 원화 `KRW`는 금액. 매수면
      코인은 늘고 원화는 줄어든다). 없으면 0이다.
    - 수량은 보유 전체(`balance` + `locked`)로 견준다. 앞의 값이 없는 코인은 견주지 않는다.
    """
    previous = {row.market: row for row in before}
    holdings = {holding.market: holding for holding in account.holdings}
    changes: list[ExternalChange] = []
    krw_before = previous.get(KRW_MARKET)
    if krw_before is not None:
        delta = (account.krw + account.krw_locked) - (krw_before.balance + krw_before.locked)
        delta -= program_deltas.get(KRW_MARKET, _ZERO)
        delta = delta.quantize(_WON_STEP, rounding=ROUND_HALF_EVEN)
        if delta != 0:
            changes.append(ExternalChange(market=KRW_MARKET, qty_delta=delta))
    for market in markets:
        old = previous.get(market)
        if old is None:
            continue
        holding = holdings.get(market)
        now_qty = _ZERO if holding is None else holding.balance + holding.locked
        delta = now_qty - (old.balance + old.locked) - program_deltas.get(market, _ZERO)
        delta = delta.quantize(_QTY_STEP, rounding=ROUND_HALF_EVEN)
        if delta != 0:
            changes.append(ExternalChange(market=market, qty_delta=delta))
    return tuple(changes)


def direct_locked(holding: ExchangeHolding | None, program_locked: Decimal) -> Decimal:
    """직접 건 주문에 묶인 몫(A-6): 묶인 수량 − 프로그램 주문에 묶인 수량. 0보다 작으면 0이다."""
    if holding is None:
        return _ZERO
    return max(_ZERO, holding.locked - program_locked)


def direct_lock_started(before: Decimal, now: Decimal) -> bool:
    """A-6을 낼 때: 직접 건 주문에 묶인 몫이 0에서 0보다 크게 바뀌었다(상태가 바뀔 때 한 번)."""
    return before <= 0 < now


def canceled_direct_orders(
    before: Iterable[OrderSnapshot],
    now: Iterable[OrderSnapshot],
    program_fills: Iterable[tuple[str, OrderSide]],
) -> tuple[OrderSnapshot, ...]:
    """A-7: 앞의 목록의 직접 건 주문 가운데 지금 목록에서 사라졌고, 그 사이 그 코인의 프로그램
    주문이 반대편으로 체결된 것.

    - `before`, `now`: 앞과 지금의 직접 건 주문(`direct_orders`의 결과).
    - `program_fills`: 그 사이 체결된 프로그램 주문의 (코인, 방향).
    """
    still = {order.uid for order in now}
    filled = set(program_fills)
    return tuple(
        order
        for order in before
        if order.uid not in still and (order.market, _opposite(order.side)) in filled
    )


def _opposite(side: OrderSide) -> OrderSide:
    return OrderSide.SELL if side is OrderSide.BUY else OrderSide.BUY
