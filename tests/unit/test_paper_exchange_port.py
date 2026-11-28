"""5단계 잇기: 모의매매를 거래소 경계(`PaperExchange`)로 옮김(M5 명세 4절 15번, 3.4).

모의매매의 주문, 체결 판정, 취소가 `select_exchange`가 고른 `PaperExchange`를 지나고, 3단계의 동작과
기록(최우선 호가를 읽은 뒤에 주문 줄을 씀, 판정한 때를 `orders.judged_until_utc`에 씀, 알림, 체결)이
그대로인지 본다. 가짜 실행기, 가짜 시세, 가짜 시계로만 돈다. 값은 모두 만든 값이다.
"""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fakes.live_rig import LiveRig, live_rig_builder
from fakes.quote import BOOK, MINUTES
from fakes.trade_rig import (
    AAA,
    PARTS,
    SAFETY,
    SEED_AT,
    Rig,
    holding_seed,
    order_seed,
    rig_builder,
    seeds,
)

from coin_report_trader.app.data_thread import DataWorker
from coin_report_trader.core import Mode, OrderKind, OrderSide, QuoteError
from coin_report_trader.core.texts import ERROR_BLOCKED, ERROR_NETWORK
from coin_report_trader.storage.database import open_database
from coin_report_trader.storage.paper_store import SqlitePaperStore
from coin_report_trader.trading.paper.exchange import PaperExchange

BUY_99 = order_seed(AAA, OrderSide.BUY, OrderKind.LIMIT, Decimal(99), "10")


@pytest.fixture
def build(tmp_path: Path) -> Iterator[Callable[..., Rig]]:
    yield from rig_builder(tmp_path)


@pytest.fixture
def build_live(tmp_path: Path) -> Iterator[Callable[..., LiveRig]]:
    root = tmp_path / "live"
    root.mkdir()
    yield from live_rig_builder(root)


class Spy:
    """`PaperExchange`의 메서드 하나를 감싸 부른 값을 남긴다(동작은 그대로)."""

    def __init__(self, exchange: Any, name: str) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        real = getattr(exchange, name)

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((args, kwargs))
            return real(*args, **kwargs)

        setattr(exchange, name, wrapped)


def paper_exchange(rig: Rig) -> Any:
    exchange = rig.worker._exchange
    assert isinstance(exchange, PaperExchange)
    return exchange


def restart(rig: Rig) -> Rig:
    """앱을 끄고 켠다: 같은 DB, 가짜 시세, 시계로 새 자료 스레드를 띄운다."""
    rig.worker.stop()
    rig.worker.join(SAFETY)
    worker = DataWorker(
        database_file=rig.db,
        logs_dir=rig.db.parent / "logs",
        clock=rig.clock,
        quote=rig.quote,
        sink=rig.sink,
        ai=rig.runner,
        sessions_root=rig.db.parent / "ai-sessions",
    )
    again = Rig(worker, rig.sink, rig.clock, rig.quote, rig.runner, rig.db)
    again.start()
    return again


# --- 고르기 -------------------------------------------------------------------------------------


def test_paper_mode_selects_the_paper_exchange_at_startup(build: Callable[..., Rig]) -> None:
    rig = build()
    rig.start()
    exchange = paper_exchange(rig)
    assert exchange.mode is Mode.PAPER
    assert rig.worker._live_ready() is False  # 실거래의 부르기는 모의매매의 경계를 쓰지 않는다


def test_turning_live_off_selects_the_paper_exchange_again(
    build_live: Callable[..., LiveRig],
) -> None:
    rig = build_live(mode=Mode.LIVE)
    rig.start()
    assert rig.worker._exchange.mode is Mode.LIVE
    rig.worker.turn_live_off()
    rig.settle()
    assert rig.mode() == "paper"
    assert isinstance(rig.worker._exchange, PaperExchange)
    assert rig.worker._live_ready() is False


# --- 주문(3.8.3의 차례) -------------------------------------------------------------------------


def test_a_round_order_reads_the_book_once_then_writes_the_row_then_places(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    place = Spy(paper_exchange(rig), "place")
    rig.answer(direction="오를 것", buy_price="99")
    rig.round()
    ((args, kwargs),) = place.calls
    (row,) = rig.query("SELECT uid, state, sent FROM orders")
    assert args[1] == row[0]  # 쓴 식별값으로 냈다
    assert row[1:] == ("open", 1)
    assert kwargs["best"] is not None  # 주문 줄을 쓰기 전에 읽은 최우선 호가를 넘겼다
    assert len(rig.quote.book_requests(BOOK, AAA)) == 1  # 경계는 다시 읽지 않았다


def test_a_book_that_cannot_be_read_writes_no_row_and_places_nothing(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=holding_seed(AAA, "100", "100"))
    rig.start()
    place = Spy(paper_exchange(rig), "place")
    rig.quote.set_prices({AAA: Decimal(90)})  # 손절
    rig.quote.script(QuoteError(ERROR_NETWORK), kind=BOOK, market=AAA)
    rig.advance(60)
    assert rig.orders() == []
    assert place.calls == []
    assert "A-8" in rig.codes()


def test_a_fill_at_place_that_cannot_be_written_leaves_the_order_open_and_sent(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=holding_seed(AAA, "100", "100"))
    rig.start()
    rig.execute(
        "CREATE TRIGGER no_fills BEFORE INSERT ON fills BEGIN SELECT RAISE(ABORT, 'x'); END"
    )
    rig.quote.set_prices({AAA: Decimal(90)})  # 손절 시장가 매도는 넣을 때 체결된다
    rig.advance(60)
    assert rig.query("SELECT kind, state, sent, closed_utc FROM orders") == [
        ("market", "open", 1, None)
    ]
    assert rig.query("SELECT count(*) FROM fills") == [(0,)]
    codes = [c for c in rig.codes() if c in ("A-27", "A-28", "A-29")]
    assert codes == ["A-27", "A-29"]  # 낸 알림 뒤에 쓰기의 실패(3단계의 차례)
    (a29,) = [a for a in rig.alerts() if a.code == "A-29"]
    assert a29.detail is not None
    assert a29.detail.stage == "주문 저장"
    assert rig.status().error_paused is True


def test_an_immediate_fill_is_written_by_the_exchange_with_the_fill_time(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    rig.quote.set_book(AAA, Decimal(101), Decimal(100))
    rig.answer(direction="오를 것", buy_price="102")
    rig.round()
    ((state, sent, closed),) = rig.query("SELECT state, sent, closed_utc FROM orders")
    ((filled,),) = rig.query("SELECT filled_utc FROM fills")
    assert (state, sent, closed) == ("filled", 1, filled)
    assert rig.query("SELECT trade_key FROM fills") == [("paper",)]


# --- 체결 판정과 판정한 때(3.7.2, 3.12) ----------------------------------------------------------


def test_the_minute_check_judges_through_the_exchange_and_stores_the_judged_time(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=BUY_99)
    rig.start()
    query = Spy(paper_exchange(rig), "query")
    rig.advance(60)
    ((args, _),) = query.calls
    assert args[0] == f"seed-{AAA}-buy-limit-10"
    assert rig.query("SELECT judged_until_utc FROM orders") == [("2026-01-05T03:01:00Z",)]


def test_after_a_restart_the_judgment_goes_on_from_the_stored_time(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=BUY_99)
    rig.start()  # 켤 때의 소급 판정: 02:00~03:00
    assert rig.query("SELECT judged_until_utc FROM orders") == [("2026-01-05T03:00:00Z",)]
    before = len(rig.quote.book_requests(MINUTES, AAA))
    rig.clock.advance(60)
    rig = restart(rig)
    after = rig.quote.book_requests(MINUTES, AAA)[before:]
    # 다시 켠 뒤에는 건 때(02:00)가 아니라 저장된 자리(03:00)부터 03:01까지만 받는다
    assert [(r.to, r.count) for r in after] == [(datetime(2026, 1, 5, 3, 1, tzinfo=UTC), 1)]
    assert rig.query("SELECT judged_until_utc FROM orders") == [("2026-01-05T03:01:00Z",)]


def test_a_minute_read_while_blocked_is_not_requested_and_not_judged(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=BUY_99)
    rig.quote.script(QuoteError(ERROR_BLOCKED, retry_after=600), kind=MINUTES, market=AAA)
    rig.start()
    count = len(rig.quote.book_requests(MINUTES, AAA))
    query = Spy(paper_exchange(rig), "query")
    rig.worker.pause()
    rig.settle()
    # 차단 중의 취소 전 판정은 요청하지 않고, 취소가 확인되지 않은 주문으로 둔다(3.7.2)
    assert len(rig.quote.book_requests(MINUTES, AAA)) == count
    assert len(query.calls) == 1
    assert rig.query("SELECT state, judged_until_utc FROM orders") == [
        ("open", SEED_AT.strftime("%Y-%m-%dT%H:%M:%SZ"))
    ]


# --- 취소(3.7.2) --------------------------------------------------------------------------------


def test_a_cleanup_cancel_goes_through_the_exchange_and_is_marked_by_the_round(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    cancel = Spy(paper_exchange(rig), "cancel")
    rig.answer(direction="오를 것", buy_price="99")
    rig.round()
    rig.answer(direction="오를 것", buy_price="99", result_text="지난 결과", compared=PARTS)
    rig.round()
    assert len(cancel.calls) == 1
    first, second = rig.query("SELECT state, canceled_by_round, closed_utc FROM orders")
    assert first[:2] == ("canceled", 1)
    assert first[2] is not None
    assert second[:2] == ("open", None)


def test_an_open_market_order_is_closed_without_minutes_or_the_exchange(
    build: Callable[..., Rig],
) -> None:
    seed = seeds(
        holding_seed(AAA, "100", "100"),
        order_seed(AAA, OrderSide.SELL, OrderKind.MARKET, None, "5"),
    )
    rig = build(seed=seed)
    rig.start()
    cancel = Spy(paper_exchange(rig), "cancel")
    query = Spy(paper_exchange(rig), "query")
    rig.worker.pause()
    rig.settle()
    assert rig.query("SELECT kind, state FROM orders") == [("market", "canceled")]
    assert rig.quote.book_requests(MINUTES, AAA) == []
    assert (cancel.calls, query.calls) == ([], [])
    assert "A-30" not in rig.codes()


# --- 저장 어댑터의 판정한 때(`PaperStore`) -------------------------------------------------------


def test_the_sqlite_paper_store_reads_and_writes_the_judged_time(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=BUY_99)
    conn = open_database(rig.db)
    try:
        store = SqlitePaperStore(conn)
        (order_id,) = rig.query("SELECT id FROM orders")[0]
        assert store.read_judged_until(order_id) == SEED_AT
        later = datetime(2026, 1, 5, 2, 30, tzinfo=UTC)
        store.set_judged_until(order_id, later)
        assert store.read_judged_until(order_id) == later
        assert store.read_judged_until(order_id + 100) is None
    finally:
        conn.close()
