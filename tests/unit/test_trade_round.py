"""3단계 잇기 1(M3 명세 4절 23번): 설정의 매매 칸, 회차의 주문, 1분 점검, 매매 알림.

자료 스레드, 가짜 실행기, 가짜 시세(호가, 호가 단위, 1분봉), 가짜 시계로 본다. 진짜 `claude`와
거래소는 부르지 않는다. 값은 모두 만든 값이다(코인 `KRW-AAA`, 현재가 100, 호가 단위 1).
"""

import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fakes.ai import FakeAiRunner, answered, good_answers, timeout
from fakes.clock import FakeClock
from fakes.quote import MINUTES, OK, FakeQuoteSource, make_candles
from fakes.stage3 import stage3_trade, write_stage3

from coin_report_trader.ai import inputs as ai_inputs
from coin_report_trader.app.alert_rules import AlertSpec, report_failed
from coin_report_trader.app.data_thread import DataWorker
from coin_report_trader.core import (
    AiChoice,
    AiSlot,
    AlertCategory,
    BelowStopView,
    CandleUnit,
    CoinTradeView,
    Effort,
    FillRecord,
    Market,
    MinuteCandle,
    Mode,
    NoOrderReason,
    OrderKind,
    OrderPlan,
    OrderReason,
    OrderSide,
    OrderState,
    PaperAccount,
    PaperHolding,
    QuoteError,
    RoundNowView,
    SaveResult,
    SettingsView,
    TodayView,
    TradeMethod,
    UiUpdate,
)
from coin_report_trader.core.texts import (
    BELOW_STOP_PRICE_UNREAD,
    ERROR_BLOCKED,
    ERROR_NETWORK,
    STOP_ONLY_LEFT,
    error_unknown_exchange,
    order_summary_text,
    qty_text,
    trade_balance_line,
    won_text,
)
from coin_report_trader.notify import alert_message
from coin_report_trader.storage import reports as report_store
from coin_report_trader.storage import trading as trade_store
from coin_report_trader.storage.alerts import list_alerts
from coin_report_trader.storage.database import migrate, open_database
from coin_report_trader.storage.settings import read_first_setup_level, write_settings
from coin_report_trader.trading.paper import lock_order

AAA, BBB = "KRW-AAA", "KRW-BBB"
HOUR = CandleUnit.MINUTE_60
SAFETY = 10.0
THIS_HOUR = datetime(2026, 1, 5, 3, tzinfo=UTC)
FOUR = tuple(AiChoice(slot, "haiku", Effort.LOW) for slot in AiSlot)
PRICE = Decimal(100)
PARTS = [("검색 판별", 1), ("봉 판별", 1), ("최종 예측", 1)]
# 코인 하나(100%)의 코인 예산 1,000,000원 × 첫 매수 20% = 200,000원(`stage3_trade`의 만든 값)
FIRST_AMOUNT = Decimal(200_000)


@dataclass
class Sink:
    items: list[UiUpdate] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __call__(self, update: UiUpdate) -> None:
        with self.lock:
            self.items.append(update)

    def of(self, kind: type) -> list[Any]:
        with self.lock:
            return [item for item in self.items if isinstance(item, kind)]


@dataclass
class Rig:
    worker: DataWorker
    sink: Sink
    clock: FakeClock
    quote: FakeQuoteSource
    runner: FakeAiRunner
    db: Path

    def start(self) -> None:
        self.worker.start()
        self.settle()

    def settle(self) -> None:
        assert self.worker.wait_idle(SAFETY), "자료 스레드가 쉬지 않는다"

    def round(self) -> None:
        due = self.worker._next_due
        assert due is not None
        self.clock.advance((due - self.clock.now()).total_seconds())
        self.settle()
        deadline = time.monotonic() + SAFETY
        while self.worker._pending and time.monotonic() < deadline:
            time.sleep(0.005)
        assert not self.worker._pending, "세션이 끝나지 않았다"
        self.settle()

    def advance(self, seconds: float) -> None:
        self.clock.advance(seconds)
        self.settle()

    def query(self, sql: str, *args: Any) -> list[tuple[Any, ...]]:
        conn = open_database(self.db)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def codes(self) -> list[str]:
        return [alert.code for alert in self.alerts()]

    def alerts(self) -> list[Any]:
        conn = open_database(self.db)
        try:
            return list(reversed(list_alerts(conn)))
        finally:
            conn.close()

    def orders(self) -> list[Any]:
        conn = open_database(self.db)
        try:
            return list(
                reversed(
                    trade_store.list_orders(
                        conn,
                        Mode.PAPER,
                        start=datetime(2000, 1, 1, tzinfo=UTC),
                        end=datetime(2100, 1, 1, tzinfo=UTC),
                    )
                )
            )
        finally:
            conn.close()

    def account(self) -> PaperAccount:
        conn = open_database(self.db)
        try:
            account = trade_store.read_paper_account(conn, Mode.PAPER)
        finally:
            conn.close()
        assert account is not None
        return account

    def last_report(self, market: str = AAA) -> Any:
        conn = open_database(self.db)
        try:
            items = report_store.list_round_items(conn, market, Mode.PAPER)
            view = report_store.read_report(conn, items[0].report_id)
        finally:
            conn.close()
        assert view is not None
        return view

    def answer(self, **kwargs: Any) -> None:
        self.runner.plan(AAA, lambda req: answered(good_answers(AAA, **kwargs)))


def make_rig(
    tmp_path: Path,
    *,
    coins: tuple[str, ...] = (AAA,),
    method: TradeMethod = TradeMethod.TREND,
    done: bool = True,
    seed: Callable[[Any], None] | None = None,
    min_order: Decimal | None = None,
) -> Rig:
    db = tmp_path / "app.db"
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    conn = open_database(db)
    try:
        migrate(conn)
        view = SettingsView(coins, 30, done, candle_unit=HOUR, candle_count=100, ai_choices=FOUR)
        if done:
            write_stage3(conn, replace(view, trade=stage3_trade(coins, method=method)))
        else:
            write_settings(conn, view)
        if seed is not None:
            seed(conn)
    finally:
        conn.close()
    clock = FakeClock()
    quote = FakeQuoteSource(
        markets=[Market(c, f"이름 {c}", False) for c in (AAA, BBB)], clock=clock
    )
    for coin in (AAA, BBB):
        quote.set_candles(coin, HOUR, make_candles(coin, HOUR, 200, last_start=THIS_HOUR))
    quote.set_prices({AAA: PRICE, BBB: PRICE})
    runner = FakeAiRunner()
    sink = Sink()
    worker = DataWorker(
        database_file=db,
        logs_dir=logs,
        clock=clock,
        quote=quote,
        sink=sink,
        ai=runner,
        sessions_root=tmp_path / "ai-sessions",
        min_order=min_order,
    )
    return Rig(worker, sink, clock, quote, runner, db)


@pytest.fixture
def build(tmp_path: Path) -> Iterator[Callable[..., Rig]]:
    rigs: list[Rig] = []

    def factory(**kwargs: Any) -> Rig:
        root = tmp_path / f"r{len(rigs)}"
        root.mkdir()
        rig = make_rig(root, **kwargs)
        rigs.append(rig)
        return rig

    yield factory
    for rig in rigs:
        rig.worker.stop()
        rig.worker.join(SAFETY)


def holding_seed(market: str, qty: str, avg: str) -> Callable[[Any], None]:
    """가상 보유 하나를 심는다(매수 합 = 평균 매수가 × 수량)."""

    def seed(conn: Any) -> None:
        account = trade_store.read_paper_account(conn, Mode.PAPER)
        assert account is not None
        holding = PaperHolding(
            market=market,
            qty=Decimal(qty),
            locked_qty=Decimal(0),
            avg_price=Decimal(avg),
            buy_amount_sum=Decimal(avg) * Decimal(qty),
            buy_qty_sum=Decimal(qty),
        )
        trade_store.write_paper_account(
            conn, Mode.PAPER, replace(account, holdings=(*account.holdings, holding))
        )

    return seed


# --- 회차의 주문(3.5.1의 6) -------------------------------------------------------------------


def test_a_trend_buy_is_stored_open_with_a_uid_one_a27_and_the_report_points_to_it(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    rig.answer(direction="오를 것", buy_price="99")
    rig.round()
    (order,) = rig.orders()
    qty = Decimal("2020.20202020")  # 200,000 ÷ 99를 소수 여덟째 자리에서 내림(3.8.1)
    assert (order.side, order.kind, order.reason, order.price, order.qty, order.state) == (
        OrderSide.BUY, OrderKind.LIMIT, OrderReason.JUDGMENT, Decimal(99), qty, OrderState.OPEN,
    )  # fmt: skip
    assert len(order.uid) == 36
    assert rig.query("SELECT sent, judged_until_utc FROM orders") == [(1, "2026-01-05T04:01:00Z")]
    assert rig.account().locked_krw == Decimal(99) * qty * Decimal("1.0005")
    placed = [a for a in rig.alerts() if a.code == "A-27"]
    assert [(a.category, a.coin, a.text) for a in placed] == [
        (AlertCategory.TRADE, AAA, "주문을 냈습니다(지정가 매수 99원 × 2020.20202020).")
    ]
    assert "A-28" not in rig.codes()
    view = rig.last_report()
    assert view.order_summary == "지정가 매수 99원 × 2020.20202020"
    assert view.no_order_reason is None
    # 주문을 저장한 때 − T(04:00). 10초 뒤에 받고 그 뒤에 냈다
    assert view.elapsed_order_seconds >= view.elapsed_report_seconds >= 10
    assert rig.query("SELECT method, order_id FROM reports") == [("trend", order.order_id)]
    assert rig.query("SELECT stage, state FROM rounds") == [("기록", "done")]


def test_a_buy_at_or_above_the_best_ask_fills_at_once_with_a27_and_a28(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    rig.quote.set_book(AAA, Decimal(101), Decimal(100))
    rig.answer(direction="오를 것", buy_price="102")  # 현재가 × 1.02 안이고 최우선 매도 호가 이상
    rig.round()
    (order,) = rig.orders()
    assert order.state is OrderState.FILLED
    qty = Decimal("1960.78431372")  # 200,000 ÷ 102
    (fill,) = rig.query("SELECT price, qty, fee FROM fills")
    assert (Decimal(fill[0]), Decimal(fill[1])) == (Decimal(101), qty)  # 최우선 매도 호가에
    assert Decimal(fill[2]) == Decimal(101) * qty * Decimal("0.0005")
    account = rig.account()
    assert account.locked_krw == 0
    assert account.krw == Decimal(1_000_000) - Decimal(101) * qty * Decimal("1.0005")
    assert [c for c in rig.codes() if c in ("A-27", "A-28")] == ["A-27", "A-28"]
    filled = next(a for a in rig.alerts() if a.code == "A-28")
    assert filled.text == f"매수 {qty_text(qty)}이 101원에 체결되었습니다."


def test_mean_reversion_with_no_holding_and_up_does_nothing(build: Callable[..., Rig]) -> None:
    rig = build(method=TradeMethod.MEAN_REVERSION)
    rig.start()
    rig.answer(direction="오를 것")
    rig.round()
    assert rig.orders() == []
    view = rig.last_report()
    assert (view.order_summary, view.no_order_reason) == (None, NoOrderReason.NOTHING.value)
    assert view.elapsed_order_seconds == view.elapsed_report_seconds
    assert "A-27" not in rig.codes()


def test_a_failed_report_records_no_judgment_and_the_a17_has_the_stage_three_text(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    rig.runner.plan(AAA, lambda req: timeout())
    rig.round()
    view = rig.last_report()
    assert view.no_order_reason == NoOrderReason.NO_JUDGMENT.value
    (a17,) = [a for a in rig.alerts() if a.code == "A-17"]
    assert a17.text == "AI 보고서를 받지 못해 이번 회차에 주문하지 않았습니다(시간 초과)."
    assert rig.orders() == []


def test_a_price_read_failure_before_the_order_is_the_stage_three_a2(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    rig.answer(direction="오를 것", buy_price="99")
    # 첫 현재가(세션 앞)는 받고, 주문 앞에 새로 읽는 현재가는 받지 못한다
    rig.quote.script(OK, QuoteError(ERROR_NETWORK), kind="price", market=AAA)
    rig.round()
    assert rig.orders() == []
    (a2,) = [a for a in rig.alerts() if a.code == "A-2"]
    assert a2.text == "계정이나 현재가를 읽지 못해 이번 회차에 주문하지 않았습니다."
    assert (a2.detail.stage, a2.detail.error_name) == ("주문", ERROR_NETWORK)
    assert a2.detail.content == (
        "주문하기 전에 현재가, 호가 단위, 최우선 호가 가운데 하나를 읽지 못했습니다."
    )
    assert rig.last_report().no_order_reason == NoOrderReason.NO_PRICE_OR_TICK.value


def test_a_price_guard_is_an_a4_with_the_suggested_price(build: Callable[..., Rig]) -> None:
    rig = build()
    rig.start()
    rig.answer(direction="오를 것", buy_price="103")  # 100 × 1.02 = 102보다 크다
    rig.round()
    assert rig.orders() == []
    (a4,) = [a for a in rig.alerts() if a.code == "A-4"]
    assert (a4.category, a4.text) == (
        AlertCategory.LIMIT,
        "제안가(103원)가 가격 안전 검사에 걸려 주문하지 않았습니다(현재가 100원).",
    )
    assert rig.last_report().no_order_reason == NoOrderReason.PRICE_GUARD.value


def test_the_next_round_cancels_the_open_order_and_lists_it_in_final_trades(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    rig.answer(direction="오를 것", buy_price="99")
    rig.round()
    # 지난 판단이 있으므로 지난 결과의 글과 견준 근거가 든 답이다
    rig.answer(direction="오를 것", buy_price="99", result_text="지난 결과", compared=PARTS)
    rig.round()
    first, second = rig.orders()
    assert first.state is OrderState.CANCELED
    assert rig.query("SELECT canceled_by_round FROM orders WHERE id = ?", first.order_id) == [(1,)]
    assert second.state is OrderState.OPEN  # 주문 간격(60초)은 한 시간 뒤라 지났다
    files = rig.runner.requests[1].files[ai_inputs.FILE_FINAL_TRADES].splitlines()
    assert files[1].startswith(f"{AAA},체결되지 않고 끝난 주문 가격,99,")
    # 묶음은 둘째 주문의 것만 남는다
    assert rig.account().locked_krw == Decimal(99) * second.qty * Decimal("1.0005")


def test_a_sell_canceled_in_the_cleanup_adds_the_stop_only_line_to_the_a17(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=holding_seed(AAA, "100", "100"))
    rig.start()
    rig.answer(direction="내릴 것", predicted="98", sell_price="101")
    rig.round()
    (sell,) = rig.orders()
    assert (sell.side, sell.state, sell.qty) == (OrderSide.SELL, OrderState.OPEN, Decimal(100))
    assert rig.account().holdings[0].locked_qty == Decimal(100)
    rig.runner.plan(AAA, lambda req: timeout())
    rig.round()
    (a17,) = [a for a in rig.alerts() if a.code == "A-17"]
    assert a17.text.endswith(f" {STOP_ONLY_LEFT}")
    assert rig.account().holdings[0].locked_qty == 0


# --- 1분 점검(3.9) ---------------------------------------------------------------------------


def test_the_minute_check_fills_an_open_buy_when_the_low_crosses_the_price(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    rig.answer(direction="오를 것", buy_price="99")
    rig.round()
    (order,) = rig.orders()
    start = datetime(2026, 1, 5, 4, 1, tzinfo=UTC)
    touched = MinuteCandle(AAA, start, Decimal(100), Decimal(99), Decimal(99))
    crossed = MinuteCandle(
        AAA, start + timedelta(minutes=1), Decimal(100), Decimal(98), Decimal(98)
    )
    rig.quote.put_minutes(touched, crossed)
    rig.advance(180)
    (order,) = rig.orders()
    assert order.state is OrderState.FILLED
    assert rig.query("SELECT price, filled_utc FROM fills") == [("99", "2026-01-05T04:03:00Z")]
    account = rig.account()
    assert account.locked_krw == 0
    assert account.holdings[0].qty == order.qty
    assert rig.codes().count("A-28") == 1


def test_a_price_at_the_stop_line_sells_at_market_with_reason_stop_loss(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=holding_seed(AAA, "100", "100"))
    rig.start()
    assert rig.orders() == []  # 100은 손절선(95)보다 높다
    rig.quote.set_prices({AAA: Decimal(95)})
    rig.quote.set_book(AAA, Decimal(96), Decimal(94))
    rig.advance(60)
    (order,) = rig.orders()
    assert (order.kind, order.side, order.reason, order.state) == (
        OrderKind.MARKET, OrderSide.SELL, OrderReason.STOP_LOSS, OrderState.FILLED,
    )  # fmt: skip
    (fill,) = rig.query("SELECT price, qty, realized_pnl FROM fills")
    assert (Decimal(fill[0]), Decimal(fill[1])) == (Decimal(94), Decimal(100))
    expected = Decimal(100) * (Decimal(94) * Decimal("0.9995") - Decimal(100) * Decimal("1.0005"))
    assert Decimal(fill[2]) == expected
    assert rig.account().holdings[0].qty == 0
    assert [a.text for a in rig.alerts() if a.code == "A-27"] == [
        "주문을 냈습니다(시장가 매도 100.0)."
    ]


def test_a_failed_stop_loss_sell_is_one_a8_within_sixty_minutes(build: Callable[..., Rig]) -> None:
    rig = build(seed=holding_seed(AAA, "100", "100"))
    rig.start()
    rig.quote.set_prices({AAA: Decimal(90)})
    rig.quote.script(*[QuoteError(ERROR_NETWORK)] * 5, kind="book", market=AAA)
    rig.advance(60)
    rig.advance(60)
    assert rig.orders() == []
    (a8,) = [a for a in rig.alerts() if a.code == "A-8"]
    assert (a8.text, a8.detail.stage, a8.detail.error_name) == (
        "손절 매도에 실패했습니다. 1분 뒤 다시 시도합니다.", "손절", ERROR_NETWORK,
    )  # fmt: skip


def test_a_holding_without_an_average_price_is_one_a9(build: Callable[..., Rig]) -> None:
    def seed(conn: Any) -> None:
        account = trade_store.read_paper_account(conn, Mode.PAPER)
        assert account is not None
        holding = PaperHolding(AAA, Decimal(5), Decimal(0), None, Decimal(0), Decimal(0))
        trade_store.write_paper_account(conn, Mode.PAPER, replace(account, holdings=(holding,)))

    rig = build(seed=seed)
    rig.start()
    rig.advance(60)
    (a9,) = [a for a in rig.alerts() if a.code == "A-9"]
    assert (a9.detail.stage, a9.detail.error_name) == ("손절", "평균 매수가 없음")


def test_a_daily_loss_at_the_limit_cancels_program_buys_and_is_one_a12(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=holding_seed(AAA, "100", "100"))
    rig.start()
    rig.quote.set_prices({AAA: Decimal(60)})  # 손절: 100 × (60 × 0.9995 − 100.05) = −4,008원
    rig.advance(60)
    assert rig.codes().count("A-12") == 1
    rig.quote.set_prices({AAA: PRICE})
    rig.advance(60)
    assert rig.codes().count("A-12") == 1  # 하루에 한 번
    today = rig.sink.of(TodayView)[-1]
    assert today.limit_reached is True
    assert today.realized_pnl < -today.daily_loss_limit
    trade = rig.sink.of(CoinTradeView)[-1]
    assert NoOrderReason.DAILY_LOSS_LIMIT.value in trade.buy_blocked


def test_startup_closes_unsent_orders_as_not_sent(build: Callable[..., Rig]) -> None:
    def seed(conn: Any) -> None:
        from coin_report_trader.core import OrderPlan

        plan = OrderPlan(
            AAA, OrderSide.BUY, OrderKind.LIMIT, OrderReason.JUDGMENT, Decimal(99), Decimal(1),
            Decimal(99),
        )  # fmt: skip
        when = datetime(2026, 1, 5, 2, tzinfo=UTC)
        trade_store.insert_order(
            conn, plan, mode=Mode.PAPER, uid="u-1", round_id=None, created_at=when,
            judged_until=when,
        )  # fmt: skip

    rig = build(seed=seed)
    rig.start()
    (order,) = rig.orders()
    assert order.state is OrderState.NOT_SENT
    assert rig.account().locked_krw == 0  # 묶이기 전이었으므로 풀 것이 없다


# --- 설정 저장(3.11) -----------------------------------------------------------------------


def test_a_first_setup_save_writes_level_three_the_account_and_the_setting_events(
    build: Callable[..., Rig],
) -> None:
    rig = build(done=False)
    rig.start()
    assert rig.sink.of(SettingsView)[-1].first_setup_done is False
    rig.worker.save_settings(
        (AAA,), 30, first_setup=True, candle_unit=HOUR, candle_count=100, trade=stage3_trade((AAA,))
    )
    rig.settle()
    assert rig.sink.of(SaveResult)[-1].ok is True
    view = rig.sink.of(SettingsView)[-1]
    assert view.first_setup_done is True
    assert view.trade == stage3_trade((AAA,))
    conn = open_database(rig.db)
    try:
        assert read_first_setup_level(conn) == "3"
    finally:
        conn.close()
    assert rig.account().krw == Decimal(1_000_000)
    assert rig.query("SELECT kind, market, value FROM setting_events ORDER BY id") == [
        ("coin_added", AAA, None),
        ("method_changed", AAA, "mean_reversion"),
    ]
    assert rig.sink.of(RoundNowView)  # 1분 점검이 켜졌다


def test_an_old_record_at_level_two_is_not_done(build: Callable[..., Rig]) -> None:
    def downgrade(conn: Any) -> None:
        conn.execute("UPDATE settings SET value = '2' WHERE key = 'first_setup_level'")
        conn.commit()

    rig = build(seed=downgrade)
    rig.start()
    assert rig.sink.of(SettingsView)[-1].first_setup_done is False
    assert rig.quote.candle_requests() == []  # 처음 설정을 다시 보이고 받지 않는다


def test_a_setting_that_fails_the_check_is_not_saved_and_names_the_field(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    saved = stage3_trade((AAA,), method=TradeMethod.TREND)
    bad = replace(saved, coin_ratios={AAA: Decimal("99")})
    rig.worker.save_settings((AAA,), 30, first_setup=False, trade=bad)
    rig.settle()
    result = rig.sink.of(SaveResult)[-1]
    assert result.ok is False
    assert result.field_errors == {
        "coin_ratios": "코인별 투자 비율의 합이 100%여야 합니다(지금 99.00%)."
    }
    assert rig.sink.of(SettingsView)[-1].trade == saved  # 저장된 값 그대로


def test_adding_a_coin_held_below_its_stop_line_asks_first(build: Callable[..., Rig]) -> None:
    rig = build(seed=holding_seed(BBB, "10", "100"))
    rig.start()
    rig.quote.set_prices({BBB: Decimal(90)})  # 손절선 95 아래
    rig.worker.refresh_markets()
    trade = stage3_trade((AAA, BBB))
    rig.worker.save_settings((AAA, BBB), 30, first_setup=False, trade=trade)
    rig.settle()
    assert rig.sink.of(BelowStopView) == [
        BelowStopView(BBB, Decimal(100), Decimal(90), Decimal(95))
    ]
    assert rig.sink.of(SettingsView)[-1].coins == (AAA,)  # 저장하지 않았다
    rig.worker.save_settings(
        (AAA, BBB), 30, first_setup=False, trade=trade, accept_below_stop=(BBB,)
    )
    rig.settle()
    assert rig.sink.of(SettingsView)[-1].coins == (AAA, BBB)


def test_an_unreadable_price_for_the_below_stop_check_names_the_coin_field(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=holding_seed(BBB, "10", "100"))
    rig.start()
    rig.worker.refresh_markets()
    rig.settle()
    rig.quote.script(QuoteError(ERROR_NETWORK), kind="price", market=BBB)
    rig.worker.save_settings((AAA, BBB), 30, first_setup=False, trade=stage3_trade((AAA, BBB)))
    rig.settle()
    result = rig.sink.of(SaveResult)[-1]
    assert (result.ok, dict(result.field_errors)) == (
        False, {f"coin_ratio:{BBB}": BELOW_STOP_PRICE_UNREAD},
    )  # fmt: skip


def test_removing_a_held_coin_cancels_its_orders_and_is_one_a16(build: Callable[..., Rig]) -> None:
    rig = build(coins=(AAA, BBB), seed=holding_seed(BBB, "10", "100"))
    rig.start()
    rig.worker.refresh_markets()
    rig.worker.save_settings((AAA,), 30, first_setup=False, trade=stage3_trade((AAA,)))
    rig.settle()
    (a16,) = [a for a in rig.alerts() if a.code == "A-16"]
    assert (a16.category, a16.coin) == (AlertCategory.HOLDING, BBB)
    assert a16.text == "목록에서 뺐습니다. 보유는 그대로 남고, 이 코인의 손절도 없어집니다."
    assert ("coin_removed", BBB) in rig.query("SELECT kind, market FROM setting_events")


def test_a_saved_setting_that_fails_a_larger_minimum_order_is_one_a15_at_startup(
    build: Callable[..., Rig],
) -> None:
    rig = build(min_order=Decimal(180_000))  # 첫 매수 200,000원이 하한(208,421원)보다 작다
    rig.start()
    (a15,) = [a for a in rig.alerts() if a.code == "A-15"]
    assert a15.category is AlertCategory.LIMIT
    result = rig.sink.of(SaveResult)[-1]
    assert result.ok is False
    assert "first_ratio" in result.field_errors
    rig.answer(direction="오를 것", buy_price="99")
    rig.round()
    assert rig.orders() == []
    assert rig.last_report().no_order_reason == NoOrderReason.SETTINGS_CHECK.value
    assert rig.codes().count("A-15") == 1  # 다시 통과하기 전에는 다시 만들지 않는다


# --- 글(3.14) ------------------------------------------------------------------------------


def test_the_discord_trade_alert_has_the_amount_and_balance_on_the_third_line() -> None:
    line = trade_balance_line(Decimal("20000.5"), Decimal("980000.9"), "AAA", Decimal("6"))
    assert line == "주문 금액 20,000원 · 계정 잔고: 원화 980,000원, AAA 6.0"
    text = alert_message(
        mode=Mode.PAPER, category=AlertCategory.TRADE, coin=AAA, text="주문을 냈습니다(…).",
        trade_line=line,
    )  # fmt: skip
    assert text.splitlines() == ["[모의매매] 매매 알림", "AAA: 주문을 냈습니다(…).", line]
    other = alert_message(
        mode=Mode.PAPER, category=AlertCategory.ERROR, coin=AAA, text="글", trade_line=line
    )
    assert line not in other  # 매매 알림에만 붙인다


def test_order_summaries_and_money_follow_the_examples() -> None:
    assert order_summary_text("지정가", "매수", Decimal(990), Decimal("6.0")) == (
        "지정가 매수 990원 × 6.0"
    )
    assert order_summary_text("지정가", "매도", Decimal(1010), Decimal(6)) == (
        "지정가 매도 1,010원 × 6.0"
    )
    assert order_summary_text("시장가", "매도", None, Decimal("6.0")) == "시장가 매도 6.0"
    assert won_text(Decimal("1234.99")) == "1,234"


def test_the_a17_stop_only_suffix_is_added_only_when_a_sell_was_canceled() -> None:
    plain = report_failed(AAA, "시간 초과", "까닭", "05:00")
    marked = report_failed(AAA, "시간 초과", "까닭", "05:00", stop_only=True)
    assert plain.text == "AI 보고서를 받지 못해 이번 회차에 주문하지 않았습니다(시간 초과)."
    assert marked.text == f"{plain.text} {STOP_ONLY_LEFT}"
    assert isinstance(marked, AlertSpec)


# --- 검토의 고칠 것 7개(PR 검토. 경계와 갈래마다 시험 하나씩) -------------------------------------

SEED_AT = datetime(2026, 1, 5, 2, tzinfo=UTC)
"""심는 주문을 건 때(가짜 시계의 지금 03:00보다 한 시간 앞)."""


def order_seed(
    market: str,
    side: OrderSide,
    kind: OrderKind,
    price: Decimal | None,
    qty: str,
    *,
    sent: bool = True,
    lock: bool = True,
) -> Callable[[Any], None]:
    """걸린 주문 하나를 심는다(`sent`가 거짓이면 보내기 전에 꺼진 줄). 지정가는 묶음도 쓴다."""

    def seed(conn: Any) -> None:
        amount = (price or Decimal(0)) * Decimal(qty)
        plan = OrderPlan(market, side, kind, OrderReason.JUDGMENT, price, Decimal(qty), amount)
        order = trade_store.insert_order(
            conn,
            plan,
            mode=Mode.PAPER,
            uid=f"seed-{market}-{side.value}-{kind.value}-{qty}",
            round_id=None,
            created_at=SEED_AT,
            judged_until=SEED_AT,
        )
        if sent:
            trade_store.update_order(conn, order.order_id, state=OrderState.OPEN, sent=True)
        if lock and kind is OrderKind.LIMIT:
            account = trade_store.read_paper_account(conn, Mode.PAPER)
            assert account is not None
            trade_store.write_paper_account(conn, Mode.PAPER, lock_order(account, order))

    return seed


def fill_seed(market: str, pnl: str) -> Callable[[Any], None]:
    """그날(한국 09:00 경계) 프로그램이 판 체결 하나를 심는다."""

    def seed(conn: Any) -> None:
        plan = OrderPlan(
            market, OrderSide.SELL, OrderKind.MARKET, OrderReason.STOP_LOSS, None,
            Decimal(1), Decimal(1),
        )  # fmt: skip
        order = trade_store.insert_order(
            conn, plan, mode=Mode.PAPER, uid="seed-fill", round_id=None, created_at=SEED_AT,
            judged_until=SEED_AT,
        )  # fmt: skip
        account = trade_store.read_paper_account(conn, Mode.PAPER)
        assert account is not None
        fill = FillRecord(
            0, order.order_id, Mode.PAPER, market, OrderSide.SELL, Decimal(1), Decimal(1),
            Decimal(0), Decimal(pnl), datetime(2026, 1, 5, 1, tzinfo=UTC),
        )  # fmt: skip
        trade_store.record_fill(conn, fill, account)

    return seed


def seeds(*parts: Callable[[Any], None]) -> Callable[[Any], None]:
    def seed(conn: Any) -> None:
        for part in parts:
            part(conn)

    return seed


def set_holding(rig: Rig, market: str, qty: str, avg: str) -> None:
    """돌고 있는 앱 밖에서 가상 보유를 바꾼다(다른 연결. 수량 0이면 줄은 남기고 0으로 둔다)."""
    conn = open_database(rig.db)
    try:
        account = trade_store.read_paper_account(conn, Mode.PAPER)
        assert account is not None
        amount = Decimal(avg) * Decimal(qty)
        holding = PaperHolding(
            market, Decimal(qty), Decimal(0), Decimal(avg) if Decimal(qty) else None,
            amount, Decimal(qty),
        )  # fmt: skip
        others = tuple(h for h in account.holdings if h.market != market)
        trade_store.write_paper_account(
            conn, Mode.PAPER, replace(account, holdings=(*others, holding))
        )
    finally:
        conn.close()


def order_states(rig: Rig) -> list[tuple[str, str, str]]:
    return rig.query("SELECT side, kind, state FROM orders ORDER BY id")


def listed_rig(build: Callable[..., Rig], **kwargs: Any) -> Rig:
    rig = build(**kwargs)
    rig.start()
    rig.worker.refresh_markets()
    rig.settle()
    return rig


# 1. 손절선 아래 보유 확인의 경계(3.11.4)


def test_an_empty_holding_row_does_not_ask_below_the_stop_line(build: Callable[..., Rig]) -> None:
    def seed(conn: Any) -> None:
        account = trade_store.read_paper_account(conn, Mode.PAPER)
        assert account is not None
        empty = PaperHolding(BBB, Decimal(0), Decimal(0), None, Decimal(0), Decimal(0))
        trade_store.write_paper_account(conn, Mode.PAPER, replace(account, holdings=(empty,)))

    rig = build(seed=seed)  # `paper_holdings`의 줄은 보유가 0이어도 남는다(3.12)
    rig.start()
    rig.quote.set_prices({BBB: Decimal(1)})
    rig.worker.refresh_markets()
    rig.settle()
    rig.worker.save_settings((AAA, BBB), 30, first_setup=False, trade=stage3_trade((AAA, BBB)))
    rig.settle()
    assert rig.sink.of(BelowStopView) == []
    assert rig.sink.of(SettingsView)[-1].coins == (AAA, BBB)


@pytest.mark.parametrize(("price", "asks"), [("95", True), ("96", False)])
def test_the_below_stop_question_includes_a_price_exactly_at_the_stop_line(
    build: Callable[..., Rig], price: str, asks: bool
) -> None:
    rig = build(seed=holding_seed(BBB, "10", "100"))  # 손절선 = 100 × (1 − 5%) = 95
    rig.start()
    rig.quote.set_prices({BBB: Decimal(price)})
    rig.worker.refresh_markets()
    rig.settle()
    rig.worker.save_settings((AAA, BBB), 30, first_setup=False, trade=stage3_trade((AAA, BBB)))
    rig.settle()
    assert bool(rig.sink.of(BelowStopView)) is asks
    assert (rig.sink.of(SettingsView)[-1].coins == (AAA, BBB)) is (not asks)


# 2. 설정 바뀜의 세 갈래(3.16.2)


def test_each_setting_change_writes_its_own_event_lines(build: Callable[..., Rig]) -> None:
    rig = listed_rig(build)
    trend = TradeMethod.TREND
    rig.worker.save_settings(
        (AAA, BBB), 30, first_setup=False, trade=stage3_trade((AAA, BBB), method=trend)
    )
    rig.settle()
    events = "SELECT kind, market, value FROM setting_events ORDER BY id"
    assert rig.query(events) == [("coin_added", BBB, None)]  # 방식은 그대로라 그 줄이 없다
    rig.worker.save_settings(
        (AAA, BBB),
        30,
        first_setup=False,
        trade=stage3_trade((AAA, BBB), method=TradeMethod.MEAN_REVERSION),
    )
    rig.settle()
    assert rig.query(events)[1:] == [
        ("method_changed", AAA, "mean_reversion"),
        ("method_changed", BBB, "mean_reversion"),
    ]
    rig.worker.save_settings((AAA, BBB), 30, first_setup=False, candle_count=200)
    rig.settle()
    assert rig.query(events)[3:] == [
        ("candle_count_changed", AAA, "200"),
        ("candle_count_changed", BBB, "200"),
    ]


# 3. A-16은 보유 중인 코인을 뺄 때만, A-15는 다시 통과하면 풀림(3.11.4, 3.11.2)


def test_removing_a_coin_without_a_holding_has_no_a16(build: Callable[..., Rig]) -> None:
    rig = listed_rig(build, coins=(AAA, BBB))
    rig.worker.save_settings((AAA,), 30, first_setup=False, trade=stage3_trade((AAA,)))
    rig.settle()
    assert rig.sink.of(SettingsView)[-1].coins == (AAA,)
    assert "A-16" not in rig.codes()
    assert ("coin_removed", BBB) in rig.query("SELECT kind, market FROM setting_events")


def test_a15_is_made_again_after_the_saved_setting_passed_in_between(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    assert "A-15" not in rig.codes()

    def save_with(min_order: str) -> None:
        rig.worker._min_order = Decimal(min_order)  # 쉬는 동안 바꾼다(최소 주문 금액이 바뀜)
        rig.worker.save_settings((AAA,), 30, first_setup=False)  # `trade=None`: 저장된 칸을 검사
        rig.settle()

    save_with("180000")
    assert rig.codes().count("A-15") == 1
    save_with("180000")
    assert rig.codes().count("A-15") == 1  # 걸려 있는 동안은 다시 만들지 않는다
    save_with("5000")
    assert rig.codes().count("A-15") == 1  # 다시 통과했다
    save_with("180000")
    assert rig.codes().count("A-15") == 2  # 다시 걸리면 새로 만든다


# 4. 켤 때 `sent` 0인 주문을 닫으며 묶음도 푼다(3.8.3)


def test_startup_frees_the_locked_krw_of_an_unsent_buy(build: Callable[..., Rig]) -> None:
    seed = order_seed(AAA, OrderSide.BUY, OrderKind.LIMIT, Decimal(99), "10", sent=False)
    rig = build(seed=seed)
    rig.start()
    assert order_states(rig) == [("buy", "limit", "not_sent")]
    account = rig.account()
    assert (account.krw, account.locked_krw) == (Decimal(1_000_000), Decimal(0))


# 5. 손절의 순서와 A-3(3.9)


def test_a_stop_loss_cancels_the_coins_open_orders_before_it_sells(
    build: Callable[..., Rig],
) -> None:
    rig = build(
        seed=seeds(
            holding_seed(AAA, "100", "100"),
            order_seed(AAA, OrderSide.BUY, OrderKind.LIMIT, Decimal(90), "10"),
        )
    )
    rig.start()
    assert order_states(rig) == [("buy", "limit", "open")]
    rig.quote.set_prices({AAA: Decimal(90)})  # 손절선 95 아래
    rig.quote.set_book(AAA, Decimal(91), Decimal(89))
    rig.advance(60)
    assert order_states(rig) == [("buy", "limit", "canceled"), ("sell", "market", "filled")]
    assert rig.account().locked_krw == 0


def test_a_holding_too_small_to_sell_is_one_a3_and_it_comes_back_after_the_holding_ends(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=holding_seed(AAA, "1", "100"))
    rig.quote.set_prices({AAA: Decimal(90)})  # 손절선(95) 이하이지만 평가 금액 90원 < 5,000원
    rig.start()
    assert rig.codes().count("A-3") == 1
    assert rig.orders() == []
    rig.advance(60)
    assert rig.codes().count("A-3") == 1  # 그 상태에서는 되풀이하지 않는다
    set_holding(rig, AAA, "0", "0")
    rig.advance(60)  # 보유가 없는 점검이 풀어 둔다
    set_holding(rig, AAA, "1", "100")
    rig.advance(60)
    assert rig.codes().count("A-3") == 2


# 6. 하루 손실 한도의 경계와 취소 범위(3.8.2, 3.9)


@pytest.mark.parametrize(("pnl", "reached"), [("-3000", True), ("-2999", False)])
def test_the_daily_loss_limit_includes_exactly_the_limit_and_cancels_only_buys(
    build: Callable[..., Rig], pnl: str, reached: bool
) -> None:
    rig = build(
        seed=seeds(
            holding_seed(AAA, "100", "100"),
            fill_seed(AAA, pnl),
            order_seed(AAA, OrderSide.BUY, OrderKind.LIMIT, Decimal(90), "10"),
            order_seed(AAA, OrderSide.SELL, OrderKind.LIMIT, Decimal(150), "10"),
        )
    )
    rig.start()
    states = [(side, state) for side, _, state in order_states(rig) if state != "filled"]
    assert states == [("buy", "canceled" if reached else "open"), ("sell", "open")]
    assert rig.codes().count("A-12") == (1 if reached else 0)


# 7. 1분 점검의 판정 길(3.7.2, 3.8.4, 3.9)

BUY_99 = order_seed(AAA, OrderSide.BUY, OrderKind.LIMIT, Decimal(99), "10")
JUDGED_UNTIL = "SELECT judged_until_utc, state FROM orders"


def test_a_minute_candle_that_could_not_be_read_is_judged_again_over_the_same_span(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=BUY_99)
    rig.quote.script(*[QuoteError(ERROR_NETWORK)] * 20, kind=MINUTES, market=AAA)
    rig.start()
    assert rig.query(JUDGED_UNTIL) == [("2026-01-05T02:00:00Z", "open")]  # 본 구간이 앞서지 않았다
    assert "A-24" not in rig.codes()  # 잠깐의 문제는 알림이 없다
    rig.quote.clear_script()
    low = MinuteCandle(
        AAA, datetime(2026, 1, 5, 2, 10, tzinfo=UTC), Decimal(100), Decimal(98), Decimal(98)
    )
    rig.quote.put_minutes(low)
    rig.advance(60)
    assert rig.query("SELECT state FROM orders") == [("filled",)]
    assert rig.query("SELECT filled_utc FROM fills") == [("2026-01-05T02:11:00Z",)]


def test_the_judged_span_is_stored_after_a_judgment_with_no_fill(build: Callable[..., Rig]) -> None:
    rig = build(seed=BUY_99)
    rig.start()
    assert rig.query(JUDGED_UNTIL) == [("2026-01-05T03:00:00Z", "open")]
    rig.advance(60)
    assert rig.query(JUDGED_UNTIL) == [("2026-01-05T03:01:00Z", "open")]
    assert len(rig.quote.book_requests(MINUTES, AAA)) == 2  # 봉을 겹쳐 받지 않고 이어 받는다


def test_a_market_order_is_never_judged_by_minute_candles(build: Callable[..., Rig]) -> None:
    seed = seeds(
        holding_seed(AAA, "100", "100"),
        order_seed(AAA, OrderSide.SELL, OrderKind.MARKET, None, "5"),
    )
    rig = build(seed=seed)
    rig.start()
    rig.advance(60)
    assert rig.quote.book_requests(MINUTES, AAA) == []
    assert "A-30" not in rig.codes()
    assert order_states(rig) == [("sell", "market", "open")]


def test_a_blocked_price_read_stops_price_requests_until_the_block_ends(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.quote.script(QuoteError(ERROR_BLOCKED, retry_after=600), kind="price", market=AAA)
    rig.start()
    count = len(rig.quote.price_requests(AAA))
    rig.advance(60)
    rig.advance(60)
    assert len(rig.quote.price_requests(AAA)) == count  # 차단 중에는 요청하지 않는다
    rig.advance(660)
    assert len(rig.quote.price_requests(AAA)) > count  # 풀린 뒤에는 다시 읽는다


def test_a_blocked_minute_candle_read_stops_minute_and_price_requests(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=BUY_99)
    rig.quote.script(QuoteError(ERROR_BLOCKED, retry_after=600), kind=MINUTES, market=AAA)
    rig.start()
    minutes = len(rig.quote.book_requests(MINUTES, AAA))
    prices = len(rig.quote.price_requests(AAA))
    rig.advance(60)
    assert len(rig.quote.book_requests(MINUTES, AAA)) == minutes
    assert len(rig.quote.price_requests(AAA)) == prices
    assert rig.query(JUDGED_UNTIL) == [("2026-01-05T02:00:00Z", "open")]


def test_an_unlisted_exchange_error_on_minute_candles_is_one_a24_per_sixty_minutes(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=BUY_99)
    unlisted = QuoteError(error_unknown_exchange(418))
    rig.quote.script(*[unlisted] * 80, kind=MINUTES, market=AAA)
    rig.start()
    assert rig.codes().count("A-24") == 1
    rig.advance(60)
    assert (
        rig.codes().count("A-24") == 1
    )  # 같은 코인, 같은 오류 이름은 60분 안에 다시 만들지 않는다
    rig.advance(3600)
    assert rig.codes().count("A-24") == 2
    (first, *_) = [a for a in rig.alerts() if a.code == "A-24"]
    assert (first.category, first.detail.stage) == (AlertCategory.OTHER, "거래소와 맞춰 보기")


def test_no_round_runs_until_every_open_order_was_judged(build: Callable[..., Rig]) -> None:
    rig = build(seed=BUY_99)
    rig.quote.script(*[QuoteError(ERROR_NETWORK)] * 80, kind=MINUTES, market=AAA)
    rig.start()
    assert rig.worker._trade_ready is False
    rig.advance(3610)  # (라)의 때가 와도 판정이 끝나지 않아 회차를 돌리지 않는다
    assert rig.query("SELECT count(*) FROM rounds") == [(0,)]
    assert rig.worker._trade_ready is False
    rig.quote.clear_script()
    rig.advance(60)
    assert rig.worker._trade_ready is True
    rig.round()
    assert rig.query("SELECT count(*) FROM rounds") == [(1,)]
