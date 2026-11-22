"""현재가 읽기(M2 명세 3.6)의 시험.

- 진짜 `HttpQuoteSource.get_price`는 소켓을 열지 않고 `httpx.MockTransport`로 본다.
- `QuoteClient.read_price`는 가짜 시세와 가짜 시계로 본다.
- 응답의 꼴(필드 이름)은 옛 코드의 변환 코드에서 읽었고, 값은 모두 만든 숫자다.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from fakes.clock import FakeClock
from fakes.quote import PRICE, FakeQuoteSource, make_candles

from coin_report_trader.core import CandleUnit, Market, PriceSource, QuoteError, QuoteSource
from coin_report_trader.core.texts import (
    ERROR_BAD_RESPONSE,
    ERROR_BLOCKED,
    ERROR_MAINTENANCE,
    ERROR_NETWORK,
    ERROR_RATE_LIMIT,
    ERROR_SERVER,
    ERROR_TIMEOUT,
    error_unknown_exchange,
)
from coin_report_trader.trading.quote import HttpQuoteSource, QuoteClient

BASE = "https://quote.test"
M = "KRW-AAA"
NOW = datetime(2026, 1, 5, 3, 20, tzinfo=UTC)


def ticker_json(market: str = M, price: str = "101.5") -> str:
    """현재가 응답의 항목 하나. 숫자를 글로 직접 적어 부동소수점을 거치지 않는다."""
    return (
        f'{{"market": "{market}", "trade_price": {price}, "opening_price": 100, '
        f'"high_price": 103, "low_price": 99, "timestamp": 1}}'
    )


class Recorder:
    def __init__(self, respond: Callable[[httpx.Request], httpx.Response]) -> None:
        self.respond = respond
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)


def make_source(
    respond: Callable[[httpx.Request], httpx.Response],
) -> tuple[HttpQuoteSource, Recorder]:
    recorder = Recorder(respond)
    return HttpQuoteSource(transport=httpx.MockTransport(recorder), base_url=BASE), recorder


def body(text: str, status: int = 200, headers: dict[str, str] | None = None) -> Callable:
    return lambda request: httpx.Response(status, content=text.encode(), headers=headers)


# --- 진짜: HttpQuoteSource.get_price -------------------------------------------------------


def test_http_source_is_a_price_source_and_a_quote_source() -> None:
    source, _ = make_source(body("[]"))
    assert isinstance(source, PriceSource)
    assert isinstance(source, QuoteSource)
    source.close()


def test_get_price_reads_the_last_trade_price_as_decimal() -> None:
    source, recorder = make_source(body("[" + ticker_json(price="0.1000000000000000055511") + "]"))
    price = source.get_price(M)
    assert price == Decimal("0.1000000000000000055511")
    assert type(price) is Decimal
    assert len(recorder.requests) == 1
    request = recorder.requests[0]
    assert request.method == "GET"
    assert request.url.path == "/v1/ticker"
    assert dict(request.url.params) == {"markets": M}
    assert "authorization" not in request.headers


def test_get_price_accepts_a_numeric_string() -> None:
    source, _ = make_source(body("[" + ticker_json(price='"102"') + "]"))
    assert source.get_price(M) == Decimal(102)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "{}",
        "[]",  # 항목이 없다
        "[" + ticker_json() + "," + ticker_json() + "]",  # 항목이 둘이다
        "[" + ticker_json(market="KRW-BBB") + "]",  # 다른 마켓
        '[{"market": "KRW-AAA"}]',  # trade_price가 없다
        "[" + ticker_json(price="NaN") + "]",
        "[" + ticker_json(price="Infinity") + "]",
        "[" + ticker_json(price="true") + "]",
        "[" + ticker_json(price="null") + "]",
        "[" + ticker_json(price='"1,000"') + "]",
        "[" + ticker_json(price="0") + "]",
        "[" + ticker_json(price="-1") + "]",
        "[null]",
    ],
)
def test_unreadable_price_is_bad_response(text: str) -> None:
    source, recorder = make_source(body(text))
    with pytest.raises(QuoteError) as caught:
        source.get_price(M)
    assert caught.value.error_name == ERROR_BAD_RESPONSE
    assert caught.value.retry_after is None
    assert caught.value.__context__ is None
    assert len(recorder.requests) == 1


@pytest.mark.parametrize(
    ("status", "name"),
    [
        (418, ERROR_BLOCKED),
        (429, ERROR_RATE_LIMIT),
        (503, ERROR_MAINTENANCE),
        (500, ERROR_SERVER),
        (404, error_unknown_exchange(404)),
    ],
)
def test_status_becomes_the_same_error_name_as_other_requests(status: int, name: str) -> None:
    source, recorder = make_source(
        body('{"error": {"name": "made_up"}}', status, {"Retry-After": "120"})
    )
    with pytest.raises(QuoteError) as caught:
        source.get_price(M)
    assert (caught.value.error_name, caught.value.retry_after) == (name, 120.0)
    assert "made_up" not in repr(caught.value)
    assert len(recorder.requests) == 1  # 다시 보내지 않는다


@pytest.mark.parametrize(
    ("exception", "name"),
    [(httpx.ReadTimeout, ERROR_TIMEOUT), (httpx.ConnectError, ERROR_NETWORK)],
)
def test_transport_exception_becomes_error_name(exception: type, name: str) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        raise exception("made up", request=request)

    source, recorder = make_source(respond)
    with pytest.raises(QuoteError) as caught:
        source.get_price(M)
    assert caught.value.error_name == name
    assert len(recorder.requests) == 1


@pytest.mark.parametrize("market", ["krw-aaa", "AAA", "", 5])
def test_bad_market_is_rejected_before_sending(market: object) -> None:
    source, recorder = make_source(body("[" + ticker_json() + "]"))
    with pytest.raises(ValueError, match="마켓 코드"):
        source.get_price(market)  # type: ignore[arg-type]
    assert recorder.requests == []


# --- 감싸는 부품: QuoteClient.read_price -----------------------------------------------------


def setup(**prices: Decimal) -> tuple[QuoteClient, FakeQuoteSource, FakeClock]:
    clock = FakeClock(NOW)
    source = FakeQuoteSource(prices={M: Decimal(100), **prices}, clock=clock)
    return QuoteClient(source, clock), source, clock


def test_read_price_is_one_request() -> None:
    client, source, clock = setup()
    assert client.read_price(M) == Decimal(100)
    assert [(r.kind, r.market) for r in source.requests] == [(PRICE, M)]
    assert clock.sleeps == []


def test_read_price_through_the_real_source() -> None:
    source, recorder = make_source(body("[" + ticker_json(price="99.25") + "]"))
    client = QuoteClient(source, FakeClock(NOW))
    assert client.read_price(M) == Decimal("99.25")
    assert len(recorder.requests) == 1


@pytest.mark.parametrize(
    "error",
    [
        QuoteError(ERROR_BLOCKED, 120.0),
        QuoteError(ERROR_RATE_LIMIT, 1.0),
        QuoteError(ERROR_NETWORK),
    ],
)
def test_read_price_raises_the_error_as_is_without_retrying(error: QuoteError) -> None:
    client, source, clock = setup()
    source.script(error, kind=PRICE, market=M)
    with pytest.raises(QuoteError) as caught:
        client.read_price(M)
    assert (caught.value.error_name, caught.value.retry_after) == (
        error.error_name,
        error.retry_after,
    )
    assert len(source.requests) == 1
    assert clock.sleeps == []
    # 다음에 부르면(그 자리가 아니라) 다시 읽는다.
    assert client.read_price(M) == Decimal(100)


def test_read_price_shares_the_limit_with_markets_and_candles() -> None:
    clock = FakeClock(NOW)
    candles = make_candles(
        M, CandleUnit.MINUTE_60, 3, last_start=datetime(2026, 1, 5, 3, tzinfo=UTC)
    )
    source = FakeQuoteSource(candles=candles, prices={M: Decimal(100)}, clock=clock)
    client = QuoteClient(source, clock)
    client.list_markets()
    client.read_price(M)
    client.fetch_recent(
        M, CandleUnit.MINUTE_60, count=100, last_start=None, first_start=None, missing=0
    )
    client.read_price(M)
    client.read_price(M)
    client.read_price(M)  # 여섯째 요청
    times = [r.monotonic for r in source.requests]
    assert [r.kind for r in source.requests][:3] == ["markets", PRICE, "candles"]
    assert len(times) == 6
    assert times[5] - times[0] > 1.0
    assert len(clock.sleeps) == 1


class CandlesOnly:
    """`QuoteSource`만 갖춘 시세(`get_price`가 없다)."""

    def __init__(self) -> None:
        self.calls = 0

    def list_markets(self) -> tuple[Market, ...]:
        self.calls += 1
        return ()

    def get_candles(self, market, unit, *, to, count):  # type: ignore[no-untyped-def]
        self.calls += 1
        return ()


def test_read_price_needs_a_price_source() -> None:
    source = CandlesOnly()
    assert isinstance(source, QuoteSource)
    assert not isinstance(source, PriceSource)
    clock = FakeClock(NOW)
    client = QuoteClient(source, clock)
    with pytest.raises(TypeError, match="PriceSource"):
        client.read_price(M)
    assert source.calls == 0
    # 요청을 내지 않았으므로 한도에도 세지 않는다: 그 뒤 다섯 요청은 기다리지 않는다.
    for _ in range(5):
        client.list_markets()
    assert clock.sleeps == []
