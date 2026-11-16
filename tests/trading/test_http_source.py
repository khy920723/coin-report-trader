"""진짜 `QuoteSource`(`HttpQuoteSource`)의 시험. 소켓을 열지 않고 `httpx.MockTransport`로 한다.

응답의 꼴(필드 이름)은 옛 코드의 변환 코드에서 읽었고, 값은 모두 만든 숫자다.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import certifi
import httpx
import pytest

from coin_report_trader.core import Candle, CandleUnit, Market, QuoteError, QuoteSource
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
from coin_report_trader.trading.quote import HttpQuoteSource
from coin_report_trader.trading.quote import http_source as http_source_module
from coin_report_trader.trading.quote.errors import parse_retry_after

BASE = "https://quote.test"


def candle_json(market: str, start: str, *, price: str = "1500.5", volume: str = "12.25") -> str:
    """봉 하나의 JSON 글. 숫자를 글로 직접 적어 부동소수점을 거치지 않는다."""
    return (
        f'{{"market": "{market}", "candle_date_time_utc": "{start}", '
        f'"candle_date_time_kst": "{start}", "opening_price": 1400, "high_price": 1600.75, '
        f'"low_price": 1390.125, "trade_price": {price}, "timestamp": 1, '
        f'"candle_acc_trade_price": 18381.125, "candle_acc_trade_volume": {volume}}}'
    )


def market_json(market: str, name: str, *, warning: str = "false", caution: str = "{}") -> str:
    return (
        f'{{"market": "{market}", "korean_name": "{name}", "english_name": "Made Up", '
        f'"market_event": {{"warning": {warning}, "caution": {caution}}}}}'
    )


class Recorder:
    """MockTransport의 처리기. 받은 요청을 남기고 정해 둔 응답을 준다."""

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


# --- 꼴과 요청 ------------------------------------------------------------------------------


def test_is_a_quote_source() -> None:
    source, _ = make_source(body("[]"))
    assert isinstance(source, QuoteSource)
    source.close()


def test_list_markets_keeps_krw_markets_with_caution() -> None:
    text = (
        "["
        + ",".join(
            [
                market_json("KRW-AAA", "가가"),
                market_json("BTC-AAA", "가가"),
                market_json("KRW-BBB", "나나", warning="true"),
                market_json("KRW-CCC", "다다", caution='{"KIND_A": false, "KIND_B": true}'),
                market_json("USDT-CCC", "다다"),
                market_json("KRW-DDD", "라라", caution='{"KIND_A": false, "KIND_B": false}'),
            ]
        )
        + "]"
    )
    source, recorder = make_source(body(text))
    markets = source.list_markets()
    assert markets == (
        Market("KRW-AAA", "가가", False),
        Market("KRW-BBB", "나나", True),
        Market("KRW-CCC", "다다", True),
        Market("KRW-DDD", "라라", False),
    )
    request = recorder.requests[0]
    assert request.method == "GET"
    assert request.url.path == "/v1/market/all"
    assert dict(request.url.params) == {"is_details": "true"}


@pytest.mark.parametrize(
    "event",
    [
        "",  # market_event가 없다
        ', "market_event": null',
        ', "market_event": {"caution": {}}',  # warning이 없다
        ', "market_event": {"warning": "false", "caution": {}}',  # 참·거짓이 아니다
        ', "market_event": {"warning": false}',  # caution이 없다
        ', "market_event": {"warning": false, "caution": []}',
        ', "market_event": {"warning": false, "caution": {"KIND_A": "no"}}',
    ],
)
def test_unreadable_market_event_counts_as_caution(event: str) -> None:
    text = f'[{{"market": "KRW-AAA", "korean_name": "가가"{event}}}]'
    source, _ = make_source(body(text))
    assert source.list_markets() == (Market("KRW-AAA", "가가", True),)


def test_get_candles_converts_with_decimal_and_utc() -> None:
    text = "[" + candle_json("KRW-AAA", "2026-01-04T00:00:00") + "]"
    source, recorder = make_source(body(text))
    candles = source.get_candles("KRW-AAA", CandleUnit.DAY, to=None, count=200)
    assert candles == (
        Candle(
            market="KRW-AAA",
            unit=CandleUnit.DAY,
            start=datetime(2026, 1, 4, tzinfo=UTC),
            open=Decimal(1400),
            high=Decimal("1600.75"),
            low=Decimal("1390.125"),
            close=Decimal("1500.5"),
            volume=Decimal("12.25"),
            value=Decimal("18381.125"),
        ),
    )
    candle = candles[0]
    for name in ("open", "high", "low", "close", "volume", "value"):
        assert type(getattr(candle, name)) is Decimal, name
    assert candle.start.tzinfo is UTC
    request = recorder.requests[0]
    assert request.url.path == "/v1/candles/days"
    assert dict(request.url.params) == {"market": "KRW-AAA", "count": "200"}


def test_decimal_keeps_digits_a_float_would_lose() -> None:
    text = (
        "[" + candle_json("KRW-AAA", "2026-01-04T00:00:00", price="0.1000000000000000055511") + "]"
    )
    source, _ = make_source(body(text))
    (candle,) = source.get_candles("KRW-AAA", CandleUnit.DAY, to=None, count=1)
    assert candle.close == Decimal("0.1000000000000000055511")


def test_numeric_strings_are_read_as_decimal() -> None:
    text = (
        '[{"market": "KRW-AAA", "candle_date_time_utc": "2026-01-04T05:00:00", '
        '"opening_price": "10", "high_price": "12", "low_price": "9", "trade_price": "11.5", '
        '"candle_acc_trade_price": "100", "candle_acc_trade_volume": "9.5"}]'
    )
    source, recorder = make_source(body(text))
    (candle,) = source.get_candles("KRW-AAA", CandleUnit.MINUTE_60, to=None, count=1)
    assert candle.close == Decimal("11.5")
    assert candle.unit is CandleUnit.MINUTE_60
    assert candle.start == datetime(2026, 1, 4, 5, tzinfo=UTC)
    assert recorder.requests[0].url.path == "/v1/candles/minutes/60"


def test_to_is_sent_as_utc_and_rounded_up_to_the_second() -> None:
    source, recorder = make_source(body("[]"))
    kst_noon = datetime.fromisoformat("2026-01-04T12:00:00+09:00")
    source.get_candles("KRW-AAA", CandleUnit.DAY, to=kst_noon, count=5)
    source.get_candles(
        "KRW-AAA", CandleUnit.DAY, to=datetime(2026, 1, 4, 3, 0, 0, 1, tzinfo=UTC), count=5
    )
    sent = [dict(r.url.params) for r in recorder.requests]
    assert sent[0] == {"market": "KRW-AAA", "to": "2026-01-04T03:00:00Z", "count": "5"}
    assert sent[1]["to"] == "2026-01-04T03:00:01Z"


def test_candles_come_newest_first_before_to_and_at_most_count() -> None:
    starts = [
        "2026-01-02T00:00:00",
        "2026-01-04T00:00:00",
        "2026-01-03T00:00:00",
        "2026-01-05T00:00:00",
        "2026-01-04T00:00:00",
    ]
    text = "[" + ",".join(candle_json("KRW-AAA", s) for s in starts) + "]"
    source, _ = make_source(body(text))
    to = datetime(2026, 1, 5, tzinfo=UTC)
    candles = source.get_candles("KRW-AAA", CandleUnit.DAY, to=to, count=2)
    # 2026-01-05는 `to`이므로 빠지고(앞에 시작한 봉만), 겹친 01-04는 하나만, 새것부터 둘.
    assert [c.start.day for c in candles] == [4, 3]


def test_one_call_is_one_request_with_fixed_headers() -> None:
    source, recorder = make_source(body("[]", status=500))
    with pytest.raises(QuoteError):
        source.get_candles("KRW-AAA", CandleUnit.DAY, to=None, count=10)
    assert len(recorder.requests) == 1
    headers = recorder.requests[0].headers
    assert headers["Accept"] == "application/json"
    assert headers["User-Agent"].startswith("coin-report-trader/")
    assert "authorization" not in headers
    assert "cookie" not in headers


def test_redirect_is_not_followed() -> None:
    source, recorder = make_source(
        body("", status=302, headers={"Location": "https://elsewhere.test/v1/market/all"})
    )
    with pytest.raises(QuoteError) as caught:
        source.list_markets()
    assert caught.value.error_name == error_unknown_exchange(302)
    assert len(recorder.requests) == 1


def test_cookies_are_not_kept() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"[]", headers={"Set-Cookie": "k=v; Path=/"})

    source, recorder = make_source(respond)
    source.list_markets()
    source.list_markets()
    assert "cookie" not in recorder.requests[1].headers


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"market": "krw-aaa"}, ValueError),
        ({"market": "AAA"}, ValueError),
        ({"count": 0}, ValueError),
        ({"count": 201}, ValueError),
        ({"count": True}, TypeError),
        ({"count": 2.0}, TypeError),
        ({"to": datetime(2026, 1, 4)}, ValueError),
        ({"unit": "week"}, ValueError),
    ],
)
def test_bad_arguments_are_rejected_before_sending(kwargs: dict, error: type) -> None:
    source, recorder = make_source(body("[]"))
    args = {"market": "KRW-AAA", "unit": CandleUnit.DAY, "to": None, "count": 10, **kwargs}
    with pytest.raises(error):
        source.get_candles(args.pop("market"), args.pop("unit"), **args)
    assert recorder.requests == []


def test_default_transport_uses_the_app_tls_context(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real = http_source_module.make_tls_context

    def spy(cafile: str):
        calls.append(cafile)
        return real(cafile)

    monkeypatch.setattr(http_source_module, "make_tls_context", spy)
    with HttpQuoteSource() as source:
        assert repr(source) == "HttpQuoteSource()"
    assert calls == [certifi.where()]


def test_base_url_must_not_end_with_slash() -> None:
    with pytest.raises(ValueError, match="base_url"):
        HttpQuoteSource(
            transport=httpx.MockTransport(lambda r: httpx.Response(200)), base_url=BASE + "/"
        )


# --- 오류 이름 나눔 --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "name"),
    [
        (418, ERROR_BLOCKED),
        (429, ERROR_RATE_LIMIT),
        (503, ERROR_MAINTENANCE),
        (500, ERROR_SERVER),
        (502, ERROR_SERVER),
        (504, ERROR_SERVER),
        (599, ERROR_SERVER),
        (400, error_unknown_exchange(400)),
        (401, error_unknown_exchange(401)),
        (404, error_unknown_exchange(404)),
        (301, error_unknown_exchange(301)),
    ],
)
def test_status_becomes_error_name(status: int, name: str) -> None:
    source, _ = make_source(body('{"error": {"name": "made_up_name", "message": "글"}}', status))
    with pytest.raises(QuoteError) as caught:
        source.get_candles("KRW-AAA", CandleUnit.DAY, to=None, count=1)
    assert caught.value.error_name == name
    assert caught.value.retry_after is None
    assert "made_up_name" not in str(caught.value)
    assert "글" not in caught.value.error_name


@pytest.mark.parametrize("status", [418, 429, 503, 500, 400])
def test_retry_after_is_read_when_the_response_gives_it(status: int) -> None:
    source, _ = make_source(body("", status, headers={"Retry-After": "120"}))
    with pytest.raises(QuoteError) as caught:
        source.list_markets()
    assert caught.value.retry_after == 120.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("120", 120.0),
        (" 7 ", 7.0),
        ("007", 7.0),
        ("0", None),
        ("000", None),
        ("-5", None),
        ("1.5", None),
        ("", None),
        ("soon", None),
        ("Wed, 21 Oct 2026 07:28:00 GMT", None),
        ("１２", None),  # 전각 숫자
        ("999999999", 999999999.0),
        ("1000000000", None),  # 9자리를 넘는다
    ],
)
def test_parse_retry_after(value: str | None, expected: float | None) -> None:
    assert parse_retry_after(value) == expected


@pytest.mark.parametrize(
    ("exception", "name"),
    [
        (httpx.ConnectTimeout, ERROR_TIMEOUT),
        (httpx.ReadTimeout, ERROR_TIMEOUT),
        (httpx.WriteTimeout, ERROR_TIMEOUT),
        (httpx.PoolTimeout, ERROR_TIMEOUT),
        (httpx.ConnectError, ERROR_NETWORK),
        (httpx.ReadError, ERROR_NETWORK),
        (httpx.WriteError, ERROR_NETWORK),
        (httpx.RemoteProtocolError, ERROR_NETWORK),
        (httpx.ProxyError, ERROR_NETWORK),
        (httpx.DecodingError, ERROR_BAD_RESPONSE),
    ],
)
def test_transport_exception_becomes_error_name(exception: type, name: str) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        raise exception("made up", request=request)

    source, recorder = make_source(respond)
    with pytest.raises(QuoteError) as caught:
        source.get_candles("KRW-AAA", CandleUnit.DAY, to=None, count=1)
    assert caught.value.error_name == name
    assert caught.value.retry_after is None
    assert len(recorder.requests) == 1  # 다시 보내지 않는다


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "{}",
        "[1]",
        '{"market": "KRW-AAA"}',
        "[" + candle_json("KRW-AAA", "2026-01-04T00:00:00", price="NaN") + "]",
        "[" + candle_json("KRW-AAA", "2026-01-04T00:00:00", price="Infinity") + "]",
        "[" + candle_json("KRW-AAA", "2026-01-04T00:00:00", price="true") + "]",
        "[" + candle_json("KRW-AAA", "2026-01-04T00:00:00", price="null") + "]",
        "[" + candle_json("KRW-AAA", "2026-01-04T00:00:00", price='"1,000"') + "]",
        "[" + candle_json("KRW-AAA", "2026-01-04T00:00:00", price='"NaN"') + "]",
        "[" + candle_json("KRW-AAA", "not a time") + "]",
        "[" + candle_json("KRW-BBB", "2026-01-04T00:00:00") + "]",  # 다른 마켓
        "[" + candle_json("krw-aaa", "2026-01-04T00:00:00") + "]",
        '[{"market": "KRW-AAA", "candle_date_time_utc": "2026-01-04T00:00:00"}]',
        "[" * 3000 + "]" * 3000,
    ],
)
def test_unreadable_candles_are_bad_response(text: str) -> None:
    source, _ = make_source(body(text))
    with pytest.raises(QuoteError) as caught:
        source.get_candles("KRW-AAA", CandleUnit.DAY, to=None, count=10)
    assert caught.value.error_name == ERROR_BAD_RESPONSE
    assert caught.value.retry_after is None
    # 응답의 값이 traceback의 앞 예외로 남지 않는다.
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "text",
    [
        "{}",
        '[{"market": "KRW-AAA"}]',  # 이름이 없다
        '[{"market": 5, "korean_name": "가가"}]',
        '[{"market": "KRW-AAA", "korean_name": null}]',
        "[null]",
    ],
)
def test_unreadable_markets_are_bad_response(text: str) -> None:
    source, _ = make_source(body(text))
    with pytest.raises(QuoteError) as caught:
        source.list_markets()
    assert caught.value.error_name == ERROR_BAD_RESPONSE


def test_error_payload_never_reaches_the_error_name() -> None:
    payload = json.dumps({"error": {"name": "secret-ish", "message": "거래소가 쓴 글"}})
    for status in (400, 418, 429, 500, 503):
        source, _ = make_source(body(payload, status))
        with pytest.raises(QuoteError) as caught:
            source.list_markets()
        assert "거래소가 쓴 글" not in repr(caught.value)
        assert "secret-ish" not in repr(caught.value)
