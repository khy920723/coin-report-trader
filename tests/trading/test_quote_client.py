"""`QuoteClient`의 코인 목록 시험. 가짜 시세와 가짜 시계로 한다.

봉 받기(`fetch_recent`)의 시험은 `test_fetch_recent.py`에 있다. 1단계 보완 전의 `fetch_candles`와
그 시험은 43번이 지웠다(M1 명세 4절).
"""

from datetime import UTC, datetime

import pytest
from fakes.clock import FakeClock
from fakes.quote import FakeQuoteSource

from coin_report_trader.core import Market, QuoteError
from coin_report_trader.core.texts import ERROR_BLOCKED
from coin_report_trader.trading.quote import QuoteClient

START = datetime(2026, 1, 5, 3, 0, tzinfo=UTC)
"""가짜 시계의 처음 시각."""


def setup() -> tuple[QuoteClient, FakeQuoteSource, FakeClock]:
    clock = FakeClock(START)
    source = FakeQuoteSource(clock=clock)
    return QuoteClient(source, clock), source, clock


# --- 코인 목록 -------------------------------------------------------------------------------


def test_list_markets_is_one_request() -> None:
    client, source, _ = setup()
    source.set_markets([Market("KRW-AAA", "가가", False), Market("KRW-BBB", "나나", True)])
    assert client.list_markets() == (
        Market("KRW-AAA", "가가", False),
        Market("KRW-BBB", "나나", True),
    )
    assert [r.kind for r in source.requests] == ["markets"]


def test_list_markets_error_is_raised_as_is() -> None:
    client, source, _ = setup()
    source.script(QuoteError(ERROR_BLOCKED, 120.0), kind="markets")
    with pytest.raises(QuoteError) as caught:
        client.list_markets()
    assert (caught.value.error_name, caught.value.retry_after) == (ERROR_BLOCKED, 120.0)
    assert len(source.requests) == 1
