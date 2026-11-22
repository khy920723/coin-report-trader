"""손절(M3 명세 3.9), 멈춤 상태(3.10), 전량 매도의 결과 글(3.10.3)의 시험.

값은 모두 만든 숫자다. 3.9의 예를 넣는다.
"""

from dataclasses import replace
from decimal import Decimal

import pytest

from coin_report_trader.core.trade import (
    ErrorPauseCause,
    OrderKind,
    OrderPlan,
    OrderReason,
    OrderRules,
    OrderSide,
    PauseStopLoss,
    TradeMethod,
    TradeSettings,
)
from coin_report_trader.trading.paper import make_holding
from coin_report_trader.trading.risk import (
    PauseState,
    SellAllResult,
    StopCheck,
    StopDecision,
    enter_error,
    may_fetch_quotes,
    may_run_round,
    may_sell_all,
    may_stop_check,
    pause_user,
    release_error,
    release_user,
    sell_all_text,
    stop_line,
    stop_loss_check,
    stop_loss_off,
    stop_loss_plan,
)

A = "KRW-AAA"
D = Decimal
KEEP = PauseStopLoss.KEEP
STOP = PauseStopLoss.STOP
EXCHANGE = ErrorPauseCause.EXCHANGE
SAVE = ErrorPauseCause.SAVE
UNEXPECTED = ErrorPauseCause.UNEXPECTED
RULES = OrderRules(tick_size=D(1), min_order=D(5000))


def settings(stop_ratio: str = "5") -> TradeSettings:
    return TradeSettings(
        budget=D(100_000),
        coin_ratios={A: D(100)},
        first_ratio=D(20),
        mr_first_ratio=None,
        add_ratio=D(15),
        tolerance=D(0),
        stop_ratio=D(stop_ratio),
        daily_loss_limit=D(3000),
        order_interval_s=60,
        method=TradeMethod.TREND,
        pause_stop_loss=KEEP,
        mr_sell_unknown_fee=False,
        mr_tolerable_loss=D(5),
        paper_funds=D(100_000),
        backup_days=7,
    )


def held(qty: str = "10", avg: str = "980", locked: str = "0"):
    return make_holding(A, D(qty), D(locked), D(qty) * D(avg), D(qty))


# --- 손절선과 판정(3.9) ------------------------------------------------------------------------


def test_stop_line_example() -> None:
    # 평균 매수가 980, 손절 5%면 931이다.
    assert stop_line(D(980), D(5)) == D(931)
    assert stop_line(D(1000), D("2.5")) == D(975)
    assert stop_line(None, D(5)) is None
    assert stop_line(D(0), D(5)) is None


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        ("932", StopCheck.ABOVE),
        ("931.01", StopCheck.ABOVE),
        ("931", StopCheck.REACHED),
        ("900", StopCheck.REACHED),
    ],
)
def test_stop_loss_check_example(price: str, expected: StopCheck) -> None:
    # 현재가 931이면 닿았으므로 판다(현재가 ≤ 손절선).
    assert stop_loss_check(held(avg="980"), D(price), settings()) is expected


def test_stop_loss_check_without_holding_or_average() -> None:
    assert stop_loss_check(None, D(100), settings()) is StopCheck.NO_HOLDING
    empty = make_holding(A, D(0), D(0), D(0), D(0))
    assert stop_loss_check(empty, D(100), settings()) is StopCheck.NO_HOLDING
    no_avg = make_holding(A, D(10), D(0), D(0), D(0))
    assert no_avg.avg_price is None
    assert stop_loss_check(no_avg, D(1), settings()) is StopCheck.NO_AVG


def test_stop_loss_check_uses_the_stop_ratio_of_the_settings() -> None:
    # 손절 10%면 평균 1,000의 손절선은 900이다.
    assert stop_loss_check(held(avg="1000"), D(901), settings("10")) is StopCheck.ABOVE
    assert stop_loss_check(held(avg="1000"), D(900), settings("10")) is StopCheck.REACHED


# --- 손절 매도의 계획 --------------------------------------------------------------------------


def test_stop_loss_plan_sells_the_sellable_qty_at_market() -> None:
    decision = stop_loss_plan(held(qty="10", locked="2"), D(931), settings(), RULES)
    expected = OrderPlan(
        market=A,
        side=OrderSide.SELL,
        kind=OrderKind.MARKET,
        reason=OrderReason.STOP_LOSS,
        price=None,
        qty=D(8),
        amount=D(7448),  # 931 × 8의 어림
    )
    assert decision == StopDecision(plan=expected, alert=None)


def test_sell_all_uses_the_same_plan_with_its_reason() -> None:
    decision = stop_loss_plan(held(), D(1000), settings(), RULES, reason=OrderReason.SELL_ALL)
    assert decision.plan is not None
    assert decision.plan.reason is OrderReason.SELL_ALL
    assert (decision.plan.kind, decision.plan.qty) == (OrderKind.MARKET, D(10))


@pytest.mark.parametrize(("qty", "sells"), [("5", True), ("4.999", False)])
def test_too_small_to_sell_gives_a3(qty: str, sells: bool) -> None:
    # 팔 수 있는 수량의 평가 금액이 최소 주문 금액(5,000원)보다 작으면 팔지 않고 A-3이다.
    decision = stop_loss_plan(held(qty=qty, avg="1100"), D(1000), settings(), RULES)
    if sells:
        assert decision.plan is not None
        assert decision.alert is None
    else:
        assert decision == StopDecision(plan=None, alert="A-3")


def test_locked_qty_is_not_sold() -> None:
    # 보유 10개 가운데 9개가 묶여 1개(1,000원)만 팔 수 있으면 A-3이다.
    decision = stop_loss_plan(held(qty="10", locked="9"), D(1000), settings(), RULES)
    assert decision == StopDecision(plan=None, alert="A-3")
    assert stop_loss_plan(held(qty="10", locked="10"), D(1000), settings(), RULES).plan is None


def test_stop_loss_plan_refuses_wrong_input() -> None:
    with pytest.raises(ValueError, match="까닭"):
        stop_loss_plan(held(), D(1000), settings(), RULES, reason=OrderReason.JUDGMENT)
    with pytest.raises(ValueError, match="가격"):
        stop_loss_plan(held(), D(0), settings(), RULES)


# --- 멈춤 상태(3.10) ---------------------------------------------------------------------------


def test_new_state_is_running() -> None:
    state = PauseState()
    assert state == PauseState(user=False, error=False, error_cause=None, error_detail=None)
    assert may_run_round(state)
    assert may_stop_check(state, STOP)
    assert may_fetch_quotes(state)
    assert may_sell_all(state)
    assert not stop_loss_off(state, STOP)


def test_user_pause_and_release() -> None:
    paused = pause_user(PauseState())
    assert paused == PauseState(user=True)
    assert pause_user(paused) == paused  # 이미 그 멈춤이면 같다
    assert release_user(paused) == PauseState()
    assert release_user(PauseState()) == PauseState()


def test_error_pause_keeps_the_first_cause() -> None:
    first = enter_error(PauseState(), SAVE, None)
    assert first == PauseState(error=True, error_cause=SAVE, error_detail=None)
    again = enter_error(first, EXCHANGE, "일시 차단")
    assert again == first  # 처음 원인이 남는다
    assert release_error(again) == PauseState()


def test_user_and_error_pauses_are_kept_apart() -> None:
    both = pause_user(enter_error(PauseState(), EXCHANGE, "일시 차단"))
    assert both == PauseState(user=True, error=True, error_cause=EXCHANGE, error_detail="일시 차단")
    assert release_user(both) == PauseState(
        user=False, error=True, error_cause=EXCHANGE, error_detail="일시 차단"
    )
    assert release_error(both) == PauseState(user=True)
    # 사용자 멈춤 중에 오류 멈춤에 들어가도 사용자 멈춤은 그대로다.
    assert enter_error(PauseState(user=True), UNEXPECTED, None).user is True


@pytest.mark.parametrize(
    ("state", "setting", "round_", "stop", "quotes", "sell_all"),
    [
        (PauseState(), KEEP, True, True, True, True),
        (PauseState(user=True), KEEP, False, True, True, True),
        (PauseState(user=True), STOP, False, False, True, True),
        (PauseState(error=True, error_cause=SAVE), KEEP, False, False, True, False),
        (PauseState(error=True, error_cause=UNEXPECTED), KEEP, False, False, True, False),
        (PauseState(error=True, error_cause=EXCHANGE), KEEP, False, False, False, False),
        (PauseState(user=True, error=True, error_cause=EXCHANGE), KEEP, False, False, False, False),
    ],
)
def test_permissions(
    state: PauseState,
    setting: PauseStopLoss,
    round_: bool,
    stop: bool,
    quotes: bool,
    sell_all: bool,
) -> None:
    assert may_run_round(state) is round_
    assert may_stop_check(state, setting) is stop
    assert stop_loss_off(state, setting) is (not stop)
    assert may_fetch_quotes(state) is quotes
    assert may_sell_all(state) is sell_all


def test_state_is_frozen() -> None:
    with pytest.raises(AttributeError):
        PauseState().user = True  # type: ignore[misc]
    assert replace(PauseState(), user=True).user


# --- 전량 매도의 결과 글(3.10.3) ---------------------------------------------------------------


def test_sell_all_texts() -> None:
    assert sell_all_text(SellAllResult.DONE) == "전량 매도: 끝남"
    assert sell_all_text(SellAllResult.RETRYING) == "전량 매도: 다시 시도하는 중"
    assert sell_all_text(SellAllResult.LEFT, D("0.00123")) == "전량 매도: 0.00123 팔지 못함"
    assert sell_all_text(SellAllResult.STOPPED) == (
        "전량 매도: 오류 멈춤으로 그만둠. 오류 멈춤을 푼 뒤 다시 누르세요"
    )


@pytest.mark.parametrize(
    ("qty", "text"),
    [("3", "3"), ("2.50000000", "2.5"), ("1E-8", "0.00000001"), ("120", "120"), ("1E+2", "100")],
)
def test_left_qty_is_written_without_exponent_or_trailing_zeros(qty: str, text: str) -> None:
    assert sell_all_text(SellAllResult.LEFT, D(qty)) == f"전량 매도: {text} 팔지 못함"


def test_left_needs_a_qty_and_others_take_none() -> None:
    with pytest.raises(ValueError, match="팔지 못한 수량"):
        sell_all_text(SellAllResult.LEFT)
    with pytest.raises(ValueError, match="팔지 못한 수량"):
        sell_all_text(SellAllResult.LEFT, D(0))
    with pytest.raises(ValueError, match="LEFT"):
        sell_all_text(SellAllResult.DONE, D(1))
