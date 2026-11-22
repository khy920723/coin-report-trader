"""`ai/last_result.py`(M2 명세 3.9): 지난 판단의 결과. 예는 명세의 만든 숫자다."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from coin_report_trader.ai.last_result import (
    PastJudgment,
    candle_start_for_actual,
    compute_last_result,
    target_close_utc,
)
from coin_report_trader.core import CandleUnit, Direction, LastResult, Verdict

T = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)


def past(
    direction: Direction = Direction.UP,
    base: str = "100",
    predicted: str = "102",
    unit: CandleUnit = CandleUnit.MINUTE_60,
) -> PastJudgment:
    return PastJudgment(1, T, unit, Decimal(base), direction, Decimal(predicted))


@pytest.mark.parametrize(
    ("actual", "diff", "hit"),
    [
        ("101", "-0.0098", Verdict.HIT),  # 101 ÷ 102 − 1 = −0.009803…
        ("99", "-0.0294", Verdict.MISS),
        ("100", "-0.0196", Verdict.HIT),  # 같으면 맞음
        ("102", "0.0000", Verdict.HIT),
        ("120", "0.1765", Verdict.HIT),
    ],
)
def test_the_examples_of_the_spec_for_an_up_judgment(actual: str, diff: str, hit: Verdict) -> None:
    result = compute_last_result(past(), Decimal(actual))
    assert result == LastResult(1, Decimal("102"), Decimal(actual), Decimal(diff), hit)


def test_the_same_price_is_a_hit_for_a_down_judgment_too() -> None:
    result = compute_last_result(past(Direction.DOWN), Decimal("100"))
    assert result.hit is Verdict.HIT
    assert result.diff == Decimal("-0.0196")


@pytest.mark.parametrize(
    ("direction", "actual", "hit"),
    [
        (Direction.UP, "100", Verdict.HIT),
        (Direction.UP, "99.99", Verdict.MISS),
        (Direction.DOWN, "100", Verdict.HIT),
        (Direction.DOWN, "100.01", Verdict.MISS),
        (Direction.DOWN, "90", Verdict.HIT),
    ],
)
def test_the_hit_is_judged_against_the_price_given_at_the_judgment(
    direction: Direction, actual: str, hit: Verdict
) -> None:
    assert compute_last_result(past(direction), Decimal(actual)).hit is hit


def test_the_diff_is_rounded_half_even_to_four_places() -> None:
    # 실제 ÷ 예측 − 1이 정확히 0.00125, 0.00135, −0.00125일 때.
    assert compute_last_result(past(predicted="100"), Decimal("100.125")).diff == Decimal("0.0012")
    assert compute_last_result(past(predicted="100"), Decimal("100.135")).diff == Decimal("0.0014")
    assert compute_last_result(past(predicted="100"), Decimal("99.875")).diff == Decimal("-0.0012")


def test_a_candle_that_is_not_stored_or_not_complete_leaves_everything_empty() -> None:
    result = compute_last_result(past(), None)
    assert result == LastResult(1, Decimal("102"), None, None, None)


def test_the_round_number_and_the_predicted_price_are_carried() -> None:
    judgment = PastJudgment(7, T, CandleUnit.DAY, Decimal("5"), Direction.DOWN, Decimal("4.5"))
    result = compute_last_result(judgment, Decimal("4"))
    assert (result.round_number, result.predicted_price) == (7, Decimal("4.5"))


@pytest.mark.parametrize("unit", list(CandleUnit))
def test_the_target_is_t_plus_the_unit_length_and_the_actual_candle_starts_at_t(
    unit: CandleUnit,
) -> None:
    lengths = {
        CandleUnit.MINUTE_30: timedelta(minutes=30),
        CandleUnit.MINUTE_60: timedelta(hours=1),
        CandleUnit.MINUTE_240: timedelta(hours=4),
        CandleUnit.DAY: timedelta(days=1),
    }
    judgment = past(unit=unit)
    assert target_close_utc(judgment) == T + lengths[unit]
    assert candle_start_for_actual(judgment) == T


def test_a_zero_predicted_price_does_not_crash_and_leaves_the_diff_empty() -> None:
    result = compute_last_result(past(predicted="0"), Decimal("1"))
    assert result.diff is None
    assert result.hit is Verdict.MISS  # 맞았는지는 예측 가격이 아니라 현재가 P(100)로 가린다
