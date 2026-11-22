"""`UpbitClient`의 여섯 요청(M5 명세 3.5.2)과 진짜 운반의 시험.

가짜 운반(`tests/fakes/upbit.py`)과 `httpx.MockTransport`로 하고 소켓을 열지 않는다. 값은 모두 만든
숫자다.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from fakes.clock import FakeClock
from fakes.upbit import (
    TEST_ACCESS_KEY,
    TEST_SECRET_KEY,
    FakeUpbitTransport,
    NoResponse,
    Reply,
)

from coin_report_trader.core.texts import ERROR_BAD_RESPONSE, ERROR_NETWORK, ERROR_TIMEOUT
from coin_report_trader.trading.upbit import (
    HttpxUpbitTransport,
    TransportError,
    TransportResponse,
    UpbitBalance,
    UpbitChance,
    UpbitClient,
    UpbitError,
    UpbitKeys,
    UpbitOrder,
    UpbitTransport,
)
from coin_report_trader.trading.upbit import transport as transport_module
from coin_report_trader.trading.upbit.client import number_text

KEYS = UpbitKeys(TEST_ACCESS_KEY, TEST_SECRET_KEY)
M = "KRW-AAA"


def setup() -> tuple[UpbitClient, FakeUpbitTransport, FakeClock]:
    clock = FakeClock()
    fake = FakeUpbitTransport(clock=clock)
    fake.set_account(krw=Decimal(100_000), coins={"AAA": (Decimal(20), Decimal(990))})
    fake.set_chance(M, bid_fee=Decimal("0.0005"), ask_fee=Decimal("0.0005"))
    fake.set_market_price(M, Decimal(1000))
    return UpbitClient(KEYS, fake, clock), fake, clock


class Canned:
    """정한 응답 하나를 늘 주는 운반(가짜가 만들지 않는 꼴을 보려고)."""

    def __init__(self, status: int, text: str, headers: dict[str, str] | None = None) -> None:
        self.response = TransportResponse(status, headers or {}, text)
        self.calls: list[tuple[str, str, str, str | None, dict[str, str]]] = []

    def request(self, method, path, *, query, body, headers):  # type: ignore[no-untyped-def]
        self.calls.append((method, path, query, body, dict(headers)))
        return self.response


def canned(text: str, status: int = 200) -> tuple[UpbitClient, Canned]:
    transport = Canned(status, text)
    return UpbitClient(KEYS, transport, FakeClock()), transport


# --- 여섯 요청 -------------------------------------------------------------------------------


def test_fake_is_an_upbit_transport() -> None:
    assert isinstance(FakeUpbitTransport(), UpbitTransport)
    assert isinstance(
        HttpxUpbitTransport(transport=httpx.MockTransport(lambda r: None)), UpbitTransport
    )


def test_accounts_reads_each_currency() -> None:
    client, fake, _ = setup()
    balances = client.accounts()
    assert balances == (
        UpbitBalance("KRW", Decimal(100_000), Decimal(0), None),
        UpbitBalance("AAA", Decimal(20), Decimal(0), Decimal(990)),
    )
    request = fake.requests[0]
    assert (request.method, request.path, request.query, request.body) == (
        "GET",
        "/v1/accounts",
        "",
        None,
    )


def test_avg_buy_price_of_zero_is_none() -> None:
    client, fake, _ = setup()
    fake.set_account(krw=Decimal(1), coins={"AAA": (Decimal(1), Decimal(0))})
    assert client.accounts()[1].avg_buy_price is None


def test_order_chance_reads_fees_and_totals() -> None:
    client, fake, _ = setup()
    fake.set_chance(
        M, bid_fee=Decimal("0.0005"), ask_fee=Decimal("0.0025"), min_total=Decimal(5000)
    )
    assert client.order_chance(M) == UpbitChance(
        market=M,
        bid_fee=Decimal("0.0005"),
        ask_fee=Decimal("0.0025"),
        bid_min_total=Decimal(5000),
        ask_min_total=Decimal(5000),
        max_total=Decimal(1_000_000_000),
    )
    request = fake.requests[0]
    assert (request.method, request.path, request.query) == (
        "GET",
        "/v1/orders/chance",
        "market=KRW-AAA",
    )


def test_create_limit_order_sends_the_fields_with_smp_type() -> None:
    client, fake, _ = setup()
    order = client.create_order(M, "bid", "limit", Decimal("10.50"), Decimal("1000"), "uid-1")
    request = fake.requests[0]
    assert (request.method, request.path, request.query) == ("POST", "/v1/orders", "")
    assert json.loads(request.body, object_pairs_hook=list) == [
        ("market", M),
        ("side", "bid"),
        ("volume", "10.5"),
        ("price", "1000"),
        ("ord_type", "limit"),
        ("identifier", "uid-1"),
        ("smp_type", "cancel_maker"),
    ]
    assert order.identifier == "uid-1"
    assert order.state == "wait"
    assert order.price == Decimal(1000)
    assert order.volume == Decimal("10.5")
    assert order.executed_volume == Decimal(0)
    assert order.trades == ()


def test_market_sell_has_no_price_and_still_smp_type() -> None:
    client, fake, _ = setup()
    order = client.create_order(M, "ask", "market", Decimal(6), None, "uid-stop")
    body = dict(json.loads(fake.requests[0].body, object_pairs_hook=list))
    assert body == {
        "market": M,
        "side": "ask",
        "volume": "6",
        "ord_type": "market",
        "identifier": "uid-stop",
        "smp_type": "cancel_maker",
    }
    assert order.ord_type == "market"


def test_every_order_has_smp_type_cancel_maker() -> None:
    client, fake, _ = setup()
    client.create_order(M, "bid", "limit", Decimal(10), Decimal(1000), "a")
    client.create_order(M, "ask", "limit", Decimal(10), Decimal(1010), "b")
    client.create_order(M, "ask", "market", Decimal(5), None, "c")
    bodies = [dict(json.loads(r.body, object_pairs_hook=list)) for r in fake.requests]
    assert [b["smp_type"] for b in bodies] == ["cancel_maker"] * 3


@pytest.mark.parametrize(
    ("args", "match"),
    [
        ((M, "bid", "limit", Decimal(1), Decimal(1000), "u", "cancel_taker"), "smp_type"),
        ((M, "bid", "market", Decimal(1), None, "u"), "시장가"),  # 시장가 매수는 쓰지 않는다
        ((M, "ask", "market", Decimal(1), Decimal(1000), "u"), "시장가"),
        ((M, "bid", "price", Decimal(1), Decimal(1000), "u"), "주문 종류"),
        ((M, "bid", "best", Decimal(1), None, "u"), "주문 종류"),
        ((M, "bid", "limit", Decimal(1), None, "u"), "가격"),
        ((M, "buy", "limit", Decimal(1), Decimal(1), "u"), "방향"),
        (("krw-aaa", "bid", "limit", Decimal(1), Decimal(1), "u"), "마켓"),
        ((M, "bid", "limit", Decimal(0), Decimal(1), "u"), "Decimal"),
        ((M, "bid", "limit", Decimal("NaN"), Decimal(1), "u"), "Decimal"),
        ((M, "bid", "limit", 1.0, Decimal(1), "u"), "Decimal"),
        ((M, "bid", "limit", Decimal(1), Decimal(1), ""), "식별값"),
        ((M, "bid", "limit", Decimal(1), Decimal(1), "x" * 65), "식별값"),
        ((M, "bid", "limit", Decimal(1), Decimal(1), "a b"), "인코딩"),
    ],
)
def test_bad_orders_are_refused_before_sending(args: tuple, match: str) -> None:
    client, fake, _ = setup()
    with pytest.raises(ValueError, match=match):
        client.create_order(*args)
    assert fake.requests == []


def test_number_text_has_no_exponent() -> None:
    assert number_text(Decimal("1E+3")) == "1000"
    assert number_text(Decimal("0.00012000")) == "0.00012"
    assert number_text(Decimal("1234.5")) == "1234.5"
    assert number_text(Decimal("1E-8")) == "0.00000001"


def test_get_order_reads_state_and_trades() -> None:
    client, fake, _ = setup()
    client.create_order(M, "bid", "limit", Decimal(10), Decimal(1000), "uid-2")
    fake.fill("uid-2", Decimal(4))
    order = client.get_order("uid-2")
    request = fake.requests[-1]
    assert (request.method, request.path, request.query) == ("GET", "/v1/order", "identifier=uid-2")
    assert order.state == "wait"
    assert order.executed_volume == Decimal(4)
    assert len(order.trades) == 1
    trade = order.trades[0]
    assert (trade.price, trade.volume, trade.funds) == (Decimal(1000), Decimal(4), Decimal(4000))
    assert trade.trade_key
    assert trade.created_at is not None
    assert trade.created_at.tzinfo is UTC


def test_open_orders_includes_direct_orders() -> None:
    client, fake, _ = setup()
    client.create_order(M, "bid", "limit", Decimal(10), Decimal(990), "uid-3")
    direct = fake.add_direct_order(M, "ask", Decimal(1100), Decimal(5))
    orders = client.open_orders(M)
    assert {o.identifier for o in orders} == {"uid-3", None}
    assert {o.uuid for o in orders if o.identifier is None} == {direct}
    request = fake.requests[-1]
    assert (request.method, request.path, request.query) == (
        "GET",
        "/v1/orders/open",
        "market=KRW-AAA",
    )


def test_cancel_order() -> None:
    client, fake, _ = setup()
    client.create_order(M, "bid", "limit", Decimal(10), Decimal(990), "uid-4")
    order = client.cancel_order("uid-4")
    request = fake.requests[-1]
    assert (request.method, request.path, request.query) == (
        "DELETE",
        "/v1/order",
        "identifier=uid-4",
    )
    assert isinstance(order, UpbitOrder)
    assert fake.order_state("uid-4") == "cancel"


def test_headers_are_only_these_and_never_origin() -> None:
    client, fake, _ = setup()
    client.accounts()
    client.create_order(M, "bid", "limit", Decimal(10), Decimal(1000), "uid-5")
    assert fake.requests[0].header_names == ("accept", "authorization", "user-agent")
    assert fake.requests[1].header_names == (
        "accept",
        "authorization",
        "content-type",
        "user-agent",
    )
    assert all("origin" not in r.header_names for r in fake.requests)


def test_user_agent_is_a_plain_app_name() -> None:
    transport = Canned(200, "[]")
    UpbitClient(KEYS, transport, FakeClock()).accounts()
    headers = transport.calls[0][4]
    assert headers["User-Agent"].startswith("coin-report-trader/")


# --- 응답 형식 오류 -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "{}",
        '[{"currency": "KRW"}]',
        '[{"currency": "KRW", "balance": "x", "locked": "0", "avg_buy_price": "0"}]',
        '[{"currency": "KRW", "balance": "NaN", "locked": "0", "avg_buy_price": "0"}]',
        '[{"currency": 5, "balance": "1", "locked": "0", "avg_buy_price": "0"}]',
    ],
)
def test_unreadable_accounts_are_bad_response(text: str) -> None:
    client, _ = canned(text)
    with pytest.raises(UpbitError) as caught:
        client.accounts()
    assert caught.value.error_name == ERROR_BAD_RESPONSE
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "text",
    [
        '{"bid_fee": "0.0005"}',
        '{"bid_fee": "0.0005", "ask_fee": "0.0005", "market": {"id": "KRW-BBB", '
        '"bid": {"min_total": "5000"}, "ask": {"min_total": "5000"}}}',  # 다른 마켓
        '{"bid_fee": "x", "ask_fee": "0.0005", "market": {"id": "KRW-AAA", '
        '"bid": {"min_total": "5000"}, "ask": {"min_total": "5000"}}}',
    ],
)
def test_unreadable_chance_is_bad_response(text: str) -> None:
    client, _ = canned(text)
    with pytest.raises(UpbitError) as caught:
        client.order_chance(M)
    assert caught.value.error_name == ERROR_BAD_RESPONSE


@pytest.mark.parametrize(
    "text",
    [
        '{"uuid": "u"}',
        '{"uuid": "u", "market": "KRW-AAA", "side": "bid", "ord_type": "limit", '
        '"state": "wait", "executed_volume": "abc"}',
        '{"uuid": "u", "market": "KRW-AAA", "side": "bid", "ord_type": "limit", '
        # 시간대 없는 시각
        '"state": "wait", "executed_volume": "0", "created_at": "2026-01-05T12:00:00"}',
        '{"uuid": "u", "market": "KRW-AAA", "side": "bid", "ord_type": "limit", '
        '"state": "wait", "executed_volume": "0", "trades": [{"uuid": "t"}]}',
    ],
)
def test_unreadable_order_is_bad_response(text: str) -> None:
    client, _ = canned(text)
    with pytest.raises(UpbitError) as caught:
        client.get_order("uid")
    assert caught.value.error_name == ERROR_BAD_RESPONSE


def test_order_without_optional_fields_is_read() -> None:
    client, _ = canned(
        '{"uuid": "u", "market": "KRW-AAA", "side": "ask", "ord_type": "market", '
        '"state": "done", "executed_volume": "2", "price": null, "volume": "2", '
        '"created_at": "2026-01-05T12:00:00+09:00"}'
    )
    order = client.get_order("uid")
    assert order.identifier is None
    assert order.price is None
    assert order.created_at == datetime(2026, 1, 5, 3, 0, tzinfo=UTC)


# --- 응답이 없을 때 -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "name", "unknown"),
    [
        (NoResponse(sent=True), ERROR_TIMEOUT, True),
        (NoResponse(sent=False), ERROR_TIMEOUT, False),
    ],
)
def test_no_response_is_not_retried(outcome: NoResponse, name: str, unknown: bool) -> None:
    client, fake, _ = setup()
    fake.script(outcome, path="/v1/orders")
    with pytest.raises(UpbitError) as caught:
        client.create_order(M, "bid", "limit", Decimal(10), Decimal(1000), "uid-6")
    assert (caught.value.error_name, caught.value.unknown) == (name, unknown)
    assert len(fake.requests) == 1
    # 보냈지만 응답이 없었으면 주문은 만들어져 있고(조회로 안다), 보내지 않았으면 없다.
    assert (fake.order_state("uid-6") == "wait") is unknown


class Raising:
    def __init__(self, error: TransportError) -> None:
        self.error = error
        self.calls = 0

    def request(self, method, path, *, query, body, headers):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise self.error


@pytest.mark.parametrize(
    ("error", "name", "unknown"),
    [
        (TransportError(timeout=True, sent=True), ERROR_TIMEOUT, True),
        (TransportError(timeout=False, sent=False), ERROR_NETWORK, False),
        (TransportError(timeout=False, sent=True), ERROR_NETWORK, True),
    ],
)
def test_transport_errors_become_names(error: TransportError, name: str, unknown: bool) -> None:
    transport = Raising(error)
    client = UpbitClient(KEYS, transport, FakeClock())
    with pytest.raises(UpbitError) as caught:
        client.accounts()
    assert (caught.value.error_name, caught.value.unknown) == (name, unknown)
    assert transport.calls == 1


def test_scripted_error_reply_reaches_the_client() -> None:
    client, fake, _ = setup()
    fake.script(Reply(400, "insufficient_funds_bid"), path="/v1/orders")
    with pytest.raises(UpbitError) as caught:
        client.create_order(M, "bid", "limit", Decimal(10), Decimal(1000), "uid-7")
    assert caught.value.error_name == "잔고 부족"


# --- 진짜 운반(httpx.MockTransport) ---------------------------------------------------------


def test_httpx_transport_sends_the_query_as_given() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, text="[]", headers={"Remaining-Req": "group=default; min=1800; sec=29"}
        )

    transport = HttpxUpbitTransport(
        transport=httpx.MockTransport(respond), base_url="https://up.test"
    )
    response = transport.request(
        "GET",
        "/v1/orders/open",
        query="market=KRW-AAA&states[]=wait",
        body=None,
        headers={"Authorization": "Bearer t"},
    )
    assert response.status == 200
    assert response.header("remaining-req") == "group=default; min=1800; sec=29"
    request = seen[0]
    assert request.url.raw_path == b"/v1/orders/open?market=KRW-AAA&states[]=wait"
    assert "origin" not in request.headers
    assert "cookie" not in request.headers


def test_httpx_transport_sends_the_body_as_given() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, text="{}")

    transport = HttpxUpbitTransport(
        transport=httpx.MockTransport(respond), base_url="https://up.test"
    )
    transport.request("POST", "/v1/orders", query="", body='{"market":"KRW-AAA"}', headers={})
    assert seen[0].content == b'{"market":"KRW-AAA"}'
    assert seen[0].url.raw_path == b"/v1/orders"


@pytest.mark.parametrize(
    ("exception", "timeout", "sent"),
    [
        (httpx.ConnectTimeout, True, False),
        (httpx.PoolTimeout, True, False),
        (httpx.ReadTimeout, True, True),
        (httpx.WriteTimeout, True, True),
        (httpx.ConnectError, False, False),
        (httpx.ReadError, False, True),
        (httpx.RemoteProtocolError, False, True),
    ],
)
def test_httpx_exceptions_become_transport_errors(
    exception: type, timeout: bool, sent: bool
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        raise exception("made up", request=request)

    transport = HttpxUpbitTransport(
        transport=httpx.MockTransport(respond), base_url="https://up.test"
    )
    with pytest.raises(TransportError) as caught:
        transport.request("GET", "/v1/accounts", query="", body=None, headers={})
    assert (caught.value.timeout, caught.value.sent) == (timeout, sent)
    # httpx의 예외를 잇지 않는다(그 글에 요청의 주소가 든다).
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_httpx_transport_uses_the_app_tls_context(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real = transport_module.make_tls_context

    def spy(cafile: str):
        calls.append(cafile)
        return real(cafile)

    monkeypatch.setattr(transport_module, "make_tls_context", spy)
    transport = HttpxUpbitTransport()
    assert repr(transport) == "HttpxUpbitTransport()"
    transport.close()
    assert len(calls) == 1


def test_httpx_transport_base_url_has_no_trailing_slash() -> None:
    with pytest.raises(ValueError, match="base_url"):
        HttpxUpbitTransport(base_url="https://up.test/")


def test_client_needs_keys() -> None:
    with pytest.raises(TypeError):
        UpbitClient(("a", "b"), FakeUpbitTransport(), FakeClock())  # type: ignore[arg-type]


def test_client_end_to_end_through_httpx_mock_and_fake() -> None:
    # 진짜 운반에 MockTransport로 가짜를 이어도 서명 검사를 통과한다(운반이 글을 바꾸지 않는다).
    fake = FakeUpbitTransport()
    fake.set_account(krw=Decimal(5000))

    def respond(request: httpx.Request) -> httpx.Response:
        path, _, query = request.url.raw_path.decode().partition("?")
        reply = fake.request(
            request.method,
            path,
            query=query,
            body=request.content.decode() or None,
            headers=dict(request.headers),
        )
        return httpx.Response(reply.status, text=reply.text, headers=dict(reply.headers))

    transport = HttpxUpbitTransport(
        transport=httpx.MockTransport(respond), base_url="https://up.test"
    )
    client = UpbitClient(KEYS, transport, FakeClock())
    assert client.accounts()[0].balance == Decimal(5000)
    fake.set_chance(M)
    assert client.order_chance(M).market == M
    assert [r.auth for r in fake.requests] == [None, None]
