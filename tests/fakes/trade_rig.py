"""3단계 잇기 2의 시험이 함께 쓰는 자료 스레드의 틀(M3 명세 4절 25번).

가짜 실행기, 가짜 시세(호가, 호가 단위, 1분봉), 가짜 시계, 3단계로 처음 설정을 마친 DB로 자료
스레드를 띄운다. 값은 모두 만든 값이다(코인 `KRW-AAA`, `KRW-BBB`, 현재가 100, 호가 단위 1).
"""

import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from coin_report_trader.app.data_thread import DataWorker
from coin_report_trader.core import (
    AiChoice,
    AiSlot,
    CandleUnit,
    CoinTradeView,
    Effort,
    Market,
    Mode,
    OrderKind,
    OrderPlan,
    OrderReason,
    OrderSide,
    OrderState,
    PaperHolding,
    PauseStopLoss,
    SettingsView,
    TradeStatusView,
    UiUpdate,
)
from coin_report_trader.storage import trading as trade_store
from coin_report_trader.storage.alerts import list_alerts
from coin_report_trader.storage.database import migrate, open_database
from coin_report_trader.storage.settings import write_settings
from coin_report_trader.trading.paper import lock_order
from fakes.ai import FakeAiRunner, answered, good_answers
from fakes.clock import FakeClock
from fakes.quote import FakeQuoteSource, make_candles
from fakes.stage3 import stage3_trade, write_stage3

AAA, BBB = "KRW-AAA", "KRW-BBB"
HOUR = CandleUnit.MINUTE_60
SAFETY = 10.0
THIS_HOUR = datetime(2026, 1, 5, 3, tzinfo=UTC)
FOUR = tuple(AiChoice(slot, "haiku", Effort.LOW) for slot in AiSlot)
PRICE = Decimal(100)
PARTS = [("검색 판별", 1), ("봉 판별", 1), ("최종 예측", 1)]


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

    def execute(self, sql: str) -> None:
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(sql)
            conn.commit()
        finally:
            conn.close()

    def codes(self) -> list[str]:
        conn = open_database(self.db)
        try:
            return [alert.code for alert in reversed(list_alerts(conn))]
        finally:
            conn.close()

    def alerts(self) -> list[Any]:
        conn = open_database(self.db)
        try:
            return list(reversed(list_alerts(conn)))
        finally:
            conn.close()

    def status(self) -> TradeStatusView:
        return self.sink.of(TradeStatusView)[-1]

    def coin_view(self, market: str) -> CoinTradeView:
        return [v for v in self.sink.of(CoinTradeView) if v.market == market][-1]

    def orders(self) -> list[tuple[Any, ...]]:
        return self.query("SELECT market, side, kind, reason, state FROM orders ORDER BY id")

    def pauses(self) -> list[tuple[Any, ...]]:
        return self.query(
            "SELECT kind, cause, sell_all, released_utc IS NULL FROM pauses ORDER BY id"
        )

    def answer(self, **kwargs: Any) -> None:
        self.runner.plan(AAA, lambda req: answered(good_answers(AAA, **kwargs)))


def holding_seed(market: str, qty: str, avg: str) -> Callable[[Any], None]:
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


SEED_AT = datetime(2026, 1, 5, 2, tzinfo=UTC)


def order_seed(
    market: str, side: OrderSide, kind: OrderKind, price: Decimal | None, qty: str
) -> Callable[[Any], None]:
    """걸린 주문 하나를 심는다(지정가는 묶음도 쓴다)."""

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
        trade_store.update_order(conn, order.order_id, state=OrderState.OPEN, sent=True)
        if kind is OrderKind.LIMIT:
            account = trade_store.read_paper_account(conn, Mode.PAPER)
            assert account is not None
            trade_store.write_paper_account(conn, Mode.PAPER, lock_order(account, order))

    return seed


def seeds(*items: Callable[[Any], None]) -> Callable[[Any], None]:
    def seed(conn: Any) -> None:
        for item in items:
            item(conn)

    return seed


def make_rig(
    tmp_path: Path,
    *,
    coins: tuple[str, ...] = (AAA,),
    pause_stop_loss: PauseStopLoss = PauseStopLoss.KEEP,
    seed: Callable[[Any], None] | None = None,
    backup_days: int = 7,
    clock: FakeClock | None = None,
    done: bool = True,
) -> Rig:
    db = tmp_path / "app.db"
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    conn = open_database(db)
    try:
        migrate(conn)
        view = SettingsView(coins, 30, done, candle_unit=HOUR, candle_count=100, ai_choices=FOUR)
        trade = replace(
            stage3_trade(coins, method=_trend()),
            pause_stop_loss=pause_stop_loss,
            backup_days=backup_days,
        )
        if done:
            write_stage3(conn, replace(view, trade=trade))
        else:
            write_settings(conn, view)
        if seed is not None:
            seed(conn)
    finally:
        conn.close()
    clock = clock or FakeClock()
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
    )
    return Rig(worker, sink, clock, quote, runner, db)


def _trend() -> Any:
    from coin_report_trader.core import TradeMethod

    return TradeMethod.TREND


def rig_builder(tmp_path: Path) -> Iterator[Callable[..., Rig]]:
    """`build` 픽스처의 몸(시험 파일마다 `@pytest.fixture`로 감싼다)."""
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


def raise_in_minute_check(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    """1분 점검에서 예상하지 못한 예외를 일으켜 오류 멈춤(`UNEXPECTED`)으로 들게 한다."""

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("뜻밖")

    monkeypatch.setattr(rig.worker, "_check_daily_loss", broken)
    rig.advance(60)


def rounds(rig: Rig) -> list[tuple[Any, ...]]:
    return rig.query("SELECT number, state FROM rounds ORDER BY id")
