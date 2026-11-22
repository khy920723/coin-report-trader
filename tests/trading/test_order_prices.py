"""가격과 수량(M3 명세 3.8.1), 허용 범위(3.8.5), 오류의 갈래(3.8.4), 식별과 상태(3.8.3)의 시험.

값은 모두 만든 숫자다. 3.8.1과 3.8.5의 예를 모두 넣는다.
"""

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from coin_report_trader.core.alerts import Mode
from coin_report_trader.core.texts import (
    ERROR_BAD_RESPONSE,
    ERROR_BLOCKED,
    ERROR_MAINTENANCE,
    ERROR_NETWORK,
    ERROR_RATE_LIMIT,
    ERROR_SERVER,
    ERROR_TIMEOUT,
    error_unknown_exchange,
)
from coin_report_trader.core.trade import (
    OrderKind,
    OrderReason,
    OrderRecord,
    OrderSide,
    OrderState,
)
from coin_report_trader.trading.orders import (
    STATE_CHANGES,
    buy_qty,
    can_change,
    cancel_wait_over,
    change_state,
    error_branch,
    new_uid,
    price_guard_ok,
    round_to_tick,
    unconfirmed_expired,
    widen_price,
)
from coin_report_trader.trading.upbit import ErrorBranch

D = Decimal
BUY = OrderSide.BUY
SELL = OrderSide.SELL
T = datetime(2026, 1, 5, 3, 20, tzinfo=UTC)


# --- 호가 단위 맞추기, 수량, 가격 안전 검사(3.8.1) -------------------------------------------


def test_tick_rounding_example() -> None:
    # 호가 단위 1에서 매수 991.7은 991, 매도 1003.2는 1004다.
    assert round_to_tick(D("991.7"), BUY, D(1)) == D(991)
    assert round_to_tick(D("1003.2"), SELL, D(1)) == D(1004)


@pytest.mark.parametrize(
    ("price", "side", "tick", "expected"),
    [
        ("1000", BUY, "1", "1000"),
        ("1000", SELL, "1", "1000"),
        ("100.17", BUY, "0.1", "100.1"),
        ("100.11", SELL, "0.1", "100.2"),
        ("1234", BUY, "5", "1230"),
        ("1231", SELL, "5", "1235"),
    ],
)
def test_tick_rounding_goes_down_for_buy_and_up_for_sell(
    price: str, side: OrderSide, tick: str, expected: str
) -> None:
    assert round_to_tick(D(price), side, D(tick)) == D(expected)


def test_tick_must_be_positive() -> None:
    with pytest.raises(ValueError, match="호가 단위"):
        round_to_tick(D(1000), BUY, D(0))


def test_buy_qty_example() -> None:
    # 10,000원을 990에 사면 10.10101010개다(소수 여덟째 자리에서 내림).
    assert buy_qty(D(10_000), D(990)) == D("10.10101010")
    assert buy_qty(D(2), D(3)) == D("0.66666666")
    with pytest.raises(ValueError, match="가격"):
        buy_qty(D(1), D(0))
    with pytest.raises(ValueError, match="금액"):
        buy_qty(D(-1), D(1))


@pytest.mark.parametrize(
    ("price", "side", "ok"),
    [
        ("1020", BUY, True),
        ("1021", BUY, False),
        ("500", BUY, True),
        ("499", BUY, False),
        ("980", SELL, True),
        ("979", SELL, False),
        ("2000", SELL, True),
        ("2001", SELL, False),
    ],
)
def test_price_guard_example(price: str, side: OrderSide, ok: bool) -> None:
    # 현재가 1000이면 매수 1020은 되고 1021은 걸린다. 매도 980은 되고 979는 걸린다.
    assert price_guard_ok(D(price), side, D(1000)) is ok


def test_price_guard_needs_a_current_price() -> None:
    with pytest.raises(ValueError, match="현재가"):
        price_guard_ok(D(1000), BUY, D(0))


# --- 허용 범위(3.8.5) -------------------------------------------------------------------------


def test_tolerance_example() -> None:
    # P 1,000, 호가 단위 1, t 1.25%면 매수는 1,012.5를 내려 1,012, 매도는 987.5를 올려 988이다.
    assert widen_price(D(1000), BUY, D("1.25"), D(1)) == D(1012)
    assert widen_price(D(1000), SELL, D("1.25"), D(1)) == D(988)
    # t 0%면 둘 다 1,000이다.
    assert widen_price(D(1000), BUY, D(0), D(1)) == D(1000)
    assert widen_price(D(1000), SELL, D(0), D(1)) == D(1000)


def test_tolerance_with_the_price_guard_example() -> None:
    # 현재가 1,000, 매수 제안가 1,010에서 t 1%면 1,020.1을 내린 1,020이라 된다. t 2%면 1,030.2를
    # 내린 1,030이라 걸린다.
    one = widen_price(D(1010), BUY, D(1), D(1))
    two = widen_price(D(1010), BUY, D(2), D(1))
    assert (one, price_guard_ok(one, BUY, D(1000))) == (D(1020), True)
    assert (two, price_guard_ok(two, BUY, D(1000))) == (D(1030), False)


def test_widened_price_never_goes_outside_the_tolerance() -> None:
    for base in (D(997), D("1003.3"), D("1234.5")):
        for t in (D(0), D("0.5"), D("1.25"), D(2)):
            buy = widen_price(base, BUY, t, D(1))
            sell = widen_price(base, SELL, t, D(1))
            assert buy <= base * (1 + t / 100)
            assert sell >= base * (1 - t / 100)


def test_tolerance_range_is_checked() -> None:
    assert widen_price(D(1000), BUY, D(2), D(1)) == D(1020)
    with pytest.raises(ValueError, match="허용 범위"):
        widen_price(D(1000), BUY, D("2.01"), D(1))
    with pytest.raises(ValueError, match="허용 범위"):
        widen_price(D(1000), SELL, D("-0.01"), D(1))
    with pytest.raises(ValueError, match="기준 가격"):
        widen_price(D(0), BUY, D(1), D(1))


# --- 오류의 세 갈래(3.8.4) --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "branch"),
    [
        (ERROR_BLOCKED, ErrorBranch.BLOCKING),
        (ERROR_RATE_LIMIT, ErrorBranch.TRANSIENT),
        (ERROR_MAINTENANCE, ErrorBranch.TRANSIENT),
        (ERROR_SERVER, ErrorBranch.TRANSIENT),
        (ERROR_TIMEOUT, ErrorBranch.TRANSIENT),
        (ERROR_NETWORK, ErrorBranch.TRANSIENT),
        (ERROR_BAD_RESPONSE, ErrorBranch.TRANSIENT),
        (error_unknown_exchange(451), ErrorBranch.UNLISTED),
        ("만든 오류 이름", ErrorBranch.UNLISTED),
    ],
)
def test_error_branches_of_3_8_4(name: str, branch: ErrorBranch) -> None:
    assert error_branch(name) is branch


# --- 식별과 상태(3.8.3) -----------------------------------------------------------------------


def record(state: OrderState = OrderState.OPEN) -> OrderRecord:
    return OrderRecord(
        order_id=1,
        uid=new_uid(),
        mode=Mode.PAPER,
        round_id=3,
        market="KRW-AAA",
        side=BUY,
        kind=OrderKind.LIMIT,
        reason=OrderReason.JUDGMENT,
        price=D(990),
        qty=D(1),
        state=state,
        created_utc=T,
        closed_utc=None,
        error_name=None,
    )


def test_uid_is_a_new_uuid4_text_each_time() -> None:
    first, second = new_uid(), new_uid()
    assert first != second
    pattern = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
    assert re.fullmatch(pattern, first)


@pytest.mark.parametrize(
    "to",
    [OrderState.FILLED, OrderState.CANCELED, OrderState.NOT_SENT, OrderState.FAILED],
)
def test_closing_states_write_the_closed_time(to: OrderState) -> None:
    later = T + timedelta(minutes=5)
    before = record()
    changed = change_state(before, to, later)
    assert (changed.state, changed.closed_utc) == (to, later)
    # 상태와 닫은 때 말고는 그대로다(식별값 포함).
    assert replace(changed, state=OrderState.OPEN, closed_utc=None) == before


def test_open_and_unconfirmed_can_go_back_and_forth() -> None:
    unconfirmed = change_state(record(), OrderState.UNCONFIRMED, T, error_name=ERROR_TIMEOUT)
    assert (unconfirmed.state, unconfirmed.closed_utc, unconfirmed.error_name) == (
        OrderState.UNCONFIRMED,
        None,
        ERROR_TIMEOUT,
    )
    found = change_state(unconfirmed, OrderState.OPEN, T)
    assert (found.state, found.closed_utc, found.error_name) == (
        OrderState.OPEN,
        None,
        ERROR_TIMEOUT,
    )
    closed = change_state(unconfirmed, OrderState.NOT_SENT, T)
    assert closed.state is OrderState.NOT_SENT


@pytest.mark.parametrize(
    "done", [OrderState.FILLED, OrderState.CANCELED, OrderState.NOT_SENT, OrderState.FAILED]
)
def test_closed_orders_do_not_change(done: OrderState) -> None:
    assert STATE_CHANGES[done] == frozenset()
    for to in OrderState:
        assert not can_change(done, to)
    with pytest.raises(ValueError, match="바꿀 수 없다"):
        change_state(record(done), OrderState.OPEN, T)


def test_open_cannot_become_open_again() -> None:
    assert not can_change(OrderState.OPEN, OrderState.OPEN)
    assert can_change(OrderState.OPEN, OrderState.UNCONFIRMED)
    with pytest.raises(ValueError, match="시간대"):
        change_state(record(), OrderState.CANCELED, datetime(2026, 1, 5))


def test_unconfirmed_closes_after_600_seconds_of_missing() -> None:
    assert not unconfirmed_expired(T, T + timedelta(seconds=599))
    assert unconfirmed_expired(T, T + timedelta(seconds=600))


def test_cancel_confirm_waits_10_seconds() -> None:
    assert not cancel_wait_over(T, T + timedelta(seconds=9))
    assert cancel_wait_over(T, T + timedelta(seconds=10))
