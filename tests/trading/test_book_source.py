"""공개 시세 더하기(M3 명세 3.6)의 시험: 호가, 호가 단위, 1분봉.

- 진짜 `HttpQuoteSource`의 `BookSource`는 소켓을 열지 않고 `httpx.MockTransport`로 본다.
- `QuoteClient`의 세 읽기는 가짜 시세와 가짜 시계로 본다.
- 응답의 꼴(필드 이름)은 공식 문서의 뜻을 따르고, 값은 모두 만든 숫자다.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from fakes.clock import FakeClock
from fakes.quote import BOOK, MINUTES, OK, TICK, FakeQuoteSource, make_minutes

from coin_report_trader.core import (
    BookSource,
    CandleUnit,
    Market,
    MinuteCandle,
    QuoteError,
)
from coin_report_trader.core.texts import (
    ERROR_BAD_RESPONSE,
    ERROR_BLOCKED,
    ERROR_NETWORK,
    ERROR_RATE_LIMIT,
    ERROR_TIMEOUT,
)
from coin_report_trader.trading.quote import HttpQuoteSource, QuoteClient

BASE = "https://quote.test"
M = "KRW-AAA"
NOW = datetime(2026, 1, 5, 3, 20, tzinfo=UTC)


class Recorder:
    def __init__(self, respond: Callable[[httpx.Request], httpx.Response]) -> None:
        self.respond = respond
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)


def make_source(text: str, status: int = 200) -> tuple[HttpQuoteSource, Recorder]:
    recorder = Recorder(lambda request: httpx.Response(status, content=text.encode()))
    return HttpQuoteSource(transport=httpx.MockTransport(recorder), base_url=BASE), recorder


def book_json(market: str = M, ask: str = "1001", bid: str = "999") -> str:
    units = [
        {"ask_price": "__ASK__", "bid_price": "__BID__", "ask_size": "1.5", "bid_size": "2.5"},
        {"ask_price": 1002, "bid_price": 998, "ask_size": 3, "bid_size": 4},
    ]
    text = json.dumps(
        [{"market": market, "timestamp": 1, "total_ask_size": 4.5, "orderbook_units": units}]
    )
    return text.replace('"__ASK__"', ask).replace('"__BID__"', bid)


def tick_json(market: str = M, tick: str = "0.1") -> str:
    return json.dumps([{"market": market, "tick_size": "__T__"}]).replace('"__T__"', tick)


def minute_json(market: str, start: str, *, high: str = "1003", low: str = "997") -> str:
    return (
        f'{{"market": "{market}", "candle_date_time_utc": "{start}", '
        f'"candle_date_time_kst": "{start}", "opening_price": 1000, "high_price": {high}, '
        f'"low_price": {low}, "trade_price": 1001.5, "timestamp": 1, '
        f'"candle_acc_trade_price": 1000, "candle_acc_trade_volume": 1, "unit": 1}}'
    )


# --- 진짜: 호가 ------------------------------------------------------------------------------


def test_http_source_is_a_book_source() -> None:
    source, _ = make_source("[]")
    assert isinstance(source, BookSource)
    source.close()


def test_best_prices_are_the_first_line_of_the_book() -> None:
    source, recorder = make_source(book_json(ask="1001.5", bid='"999"'))
    ask, bid = source.get_best_prices(M)
    assert (ask, bid) == (Decimal("1001.5"), Decimal(999))
    assert type(ask) is Decimal
    assert type(bid) is Decimal
    request = recorder.requests[0]
    assert (request.method, request.url.path) == ("GET", "/v1/orderbook")
    assert dict(request.url.params) == {"markets": M}
    assert len(recorder.requests) == 1


@pytest.mark.parametrize(
    "text",
    [
        "",
        "{}",
        "[]",
        book_json() + "x",
        "[" + book_json()[1:-1] + "," + book_json()[1:-1] + "]",  # 항목이 둘
        book_json(market="KRW-BBB"),
        book_json(ask="0"),
        book_json(bid="-1"),
        book_json(ask="null"),
        book_json(ask='"NaN"'),
        json.dumps([{"market": M, "orderbook_units": []}]),
        json.dumps([{"market": M}]),
        json.dumps([{"market": M, "orderbook_units": [{"ask_price": "1"}]}]),
    ],
)
def test_unreadable_book_is_bad_response(text: str) -> None:
    source, _ = make_source(text)
    with pytest.raises(QuoteError) as caught:
        source.get_best_prices(M)
    assert caught.value.error_name == ERROR_BAD_RESPONSE
    assert caught.value.__context__ is None


# --- 진짜: 호가 단위 -------------------------------------------------------------------------


def test_tick_size_is_read_from_the_instruments() -> None:
    source, recorder = make_source(tick_json(tick='"0.1"'))
    assert source.get_tick_size(M) == Decimal("0.1")
    request = recorder.requests[0]
    assert (request.method, request.url.path) == ("GET", "/v1/orderbook/instruments")
    assert dict(request.url.params) == {"markets": M}


@pytest.mark.parametrize(
    "text",
    [
        "[]",
        tick_json(tick="0"),
        tick_json(tick='"-0.1"'),
        tick_json(tick="true"),
        tick_json(market="KRW-BBB"),
        json.dumps([{"market": M}]),
    ],
)
def test_unreadable_tick_size_is_bad_response(text: str) -> None:
    source, _ = make_source(text)
    with pytest.raises(QuoteError) as caught:
        source.get_tick_size(M)
    assert caught.value.error_name == ERROR_BAD_RESPONSE


# --- 진짜: 1분봉 ----------------------------------------------------------------------------


def test_minute_candles_come_newest_first_before_to() -> None:
    starts = [
        "2026-01-05T03:00:00",
        "2026-01-05T03:02:00",
        "2026-01-05T03:01:00",
        "2026-01-05T03:03:00",
        "2026-01-05T03:02:00",
    ]
    text = "[" + ",".join(minute_json(M, s) for s in starts) + "]"
    source, recorder = make_source(text)
    to = datetime(2026, 1, 5, 3, 3, tzinfo=UTC)
    candles = source.get_minute_candles(M, to=to, count=2)
    assert candles == (
        MinuteCandle(
            M,
            datetime(2026, 1, 5, 3, 2, tzinfo=UTC),
            Decimal(1003),
            Decimal(997),
            Decimal("1001.5"),
        ),
        MinuteCandle(
            M,
            datetime(2026, 1, 5, 3, 1, tzinfo=UTC),
            Decimal(1003),
            Decimal(997),
            Decimal("1001.5"),
        ),
    )
    request = recorder.requests[0]
    assert request.url.path == "/v1/candles/minutes/1"
    assert dict(request.url.params) == {"market": M, "to": "2026-01-05T03:03:00Z", "count": "2"}


def test_minute_candles_without_to() -> None:
    source, recorder = make_source("[]")
    assert source.get_minute_candles(M, to=None, count=200) == ()
    assert dict(recorder.requests[0].url.params) == {"market": M, "count": "200"}


@pytest.mark.parametrize(
    "text",
    [
        "{}",
        "[" + minute_json("KRW-BBB", "2026-01-05T03:00:00") + "]",
        "[" + minute_json(M, "2026-01-05T03:00:00", high='"x"') + "]",
        '[{"market": "KRW-AAA", "candle_date_time_utc": "2026-01-05T03:00:00"}]',
    ],
)
def test_unreadable_minute_candles_are_bad_response(text: str) -> None:
    source, _ = make_source(text)
    with pytest.raises(QuoteError) as caught:
        source.get_minute_candles(M, to=None, count=10)
    assert caught.value.error_name == ERROR_BAD_RESPONSE


@pytest.mark.parametrize(
    ("call", "error"),
    [
        (lambda s: s.get_best_prices("krw-aaa"), ValueError),
        (lambda s: s.get_tick_size("AAA"), ValueError),
        (lambda s: s.get_minute_candles("AAA", to=None, count=1), ValueError),
        (lambda s: s.get_minute_candles(M, to=None, count=0), ValueError),
        (lambda s: s.get_minute_candles(M, to=None, count=201), ValueError),
        (lambda s: s.get_minute_candles(M, to=datetime(2026, 1, 5), count=1), ValueError),
    ],
)
def test_bad_arguments_are_refused_before_sending(call: Callable, error: type) -> None:
    source, recorder = make_source("[]")
    with pytest.raises(error):
        call(source)
    assert recorder.requests == []


@pytest.mark.parametrize(
    ("status", "name"),
    [(418, ERROR_BLOCKED), (429, ERROR_RATE_LIMIT), (500, "서버 오류")],
)
def test_book_requests_share_the_error_names(status: int, name: str) -> None:
    for call in (
        lambda s: s.get_best_prices(M),
        lambda s: s.get_tick_size(M),
        lambda s: s.get_minute_candles(M, to=None, count=1),
    ):
        source, recorder = make_source('{"error": {"name": "made_up"}}', status)
        with pytest.raises(QuoteError) as caught:
            call(source)
        assert caught.value.error_name == name
        assert len(recorder.requests) == 1


# --- 감싸는 부품: QuoteClient ---------------------------------------------------------------


def setup(**prices: Decimal) -> tuple[QuoteClient, FakeQuoteSource, FakeClock]:
    clock = FakeClock(NOW)
    source = FakeQuoteSource(prices={M: Decimal(1000), **prices}, clock=clock)
    return QuoteClient(source, clock), source, clock


def test_read_best_prices_and_tick_size_are_one_request_each() -> None:
    client, source, _ = setup()
    source.set_book(M, Decimal(1002), Decimal(998))
    source.set_tick_size(M, Decimal("0.1"))
    assert client.read_best_prices(M) == (Decimal(1002), Decimal(998))
    assert client.read_tick_size(M) == Decimal("0.1")
    assert [(r.kind, r.market) for r in source.requests] == [(BOOK, M), (TICK, M)]


def test_read_through_the_real_source() -> None:
    source, recorder = make_source(tick_json(tick="1"))
    client = QuoteClient(source, FakeClock(NOW))
    assert client.read_tick_size(M) == Decimal(1)
    assert len(recorder.requests) == 1


def test_fetch_minutes_takes_since_inclusive_until_exclusive_in_order() -> None:
    client, source, _ = setup()
    candles = make_minutes(M, 30, last_start=datetime(2026, 1, 5, 3, 19, tzinfo=UTC))
    source.put_minutes(*candles)
    since = datetime(2026, 1, 5, 3, 5, tzinfo=UTC)
    until = datetime(2026, 1, 5, 3, 15, tzinfo=UTC)
    got = client.fetch_minutes(M, since=since, until=until)
    assert got == tuple(c for c in candles if since <= c.start < until)
    assert [c.start.minute for c in got] == list(range(5, 15))
    requests = source.book_requests(MINUTES)
    assert [(r.to, r.count) for r in requests] == [(until, 10)]


def test_fetch_minutes_splits_more_than_200() -> None:
    client, source, _ = setup()
    last = datetime(2026, 1, 5, 3, 19, tzinfo=UTC)
    candles = make_minutes(M, 600, last_start=last)
    source.put_minutes(*candles)
    since = candles[100].start
    until = last + timedelta(minutes=1)
    got = client.fetch_minutes(M, since=since, until=until)
    assert got == candles[100:]
    requests = source.book_requests(MINUTES)
    assert [r.count for r in requests] == [200, 200, 100]
    assert [r.to for r in requests] == [until, candles[400].start, candles[200].start]
    assert all(r.count <= 200 for r in requests)


def test_fetch_minutes_with_gaps_and_with_nothing() -> None:
    client, source, _ = setup()
    candles = make_minutes(M, 10, last_start=datetime(2026, 1, 5, 3, 9, tzinfo=UTC))
    kept = candles[::3]  # 체결이 없던 분은 봉이 없다
    source.put_minutes(*kept)
    got = client.fetch_minutes(
        M,
        since=datetime(2026, 1, 5, 3, 0, tzinfo=UTC),
        until=datetime(2026, 1, 5, 3, 10, tzinfo=UTC),
    )
    assert got == kept
    # 1분봉이 하나도 없으면 빈 튜플이다(정하지 않은 코인의 기본값).
    assert client.fetch_minutes("KRW-BBB", since=since_of(NOW), until=NOW) == ()


def since_of(moment: datetime) -> datetime:
    return moment - timedelta(minutes=5)


def test_fetch_minutes_with_an_empty_range_sends_nothing() -> None:
    client, source, _ = setup()
    assert client.fetch_minutes(M, since=NOW, until=NOW) == ()
    assert client.fetch_minutes(M, since=NOW, until=NOW - timedelta(minutes=1)) == ()
    assert source.requests == []


def test_fetch_minutes_needs_time_zones() -> None:
    client, source, _ = setup()
    with pytest.raises(ValueError, match="since"):
        client.fetch_minutes(M, since=datetime(2026, 1, 5), until=NOW)
    with pytest.raises(ValueError, match="until"):
        client.fetch_minutes(M, since=NOW, until=datetime(2026, 1, 5))
    assert source.requests == []


def test_fetch_minutes_failure_in_the_middle_returns_nothing() -> None:
    client, source, _ = setup()
    candles = make_minutes(M, 600, last_start=datetime(2026, 1, 5, 3, 19, tzinfo=UTC))
    source.put_minutes(*candles)
    source.script(OK, QuoteError(ERROR_NETWORK), kind=MINUTES)
    with pytest.raises(QuoteError) as caught:
        client.fetch_minutes(M, since=candles[0].start, until=NOW)
    assert caught.value.error_name == ERROR_NETWORK
    assert len(source.requests) == 2


@pytest.mark.parametrize("kind", [BOOK, TICK, MINUTES])
def test_errors_are_raised_as_is_without_retrying(kind: str) -> None:
    client, source, clock = setup()
    source.script(QuoteError(ERROR_RATE_LIMIT, 1.0), kind=kind)
    call = {
        BOOK: lambda: client.read_best_prices(M),
        TICK: lambda: client.read_tick_size(M),
        MINUTES: lambda: client.fetch_minutes(M, since=since_of(NOW), until=NOW),
    }[kind]
    with pytest.raises(QuoteError) as caught:
        call()
    assert (caught.value.error_name, caught.value.retry_after) == (ERROR_RATE_LIMIT, 1.0)
    assert len(source.requests) == 1
    assert clock.sleeps == []


def test_the_three_reads_share_the_limit_with_the_others() -> None:
    client, source, clock = setup()
    source.put_minutes(*make_minutes(M, 3, last_start=datetime(2026, 1, 5, 3, 19, tzinfo=UTC)))
    client.list_markets()
    client.read_price(M)
    client.read_best_prices(M)
    client.read_tick_size(M)
    client.fetch_minutes(M, since=since_of(NOW), until=NOW)
    client.fetch_recent(
        M, CandleUnit.MINUTE_60, count=100, last_start=None, first_start=None, missing=0
    )  # 여섯째 요청
    times = [r.monotonic for r in source.requests]
    assert len(times) == 6
    assert times[5] - times[0] > 1.0
    assert len(clock.sleeps) == 1


class BrokenMinutes:
    """`to`를 보지 않고 늘 같은 1분봉을 주는 소스(약속을 어김)."""

    def __init__(self, candles: tuple[MinuteCandle, ...]) -> None:
        self.candles = candles
        self.calls = 0

    def list_markets(self) -> tuple[Market, ...]:
        return ()

    def get_candles(self, market, unit, *, to, count):  # type: ignore[no-untyped-def]
        return ()

    def get_best_prices(self, market):  # type: ignore[no-untyped-def]
        return Decimal(1), Decimal(1)

    def get_tick_size(self, market):  # type: ignore[no-untyped-def]
        return Decimal(1)

    def get_minute_candles(self, market, *, to, count):  # type: ignore[no-untyped-def]
        self.calls += 1
        return tuple(reversed(self.candles))[:count]


@pytest.mark.parametrize(
    "candles",
    [
        make_minutes(M, 5, last_start=NOW),  # 마지막 봉이 `until`(= `to`)에 시작한다
        make_minutes("KRW-BBB", 5, last_start=NOW - timedelta(minutes=1)),  # 다른 마켓
    ],
    ids=["at-or-after-to", "other-market"],
)
def test_a_source_breaking_the_contract_gives_bad_response(
    candles: tuple[MinuteCandle, ...],
) -> None:
    source = BrokenMinutes(candles)
    client = QuoteClient(source, FakeClock(NOW))
    with pytest.raises(QuoteError) as caught:
        client.fetch_minutes(M, since=NOW - timedelta(minutes=5), until=NOW)
    assert caught.value.error_name == ERROR_BAD_RESPONSE
    assert source.calls == 1


class QuoteOnly:
    def __init__(self) -> None:
        self.calls = 0

    def list_markets(self) -> tuple[Market, ...]:
        self.calls += 1
        return ()

    def get_candles(self, market, unit, *, to, count):  # type: ignore[no-untyped-def]
        self.calls += 1
        return ()


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.read_best_prices(M),
        lambda c: c.read_tick_size(M),
        lambda c: c.fetch_minutes(M, since=NOW - timedelta(minutes=1), until=NOW),
    ],
)
def test_the_three_reads_need_a_book_source(call: Callable) -> None:
    source = QuoteOnly()
    assert not isinstance(source, BookSource)
    clock = FakeClock(NOW)
    client = QuoteClient(source, clock)
    with pytest.raises(TypeError, match="BookSource"):
        call(client)
    assert source.calls == 0
    for _ in range(5):  # 한도에도 세지 않는다
        client.list_markets()
    assert clock.sleeps == []


def test_timeouts_stay_timeouts() -> None:
    client, source, _ = setup()
    source.script(QuoteError(ERROR_TIMEOUT), kind=TICK)
    with pytest.raises(QuoteError) as caught:
        client.read_tick_size(M)
    assert caught.value.error_name == ERROR_TIMEOUT
