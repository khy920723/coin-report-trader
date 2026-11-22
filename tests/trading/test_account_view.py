"""계정 화면의 읽기와 셈(M5 명세 3.15.2)의 시험.

3.15.2의 예, 평가하지 못하는 줄, 합계의 셈, 계좌 응답의 두 필드(`unit_currency`,
`avg_buy_price_modified`)를 넣는다. 값은 모두 만든 숫자이고, 실제 계좌의 값이나 응답을 옮기지
않는다.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fakes.clock import FakeClock
from fakes.quote import FakeQuoteSource
from fakes.upbit import TEST_ACCESS_KEY, TEST_SECRET_KEY, FakeUpbitTransport

from coin_report_trader.core.errors import QuoteError
from coin_report_trader.core.ports import TransportResponse
from coin_report_trader.core.texts import ERROR_NETWORK
from coin_report_trader.core.views import AccountRow, AccountState
from coin_report_trader.trading.live import (
    NOTE_NO_AVG,
    NOTE_NO_KRW_MARKET,
    NOTE_NO_PRICE,
    account_price_markets,
    build_account_view,
)
from coin_report_trader.trading.quote import QuoteClient
from coin_report_trader.trading.upbit import UpbitClient, UpbitKeys
from coin_report_trader.trading.upbit.client import UpbitBalance

A = "KRW-AAA"
B = "KRW-BBB"
C = "KRW-CCC"
D = Decimal
T = datetime(2026, 1, 5, 3, 0, tzinfo=UTC)
KEYS = UpbitKeys(TEST_ACCESS_KEY, TEST_SECRET_KEY)


def bal(currency: str, qty: str, avg: str | None, *, locked: str = "0", unit="KRW", mod=False):
    return UpbitBalance(
        currency=currency,
        balance=D(qty),
        locked=D(locked),
        avg_buy_price=None if avg is None else D(avg),
        unit_currency=unit,
        avg_buy_price_modified=mod,
    )


# --- 3.15.2의 예 ----------------------------------------------------------------------------


def test_example_of_3_15_2() -> None:
    # 원화 주문 가능 100,000원, 묶임 20,000원. AAA 10개(평균 1,000, 현재가 1,100), BBB 2개
    # (평균 매수가 없음, 현재가 500), CCC 5개(원화 마켓 없음).
    accounts = [
        bal("KRW", "100000", None, locked="20000"),
        bal("AAA", "10", "1000"),
        bal("BBB", "2", None),
        bal("CCC", "5", None),
    ]
    view = build_account_view(accounts, {A: D(1100), B: D(500)}, {A, B}, {A}, T)
    assert view.state is AccountState.OK
    assert (view.krw_available, view.krw_locked) == (D(100_000), D(20_000))
    aaa, bbb, ccc = view.rows
    assert aaa == AccountRow(
        currency="AAA",
        market=A,
        qty=D(10),
        locked=D(0),
        avg_buy_price=D(1000),
        avg_modified=False,
        price=D(1100),
        buy_amount=D(10_000),
        value=D(11_000),
        pnl=D(1000),
        pnl_rate=D("0.1"),
        picked=True,
        note=None,
    )
    assert (bbb.value, bbb.buy_amount, bbb.pnl, bbb.pnl_rate, bbb.note) == (
        D(1000),
        None,
        None,
        None,
        NOTE_NO_AVG,
    )
    assert (ccc.market, ccc.price, ccc.value, ccc.buy_amount, ccc.pnl, ccc.note) == (
        None,
        None,
        None,
        None,
        None,
        NOTE_NO_KRW_MARKET,
    )
    assert (view.holdings_value, view.total_assets) == (D(12_000), D(132_000))
    assert (view.total_buy, view.total_pnl, view.total_pnl_rate) == (D(10_000), D(1000), D("0.1"))
    assert (view.excluded_value, view.excluded_pnl) == (1, 2)
    assert (view.read_utc, view.error_name, view.reading) == (T, None, False)


def test_price_markets_of_the_example_are_only_the_listed_ones() -> None:
    accounts = [bal("KRW", "1", None), bal("AAA", "1", "1"), bal("BBB", "1", None)]
    accounts += [bal("CCC", "1", None), bal("DDD", "0", "1")]  # DDD는 보유가 없다
    assert account_price_markets(accounts, {A, B, "KRW-DDD"}) == (A, B)


# --- 평가하지 못하는 줄 ------------------------------------------------------------------------


def test_failed_price_request_marks_every_asked_row() -> None:
    accounts = [bal("KRW", "5000", None), bal("AAA", "10", "1000"), bal("CCC", "1", "10")]
    view = build_account_view(accounts, None, {A}, set(), T)
    aaa, ccc = sorted(view.rows, key=lambda r: r.currency)
    assert (aaa.price, aaa.value, aaa.pnl, aaa.note) == (None, None, None, NOTE_NO_PRICE)
    assert aaa.buy_amount == D(10_000)  # 매수 금액은 셈할 수 있다
    assert ccc.note == NOTE_NO_KRW_MARKET
    assert view.state is AccountState.OK  # 계좌는 읽었다
    assert (view.holdings_value, view.total_assets) == (D(0), D(5000))
    assert (view.total_buy, view.total_pnl, view.total_pnl_rate) == (D(0), D(0), None)
    assert (view.excluded_value, view.excluded_pnl) == (2, 2)


def test_a_market_missing_from_the_reply_is_not_read() -> None:
    accounts = [bal("AAA", "1", "1000"), bal("BBB", "1", "1000")]
    view = build_account_view(accounts, {A: D(1000)}, {A, B}, set(), T)
    notes = {row.currency: row.note for row in view.rows}
    assert notes == {"AAA": None, "BBB": NOTE_NO_PRICE}


@pytest.mark.parametrize(
    ("avg", "unit"), [(None, "KRW"), ("0", "KRW"), ("1000", "BTC"), ("1000", None)]
)
def test_no_average_price_rows_are_valued_but_not_counted_for_pnl(avg, unit) -> None:
    view = build_account_view([bal("AAA", "3", avg, unit=unit)], {A: D(1200)}, {A}, set(), T)
    (row,) = view.rows
    assert (row.avg_buy_price, row.buy_amount, row.pnl, row.note) == (None, None, None, NOTE_NO_AVG)
    assert row.value == D(3600)
    assert (view.holdings_value, view.total_assets, view.excluded_value, view.excluded_pnl) == (
        D(3600),
        D(3600),
        0,
        1,
    )


def test_no_market_note_comes_before_the_average_note() -> None:
    view = build_account_view([bal("CCC", "5", None)], {}, set(), set(), T)
    assert view.rows[0].note == NOTE_NO_KRW_MARKET


# --- 줄과 합계 ---------------------------------------------------------------------------------


def test_only_held_coins_are_rows_and_locked_counts_as_held() -> None:
    accounts = [
        bal("AAA", "0", "1000"),  # 고른 코인이라도 보유가 0이면 넣지 않는다
        bal("BBB", "0", "1000", locked="2"),  # 묶인 것만 있어도 보유다
    ]
    view = build_account_view(accounts, {B: D(1000)}, {A, B}, {A, B}, T)
    assert [(r.currency, r.qty, r.locked, r.picked) for r in view.rows] == [
        ("BBB", D(2), D(2), True)
    ]


def test_buy_amount_and_value_count_the_locked_qty_too() -> None:
    # 주문 가능 8개, 묶임 2개, 평균 1,000, 현재가 1,100: 보유 10개로 셈한다.
    view = build_account_view([bal("AAA", "8", "1000", locked="2")], {A: D(1100)}, {A}, {A}, T)
    (row,) = view.rows
    assert (row.qty, row.locked) == (D(10), D(2))
    assert (row.buy_amount, row.value, row.pnl, row.pnl_rate) == (
        D(10_000),
        D(11_000),
        D(1000),
        D("0.1"),
    )
    assert (view.holdings_value, view.total_buy, view.total_pnl) == (
        D(11_000),
        D(10_000),
        D(1000),
    )
    assert view.total_pnl_rate == D("0.1")


def test_rows_are_ordered_by_value_then_unvalued_then_abc() -> None:
    accounts = [
        bal("EEE", "1", "1"),  # 평가하지 못함(원화 마켓 없음)
        bal("BBB", "1", "1"),  # 1,000원
        bal("AAA", "2", "1"),  # 2,000원
        bal("CCC", "1", "1"),  # 1,000원(BBB와 같음)
        bal("DDD", "1", "1"),  # 평가하지 못함
    ]
    prices = {A: D(1000), B: D(1000), C: D(1000)}
    view = build_account_view(accounts, prices, {A, B, C}, set(), T)
    assert [row.currency for row in view.rows] == ["AAA", "BBB", "CCC", "DDD", "EEE"]


def test_totals_use_only_rows_with_both_buy_and_value() -> None:
    accounts = [
        bal("KRW", "1000", None, locked="500"),
        bal("AAA", "10", "100"),  # 매수 1,000, 평가 1,200, +200
        bal("BBB", "4", "500"),  # 매수 2,000, 평가 1,600, −400
        bal("CCC", "1", None),  # 평가 300(손익 셈 없음)
    ]
    prices = {A: D(120), B: D(400), C: D(300)}
    view = build_account_view(accounts, prices, {A, B, C}, set(), T)
    assert view.holdings_value == D(3100)
    assert view.total_assets == D(4600)
    assert (view.total_buy, view.total_pnl) == (D(3000), D(-200))
    # 수익률 = −200 ÷ 3,000(평가 합 − 총 매수로 셈하지 않는다)
    assert view.total_pnl_rate == D(-200) / D(3000)
    assert (view.excluded_value, view.excluded_pnl) == (0, 1)


def test_modified_average_is_shown_and_multiple_krw_lines_are_added() -> None:
    accounts = [bal("KRW", "100", None), bal("AAA", "1", "900", mod=True), bal("KRW", "50", None)]
    view = build_account_view(accounts, {A: D(1000)}, {A}, set(), T)
    assert view.rows[0].avg_modified is True
    assert view.krw_available == D(150)


def test_nothing_held() -> None:
    view = build_account_view([bal("KRW", "0", None)], {}, set(), set(), T)
    assert view.rows == ()
    assert (view.holdings_value, view.total_assets, view.total_pnl_rate) == (D(0), D(0), None)


# --- 계좌 응답의 두 필드(9번이 accounts()에 더함) ------------------------------------------------


def test_accounts_read_unit_currency_and_avg_modified() -> None:
    clock = FakeClock(T)
    fake = FakeUpbitTransport(clock=clock)
    fake.set_account(krw=D(1000), coins={"AAA": (D(2), D(900)), "BBB": (D(1), D(50))})
    fake.set_coin_details("AAA", avg_buy_price_modified=True)
    fake.set_coin_details("BBB", unit_currency="BTC")
    balances = UpbitClient(KEYS, fake, clock).accounts()
    details = {b.currency: (b.unit_currency, b.avg_buy_price_modified) for b in balances}
    assert details == {"KRW": ("KRW", False), "AAA": ("KRW", True), "BBB": ("BTC", False)}
    view = build_account_view(balances, {A: D(1000), B: D(60)}, {A, B}, {A}, T)
    notes = {row.currency: (row.avg_modified, row.note) for row in view.rows}
    assert notes == {"AAA": (True, None), "BBB": (False, NOTE_NO_AVG)}


class _OneReply:
    """정한 응답 하나를 주는 운반(계좌 응답의 꼴을 바꿔 보는 데 쓴다)."""

    def __init__(self, body: object) -> None:
        self._text = json.dumps(body)

    def request(self, method, path, *, query, body, headers) -> TransportResponse:
        return TransportResponse(200, {}, self._text)


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({}, (None, False)),
        ({"unit_currency": 1, "avg_buy_price_modified": "yes"}, (None, False)),
        ({"unit_currency": "KRW", "avg_buy_price_modified": True}, ("KRW", True)),
    ],
)
def test_missing_or_odd_fields_do_not_fail_the_whole_account(extra: dict, expected) -> None:
    item = {"currency": "AAA", "balance": "1", "locked": "0", "avg_buy_price": "10", **extra}
    clock = FakeClock(T)
    (balance,) = UpbitClient(KEYS, _OneReply([item]), clock).accounts()  # type: ignore[arg-type]
    assert (balance.unit_currency, balance.avg_buy_price_modified) == expected


# --- 묶은 현재가 읽기와 함께 ---------------------------------------------------------------------


def test_view_from_one_batched_price_request() -> None:
    clock = FakeClock(T)
    quotes = FakeQuoteSource(prices={A: D(1100), B: D(500)}, clock=clock)
    client = QuoteClient(quotes, clock)
    accounts = [bal("AAA", "10", "1000"), bal("BBB", "2", None), bal("CCC", "5", None)]
    markets = account_price_markets(accounts, {A, B})
    prices = client.read_prices(markets)
    assert [r.market for r in quotes.prices_requests()] == ["KRW-AAA,KRW-BBB"]
    view = build_account_view(accounts, prices, {A, B}, set(), T)
    assert view.holdings_value == D(12_000)
    quotes.script(QuoteError(ERROR_NETWORK), kind="prices")
    with pytest.raises(QuoteError):
        client.read_prices(markets)
