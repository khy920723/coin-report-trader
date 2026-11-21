"""봉 통계(M1 명세 3.5, 3.5.1, 3.5.2). 숫자는 만든 값이다."""

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

import coin_report_trader.stats as stats_package
from coin_report_trader.core import (
    CANDLE_UNIT_LENGTHS,
    BarStats,
    Candle,
    CandleUnit,
    empty_bar_stats,
)
from coin_report_trader.stats import compute_bar_stats

DAY = CandleUnit.DAY
D = Decimal

H1 = CandleUnit.MINUTE_60
BASE = datetime(2026, 1, 1, tzinfo=UTC)
BARS = [
    (100, 110, 100, 110),
    (110, 110, 99, 99),
    (99, 121, 99, 121),
    (121, 121, 110, 110),
    (110, 132, 110, 132),
]


def bar(
    unit: CandleUnit, index: int, o: int, h: int, low: int, c: int, market: str = "KRW-AAA"
) -> Candle:
    start = BASE + CANDLE_UNIT_LENGTHS[unit] * index
    return Candle(market, unit, start, D(o), D(h), D(low), D(c), D(1), D(1))


def example(unit: CandleUnit = H1) -> list[Candle]:
    return [bar(unit, i, *values) for i, values in enumerate(BARS)]


def after(unit: CandleUnit, index: int) -> datetime:
    """`index`번 봉이 막 끝난 때."""
    return BASE + CANDLE_UNIT_LENGTHS[unit] * (index + 1)


def bar_stats(candles: list[Candle], now: datetime, count: int, unit: CandleUnit = H1):
    return compute_bar_stats(candles, now, market="KRW-AAA", unit=unit, count=count)


def test_the_example_of_the_spec_gives_every_value() -> None:  # B1, B2, B5, B6, B7
    stats, incomplete = bar_stats(example(), after(H1, 4), 5)
    assert incomplete is False
    assert stats == BarStats(
        "KRW-AAA", H1, 5, 5,
        first_start_utc=BASE, last_start_utc=BASE + timedelta(hours=4),
        close_last=D("132"), change=D("0.3200"), slope=D("0.0481"), vs_mean=D("0.1538"),
        up_ratio=D("0.6000"), volatility=D("0.1535"), max_drawdown=D("-0.1000"),
        from_high=D("0.0000"), high=D("132"), low=D("99"),
    )  # fmt: skip
    assert str(stats.change) == "0.3200"
    assert str(stats.max_drawdown) == "-0.1000"


def test_more_candles_than_the_count_use_only_the_latest_ones() -> None:
    early = [bar(H1, i, 50, 50, 50, 50) for i in range(2)]
    later = [bar(H1, i + 2, *values) for i, values in enumerate(BARS)]
    stats, _ = bar_stats(early + later, after(H1, 6), 5)
    same_values = replace(
        bar_stats(example(), after(H1, 4), 5)[0],
        first_start_utc=BASE + timedelta(hours=2),
        last_start_utc=BASE + timedelta(hours=6),
    )
    assert stats == same_values  # 앞의 두 봉은 쓰지 않아 값이 같다


def test_fewer_candles_than_the_count_give_the_count_of_bars_and_no_values() -> None:
    stats, _ = bar_stats(example(), after(H1, 4), 6)
    assert (stats.requested_count, stats.bar_count) == (6, 5)
    assert stats == BarStats("KRW-AAA", H1, 6, 5)
    assert stats.first_start_utc is None
    assert stats.low is None


def test_no_candles_give_the_empty_value() -> None:
    stats, incomplete = bar_stats([], BASE, 200)
    assert stats == empty_bar_stats("KRW-AAA", H1, 200)
    assert incomplete is False


def test_equal_candles_give_zero_for_every_ratio() -> None:
    flat = [bar(H1, i, 100, 100, 100, 100) for i in range(3)]
    stats, _ = bar_stats(flat, after(H1, 2), 3)
    for name in (
        "change",
        "slope",
        "vs_mean",
        "up_ratio",
        "volatility",
        "max_drawdown",
        "from_high",
    ):
        assert getattr(stats, name) == D("0.0000"), name
        assert str(getattr(stats, name)) == "0.0000", name
    assert (stats.high, stats.low, stats.close_last) == (D(100), D(100), D(100))


@pytest.mark.parametrize("unit", list(CandleUnit))
def test_every_unit_uses_its_own_length_for_complete_and_building(unit: CandleUnit) -> None:
    candles = example(unit)
    length = CANDLE_UNIT_LENGTHS[unit]
    assert bar_stats(candles, after(unit, 4), 5, unit)[0].bar_count == 5  # 끝난 때와 같으면 완성
    just_before = after(unit, 4) - timedelta(seconds=1)
    stats, incomplete = bar_stats(candles, just_before, 4, unit)
    assert incomplete is True  # 저장된 마지막 봉이 아직 만드는 중이다
    assert stats.last_start_utc == BASE + length * 3
    assert stats.bar_count == 4
    assert bar_stats(candles, just_before, 5, unit)[0].bar_count == 4  # 미완성은 세지 않는다


def test_an_incomplete_last_candle_is_flagged_and_not_used() -> None:
    stats, incomplete = bar_stats(example(), after(H1, 4) - timedelta(minutes=30), 4)
    assert incomplete is True
    assert stats.last_start_utc == BASE + timedelta(hours=3)
    assert stats.close_last == D(110)


def test_candles_of_other_coins_and_units_change_no_value() -> None:
    expected = bar_stats(example(), after(H1, 4), 5)
    mixed = example()
    mixed += [bar(H1, i, 1, 9, 1, 2, market="KRW-BBB") for i in range(5)]  # 다른 코인, 같은 시작
    mixed += [bar(CandleUnit.DAY, i, 1, 9, 1, 2) for i in range(5)]  # 다른 단위, 같은 시작
    mixed += [bar(CandleUnit.MINUTE_30, 3, 1, 9, 1, 2)]
    assert bar_stats(mixed, after(H1, 4), 5) == expected  # `BarStats`와 `last_incomplete`까지 같다
    only_other = [bar(H1, i, 1, 2, 1, 2, market="KRW-BBB") for i in range(5)]
    assert bar_stats(only_other, after(H1, 4), 5) == (BarStats("KRW-AAA", H1, 5, 0), False)


def test_the_same_start_keeps_the_last_given_candle() -> None:
    candles = example()
    candles.append(bar(H1, 4, 110, 140, 110, 140))
    stats, _ = bar_stats(candles, after(H1, 4), 5)
    assert stats.close_last == D(140)
    assert stats.high == D(140)
    assert stats.bar_count == 5


def test_the_order_of_the_given_candles_does_not_matter() -> None:
    ordered = bar_stats(example(), after(H1, 4), 5)
    assert bar_stats(list(reversed(example())), after(H1, 4), 5) == ordered
    shuffled = example()
    random.Random(7).shuffle(shuffled)
    assert bar_stats(shuffled, after(H1, 4), 5) == ordered


def test_gaps_are_not_filled() -> None:
    candles = [bar(H1, i, 100, 100, 100, 100) for i in (0, 1, 5, 9)]
    stats, _ = bar_stats(candles, after(H1, 9), 4)
    assert stats.bar_count == 4
    assert stats.first_start_utc == BASE
    assert stats.last_start_utc == BASE + timedelta(hours=9)


def test_a_zero_divisor_makes_only_that_value_empty() -> None:
    zero_open = [bar(H1, 0, 0, 5, 0, 5), bar(H1, 1, 5, 5, 5, 5)]
    stats, _ = bar_stats(zero_open, after(H1, 1), 2)
    assert stats.change is None
    assert stats.vs_mean == D("0.0000")
    zero_all = [bar(H1, 0, 0, 0, 0, 0), bar(H1, 1, 0, 0, 0, 0)]
    stats, _ = bar_stats(zero_all, after(H1, 1), 2)
    assert (stats.change, stats.slope, stats.vs_mean, stats.from_high) == (None, None, None, None)
    assert (stats.volatility, stats.max_drawdown) == (None, None)
    assert (stats.high, stats.low, stats.up_ratio) == (D(0), D(0), D("0.0000"))


@pytest.mark.parametrize(
    ("close", "expected"),
    [("100005", "0.0000"), ("100015", "0.0002"), ("100025", "0.0002"), ("100035", "0.0004")],
)
def test_ratios_round_half_even_to_four_places(close: str, expected: str) -> None:
    candles = [
        Candle("KRW-AAA", H1, BASE, D(100000), D(200000), D(1), D(100000), D(1), D(1)),
        Candle(
            "KRW-AAA",
            H1,
            BASE + timedelta(hours=1),
            D(100000),
            D(200000),
            D(1),
            D(close),
            D(1),
            D(1),
        ),
    ]
    stats, _ = bar_stats(candles, after(H1, 1), 2)
    assert stats.change == D(expected)


def test_a_tiny_negative_ratio_is_written_as_zero_not_negative_zero() -> None:
    candles = [
        Candle("KRW-AAA", H1, BASE, D(100000), D(100000), D(1), D(100000), D(1), D(1)),
        Candle(
            "KRW-AAA",
            H1,
            BASE + timedelta(hours=1),
            D(100000),
            D(100000),
            D(1),
            D("99999.99"),
            D(1),
            D(1),
        ),
    ]
    stats, _ = bar_stats(candles, after(H1, 1), 2)
    assert str(stats.change) == "0.0000"


def test_the_values_are_decimal_and_the_input_is_not_changed() -> None:
    candles = example()
    snapshot = list(candles)
    stats, _ = bar_stats(candles, after(H1, 4), 5)
    assert candles == snapshot
    for name in (
        "close_last",
        "change",
        "slope",
        "vs_mean",
        "up_ratio",
        "volatility",
        "max_drawdown",
        "from_high",
        "high",
        "low",
    ):
        assert isinstance(getattr(stats, name), Decimal), name


def test_only_the_bar_stats_function_is_exported() -> None:
    assert set(stats_package.__all__) == {"compute_bar_stats"}
    assert stats_package.compute_bar_stats is compute_bar_stats
    assert not hasattr(stats_package, "compute_candle_stats")  # 옛 통계는 51번이 지웠다
    assert not hasattr(stats_package, "CandleStats")


def test_the_drawdown_is_measured_from_the_highest_close_so_far() -> None:
    candles = [bar(H1, 0, 100, 100, 100, 100), bar(H1, 1, 100, 200, 100, 200)]
    candles.append(bar(H1, 2, 200, 200, 150, 150))
    stats, _ = bar_stats(candles, after(H1, 2), 3)
    assert stats.max_drawdown == D("-0.2500")  # 최고 종가 200에서 150으로


def test_from_high_uses_the_highest_high_not_the_highest_close() -> None:
    candles = [bar(H1, 0, 100, 150, 100, 100), bar(H1, 1, 100, 100, 100, 100)]
    stats, _ = bar_stats(candles, after(H1, 1), 2)
    assert stats.high == D(150)
    assert stats.from_high == D("-0.3333")  # 100 ÷ 150 − 1


def test_low_is_the_lowest_low_not_the_lowest_close() -> None:
    candles = [bar(H1, i, 100, 110, 90, 100) for i in range(3)]
    stats, _ = bar_stats(candles, after(H1, 2), 3)
    assert stats.low == D(90)
    assert stats.high == D(110)
