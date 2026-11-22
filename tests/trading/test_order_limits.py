"""한도(M3 명세 3.8.2)의 시험. 3.8.2의 예를 모두 넣는다. 값은 모두 만든 숫자다."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from coin_report_trader.core.alerts import Mode
from coin_report_trader.core.trade import (
    DAILY_CANCEL_LIMIT,
    NoOrderReason,
    OrderKind,
    OrderPlan,
    OrderReason,
    OrderRecord,
    OrderRules,
    OrderSide,
    OrderState,
    PaperAccount,
    PauseStopLoss,
    TradeMethod,
    TradeSettings,
)
from coin_report_trader.trading.orders import BuyContext, check_buy, check_cancel_cap
from coin_report_trader.trading.paper import make_holding

A = "KRW-AAA"
B = "KRW-BBB"
C = "KRW-CCC"
D = Decimal
NOW = datetime(2026, 1, 5, 3, 20, tzinfo=UTC)
RULES = OrderRules(tick_size=D(1), min_order=D(5000))


def settings(**changes: object) -> TradeSettings:
    base = TradeSettings(
        budget=D(100_000),
        coin_ratios={A: D(50), B: D(50)},
        first_ratio=D(20),
        mr_first_ratio=D(20),
        add_ratio=D(15),
        tolerance=D(0),
        stop_ratio=D(5),
        daily_loss_limit=D(3000),
        order_interval_s=60,
        method=TradeMethod.TREND,
        pause_stop_loss=PauseStopLoss.KEEP,
        mr_sell_unknown_fee=False,
        mr_tolerable_loss=D(5),
        paper_funds=D(100_000),
        backup_days=7,
    )
    return replace(base, **changes)


def plan(amount: str, *, side: OrderSide = OrderSide.BUY, market: str = A) -> OrderPlan:
    """가격 1,000에 금액만큼(수량 = 금액 ÷ 1,000)."""
    qty = D(amount) / D(1000)
    return OrderPlan(market, side, OrderKind.LIMIT, OrderReason.JUDGMENT, D(1000), qty, D(amount))


def order(market: str, amount: str, *, side: OrderSide = OrderSide.BUY, n: int = 1) -> OrderRecord:
    return OrderRecord(
        order_id=n,
        uid=f"uid-{n}",
        mode=Mode.PAPER,
        round_id=1,
        market=market,
        side=side,
        kind=OrderKind.LIMIT,
        reason=OrderReason.JUDGMENT,
        price=D(1000),
        qty=D(amount) / D(1000),
        state=OrderState.OPEN,
        created_utc=NOW,
        closed_utc=None,
        error_name=None,
    )


def ctx(**changes: object) -> BuyContext:
    base = BuyContext(
        account=PaperAccount(krw=D(100_000), locked_krw=D(0), holdings=()),
        open_orders=(),
        day_realized_pnl=D(0),
        caution=False,
        settings_check_failed=False,
        last_order_utc=None,
        unconfirmed=False,
    )
    return replace(base, **changes)


def holding_cost(market: str, cost: str):
    """평균 매수가 1,000으로 원가가 `cost`인 보유."""
    qty = D(cost) / D(1000)
    return make_holding(market, qty, D(0), D(cost), qty)


def check(p: OrderPlan, c: BuyContext, **changes: object) -> NoOrderReason | None:
    return check_buy(p, c, settings(**changes), RULES, NOW)


# --- 예(3.8.2) --------------------------------------------------------------------------------


@pytest.mark.parametrize(("amount", "expected"), [("10000", None), ("10001", "over")])
def test_coin_budget_example(amount: str, expected: str | None) -> None:
    # 코인 예산 50,000, 보유 원가 40,000, 이번 10,000이면 50,000이라 넘지 않고, 10,001이면 넘는다.
    account = PaperAccount(krw=D(100_000), locked_krw=D(0), holdings=(holding_cost(A, "40000"),))
    result = check(plan(amount), ctx(account=account))
    assert result is (None if expected is None else NoOrderReason.COIN_BUDGET)


@pytest.mark.parametrize(
    ("losses", "reached"), [(("-1200", "-1900"), True), (("-1200", "-1700"), False)]
)
def test_daily_loss_limit_example(losses: tuple[str, ...], reached: bool) -> None:
    # 한도 3,000원, 그날 −1,200원과 −1,900원이면 합 −3,100원이라 닿았다. −2,900원이면 아니다.
    total = sum((D(x) for x in losses), D(0))
    result = check(plan("10000"), ctx(day_realized_pnl=total))
    assert (result is NoOrderReason.DAILY_LOSS_LIMIT) is reached


def test_daily_loss_limit_is_reached_at_exactly_minus_the_limit() -> None:
    assert check(plan("10000"), ctx(day_realized_pnl=D(-3000))) is NoOrderReason.DAILY_LOSS_LIMIT
    assert check(plan("10000"), ctx(day_realized_pnl=D("-2999.99"))) is None


# --- 하나씩 ------------------------------------------------------------------------------------


def test_nothing_blocks_a_plain_buy() -> None:
    assert check(plan("10000"), ctx()) is None


def test_caution_and_settings_check() -> None:
    assert check(plan("10000"), ctx(caution=True)) is NoOrderReason.CAUTION
    assert check(plan("10000"), ctx(settings_check_failed=True)) is NoOrderReason.SETTINGS_CHECK


def test_order_interval() -> None:
    # 마지막 주문 + 60초 > 지금이면 걸린다.
    assert check(plan("10000"), ctx(last_order_utc=NOW - timedelta(seconds=59))) is (
        NoOrderReason.ORDER_INTERVAL
    )
    assert check(plan("10000"), ctx(last_order_utc=NOW - timedelta(seconds=60))) is None
    old = ctx(last_order_utc=NOW - timedelta(days=3))
    assert check(plan("10000"), old, order_interval_s=3600) is None


def test_coin_budget_counts_open_buy_orders_but_not_sells_or_other_coins() -> None:
    orders = (
        order(A, "40000", n=1),
        order(A, "30000", side=OrderSide.SELL, n=2),
        order(B, "30000", n=3),
    )
    c = ctx(open_orders=orders)
    assert check(plan("10000"), c) is None
    assert check(plan("10001"), c) is NoOrderReason.COIN_BUDGET


def test_closed_orders_are_not_counted() -> None:
    done = replace(order(A, "45000"), state=OrderState.CANCELED)
    assert check(plan("10000"), ctx(open_orders=(done,))) is None


def test_coin_budget_uses_average_price_times_qty() -> None:
    # 20개를 평균 1,000에 사고 10개를 판 보유: 원가는 1,000 × 10 = 10,000(매수 합 20,000이 아님).
    sold = make_holding(A, D(10), D(0), D(20_000), D(20))
    account = PaperAccount(krw=D(100_000), locked_krw=D(0), holdings=(sold,))
    assert check(plan("40000"), ctx(account=account)) is None
    assert check(plan("40001"), ctx(account=account)) is NoOrderReason.COIN_BUDGET


def test_budget_is_the_sum_over_the_chosen_coins() -> None:
    # 코인 예산은 각 60,000이지만 예산 100,000을 넘으면 걸린다. 고르지 않은 코인(CCC)은 세지 않는다.
    account = PaperAccount(
        krw=D(200_000),
        locked_krw=D(0),
        holdings=(holding_cost(B, "50000"), holding_cost(C, "90000")),
    )
    c = ctx(account=account, open_orders=(order(B, "10000"),))
    ratios = {A: D(60), B: D(60)}
    assert check(plan("40000"), c, budget=D(100_000), coin_ratios=ratios) is None
    assert check(plan("40001"), c, budget=D(100_000), coin_ratios=ratios) is NoOrderReason.BUDGET


def test_not_enough_krw_counts_the_fee_and_locks() -> None:
    # 10,000원 × 1.0005 = 10,005원이 필요하다.
    rich = PaperAccount(krw=D(30_005), locked_krw=D(20_000), holdings=())
    poor = replace(rich, krw=D("30004.99"))
    assert check(plan("10000"), ctx(account=rich)) is None
    assert check(plan("10000"), ctx(account=poor)) is NoOrderReason.NO_KRW


def test_unconfirmed_orders_block_new_buys() -> None:
    assert check(plan("10000"), ctx(unconfirmed=True)) is NoOrderReason.UNCONFIRMED_ORDER


def test_the_first_hit_in_table_order_is_returned() -> None:
    poor = PaperAccount(krw=D(0), locked_krw=D(0), holdings=())
    everything = ctx(
        account=poor,
        day_realized_pnl=D(-5000),
        caution=True,
        settings_check_failed=True,
        last_order_utc=NOW,
        unconfirmed=True,
    )
    big = plan("60000")
    assert check(big, everything) is NoOrderReason.DAILY_LOSS_LIMIT
    rest = replace(everything, day_realized_pnl=D(0))
    assert check(big, rest) is NoOrderReason.CAUTION
    rest = replace(rest, caution=False)
    assert check(big, rest) is NoOrderReason.SETTINGS_CHECK
    rest = replace(rest, settings_check_failed=False)
    assert check(big, rest) is NoOrderReason.ORDER_INTERVAL
    rest = replace(rest, last_order_utc=None)
    assert check(big, rest) is NoOrderReason.COIN_BUDGET
    with_b = replace(rest, account=replace(poor, holdings=(holding_cost(B, "50000"),)))
    ratios = {A: D(60), B: D(60)}
    assert check(big, with_b, coin_ratios=ratios) is NoOrderReason.BUDGET
    assert check(plan("10000"), rest) is NoOrderReason.NO_KRW
    rest = replace(rest, account=PaperAccount(krw=D(100_000), locked_krw=D(0), holdings=()))
    assert check(plan("10000"), rest) is NoOrderReason.UNCONFIRMED_ORDER


def test_sells_are_never_blocked() -> None:
    everything = ctx(day_realized_pnl=D(-5000), caution=True, unconfirmed=True)
    assert check(plan("90000", side=OrderSide.SELL), everything) is None


def test_market_buy_is_refused() -> None:
    market_buy = replace(plan("10000"), kind=OrderKind.MARKET, price=None)
    with pytest.raises(ValueError, match="지정가"):
        check(market_buy, ctx())


# --- 걸었다 지우기의 상한(5.7절) ---------------------------------------------------------------


def test_cancel_cap_is_reached_at_48() -> None:
    # 30분봉의 예: 마지막 회차(T 08:30)의 정리 때 그날 만든 주문 가운데 취소된 것은 47건이라
    # 걸린다. 48건이면 그날은 새로 걸지 않는다.
    assert DAILY_CANCEL_LIMIT == 48
    assert check_cancel_cap(0) is None
    assert check_cancel_cap(47) is None
    assert check_cancel_cap(48) is NoOrderReason.DAILY_CANCEL_CAP
    assert check_cancel_cap(60) is NoOrderReason.DAILY_CANCEL_CAP
    with pytest.raises(ValueError, match="취소된 수"):
        check_cancel_cap(-1)
