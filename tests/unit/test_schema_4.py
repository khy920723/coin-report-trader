"""저장 스키마 4와 키 보관(M5 명세 3.8, 3.9, 4절 11번). 값은 모두 만든 값이다."""

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from coin_report_trader.core import (
    FillRecord,
    LiveSettings,
    Mode,
    OrderKind,
    OrderPlan,
    OrderReason,
    OrderSide,
    OrderState,
    PaperAccount,
    PaperHolding,
    SettingsView,
    StorageError,
)
from coin_report_trader.core.ports import PaperStore
from coin_report_trader.storage import database, settings
from coin_report_trader.storage import trading as trade_store
from coin_report_trader.storage.database import (
    SCHEMA_VERSION,
    TABLES,
    migrate,
    open_database,
    schema_version,
)
from coin_report_trader.storage.paper_store import SqlitePaperStore
from coin_report_trader.vault import UPBIT_ACCESS_KEY_NAME, UPBIT_SECRET_KEY_NAME

AAA, BBB = "KRW-AAA", "KRW-BBB"
NOW = datetime(2026, 1, 5, 3, 0, 30, tzinfo=UTC)
FOURTEEN = (
    "settings", "candles", "alerts", "rounds", "reports", "report_rows", "paper_accounts",
    "paper_holdings", "orders", "fills", "pauses", "setting_events", "live_snapshots",
    "external_changes",
)  # fmt: skip


def columns(conn: sqlite3.Connection, table: str) -> list[tuple[object, ...]]:
    return [tuple(row) for row in conn.execute(f"PRAGMA table_info({table})")]


def at_three(path: Path) -> sqlite3.Connection:
    """스키마 3의 DB를 흉내 낸다(옛 판의 DB)."""
    conn = open_database(path)
    for statements in (database._SCHEMA_1, database._SCHEMA_2, database._SCHEMA_3):
        for statement in statements:
            conn.execute(statement)
    conn.execute("PRAGMA user_version=3")
    conn.commit()
    return conn


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_database(tmp_path / "app.db")
    migrate(connection)
    yield connection
    connection.close()


def plan(market: str = AAA, side: OrderSide = OrderSide.BUY) -> OrderPlan:
    return OrderPlan(
        market, side, OrderKind.LIMIT, OrderReason.JUDGMENT, Decimal(100), Decimal(2), Decimal(200)
    )


def seed_account(conn: sqlite3.Connection) -> None:
    trade_store.create_paper_account(conn, Mode.PAPER, Decimal(1_000_000), created_at=NOW)


def new_order(conn: sqlite3.Connection, uid: str = "u1") -> int:
    order = trade_store.insert_order(
        conn, plan(), mode=Mode.PAPER, uid=uid, round_id=None, created_at=NOW, judged_until=NOW
    )
    return order.order_id


def fill_of(order_id: int, price: str = "100") -> FillRecord:
    return FillRecord(
        0, order_id, Mode.PAPER, AAA, OrderSide.BUY, Decimal(price), Decimal(2), Decimal("0.1"),
        None, NOW,
    )  # fmt: skip


# --- 스키마 4(3.9) --------------------


def test_the_schema_number_is_four_and_the_tables_are_the_fourteen_of_the_spec() -> None:
    assert SCHEMA_VERSION == 4
    assert TABLES == FOURTEEN


def test_a_schema_three_database_is_raised_to_four_and_keeps_every_row(tmp_path: Path) -> None:
    old = at_three(tmp_path / "old.db")
    try:
        old.execute("INSERT INTO settings VALUES ('coins', '[\"KRW-AAA\"]')")
        old.execute("INSERT INTO paper_accounts VALUES ('paper', '1000000', '999000', '0', 'x')")
        order = (
            "INSERT INTO orders (uid, mode, market, side, kind, reason, qty, state, sent,"
            " created_utc, judged_until_utc) VALUES (?, 'paper', 'KRW-AAA', 'buy', 'limit',"
            " 'judgment', '2', 'filled', 1, 'x', 'x')"
        )
        old.execute(order, ("u1",))
        old.execute(order, ("u2",))
        for order_id, price in ((1, "100"), (2, "101")):
            old.execute(
                "INSERT INTO fills (order_id, mode, market, side, price, qty, fee, realized_pnl,"
                " filled_utc) VALUES (?, 'paper', 'KRW-AAA', 'buy', ?, '2', '0.1', NULL, 'y')",
                (order_id, price),
            )
        old.commit()
        before = old.execute("SELECT * FROM orders ORDER BY id").fetchall()
        assert migrate(old) == 4
        assert schema_version(old) == 4
        assert old.execute("SELECT value FROM settings WHERE key = 'coins'").fetchall() == [
            ('["KRW-AAA"]',)
        ]
        assert old.execute(
            "SELECT id, order_id, price, qty, fee, realized_pnl, filled_utc, trade_key"
            " FROM fills ORDER BY id"
        ).fetchall() == [
            (1, 1, "100", "2", "0.1", None, "y", "paper"),
            (2, 2, "101", "2", "0.1", None, "y", "paper"),
        ]
        after = old.execute("SELECT * FROM orders ORDER BY id").fetchall()
        assert [row[:-2] for row in after] == before  # 옛 열은 그대로
        assert [row[-2:] for row in after] == [(None, None), (None, None)]
        # 번호는 이어진다(AUTOINCREMENT의 순서가 옮겨진다)
        old.execute(
            "INSERT INTO fills (order_id, mode, market, side, price, qty, fee, filled_utc,"
            " trade_key) VALUES (1, 'paper', 'KRW-AAA', 'buy', '1', '1', '0', 'z', 'exchange-1')"
        )
        assert old.execute("SELECT max(id) FROM fills").fetchone() == (3,)
    finally:
        old.close()


def test_the_tables_are_the_same_from_zero_and_from_three(tmp_path: Path) -> None:
    zero = open_database(tmp_path / "zero.db")
    three = at_three(tmp_path / "three.db")
    try:
        assert migrate(zero) == 4
        assert migrate(three) == 4
        for table in FOURTEEN:
            assert columns(zero, table) == columns(three, table), table
        names = {
            row[0]
            for conn in (zero, three)
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert names == set(FOURTEEN)
        assert migrate(three) == 4  # 4면 아무것도 하지 않는다
    finally:
        zero.close()
        three.close()


def test_the_new_columns_and_tables_follow_the_spec(conn: sqlite3.Connection) -> None:
    order_columns = [c[1] for c in columns(conn, "orders")]
    assert order_columns[-3:] == ["judged_until_utc", "executed_qty", "avg_price_at_send"]
    fill_columns = [c[1] for c in columns(conn, "fills")]
    assert fill_columns[-1] == "trade_key"
    assert [c[1] for c in columns(conn, "live_snapshots")] == [
        "market", "balance", "locked", "direct_locked", "read_utc",
    ]  # fmt: skip
    assert [c[1] for c in columns(conn, "external_changes")] == [
        "id", "mode", "market", "qty_delta", "utc",
    ]  # fmt: skip
    for table in ("fills", "live_snapshots", "external_changes"):
        (sql,) = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        assert sql.rstrip().upper().endswith("STRICT"), table
    with pytest.raises(sqlite3.IntegrityError):  # STRICT
        conn.execute(
            "INSERT INTO external_changes (mode, market, qty_delta, utc)"
            " VALUES (X'00', 'a', 'b', 'c')"
        )


def test_a_fill_is_unique_per_order_and_trade_key(conn: sqlite3.Connection) -> None:
    seed_account(conn)
    order_id = new_order(conn)
    account = PaperAccount(Decimal(1), Decimal(0), ())
    trade_store.record_fill(conn, fill_of(order_id), account)  # 모의매매는 "paper"
    assert conn.execute("SELECT trade_key FROM fills").fetchall() == [("paper",)]
    with pytest.raises(StorageError):  # 모의매매는 주문 하나에 체결 하나
        trade_store.record_fill(conn, fill_of(order_id), account)
    trade_store.record_fill(conn, fill_of(order_id, "101"), account, trade_key="t-2")
    trade_store.record_fill(conn, fill_of(order_id, "102"), account, trade_key="t-3")
    with pytest.raises(StorageError):  # 같은 거래소 체결은 한 번
        trade_store.record_fill(conn, fill_of(order_id, "103"), account, trade_key="t-3")
    assert len(trade_store.fills_of(conn, order_id)) == 3


def test_a_failure_while_raising_to_four_leaves_number_three_and_the_old_fills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = at_three(tmp_path / "old.db")
    try:
        old.execute(
            "INSERT INTO orders (uid, mode, market, side, kind, reason, qty, state, sent,"
            " created_utc, judged_until_utc) VALUES ('u', 'paper', 'KRW-AAA', 'buy', 'limit',"
            " 'judgment', '2', 'filled', 1, 'x', 'x')"
        )
        old.execute(
            "INSERT INTO fills (order_id, mode, market, side, price, qty, fee, filled_utc)"
            " VALUES (1, 'paper', 'KRW-AAA', 'buy', '1', '1', '0', 'y')"
        )
        old.commit()
        monkeypatch.setattr(database, "_SCHEMA_4", (*database._SCHEMA_4[:-1], "CREATE TABLE ("))
        with pytest.raises(StorageError):
            migrate(old)
        assert schema_version(old) == 3
        assert old.execute("SELECT order_id, price FROM fills").fetchall() == [(1, "1")]
        assert "fills_v4" not in {
            r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert not old.in_transaction
    finally:
        old.close()


# --- 설정 키(3.9, 3.7.5) --------------------


def test_the_mode_is_paper_until_it_is_written(conn: sqlite3.Connection) -> None:
    assert settings.read_trade_mode(conn) is Mode.PAPER
    settings.write_trade_mode(conn, Mode.LIVE)
    assert settings.read_trade_mode(conn) is Mode.LIVE
    settings.write_trade_mode(conn, Mode.PAPER)
    assert settings.read_trade_mode(conn) is Mode.PAPER
    conn.execute("UPDATE settings SET value = 'x' WHERE key = 'trade_mode'")
    with pytest.raises(StorageError):
        settings.read_trade_mode(conn)


def test_the_key_created_day_is_written_read_and_deleted(conn: sqlite3.Connection) -> None:
    assert settings.read_key_created(conn) is None
    settings.write_key_created(conn, date(2026, 11, 20))
    assert conn.execute(
        "SELECT value FROM settings WHERE key = 'upbit_key_created'"
    ).fetchall() == [("2026-11-20",)]
    assert settings.read_key_created(conn) == date(2026, 11, 20)
    settings.delete_key_created(conn)
    assert settings.read_key_created(conn) is None
    conn.execute("INSERT INTO settings VALUES ('upbit_key_created', '내일')")
    with pytest.raises(StorageError):
        settings.read_key_created(conn)


def test_the_round_count_n_is_thirty_by_default_and_takes_four_values(
    conn: sqlite3.Connection,
) -> None:
    assert settings.read_live_rounds_n(conn) == 30
    for n in (10, 30, 100, 200):
        settings.write_live_rounds_n(conn, n)
        assert settings.read_live_rounds_n(conn) == n
    with pytest.raises(ValueError, match="N은"):
        settings.write_live_rounds_n(conn, 50)
    conn.execute("UPDATE settings SET value = '50' WHERE key = 'live_rounds_n'")
    with pytest.raises(StorageError):
        settings.read_live_rounds_n(conn)


def view(coins: tuple[str, ...] = (AAA, BBB), live: LiveSettings | None = None) -> SettingsView:
    from fakes.stage3 import stage3_trade

    from coin_report_trader.core import CandleUnit

    return SettingsView(
        coins, 30, True, candle_unit=CandleUnit.MINUTE_60, candle_count=100,
        trade=stage3_trade(coins), live=live,
    )  # fmt: skip


def test_no_live_settings_are_none_and_the_three_keys_are_written_and_read(
    conn: sqlite3.Connection,
) -> None:
    settings.write_settings(conn, view(), created_at=NOW)
    assert settings.read_settings(conn).live is None  # 아직 넣지 않았다
    live = LiveSettings(Decimal(500_000), {AAA: Decimal("60"), BBB: Decimal("40")}, Decimal(5000))
    settings.write_settings(conn, view(live=live), created_at=NOW)
    stored = settings.read_settings(conn).live
    assert stored == live
    keys = dict(conn.execute("SELECT key, value FROM settings WHERE key LIKE 'live_%'").fetchall())
    assert keys == {
        "live_budget": "500000",
        "live_coin_ratio:KRW-AAA": "60",
        "live_coin_ratio:KRW-BBB": "40",
        "live_daily_loss_limit": "5000",
    }
    assert settings.read_settings(conn).trade == view().trade  # 모의매매의 값은 바뀌지 않는다


def test_a_save_without_live_keeps_the_stored_live_settings(conn: sqlite3.Connection) -> None:
    live = LiveSettings(Decimal(500_000), {AAA: Decimal(100)}, Decimal(5000))
    settings.write_settings(conn, view((AAA,), live=live), created_at=NOW)
    settings.write_settings(conn, view((AAA,), live=None), created_at=NOW)
    assert settings.read_settings(conn).live == live


def test_a_live_ratio_of_a_coin_that_is_no_longer_chosen_is_dropped_on_every_save(
    conn: sqlite3.Connection,
) -> None:
    live = LiveSettings(Decimal(500_000), {AAA: Decimal("60"), BBB: Decimal("40")}, Decimal(5000))
    settings.write_settings(conn, view(live=live), created_at=NOW)
    settings.write_settings(conn, view((AAA,), live=None), created_at=NOW)  # 목록에서 BBB를 뺌
    names = [k for (k,) in conn.execute("SELECT key FROM settings WHERE key LIKE 'live_coin%'")]
    assert names == ["live_coin_ratio:KRW-AAA"]
    # 실거래 설정을 비워 저장하면(예산과 한도 None) 그 키는 지워진다
    empty = LiveSettings(None, {AAA: Decimal(100)}, None)
    settings.write_settings(conn, replace(view((AAA,)), live=empty), created_at=NOW)
    assert settings.read_settings(conn).live == empty
    assert conn.execute("SELECT key FROM settings WHERE key = 'live_budget'").fetchall() == []


def test_live_settings_can_be_applied_to_the_trade_settings_after_a_round_trip(
    conn: sqlite3.Connection,
) -> None:
    live = LiveSettings(Decimal(300_000), {AAA: Decimal(100)}, Decimal(2000))
    settings.write_settings(conn, view((AAA,), live=live), created_at=NOW)
    stored = settings.read_settings(conn)
    assert stored.live is not None
    assert stored.trade is not None
    assert stored.live.complete((AAA,))
    applied = stored.live.apply(stored.trade)
    assert (applied.budget, applied.daily_loss_limit) == (Decimal(300_000), Decimal(2000))


def test_the_setting_event_kinds_take_n_changed_and_nothing_else(
    conn: sqlite3.Connection,
) -> None:
    assert trade_store.SETTING_N_CHANGED == "n_changed"
    trade_store.insert_setting_event(
        conn, mode=Mode.LIVE, kind="n_changed", market=None, value="100", at=NOW
    )
    (event,) = trade_store.list_setting_events(conn, Mode.LIVE)
    assert (event.kind, event.value) == ("n_changed", "100")
    with pytest.raises(ValueError, match="종류"):
        trade_store.insert_setting_event(
            conn, mode=Mode.LIVE, kind="x_changed", market=None, value=None, at=NOW
        )


# --- 읽고 쓰기 함수(3.9) --------------------


def test_the_order_columns_for_the_live_fill_and_the_average_at_send(
    conn: sqlite3.Connection,
) -> None:
    seed_account(conn)
    order_id = new_order(conn)
    assert trade_store.read_order_exec(conn, order_id) == (None, None)
    assert trade_store.update_order_exec(conn, order_id, executed_qty=Decimal("1.5"))
    assert trade_store.read_order_exec(conn, order_id) == (Decimal("1.5"), None)
    assert trade_store.update_order_exec(conn, order_id, avg_price_at_send=Decimal("980"))
    assert trade_store.read_order_exec(conn, order_id) == (Decimal("1.5"), Decimal("980"))
    assert trade_store.update_order_exec(conn, order_id)  # 둘 다 없으면 줄이 있는지만 본다
    assert not trade_store.update_order_exec(conn, 999, executed_qty=Decimal(1))
    assert trade_store.read_order_exec(conn, 999) is None


def test_the_last_read_account_is_replaced_as_a_whole(conn: sqlite3.Connection) -> None:
    first = [
        trade_store.LiveSnapshotRow("KRW", Decimal(1000), Decimal(10), Decimal(0), NOW),
        trade_store.LiveSnapshotRow(AAA, Decimal("1.5"), Decimal(0), Decimal("0.5"), NOW),
    ]
    trade_store.replace_live_snapshots(conn, first)
    assert trade_store.read_live_snapshots(conn) == tuple(sorted(first, key=lambda r: r.market))
    second = [trade_store.LiveSnapshotRow("KRW", Decimal(2000), Decimal(0), Decimal(0), NOW)]
    trade_store.replace_live_snapshots(conn, second)
    assert trade_store.read_live_snapshots(conn) == tuple(second)  # 코인 줄은 지워졌다
    trade_store.replace_live_snapshots(conn, [])
    assert trade_store.read_live_snapshots(conn) == ()


def test_external_changes_are_written_and_listed_newest_first(conn: sqlite3.Connection) -> None:
    early = datetime(2026, 1, 5, 1, tzinfo=UTC)
    late = datetime(2026, 1, 5, 2, tzinfo=UTC)
    first = trade_store.insert_external_change(
        conn, mode=Mode.LIVE, market=AAA, qty_delta=Decimal("-0.5"), at=early
    )
    second = trade_store.insert_external_change(
        conn, mode=Mode.LIVE, market=BBB, qty_delta=Decimal("2"), at=late
    )
    rows = trade_store.list_external_changes(conn, Mode.LIVE)
    assert [(r.change_id, r.market, r.qty_delta) for r in rows] == [
        (second, BBB, Decimal(2)),
        (first, AAA, Decimal("-0.5")),
    ]
    assert [
        r.change_id for r in trade_store.list_external_changes(conn, Mode.LIVE, market=AAA)
    ] == [first]
    assert [
        r.change_id for r in trade_store.list_external_changes(conn, Mode.LIVE, start=late)
    ] == [second]
    assert trade_store.list_external_changes(conn, Mode.PAPER) == ()


# --- `PaperStore`를 갖춘 어댑터(3.4, 3.9) --------------------


def test_the_adapter_is_a_paper_store_and_runs_a_whole_order(conn: sqlite3.Connection) -> None:
    store = SqlitePaperStore(conn)
    assert isinstance(store, PaperStore)
    assert store.read_account(Mode.PAPER) is None
    seed_account(conn)
    account = store.read_account(Mode.PAPER)
    assert account is not None
    assert account.krw == 1_000_000
    order = store.insert_order(plan(), mode=Mode.PAPER, uid="a1", round_id=None, created_utc=NOW)
    assert order.state is OrderState.OPEN
    assert conn.execute("SELECT sent, judged_until_utc FROM orders").fetchall() == [
        (0, "2026-01-05T03:01:00Z")  # 건 때를 올림한 분
    ]
    assert store.find_order("a1") == order
    assert store.find_order("none") is None
    store.update_order(order.order_id, state=OrderState.OPEN, sent=True)
    assert store.open_orders(Mode.PAPER) == (store.find_order("a1"),)
    assert store.open_orders(Mode.PAPER, BBB) == ()
    holding = PaperHolding(AAA, Decimal(2), Decimal(0), Decimal(100), Decimal(200), Decimal(2))
    bought = replace(account, krw=Decimal(999_800), holdings=(holding,))
    written = store.apply_fill(fill_of(order.order_id), bought)
    assert written.fill_id > 0
    assert store.fills_of(order.order_id) == (written,)
    assert store.find_order("a1").state is OrderState.FILLED  # type: ignore[union-attr]
    assert store.open_orders(Mode.PAPER) == ()
    assert store.read_account(Mode.PAPER) == bought
    store.write_account(Mode.PAPER, replace(bought, krw=Decimal(1)))
    assert store.read_account(Mode.PAPER).krw == 1  # type: ignore[union-attr]
    with pytest.raises(StorageError):  # 같은 주문의 체결은 한 번
        store.apply_fill(fill_of(order.order_id), bought)


# --- 보관소 이름(3.8) --------------------


def test_the_vault_names_of_the_two_keys() -> None:
    assert (UPBIT_ACCESS_KEY_NAME, UPBIT_SECRET_KEY_NAME) == (
        "upbit_access_key",
        "upbit_secret_key",
    )
