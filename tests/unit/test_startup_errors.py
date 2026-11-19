"""`logs/startup-errors.jsonl`(M1 명세 3.9): `"startup"` 줄을 쓰고 알림으로 옮긴다."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from coin_report_trader.app import startup_errors as se
from coin_report_trader.core import AlertCategory, StorageError
from coin_report_trader.storage import alerts
from coin_report_trader.storage.database import migrate, open_database
from coin_report_trader.storage.states import DiscordState

WHEN = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
KEYS = [
    "kind", "created_utc", "mode", "category", "code", "coin", "text", "stage", "error_name",
    "content", "after",
]  # fmt: skip


def webhook() -> str:
    return "https://" + "discord.com/api/webhooks/" + "1" * 18 + "/" + "Tok_" * 16


def user_dir(name: str) -> str:
    return "/" + "home" + "/" + name


def test_a_storage_error_is_an_a29_line_of_the_startup_stage() -> None:
    line = se.startup_line(StorageError("DB 열기에 실패했습니다: OperationalError"), WHEN)
    assert list(line) == KEYS
    assert line == {
        "kind": "startup",
        "created_utc": "2026-03-04T05:06:07Z",
        "mode": "paper",
        "category": "error",
        "code": "A-29",
        "coin": None,
        "text": "저장하지 못했습니다(DB 오류).",
        "stage": "켜기",
        "error_name": "DB 오류",
        "content": "DB 열기에 실패했습니다: OperationalError",
        "after": "창을 띄우지 못하고 끝냈습니다. 다음에 켤 때 다시 시도합니다.",
    }


def test_any_other_exception_is_an_a30_line_named_by_its_kind() -> None:
    line = se.startup_line(RuntimeError("창을 만들지 못했다"), WHEN)
    assert (line["code"], line["category"], line["error_name"]) == ("A-30", "other", "RuntimeError")
    assert line["text"] == "예상하지 못한 오류가 났습니다(RuntimeError)."
    assert line["after"] == "그 일을 그만두고 다음 일을 계속합니다."
    assert (line["stage"], line["coin"]) == ("켜기", None)


def test_the_texts_of_a_line_are_redacted() -> None:
    line = se.startup_line(RuntimeError(f"{webhook()} 와 {user_dir('u1')}/data/app.db"), WHEN)
    assert line["content"] == "(가려서 보임) 와 ~/data/app.db"
    assert "Tok_" not in json.dumps(line)


def test_lines_are_appended_as_utf8_json(tmp_path: Path) -> None:
    assert se.append_line(tmp_path, se.startup_line(StorageError("가"), WHEN))
    assert se.append_line(tmp_path, se.startup_line(RuntimeError("나"), WHEN))
    raw = (tmp_path / se.FILE_NAME).read_bytes()
    assert raw.endswith(b"\n")
    assert b"\r" not in raw
    lines = raw.decode("utf-8").splitlines()
    assert [json.loads(line)["code"] for line in lines] == ["A-29", "A-30"]
    assert "저장하지 못했습니다" in lines[0]  # `\uXXXX`로 바꿔 쓰지 않는다


def test_a_file_that_cannot_be_written_is_not_an_error(tmp_path: Path) -> None:
    assert (
        se.append_line(tmp_path / "missing" / "folder", se.startup_line(StorageError("x"), WHEN))
        is False
    )
    (tmp_path / se.FILE_NAME).mkdir()  # 파일이 있어야 할 자리가 폴더다
    assert se.append_line(tmp_path, se.startup_line(StorageError("x"), WHEN)) is False


@pytest.fixture
def conn(tmp_path: Path) -> object:
    connection = open_database(tmp_path / "app.db")
    migrate(connection)
    yield connection
    connection.close()


def write_lines(folder: Path, *lines: str) -> Path:
    path = folder / se.FILE_NAME
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return path


def good(**changes: object) -> str:
    line = se.startup_line(StorageError("DB 열기에 실패했습니다: OperationalError"), WHEN)
    line.update(changes)
    return json.dumps(line, ensure_ascii=False)


def test_startup_lines_become_local_only_alerts_with_the_written_time(
    tmp_path: Path, conn: object
) -> None:
    path = write_lines(
        tmp_path, good(), good(created_utc="2026-01-01T00:00:00Z", code="A-30", category="other")
    )
    assert se.move_to_alerts(conn, tmp_path) == 2  # type: ignore[arg-type]
    shown = list(alerts.list_alerts(conn))  # type: ignore[arg-type]  # 새것이 앞
    assert [a.created_at for a in shown] == [WHEN, datetime(2026, 1, 1, tzinfo=UTC)]
    assert [(a.code, a.category) for a in shown] == [
        ("A-29", AlertCategory.ERROR), ("A-30", AlertCategory.OTHER),
    ]  # fmt: skip
    first = shown[0]
    assert (first.coin, first.seen, first.discord_failed, first.mode.value) == (
        None,
        False,
        False,
        "paper",
    )
    assert first.text == "저장하지 못했습니다(DB 오류)."
    assert first.detail is not None
    assert (first.detail.stage, first.detail.error_name) == ("켜기", "DB 오류")
    assert path.read_text(encoding="utf-8") == ""
    states = [r[0] for r in conn.execute("SELECT discord_state FROM alerts")]  # type: ignore[attr-defined]
    assert states == [DiscordState.LOCAL_ONLY.value] * 2


def test_unreadable_lines_are_skipped_and_memory_lines_stay(tmp_path: Path, conn: object) -> None:
    memory = good(kind="memory", id=-1, seen=False, discord_state=None)
    path = write_lines(
        tmp_path,
        "",
        "이것은 JSON이 아니다",
        "[1, 2]",
        '{"kind": "startup"}',
        good(created_utc="어제"),
        good(mode="live-ish"),
        good(category="없는분류"),
        good(text=3),
        good(stage=None),  # 자세한 글이 일부만 있다
        memory,
        good(),
    )
    assert se.move_to_alerts(conn, tmp_path) == 1  # type: ignore[arg-type]
    assert path.read_text(encoding="utf-8") == memory + "\n"
    assert len(alerts.list_alerts(conn)) == 1  # type: ignore[arg-type]


def test_a_line_that_cannot_be_saved_stays_in_the_file(
    tmp_path: Path, conn: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> int:
        raise StorageError("알림 저장에 실패했습니다: OperationalError")

    monkeypatch.setattr(se, "insert_alert", refuse)
    path = write_lines(tmp_path, good())
    assert se.move_to_alerts(conn, tmp_path) == 0  # type: ignore[arg-type]
    assert path.read_text(encoding="utf-8") == good() + "\n"


def test_no_file_or_an_unreadable_file_changes_nothing(tmp_path: Path, conn: object) -> None:
    assert se.move_to_alerts(conn, tmp_path) == 0  # type: ignore[arg-type]
    assert not (tmp_path / se.FILE_NAME).exists()
    (tmp_path / se.FILE_NAME).mkdir()
    assert se.move_to_alerts(conn, tmp_path) == 0  # type: ignore[arg-type]
    (tmp_path / se.FILE_NAME).rmdir()
    (tmp_path / se.FILE_NAME).write_bytes(b"\xff\xfe\x00 not utf-8")
    assert se.move_to_alerts(conn, tmp_path) == 0  # type: ignore[arg-type]
    assert alerts.list_alerts(conn) == ()  # type: ignore[arg-type]
