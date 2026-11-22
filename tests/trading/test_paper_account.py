"""가상 계정의 셈(M3 명세 3.7.1)과 수수료와 손익(3.7.3)의 시험.

값은 모두 만든 숫자다. 3.7.1과 3.7.3의 예를 모두 넣는다.
"""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from coin_report_trader.core.alerts import Mode
from coin_report_trader.core.trade import (
    PAPER_FEE_RATE,
    OrderKind,
    OrderReason,
    OrderRecord,
    OrderSide,
    OrderState,
    PaperAccount,
)
from coin_report_trader.trading.paper import (
    Fill,
    apply_fill,
    available_krw,
    buy_lock_amount,
    fee_of,
    fill_on_place,
    holding_of,
    is_dust,
    lock_order,
    make_holding,
    new_account,
    sellable_qty,
    unlock_order,
)

M = "KRW-AAA"
N = "KRW-BBB"
T = datetime(2026, 1, 5, 3, 20, tzinfo=UTC)
F = PAPER_FEE_RATE
D = Decimal


def order(
    side: OrderSide,
    qty: str,
    price: str | None,
    *,
    kind: OrderKind = OrderKind.LIMIT,
    order_id: int = 1,
    market: str = M,
) -> OrderRecord:
    return OrderRecord(
        order_id=order_id,
        uid=f"uid-{order_id}",
        mode=Mode.PAPER,
        round_id=None,
        market=market,
        side=side,
        kind=kind,
        reason=OrderReason.JUDGMENT if kind is OrderKind.LIMIT else OrderReason.STOP_LOSS,
        price=None if price is None else D(price),
        qty=D(qty),
        state=OrderState.OPEN,
        created_utc=T,
        closed_utc=None,
        error_name=None,
    )


def later_fill(o: OrderRecord) -> Fill:
    """걸린 뒤의 체결(지정가에 체결)."""
    assert o.price is not None
    return Fill(o.order_id, o.market, o.side, o.price, o.qty, T, at_place=False)


def place_fill(o: OrderRecord, price: str) -> Fill:
    """넣을 때의 바로 체결."""
    return Fill(o.order_id, o.market, o.side, D(price), o.qty, T, at_place=True)


def buy_now(account: PaperAccount, qty: str, price: str, order_id: int) -> PaperAccount:
    o = order(OrderSide.BUY, qty, price, order_id=order_id)
    return apply_fill(account, o, place_fill(o, price)).account


def sell_now(account: PaperAccount, qty: str, price: str, order_id: int):
    o = order(OrderSide.SELL, qty, price, order_id=order_id)
    return apply_fill(account, o, place_fill(o, price))


# --- 처음 계정과 보유 --------------------------------------------------------------------------


def test_new_account_is_the_funds_with_nothing_held() -> None:
    account = new_account(D(100_000))
    assert account == PaperAccount(krw=D(100_000), locked_krw=D(0), holdings=())
    assert available_krw(account) == D(100_000)
    assert holding_of(account, M) is None
    with pytest.raises(ValueError, match="가상 자금"):
        new_account(D(0))


def test_make_holding_computes_the_average_price() -> None:
    holding = make_holding(M, D(20), D(5), D(2100), D(20))
    assert holding.avg_price == D(105)
    assert make_holding(M, D(0), D(0), D(0), D(0)).avg_price is None
    with pytest.raises(ValueError, match="묶인 수량"):
        make_holding(M, D(1), D(2), D(100), D(1))
    with pytest.raises(ValueError, match="매수 합"):
        make_holding(M, D(1), D(0), D(-1), D(1))


def test_average_price_example_of_3_7_1() -> None:
    # 10개를 100에, 10개를 110에 사면 105다.
    account = new_account(D(100_000))
    account = buy_now(account, "10", "100", 1)
    account = buy_now(account, "10", "110", 2)
    holding = holding_of(account, M)
    assert holding is not None
    assert holding.avg_price == D(105)
    assert (holding.buy_amount_sum, holding.buy_qty_sum) == (D(2100), D(20))
    # 5개를 팔아도 105다.
    account = sell_now(account, "5", "120", 3).account
    holding = holding_of(account, M)
    assert holding is not None
    assert (holding.qty, holding.avg_price) == (D(15), D(105))
    # 남은 15개를 다 판 뒤에는 두 합이 0이고 평균 매수가가 없다.
    settled = sell_now(account, "15", "100", 4)
    assert settled.holding == make_holding(M, D(0), D(0), D(0), D(0))
    assert settled.holding.avg_price is None
    # 그 뒤 10개를 90에 사면 90이다.
    account = buy_now(settled.account, "10", "90", 5)
    holding = holding_of(account, M)
    assert holding is not None
    assert (holding.avg_price, holding.buy_amount_sum, holding.buy_qty_sum) == (
        D(90),
        D(900),
        D(10),
    )


def test_holding_row_of_zero_is_kept_and_reused() -> None:
    account = buy_now(new_account(D(100_000)), "1", "1000", 1)
    account = sell_now(account, "1", "1000", 2).account
    assert len(account.holdings) == 1
    assert account.holdings[0].qty == 0
    account = buy_now(account, "2", "500", 3)
    assert len(account.holdings) == 1
    assert account.holdings[0].avg_price == D(500)


def test_other_coins_are_kept_in_their_order() -> None:
    account = buy_now(new_account(D(100_000)), "1", "100", 1)
    o = order(OrderSide.BUY, "2", "50", order_id=2, market=N)
    account = apply_fill(account, o, place_fill(o, "50")).account
    account = buy_now(account, "1", "100", 3)
    assert [h.market for h in account.holdings] == [M, N]
    holding_n = holding_of(account, N)
    assert holding_n is not None
    assert holding_n.qty == D(2)


# --- 팔 수 있는 수량과 팔 수 없는 잔량 ----------------------------------------------------------


def test_sellable_qty_is_qty_minus_locked() -> None:
    assert sellable_qty(None) == 0
    assert sellable_qty(make_holding(M, D(10), D(3), D(1000), D(10))) == D(7)


@pytest.mark.parametrize(
    ("qty", "price", "expected"),
    [
        ("4", "1249", True),  # 4,996원 < 5,000원
        ("4", "1250", False),  # 5,000원은 작지 않다
        ("0", "1000", False),  # 보유가 없음
    ],
)
def test_dust_is_a_holding_worth_less_than_the_min_order(
    qty: str, price: str, expected: bool
) -> None:
    holding = make_holding(M, D(qty), D(0), D(qty) * D(1000), D(qty))
    assert is_dust(holding, D(price), D(5000)) is expected


def test_no_holding_is_not_dust() -> None:
    assert is_dust(None, D(1), D(5000)) is False


def test_dust_counts_the_whole_holding_not_only_the_sellable_qty() -> None:
    holding = make_holding(M, D(10), D(8), D(10_000), D(10))
    assert is_dust(holding, D(1000), D(5000)) is False


# --- 묶음 -------------------------------------------------------------------------------------


def test_lock_and_unlock_of_a_limit_buy() -> None:
    account = new_account(D(100_000))
    o = order(OrderSide.BUY, "10", "990")
    locked = lock_order(account, o)
    # 990 × 10 × 1.0005 = 9,904.95
    assert buy_lock_amount(D(990), D(10)) == D("9904.95")
    assert locked.locked_krw == D("9904.95")
    assert locked.krw == D(100_000)
    assert available_krw(locked) == D("90095.05")
    assert unlock_order(locked, o) == account


def test_lock_and_unlock_of_a_limit_sell() -> None:
    account = buy_now(new_account(D(100_000)), "10", "100", 1)
    o = order(OrderSide.SELL, "4", "120", order_id=2)
    locked = lock_order(account, o)
    holding = holding_of(locked, M)
    assert holding is not None
    assert (holding.qty, holding.locked_qty, sellable_qty(holding)) == (D(10), D(4), D(6))
    assert locked.krw == account.krw
    assert unlock_order(locked, o) == account


def test_lock_refuses_more_than_available() -> None:
    account = new_account(D(1000))
    with pytest.raises(ValueError, match="원화가 모자란다"):
        lock_order(account, order(OrderSide.BUY, "1", "1000"))  # 1,000.5원이 필요하다
    held = buy_now(new_account(D(100_000)), "2", "100", 1)
    with pytest.raises(ValueError, match="팔 수 있는 수량"):
        lock_order(held, order(OrderSide.SELL, "3", "100", order_id=2))
    with pytest.raises(ValueError, match="팔 수 있는 수량"):
        lock_order(new_account(D(1000)), order(OrderSide.SELL, "1", "100"))


def test_unlock_refuses_more_than_locked() -> None:
    with pytest.raises(ValueError, match="묶인 원화"):
        unlock_order(new_account(D(100_000)), order(OrderSide.BUY, "1", "100"))
    held = buy_now(new_account(D(100_000)), "2", "100", 1)
    with pytest.raises(ValueError, match="묶인 수량"):
        unlock_order(held, order(OrderSide.SELL, "1", "100", order_id=2))


def test_market_orders_are_not_locked() -> None:
    account = buy_now(new_account(D(100_000)), "2", "100", 1)
    with pytest.raises(ValueError, match="지정가"):
        lock_order(account, order(OrderSide.SELL, "1", None, kind=OrderKind.MARKET))


# --- 체결을 계정에 넣기, 수수료와 손익 ---------------------------------------------------------


def test_buy_fill_takes_price_times_qty_times_one_plus_f() -> None:
    account = new_account(D(100_000))
    o = order(OrderSide.BUY, "10", "1000")
    settled = apply_fill(account, o, place_fill(o, "990"))
    # 바로 체결은 최우선 매도 호가 990에: 990 × 10 × 1.0005 = 9,904.95를 뺀다.
    assert settled.account.krw == D("90095.05")
    assert settled.account.locked_krw == 0
    assert settled.fee == D("4.95")  # 990 × 10 × 0.0005
    assert settled.realized_pnl is None
    assert settled.holding == make_holding(M, D(10), D(0), D(9900), D(10))


def test_buy_fill_after_waiting_releases_its_lock() -> None:
    account = new_account(D(100_000))
    o = order(OrderSide.BUY, "10", "990")
    locked = lock_order(account, o)
    settled = apply_fill(locked, o, later_fill(o))
    assert settled.account.locked_krw == 0
    assert settled.account.krw == D("90095.05")
    assert available_krw(settled.account) == D("90095.05")


def test_sell_example_of_3_7_3() -> None:
    # 평균 매수가 100, 10개를 102에 팔면 10 × (101.949 − 100.05) = 18.99원이다.
    account = buy_now(new_account(D(100_000)), "10", "100", 1)
    krw_before = account.krw
    o = order(OrderSide.SELL, "10", "102", order_id=2)
    settled = apply_fill(lock_order(account, o), o, later_fill(o))
    assert settled.realized_pnl == D("18.99")
    assert settled.fee == D("0.51")  # 102 × 10 × 0.0005
    # 가상 원화에 102 × 10 × 0.9995 = 1,019.49를 더한다.
    assert settled.account.krw - krw_before == D("1019.49")
    assert settled.holding.qty == 0
    assert settled.holding.locked_qty == 0


def test_partial_sell_keeps_other_locks_and_sums() -> None:
    account = buy_now(new_account(D(100_000)), "10", "100", 1)
    waiting = order(OrderSide.SELL, "3", "130", order_id=2)
    account = lock_order(account, waiting)
    market_sell = order(OrderSide.SELL, "7", None, kind=OrderKind.MARKET, order_id=3)
    fill = fill_on_place(market_sell, D(99), D(98), T)
    assert fill is not None
    settled = apply_fill(account, market_sell, fill)
    assert settled.holding == make_holding(M, D(3), D(3), D(1000), D(10))
    # 7 × (98 × 0.9995 − 100 × 1.0005) = 7 × (97.951 − 100.05)
    assert settled.realized_pnl == D("-14.693")


def test_a_market_sell_cannot_take_the_locked_qty() -> None:
    account = buy_now(new_account(D(100_000)), "10", "100", 1)
    account = lock_order(account, order(OrderSide.SELL, "4", "130", order_id=2))
    market_sell = order(OrderSide.SELL, "7", None, kind=OrderKind.MARKET, order_id=3)
    fill = fill_on_place(market_sell, D(99), D(98), T)
    assert fill is not None
    with pytest.raises(ValueError, match="팔 수 있는 수량"):
        apply_fill(account, market_sell, fill)


def test_fee_of_is_the_fill_amount_times_f() -> None:
    assert fee_of(D(1000), D("0.5")) == D("0.25")
    assert fee_of(D(1000), D(1), D("0.001")) == D(1)


def test_apply_fill_refuses_wrong_input() -> None:
    account = new_account(D(1000))
    o = order(OrderSide.BUY, "1", "1000")
    with pytest.raises(ValueError, match="원화가 모자란다"):
        apply_fill(account, o, place_fill(o, "1000"))
    with pytest.raises(ValueError, match="그 주문의 것"):
        apply_fill(account, o, replace(place_fill(o, "1"), order_id=9))
    with pytest.raises(ValueError, match="전부 체결"):
        apply_fill(account, o, replace(place_fill(o, "1"), qty=D("0.5")))
    with pytest.raises(ValueError, match="걸린 주문"):
        apply_fill(account, replace(o, state=OrderState.FILLED), place_fill(o, "1"))
    with pytest.raises(ValueError, match="팔 수 있는 수량"):
        sell_now(account, "1", "100", 2)


def test_buy_after_zero_uses_only_new_buys_even_if_old_sums_were_left() -> None:
    # 보유가 0이 된 뒤의 매수만으로 평균 매수가를 셈한다. 저장된 줄에 옛 합이 남아 있어도 같다.
    stale = make_holding(M, D(0), D(0), D(1100), D(10))
    account = PaperAccount(krw=D(100_000), locked_krw=D(0), holdings=(stale,))
    account = buy_now(account, "10", "90", 1)
    holding = holding_of(account, M)
    assert holding is not None
    assert (holding.avg_price, holding.buy_amount_sum, holding.buy_qty_sum) == (
        D(90),
        D(900),
        D(10),
    )
