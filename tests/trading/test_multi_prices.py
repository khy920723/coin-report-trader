"""여러 마켓의 현재가 읽기(M5 명세 3.4의 `MultiPriceSource`, 3.15.2)의 시험.

- 진짜 `HttpQuoteSource.get_prices`는 소켓을 열지 않고 `httpx.MockTransport`로 본다.
- `QuoteClient.read_prices`는 가짜 시세와 가짜 시계로 본다.
- 응답은 필드 이름의 꼴만 따르고, 값은 모두 만든 숫자다.
"""

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from fakes.clock import FakeClock
from fakes.quote import FakeQuoteSource

from coin_report_trader.core import QuoteError
from coin_report_trader.core.ports import MultiPriceSource
from coin_report_trader.core.texts import ERROR_BAD_RESPONSE, ERROR_NETWORK, ERROR_SERVER
from coin_report_trader.trading.quote import HttpQuoteSource, QuoteClient

BASE = "https://quote.test"
A = "KRW-AAA"
B = "KRW-BBB"
NOW = datetime(2026, 1, 5, 3, 20, tzinfo=UTC)


def item(market: str, price: str) -> str:
    return f'{{"market": "{market}", "trade_price": {price}, "timestamp": 1}}'


def source_with(text: str, status: int = 200):
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, content=text.encode())

    return HttpQuoteSource(transport=httpx.MockTransport(respond), base_url=BASE), requests


# --- 진짜 소스 --------------------------------------------------------------------------------


def test_http_source_is_a_multi_price_source() -> None:
    source, _ = source_with("[]")
    assert isinstance(source, MultiPriceSource)


def test_get_prices_asks_once_with_comma_joined_markets() -> None:
    source, requests = source_with(f"[{item(A, '1100')}, {item(B, '500.5')}]")
    assert source.get_prices((A, B)) == {A: Decimal(1100), B: Decimal("500.5")}
    (request,) = requests
    assert request.url.path == "/v1/ticker"
    assert request.url.params["markets"] == "KRW-AAA,KRW-BBB"


@pytest.mark.parametrize(
    "text",
    [
        f"[{item(A, '1100')}]",  # B가 빠짐
        f"[{item(A, '1100')}, {item(A, '1100')}]",  # 겹침
        f"[{item(A, '1100')}, {item(B, '1')}, {item(A, '1200')}]",  # 다 있지만 겹침
        f"[{item(A, '1100')}, {item(B, '1')}, {item('KRW-CCC', '1')}]",  # 다 있지만 더 있음
        f"[{item(A, '1100')}, {item('KRW-CCC', '1')}]",  # 묻지 않은 마켓
        f"[{item(A, '0')}, {item(B, '1')}]",  # 0
        f'[{item(A, "1")}, {{"market": "{B}"}}]',  # trade_price 없음
        "{}",
        "not json",
    ],
)
def test_unreadable_replies_are_bad_response(text: str) -> None:
    source, _ = source_with(text)
    with pytest.raises(QuoteError) as caught:
        source.get_prices((A, B))
    assert caught.value.error_name == ERROR_BAD_RESPONSE


def test_status_errors_keep_their_names() -> None:
    source, _ = source_with("{}", status=500)
    with pytest.raises(QuoteError) as caught:
        source.get_prices((A,))
    assert caught.value.error_name == ERROR_SERVER


@pytest.mark.parametrize("markets", [(), (A, A), ("KRW-aaa",)])
def test_bad_arguments_are_refused_before_sending(markets: tuple[str, ...]) -> None:
    source, requests = source_with("[]")
    with pytest.raises(ValueError, match="마켓"):
        source.get_prices(markets)
    assert requests == []


# --- 감싸는 부품 ------------------------------------------------------------------------------


def client_with(**prices: Decimal):
    clock = FakeClock(NOW)
    quotes = FakeQuoteSource(prices={A: Decimal(1100), B: Decimal(500), **prices}, clock=clock)
    return QuoteClient(quotes, clock), quotes, clock


def test_read_prices_is_one_request() -> None:
    client, quotes, _ = client_with()
    assert client.read_prices((A, B)) == {A: Decimal(1100), B: Decimal(500)}
    assert [r.market for r in quotes.prices_requests()] == ["KRW-AAA,KRW-BBB"]


def test_read_prices_with_nothing_to_ask_sends_nothing() -> None:
    client, quotes, _ = client_with()
    assert client.read_prices(()) == {}
    assert quotes.requests == []


def test_read_prices_shares_the_limit_with_the_other_reads() -> None:
    client, quotes, _ = client_with()
    for _ in range(3):
        client.read_price(A)
    client.read_best_prices(A)
    client.read_prices((A, B))  # 다섯째
    client.read_prices((A,))  # 여섯째는 첫 요청에서 1초 뒤
    times = [r.monotonic for r in quotes.requests]
    assert times[5] - times[0] > 1.0


def test_read_prices_errors_are_raised_as_is_without_retrying() -> None:
    client, quotes, _ = client_with()
    quotes.script(QuoteError(ERROR_NETWORK), kind="prices")
    with pytest.raises(QuoteError) as caught:
        client.read_prices((A, B))
    assert caught.value.error_name == ERROR_NETWORK
    assert len(quotes.prices_requests()) == 1


def test_read_prices_needs_a_multi_price_source() -> None:
    class OnlyPrice:
        def get_price(self, market: str) -> Decimal:
            return Decimal(1)

    clock = FakeClock(NOW)
    with pytest.raises(TypeError, match="MultiPriceSource"):
        QuoteClient(OnlyPrice(), clock).read_prices((A,))  # type: ignore[arg-type]
