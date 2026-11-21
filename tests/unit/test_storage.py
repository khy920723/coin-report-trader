"""저장 스키마 1(M1 명세 3.7): `migrate`, 설정, 봉, 알림의 읽고 쓰기."""

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from coin_report_trader.core import (
    AlertCategory,
    AlertDetail,
    Candle,
    CandleUnit,
    Mode,
    SettingsView,
    StorageError,
)
from coin_report_trader.storage import alerts, candles, settings
from coin_report_trader.storage.database import (
    SCHEMA_VERSION,
    migrate,
    open_database,
    schema_version,
)
from coin_report_trader.storage.states import DiscordState
from coin_report_trader.storage.times import format_utc, parse_utc

T0 = datetime(2026, 1, 2, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_database(tmp_path / "app.db")
    migrate(connection)
    yield connection
    connection.close()


def _pragmas(connection: sqlite3.Connection) -> tuple[str, int, int]:
    return (
        connection.execute("PRAGMA journal_mode").fetchone()[0],
        connection.execute("PRAGMA synchronous").fetchone()[0],
        schema_version(connection),
    )


def _tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )
    return {name for (name,) in rows}


def _candle(day: int = 2, close: str = "100", market: str = "KRW-AAA") -> Candle:
    return Candle(
        market,
        CandleUnit.DAY,
        datetime(2026, 1, day, tzinfo=UTC),
        Decimal("1.5"),
        Decimal("2"),
        Decimal("0.00000001"),
        Decimal(close),
        Decimal("12345.678"),
        Decimal("1E+3"),
    )


def _alert(connection: sqlite3.Connection, **overrides: object) -> int:
    fields: dict[str, object] = {
        "created_at": T0,
        "mode": Mode.PAPER,
        "category": AlertCategory.ERROR,
        "code": "A-1",
        "coin": "KRW-AAA",
        "text": "글",
    }
    fields.update(overrides)
    return alerts.insert_alert(connection, **fields)  # type: ignore[arg-type]


# --- migrate -------------------------------------------------------------------------


def test_migrate_creates_three_strict_tables(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "app.db")
    try:
        assert SCHEMA_VERSION == 1
        assert _tables(connection) == set()
        assert migrate(connection) == 1
        assert _pragmas(connection) == ("wal", 2, 1)
        assert _tables(connection) == {"settings", "candles", "alerts"}
        columns = {
            table: [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
            for table in ("settings", "candles", "alerts")
        }
        assert columns["settings"] == ["key", "value"]
        assert columns["candles"] == [
            "market", "unit", "start_utc", "open", "high", "low", "close", "volume", "value",
        ]  # fmt: skip
        assert columns["alerts"] == [
            "id", "created_utc", "mode", "category", "code", "coin", "text", "stage",
            "error_name", "content", "after", "seen", "discord_state",
        ]  # fmt: skip
        strict = {row[1]: row[5] for row in connection.execute("PRAGMA table_list")}
        assert strict["settings"] == strict["candles"] == strict["alerts"] == 1
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO settings VALUES ('k', x'00')")
    finally:
        connection.close()


def test_alert_numbers_are_never_given_again(conn: sqlite3.Connection) -> None:
    first = _alert(conn)
    last = _alert(conn)
    assert alerts.delete_alert(conn, last) is True
    again = _alert(conn)
    assert again > last > first  # 가장 큰 번호의 줄을 지운 뒤에도 그 번호를 다시 쓰지 않는다
    alerts.delete_category(conn, AlertCategory.ERROR)
    assert _alert(conn) > again
    assert "AUTOINCREMENT" in conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'alerts'"
    ).fetchone()[0]


def _internal_tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE name LIKE 'sqlite_%'")
    return {name for (name,) in rows if not name.startswith("sqlite_autoindex")}


def test_the_number_table_exists_after_migrate_and_not_after_a_failed_one(tmp_path: Path) -> None:
    broken = open_database(tmp_path / "broken.db")
    late = open_database(tmp_path / "late.db")
    good = open_database(tmp_path / "good.db")
    try:
        broken.execute("CREATE TABLE alerts (x TEXT)")  # 셋째 표를 만들 때 실패한다
        broken.commit()
        with pytest.raises(StorageError):
            migrate(broken)
        assert _internal_tables(broken) == set()
        # 셋째 표(`AUTOINCREMENT`)를 만든 뒤, 번호를 쓰다 실패해도 `sqlite_sequence`가 남지 않는다.
        with pytest.raises(StorageError):
            migrate(_FailsOnVersionWrite(late))  # type: ignore[arg-type]
        assert _internal_tables(late) == set()
        assert _tables(late) == set()
        assert migrate(good) == 1
        assert _internal_tables(good) == {"sqlite_sequence"}
    finally:
        broken.close()
        late.close()
        good.close()


def test_migrate_at_version_one_changes_nothing(conn: sqlite3.Connection) -> None:
    settings.write_settings(conn, SettingsView(("KRW-AAA",), 30, True))
    assert migrate(conn) == 1
    assert migrate(conn) == 1
    assert settings.read_settings(conn) == SettingsView(("KRW-AAA",), 30, True)
    assert not conn.in_transaction


def test_migrate_refuses_a_newer_number(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "app.db")
    try:
        connection.execute("PRAGMA user_version=2")
        with pytest.raises(StorageError):
            migrate(connection)
        assert schema_version(connection) == 2
        assert _tables(connection) == set()
    finally:
        connection.close()


def test_migrate_failure_midway_leaves_no_number_and_no_tables(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "app.db")
    try:
        # 셋째 표(alerts)를 만들 때 이미 있어서 실패한다. 앞서 만든 두 표도 남으면 안 된다.
        connection.execute("CREATE TABLE alerts (x TEXT)")
        connection.commit()
        with pytest.raises(StorageError):
            migrate(connection)
        assert _tables(connection) == {"alerts"}
        assert _pragmas(connection) == ("wal", 2, 0)
        assert not connection.in_transaction
    finally:
        connection.close()


class _FailsOnVersionWrite:
    """`PRAGMA user_version=`를 쓸 때만 실패하는 연결(앞의 표 만들기는 이미 끝난 뒤)."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    def execute(self, sql: str, *args: object) -> sqlite3.Cursor:
        if sql.startswith("PRAGMA user_version="):
            raise sqlite3.OperationalError("disk I/O error")
        return self._real.execute(sql, *args)

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)


def test_migrate_failure_at_the_number_write_rolls_the_tables_back(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "app.db")
    try:
        with pytest.raises(StorageError):
            migrate(_FailsOnVersionWrite(connection))  # type: ignore[arg-type]
        assert _tables(connection) == set()
        assert _pragmas(connection) == ("wal", 2, 0)
        assert migrate(connection) == 1
    finally:
        connection.close()


# --- 시각 글 -----------------------------------------------------------------------------


def test_time_text_format() -> None:
    assert format_utc(T0) == "2026-01-02T00:00:00Z"
    assert format_utc(datetime(2026, 1, 2, 3, 4, 5, 999999, tzinfo=UTC)) == "2026-01-02T03:04:05Z"
    kst = timezone(timedelta(hours=9))
    assert format_utc(datetime(2026, 1, 2, 9, 0, tzinfo=kst)) == "2026-01-02T00:00:00Z"
    with pytest.raises(ValueError, match="시간대"):
        format_utc(datetime(2026, 1, 2))
    assert parse_utc("2026-01-02T03:04:05Z") == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    for bad in ("2026-01-02", "2026-01-02T03:04:05", "2026-13-02T03:04:05Z", "2026-1-2T3:4:5Z"):
        with pytest.raises(StorageError):
            parse_utc(bad)


# --- 설정 ----------------------------------------------------------------------------------


def test_settings_default_and_four_keys(conn: sqlite3.Connection) -> None:
    assert settings.read_settings(conn) == SettingsView()
    settings.write_settings(conn, SettingsView(("KRW-BBB", "KRW-AAA"), 90, True))
    settings.write_fetched_at(conn, "KRW-AAA", datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=UTC))
    stored = dict(conn.execute("SELECT key, value FROM settings"))
    assert stored == {
        "coins": '["KRW-BBB", "KRW-AAA"]',
        "retention_days": "90",
        "first_setup_level": "1",
        "fetched_at:KRW-AAA": "2026-01-02T03:04:05Z",
    }
    assert settings.read_settings(conn) == SettingsView(("KRW-BBB", "KRW-AAA"), 90, True)


def test_settings_overwrite_and_clear(conn: sqlite3.Connection) -> None:
    settings.write_settings(conn, SettingsView(("KRW-AAA",), 7, True))
    settings.write_settings(conn, SettingsView(("KRW-CCC",), None, False))
    assert settings.read_settings(conn) == SettingsView(("KRW-CCC",), None, False)
    assert dict(conn.execute("SELECT key, value FROM settings")) == {"coins": '["KRW-CCC"]'}
    assert not conn.in_transaction


def test_settings_reject_a_retention_that_is_not_offered(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="보관 기간"):
        settings.write_settings(conn, SettingsView(("KRW-AAA",), 14, False))
    assert settings.read_settings(conn) == SettingsView()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("coins", "not json"),
        ("coins", '{"a": 1}'),
        ("coins", "[1]"),
        ("retention_days", "14"),
        ("first_setup_level", "x"),
    ],
)
def test_settings_read_stops_on_a_stored_value_that_does_not_fit(
    conn: sqlite3.Connection, key: str, value: str
) -> None:
    conn.execute("INSERT INTO settings VALUES (?, ?)", (key, value))
    conn.commit()
    with pytest.raises(StorageError):
        settings.read_settings(conn)


def test_fetched_at_per_coin(conn: sqlite3.Connection) -> None:
    assert settings.read_fetched_at(conn, "KRW-AAA") is None
    settings.write_fetched_at(conn, "KRW-AAA", T0)
    settings.write_fetched_at(conn, "KRW-BBB", T0 + timedelta(hours=1))
    settings.write_fetched_at(conn, "KRW-AAA", T0 + timedelta(hours=2))
    assert settings.read_fetched_at(conn, "KRW-AAA") == T0 + timedelta(hours=2)
    assert settings.read_fetched_at(conn, "KRW-BBB") == T0 + timedelta(hours=1)
    with pytest.raises(ValueError, match="시간대"):
        settings.write_fetched_at(conn, "KRW-AAA", datetime(2026, 1, 1))


# --- 봉 ------------------------------------------------------------------------------------


def test_candles_round_trip_decimal_text_and_order(conn: sqlite3.Connection) -> None:
    given = [_candle(4), _candle(2), _candle(3)]
    assert candles.write_candles(conn, given) == 3
    read = candles.read_candles(conn, "KRW-AAA", CandleUnit.DAY)
    assert [c.start.day for c in read] == [2, 3, 4]
    assert read[0] == _candle(2)
    assert read[0].low == Decimal("0.00000001")
    assert str(read[0].value) == "1E+3"
    row = conn.execute(
        "SELECT open, low, value, start_utc, unit FROM candles ORDER BY start_utc"
    ).fetchone()
    assert row == ("1.5", "1E-8", "1E+3", "2026-01-02T00:00:00Z", "day")
    assert candles.read_candles(conn, "KRW-AAA", CandleUnit.MINUTE_60) == ()
    assert candles.read_candles(conn, "KRW-BBB", CandleUnit.DAY) == ()


def test_candles_with_the_same_key_are_overwritten(conn: sqlite3.Connection) -> None:
    candles.write_candles(conn, [_candle(2, "100"), _candle(3, "100")])
    candles.write_candles(conn, [_candle(3, "250"), _candle(4, "300")])
    read = candles.read_candles(conn, "KRW-AAA", CandleUnit.DAY)
    assert [(c.start.day, c.close) for c in read] == [
        (2, Decimal(100)), (3, Decimal(250)), (4, Decimal(300)),
    ]  # fmt: skip
    assert conn.execute("SELECT count(*) FROM candles").fetchone() == (3,)


def test_last_candle_start(conn: sqlite3.Connection) -> None:
    assert candles.last_candle_start(conn, "KRW-AAA", CandleUnit.DAY) is None
    candles.write_candles(conn, [_candle(3), _candle(2), _candle(9, market="KRW-BBB")])
    assert candles.last_candle_start(conn, "KRW-AAA", CandleUnit.DAY) == datetime(
        2026, 1, 3, tzinfo=UTC
    )
    assert candles.last_candle_start(conn, "KRW-AAA", CandleUnit.MINUTE_60) is None


def test_write_candles_with_none_does_nothing(conn: sqlite3.Connection) -> None:
    assert candles.write_candles(conn, []) == 0
    assert not conn.in_transaction


def test_a_failing_write_leaves_none_of_its_candles_and_no_open_transaction(
    conn: sqlite3.Connection,
) -> None:
    candles.write_candles(conn, [_candle(2, "100")])
    bad = replace(_candle(5), market=object())  # type: ignore[arg-type]  # DB가 받지 못하는 값
    with pytest.raises(StorageError):
        candles.write_candles(conn, [_candle(2, "999"), _candle(3), bad, _candle(4)])
    assert not conn.in_transaction
    # 같은 연결의 다음 쓰기와 커밋이 앞의 반쪽 쓰기를 확정하지 않는다.
    settings.write_fetched_at(conn, "KRW-AAA", T0)
    conn.commit()
    read = candles.read_candles(conn, "KRW-AAA", CandleUnit.DAY)
    assert [(c.start.day, c.close) for c in read] == [(2, Decimal(100))]


def test_read_candles_stops_on_a_stored_value_that_does_not_fit(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO candles VALUES ('KRW-AAA', 'day', '2026-01-02T00:00:00Z',"
        " 'x', '1', '1', '1', '1', '1')"
    )
    conn.commit()
    with pytest.raises(StorageError):
        candles.read_candles(conn, "KRW-AAA", CandleUnit.DAY)


# --- 알림 ----------------------------------------------------------------------------------


def test_alert_columns_null_seen_and_discord_state(conn: sqlite3.Connection) -> None:
    plain = _alert(conn, category=AlertCategory.LIMIT, coin=None)
    detailed = _alert(
        conn,
        detail=AlertDetail("봉 받기", "KRW-AAA", "시간 초과", "내용", "다시 받습니다."),
        seen=True,
        discord_state=DiscordState.LOCAL_ONLY,
    )
    first = conn.execute("SELECT * FROM alerts WHERE id = ?", (plain,)).fetchone()
    assert first == (
        plain, "2026-01-02T00:00:00Z", "paper", "limit", "A-1", None, "글",
        None, None, None, None, 0, "not_sent",
    )  # fmt: skip
    second = conn.execute("SELECT * FROM alerts WHERE id = ?", (detailed,)).fetchone()
    assert second[7:] == ("봉 받기", "시간 초과", "내용", "다시 받습니다.", 1, "local_only")
    shown = {a.id: a for a in alerts.list_alerts(conn)}
    assert shown[plain].detail is None
    assert shown[plain].coin is None
    assert not shown[plain].seen
    assert shown[detailed].detail == AlertDetail(
        "봉 받기", "KRW-AAA", "시간 초과", "내용", "다시 받습니다."
    )
    assert shown[detailed].seen
    assert not shown[detailed].discord_failed


def test_insert_alert_rules(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match=r"detail\.coin"):
        _alert(conn, detail=AlertDetail("단계", "KRW-BBB", "오류", "내용", "뒤"))
    with pytest.raises(ValueError, match="시간대"):
        _alert(conn, created_at=datetime(2026, 1, 2))
    with pytest.raises(ValueError, match="lost"):
        _alert(conn, discord_state="lost")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO alerts (created_utc, mode, category, code, text, seen, discord_state)"
            " VALUES ('2026-01-02T00:00:00Z', 'paper', 'error', 'A-1', 't', 2, 'sent')"
        )
    assert conn.execute("SELECT count(*) FROM alerts").fetchone() == (0,)


def test_list_alerts_newest_first(conn: sqlite3.Connection) -> None:
    old = _alert(conn, created_at=T0)
    new = _alert(conn, created_at=T0 + timedelta(hours=1))
    tied = _alert(conn, created_at=T0 + timedelta(hours=1))
    assert [a.id for a in alerts.list_alerts(conn)] == [tied, new, old]
    assert alerts.list_alerts(conn)[0].created_at == T0 + timedelta(hours=1)
    assert alerts.list_alerts(conn)[0].mode is Mode.PAPER
    assert alerts.list_alerts(conn)[0].category is AlertCategory.ERROR


def test_count_unseen_per_category_and_mark_seen(conn: sqlite3.Connection) -> None:
    assert dict(alerts.count_unseen(conn)) == dict.fromkeys(AlertCategory, 0)
    first = _alert(conn)
    _alert(conn)
    _alert(conn, category=AlertCategory.OTHER)
    _alert(conn, seen=True)
    counts = alerts.count_unseen(conn)
    assert counts[AlertCategory.ERROR] == 2
    assert counts[AlertCategory.OTHER] == 1
    assert counts[AlertCategory.TRADE] == 0
    assert alerts.mark_seen(conn, first) is True
    assert alerts.mark_seen(conn, 999) is False
    assert alerts.count_unseen(conn)[AlertCategory.ERROR] == 1


def test_delete_one_and_delete_a_category(conn: sqlite3.Connection) -> None:
    one = _alert(conn)
    other = _alert(conn)
    _alert(conn, category=AlertCategory.OTHER)
    _alert(conn, category=AlertCategory.OTHER)
    assert alerts.delete_alert(conn, one) is True
    assert alerts.delete_alert(conn, one) is False
    assert [a.id for a in alerts.list_alerts(conn) if a.category is AlertCategory.ERROR] == [other]
    assert alerts.delete_category(conn, AlertCategory.OTHER) == 2
    assert alerts.delete_category(conn, AlertCategory.OTHER) == 0
    assert [a.id for a in alerts.list_alerts(conn)] == [other]
    assert not conn.in_transaction


def test_delete_expired_uses_created_plus_days_at_or_before_now(conn: sqlite3.Connection) -> None:
    now = datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC)
    exactly = _alert(conn, created_at=now - timedelta(days=7))
    older = _alert(conn, created_at=now - timedelta(days=30))
    just_inside = _alert(conn, created_at=now - timedelta(days=7) + timedelta(seconds=1))
    fresh = _alert(conn, created_at=now)
    assert alerts.delete_expired(conn, retention_days=7, now=now) == 2
    assert {a.id for a in alerts.list_alerts(conn)} == {just_inside, fresh}
    assert exactly != older
    assert alerts.delete_expired(conn, retention_days=30, now=now) == 0
    # 초 아래가 있는 now도 같은 규칙이다.
    later = now + timedelta(seconds=1, microseconds=5)
    assert alerts.delete_expired(conn, retention_days=7, now=later) == 1


def test_set_discord_state_and_failed_flag(conn: sqlite3.Connection) -> None:
    alert_id = _alert(conn)
    assert alerts.set_discord_state(conn, alert_id, DiscordState.FAILED) is True
    assert alerts.list_alerts(conn)[0].discord_failed is True
    assert alerts.set_discord_state(conn, alert_id, DiscordState.SENT) is True
    assert alerts.list_alerts(conn)[0].discord_failed is False
    assert alerts.set_discord_state(conn, 999, DiscordState.SENT) is False
    for state in (DiscordState.NO_ADDRESS, DiscordState.LOCAL_ONLY):
        alerts.set_discord_state(conn, alert_id, state)
        assert alerts.list_alerts(conn)[0].discord_failed is False


def test_settle_not_sent_changes_only_that_category(conn: sqlite3.Connection) -> None:
    a = _alert(conn)
    b = _alert(conn, discord_state=DiscordState.SENT)
    c = _alert(conn, category=AlertCategory.OTHER)
    d = _alert(conn, discord_state=DiscordState.LOCAL_ONLY)
    assert alerts.settle_not_sent(conn, AlertCategory.ERROR, DiscordState.FAILED) == 1
    states = dict(conn.execute("SELECT id, discord_state FROM alerts"))
    assert states == {a: "failed", b: "sent", c: "not_sent", d: "local_only"}
    assert alerts.settle_not_sent(conn, AlertCategory.OTHER, DiscordState.NO_ADDRESS) == 1
    assert dict(conn.execute("SELECT id, discord_state FROM alerts"))[c] == "no_address"
    for state in (DiscordState.NOT_SENT, DiscordState.SENT, DiscordState.LOCAL_ONLY):
        with pytest.raises(ValueError, match="주소 상태"):
            alerts.settle_not_sent(conn, AlertCategory.ERROR, state)


def test_list_alerts_stops_on_a_stored_row_that_does_not_fit(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO alerts (created_utc, mode, category, code, text, stage, seen, discord_state)"
        " VALUES ('2026-01-02T00:00:00Z', 'paper', 'error', 'A-1', 't', 's', 0, 'sent')"
    )
    conn.commit()
    with pytest.raises(StorageError):  # 자세한 글이 일부만 있다
        alerts.list_alerts(conn)


def test_a_closed_connection_is_reported_as_storage_error(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "app.db")
    migrate(connection)
    connection.close()
    with pytest.raises(StorageError):
        settings.read_settings(connection)
    with pytest.raises(StorageError):
        alerts.delete_alert(connection, 1)
    with pytest.raises(StorageError):
        candles.write_candles(connection, [_candle()])


# --- 1단계 보완(M1 명세 3.5.3, 3.5.4, 3.7, 35번): 봉 단위·봉 개수, 읽기와 지우기 ------------------


def test_settings_keep_the_unit_and_the_count_and_write_level_two(conn: sqlite3.Connection) -> None:
    done = SettingsView(("KRW-AAA",), 30, True, candle_unit=CandleUnit.MINUTE_240, candle_count=500)
    settings.write_settings(conn, done)
    assert dict(conn.execute("SELECT key, value FROM settings")) == {
        "coins": '["KRW-AAA"]',
        "retention_days": "30",
        "candle_unit": "minute240",
        "candle_count": "500",
        "first_setup_level": "2",
    }
    assert settings.read_settings(conn) == done


def test_the_level_is_one_when_the_unit_or_the_count_is_missing(conn: sqlite3.Connection) -> None:
    for unit, count in ((None, None), (CandleUnit.DAY, None), (None, 100)):
        settings.write_settings(
            conn, SettingsView(("KRW-AAA",), 7, True, candle_unit=unit, candle_count=count)
        )
        stored = dict(conn.execute("SELECT key, value FROM settings"))
        assert stored["first_setup_level"] == "1"  # 1단계 보완 전에 마친 모습 그대로
        assert settings.read_settings(conn).first_setup_done is True


def test_first_setup_done_still_follows_only_the_level_key(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO settings VALUES ('first_setup_level', '2')")
    conn.commit()
    view = settings.read_settings(conn)
    assert (view.first_setup_done, view.candle_unit, view.candle_count) == (True, None, None)


def test_a_not_done_setting_clears_the_level_but_keeps_the_unit_and_the_count(
    conn: sqlite3.Connection,
) -> None:
    settings.write_settings(
        conn, SettingsView((), 7, True, candle_unit=CandleUnit.DAY, candle_count=200)
    )
    settings.write_settings(
        conn, SettingsView((), 7, False, candle_unit=CandleUnit.MINUTE_30, candle_count=100)
    )
    stored = dict(conn.execute("SELECT key, value FROM settings"))
    assert "first_setup_level" not in stored
    assert (stored["candle_unit"], stored["candle_count"]) == ("minute30", "100")


def test_none_unit_and_none_count_delete_their_keys(conn: sqlite3.Connection) -> None:
    settings.write_settings(
        conn, SettingsView(("KRW-AAA",), 7, True, candle_unit=CandleUnit.DAY, candle_count=200)
    )
    settings.write_settings(conn, SettingsView(("KRW-AAA",), 7, True))
    assert set(dict(conn.execute("SELECT key, value FROM settings"))) == {
        "coins", "retention_days", "first_setup_level",
    }  # fmt: skip


def test_settings_reject_a_count_that_is_not_offered(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="봉 개수"):
        settings.write_settings(conn, SettingsView(("KRW-AAA",), 7, False, candle_count=150))
    assert settings.read_settings(conn) == SettingsView()
    assert not conn.in_transaction


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("candle_unit", "minute15"),
        ("candle_unit", ""),
        ("candle_count", "150"),
        ("candle_count", "x"),
    ],
)
def test_settings_read_stops_on_a_stored_unit_or_count_that_does_not_fit(
    conn: sqlite3.Connection, key: str, value: str
) -> None:
    conn.execute("INSERT INTO settings VALUES (?, ?)", (key, value))
    conn.commit()
    with pytest.raises(StorageError):
        settings.read_settings(conn)


def _unit_candle(unit: CandleUnit, start: datetime, market: str = "KRW-AAA") -> Candle:
    price = Decimal("100")
    return Candle(market, unit, start, price, price, price, price, Decimal(1), Decimal(1))


def test_first_candle_start_per_coin_and_unit(conn: sqlite3.Connection) -> None:
    assert candles.first_candle_start(conn, "KRW-AAA", CandleUnit.DAY) is None
    candles.write_candles(conn, [_candle(4), _candle(2), _candle(3, market="KRW-BBB")])
    assert candles.first_candle_start(conn, "KRW-AAA", CandleUnit.DAY) == T0
    assert candles.first_candle_start(conn, "KRW-BBB", CandleUnit.DAY) == T0 + timedelta(days=1)
    assert candles.first_candle_start(conn, "KRW-AAA", CandleUnit.MINUTE_60) is None


@pytest.mark.parametrize(
    ("unit", "length"),
    [
        (CandleUnit.MINUTE_30, timedelta(minutes=30)),
        (CandleUnit.MINUTE_60, timedelta(minutes=60)),
        (CandleUnit.MINUTE_240, timedelta(minutes=240)),
        (CandleUnit.DAY, timedelta(days=1)),
    ],
)
def test_a_candle_is_complete_when_start_plus_length_is_not_after_now(
    conn: sqlite3.Connection, unit: CandleUnit, length: timedelta
) -> None:
    starts = [T0 + length * i for i in range(5)]  # 마지막 봉은 시작 + 길이가 `now`와 같다
    candles.write_candles(conn, [_unit_candle(unit, start) for start in starts])
    now = starts[-1] + length
    assert candles.count_complete_candles(conn, "KRW-AAA", unit, now) == 5
    assert candles.count_complete_candles(conn, "KRW-AAA", unit, now - timedelta(seconds=1)) == 4
    assert candles.count_complete_candles(conn, "KRW-AAA", unit, starts[0]) == 0
    assert candles.count_complete_candles(conn, "KRW-BBB", unit, now) == 0
    other = next(u for u in CandleUnit if u is not unit)
    assert candles.count_complete_candles(conn, "KRW-AAA", other, now) == 0


def test_the_latest_complete_candles_come_oldest_first_and_skip_the_building_one(
    conn: sqlite3.Connection,
) -> None:
    unit = CandleUnit.MINUTE_60
    hour = timedelta(hours=1)
    candles.write_candles(conn, [_unit_candle(unit, T0 + hour * i) for i in range(6)])
    now = T0 + hour * 5 + timedelta(minutes=30)  # 봉 5는 만드는 중이다
    latest = candles.read_latest_complete_candles(conn, "KRW-AAA", unit, now, 3)
    assert [c.start for c in latest] == [T0 + hour * 2, T0 + hour * 3, T0 + hour * 4]
    fewer = candles.read_latest_complete_candles(conn, "KRW-AAA", unit, now, 100)
    assert [c.start for c in fewer] == [T0 + hour * i for i in range(5)]
    assert candles.read_latest_complete_candles(conn, "KRW-AAA", unit, now, 0) == ()
    assert candles.read_latest_complete_candles(conn, "KRW-BBB", unit, now, 3) == ()
    assert latest[0] == _unit_candle(unit, T0 + hour * 2)  # 값은 쓴 그대로 읽힌다


def test_has_candles_looks_at_any_coin_of_that_unit(conn: sqlite3.Connection) -> None:
    assert not any(candles.has_candles(conn, unit) for unit in CandleUnit)
    candles.write_candles(conn, [_unit_candle(CandleUnit.MINUTE_240, T0, market="KRW-BBB")])
    assert candles.has_candles(conn, CandleUnit.MINUTE_240)
    assert not candles.has_candles(conn, CandleUnit.DAY)


def test_delete_candles_except_keeps_only_the_chosen_unit(conn: sqlite3.Connection) -> None:
    given = [
        _unit_candle(unit, T0 + timedelta(hours=i), market)
        for unit in CandleUnit
        for market in ("KRW-AAA", "KRW-BBB")
        for i in range(2)
    ]
    candles.write_candles(conn, given)
    assert candles.delete_candles_except(conn, CandleUnit.MINUTE_60) == 12
    left = {(row[0], row[1]) for row in conn.execute("SELECT market, unit FROM candles")}
    assert left == {("KRW-AAA", "minute60"), ("KRW-BBB", "minute60")}
    assert conn.execute("SELECT count(*) FROM candles").fetchone() == (4,)
    assert candles.delete_candles_except(conn, CandleUnit.MINUTE_60) == 0
    assert not conn.in_transaction


def test_delete_all_fetched_at_leaves_the_other_keys(conn: sqlite3.Connection) -> None:
    settings.write_settings(
        conn, SettingsView(("KRW-AAA",), 7, True, candle_unit=CandleUnit.DAY, candle_count=100)
    )
    settings.write_fetched_at(conn, "KRW-AAA", T0)
    settings.write_fetched_at(conn, "KRW-BBB", T0)
    conn.execute("INSERT INTO settings VALUES ('fetched_atX', 'keep')")
    conn.commit()
    assert settings.delete_all_fetched_at(conn) == 2
    assert settings.read_fetched_at(conn, "KRW-AAA") is None
    assert settings.read_fetched_at(conn, "KRW-BBB") is None
    stored = dict(conn.execute("SELECT key, value FROM settings"))
    assert set(stored) == {
        "coins", "retention_days", "candle_unit", "candle_count", "first_setup_level",
        "fetched_atX",
    }  # fmt: skip
    assert not conn.in_transaction


def test_the_new_reads_and_deletes_report_a_closed_connection_as_storage_error(
    tmp_path: Path,
) -> None:
    connection = open_database(tmp_path / "app.db")
    migrate(connection)
    connection.close()
    unit = CandleUnit.DAY
    for call in (
        lambda: candles.first_candle_start(connection, "KRW-AAA", unit),
        lambda: candles.count_complete_candles(connection, "KRW-AAA", unit, T0),
        lambda: candles.read_latest_complete_candles(connection, "KRW-AAA", unit, T0, 3),
        lambda: candles.has_candles(connection, unit),
        lambda: candles.delete_candles_except(connection, unit),
        lambda: settings.delete_all_fetched_at(connection),
    ):
        with pytest.raises(StorageError):
            call()
