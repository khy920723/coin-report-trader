"""기간별 봉 통계와 지표(M1 명세 3.5, 3.6). 숫자는 만든 값이다."""

import random
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fakes.clock import FakeClock

from coin_report_trader.core import PERIODS, Candle, CandleUnit, PeriodStats, empty_periods
from coin_report_trader.stats import CandleStats, compute_candle_stats

DAY = CandleUnit.DAY
HOUR = CandleUnit.MINUTE_60
D = Decimal
JAN1 = datetime(2026, 1, 1, tzinfo=UTC)  # 한국 시간 09:00. 일봉의 시작 시각
JAN10 = datetime(2026, 1, 10, tzinfo=UTC)
INSUFFICIENT = "봉이 모자람"


def day(n: int, open_: str, close: str, *, value: str = "1000") -> Candle:
    o, c = D(open_), D(close)
    return Candle(
        "KRW-AAA", DAY, JAN1 + timedelta(days=n), o, max(o, c), min(o, c), c, D(1), D(value)
    )


def flat(unit: CandleUnit, start: datetime, price: str = "100") -> Candle:
    p = D(price)
    return Candle("KRW-AAA", unit, start, p, p, p, p, D(1), D(1000))


def flat_days(count: int, *, first: int = 0, price: str = "100") -> list[Candle]:
    return [flat(DAY, JAN1 + timedelta(days=first + i), price) for i in range(count)]


def flat_hours(count: int, *, first: int = 0) -> list[Candle]:
    return [flat(HOUR, JAN1 + timedelta(hours=first + i)) for i in range(count)]


def b1_days() -> list[Candle]:
    pairs = [("100", "110"), ("110", "99"), ("99", "121"), ("121", "110"), ("110", "132")]
    return [day(i, o, c) for i, (o, c) in enumerate(pairs)]


def compute(candles: Sequence[Candle], now: datetime = JAN10) -> CandleStats:
    return compute_candle_stats(candles, FakeClock(start=now).now())


def period(result: CandleStats, name: str) -> PeriodStats:
    return next(p for p in result.periods if p.period == name)


def counts(result: CandleStats) -> dict[str, int]:
    return {p.period: p.count for p in result.periods}


# --- 1개월 통계(B1, B2, B3) ----------------------------------------------------------------


def test_one_month_stats_of_five_days() -> None:
    result = compute(b1_days())
    month = period(result, "1개월")
    assert month.count == 5
    assert month.change == D("0.3200")
    assert month.high == D(132)
    assert month.low == D(99)
    assert month.from_high == D("0.0000")
    assert month.max_drawdown == D("-0.1000")
    assert month.up_ratio == D("0.6000")
    assert month.avg_value == D(1000)
    assert (month.open_price, month.close_price) == (D(100), D(132))
    assert (month.first_start, month.last_start) == (JAN1, JAN1 + timedelta(days=4))
    assert (month.period, month.unit) == ("1개월", DAY)
    assert result.last_incomplete is False
    assert result.has_values


def test_volatility_is_the_population_deviation_rounded_to_four_places() -> None:
    result = compute(b1_days())
    assert period(result, "1개월").volatility == D("0.1535")
    assert result.indicator is not None
    assert result.indicator.volatility_1y == D("0.1535")
    assert dict(result.indicator.items())["1년 일간 변동성"] == "15.35%"


def test_a_day_that_has_not_ended_is_left_out_and_flagged() -> None:
    sixth = day(5, "132", "200")
    now = JAN1 + timedelta(days=5, hours=12)  # 여섯째 일봉(1월 6일 00:00 UTC 시작)에 걸친 때
    result = compute([*b1_days(), sixth], now)
    assert result.last_incomplete is True
    assert period(result, "1개월") == period(compute(b1_days(), now), "1개월")
    assert period(result, "1개월").close_price == D(132)
    assert compute(b1_days(), now).last_incomplete is False


# --- 봉이 없는 구간, 범위, 단위마다의 L(B4, B6, B32) --------------------------------------------


def test_days_without_a_candle_are_not_filled() -> None:
    result = compute([day(0, "100", "110"), day(2, "110", "99"), day(3, "99", "121")])
    month = period(result, "1개월")
    assert month.count == 3
    assert month.close_price == D(121)


def test_one_month_takes_thirty_days_of_thirty_one() -> None:
    result = compute(flat_days(31), JAN1 + timedelta(days=40))
    month = period(result, "1개월")
    assert month.count == 30
    assert month.first_start == JAN1 + timedelta(days=1)
    assert month.last_start == JAN1 + timedelta(days=30)


def test_period_sizes_and_order_with_a_long_history() -> None:
    result = compute(flat_days(400), JAN1 + timedelta(days=450))
    assert [p.period for p in result.periods] == [name for name, _ in PERIODS]
    assert [p.unit for p in result.periods] == [unit for _, unit in PERIODS]
    assert counts(result) == {"전체": 400, "1년": 365, "3개월": 90, "1개월": 30, "1주": 0}


@pytest.mark.parametrize(
    ("name", "unit", "length", "step"),
    [
        ("1년", DAY, 364, timedelta(days=1)),
        ("3개월", DAY, 89, timedelta(days=1)),
        ("1개월", DAY, 29, timedelta(days=1)),
        ("1주", HOUR, 167, timedelta(hours=1)),
    ],
)
def test_period_range_includes_the_lookback_edge_and_not_one_before(
    name: str, unit: CandleUnit, length: int, step: timedelta
) -> None:
    last = JAN1 + step * (length + 1)
    candles = [
        flat(unit, last - step * (length + 1)),
        flat(unit, last - step * length),
        flat(unit, last),
    ]
    result = compute(candles, last + step)
    stats = period(result, name)
    assert stats.count == 2
    assert stats.first_start == last - step * length
    assert stats.last_start == last
    assert period(result, "전체").count == (3 if unit is DAY else 0)


def test_one_week_takes_168_hours() -> None:
    result = compute(flat_hours(168), JAN1 + timedelta(hours=168))
    week = period(result, "1주")
    assert week.count == 168
    assert week.volatility == D("0.0000")
    assert week.change == D("0.0000")
    assert period(compute(flat_hours(169), JAN1 + timedelta(hours=169)), "1주").count == 168


def test_each_unit_has_its_own_last_complete_candle() -> None:
    days = flat_days(5)  # L(일봉)은 1월 5일
    hours = flat_hours(400, first=24 * 30)  # L(60분봉)은 한참 뒤
    now = JAN1 + timedelta(days=60)
    result = compute([*days, *hours], now)
    assert period(result, "1개월").count == 5
    assert period(result, "1주").count == 168
    assert period(result, "1주").last_start == JAN1 + timedelta(hours=24 * 30 + 399)


# --- 완성된 봉(경계) ----------------------------------------------------------------------


def test_day_candle_is_complete_exactly_when_its_day_ends() -> None:
    candles = [flat(DAY, JAN1), flat(DAY, JAN1 + timedelta(days=1))]
    ends = JAN1 + timedelta(days=2)
    assert period(compute(candles, ends), "전체").count == 2
    assert period(compute(candles, ends - timedelta(seconds=1)), "전체").count == 1
    assert compute(candles, ends).last_incomplete is False
    assert compute(candles, ends - timedelta(seconds=1)).last_incomplete is True


def test_hour_candle_is_complete_exactly_when_its_hour_ends() -> None:
    candles = [flat(HOUR, JAN1), flat(HOUR, JAN1 + timedelta(hours=1))]
    ends = JAN1 + timedelta(hours=2)
    assert period(compute(candles, ends), "1주").count == 2
    assert period(compute(candles, ends - timedelta(seconds=1)), "1주").count == 1


def test_an_hour_candle_does_not_set_last_incomplete() -> None:
    result = compute([*flat_days(3), flat(HOUR, JAN10 - timedelta(minutes=30))])
    assert result.last_incomplete is False


def test_the_day_boundary_is_09_00_in_korea() -> None:
    kst = timezone(timedelta(hours=9))
    start = datetime(2026, 1, 1, 9, 0, tzinfo=kst)  # = 2026-01-01 00:00 UTC
    candles = [flat(DAY, start), flat(DAY, start + timedelta(days=1))]
    assert period(compute(candles, datetime(2026, 1, 3, 9, 0, tzinfo=kst)), "전체").count == 2
    assert period(compute(candles, datetime(2026, 1, 3, 8, 59, tzinfo=kst)), "전체").count == 1


# --- 봉이 모자람(B5), 봉이 없을 때 -------------------------------------------------------------


def test_one_complete_day_is_not_enough_for_any_period() -> None:
    result = compute([day(0, "100", "110")])
    assert [p.count for p in result.periods] == [1, 1, 1, 1, 0]
    for stats in result.periods:
        assert stats == PeriodStats(period=stats.period, unit=stats.unit, count=stats.count)
    assert result.has_values is False
    assert result.last_incomplete is False
    assert result.indicator is not None
    assert {shown for _, shown in result.indicator.items()} == {INSUFFICIENT}


def test_no_candles_gives_five_empty_periods_and_no_indicator() -> None:
    result = compute([])
    assert result.periods == empty_periods()
    assert result.indicator is None
    assert result.last_incomplete is False
    assert result.has_values is False


def test_only_hour_candles_have_no_indicator() -> None:
    result = compute(flat_hours(10), JAN1 + timedelta(hours=20))
    assert result.indicator is None
    assert result.last_incomplete is False
    assert period(result, "1주").count == 10
    assert period(result, "1주").volatility == D("0.0000")
    assert period(result, "전체") == PeriodStats(period="전체", unit=DAY, count=0)


def test_only_an_unfinished_day_has_no_indicator_but_is_flagged() -> None:
    result = compute([day(0, "100", "110")], JAN1 + timedelta(hours=5))
    assert result.periods == empty_periods()
    assert result.indicator is None
    assert result.last_incomplete is True


def test_two_candles_are_enough() -> None:
    month = period(compute([day(0, "100", "110"), day(1, "110", "99")]), "1개월")
    assert month.count == 2
    assert month.change == D("-0.0100")
    assert month.volatility == D("0.0000")  # 변화율이 하나뿐이다
    assert month.up_ratio == D("0.5000")
    assert month.max_drawdown == D("-0.1000")
    assert month.from_high == D("-0.1000")


# --- 지표(B7) ----------------------------------------------------------------------------


def b7_days() -> list[Candle]:
    closes = [D(76)] * 100 + [D(99)] * 18 + [D(108), D(110)]
    assert len(closes) == 120
    assert sum(closes[-20:]) / 20 == 100
    assert sum(closes) / 120 == 80
    return [
        Candle("KRW-AAA", DAY, JAN1 + timedelta(days=i), c, c, c, c, D(1), D(1000))
        for i, c in enumerate(closes)
    ]


def test_moving_average_gaps_and_the_shown_pairs() -> None:
    result = compute(b7_days(), JAN1 + timedelta(days=130))
    indicator = result.indicator
    assert indicator is not None
    assert indicator.vs_ma20 == D("0.1000")
    assert indicator.vs_ma120 == D("0.3750")
    pairs = indicator.items()
    assert pairs[0][0] == "1년 변화"
    assert pairs[2] == ("20일 평균 대비", "+10.00%")
    assert pairs[3] == ("120일 평균 대비", "+37.50%")
    assert ("1주 시간 변동성", INSUFFICIENT) in pairs
    assert indicator.volatility_1w is None


def test_indicator_fields_follow_the_period_values() -> None:
    result = compute(
        [*flat_days(400, price="100")[:399], day(399, "100", "150")], JAN1 + timedelta(days=450)
    )
    indicator = result.indicator
    assert indicator is not None
    assert indicator.trend_1y == period(result, "1년").change == D("0.5000")
    assert indicator.trend_3m == period(result, "3개월").change == D("0.5000")
    assert indicator.volatility_1y == period(result, "1년").volatility
    assert indicator.max_drawdown_1y == period(result, "1년").max_drawdown == D("0.0000")
    assert indicator.volatility_1w is None


def test_one_week_volatility_comes_from_the_hour_candles() -> None:
    hours = [flat(HOUR, JAN1 + timedelta(hours=i), p) for i, p in enumerate(["100", "110", "99"])]
    result = compute([*flat_days(3), *hours], JAN1 + timedelta(days=5))
    assert result.indicator is not None
    assert result.indicator.volatility_1w == D("0.1000")
    assert dict(result.indicator.items())["1주 시간 변동성"] == "10.00%"


def test_moving_averages_need_enough_complete_days() -> None:
    nineteen = compute(flat_days(19), JAN1 + timedelta(days=30)).indicator
    twenty = compute(flat_days(20), JAN1 + timedelta(days=30)).indicator
    assert nineteen is not None
    assert twenty is not None
    assert nineteen.vs_ma20 is None
    assert twenty.vs_ma20 == D("0.0000")
    assert twenty.vs_ma120 is None
    one_nineteen = compute(flat_days(119), JAN1 + timedelta(days=130)).indicator
    one_twenty = compute(flat_days(120), JAN1 + timedelta(days=130)).indicator
    assert one_nineteen is not None
    assert one_twenty is not None
    assert one_nineteen.vs_ma120 is None
    assert one_twenty.vs_ma120 == D("0.0000")


def test_the_unfinished_day_is_not_in_the_moving_average() -> None:
    days = flat_days(20)
    unfinished = day(20, "100", "1000")
    result = compute([*days, unfinished], JAN1 + timedelta(days=20, hours=1))
    assert result.indicator is not None
    assert result.indicator.vs_ma20 == D("0.0000")


# --- 셈의 세부 -----------------------------------------------------------------------------


def test_ratios_are_rounded_half_even_to_four_places() -> None:
    down = compute([day(0, "10000", "10000"), day(1, "10000", "11234.5")])
    up = compute([day(0, "10000", "10000"), day(1, "10000", "11235.5")])
    assert period(down, "전체").change == D("0.1234")  # 0.12345 → 짝수 쪽
    assert period(up, "전체").change == D("0.1236")  # 0.12355 → 짝수 쪽
    assert str(period(down, "전체").change) == "0.1234"


def test_prices_are_kept_as_received_and_values_are_not_floats() -> None:
    first = Candle(
        "KRW-AAA",
        DAY,
        JAN1,
        D("0.00000012"),
        D("0.0000002"),
        D("0.0000001"),
        D("0.00000015"),
        D(1),
        D("1E+3"),
    )
    second = Candle(
        "KRW-AAA",
        DAY,
        JAN1 + timedelta(days=1),
        D("0.00000015"),
        D("0.0000003"),
        D("0.00000009"),
        D("0.00000021"),
        D(1),
        D("2E+3"),
    )
    stats = period(compute([first, second]), "전체")
    assert stats.open_price == D("0.00000012")
    assert str(stats.open_price) == "1.2E-7"
    assert stats.close_price == D("0.00000021")
    assert stats.high == D("0.0000003")
    assert stats.low == D("0.00000009")
    assert stats.avg_value == D(1500)
    assert stats.change == D("0.7500")
    assert all(
        isinstance(v, Decimal)
        for v in (
            stats.change,
            stats.from_high,
            stats.max_drawdown,
            stats.volatility,
            stats.up_ratio,
        )
    )


def test_max_drawdown_takes_the_largest_fall_from_a_running_peak() -> None:
    closes = ["100", "120", "90", "130", "117", "200"]
    candles = [day(i, c, c) for i, c in enumerate(closes)]
    stats = period(compute(candles), "전체")
    assert stats.max_drawdown == D("-0.2500")  # 120 → 90
    assert stats.from_high == D("0.0000")
    rising = period(compute([day(0, "1", "1"), day(1, "2", "2")]), "전체")
    assert rising.max_drawdown == D("0.0000")
    assert not str(rising.max_drawdown).startswith("-")


def test_from_high_is_not_positive() -> None:
    stats = period(
        compute([day(0, "100", "100"), day(1, "100", "150"), day(2, "150", "120")]), "전체"
    )
    assert stats.from_high == D("-0.2000")  # 120 ÷ 150 − 1
    assert stats.high == D(150)


def test_up_ratio_counts_closes_above_opens() -> None:
    candles = [day(0, "10", "11"), day(1, "11", "11"), day(2, "11", "10"), day(3, "10", "12")]
    assert period(compute(candles), "전체").up_ratio == D("0.5000")
    assert period(compute([day(0, "10", "9"), day(1, "9", "8")]), "전체").up_ratio == D("0.0000")


def test_avg_value_is_the_mean_of_the_candle_values() -> None:
    candles = [
        day(0, "1", "1", value="1000"),
        day(1, "1", "1", value="2000"),
        day(2, "1", "1", value="4500"),
    ]
    assert period(compute(candles), "전체").avg_value == D("2500")


def test_a_zero_price_does_not_crash_and_leaves_the_ratio_empty() -> None:
    stats = period(compute([day(0, "0", "5"), day(1, "5", "6")]), "전체")
    assert stats.count == 2
    assert stats.change is None
    assert stats.high == D(6)


def test_the_input_order_and_repeated_starts_do_not_matter() -> None:
    candles = b1_days()
    shuffled = random.Random(7).sample(candles, len(candles))
    assert compute(shuffled) == compute(candles)
    stale = day(4, "110", "1")
    assert compute([stale, *candles]) == compute(candles)  # 같은 시작 시각은 나중에 온 것
    assert compute([*candles, stale]) != compute(candles)


def test_times_must_have_a_time_zone() -> None:
    with pytest.raises(ValueError, match="시간대"):
        compute_candle_stats(b1_days(), datetime(2026, 1, 10))
    naive = Candle("KRW-AAA", DAY, datetime(2026, 1, 1), D(1), D(1), D(1), D(1), D(1), D(1))
    with pytest.raises(ValueError, match="시간대"):
        compute([naive])


def test_five_periods_always() -> None:
    for candles in ([], b1_days(), flat_hours(3)):
        assert len(compute(candles).periods) == 5
