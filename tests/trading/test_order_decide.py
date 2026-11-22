"""판단표(M3 명세 3.5.2)의 시험. 3.5.2와 3.8.5의 예를 모두 넣는다. 값은 모두 만든 숫자다."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from coin_report_trader.core.ai import Direction, ReportState
from coin_report_trader.core.trade import (
    PAPER_FEE_RATE,
    NoOrderReason,
    OrderKind,
    OrderPlan,
    OrderReason,
    OrderRules,
    OrderSide,
    PauseStopLoss,
    TradeMethod,
    TradeSettings,
)
from coin_report_trader.core.views import ReportView
from coin_report_trader.trading.orders import Decision, decide
from coin_report_trader.trading.paper import make_holding

A = "KRW-AAA"
D = Decimal
UP = Direction.UP
DOWN = Direction.DOWN
TREND = TradeMethod.TREND
MR = TradeMethod.MEAN_REVERSION
RULES = OrderRules(tick_size=D(1), min_order=D(5000))


def settings(**changes: object) -> TradeSettings:
    base = TradeSettings(
        budget=D(100_000),
        coin_ratios={A: D(50), "KRW-BBB": D(50)},
        first_ratio=D(20),
        mr_first_ratio=D(30),
        add_ratio=D(15),
        tolerance=D(0),
        stop_ratio=D(5),
        daily_loss_limit=D(3000),
        order_interval_s=60,
        method=TREND,
        pause_stop_loss=PauseStopLoss.KEEP,
        mr_sell_unknown_fee=False,
        mr_tolerable_loss=D(5),
        paper_funds=D(100_000),
        backup_days=7,
    )
    return replace(base, **changes)


def report(
    *,
    buy: str | None = "990",
    sell: str | None = "1010",
    predicted: str | None = "1005",
    state: ReportState = ReportState.OK,
) -> ReportView:
    return ReportView(
        report_id=1,
        market=A,
        round_number=4,
        close_utc=datetime(2026, 1, 5, 3, 0, tzinfo=UTC),
        state=state,
        failure=None,
        base_price=D(1000),
        search_judgment=None,
        candle_judgment=None,
        direction=UP,
        predicted_price=None if predicted is None else D(predicted),
        predicted_reason=None,
        buy_price=None if buy is None else D(buy),
        sell_price=None if sell is None else D(sell),
        result_text=None,
        last_result=None,
        compared=(),
        checklist=(),
        evidence=(),
        sources=(),
        tokens=None,
        elapsed_report_seconds=None,
    )


def held(qty: str = "20", avg: str = "1000", locked: str = "0"):
    return make_holding(A, D(qty), D(locked), D(qty) * D(avg), D(qty))


def run(
    method: TradeMethod,
    direction: Direction,
    holding=None,
    *,
    price: str = "1000",
    rep: ReportView | None = None,
    fee: Decimal | None = PAPER_FEE_RATE,
    rules: OrderRules = RULES,
    **changes: object,
) -> Decision:
    return decide(
        method,
        direction,
        holding,
        D(price),
        rep or report(),
        settings(method=method, **changes),
        fee,
        rules,
    )


def limit(side: OrderSide, price: str, qty: str) -> OrderPlan:
    return OrderPlan(
        market=A,
        side=side,
        kind=OrderKind.LIMIT,
        reason=OrderReason.JUDGMENT,
        price=D(price),
        qty=D(qty),
        amount=D(price) * D(qty),
    )


# --- 판단표의 줄 --------------------------------------------------------------------------------


def test_trend_no_holding_up_buys_at_the_buy_proposal_with_the_first_ratio() -> None:
    # 코인 예산 50,000 × 첫 매수 20% = 10,000원을 990에: 10.10101010개.
    decision = run(TREND, UP)
    assert decision == Decision(limit(OrderSide.BUY, "990", "10.10101010"), None, None)


def test_trend_no_holding_down_does_nothing() -> None:
    assert run(TREND, DOWN) == Decision(None, NoOrderReason.NOTHING, None)


def test_trend_holding_up_adds_only_above_the_average() -> None:
    # 추가 한 번: 50,000 × 15% = 7,500원을 990에.
    decision = run(TREND, UP, held(avg="999"))
    assert decision.plan == limit(OrderSide.BUY, "990", "7.57575757")
    assert run(TREND, UP, held(avg="1000")).reason is NoOrderReason.PRICE_NOT_ABOVE_AVG
    assert run(TREND, UP, held(avg="1001")).reason is NoOrderReason.PRICE_NOT_ABOVE_AVG


def test_trend_holding_down_sells_all_sellable_at_the_sell_proposal() -> None:
    decision = run(TREND, DOWN, held(qty="20", locked="3"), price="1000")
    assert decision == Decision(limit(OrderSide.SELL, "1010", "17"), None, None)


def test_mean_reversion_no_holding_up_does_nothing() -> None:
    assert run(MR, UP) == Decision(None, NoOrderReason.NOTHING, None)


def test_mean_reversion_no_holding_down_buys_at_the_prediction() -> None:
    # 50,000 × 평균회귀 첫 매수 30% = 15,000원을 예측 가격 995에.
    decision = run(MR, DOWN, rep=report(predicted="995"))
    assert decision.plan == limit(OrderSide.BUY, "995", "15.07537688")


def test_mean_reversion_holding_down_adds_even_at_a_loss() -> None:
    decision = run(MR, DOWN, held(avg="1100"), rep=report(predicted="995"))
    assert decision.plan == limit(OrderSide.BUY, "995", "7.53768844")


def test_mean_reversion_holding_up_sells_at_the_prediction() -> None:
    decision = run(MR, UP, held(qty="20", avg="1000"), rep=report(predicted="1005"))
    assert decision.plan == limit(OrderSide.SELL, "1005", "20")


def test_dust_only_is_still_holding() -> None:
    # 팔 수 없는 잔량만 있어도 보유 중이다: 추세추종 '오를 것'은 추가 매수다.
    decision = run(TREND, UP, held(qty="0.001", avg="900"))
    assert decision.plan is not None
    assert decision.plan.qty == D("7.57575757")


# --- 평균회귀의 매도(3.5.2의 예) ----------------------------------------------------------------


@pytest.mark.parametrize(("p", "sells"), [("100.1", False), ("100.2", True)])
def test_mean_reversion_sell_needs_to_beat_average_and_fee(p: str, sells: bool) -> None:
    # 평균 매수가 100, f 0.0005. 기준은 100 × 1.0005 ÷ 0.9995 = 100.10005…다.
    rules = OrderRules(tick_size=D("0.1"), min_order=D(100))
    decision = run(
        MR, UP, held(qty="10", avg="100"), price="100", rep=report(predicted=p), rules=rules
    )
    if sells:
        assert decision.plan == limit(OrderSide.SELL, p, "10")
    else:
        assert decision == Decision(None, NoOrderReason.NOT_OVER_AVG_AND_FEE, None)


@pytest.mark.parametrize(("p", "sells"), [("96", True), ("95", True), ("94", False)])
def test_mean_reversion_sell_with_unknown_fee_uses_the_tolerable_loss(p: str, sells: bool) -> None:
    # 평균 매수가 100, 감내할 손해 5%: P 96과 95는 팔고, 94는 팔지 않는다.
    rules = OrderRules(tick_size=D(1), min_order=D(100))
    decision = run(
        MR,
        UP,
        held(qty="10", avg="100"),
        price="96",
        rep=report(predicted=p),
        fee=None,
        rules=rules,
        mr_sell_unknown_fee=True,
    )
    if sells:
        assert decision.plan == limit(OrderSide.SELL, p, "10")
    else:
        assert decision.reason is NoOrderReason.NOT_OVER_AVG_AND_FEE


def test_mean_reversion_sell_with_unknown_fee_and_no_does_not_sell() -> None:
    decision = run(
        MR, UP, held(qty="10", avg="100"), price="100", rep=report(predicted="120"), fee=None
    )
    assert decision == Decision(None, NoOrderReason.FEE_UNKNOWN, None)


def test_mean_reversion_sell_is_judged_with_the_widened_price() -> None:
    # 예측 가격 100.2는 넘지만, 허용 범위 0.1%로 낮춘 100.0998을 올린 100.1은 넘지 않는다.
    rules = OrderRules(tick_size=D("0.1"), min_order=D(100))
    decision = run(
        MR,
        UP,
        held(qty="10", avg="100"),
        price="100",
        rep=report(predicted="100.2"),
        rules=rules,
        tolerance=D("0.1"),
    )
    assert decision.reason is NoOrderReason.NOT_OVER_AVG_AND_FEE


# --- 평균 매수가 없음(A-9) ----------------------------------------------------------------------


@pytest.mark.parametrize(("method", "direction"), [(TREND, UP), (MR, UP), (MR, DOWN)])
def test_no_average_price_gives_a9(method: TradeMethod, direction: Direction) -> None:
    holding = make_holding(A, D(10), D(0), D(0), D(0))  # 평균 매수가가 없다
    assert holding.avg_price is None
    assert run(method, direction, holding) == Decision(None, NoOrderReason.NO_AVG_PRICE, "A-9")


def test_trend_sell_does_not_need_an_average_price() -> None:
    holding = make_holding(A, D(10), D(0), D(0), D(0))
    assert run(TREND, DOWN, holding).plan == limit(OrderSide.SELL, "1010", "10")


# --- 허용 범위와 가격 안전 검사 -------------------------------------------------------------------


def test_widened_buy_with_the_tolerance_example() -> None:
    # 현재가 1,000, 매수 제안가 1,010: t 1%면 1,020이라 된다. t 2%면 1,030이라 A-4다.
    one = run(TREND, UP, rep=report(buy="1010"), tolerance=D(1))
    assert one.plan is not None
    assert one.plan.price == D(1020)
    assert one.plan.qty == D("9.80392156")  # 10,000원 ÷ 넓힌 가격 1,020
    two = run(TREND, UP, rep=report(buy="1010"), tolerance=D(2))
    assert two == Decision(None, NoOrderReason.PRICE_GUARD, "A-4")


def test_widened_sell_rounds_up_and_the_guard_uses_it() -> None:
    sell = run(TREND, DOWN, held(), rep=report(sell="1000"), tolerance=D("1.25"))
    assert sell.plan == limit(OrderSide.SELL, "988", "20")
    low = run(TREND, DOWN, held(), rep=report(sell="979"))
    assert low == Decision(None, NoOrderReason.PRICE_GUARD, "A-4")


def test_price_guard_uses_the_current_price_read_for_the_order() -> None:
    # 매수 제안가 990이 현재가 1,500의 절반(750) 밖이 아니므로 되지만, 현재가 2,000이면 걸린다.
    assert run(TREND, UP, price="1500").plan is not None
    assert run(TREND, UP, price="2000").reason is NoOrderReason.PRICE_GUARD


# --- 최소 주문 금액과 팔 수 있는 수량 -------------------------------------------------------------


def test_buy_below_the_min_order_is_not_placed_without_an_alert() -> None:
    rules = OrderRules(tick_size=D(1), min_order=D(10_001))
    assert run(TREND, UP, rules=rules) == Decision(None, NoOrderReason.BELOW_MIN_ORDER, None)


def test_sell_below_the_min_order_gives_a3() -> None:
    decision = run(TREND, DOWN, held(qty="4", avg="900"), rep=report(sell="1010"))
    assert decision == Decision(None, NoOrderReason.BELOW_MIN_ORDER, "A-3")  # 4,040원


def test_no_sellable_qty() -> None:
    decision = run(TREND, DOWN, held(qty="10", locked="10"))
    assert decision == Decision(None, NoOrderReason.NO_SELLABLE_QTY, None)


# --- 판단을 받지 못함 ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", [ReportState.FAILED, ReportState.SKIPPED, ReportState.ERROR])
def test_reports_that_are_not_ok_give_no_judgment(state: ReportState) -> None:
    decision = run(TREND, UP, rep=report(state=state))
    assert decision == Decision(None, NoOrderReason.NO_JUDGMENT, None)


def test_a_missing_price_in_the_report_gives_no_judgment() -> None:
    assert run(TREND, UP, rep=report(buy=None)).reason is NoOrderReason.NO_JUDGMENT
    assert run(MR, DOWN, rep=report(predicted=None)).reason is NoOrderReason.NO_JUDGMENT


def test_wrong_input_is_refused() -> None:
    with pytest.raises(ValueError, match="현재가"):
        run(TREND, UP, price="0")
    with pytest.raises(ValueError, match="첫 매수 비율"):
        run(TREND, UP, first_ratio=None)
    with pytest.raises(ValueError, match="투자 비율"):
        run(TREND, UP, coin_ratios={"KRW-BBB": D(100)})
    with pytest.raises(ValueError, match="꼭 하나"):
        Decision(None, None, None)


def test_mean_reversion_sell_at_exactly_average_and_fee_does_not_sell() -> None:
    # P × (1 − f) = 평균 매수가 × (1 + f)이면 "넘지" 않으므로 팔지 않는다: 100.05 × 0.9995 =
    # 99.95 × 1.0005 = 99.999975.
    rules = OrderRules(tick_size=D("0.01"), min_order=D(100))
    decision = run(
        MR,
        UP,
        held(qty="10", avg="99.95"),
        price="100",
        rep=report(predicted="100.05"),
        rules=rules,
    )
    assert decision == Decision(None, NoOrderReason.NOT_OVER_AVG_AND_FEE, None)


def test_a_holding_row_of_zero_is_no_holding() -> None:
    # 보유 중은 qty > 0이다. 0인 줄(팔아서 남은 줄)은 보유가 없는 것으로 본다.
    empty = make_holding(A, D(0), D(0), D(0), D(0))
    assert run(TREND, UP, empty).plan == limit(OrderSide.BUY, "990", "10.10101010")
    assert run(TREND, DOWN, empty) == Decision(None, NoOrderReason.NOTHING, None)
    assert run(MR, UP, empty) == Decision(None, NoOrderReason.NOTHING, None)
