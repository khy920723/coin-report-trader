"""실거래 체결의 저장 함수(M5 명세 3.6.4, 3.9. 4절 15번)와 `app`의 `StoredLiveFills`.

`record_live_fill`은 체결 한 줄만 쓰고(가상 계정과 주문의 상태는 쓰지 않는다), `fill_trade_keys`는
그 주문에 쓴 `trade_key`를 읽는다. 값은 모두 만든 값이다.
"""

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from coin_report_trader.app.live_engine import StoredLiveFills
from coin_report_trader.core import (
    FillRecord,
    Mode,
    OrderKind,
    OrderPlan,
    OrderReason,
    OrderRecord,
    OrderSide,
    OrderState,
    StorageError,
)
from coin_report_trader.storage import trading as trade_store
from coin_report_trader.storage.database import migrate, open_database

AAA = "KRW-AAA"
T0 = datetime(2026, 1, 5, 3, 0, tzinfo=UTC)
T1 = datetime(2026, 1, 5, 3, 1, tzinfo=UTC)
D = Decimal


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_database(tmp_path / "app.db")
    migrate(connection)
    trade_store.create_paper_account(connection, Mode.PAPER, D(1_000_000), created_at=T0)
    yield connection
    connection.close()


def live_order(
    conn: sqlite3.Connection, uid: str = "live-1", mode: Mode = Mode.LIVE
) -> OrderRecord:
    plan = OrderPlan(
        AAA, OrderSide.SELL, OrderKind.LIMIT, OrderReason.JUDGMENT, D(1010), D(5), D(5050)
    )
    order = trade_store.insert_order(
        conn, plan, mode=mode, uid=uid, round_id=None, created_at=T0, judged_until=T0
    )
    trade_store.update_order(conn, order.order_id, state=OrderState.OPEN, sent=True)
    return order


def fill_of(order: OrderRecord, qty: str = "2", mode: Mode = Mode.LIVE) -> FillRecord:
    return FillRecord(
        fill_id=0,
        order_id=order.order_id,
        mode=mode,
        market=order.market,
        side=order.side,
        price=D(1010),
        qty=D(qty),
        fee=D("1.01"),
        realized_pnl=D("17.99"),
        filled_utc=T1,
    )


def order_row(conn: sqlite3.Connection, order_id: int) -> tuple[object, ...]:
    return conn.execute(
        "SELECT state, closed_utc, sent, executed_qty FROM orders WHERE id = ?", (order_id,)
    ).fetchone()


def test_one_live_fill_is_one_row_with_its_trade_key(conn: sqlite3.Connection) -> None:
    order = live_order(conn)
    written = trade_store.record_live_fill(conn, fill_of(order), "t-1")
    assert written.fill_id > 0
    assert written == FillRecord(
        written.fill_id, order.order_id, Mode.LIVE, AAA, OrderSide.SELL, D(1010), D(2), D("1.01"),
        D("17.99"), T1,
    )  # fmt: skip
    rows = conn.execute(
        "SELECT order_id, mode, price, qty, fee, realized_pnl, filled_utc, trade_key FROM fills"
    ).fetchall()
    assert rows == [
        (order.order_id, "live", "1010", "2", "1.01", "17.99", "2026-01-05T03:01:00Z", "t-1")
    ]
    assert trade_store.fills_of(conn, order.order_id) == (written,)


def test_the_paper_account_and_the_order_state_do_not_change(conn: sqlite3.Connection) -> None:
    order = live_order(conn)
    account = trade_store.read_paper_account(conn, Mode.PAPER)
    before = order_row(conn, order.order_id)
    trade_store.record_live_fill(conn, fill_of(order), "t-1")
    trade_store.record_live_fill(conn, fill_of(order, "3"), "t-2")
    assert order_row(conn, order.order_id) == before == ("open", None, 1, None)
    assert trade_store.read_paper_account(conn, Mode.PAPER) == account
    assert conn.execute("SELECT count(*) FROM paper_holdings").fetchone() == (0,)


def test_the_same_fill_twice_is_a_storage_error_and_writes_nothing(
    conn: sqlite3.Connection,
) -> None:
    order = live_order(conn)
    trade_store.record_live_fill(conn, fill_of(order), "t-1")
    with pytest.raises(StorageError):
        trade_store.record_live_fill(conn, fill_of(order), "t-1")
    assert conn.execute("SELECT count(*) FROM fills").fetchone() == (1,)
    assert not conn.in_transaction
    other = live_order(conn, "live-2")
    trade_store.record_live_fill(conn, fill_of(other), "t-1")  # 다른 주문의 같은 글은 된다
    assert conn.execute("SELECT count(*) FROM fills").fetchone() == (2,)


def test_a_write_that_fails_is_a_storage_error(conn: sqlite3.Connection) -> None:
    order = live_order(conn)
    conn.execute(
        "CREATE TRIGGER no_fills BEFORE INSERT ON fills BEGIN SELECT RAISE(ABORT, 'x'); END"
    )
    with pytest.raises(StorageError, match="주문 저장"):
        trade_store.record_live_fill(conn, fill_of(order), "t-1")
    assert not conn.in_transaction
    conn.execute("DROP TRIGGER no_fills")
    assert conn.execute("SELECT count(*) FROM fills").fetchone() == (0,)


def test_a_fill_of_a_missing_or_paper_order_is_a_storage_error(conn: sqlite3.Connection) -> None:
    order = live_order(conn)
    missing = fill_of(order)
    with pytest.raises(StorageError, match="주문이 없습니다"):
        trade_store.record_live_fill(conn, replace(missing, order_id=999), "t")
    paper = live_order(conn, "paper-1", Mode.PAPER)
    with pytest.raises(StorageError, match="주문이 없습니다"):
        trade_store.record_live_fill(conn, fill_of(paper), "t")
    assert conn.execute("SELECT count(*) FROM fills").fetchone() == (0,)


def test_only_live_fills_with_a_trade_key_are_taken(conn: sqlite3.Connection) -> None:
    order = live_order(conn)
    with pytest.raises(ValueError, match="실거래"):
        trade_store.record_live_fill(conn, fill_of(order, mode=Mode.PAPER), "t-1")
    with pytest.raises(ValueError, match="식별값"):
        trade_store.record_live_fill(conn, fill_of(order), "")
    assert conn.execute("SELECT count(*) FROM fills").fetchone() == (0,)


def test_the_trade_keys_of_an_order(conn: sqlite3.Connection) -> None:
    order = live_order(conn)
    other = live_order(conn, "live-2")
    assert trade_store.fill_trade_keys(conn, order.order_id) == frozenset()
    trade_store.record_live_fill(conn, fill_of(order), "t-1")
    trade_store.record_live_fill(conn, fill_of(order, "1"), "t-2")
    trade_store.record_live_fill(conn, fill_of(other), "t-9")
    assert trade_store.fill_trade_keys(conn, order.order_id) == frozenset({"t-1", "t-2"})
    assert trade_store.fill_trade_keys(conn, other.order_id) == frozenset({"t-9"})
    assert trade_store.fill_trade_keys(conn, 999) == frozenset()


def test_the_program_s_live_fill_store_calls_the_storage_functions(
    conn: sqlite3.Connection,
) -> None:
    store = StoredLiveFills()
    assert repr(store) == "StoredLiveFills()"
    order = live_order(conn)
    written = store.write(conn, fill_of(order), "t-1")
    assert trade_store.fills_of(conn, order.order_id) == (written,)
    assert store.trade_keys(conn, order.order_id) == frozenset({"t-1"})
    with pytest.raises(StorageError):
        store.write(conn, fill_of(order), "t-1")
