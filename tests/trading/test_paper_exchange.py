"""`PaperExchange`(M5 명세 3.4. 3단계 엔진을 `PaperStore`로 감쌈)의 시험.

3단계의 동작(M3 명세 3.7)을 바꾸지 않는지 본다. 가짜 시세(`tests/fakes/quote.py`)와 가짜 시계, 이
파일의 메모리 `PaperStore`로 한다. 값은 모두 만든 숫자다.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fakes.clock import FakeClock
from fakes.quote import BOOK, MINUTES, FakeQuoteSource

from coin_report_trader.core.alerts import Mode
from coin_report_trader.core.errors import ExchangeError, QuoteError
from coin_report_trader.core.exchange import ExchangeHolding, ExchangeRules, ExchangeTrade
from coin_report_trader.core.ports import MinuteCandle, PaperStore
from coin_report_trader.core.texts import ERROR_NETWORK, ERROR_TIMEOUT
from coin_report_trader.core.trade import (
    MIN_ORDER_FALLBACK,
    PAPER_FEE_RATE,
    FillRecord,
    OrderKind,
    OrderPlan,
    OrderReason,
    OrderRecord,
    OrderSide,
    OrderState,
    PaperAccount,
)
from coin_report_trader.trading.paper import make_holding, new_account
from coin_report_trader.trading.paper.exchange import PaperExchange
from coin_report_trader.trading.quote import QuoteClient

A = "KRW-AAA"
D = Decimal
T0 = datetime(2026, 1, 5, 10, 3, 20, tzinfo=UTC)


class MemoryStore:
    """시험의 `PaperStore`(M5 명세 3.4). DB 대신 메모리에 둔다."""

    def __init__(self, account: PaperAccount | None) -> None:
        self.account = account
        self.orders: dict[int, OrderRecord] = {}
        self.sent: dict[int, bool] = {}
        self.fills: list[FillRecord] = []
        self.writes: list[str] = []
        self.judged: dict[int, datetime] = {}
        """판정한 때(`orders.judged_until_utc`). 넣을 때는 비워 둔다(없으면 건 때를 올림한 분)."""

    def read_account(self, mode: Mode) -> PaperAccount | None:
        assert mode is Mode.PAPER
        return self.account

    def write_account(self, mode: Mode, account: PaperAccount) -> None:
        assert mode is Mode.PAPER
        self.account = account
        self.writes.append("account")

    def insert_order(
        self, plan: OrderPlan, *, mode: Mode, uid: str, round_id: int | None, created_utc: datetime
    ) -> OrderRecord:
        order = OrderRecord(
            order_id=len(self.orders) + 1,
            uid=uid,
            mode=mode,
            round_id=round_id,
            market=plan.market,
            side=plan.side,
            kind=plan.kind,
            reason=plan.reason,
            price=plan.price,
            qty=plan.qty,
            state=OrderState.OPEN,
            created_utc=created_utc,
            closed_utc=None,
            error_name=None,
        )
        self.orders[order.order_id] = order
        self.sent[order.order_id] = False
        return order

    def update_order(
        self,
        order_id: int,
        *,
        state: OrderState,
        closed_utc: datetime | None = None,
        error_name: str | None = None,
        sent: bool | None = None,
    ) -> None:
        order = self.orders[order_id]
        self.orders[order_id] = replace(
            order, state=state, closed_utc=closed_utc, error_name=error_name
        )
        if sent is not None:
            self.sent[order_id] = sent
        self.writes.append(f"order:{state}")

    def find_order(self, uid: str) -> OrderRecord | None:
        return next((o for o in self.orders.values() if o.uid == uid), None)

    def open_orders(self, mode: Mode, market: str | None = None) -> tuple[OrderRecord, ...]:
        return tuple(
            o
            for o in self.orders.values()
            if o.mode is mode and o.state is OrderState.OPEN and market in (None, o.market)
        )

    def apply_fill(self, fill: FillRecord, account: PaperAccount) -> FillRecord:
        written = replace(fill, fill_id=len(self.fills) + 1)
        self.fills.append(written)
        order = self.orders[fill.order_id]
        self.orders[fill.order_id] = replace(
            order, state=OrderState.FILLED, closed_utc=fill.filled_utc
        )
        self.account = account
        self.writes.append("fill")
        return written

    def fills_of(self, order_id: int) -> tuple[FillRecord, ...]:
        return tuple(f for f in self.fills if f.order_id == order_id)

    def read_judged_until(self, order_id: int) -> datetime | None:
        return self.judged.get(order_id)

    def set_judged_until(self, order_id: int, until: datetime) -> None:
        self.judged[order_id] = until


def setup(
    *, krw: str = "100000", holding: tuple[str, str] | None = None, min_order: Decimal | None = None
):
    clock = FakeClock(T0)
    quotes = FakeQuoteSource(prices={A: D(1000)}, clock=clock)
    quotes.set_book(A, D(1001), D(999))
    account = new_account(D(krw))
    if holding is not None:
        qty, avg = holding
        account = replace(
            account, holdings=(make_holding(A, D(qty), D(0), D(qty) * D(avg), D(qty)),)
        )
    store = MemoryStore(account)
    exchange = PaperExchange(store, QuoteClient(quotes, clock), clock, min_order=min_order)
    return exchange, store, quotes, clock


def plan(side: OrderSide, price: str | None, qty: str) -> OrderPlan:
    kind = OrderKind.LIMIT if price is not None else OrderKind.MARKET
    reason = OrderReason.JUDGMENT if price is not None else OrderReason.STOP_LOSS
    p = None if price is None else D(price)
    return OrderPlan(A, side, kind, reason, p, D(qty), (p or D(1000)) * D(qty))


def place(exchange: PaperExchange, store: MemoryStore, clock: FakeClock, p: OrderPlan, uid: str):
    """`app`의 차례(3.8.3): 식별값과 주문 값을 먼저 쓰고 낸다."""
    store.insert_order(p, mode=Mode.PAPER, uid=uid, round_id=1, created_utc=clock.now())
    return exchange.place(p, uid)


def candle(start: datetime, high: str, low: str) -> MinuteCandle:
    return MinuteCandle(market=A, start=start, high=D(high), low=D(low), close=D(low))


def at(minute: int, second: int = 0) -> datetime:
    return datetime(2026, 1, 5, 10, minute, second, tzinfo=UTC)


# --- 경계, 계정, 규칙 ---------------------------------------------------------------------------


def test_paper_exchange_is_an_exchange_port_in_paper_mode() -> None:
    exchange, store, _, _ = setup()
    assert exchange.mode is Mode.PAPER
    assert isinstance(store, PaperStore)
    assert repr(exchange) == "PaperExchange()"


def test_read_account_gives_available_krw_and_sellable_holdings() -> None:
    exchange, store, _, clock = setup(holding=("10", "980"))
    place(exchange, store, clock, plan(OrderSide.SELL, "1100", "4"), "u-sell")  # 4개를 묶음
    place(exchange, store, clock, plan(OrderSide.BUY, "990", "10"), "u-buy")  # 9,904.95원을 묶음
    account = exchange.read_account()
    assert account.krw == D("90095.05")
    assert account.krw_locked == D("9904.95")
    assert account.holdings == (
        ExchangeHolding(market=A, balance=D(6), locked=D(4), avg_buy_price=D(980)),
    )
    assert account.read_utc == clock.now()


def test_holdings_of_zero_are_not_shown() -> None:
    exchange, store, _, _ = setup()
    store.account = replace(
        new_account(D(1000)), holdings=(make_holding(A, D(0), D(0), D(0), D(0)),)
    )
    assert exchange.read_account().holdings == ()


def test_read_account_before_setup_is_refused() -> None:
    exchange, store, _, _ = setup()
    store.account = None
    with pytest.raises(ValueError, match="가상 계정"):
        exchange.read_account()


def test_rules_are_the_paper_fee_and_the_min_order() -> None:
    exchange, _, _, _ = setup()
    assert exchange.read_rules(A) == ExchangeRules(
        bid_fee=PAPER_FEE_RATE, ask_fee=PAPER_FEE_RATE, min_total=MIN_ORDER_FALLBACK, max_total=None
    )
    tested, _, _, _ = setup(min_order=D(7000))
    assert tested.read_rules(A).min_total == D(7000)


# --- 넣을 때(M3 명세 3.7.2 그대로) ---------------------------------------------------------------


def test_a_limit_buy_above_the_best_ask_fills_at_the_ask_at_once() -> None:
    exchange, store, quotes, clock = setup()
    result = place(exchange, store, clock, plan(OrderSide.BUY, "1010", "10"), "u-1")
    assert (result.error_name, result.unknown) == (None, False)
    snapshot = result.snapshot
    assert snapshot is not None
    assert (snapshot.state, snapshot.executed_qty, snapshot.program) == (
        OrderState.FILLED,
        D(10),
        True,
    )
    assert snapshot.trades == (
        ExchangeTrade(trade_key="paper", price=D(1001), qty=D(10), fee=D("5.005"), filled_utc=T0),
    )
    # 체결 줄과 계정은 한 트랜잭션(apply_fill)으로 썼다: 1001 × 10 × 1.0005 = 10,015.005
    assert store.writes == ["fill"]
    assert store.account is not None
    assert store.account.krw == D("89984.995")
    assert [r.kind for r in quotes.requests] == [BOOK]


def test_a_limit_buy_below_the_ask_is_locked_and_waits() -> None:
    exchange, store, _, clock = setup()
    result = place(exchange, store, clock, plan(OrderSide.BUY, "990", "10"), "u-2")
    assert result.snapshot is not None
    assert (result.snapshot.state, result.snapshot.trades) == (OrderState.OPEN, ())
    assert store.writes == ["account"]
    assert store.account is not None
    assert store.account.locked_krw == D("9904.95")


def test_market_sell_fills_at_the_best_bid() -> None:
    exchange, store, _, clock = setup(holding=("5", "1000"))
    result = place(exchange, store, clock, plan(OrderSide.SELL, None, "5"), "u-3")
    assert result.snapshot is not None
    assert result.snapshot.state is OrderState.FILLED
    assert [t.price for t in result.snapshot.trades] == [D(999)]
    fill = store.fills[0]
    # 5 × (999 × 0.9995 − 1000 × 1.0005) = 5 × (998.5005 − 1000.5)
    assert fill.realized_pnl == D("-9.9975")


def test_best_prices_not_read_means_not_placed() -> None:
    exchange, store, quotes, clock = setup()
    quotes.script(QuoteError(ERROR_NETWORK), kind=BOOK, market=A)
    result = place(exchange, store, clock, plan(OrderSide.BUY, "990", "10"), "u-4")
    assert (result.snapshot, result.error_name, result.unknown) == (None, ERROR_NETWORK, False)
    assert store.writes == []  # 계정도 묶음도 바뀌지 않았다


def test_place_needs_the_order_written_first_and_the_same_values() -> None:
    exchange, store, _, _ = setup()
    with pytest.raises(ValueError, match="먼저"):
        exchange.place(plan(OrderSide.BUY, "990", "10"), "u-missing")
    store.insert_order(
        plan(OrderSide.BUY, "990", "10"), mode=Mode.PAPER, uid="u-5", round_id=1, created_utc=T0
    )
    with pytest.raises(ValueError, match="다르다"):
        exchange.place(plan(OrderSide.BUY, "991", "10"), "u-5")


# --- 걸린 뒤의 판정(조회) ---------------------------------------------------------------------


def test_query_judges_the_open_order_with_minute_candles_example() -> None:
    # 10:03:20에 990에 매수를 걸었다. 10:04의 저가가 990이면 체결이 아니고, 10:05의 저가가 989이면
    # 990에 체결되고 체결 때는 10:06:00이다(M3 명세 3.7.2의 예).
    exchange, store, quotes, clock = setup()
    place(exchange, store, clock, plan(OrderSide.BUY, "990", "10"), "u-6")
    quotes.put_minutes(candle(at(4), "995", "990"))
    clock.set_now(at(5, 30))
    first = exchange.query("u-6")
    assert first is not None
    assert first.state is OrderState.OPEN
    quotes.put_minutes(candle(at(5), "995", "989"))
    clock.set_now(at(6, 10))
    second = exchange.query("u-6")
    assert second is not None
    assert second.state is OrderState.FILLED
    assert [(t.price, t.filled_utc) for t in second.trades] == [(D(990), at(6))]
    # 두 번째 조회는 이미 본 10:04 뒤(10:05부터)만 받았다.
    minute_requests = quotes.book_requests(MINUTES)
    assert [(r.to, r.count) for r in minute_requests] == [(at(5), 1), (at(6), 1)]
    # 체결은 묶은 원화로 샀다.
    assert store.account is not None
    assert (store.account.krw, store.account.locked_krw) == (D("90095.05"), D(0))


def test_query_before_a_complete_minute_does_not_ask_for_candles() -> None:
    exchange, store, quotes, clock = setup()
    place(exchange, store, clock, plan(OrderSide.BUY, "990", "10"), "u-7")
    clock.set_now(at(3, 59))
    snapshot = exchange.query("u-7")
    assert snapshot is not None
    assert snapshot.state is OrderState.OPEN
    assert quotes.book_requests(MINUTES) == []


def test_after_restart_the_judgment_starts_from_the_order_time() -> None:
    # 판정한 적이 없으면 새 객체(다시 켬)는 건 때를 올림한 분부터 소급해 본다(3.7.4).
    exchange, store, quotes, clock = setup()
    place(exchange, store, clock, plan(OrderSide.BUY, "990", "10"), "u-8")
    quotes.put_minutes(candle(at(4), "995", "989"))
    clock.set_now(at(30))
    fresh = PaperExchange(store, QuoteClient(quotes, clock), clock)
    snapshot = fresh.query("u-8")
    assert snapshot is not None
    assert snapshot.state is OrderState.FILLED
    assert [(r.to, r.count) for r in quotes.book_requests(MINUTES)] == [(at(30), 26)]


def test_query_failure_is_an_exchange_error() -> None:
    exchange, store, quotes, clock = setup()
    place(exchange, store, clock, plan(OrderSide.BUY, "990", "10"), "u-9")
    clock.set_now(at(6))
    quotes.script(QuoteError(ERROR_TIMEOUT), kind=MINUTES, market=A)
    with pytest.raises(ExchangeError) as caught:
        exchange.query("u-9")
    assert caught.value.error_name == ERROR_TIMEOUT


def test_query_of_closed_and_missing_orders() -> None:
    exchange, store, _, clock = setup()
    place(exchange, store, clock, plan(OrderSide.BUY, "1010", "10"), "u-10")
    filled = exchange.query("u-10")
    assert filled is not None
    assert (filled.state, filled.executed_qty) == (OrderState.FILLED, D(10))
    assert exchange.query("u-none") is None


# --- 취소(취소하기 전의 판정) -------------------------------------------------------------------


def test_cancel_unlocks_and_closes_the_order() -> None:
    exchange, store, _, clock = setup()
    place(exchange, store, clock, plan(OrderSide.BUY, "990", "10"), "u-11")
    clock.set_now(at(5))
    canceled = exchange.cancel("u-11")
    assert canceled is not None
    assert (canceled.state, canceled.trades) == (OrderState.CANCELED, ())
    order = store.find_order("u-11")
    assert order is not None
    assert (order.state, order.closed_utc) == (OrderState.CANCELED, at(5))
    assert store.account is not None
    assert (store.account.krw, store.account.locked_krw) == (D(100_000), D(0))


def test_cancel_judges_first_and_a_fill_is_not_canceled() -> None:
    exchange, store, quotes, clock = setup()
    place(exchange, store, clock, plan(OrderSide.BUY, "990", "10"), "u-12")
    quotes.put_minutes(candle(at(4), "995", "980"))
    clock.set_now(at(5, 40))
    result = exchange.cancel("u-12")
    assert result is not None
    assert result.state is OrderState.FILLED
    order = store.find_order("u-12")
    assert order is not None
    assert order.state is OrderState.FILLED


def test_cancel_without_candles_leaves_the_order_open() -> None:
    # 1분봉을 받지 못해 판정하지 못하면 취소하지 않는다(취소가 확인되지 않은 주문. M3 명세 3.7.2).
    exchange, store, quotes, clock = setup()
    place(exchange, store, clock, plan(OrderSide.BUY, "990", "10"), "u-13")
    clock.set_now(at(6))
    quotes.script(QuoteError(ERROR_NETWORK), kind=MINUTES, market=A)
    result = exchange.cancel("u-13")
    assert result is not None
    assert result.state is OrderState.OPEN
    order = store.find_order("u-13")
    assert order is not None
    assert order.state is OrderState.OPEN
    assert store.account is not None
    assert store.account.locked_krw == D("9904.95")


def test_cancel_of_closed_and_missing_orders() -> None:
    exchange, store, _, clock = setup()
    place(exchange, store, clock, plan(OrderSide.BUY, "1010", "10"), "u-14")
    done = exchange.cancel("u-14")
    assert done is not None
    assert done.state is OrderState.FILLED
    assert exchange.cancel("u-none") is None


# --- 걸린 주문 목록 -----------------------------------------------------------------------------


def test_open_orders_are_the_store_s_open_orders_and_all_ours() -> None:
    exchange, store, _, clock = setup()
    place(exchange, store, clock, plan(OrderSide.BUY, "990", "10"), "u-15")
    place(exchange, store, clock, plan(OrderSide.BUY, "1010", "1"), "u-16")  # 바로 체결
    clock.set_now(T0 + timedelta(seconds=5))
    orders = exchange.open_orders(A)
    assert [(o.uid, o.state, o.program) for o in orders] == [("u-15", OrderState.OPEN, True)]
    assert exchange.open_orders("KRW-BBB") == ()


# --- 판정한 때는 `PaperStore`에(M3 명세 3.12, 3.7.2), 주문 줄 앞의 최우선 호가 -----------------


def test_the_judged_time_is_written_to_the_store_and_a_fill_leaves_it() -> None:
    exchange, store, quotes, clock = setup()
    place(exchange, store, clock, plan(OrderSide.BUY, "990", "10"), "u-17")
    order = store.find_order("u-17")
    assert order is not None
    assert store.judged == {}  # 넣을 때는 쓰지 않는다(저장소가 건 때를 올림한 분으로 넣는다)
    quotes.put_minutes(candle(at(4), "995", "990"))
    clock.set_now(at(5, 30))
    exchange.query("u-17")
    assert store.judged == {order.order_id: at(5)}
    quotes.put_minutes(candle(at(5), "995", "989"))
    clock.set_now(at(6, 10))
    filled = exchange.query("u-17")
    assert filled is not None
    assert filled.state is OrderState.FILLED
    assert store.judged == {order.order_id: at(5)}  # 체결은 판정한 때를 고치지 않는다


def test_a_new_object_goes_on_from_the_stored_judged_time() -> None:
    # 다시 켬: 새 객체도 저장된 판정한 때(10:05) 뒤만 받는다(3.7.4).
    exchange, store, quotes, clock = setup()
    place(exchange, store, clock, plan(OrderSide.BUY, "990", "10"), "u-18")
    quotes.put_minutes(candle(at(4), "995", "990"))
    clock.set_now(at(5, 30))
    exchange.query("u-18")
    clock.set_now(at(8, 5))
    fresh = PaperExchange(store, QuoteClient(quotes, clock), clock)
    snapshot = fresh.query("u-18")
    assert snapshot is not None
    assert snapshot.state is OrderState.OPEN
    assert [(r.to, r.count) for r in quotes.book_requests(MINUTES)] == [(at(5), 1), (at(8), 3)]
    order = store.find_order("u-18")
    assert order is not None
    assert store.judged[order.order_id] == at(8)


def test_a_cancel_after_a_judgment_in_the_same_minute_asks_for_no_more_candles() -> None:
    exchange, store, quotes, clock = setup()
    place(exchange, store, clock, plan(OrderSide.BUY, "990", "10"), "u-19")
    quotes.put_minutes(candle(at(4), "995", "990"))
    clock.set_now(at(5, 30))
    exchange.query("u-19")
    canceled = exchange.cancel("u-19")
    assert canceled is not None
    assert canceled.state is OrderState.CANCELED
    assert len(quotes.book_requests(MINUTES)) == 1  # 취소 전 판정은 저장된 자리부터라 볼 봉이 없다


def test_best_prices_read_before_the_row_are_used_without_reading_again() -> None:
    exchange, store, quotes, clock = setup()
    p = plan(OrderSide.BUY, "1010", "10")
    store.insert_order(p, mode=Mode.PAPER, uid="u-20", round_id=1, created_utc=clock.now())
    result = exchange.place(p, "u-20", best=(D(1005), D(1000)))
    assert result.snapshot is not None
    assert [t.price for t in result.snapshot.trades] == [D(1005)]  # 넘긴 최우선 매도 호가에
    assert quotes.requests == []
    store.insert_order(
        plan(OrderSide.BUY, "990", "10"), mode=Mode.PAPER, uid="u-21", round_id=1, created_utc=T0
    )
    waiting = exchange.place(plan(OrderSide.BUY, "990", "10"), "u-21", best=(D(1005), D(1000)))
    assert waiting.snapshot is not None
    assert waiting.snapshot.state is OrderState.OPEN
    assert quotes.requests == []
