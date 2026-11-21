"""M1 명세 6절의 봉 통계 시나리오: B1~B7, B32, B43(3.5, 3.5.1, 3.5.2, 3.14.5).

봉 통계 함수(`compute_bar_stats`)를 직접 부르고, 화면에 보이는 글은 창을 만들어 본다(띄우지 않는다).
B3·B5의 `CoinView`와 받기는 자료 스레드(가짜 시세, 가짜 시계)로 본다. 숫자는 만든 값이다.
"""

import dataclasses
from datetime import timedelta
from decimal import Decimal

import pytest
from acceptance.m1_support import AAA, HOUR, NOW, THIS_HOUR, bar, spec_bars, utc
from PySide6.QtWidgets import QApplication, QLabel

from coin_report_trader.core import (
    BAR_STATS_COLUMNS,
    CandleUnit,
    CoinView,
    LoadState,
    SettingsView,
    UiContext,
    empty_bar_stats,
    format_percent,
)
from coin_report_trader.core.texts import ERROR_NOT_ENOUGH_CANDLES
from coin_report_trader.stats import compute_bar_stats
from coin_report_trader.ui import create_main_window

D = Decimal
FIRST = utc(2026, 1, 1, 1)
"""명세 3.5.2의 첫 봉의 시작."""
AFTER = FIRST + timedelta(hours=5)
"""다섯째 봉이 막 끝난 때."""
VALUE_COLUMNS = BAR_STATS_COLUMNS[BAR_STATS_COLUMNS.index("first_start_utc") :]


def stats(candles, now=AFTER, count=5, unit=HOUR):
    return compute_bar_stats(candles, now, market=AAA, unit=unit, count=count)


def short_header_on_screen(qapp: QApplication, view: CoinView, count: int, unit: CandleUnit) -> str:
    """처음 설정을 마친 창에 `view`를 주고 코인 칸의 첫 줄을 읽는다."""
    window = create_main_window(UiContext("0.0.1", "X", None))
    try:
        window.apply(SettingsView((AAA,), 30, True, candle_unit=unit, candle_count=count))
        window.apply(view)
        label = window.findChild(QLabel, f"statusCoinStatsHeader_{AAA}")
        assert label is not None
        return label.text()
    finally:
        window.deleteLater()


@pytest.mark.scenario("B1")
def test_b1_the_five_example_bars_give_every_value() -> None:
    result, incomplete = stats(spec_bars(FIRST))
    assert incomplete is False
    assert result.bar_count == 5
    assert (result.change, result.slope, result.vs_mean, result.up_ratio) == (
        D("0.3200"), D("0.0481"), D("0.1538"), D("0.6000"),
    )  # fmt: skip
    assert (result.max_drawdown, result.from_high) == (D("-0.1000"), D("0.0000"))
    assert (result.high, result.low, result.close_last) == (D(132), D(99), D(132))
    assert (result.first_start_utc, result.last_start_utc) == (FIRST, FIRST + timedelta(hours=4))


@pytest.mark.scenario("B2")
def test_b2_volatility_is_the_population_deviation_and_its_text_has_no_plus() -> None:
    result, _ = stats(spec_bars(FIRST))
    assert result.volatility == D("0.1535")
    assert format_percent(result.volatility, signed=False) == "15.35%"  # 늘 0 이상이라 "+"가 없다
    assert format_percent(result.slope, signed=True) == "+4.81%"


@pytest.mark.scenario("B3")
def test_b3_a_bar_still_building_is_not_used_and_is_flagged(rigs) -> None:
    first = THIS_HOUR - timedelta(hours=5)
    building = bar(THIS_HOUR, 132, 200, 132, 200)  # 지금 시각에 걸친 여섯째 봉(종가 200)
    candles = [*spec_bars(first), building]
    result, incomplete = stats(candles, now=NOW)
    assert dataclasses.replace(result) == stats(spec_bars(first), now=NOW)[0]  # B1과 같다
    assert incomplete is True
    # 자료 스레드가 켤 때 보내는 `CoinView`(처음 설정 전이라 받기는 하지 않는다). 저장하는 봉 개수는
    # 100, 200, 500 가운데 하나라 100으로 두고, 여섯째 봉이 셈에 들지 않는지를 쓴 봉 수로 본다.
    rig = rigs(done=False, count=100, candles=candles)
    rig.start()
    (view,) = rig.sink.coin(AAA)
    assert view.last_incomplete is True
    assert view.stats is not None
    assert view.stats.bar_count == 5
    assert view.state is LoadState.NO_DATA  # 완성된 봉 5개 < 고른 개수 100
    assert rig.quote.requests == []


@pytest.mark.scenario("B4")
def test_b4_gaps_are_not_filled_and_too_few_bars_are_not_computed(qapp: QApplication) -> None:
    starts = [utc(2026, 1, 1, hour) for hour in (10, 12, 13, 15)]  # 11:00과 14:00이 비었다
    candles = [bar(start, 100, 110, 90, 100 + i) for i, start in enumerate(starts)]
    now = utc(2026, 1, 1, 16)
    four, _ = stats(candles, now=now, count=4)
    assert four.bar_count == 4
    assert (four.first_start_utc, four.last_start_utc) == (starts[0], starts[-1])
    assert four.change == D("0.0300")  # 빈 봉을 채우지 않고 있는 넷으로 셈한다(103 ÷ 100 − 1)
    short, _ = stats(candles, now=now, count=200)
    assert short.bar_count == 4
    assert all(getattr(short, column) is None for column in VALUE_COLUMNS)
    view = CoinView(AAA, LoadState.NO_DATA, fetched_at=now, stats=short, as_of=now)
    text = short_header_on_screen(qapp, view, 200, HOUR)
    assert text == "60분봉 봉이 모자람(완성된 봉 4개 / 고른 개수 200개)"


@pytest.mark.scenario("B5")
def test_b5_one_complete_bar_for_a_hundred_is_short_and_makes_one_a1(rigs) -> None:
    one = bar(THIS_HOUR - timedelta(hours=1), 100, 110, 90, 105)
    result, _ = stats([one], now=NOW, count=100)
    assert result.bar_count == 1
    assert all(getattr(result, column) is None for column in VALUE_COLUMNS)
    rig = rigs(count=100)
    rig.quote.set_candles(AAA, HOUR, [one])
    rig.start()
    last = rig.sink.coin(AAA)[-1]
    assert last.state is LoadState.NO_DATA
    assert last.stats is not None
    assert last.stats.bar_count == 1
    assert last.fetched_at == NOW  # "봉이 모자람"도 받기는 성공이다
    (alert,) = rig.alerts()
    assert alert.code == "A-1"
    assert alert.detail is not None
    assert alert.detail.error_name == ERROR_NOT_ENOUGH_CANDLES
    assert alert.detail.content == (
        "완성된 봉이 고른 개수(100개)보다 적어 봉 통계를 셈하지 못했습니다(완성된 봉 1개)."
    )
    # 따로: 명세 3.5.2의 다섯 봉을 N 6으로 셈하면 봉이 모자람이고 `bar_count`는 5다.
    six, _ = stats(spec_bars(FIRST), count=6)
    assert six.bar_count == 5
    assert all(getattr(six, column) is None for column in VALUE_COLUMNS)


@pytest.mark.scenario("B6")
def test_b6_equal_bars_give_zero_for_every_ratio() -> None:
    candles = [bar(FIRST + timedelta(hours=i), 100, 100, 100, 100) for i in range(3)]
    result, _ = stats(candles, now=FIRST + timedelta(hours=3), count=3)
    ratios = ("change", "slope", "vs_mean", "up_ratio", "volatility", "max_drawdown", "from_high")
    assert {name: getattr(result, name) for name in ratios} == dict.fromkeys(ratios, D("0.0000"))


@pytest.mark.scenario("B7")
def test_b7_only_the_latest_count_bars_are_used() -> None:
    early = [bar(FIRST - timedelta(hours=2 - i), 50, 50, 50, 50) for i in range(2)]
    result, _ = stats([*early, *spec_bars(FIRST)])
    assert result == stats(spec_bars(FIRST))[0]  # B1과 같다
    assert result.first_start_utc == FIRST


@pytest.mark.scenario("B32")
def test_b32_the_latest_hundred_of_a_hundred_and_fifty_are_used() -> None:
    candles = [bar(FIRST + timedelta(hours=i), 100, 110, 90, 100 + i) for i in range(150)]
    result, _ = stats(candles, now=FIRST + timedelta(hours=150), count=100)
    assert result.bar_count == 100
    assert result.first_start_utc == candles[50].start  # 51번째 봉
    assert result.last_start_utc == candles[-1].start
    assert result.change == D("1.4900")  # 마지막 종가 249 ÷ 51번째 봉의 시가 100 − 1


@pytest.mark.scenario("B43")
def test_b43_the_columns_and_the_empty_value() -> None:
    assert BAR_STATS_COLUMNS == (
        "market", "unit", "requested_count", "bar_count", "first_start_utc", "last_start_utc",
        "close_last", "change", "slope", "vs_mean", "up_ratio", "volatility", "max_drawdown",
        "from_high", "high", "low",
    )  # fmt: skip
    empty = empty_bar_stats("KRW-AAA", CandleUnit.MINUTE_60, 200)
    assert (empty.market, empty.unit, empty.requested_count, empty.bar_count) == (
        "KRW-AAA", CandleUnit.MINUTE_60, 200, 0,
    )  # fmt: skip
    assert [getattr(empty, column) for column in BAR_STATS_COLUMNS[4:]] == [None] * 12
