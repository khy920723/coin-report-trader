"""대조(M5 명세 3.6.3, 3.6.5)와 외부 변동·A-6·A-7(3.6.6)의 셈의 시험.

3.6.6의 예를 넣는다. 값은 모두 만든 숫자다.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from coin_report_trader.core.alerts import Mode
from coin_report_trader.core.exchange import ExchangeAccount, ExchangeHolding, OrderSnapshot
from coin_report_trader.core.trade import (
    FillRecord,
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
    program_deltas,
    program_locked,
    queried_state,
    snapshot_rows,
)

A = "KRW-AAA"
B = "KRW-BBB"
D = Decimal
T0 = datetime(2026, 1, 5, 3, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=1)


def snap(
    uid: str,
    *,
    market: str = A,
    side: OrderSide = OrderSide.BUY,
    state=OrderState.OPEN,
    qty: str = "1",
    executed: str = "0",
    program: bool = False,
):
    return OrderSnapshot(
        uid=uid,
        market=market,
        side=side,
        kind=OrderKind.LIMIT,
        price=D(990),
        qty=D(qty),
        executed_qty=D(executed),
        state=state,
        trades=(),
        program=program,
    )


def fill(
    side: OrderSide, price: str, qty: str, fee: str, *, market: str = A, mode: Mode = Mode.LIVE
) -> FillRecord:
    return FillRecord(
        fill_id=1,
        order_id=1,
        mode=mode,
        market=market,
        side=side,
        price=D(price),
        qty=D(qty),
        fee=D(fee),
        realized_pnl=None,
        filled_utc=T0 + timedelta(seconds=30),
    )


BUY = OrderSide.BUY
SELL = OrderSide.SELL


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
    previous = [before(A, "3")]
    changes = find_external_changes(
        previous, account("0", held(A, "6")), [A], [fill(BUY, "1000", "1", "0.5")]
    )
    assert changes == (ExternalChange(market=A, qty_delta=D(2)),)


def test_program_deltas_from_buy_and_sell_fills_with_fees() -> None:
    fills = [
        fill(BUY, "1000", "3", "1.5"),  # 원화 −3,001.5, AAA +3
        fill(SELL, "1100", "2", "1.1"),  # 원화 +2,198.9, AAA −2
        fill(BUY, "200", "5", "0.5", market=B),  # 원화 −1,000.5, BBB +5
    ]
    assert program_deltas(fills) == {A: D(1), B: D(5), KRW_MARKET: D("-1803.1")}
    assert program_deltas([]) == {}


def test_no_change_when_only_the_program_moved_things() -> None:
    # (가) 앞 원화 100,000·AAA 3개. 프로그램이 AAA 7개를 1,000원에 사고(수수료 3.5원), 2개를
    # 1,100원에 팔고(수수료 1.1원), 남은 가운데 6개를 매도로 걸어 묶었다.
    previous = [before(KRW_MARKET, "100000"), before(A, "3")]
    fills = [fill(BUY, "1000", "7", "3.5"), fill(SELL, "1100", "2", "1.1")]
    # 원화: 100,000 − 7,003.5 + 2,198.9 = 95,195.4. AAA: 3 + 7 − 2 = 8(주문 가능 2, 묶임 6).
    now = account("95195.4", held(A, "2", "6"))
    assert find_external_changes(previous, now, [A], fills) == ()


def test_changes_beside_the_program_fills_are_external() -> None:
    # (나) (가)와 같은 체결에, 바깥에서 원화 50,000원이 들어오고 AAA 1개가 나갔다.
    previous = [before(KRW_MARKET, "100000"), before(A, "3")]
    fills = [fill(BUY, "1000", "7", "3.5"), fill(SELL, "1100", "2", "1.1")]
    now = account("145195.4", held(A, "1", "6"))
    assert find_external_changes(previous, now, [A], fills) == (
        ExternalChange(market=KRW_MARKET, qty_delta=D(50000)),
        ExternalChange(market=A, qty_delta=D(-1)),
    )


def test_krw_counts_available_plus_locked_and_rounds_to_one_won() -> None:
    previous = [before(KRW_MARKET, "100000", "5000")]
    # 원화 주문 가능 90,000 + 묶임 15,000.4 = 105,000.4. 0.4원은 1원 단위로 맞추면 0이다.
    small = account("90000", krw_locked="15000.4")
    assert find_external_changes(previous, small, [], []) == ()
    big = account("90000", krw_locked="15002")
    assert find_external_changes(previous, big, [], []) == (
        ExternalChange(market=KRW_MARKET, qty_delta=D(2)),
    )


def test_qty_is_compared_to_eight_places() -> None:
    previous = [before(A, "1")]
    tiny = account("0", held(A, "1.000000004"))
    assert find_external_changes(previous, tiny, [A], []) == ()
    one = account("0", held(A, "1.00000001"))
    assert find_external_changes(previous, one, [A], []) == (
        ExternalChange(market=A, qty_delta=D("0.00000001")),
    )


def test_a_coin_sold_off_outside_and_a_coin_without_a_baseline() -> None:
    previous = [before(A, "2", "1")]
    now = account("0", held(B, "5"))  # A는 모두 사라졌고, B는 앞의 값이 없다
    assert find_external_changes(previous, now, [A, B], []) == (
        ExternalChange(market=A, qty_delta=D(-3)),
    )


def test_unchosen_coins_are_not_compared() -> None:
    previous = [before(A, "1"), before(B, "1")]
    now = account("0", held(A, "1"), held(B, "9"))
    assert find_external_changes(previous, now, [A], []) == ()


def test_paper_fills_are_refused() -> None:
    paper = fill(BUY, "1000", "1", "0.5", mode=Mode.PAPER)
    with pytest.raises(ValueError, match="실거래"):
        program_deltas([paper])
    with pytest.raises(ValueError, match="실거래"):
        canceled_direct_orders((), (), [paper])


# --- live_snapshots의 줄과 A-6 --------------------------------------------------------------


def test_program_locked_is_the_rest_of_program_sell_orders() -> None:
    listed = (
        snap("ours-sell", side=SELL, qty="4", executed="1", program=True),  # 남은 3개
        snap("ours-sell-2", side=SELL, qty="2", program=True),  # 남은 2개
        snap("ours-buy", side=BUY, qty="9", program=True),  # 매수는 코인을 묶지 않음
        snap("direct-sell", side=SELL, qty="7"),  # 직접 건 주문
        snap("ours-b", market=B, side=SELL, qty="1", program=True),
    )
    assert program_locked(listed) == {A: D(5), B: D(1)}
    assert program_locked(()) == {}


def test_snapshot_rows_have_krw_and_every_chosen_coin() -> None:
    # (다) AAA 묶임 5개 가운데 프로그램 매도 4개 걸고 1개 체결(남은 3개), 직접 2개.
    now = account("1000", held(A, "2", "5"), krw_locked="300")
    listed = (
        snap("ours-sell", side=SELL, qty="4", executed="1", program=True),
        snap("direct-sell", side=SELL, qty="2"),
    )
    rows = snapshot_rows(now, [A, B], listed)
    assert rows == (
        LiveSnapshot(KRW_MARKET, D(1000), D(300), D(0), T1),
        LiveSnapshot(A, D(2), D(5), D(2), T1),
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
    # (라) 직접 건 AAA 매수가 사라졌고, 체결 목록에 AAA의 프로그램 매도가 있다.
    direct_buy = snap("direct-1", side=BUY)
    direct_sell = snap("direct-2", side=SELL)
    other_coin = snap("direct-3", market=B, side=BUY)
    previous = (direct_buy, direct_sell, other_coin)
    fills = [fill(SELL, "1000", "1", "0.5"), fill(BUY, "200", "1", "0.1", market=B)]
    gone = canceled_direct_orders(previous, (direct_sell,), fills)
    # B의 직접 건 매수는 사라졌지만 B의 프로그램 체결이 같은 편(매수)이라 알리지 않는다.
    assert gone == (direct_buy,)


def test_no_a7_without_the_opposite_fill_or_when_still_listed() -> None:
    direct_buy = snap("direct-1", side=BUY)
    same_side = [fill(BUY, "1000", "1", "0.5")]
    opposite = [fill(SELL, "1000", "1", "0.5")]
    other_coin = [fill(SELL, "1000", "1", "0.5", market=B)]
    assert canceled_direct_orders((direct_buy,), (), same_side) == ()
    assert canceled_direct_orders((direct_buy,), (), other_coin) == ()
    assert canceled_direct_orders((direct_buy,), (), []) == ()
    assert canceled_direct_orders((direct_buy,), (direct_buy,), opposite) == ()
