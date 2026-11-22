"""`"memory"` 줄(M1 명세 3.9): 메모리에 둔 알림의 줄을 쓰고, 고치고, 알림으로 옮긴다."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from coin_report_trader.app import startup_errors as se
from coin_report_trader.app.memory_alerts import MemoryAlert
from coin_report_trader.core import AlertCategory, AlertDetail, Mode, StorageError
from coin_report_trader.storage import alerts
from coin_report_trader.storage.database import migrate, open_database
from coin_report_trader.storage.states import DiscordState

WHEN = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
ERROR, TRADE = AlertCategory.ERROR, AlertCategory.TRADE


def memory_alert(
    identifier: int = -1, *, when: datetime = WHEN, seen: bool = False,
    state: DiscordState | None = None,
) -> MemoryAlert:  # fmt: skip
    detail = AlertDetail(
        "알림 저장", None, "DB 오류", "알림 저장에 실패했습니다: X", "다음에 옮깁니다."
    )
    return MemoryAlert(
        identifier, when, Mode.PAPER, ERROR, "A-29", None, "저장하지 못했습니다(DB 오류).",
        detail, seen, state,
    )  # fmt: skip


@pytest.fixture
def conn(tmp_path: Path) -> object:
    connection = open_database(tmp_path / "app.db")
    migrate(connection)
    yield connection
    connection.close()


def read(folder: Path) -> list[dict[str, object]]:
    text = (folder / se.FILE_NAME).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line]


def test_a_memory_line_has_the_eleven_keys_and_the_three_more() -> None:
    line = se.memory_line(memory_alert(-3, seen=True, state=DiscordState.FAILED))
    assert list(line) == [*se._KEYS, "id", "seen", "discord_state", "source"]
    assert line["kind"] == "memory"
    assert (line["id"], line["seen"], line["discord_state"]) == (-3, True, "failed")
    assert (line["stage"], line["error_name"]) == ("알림 저장", "DB 오류")
    assert line["created_utc"] == "2026-03-04T05:06:07Z"
    assert se.memory_line(memory_alert())["discord_state"] is None  # 아직 결과가 없다


def test_the_texts_of_a_memory_line_are_redacted() -> None:
    url = "https://" + "discord.com/api/webhooks/" + "1" * 18 + "/" + "Tok_" * 16
    alert = memory_alert()
    alert.detail = AlertDetail("알림 저장", None, "DB 오류", f"{url} 글", "다음에 옮깁니다.")
    assert "Tok_" not in json.dumps(se.memory_line(alert))


def test_update_changes_only_its_own_line_and_keeps_every_other_line(tmp_path: Path) -> None:
    first, second = memory_alert(-1), memory_alert(-2, when=datetime(2026, 3, 5, tzinfo=UTC))
    (tmp_path / se.FILE_NAME).write_text(
        "이것은 JSON이 아니다\n"
        + "".join(
            json.dumps(se.memory_line(a), ensure_ascii=False) + "\n" for a in (first, second)
        ),
        encoding="utf-8",
    )
    second.seen, second.discord_state = True, DiscordState.SENT
    assert se.update_memory_line(tmp_path, second)
    text = (tmp_path / se.FILE_NAME).read_text(encoding="utf-8")
    assert text.splitlines()[0] == "이것은 JSON이 아니다"
    lines = [json.loads(line) for line in text.splitlines()[1:]]
    assert [(x["id"], x["seen"], x["discord_state"]) for x in lines] == [
        (-1, False, None), (-2, True, "sent"),
    ]  # fmt: skip
    assert not list(tmp_path.glob("*.tmp"))


def test_remove_drops_only_its_own_line_and_a_leftover_line_with_the_same_number_stays(
    tmp_path: Path,
) -> None:
    old_run = memory_alert(-1, when=datetime(2026, 1, 1, tzinfo=UTC))  # 앞 실행에서 남은 줄
    this_run = memory_alert(-1)
    for alert in (old_run, this_run):
        assert se.append_line(tmp_path, se.memory_line(alert))
    assert se.remove_memory_line(tmp_path, this_run)
    (left,) = read(tmp_path)
    assert left["created_utc"] == "2026-01-01T00:00:00Z"


def test_a_file_that_cannot_be_read_or_written_is_not_an_error(tmp_path: Path) -> None:
    assert se.update_memory_line(tmp_path, memory_alert()) is False  # 파일이 없다
    assert se.remove_memory_line(tmp_path, memory_alert()) is False
    (tmp_path / se.FILE_NAME).mkdir()
    assert se.update_memory_line(tmp_path, memory_alert()) is False


def settle(category: AlertCategory) -> DiscordState:
    return DiscordState.FAILED if category is ERROR else DiscordState.NO_ADDRESS


def move(conn: object, folder: Path) -> int:
    return se.move_to_alerts(conn, folder, settle)  # type: ignore[arg-type]


def test_memory_lines_move_with_the_written_mark_and_result_and_time(
    tmp_path: Path, conn: object
) -> None:
    for alert in (
        memory_alert(-1, seen=True, state=DiscordState.SENT),
        memory_alert(-2, state=DiscordState.NO_ADDRESS, when=datetime(2026, 3, 6, tzinfo=UTC)),
    ):
        se.append_line(tmp_path, se.memory_line(alert))
    assert move(conn, tmp_path) == 2
    rows = conn.execute(  # type: ignore[attr-defined]
        "SELECT created_utc, seen, discord_state FROM alerts ORDER BY id"
    ).fetchall()
    assert rows == [("2026-03-04T05:06:07Z", 1, "sent"), ("2026-03-06T00:00:00Z", 0, "no_address")]
    assert read(tmp_path) == []


@pytest.mark.parametrize("recorded", [None, DiscordState.NOT_SENT])
def test_a_memory_line_without_a_result_is_settled_like_a_leftover_not_sent(
    tmp_path: Path, conn: object, recorded: DiscordState | None
) -> None:
    saved = memory_alert(-1, state=recorded)
    other = memory_alert(-2, state=recorded)
    other.category = TRADE
    for alert in (saved, other):
        se.append_line(tmp_path, se.memory_line(alert))
    assert move(conn, tmp_path) == 2
    rows = conn.execute("SELECT category, discord_state FROM alerts ORDER BY id").fetchall()  # type: ignore[attr-defined]
    assert rows == [("error", "failed"), ("trade", "no_address")]


def test_a_memory_line_is_kept_in_the_file_when_nobody_settles_it(
    tmp_path: Path, conn: object
) -> None:
    se.append_line(tmp_path, se.memory_line(memory_alert()))
    assert se.move_to_alerts(conn, tmp_path) == 0  # type: ignore[arg-type]
    assert len(read(tmp_path)) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [("id", 3), ("id", "x"), ("id", True), ("seen", "no"), ("discord_state", "엉뚱한 값")],
)
def test_a_memory_line_with_a_wrong_field_is_skipped_and_the_rest_still_move(
    tmp_path: Path, conn: object, field: str, value: object
) -> None:
    bad = se.memory_line(memory_alert(-1))
    bad[field] = value
    good = se.memory_line(memory_alert(-2))
    for line in (bad, good):
        se.append_line(tmp_path, line)
    assert move(conn, tmp_path) == 1
    assert len(alerts.list_alerts(conn)) == 1  # type: ignore[arg-type]
    assert read(tmp_path) == []  # 읽지 못하는 줄은 건너뛰고 로그에 남긴다


def test_a_memory_line_that_cannot_be_saved_stays_in_the_file(
    tmp_path: Path, conn: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> int:
        raise StorageError("알림 저장에 실패했습니다: OperationalError")

    monkeypatch.setattr(se, "insert_alert", refuse)
    se.append_line(tmp_path, se.memory_line(memory_alert()))
    assert move(conn, tmp_path) == 0
    assert len(read(tmp_path)) == 1


def test_startup_lines_keep_local_only_even_when_a_settle_function_is_given(
    tmp_path: Path, conn: object
) -> None:
    se.append_line(tmp_path, se.startup_line(StorageError("DB 열기에 실패했습니다: X"), WHEN))
    assert move(conn, tmp_path) == 1
    (state,) = conn.execute("SELECT discord_state FROM alerts").fetchone()  # type: ignore[attr-defined]
    assert state == "local_only"
