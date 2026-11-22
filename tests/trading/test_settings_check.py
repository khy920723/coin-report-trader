"""설정의 받지 않는 값(M3 명세 3.11.2)의 시험. 3.11.2의 예를 모두 넣는다. 값은 모두 만든 숫자다."""

from dataclasses import replace
from decimal import Decimal

import pytest

from coin_report_trader.core.trade import PauseStopLoss, TradeMethod, TradeSettings
from coin_report_trader.trading.orders import check_trade_settings, coin_ratio_key, first_buy_floor

A = "KRW-AAA"
B = "KRW-BBB"
C = "KRW-CCC"
D = Decimal
MIN = D(5000)


def settings(**changes: object) -> TradeSettings:
    base = TradeSettings(
        budget=D(100_000),
        coin_ratios={A: D(50), B: D(50)},
        first_ratio=D(20),
        mr_first_ratio=None,
        add_ratio=D(15),
        tolerance=D(1),
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


def errors(coins: tuple[str, ...] = (A, B), **changes: object) -> dict[str, str]:
    return dict(check_trade_settings(settings(**changes), coins, MIN))


def test_good_settings_pass() -> None:
    assert errors() == {}


def test_keys_are_the_setting_keys() -> None:
    assert coin_ratio_key(A) == "coin_ratio:KRW-AAA"


# --- 예(3.11.2) -------------------------------------------------------------------------------


def test_ratio_sum_example() -> None:
    # 33.33%, 33.33%, 33.34%는 되고, 33.33% 셋은 "…(지금 99.99%)."로 걸린다.
    good = {A: D("33.33"), B: D("33.33"), C: D("33.34")}
    bad = {A: D("33.33"), B: D("33.33"), C: D("33.33")}
    # 코인 예산 33,330원에서 추가 15%는 4,999.5원 < 5,500원이라, 추가 비율을 20%로 둔다.
    assert errors((A, B, C), coin_ratios=good, add_ratio=D(20)) == {}
    assert errors((A, B, C), coin_ratios=bad, add_ratio=D(20)) == {
        "coin_ratios": "코인별 투자 비율의 합이 100%여야 합니다(지금 99.99%)."
    }


def test_tolerance_example() -> None:
    # 허용 범위 2%는 되고 2.01%는 걸린다.
    assert errors(tolerance=D(2)) == {}
    assert errors(tolerance=D("2.01")) == {"tolerance": "0%에서 2% 사이로 넣으세요."}
    assert errors(tolerance=D(0)) == {}
    assert errors(tolerance=D("-0.01")) == {"tolerance": "0%에서 2% 사이로 넣으세요."}


def test_first_buy_floor_example() -> None:
    # 최소 주문 금액 5,000원, 손절 5%면 하한은 5,000 ÷ 0.95 × 1.1 = 5,789.47…이라 "5,790원"이다.
    floor = first_buy_floor(MIN, D(5))
    assert D("5789.47") < floor < D("5789.48")
    # 예산 100,000원, 투자 비율 50%(코인 예산 50,000원)에 첫 매수 10%(5,000원)는 걸린다.
    assert errors(first_ratio=D(10)) == {
        "first_ratio": "AAA: 첫 매수 금액(5,000원)이 하한(5,790원)보다 작습니다.\n"
        "BBB: 첫 매수 금액(5,000원)이 하한(5,790원)보다 작습니다."
    }
    # 20%(10,000원)는 되고, 추가 15%(7,500원)는 5,500원 이상이라 된다.
    assert errors(first_ratio=D(20), add_ratio=D(15)) == {}


def test_add_amount_must_be_at_least_1_1_times_the_min_order() -> None:
    # 추가 10%면 5,000원 < 5,500원이다.
    assert errors(add_ratio=D(10), coin_ratios={A: D(50), B: D(50)}) == {
        "add_ratio": "AAA: 추가 한 번의 금액(5,000원)이 최소 주문 금액의 1.1배(5,500원)보다 "
        "작습니다.\nBBB: 추가 한 번의 금액(5,000원)이 최소 주문 금액의 1.1배(5,500원)보다 작습니다."
    }
    assert errors(add_ratio=D(11)) == {}  # 5,500원은 작지 않다


def test_amounts_in_the_text_floor_the_amount_and_ceil_the_limit() -> None:
    # 코인 예산 33,333.33…원(1/3), 추가 16.5% = 5,499.99…원은 "5,499원"으로 보인다.
    third = {A: D(100) / 3, B: D(100) / 3, C: D(100) / 3}
    got = check_trade_settings(settings(coin_ratios=third, add_ratio=D("16.5")), (A,), MIN)
    assert "AAA: 추가 한 번의 금액(5,499원)" in got["add_ratio"]
    # 하한이 정수가 아니면 올려 보인다: 최소 주문 금액 5,001원 × 1.1 = 5,501.1 → "5,502원"
    over = check_trade_settings(settings(add_ratio=D(10)), (A,), D(5001))
    assert over["add_ratio"].endswith("최소 주문 금액의 1.1배(5,502원)보다 작습니다.")


def test_first_and_add_over_the_coin_budget() -> None:
    assert errors(first_ratio=D(60), add_ratio=D(50)) == {
        "coin_ratio:KRW-AAA": "첫 매수와 추가 한 번을 합친 금액이 AAA의 예산보다 큽니다.",
        "coin_ratio:KRW-BBB": "첫 매수와 추가 한 번을 합친 금액이 BBB의 예산보다 큽니다.",
    }
    assert errors(first_ratio=D(50), add_ratio=D(50)) == {}  # 같으면 크지 않다


# --- 0 이하와 범위 -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "key"),
    [
        ("budget", "budget"),
        ("first_ratio", "first_ratio"),
        ("add_ratio", "add_ratio"),
        ("daily_loss_limit", "daily_loss_limit"),
        ("paper_funds", "paper_funds"),
    ],
)
def test_zero_or_less_is_refused(field: str, key: str) -> None:
    for value in (D(0), D(-1)):
        assert errors(**{field: value})[key] == "0보다 큰 값을 넣으세요."


def test_zero_coin_ratio_is_refused_with_the_coin_key() -> None:
    got = errors(coin_ratios={A: D(0), B: D(100)})
    assert got == {"coin_ratio:KRW-AAA": "0보다 큰 값을 넣으세요."}


@pytest.mark.parametrize(
    ("value", "ok"), [("0", False), ("0.01", True), ("99.99", True), ("100", False)]
)
def test_stop_ratio_range(value: str, ok: bool) -> None:
    got = errors(stop_ratio=D(value))
    if ok:
        assert "stop_ratio" not in got
    else:
        assert got["stop_ratio"] == "손절 비율은 0보다 크고 100%보다 작아야 합니다."


@pytest.mark.parametrize(
    ("value", "ok"), [("-0.01", False), ("0", True), ("99.99", True), ("100", False)]
)
def test_tolerable_loss_range(value: str, ok: bool) -> None:
    got = errors(mr_tolerable_loss=D(value))
    assert ("mr_tolerable_loss" not in got) is ok
    if not ok:
        assert got["mr_tolerable_loss"] == "0% 이상 100% 미만으로 넣으세요."


@pytest.mark.parametrize(("value", "ok"), [(0, False), (1, True), (3600, True), (3601, False)])
def test_order_interval_range(value: int, ok: bool) -> None:
    got = errors(order_interval_s=value)
    assert ("order_interval_s" not in got) is ok
    if not ok:
        assert got["order_interval_s"] == "1초에서 3,600초 사이로 넣으세요."


@pytest.mark.parametrize(("value", "ok"), [(0, False), (1, True), (365, True), (366, False)])
def test_backup_days_range(value: int, ok: bool) -> None:
    got = errors(backup_days=value)
    assert ("backup_days" not in got) is ok
    if not ok:
        assert got["backup_days"] == "1일에서 365일 사이로 넣으세요."


# --- 첫 매수 비율의 두 칸 ----------------------------------------------------------------------


def test_the_unchosen_first_ratio_is_checked_only_when_filled() -> None:
    # 추세추종을 골랐다. 평균회귀 첫 매수 비율은 비어 있으면 보지 않고, 채웠으면 본다.
    assert errors(mr_first_ratio=None) == {}
    assert errors(mr_first_ratio=D(5))["mr_first_ratio"].startswith("AAA: 첫 매수 금액(2,500원)")
    assert errors(mr_first_ratio=D(0))["mr_first_ratio"] == "0보다 큰 값을 넣으세요."


def test_the_chosen_first_ratio_is_always_checked() -> None:
    got = errors(method=TradeMethod.MEAN_REVERSION, first_ratio=None, mr_first_ratio=None)
    assert got == {"mr_first_ratio": "이 값을 넣어야 합니다."}
    assert errors(method=TradeMethod.MEAN_REVERSION, first_ratio=None, mr_first_ratio=D(20)) == {}


def test_a_chosen_coin_without_a_ratio_needs_a_value() -> None:
    got = errors((A, B, C))
    assert got["coin_ratio:KRW-CCC"] == "이 값을 넣어야 합니다."


def test_ratios_of_unchosen_coins_are_not_counted() -> None:
    assert errors((A,), coin_ratios={A: D(100), B: D(40)}) == {}


def test_texts_on_the_same_key_are_joined_by_newlines() -> None:
    got = errors(coin_ratios={A: D(-10), B: D(110)}, first_ratio=D(60), add_ratio=D(50))
    assert got["coin_ratio:KRW-AAA"] == "0보다 큰 값을 넣으세요."
    assert got["coin_ratio:KRW-BBB"] == "첫 매수와 추가 한 번을 합친 금액이 BBB의 예산보다 큽니다."
    assert "coin_ratios" not in got  # −10 + 110 = 100


def test_first_buy_at_exactly_the_floor_passes() -> None:
    # 최소 주문 금액 5,000원, 손절 50%면 하한은 5,000 ÷ 0.5 × 1.1 = 11,000원이다. 예산 100,000원 ×
    # 투자 비율 50% × 첫 매수 22% = 11,000원은 "하한보다 작음"이 아니라 통과한다.
    assert first_buy_floor(MIN, D(50)) == D(11_000)
    assert errors(stop_ratio=D(50), first_ratio=D(22)) == {}
    assert "first_ratio" in errors(stop_ratio=D(50), first_ratio=D("21.99"))
