"""M1 명세 6절의 받기 시나리오: B8, B9, B10, B31, B35, B42, B44(2의 1, 3.5.3, 3.5.4, 3.10).

자료 스레드와 보내기 스레드를 가짜 시세, 가짜 시계로 돌린다(`acceptance.m1_support`). B10은
`QuoteClient`를 직접 부른다. 시각은 가짜 시계로만 옮긴다. 숫자는 만든 값이다.
"""

from datetime import timedelta

import pytest
from acceptance.m1_support import AAA, DAY, HOUR, MIN30, NOW, THIS_HOUR, utc
from fakes.clock import FakeClock
from fakes.quote import OK, FakeQuoteSource, make_candles

from coin_report_trader.core import CandleUnit, LoadState, QuoteError, SettingsView, UiContext
from coin_report_trader.core.texts import ERROR_NETWORK, ERROR_NOT_ENOUGH_CANDLES
from coin_report_trader.trading.quote import QuoteClient

UNITS = list(CandleUnit)


def other_unit_requests(rig, unit: CandleUnit) -> list:
    return [r for u in UNITS if u is not unit for r in rig.quote.candle_requests(unit=u)]


def seconds_to_next_round(rig) -> float:
    """다음 (라)의 때까지 남은 초(자료 스레드가 기다림에 넣은 때)."""
    due = rig.data._next_round_mono
    assert due is not None
    return due - rig.clock.monotonic()


@pytest.mark.scenario("B8")
def test_b8_a_first_fetch_of_five_hundred_takes_three_requests_of_one_unit(rigs) -> None:
    rig = rigs(count=500)
    candles = make_candles(AAA, HOUR, 600, last_start=THIS_HOUR)  # 가장 최근 봉은 미완성
    rig.quote.set_candles(AAA, HOUR, candles)
    rig.start()
    sent = rig.quote.candle_requests(AAA, HOUR)
    assert len(sent) == 3
    assert all(r.count <= 200 for r in sent)
    assert rig.stored(AAA, HOUR) == candles
    assert other_unit_requests(rig, HOUR) == []


@pytest.mark.scenario("B9")
def test_b9_a_later_fetch_takes_only_the_new_bars_and_overwrites_the_last_stored_one(rigs) -> None:
    rig = rigs(count=500)
    candles = make_candles(AAA, HOUR, 600, last_start=THIS_HOUR)
    rig.quote.set_candles(AAA, HOUR, candles)
    rig.start()  # B8
    before = len(rig.quote.candle_requests(AAA, HOUR))
    last = candles[-1]
    changed = last.__class__(**{**last.__dict__, "close": last.close + 7})
    newer = make_candles(AAA, HOUR, 3, last_start=THIS_HOUR + timedelta(hours=2))[1:]
    rig.quote.put_candles(changed, *newer)
    rig.advance(seconds_to_next_round(rig) + timedelta(hours=1).total_seconds())
    later = rig.quote.candle_requests(AAA, HOUR)[before:]
    assert len(later) == 1
    stored = rig.stored(AAA, HOUR)
    assert len(stored) == 602
    assert stored[-3] == changed  # 마지막으로 저장된 봉을 덮어썼다
    assert stored[-2:] == newer


@pytest.mark.scenario("B10")
def test_b10_twelve_requests_never_exceed_five_in_any_second() -> None:
    clock = FakeClock()
    candles = make_candles(AAA, HOUR, 2400, last_start=THIS_HOUR)
    source = FakeQuoteSource(candles=candles, clock=clock)
    client = QuoteClient(source, clock)
    client.fetch_recent(
        AAA,
        HOUR,
        count=500,
        last_start=candles[-1].start,
        first_start=candles[2199].start,
        missing=2199,  # 그 뒤의 받기 1번과 거슬러 받기 11번
    )
    times = [r.monotonic for r in source.requests]
    assert len(times) == 12
    for i in range(len(times) - 5):
        assert times[i + 5] - times[i] > 1.0, i  # 길이 1초인 어느 구간에도 5번 이하


@pytest.mark.scenario("B31")
def test_b31_a_coin_with_fewer_bars_stops_early_and_does_not_reach_back_again(rigs, qapp) -> None:
    rig = rigs(unit=MIN30, count=200)
    building = THIS_HOUR  # 03:00에 시작한 30분봉이 미완성이다
    candles = make_candles(AAA, MIN30, 150, last_start=building)
    rig.quote.set_candles(AAA, MIN30, candles)
    rig.start()
    first = rig.quote.candle_requests(AAA, MIN30)
    assert len(first) == 1  # 거래소가 요청보다 적게 주어 멈춘다(`reached_oldest`)
    assert len(rig.stored(AAA, MIN30)) == 150
    view = rig.sink.coin(AAA)[-1]
    assert view.state is LoadState.NO_DATA
    assert view.stats is not None
    assert view.stats.bar_count == 149
    assert header_on_screen(qapp, view, MIN30, 200) == (
        "30분봉 봉이 모자람(완성된 봉 149개 / 고른 개수 200개)"
    )
    rig.advance(seconds_to_next_round(rig))
    later = rig.quote.candle_requests(AAA, MIN30)[len(first) :]
    assert len(later) == 1  # 그 뒤의 받기뿐이고 거슬러 받기 요청이 없다
    assert later[0].to is None
    assert other_unit_requests(rig, MIN30) == []


def header_on_screen(qapp, view, unit, count) -> str:
    from PySide6.QtWidgets import QLabel

    from coin_report_trader.ui import create_main_window

    window = create_main_window(UiContext("0.0.1", "X", None))
    try:
        window.apply(SettingsView((AAA,), 30, True, candle_unit=unit, candle_count=count))
        window.apply(view)
        label = window.findChild(QLabel, f"statusCoinStatsHeader_{AAA}")
        assert label is not None
        return label.text()
    finally:
        window.deleteLater()


@pytest.mark.scenario("B35")
def test_b35_a_failed_second_request_saves_nothing_and_the_next_fetch_starts_over(rigs) -> None:
    rig = rigs(count=500)
    candles = make_candles(AAA, HOUR, 600, last_start=THIS_HOUR)
    rig.quote.set_candles(AAA, HOUR, candles)
    rig.quote.script(OK, QuoteError(ERROR_NETWORK), kind="candles", market=AAA, unit=HOUR)
    rig.start()
    assert rig.stored(AAA, HOUR) == ()
    view = rig.sink.coin(AAA)[-1]
    assert (view.state, view.error_name, view.fetched_at) == (LoadState.FAILED, ERROR_NETWORK, None)
    (alert,) = rig.alerts()
    assert alert.code == "A-1"
    assert alert.detail is not None
    assert (alert.detail.error_name, alert.detail.content) == (ERROR_NETWORK, "실패한 봉: 60분봉")
    assert rig.fetched_at(AAA) is None
    first = len(rig.quote.candle_requests(AAA, HOUR))
    rig.advance(seconds_to_next_round(rig))
    again = rig.quote.candle_requests(AAA, HOUR)[first:]
    assert again[0].to is None  # 다시 처음 받기(가장 최근부터)
    assert len(again) == 3
    assert len(rig.stored(AAA, HOUR)) == 600


@pytest.mark.scenario("B42")
def test_b42_the_next_fetch_is_when_the_bar_ends_plus_ten_seconds(rigs) -> None:
    # 60분봉: 저장된 마지막 봉의 시작 10:00, 지금 10:20 → 11:00:10.
    clock = FakeClock(utc(2026, 1, 5, 10, 20))
    quote = FakeQuoteSource(clock=clock)
    stored = make_candles(AAA, HOUR, 150, last_start=utc(2026, 1, 5, 10))
    quote.set_candles(AAA, HOUR, stored)
    quote.script(OK, QuoteError(ERROR_NETWORK), kind="candles", market=AAA, unit=HOUR)
    rig = rigs(clock=clock, quote=quote, candles=stored[:-1])
    rig.start()
    due = utc(2026, 1, 5, 11, 0, 10)
    assert rig.clock.monotonic() + (due - rig.clock.now()).total_seconds() == pytest.approx(
        rig.data._next_round_mono
    )
    # (다) 받기("다시 시도")는 (라)의 때를 바꾸지 않는다. 그 받기의 A-1의 "그 뒤에 한 일"도 그 때다.
    rig.data.retry_candles()
    rig.settle()
    (alert,) = rig.alerts()
    assert alert.detail is not None
    assert alert.detail.error_name == ERROR_NETWORK
    assert "20:00" in alert.detail.after  # 11:00:10 UTC는 한국 20:00
    count = len(rig.quote.candle_requests())
    rig.advance((due - rig.clock.now()).total_seconds() - 1)
    assert len(rig.quote.candle_requests()) == count  # 그때가 오기 전에는 받지 않는다
    rig.advance(1)
    assert len(rig.quote.candle_requests()) == count + 1


@pytest.mark.scenario("B42")
def test_b42_a_day_unit_with_nothing_stored_waits_for_the_next_utc_midnight(rigs) -> None:
    clock = FakeClock(utc(2026, 1, 5, 5))
    rig = rigs(clock=clock, unit=DAY, count=100, quote=FakeQuoteSource(clock=clock))
    rig.start()  # 시세에 일봉이 없어 저장된 봉이 없다
    assert rig.stored(AAA, DAY) == ()
    due = utc(2026, 1, 6, 0, 0, 10)
    assert rig.clock.monotonic() + (due - rig.clock.now()).total_seconds() == pytest.approx(
        rig.data._next_round_mono
    )
    (alert,) = rig.alerts()  # 봉이 모자람: 그 뒤에 한 일의 시각도 그 때다(한국 다음 날 09:00)
    assert alert.detail is not None
    assert alert.detail.error_name == ERROR_NOT_ENOUGH_CANDLES
    assert "2026-01-06 09:00" in alert.detail.after


@pytest.mark.scenario("B44")
def test_b44_raising_the_count_reaches_back_for_the_missing_bars(rigs) -> None:
    candles = make_candles(AAA, HOUR, 521, last_start=THIS_HOUR)  # 마지막 하나는 미완성
    rig = rigs(count=100, candles=candles[400:])  # 저장된 완성된 봉 120개
    rig.quote.set_candles(AAA, HOUR, candles)
    rig.start()
    fetched = rig.fetched_at(AAA)
    assert fetched == NOW
    stored_before = len(rig.stored(AAA, HOUR))
    rig.data.save_settings((AAA,), 30, first_setup=False, candle_unit=HOUR, candle_count=500)
    rig.settle()
    assert rig.fetched_at(AAA) == fetched  # 봉과 `fetched_at`은 지워지지 않는다
    stored = rig.stored(AAA, HOUR)
    assert len(stored) >= stored_before
    complete = [c for c in stored if c.start + timedelta(hours=1) <= rig.clock.now()]
    assert len(complete) >= 500
    reach_back = [r for r in rig.quote.candle_requests(AAA, HOUR) if r.to is not None]
    assert sum(r.count for r in reach_back) >= 380  # `missing` 380을 거슬러 받았다
    last = rig.sink.coin(AAA)[-1]
    assert last.stats is not None
    assert last.stats.bar_count == 500
