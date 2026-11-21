"""가짜 공개 시세(`tests/fakes/quote.py`)의 시험(M1 명세 3.13).

뒤 번호(19번의 조립, 29번의 수용 시험)가 이 가짜를 고치지 않고 쓰므로, 약속(봉 고르기, 결과 정하기,
기록)을 여기서 확인한다.
"""

import itertools
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fakes.clock import FakeClock
from fakes.quote import (
    CANDLES,
    MARKETS,
    OK,
    FakeQuoteSource,
    QuoteRequest,
    make_candles,
    with_close,
)

from coin_report_trader.core import CandleUnit, Market, QuoteError, QuoteSource
from coin_report_trader.core.texts import ERROR_BLOCKED, ERROR_NETWORK, ERROR_TIMEOUT

M = "KRW-AAA"
DAY = CandleUnit.DAY
HOUR = CandleUnit.MINUTE_60
TODAY = datetime(2026, 1, 5, tzinfo=UTC)


def test_is_a_quote_source() -> None:
    assert isinstance(FakeQuoteSource(), QuoteSource)


# --- 돌려주는 것 ----------------------------------------------------------------------------


def test_markets_are_krw_only_in_the_given_order() -> None:
    source = FakeQuoteSource(
        markets=[
            Market("KRW-BBB", "나나", True),
            Market("BTC-AAA", "가가", False),
            Market("KRW-AAA", "가가", False),
        ]
    )
    assert source.list_markets() == (
        Market("KRW-BBB", "나나", True),
        Market("KRW-AAA", "가가", False),
    )
    source.set_markets([])
    assert source.list_markets() == ()


def test_candles_before_to_newest_first_at_most_count() -> None:
    candles = make_candles(M, DAY, 10, last_start=TODAY)
    source = FakeQuoteSource(candles=candles)
    assert source.get_candles(M, DAY, to=None, count=3) == tuple(reversed(candles[-3:]))
    assert source.get_candles(M, DAY, to=candles[5].start, count=2) == (candles[4], candles[3])
    assert source.get_candles(M, DAY, to=candles[2].start, count=200) == (candles[1], candles[0])
    assert source.get_candles(M, DAY, to=candles[0].start, count=5) == ()
    assert source.get_candles("KRW-ZZZ", DAY, to=None, count=5) == ()
    assert source.get_candles(M, HOUR, to=None, count=5) == ()


@pytest.mark.parametrize("count", [0, 201, -1, True, 1.0])
def test_bad_count_is_rejected_and_not_recorded(count: object) -> None:
    source = FakeQuoteSource()
    with pytest.raises(ValueError, match="count"):
        source.get_candles(M, DAY, to=None, count=count)  # type: ignore[arg-type]
    assert source.requests == []


def test_to_needs_a_time_zone() -> None:
    source = FakeQuoteSource()
    with pytest.raises(ValueError, match="to"):
        source.get_candles(M, DAY, to=datetime(2026, 1, 5), count=1)


def test_set_candles_replaces_and_put_candles_adds_or_replaces() -> None:
    first = make_candles(M, DAY, 5, last_start=TODAY)
    source = FakeQuoteSource(candles=first)
    changed = with_close(first[-1], Decimal(1))
    newer = make_candles(M, DAY, 3, last_start=TODAY + timedelta(days=2))[1:]
    source.put_candles(changed, *newer)
    assert source.candles(M, DAY) == (*first[:-1], changed, *newer)
    source.set_candles(M, DAY, first[:2])
    assert source.candles(M, DAY) == first[:2]
    with pytest.raises(ValueError, match="set_candles"):
        source.set_candles(M, HOUR, first)
    with pytest.raises(ValueError, match="set_candles"):
        source.set_candles("KRW-BBB", DAY, first)


def test_make_candles_are_consecutive_made_up_numbers() -> None:
    days = make_candles(M, DAY, 450, last_start=TODAY)
    hours = make_candles(M, HOUR, 3, last_start=datetime(2026, 1, 5, 3, tzinfo=UTC))
    assert len(days) == 450
    assert days[-1].start == TODAY
    assert all(b.start - a.start == timedelta(days=1) for a, b in itertools.pairwise(days))
    assert [c.start.hour for c in hours] == [1, 2, 3]
    for candle in days:
        assert candle.low <= min(candle.open, candle.close) <= max(candle.open, candle.close)
        assert max(candle.open, candle.close) <= candle.high
        assert all(
            type(v) is Decimal
            for v in (
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume,
                candle.value,
            )
        )


@pytest.mark.parametrize(
    ("unit", "step"),
    [
        (CandleUnit.MINUTE_30, timedelta(minutes=30)),
        (CandleUnit.MINUTE_60, timedelta(hours=1)),
        (CandleUnit.MINUTE_240, timedelta(hours=4)),
        (CandleUnit.DAY, timedelta(days=1)),
    ],
)
def test_four_units_are_kept_apart(unit: CandleUnit, step: timedelta) -> None:
    # 가짜 시세의 봉 단위 넷(M1 명세 3.13). 단위마다 따로 두고 따로 돌려준다.
    groups = {u: make_candles(M, u, 3, last_start=TODAY) for u in CandleUnit}
    source = FakeQuoteSource(candles=[c for group in groups.values() for c in group])
    candles = groups[unit]
    assert [b.start - a.start for a, b in itertools.pairwise(candles)] == [step, step]
    assert candles[-1].start == TODAY
    assert source.candles(M, unit) == candles
    assert source.get_candles(M, unit, to=None, count=200) == tuple(reversed(candles))
    assert source.get_candles(M, unit, to=TODAY, count=1) == (candles[1],)
    assert [r.unit for r in source.candle_requests(M, unit)] == [unit, unit]


def test_script_can_aim_at_one_of_the_new_units() -> None:
    source = FakeQuoteSource(candles=make_candles(M, CandleUnit.MINUTE_30, 2, last_start=TODAY))
    source.script(QuoteError(ERROR_NETWORK), unit=CandleUnit.MINUTE_240)
    assert len(source.get_candles(M, CandleUnit.MINUTE_30, to=None, count=5)) == 2
    with pytest.raises(QuoteError):
        source.get_candles(M, CandleUnit.MINUTE_240, to=None, count=5)


# --- 결과 정하기 ----------------------------------------------------------------------------


def test_script_outcomes_come_in_order_then_success() -> None:
    source = FakeQuoteSource(candles=make_candles(M, DAY, 3, last_start=TODAY))
    source.script(
        QuoteError(ERROR_NETWORK), QuoteError(ERROR_NETWORK), OK, QuoteError(ERROR_TIMEOUT)
    )
    results = []
    for _ in range(5):
        try:
            results.append(len(source.get_candles(M, DAY, to=None, count=10)))
        except QuoteError as exc:
            results.append(exc.error_name)
    assert results == [ERROR_NETWORK, ERROR_NETWORK, 3, ERROR_TIMEOUT, 3]


def test_script_filters_by_kind_market_and_unit() -> None:
    source = FakeQuoteSource(
        candles=[
            *make_candles(M, DAY, 2, last_start=TODAY),
            *make_candles(M, HOUR, 2, last_start=TODAY),
            *make_candles("KRW-BBB", DAY, 2, last_start=TODAY),
        ]
    )
    source.script(QuoteError(ERROR_BLOCKED, 120.0), kind=MARKETS)
    source.script(QuoteError(ERROR_NETWORK), market=M, unit=DAY)

    assert len(source.get_candles(M, HOUR, to=None, count=5)) == 2
    assert len(source.get_candles("KRW-BBB", DAY, to=None, count=5)) == 2
    with pytest.raises(QuoteError) as day_error:
        source.get_candles(M, DAY, to=None, count=5)
    assert day_error.value.error_name == ERROR_NETWORK
    with pytest.raises(QuoteError) as markets_error:
        source.list_markets()
    assert (markets_error.value.error_name, markets_error.value.retry_after) == (
        ERROR_BLOCKED,
        120.0,
    )
    # 다 쓴 뒤에는 정상이다.
    assert source.list_markets() == ()
    assert len(source.get_candles(M, DAY, to=None, count=5)) == 2


def test_first_matching_line_with_outcomes_left_is_used() -> None:
    source = FakeQuoteSource()
    source.script(QuoteError(ERROR_NETWORK), kind=CANDLES)
    source.script(QuoteError(ERROR_TIMEOUT), QuoteError(ERROR_BLOCKED))
    names = []
    for call in (
        lambda: source.get_candles(M, DAY, to=None, count=1),
        source.list_markets,
        source.list_markets,
        source.list_markets,
    ):
        try:
            call()
            names.append(None)
        except QuoteError as exc:
            names.append(exc.error_name)
    assert names == [ERROR_NETWORK, ERROR_TIMEOUT, ERROR_BLOCKED, None]


def test_second_request_only_fails() -> None:
    # B35의 꼴: 일봉 처음 받기의 둘째 요청에서만 실패.
    source = FakeQuoteSource(candles=make_candles(M, DAY, 450, last_start=TODAY))
    source.script(OK, QuoteError(ERROR_NETWORK), kind=CANDLES, market=M, unit=DAY)
    source.get_candles(M, DAY, to=None, count=200)
    with pytest.raises(QuoteError):
        source.get_candles(M, DAY, to=TODAY, count=200)
    assert len(source.get_candles(M, DAY, to=TODAY, count=200)) == 200


def test_the_same_error_object_can_be_scripted_many_times() -> None:
    error = QuoteError(ERROR_NETWORK)
    source = FakeQuoteSource()
    source.script(error, error)
    for _ in range(2):
        with pytest.raises(QuoteError) as caught:
            source.list_markets()
        assert caught.value is not error
        assert caught.value.error_name == ERROR_NETWORK


def test_clear_script_drops_what_is_left() -> None:
    source = FakeQuoteSource()
    source.script(QuoteError(ERROR_NETWORK))
    source.clear_script()
    assert source.list_markets() == ()


def test_script_checks_its_arguments() -> None:
    source = FakeQuoteSource()
    with pytest.raises(ValueError, match="종류"):
        source.script(OK, kind="orders")
    with pytest.raises(TypeError):
        source.script(ValueError("x"))  # type: ignore[arg-type]


# --- 기록 -----------------------------------------------------------------------------------


def test_requests_are_recorded_with_the_clock() -> None:
    clock = FakeClock()
    source = FakeQuoteSource(candles=make_candles(M, DAY, 3, last_start=TODAY), clock=clock)
    source.script(OK, QuoteError(ERROR_NETWORK), kind=CANDLES)
    first_monotonic, first_now = clock.monotonic(), clock.now()
    source.list_markets()
    clock.advance(0.5)
    source.get_candles(M, DAY, to=None, count=2)
    clock.advance(0.25)
    with pytest.raises(QuoteError):
        source.get_candles(M, HOUR, to=TODAY, count=7)

    assert source.requests == [
        QuoteRequest(MARKETS, None, None, None, None, None, first_monotonic, first_now),
        QuoteRequest(
            CANDLES,
            M,
            DAY,
            None,
            2,
            None,
            first_monotonic + 0.5,
            first_now + timedelta(seconds=0.5),
        ),
        QuoteRequest(
            CANDLES,
            M,
            HOUR,
            TODAY,
            7,
            ERROR_NETWORK,
            first_monotonic + 0.75,
            first_now + timedelta(seconds=0.75),
        ),
    ]
    assert [r.unit for r in source.candle_requests()] == [DAY, HOUR]
    assert len(source.candle_requests(M, DAY)) == 1
    assert len(source.candle_requests("KRW-BBB")) == 0
    assert len(source.market_requests()) == 1


def test_without_a_clock_times_are_none() -> None:
    source = FakeQuoteSource()
    source.list_markets()
    assert (source.requests[0].monotonic, source.requests[0].now) == (None, None)


def test_threads_can_share_the_fake() -> None:
    source = FakeQuoteSource(candles=make_candles(M, DAY, 50, last_start=TODAY))

    def work() -> None:
        for _ in range(50):
            source.get_candles(M, DAY, to=None, count=10)
            source.list_markets()

    threads = [threading.Thread(target=work) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(source.requests) == 400
