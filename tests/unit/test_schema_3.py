"""저장 스키마 3(M3 명세 3.12)과 `--self-test`의 sqlite 점검(3.18). 값은 모두 만든 값이다."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from coin_report_trader.app.self_test import check_sqlite
from coin_report_trader.core import StorageError
from coin_report_trader.storage import database
from coin_report_trader.storage.database import (
    SCHEMA_VERSION,
    TABLES,
    migrate,
    open_database,
    schema_version,
)

TWELVE = (
    "settings", "candles", "alerts", "rounds", "reports", "report_rows", "paper_accounts",
    "paper_holdings", "orders", "fills", "pauses", "setting_events",
)  # fmt: skip
NEW_TABLES = ("paper_accounts", "paper_holdings", "orders", "fills", "pauses", "setting_events")


def tables(conn: sqlite3.Connection) -> set[str]:
    return {
        name
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def columns(conn: sqlite3.Connection, table: str) -> list[tuple[object, ...]]:
    return [tuple(row) for row in conn.execute(f"PRAGMA table_info({table})")]


def at_version(path: Path, version: int) -> sqlite3.Connection:
    """`migrate`의 앞 단계 묶음만 돌려 그 번호의 DB를 만든다(옛 판의 DB를 흉내 낸다)."""
    conn = open_database(path)
    steps = (database._SCHEMA_1, database._SCHEMA_2)
    for statements in steps[:version]:
        for statement in statements:
            conn.execute(statement)
    conn.execute(f"PRAGMA user_version={version}")
    conn.commit()
    return conn


@pytest.fixture
def made(tmp_path: Path) -> Iterator[dict[int, sqlite3.Connection]]:
    conns = {version: at_version(tmp_path / f"v{version}.db", version) for version in (0, 1, 2)}
    yield conns
    for conn in conns.values():
        conn.close()


def test_the_schema_number_is_three_and_the_tables_are_the_twelve_of_the_spec() -> None:
    assert SCHEMA_VERSION == 4
    assert (*TWELVE, "live_snapshots", "external_changes") == TABLES


@pytest.mark.parametrize("start", [0, 1, 2])
def test_every_old_number_is_raised_to_three_with_the_twelve_tables(
    made: dict[int, sqlite3.Connection], start: int
) -> None:
    conn = made[start]
    assert migrate(conn) == 4
    assert schema_version(conn) == 4
    assert tables(conn) == {*TWELVE, "live_snapshots", "external_changes"}
    assert migrate(conn) == 4  # 4면 아무것도 하지 않는다


def test_the_twelve_tables_are_the_same_from_every_path(
    made: dict[int, sqlite3.Connection],
) -> None:
    for conn in made.values():
        migrate(conn)
    shapes = [{table: columns(conn, table) for table in TWELVE} for conn in made.values()]
    assert shapes[0] == shapes[1] == shapes[2]


def test_the_new_columns_are_added_at_the_end(made: dict[int, sqlite3.Connection]) -> None:
    conn = made[2]
    migrate(conn)
    assert [c[1] for c in columns(conn, "rounds")][-1] == "stage"
    assert [c[1] for c in columns(conn, "reports")][-3:] == [
        "method",
        "order_id",
        "no_order_reason",
    ]


def test_the_new_tables_have_the_columns_of_the_spec(made: dict[int, sqlite3.Connection]) -> None:
    conn = made[0]
    migrate(conn)
    names = {table: [c[1] for c in columns(conn, table)] for table in NEW_TABLES}
    assert names == {
        "paper_accounts": ["mode", "funds", "krw", "locked_krw", "created_utc"],
        "paper_holdings": [
            "mode", "market", "qty", "locked_qty", "buy_amount_sum", "buy_qty_sum",
        ],
        "orders": [
            "id", "uid", "mode", "round_id", "market", "side", "kind", "reason", "price", "qty",
            "state", "sent", "created_utc", "closed_utc", "error_name", "canceled_by_round",
            "judged_until_utc", "executed_qty", "avg_price_at_send",
        ],
        "fills": [
            "id", "order_id", "mode", "market", "side", "price", "qty", "fee", "realized_pnl",
            "filled_utc", "trade_key",
        ],
        "pauses": [
            "id", "mode", "kind", "cause", "detail", "sell_all", "started_utc", "released_utc",
        ],
        "setting_events": ["id", "mode", "kind", "market", "value", "utc"],
    }  # fmt: skip


def test_all_tables_are_strict_and_the_keys_follow_the_spec(
    made: dict[int, sqlite3.Connection],
) -> None:
    conn = made[0]
    migrate(conn)
    for table in TWELVE:
        (sql,) = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        assert sql.rstrip().upper().endswith("STRICT"), table
    with pytest.raises(sqlite3.IntegrityError):  # STRICT: 글을 정수 칸에 넣을 수 없다
        conn.execute(
            "INSERT INTO pauses (mode, kind, sell_all, started_utc) VALUES ('paper', 'user', 'x',"
            " 'y')"
        )
    conn.execute("INSERT INTO paper_accounts VALUES ('paper', '1', '1', '0', 'x')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO paper_accounts VALUES ('paper', '1', '1', '0', 'x')")
    order = (
        "INSERT INTO orders (uid, mode, market, side, kind, reason, qty, state, sent, created_utc,"
        " judged_until_utc) VALUES (?, 'paper', 'KRW-AAA', 'buy', 'limit', 'judgment', '1',"
        " 'open', 0, 'x', 'x')"
    )
    conn.execute(order, ("u1",))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(order, ("u1",))  # 식별값은 UNIQUE
    fill = (
        "INSERT INTO fills (order_id, mode, market, side, price, qty, fee, filled_utc, trade_key)"
        " VALUES (1, 'paper', 'KRW-AAA', 'buy', '1', '1', '0', 'x', ?)"
    )
    conn.execute(fill, ("paper",))
    with pytest.raises(sqlite3.IntegrityError):  # 주문 하나의 같은 체결(`trade_key`)은 하나(M5 3.9)
        conn.execute(fill, ("paper",))
    conn.execute(fill, ("t2",))  # 실거래는 주문 하나에 체결이 여럿이다
    conn.rollback()


def test_the_round_and_report_states_take_discarded_without_a_check(
    made: dict[int, sqlite3.Connection],
) -> None:
    conn = made[2]
    conn.execute(
        'INSERT INTO rounds (mode, number, "trigger", unit, close_utc, next_utc, started_utc,'
        " state) VALUES ('paper', 1, 'bar_close', 'minute60', 'a', 'b', 'c', 'running')"
    )
    conn.commit()
    migrate(conn)
    conn.execute("UPDATE rounds SET state = 'discarded', stage = '정리'")
    assert conn.execute("SELECT state, stage FROM rounds").fetchall() == [("discarded", "정리")]


def test_a_v2_database_with_rows_keeps_every_row(made: dict[int, sqlite3.Connection]) -> None:
    conn = made[2]
    conn.execute("INSERT INTO settings VALUES ('coins', '[\"KRW-AAA\"]')")
    conn.execute(
        'INSERT INTO rounds (mode, number, "trigger", unit, close_utc, next_utc, started_utc,'
        " state) VALUES ('paper', 7, 'bar_close', 'minute60', 'a', 'b', 'c', 'done')"
    )
    conn.execute(
        "INSERT INTO reports (round_id, mode, market, state, created_utc)"
        " VALUES (1, 'paper', 'KRW-AAA', 'ok', 'd')"
    )
    conn.commit()
    migrate(conn)
    assert conn.execute("SELECT value FROM settings WHERE key = 'coins'").fetchone() == (
        '["KRW-AAA"]',
    )
    assert conn.execute("SELECT number, state, stage FROM rounds").fetchall() == [(7, "done", None)]
    assert conn.execute("SELECT state, method, order_id FROM reports").fetchall() == [
        ("ok", None, None)  # 2단계의 보고서는 `method`가 NULL이라 세지 않는다(3.16.2)
    ]


def test_a_failure_while_raising_leaves_no_number_and_no_new_table(
    made: dict[int, sqlite3.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = made[2]
    broken = (*database._SCHEMA_3[:-1], "CREATE TABLE setting_events (x INT) STRICT BROKEN")
    monkeypatch.setattr(database, "_SCHEMA_3", broken)
    with pytest.raises(StorageError):
        migrate(conn)
    assert schema_version(conn) == 2
    assert not (tables(conn) & set(NEW_TABLES))
    assert "stage" not in [c[1] for c in columns(conn, "rounds")]


def test_the_self_test_fails_when_a_table_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from coin_report_trader.app import self_test

    def short(conn: sqlite3.Connection) -> int:
        version = migrate(conn)
        conn.execute("DROP TABLE pauses")
        return version

    monkeypatch.setattr(self_test, "migrate", short)
    with pytest.raises(self_test.CheckFailed, match="표가 맞지 않습니다"):
        check_sqlite()


def test_the_self_test_sees_number_three_and_twelve_tables() -> None:
    detail = check_sqlite()
    assert "스키마 번호 4" in detail
    assert "표 14개" in detail
