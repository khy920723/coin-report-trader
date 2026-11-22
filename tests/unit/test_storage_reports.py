"""스키마 2(M2 명세 3.10, 5번): 올리기, 표 셋, `alerts.report_id`, 설정의 ai 키, 보고서의
쓰기와 읽기. 숫자와 글은 모두 만든 값이다."""

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from coin_report_trader.core import (
    AI_SLOT_TITLES,
    AiChoice,
    AiSlot,
    AlertCategory,
    Belief,
    CandleJudgment,
    CandleUnit,
    Direction,
    Effort,
    Mode,
    ModelTokens,
    ReportState,
    RoundTrigger,
    RowKind,
    SearchJudgment,
    SettingsView,
    StorageError,
    TokenUsage,
    Verdict,
)
from coin_report_trader.storage import alerts, candles, database, reports, settings
from coin_report_trader.storage.database import migrate, open_database
from coin_report_trader.storage.reports import (
    LastJudgment,
    ReportRecord,
    ReportRow,
    finish_report,
    finish_round,
    insert_report,
    insert_round,
)
from coin_report_trader.storage.times import parse_utc

AAA = "KRW-AAA"
T0 = parse_utc("2026-01-05T03:00:00Z")
T1 = parse_utc("2026-01-05T04:00:00Z")
T2 = parse_utc("2026-01-05T05:00:00Z")
SIX = ("settings", "candles", "alerts", "rounds", "reports", "report_rows")


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = open_database(tmp_path / "app.db")
    migrate(connection)
    yield connection
    connection.close()


def make_v1(path: Path) -> sqlite3.Connection:
    """v0.1.0이 만든 DB(스키마 1)를 흉내 낸다: `_SCHEMA_1`을 그대로 돌려 번호를 1로 쓴다."""
    connection = open_database(path)
    for statement in database._SCHEMA_1:
        connection.execute(statement)
    connection.execute("PRAGMA user_version=1")
    connection.commit()
    return connection


def table_info(connection: sqlite3.Connection) -> dict[str, list[tuple[object, ...]]]:
    return {
        table: [tuple(row) for row in connection.execute(f"PRAGMA table_info({table})")]
        for table in SIX
    }


# --- 올리기 -------------------------------------------------------------------------------


def test_a_v1_database_with_rows_is_raised_and_keeps_every_row(tmp_path: Path) -> None:
    connection = make_v1(tmp_path / "app.db")
    try:
        settings.write_settings(
            connection,
            SettingsView(("KRW-AAA",), 30, True, candle_unit=CandleUnit.DAY, candle_count=100),
        )
        connection.execute(
            "INSERT INTO candles VALUES ('KRW-AAA', 'day', '2026-01-02T00:00:00Z',"
            " '1', '2', '0.5', '1.5', '10', '15')"
        )
        connection.execute(
            "INSERT INTO alerts (created_utc, mode, category, code, coin, text, seen,"
            " discord_state) VALUES ('2026-01-02T00:00:00Z', 'paper', 'error', 'A-1',"
            " 'KRW-AAA', '글', 0, 'not_sent')"
        )
        connection.commit()
        before = {
            "settings": connection.execute("SELECT * FROM settings ORDER BY key").fetchall(),
            "candles": connection.execute("SELECT * FROM candles").fetchall(),
            "alerts": connection.execute(
                "SELECT id, created_utc, mode, category, code, coin, text, stage, error_name,"
                ' content, "after", seen, discord_state FROM alerts'
            ).fetchall(),
        }
        assert migrate(connection) == 2
        assert database.schema_version(connection) == 2
        assert (
            before["settings"]
            == connection.execute("SELECT * FROM settings ORDER BY key").fetchall()
        )
        assert before["candles"] == connection.execute("SELECT * FROM candles").fetchall()
        assert (
            before["alerts"]
            == connection.execute(
                "SELECT id, created_utc, mode, category, code, coin, text, stage, error_name,"
                ' content, "after", seen, discord_state FROM alerts'
            ).fetchall()
        )
        assert connection.execute("SELECT report_id FROM alerts").fetchall() == [(None,)]
        shown = alerts.list_alerts(connection)
        assert [a.report_id for a in shown] == [None]  # 옛 줄은 NULL이다
        assert settings.read_settings(connection).first_setup_done is True
    finally:
        connection.close()


def test_the_six_tables_are_the_same_from_zero_and_from_one(tmp_path: Path) -> None:
    zero = open_database(tmp_path / "zero.db")
    one = make_v1(tmp_path / "one.db")
    try:
        assert migrate(zero) == 2
        assert migrate(one) == 2
        assert table_info(zero) == table_info(one)
        assert [row[1] for row in zero.execute("PRAGMA table_info(alerts)")][-1] == "report_id"
    finally:
        zero.close()
        one.close()


def test_the_six_tables_are_strict_and_two_changes_nothing(conn: sqlite3.Connection) -> None:
    strict = {row[1]: row[5] for row in conn.execute("PRAGMA table_list")}
    for table in SIX:
        assert strict[table] == 1, table
    names = {
        name
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert names == set(SIX)
    before = table_info(conn)
    assert migrate(conn) == 2  # 이미 2면 아무것도 하지 않는다
    assert table_info(conn) == before
    assert not conn.in_transaction


def test_no_check_is_set_on_the_enumerated_columns_of_the_new_tables(
    conn: sqlite3.Connection,
) -> None:
    for table in ("rounds", "reports", "report_rows"):
        (sql,) = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()
        assert "CHECK" not in sql.upper(), table
    # 3단계와 4단계가 더할 값도 4단계의 exe가 만든 DB에서 쓸 수 있다.
    round_id = insert_round(
        conn,
        mode=Mode.PAPER,
        number=1,
        trigger=RoundTrigger.BAR_CLOSE,
        unit=CandleUnit.MINUTE_60,
        close_at=T0,
        next_at=T1,
        started_at=T0,
    )
    conn.execute(
        "UPDATE rounds SET state = 'whatever', \"trigger\" = 'missed' WHERE id = ?", (round_id,)
    )
    conn.commit()
    report_id = insert_report(conn, round_id=round_id, mode=Mode.PAPER, market=AAA, created_at=T0)
    conn.execute(
        "UPDATE reports SET state = 'discarded', direction = '보류' WHERE id = ?", (report_id,)
    )
    conn.commit()


def test_a_failure_while_raising_one_to_two_leaves_the_number_and_the_tables(
    tmp_path: Path,
) -> None:
    connection = make_v1(tmp_path / "app.db")
    try:
        connection.execute("CREATE TABLE reports (x TEXT)")  # 둘째 표를 만들 때 실패한다
        connection.commit()
        with pytest.raises(StorageError):
            migrate(connection)
        assert database.schema_version(connection) == 1
        tables = {
            name
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert tables == {"settings", "candles", "alerts", "reports"}  # 앞서 만든 rounds도 없다
        assert "report_id" not in [
            row[1] for row in connection.execute("PRAGMA table_info(alerts)")
        ]
        assert not connection.in_transaction
    finally:
        connection.close()


def test_the_number_is_written_last_and_once(tmp_path: Path) -> None:
    statements: list[str] = []

    class Spy:
        def __init__(self, real: sqlite3.Connection) -> None:
            self._real = real

        def execute(self, sql: str, *args: object) -> sqlite3.Cursor:
            statements.append(" ".join(sql.split()))
            return self._real.execute(sql, *args)

        def executescript(self, sql: str) -> None:
            raise AssertionError("executescript를 쓰면 안 된다")

        def __getattr__(self, name: str) -> object:
            return getattr(self._real, name)

    real = open_database(tmp_path / "app.db")
    try:
        migrate(Spy(real))  # type: ignore[arg-type]
    finally:
        real.close()
    writes = [s for s in statements if s.startswith("PRAGMA user_version=")]
    assert writes == ["PRAGMA user_version=2"]
    assert statements[-1] == "PRAGMA user_version=2"
    assert not any("IF NOT EXISTS" in s.upper() for s in statements)


# --- alerts.report_id ---------------------------------------------------------------------


def test_insert_alert_takes_a_report_id_and_the_view_carries_it(conn: sqlite3.Connection) -> None:
    fields = {
        "created_at": T0,
        "mode": Mode.PAPER,
        "category": AlertCategory.ERROR,
        "code": "A-26",
        "coin": AAA,
        "text": "글",
    }
    plain = alerts.insert_alert(conn, **fields)  # type: ignore[arg-type]
    linked = alerts.insert_alert(conn, **fields, report_id=7)  # type: ignore[arg-type]
    shown = {a.id: a for a in alerts.list_alerts(conn)}
    assert shown[plain].report_id is None
    assert shown[linked].report_id == 7


# --- 설정의 ai 키 -------------------------------------------------------------------------


def four(model: str = "haiku", effort: Effort = Effort.LOW) -> tuple[AiChoice, ...]:
    return tuple(AiChoice(slot, model, effort) for slot in AiSlot)


def base(**kwargs: object) -> SettingsView:
    return SettingsView(("KRW-AAA",), 30, False, **kwargs)  # type: ignore[arg-type]


def ai_keys(conn: sqlite3.Connection) -> dict[str, str]:
    return dict(conn.execute("SELECT key, value FROM settings WHERE key LIKE 'ai_%'"))


def test_four_choices_are_written_as_eight_keys_and_read_in_slot_order(
    conn: sqlite3.Connection,
) -> None:
    choices = (
        AiChoice(AiSlot.SESSION, "opus", Effort.MAX),
        AiChoice(AiSlot.FINAL, "sonnet", Effort.HIGH),
        AiChoice(AiSlot.CANDLE, "haiku", Effort.LOW),
        AiChoice(AiSlot.SEARCH, "fable", Effort.XHIGH),
    )
    settings.write_settings(conn, base(ai_choices=choices))
    assert ai_keys(conn) == {
        "ai_model:search": "fable", "ai_effort:search": "xhigh",
        "ai_model:candle": "haiku", "ai_effort:candle": "low",
        "ai_model:final": "sonnet", "ai_effort:final": "high",
        "ai_model:session": "opus", "ai_effort:session": "max",
    }  # fmt: skip
    read = settings.read_settings(conn).ai_choices
    assert [c.slot for c in read] == list(AiSlot)  # `AiSlot`의 차례
    assert {c.slot: (c.model, c.effort) for c in read} == {
        AiSlot.SEARCH: ("fable", Effort.XHIGH),
        AiSlot.CANDLE: ("haiku", Effort.LOW),
        AiSlot.FINAL: ("sonnet", Effort.HIGH),
        AiSlot.SESSION: ("opus", Effort.MAX),
    }


def test_an_empty_bundle_neither_writes_nor_erases_the_ai_keys(conn: sqlite3.Connection) -> None:
    settings.write_settings(conn, base(ai_choices=four()))
    kept = ai_keys(conn)
    assert len(kept) == 8
    settings.write_settings(conn, base())  # 자료 스레드는 저장할 때마다 빈 묶음을 넘긴다
    assert ai_keys(conn) == kept
    assert settings.read_settings(conn).ai_choices == four()


@pytest.mark.parametrize("count", [1, 2, 3, 5])
def test_a_bundle_that_is_not_four_slots_is_dropped_without_an_error(
    conn: sqlite3.Connection, count: int
) -> None:
    settings.write_settings(conn, base(ai_choices=four("opus", Effort.MAX)))
    bundle = tuple(
        AiChoice(AiSlot.SEARCH if i else AiSlot.CANDLE, "haiku", Effort.LOW) for i in range(count)
    )
    new = SettingsView(("KRW-BBB",), 7, False, ai_choices=bundle)
    settings.write_settings(conn, new)  # `ValueError`를 내지 않는다
    assert settings.read_settings(conn).coins == ("KRW-BBB",)  # 다른 값은 저장한다
    assert settings.read_settings(conn).retention_days == 7
    assert settings.read_settings(conn).ai_choices == four("opus", Effort.MAX)  # 앞의 값 그대로


def test_four_choices_with_a_repeated_slot_are_dropped(conn: sqlite3.Connection) -> None:
    repeated = (*four()[:3], AiChoice(AiSlot.SEARCH, "opus", Effort.MAX))
    settings.write_settings(conn, base(ai_choices=repeated))
    assert ai_keys(conn) == {}


def test_the_eight_keys_are_written_in_the_same_transaction(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TRIGGER refuse BEFORE INSERT ON settings WHEN NEW.key = 'ai_effort:session' "
        "BEGIN SELECT RAISE(ABORT, '쓸 수 없다'); END"
    )
    with pytest.raises(StorageError):
        settings.write_settings(conn, base(ai_choices=four()))
    assert ai_keys(conn) == {}  # 앞의 키도, 같이 쓰던 다른 값도 남지 않는다
    assert settings.read_settings(conn).coins == ()
    assert not conn.in_transaction


def test_only_the_slot_with_a_wrong_value_is_dropped_on_reading(conn: sqlite3.Connection) -> None:
    settings.write_settings(conn, base(ai_choices=four()))
    conn.execute("UPDATE settings SET value = 'gpt' WHERE key = 'ai_model:candle'")
    conn.execute("UPDATE settings SET value = 'extreme' WHERE key = 'ai_effort:final'")
    conn.execute("DELETE FROM settings WHERE key = 'ai_effort:session'")
    conn.commit()
    view = settings.read_settings(conn)  # `StorageError`를 내지 않는다
    assert [c.slot for c in view.ai_choices] == [AiSlot.SEARCH]
    assert view.coins == ("KRW-AAA",)


# --- 보고서의 쓰기와 읽기 ---------------------------------------------------------------------


def new_round(conn: sqlite3.Connection, number: int, close, mode: Mode = Mode.PAPER) -> int:
    return insert_round(
        conn,
        mode=mode,
        number=number,
        trigger=RoundTrigger.BAR_CLOSE,
        unit=CandleUnit.MINUTE_60,
        close_at=close,
        next_at=close.replace(hour=close.hour + 1),
        started_at=close,
    )


def ok_record(**overrides: object) -> ReportRecord:
    fields: dict[str, object] = {
        "state": ReportState.OK,
        "base_price": Decimal("100"),
        "price_read_at": T0,
        "search_judgment": "긍정",
        "search_status": "성공",
        "candle_judgment": "오름",
        "direction": "오를 것",
        "predicted_price": Decimal("102"),
        "predicted_reason": "까닭",
        "buy_price": Decimal("99.5"),
        "sell_price": None,
        "result_text": "결과 글",
        "tokens": TokenUsage(10, 20, 30, 40),
        "model_tokens": (ModelTokens("claude-test-model", TokenUsage(10, 20, 30, 40)),),
        "tools": {AiSlot.SESSION: ["Agent"], AiSlot.SEARCH: ["Read", "WebSearch"]},
        "elapsed_report_s": 61,
        "elapsed_order_s": 2,
        "finished_at": T0,
    }
    fields.update(overrides)
    return ReportRecord(**fields)  # type: ignore[arg-type]


def ok_rows() -> list[ReportRow]:
    return [
        ReportRow("search", "근거", 1, "검색 근거"),
        ReportRow("search", "출처", 1, "제목", url="https://example.invalid/a"),
        ReportRow("candle", "근거", 1, "봉 근거"),
        ReportRow("final", "근거", 1, "최종 근거"),
        ReportRow("final", "견준 결과", 1, "앞 근거", part="봉 판별", ref_seq=2, verdict="틀림"),
        ReportRow("final", "검증 목록", 1, "항목", belief="높음", compared=4, hits=3),
    ]


def test_a_report_is_written_running_then_finished_with_its_rows(conn: sqlite3.Connection) -> None:
    round_id = new_round(conn, 1, T0)
    report_id = insert_report(conn, round_id=round_id, mode=Mode.PAPER, market=AAA, created_at=T0)
    assert reports.has_running_report(conn, round_id) is True
    (state,) = conn.execute("SELECT state FROM reports WHERE id = ?", (report_id,)).fetchone()
    assert state == "running"
    assert finish_report(conn, report_id, ok_record(), ok_rows()) is True
    assert reports.has_running_report(conn, round_id) is False
    assert finish_round(conn, round_id, state=reports.ROUND_DONE, ended_at=T1) is True
    assert conn.execute("SELECT state, ended_utc FROM rounds").fetchall() == [
        ("done", "2026-01-05T04:00:00Z")
    ]
    assert conn.execute("SELECT count(*) FROM report_rows").fetchone() == (6,)
    row = conn.execute(
        "SELECT input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,"
        " model_tokens, tools_used, base_price, predicted_price, finished_utc FROM reports"
    ).fetchone()
    assert row[:4] == (10, 20, 30, 40)
    assert json.loads(row[4]) == [
        {
            "model": "claude-test-model",
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_read_tokens": 30,
            "cache_creation_tokens": 40,
        }
    ]
    assert json.loads(row[5]) == {"session": ["Agent"], "search": ["Read", "WebSearch"]}
    assert row[6:] == ("100", "102", "2026-01-05T03:00:00Z")  # 가격은 `Decimal`의 글이다


def test_a_report_without_a_session_has_null_tools_and_tokens(conn: sqlite3.Connection) -> None:
    round_id = new_round(conn, 1, T0)
    report_id = insert_report(conn, round_id=round_id, mode=Mode.PAPER, market=AAA, created_at=T0)
    record = ReportRecord(state=ReportState.SKIPPED, failure="현재가를 읽지 못함", finished_at=T0)
    assert finish_report(conn, report_id, record) is True
    assert conn.execute(
        "SELECT tools_used, model_tokens, input_tokens FROM reports"
    ).fetchone() == (None, None, None)


def test_finishing_a_missing_report_writes_no_rows(conn: sqlite3.Connection) -> None:
    assert finish_report(conn, 99, ok_record(), ok_rows()) is False
    assert conn.execute("SELECT count(*) FROM report_rows").fetchone() == (0,)


def test_the_same_round_and_market_cannot_have_two_reports(conn: sqlite3.Connection) -> None:
    round_id = new_round(conn, 1, T0)
    insert_report(conn, round_id=round_id, mode=Mode.PAPER, market=AAA, created_at=T0)
    with pytest.raises(StorageError):
        insert_report(conn, round_id=round_id, mode=Mode.PAPER, market=AAA, created_at=T0)
    with pytest.raises(StorageError):
        new_round(conn, 1, T1)  # (mode, number)가 겹친다
    assert reports.max_round_number(conn, Mode.PAPER) == 1


def test_the_largest_round_number_of_a_mode(conn: sqlite3.Connection) -> None:
    assert reports.max_round_number(conn, Mode.PAPER) == 0
    new_round(conn, 1, T0)
    new_round(conn, 2, T1)
    assert reports.max_round_number(conn, Mode.PAPER) == 2


def test_later_rows_that_repeat_a_key_are_dropped_and_the_rest_are_kept(
    conn: sqlite3.Connection,
) -> None:
    round_id = new_round(conn, 1, T0)
    report_id = insert_report(conn, round_id=round_id, mode=Mode.PAPER, market=AAA, created_at=T0)
    rows = [
        ReportRow("search", "근거", 1, "먼저"),
        ReportRow("search", "근거", 1, "겹치는 뒤 줄"),
        ReportRow("candle", "근거", 1, "다른 부분의 같은 번호"),
        ReportRow("search", "근거", 2, "다음 번호"),
    ]
    record = ReportRecord(state=ReportState.FAILED, failure="형식 오류", finished_at=T0)
    assert finish_report(conn, report_id, record, rows) is True  # `StorageError`가 아니다
    stored = conn.execute("SELECT answer_part, seq, text FROM report_rows ORDER BY 1, 2").fetchall()
    assert stored == [
        ("candle", 1, "다른 부분의 같은 번호"),
        ("search", 1, "먼저"),
        ("search", 2, "다음 번호"),
    ]


def test_finishing_again_replaces_the_rows_of_that_report_only(conn: sqlite3.Connection) -> None:
    first = insert_report(
        conn, round_id=new_round(conn, 1, T0), mode=Mode.PAPER, market=AAA, created_at=T0
    )
    other = insert_report(
        conn, round_id=new_round(conn, 2, T1), mode=Mode.PAPER, market=AAA, created_at=T1
    )
    finish_report(conn, other, ok_record(), ok_rows())
    finish_report(conn, first, ok_record(), ok_rows())
    finish_report(conn, first, ok_record(), ok_rows()[:2])
    counts = dict(conn.execute("SELECT report_id, count(*) FROM report_rows GROUP BY 1"))
    assert counts == {first: 2, other: 6}


def test_rows_and_judgments_are_written_in_one_transaction(conn: sqlite3.Connection) -> None:
    round_id = new_round(conn, 1, T0)
    report_id = insert_report(conn, round_id=round_id, mode=Mode.PAPER, market=AAA, created_at=T0)
    bad = [*ok_rows(), ReportRow("final", "근거", 9, None)]  # type: ignore[arg-type]  # text가 NULL
    with pytest.raises(StorageError):
        finish_report(conn, report_id, ok_record(), bad)
    assert conn.execute("SELECT state FROM reports").fetchone() == (
        "running",
    )  # 판단 칸도 남지 않는다
    assert conn.execute("SELECT count(*) FROM report_rows").fetchone() == (0,)
    assert not conn.in_transaction


def test_the_view_of_an_ok_report_has_every_value(conn: sqlite3.Connection) -> None:
    previous = insert_report(
        conn, round_id=new_round(conn, 1, T0), mode=Mode.PAPER, market=AAA, created_at=T0
    )
    finish_report(conn, previous, ok_record(), ok_rows())
    round_id = new_round(conn, 2, T1)
    report_id = insert_report(conn, round_id=round_id, mode=Mode.PAPER, market=AAA, created_at=T1)
    record = ok_record(
        last_report_id=previous,
        last_predicted_price=Decimal("102"),
        last_actual_price=Decimal("101"),
        last_diff=Decimal("-0.0098"),
        last_hit="맞음",
    )
    finish_report(conn, report_id, record, ok_rows())
    view = reports.read_report(conn, report_id)
    assert view is not None
    assert (view.report_id, view.market, view.round_number, view.close_utc) == (
        report_id, AAA, 2, T1,
    )  # fmt: skip
    assert view.state is ReportState.OK
    assert view.base_price == Decimal("100")
    assert view.search_judgment is SearchJudgment.POSITIVE
    assert view.candle_judgment is CandleJudgment.UP
    assert view.direction is Direction.UP
    assert (view.predicted_price, view.buy_price, view.sell_price) == (
        Decimal("102"), Decimal("99.5"), None,
    )  # fmt: skip
    assert (view.predicted_reason, view.result_text) == ("까닭", "결과 글")
    assert view.tokens == TokenUsage(10, 20, 30, 40)
    assert view.elapsed_report_seconds == 61
    # 지난 판단: 회차 번호는 `last_report_id`의 보고서의 회차에서 읽는다.
    assert view.last_result is not None
    assert view.last_result.round_number == 1
    assert view.last_result.predicted_price == Decimal("102")
    assert view.last_result.actual_price == Decimal("101")
    assert view.last_result.diff == Decimal("-0.0098")
    assert view.last_result.hit is Verdict.HIT
    # 줄 표
    assert [(e.part, e.seq, e.text) for e in view.evidence] == [
        (AiSlot.SEARCH, 1, "검색 근거"),
        (AiSlot.CANDLE, 1, "봉 근거"),
        (AiSlot.FINAL, 1, "최종 근거"),
    ]
    assert [(s.seq, s.title, s.url) for s in view.sources] == [
        (1, "제목", "https://example.invalid/a")
    ]
    (compared,) = view.compared
    assert compared.part is AiSlot.CANDLE  # 한글 부분 이름("봉 판별")을 거꾸로 읽는다
    assert compared.seq == 2  # `ref_seq`(앞 회차 근거의 번호)
    assert (compared.text, compared.verdict) == ("앞 근거", Verdict.MISS)
    (item,) = view.checklist
    assert (item.seq, item.text, item.belief, item.compared, item.hits) == (
        1, "항목", Belief.HIGH, 4, 3,
    )  # fmt: skip
    assert AI_SLOT_TITLES[compared.part] == "봉 판별"


def test_a_view_without_a_last_report_has_no_last_result(conn: sqlite3.Connection) -> None:
    report_id = insert_report(
        conn, round_id=new_round(conn, 1, T0), mode=Mode.PAPER, market=AAA, created_at=T0
    )
    finish_report(conn, report_id, ok_record(), ok_rows())
    view = reports.read_report(conn, report_id)
    assert view is not None
    assert view.last_result is None
    assert reports.read_report(conn, 999) is None


def test_a_hit_that_cannot_be_decided_is_none_in_the_last_result(
    conn: sqlite3.Connection,
) -> None:
    previous = insert_report(
        conn, round_id=new_round(conn, 1, T0), mode=Mode.PAPER, market=AAA, created_at=T0
    )
    finish_report(conn, previous, ok_record(), ok_rows())
    report_id = insert_report(
        conn, round_id=new_round(conn, 2, T1), mode=Mode.PAPER, market=AAA, created_at=T1
    )
    record = ok_record(last_report_id=previous, last_predicted_price=Decimal("102"))
    finish_report(conn, report_id, record, ok_rows())
    view = reports.read_report(conn, report_id)
    assert view is not None
    assert view.last_result is not None
    assert (view.last_result.actual_price, view.last_result.diff, view.last_result.hit) == (
        None, None, None,
    )  # fmt: skip


FAILED_ANSWER = ReportRecord(
    state=ReportState.FAILED,
    failure="형식 오류",
    base_price=Decimal("100"),
    search_judgment="중립",
    search_status="보류",
    candle_judgment="보류",
    direction="보류",
    predicted_price="수가 아님",
    predicted_reason="까닭",
    buy_price=Decimal("1"),
    result_text="글",
    last_hit="모름",
    tokens=TokenUsage(1, 2, 3, 4),
    elapsed_report_s=30,
    finished_at=T0,
)
FAILED_ROWS = [
    ReportRow("final", "근거", 1, "글"),
    ReportRow("search", "출처", 1, "제목", url="https://example.invalid/b"),
    ReportRow("final", "견준 결과", 1, "글", part="없는 부분", ref_seq=1, verdict="보류"),
    ReportRow("final", "검증 목록", 1, "글", belief="모름", compared=0, hits=0),
]


def test_values_of_a_failed_answer_are_stored_and_read_without_breaking(
    conn: sqlite3.Connection,
) -> None:
    report_id = insert_report(
        conn, round_id=new_round(conn, 1, T0), mode=Mode.PAPER, market=AAA, created_at=T0
    )
    ok_id = insert_report(
        conn, round_id=new_round(conn, 2, T1), mode=Mode.PAPER, market=AAA, created_at=T1
    )
    finish_report(conn, report_id, FAILED_ANSWER, FAILED_ROWS)
    finish_report(conn, ok_id, ok_record(), ok_rows())
    stored = conn.execute(
        "SELECT search_judgment, search_status, candle_judgment, direction, predicted_price,"
        " last_hit FROM reports WHERE id = ?",
        (report_id,),
    ).fetchone()
    assert stored == ("중립", "보류", "보류", "보류", "수가 아님", "모름")  # 저장된 값은 그대로다
    assert conn.execute(
        "SELECT verdict, belief, part FROM report_rows WHERE report_id = ?"
        " AND kind IN ('견준 결과', '검증 목록') ORDER BY kind",
        (report_id,),
    ).fetchall() == [(None, "모름", None), ("보류", None, "없는 부분")]
    items = reports.list_round_items(conn, AAA, Mode.PAPER)  # 목록이 깨지지 않는다
    assert [(i.round_number, i.state, i.direction, i.failure) for i in items] == [
        (2, ReportState.OK, Direction.UP, None),
        (1, ReportState.FAILED, None, "형식 오류"),
    ]
    view = reports.read_report(conn, report_id)
    assert view is not None
    assert view.state is ReportState.FAILED
    assert view.failure == "형식 오류"
    # `OK`가 아니면 판단 칸, 지난 판단의 결과, 줄 표를 담지 않는다.
    assert (
        view.search_judgment, view.candle_judgment, view.direction, view.predicted_price,
        view.predicted_reason, view.buy_price, view.sell_price, view.result_text,
        view.last_result,
    ) == (None,) * 9  # fmt: skip
    assert (view.compared, view.checklist, view.evidence, view.sources) == ((), (), (), ())
    assert view.tokens == TokenUsage(1, 2, 3, 4)  # 토큰과 걸린 시간은 그대로다
    assert view.elapsed_report_seconds == 30
    assert view.base_price == Decimal("100")


def test_the_list_is_newest_first_limited_and_per_coin_and_mode(conn: sqlite3.Connection) -> None:
    for number in range(1, 6):
        close = T0.replace(hour=number)
        round_id = new_round(conn, number, close)
        for market in (AAA, "KRW-BBB"):
            report_id = insert_report(
                conn, round_id=round_id, mode=Mode.PAPER, market=market, created_at=close
            )
            finish_report(conn, report_id, ok_record(), ok_rows())
    items = reports.list_round_items(conn, AAA, Mode.PAPER)
    assert [i.round_number for i in items] == [5, 4, 3, 2, 1]
    assert [i.round_number for i in reports.list_round_items(conn, AAA, Mode.PAPER, limit=2)] == [
        5, 4,
    ]  # fmt: skip
    assert reports.list_round_items(conn, "KRW-CCC", Mode.PAPER) == ()
    assert all(i.report_id for i in items)
    assert items[0].close_utc == T0.replace(hour=5)


def test_the_default_limit_is_the_report_list_limit(conn: sqlite3.Connection) -> None:
    from coin_report_trader.core import REPORT_LIST_LIMIT

    total = REPORT_LIST_LIMIT + 5
    conn.executemany(
        'INSERT INTO rounds (id, mode, number, "trigger", unit, close_utc, next_utc,'
        " started_utc, state) VALUES (?, 'paper', ?, 'bar_close', 'minute60',"
        " '2026-01-05T03:00:00Z', '2026-01-05T04:00:00Z', '2026-01-05T03:00:00Z', 'done')",
        [(n, n) for n in range(1, total + 1)],
    )
    conn.executemany(
        "INSERT INTO reports (round_id, mode, market, state, created_utc)"
        " VALUES (?, 'paper', ?, 'ok', '2026-01-05T03:00:00Z')",
        [(n, AAA) for n in range(1, total + 1)],
    )
    conn.commit()
    items = reports.list_round_items(conn, AAA, Mode.PAPER)
    assert len(items) == REPORT_LIST_LIMIT
    assert items[0].round_number == total  # 새것이 앞


def test_the_last_judgment_is_the_latest_earlier_ok_report_of_that_coin(
    conn: sqlite3.Connection,
) -> None:
    def add(number: int, hour: int, state: ReportState, market: str = AAA) -> int:
        close = T0.replace(hour=hour)
        report_id = insert_report(
            conn, round_id=new_round(conn, number, close), mode=Mode.PAPER, market=market,
            created_at=close,
        )  # fmt: skip
        record = ok_record() if state is ReportState.OK else FAILED_ANSWER
        finish_report(conn, report_id, record, ok_rows() if state is ReportState.OK else [])
        return report_id

    first = add(1, 1, ReportState.OK)
    add(2, 2, ReportState.FAILED)  # 정상이 아니면 지난 판단이 아니다
    third = add(3, 3, ReportState.OK)
    add(4, 4, ReportState.OK, market="KRW-BBB")  # 다른 코인
    add(5, 5, ReportState.OK)  # 이번 회차(T 5시)보다 뒤는 아니지만 같은 때는 앞서지 않는다
    last = reports.read_last_judgment(
        conn, market=AAA, mode=Mode.PAPER, before_close=T0.replace(hour=5)
    )
    assert isinstance(last, LastJudgment)
    assert last.report_id == third
    assert (last.round_number, last.close_utc, last.unit) == (
        3,
        T0.replace(hour=3),
        CandleUnit.MINUTE_60,
    )
    assert (last.base_price, last.direction, last.predicted_price) == (
        Decimal("100"), Direction.UP, Decimal("102"),
    )  # fmt: skip
    assert (last.predicted_reason, last.result_text) == ("까닭", "결과 글")
    assert len(last.rows) == 6
    assert {row.kind for row in last.rows} == {kind.value for kind in RowKind}
    earlier = reports.read_last_judgment(
        conn, market=AAA, mode=Mode.PAPER, before_close=T0.replace(hour=3)
    )
    assert earlier is not None
    assert earlier.report_id == first
    assert (
        reports.read_last_judgment(
            conn, market=AAA, mode=Mode.PAPER, before_close=T0.replace(hour=1)
        )
        is None
    )
    assert (
        reports.read_last_judgment(conn, market="KRW-CCC", mode=Mode.PAPER, before_close=T2) is None
    )


def test_the_three_reads_keep_paper_and_live_apart(conn: sqlite3.Connection) -> None:
    def add(mode: Mode, number: int, close, market: str = AAA) -> int:
        round_id = new_round(conn, number, close, mode)
        report_id = insert_report(
            conn, round_id=round_id, mode=mode, market=market, created_at=close
        )
        finish_report(conn, report_id, ok_record(), ok_rows())
        return report_id

    paper = add(Mode.PAPER, 1, T0)
    live_same = add(Mode.LIVE, 1, T0)  # 같은 번호, 같은 때, 같은 코인
    live_later = add(Mode.LIVE, 7, T1)  # 더 큰 번호와 더 늦은 때
    # 그 모드의 가장 큰 회차 번호
    assert reports.max_round_number(conn, Mode.PAPER) == 1
    assert reports.max_round_number(conn, Mode.LIVE) == 7
    # 보고서 목록(그 코인, 그 모드)
    assert [i.report_id for i in reports.list_round_items(conn, AAA, Mode.PAPER)] == [paper]
    assert [i.report_id for i in reports.list_round_items(conn, AAA, Mode.LIVE)] == [
        live_later,
        live_same,
    ]
    # 지난 판단(그 코인, 그 모드): 다른 모드의 더 늦은 `OK`를 고르지 않는다.
    last_paper = reports.read_last_judgment(conn, market=AAA, mode=Mode.PAPER, before_close=T2)
    assert last_paper is not None
    assert last_paper.report_id == paper
    last_live = reports.read_last_judgment(conn, market=AAA, mode=Mode.LIVE, before_close=T2)
    assert last_live is not None
    assert last_live.report_id == live_later


def test_unfinished_rounds_and_reports_become_interrupted(conn: sqlite3.Connection) -> None:
    done_round = new_round(conn, 1, T0)
    done = insert_report(conn, round_id=done_round, mode=Mode.PAPER, market=AAA, created_at=T0)
    finish_report(conn, done, ok_record(), ok_rows())
    finish_round(conn, done_round, state=reports.ROUND_DONE, ended_at=T0)
    running_round = new_round(conn, 2, T1)
    running = insert_report(
        conn, round_id=running_round, mode=Mode.PAPER, market=AAA, created_at=T1
    )
    assert reports.interrupt_unfinished(conn, now=T2) == (1, 1)
    assert conn.execute("SELECT state FROM reports WHERE id = ?", (running,)).fetchone() == (
        "interrupted",
    )
    assert conn.execute(
        "SELECT state, ended_utc FROM rounds WHERE id = ?", (running_round,)
    ).fetchone() == ("interrupted", "2026-01-05T05:00:00Z")
    assert conn.execute("SELECT state FROM reports WHERE id = ?", (done,)).fetchone() == ("ok",)
    assert reports.interrupt_unfinished(conn, now=T2) == (0, 0)  # 다시 해도 같다
    assert reports.has_running_report(conn, running_round) is False
    item = reports.list_round_items(conn, AAA, Mode.PAPER)[0]
    assert item.state is ReportState.INTERRUPTED


def test_a_read_of_a_damaged_value_is_a_storage_error(conn: sqlite3.Connection) -> None:
    report_id = insert_report(
        conn, round_id=new_round(conn, 1, T0), mode=Mode.PAPER, market=AAA, created_at=T0
    )
    finish_report(conn, report_id, ok_record(), ok_rows())
    conn.execute("UPDATE reports SET state = 'weird' WHERE id = ?", (report_id,))
    conn.commit()
    with pytest.raises(StorageError):
        reports.list_round_items(conn, AAA, Mode.PAPER)
    with pytest.raises(StorageError):
        reports.read_report(conn, report_id)


def test_the_candle_table_is_untouched_by_the_new_writes(conn: sqlite3.Connection) -> None:
    assert candles.read_candles(conn, AAA, CandleUnit.DAY) == ()
