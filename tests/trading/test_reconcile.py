"""대조(M5 명세 3.6.3, 3.6.5)와 외부 변동·A-6·A-7(3.6.6)의 셈의 시험.

3.6.6의 예를 넣는다. 값은 모두 만든 숫자다.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from coin_report_trader.core.alerts import Mode
from coin_report_trader.core.exchange import ExchangeAccount, ExchangeHolding, OrderSnapshot
from coin_report_trader.core.trade import (
    OrderKind,
    OrderReason,
    OrderRecord,
    OrderSide,
    OrderState,
)
from coin_report_trader.trading.live import (
    KRW_MARKET,
    ExternalChange,
    LiveSnapshot,
    canceled_direct_orders,
    direct_lock_started,
    direct_locked,
    direct_orders,
    find_external_changes,
    queried_state,
    snapshot_rows,
)

A = "KRW-AAA"
B = "KRW-BBB"
D = Decimal
T0 = datetime(2026, 1, 5, 3, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=1)


def snap(uid: str, *, market: str = A, side: OrderSide = OrderSide.BUY, state=OrderState.OPEN):
    return OrderSnapshot(
        uid=uid,
        market=market,
        side=side,
        kind=OrderKind.LIMIT,
        price=D(990),
        qty=D(1),
        executed_qty=D(0),
        state=state,
        trades=(),
        program=False,
    )


def record(uid: str, state: OrderState = OrderState.OPEN) -> OrderRecord:
    return OrderRecord(
        order_id=1,
        uid=uid,
        mode=Mode.LIVE,
        round_id=1,
        market=A,
        side=OrderSide.BUY,
        kind=OrderKind.LIMIT,
        reason=OrderReason.JUDGMENT,
        price=D(990),
        qty=D(1),
        state=state,
        created_utc=T0,
        closed_utc=None,
        error_name=None,
    )


def account(krw: str, *holdings: ExchangeHolding, krw_locked: str = "0") -> ExchangeAccount:
    return ExchangeAccount(krw=D(krw), krw_locked=D(krw_locked), holdings=holdings, read_utc=T1)


def held(market: str, balance: str, locked: str = "0") -> ExchangeHolding:
    return ExchangeHolding(market=market, balance=D(balance), locked=D(locked), avg_buy_price=D(1))


def before(market: str, balance: str, locked: str = "0", direct: str = "0") -> LiveSnapshot:
    return LiveSnapshot(
        market=market, balance=D(balance), locked=D(locked), direct_locked=D(direct), read_utc=T0
    )


# --- 대조(3.6.5) -------------------------------------------------------------------------------


def test_orders_on_the_list_without_our_identifier_are_direct() -> None:
    listed = (snap("ours-1"), snap("direct-uuid"), snap("ours-2"))
    assert [o.uid for o in direct_orders(listed, {"ours-1", "ours-2", "ours-closed"})] == [
        "direct-uuid"
    ]
    assert direct_orders((), {"ours-1"}) == ()


@pytest.mark.parametrize("state", [OrderState.OPEN, OrderState.FILLED, OrderState.CANCELED])
def test_a_found_order_takes_the_exchange_state(state: OrderState) -> None:
    order = record("uid-1", OrderState.UNCONFIRMED)
    assert queried_state(order, snap("uid-1", state=state), missing_since=None, now=T1) is state


def test_a_missing_order_is_unconfirmed_until_600_seconds() -> None:
    # 켤 때 sent 0인 줄도 바로 닫지 않고 이 차례다(3.6.3).
    order = record("uid-2")
    assert queried_state(order, None, missing_since=None, now=T1) is OrderState.UNCONFIRMED
    assert (
        queried_state(order, None, missing_since=T1, now=T1 + timedelta(seconds=599))
        is OrderState.UNCONFIRMED
    )
    assert (
        queried_state(order, None, missing_since=T1, now=T1 + timedelta(seconds=600))
        is OrderState.NOT_SENT
    )


def test_a_snapshot_of_another_order_is_refused() -> None:
    with pytest.raises(ValueError, match="식별값"):
        queried_state(record("uid-3"), snap("uid-other"), missing_since=None, now=T1)


# --- 외부 변동(3.6.6) --------------------------------------------------------------------------


def test_external_change_example_of_3_6_6() -> None:
    # 앞 3개, 프로그램 매수 체결 1개, 지금 6개면 2개가 외부 변동이다.
    changes = find_external_changes([before(A, "3")], account("0", held(A, "6")), [A], {A: D(1)})
    assert changes == (ExternalChange(market=A, qty_delta=D(2)),)


def test_no_change_when_only_the_program_moved_things() -> None:
    previous = [before(KRW_MARKET, "100000"), before(A, "3")]
    now = account("89995", held(A, "4", "6"), krw_locked="0")
    # 프로그램이 7개를 사고(원화 −10,005원) 그 가운데 6개를 매도로 걸어 묶었다.
    deltas = {A: D(7), KRW_MARKET: D(-10005)}
    assert find_external_changes(previous, now, [A], deltas) == ()


def test_krw_counts_available_plus_locked_and_rounds_to_one_won() -> None:
    previous = [before(KRW_MARKET, "100000", "5000")]
    # 원화 주문 가능 90,000 + 묶임 15,000.4 = 105,000.4. 0.4원은 1원 단위로 맞추면 0이다.
    small = account("90000", krw_locked="15000.4")
    assert find_external_changes(previous, small, [], {}) == ()
    big = account("90000", krw_locked="15002")
    assert find_external_changes(previous, big, [], {}) == (
        ExternalChange(market=KRW_MARKET, qty_delta=D(2)),
    )


def test_qty_is_compared_to_eight_places() -> None:
    previous = [before(A, "1")]
    tiny = account("0", held(A, "1.000000004"))
    assert find_external_changes(previous, tiny, [A], {}) == ()
    one = account("0", held(A, "1.00000001"))
    assert find_external_changes(previous, one, [A], {}) == (
        ExternalChange(market=A, qty_delta=D("0.00000001")),
    )


def test_a_coin_sold_off_outside_and_a_coin_without_a_baseline() -> None:
    previous = [before(A, "2", "1")]
    now = account("0", held(B, "5"))  # A는 모두 사라졌고, B는 앞의 값이 없다
    assert find_external_changes(previous, now, [A, B], {}) == (
        ExternalChange(market=A, qty_delta=D(-3)),
    )


def test_unchosen_coins_are_not_compared() -> None:
    previous = [before(A, "1"), before(B, "1")]
    now = account("0", held(A, "1"), held(B, "9"))
    assert find_external_changes(previous, now, [A], {}) == ()


# --- live_snapshots의 줄과 A-6 --------------------------------------------------------------


def test_snapshot_rows_have_krw_and_every_chosen_coin() -> None:
    now = account("1000", held(A, "2", "5"), krw_locked="300")
    rows = snapshot_rows(now, [A, B], {A: D(3)})
    assert rows == (
        LiveSnapshot(KRW_MARKET, D(1000), D(300), D(0), T1),
        LiveSnapshot(A, D(2), D(5), D(2), T1),  # 묶인 5개 가운데 프로그램 3개, 직접 2개
        LiveSnapshot(B, D(0), D(0), D(0), T1),
    )


def test_direct_locked_is_locked_minus_the_program_lock() -> None:
    assert direct_locked(held(A, "0", "5"), D(3)) == D(2)
    assert direct_locked(held(A, "0", "3"), D(3)) == D(0)
    assert direct_locked(held(A, "0", "3"), D(4)) == D(0)  # 0보다 작으면 0
    assert direct_locked(None, D(1)) == D(0)


@pytest.mark.parametrize(
    ("old", "new", "alert"),
    [("0", "2", True), ("2", "3", False), ("2", "0", False), ("0", "0", False)],
)
def test_a6_only_when_the_direct_lock_starts(old: str, new: str, alert: bool) -> None:
    assert direct_lock_started(D(old), D(new)) is alert


# --- A-7 ---------------------------------------------------------------------------------------


def test_a7_needs_the_direct_order_gone_and_an_opposite_program_fill() -> None:
    direct_buy = snap("direct-1", side=OrderSide.BUY)
    direct_sell = snap("direct-2", side=OrderSide.SELL)
    other_coin = snap("direct-3", market=B, side=OrderSide.BUY)
    previous = (direct_buy, direct_sell, other_coin)
    gone = canceled_direct_orders(previous, (direct_sell,), [(A, OrderSide.SELL)])
    # 직접 건 매수가 사라졌고 프로그램 매도가 체결되었다. B의 주문은 그 코인의 체결이 없다.
    assert gone == (direct_buy,)


def test_no_a7_without_the_opposite_fill_or_when_still_listed() -> None:
    direct_buy = snap("direct-1", side=OrderSide.BUY)
    assert canceled_direct_orders((direct_buy,), (), [(A, OrderSide.BUY)]) == ()
    assert canceled_direct_orders((direct_buy,), (), []) == ()
    assert canceled_direct_orders((direct_buy,), (direct_buy,), [(A, OrderSide.SELL)]) == ()
