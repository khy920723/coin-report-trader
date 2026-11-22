"""문제 보고 표(M2 명세 3.11.1): 가린 내용, 일어난 자리, 같은 문제의 표시, 파일, 쓰는 때.

값은 모두 만든 값이다. 웹후크 주소와 사용자 폴더 꼴의 경로는 실행 중에 이어 붙여 만든다.
"""

import csv
import io
import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fakes.ai import FakeAiRunner
from fakes.clock import DEFAULT_START, FakeClock
from fakes.quote import FakeQuoteSource, make_candles

from coin_report_trader import __version__
from coin_report_trader.app import problem_report as pr
from coin_report_trader.app import report_round, startup_errors
from coin_report_trader.app.data_thread import DataWorker
from coin_report_trader.core import (
    AiChoice,
    AiSlot,
    CandleUnit,
    Effort,
    Market,
    QuoteError,
    SettingsView,
    StorageError,
)
from coin_report_trader.core.texts import ERROR_NETWORK
from coin_report_trader.storage.database import migrate, open_database
from coin_report_trader.storage.settings import write_settings

KST_WHEN = datetime(2026, 11, 21, 18, 15, 20, tzinfo=UTC)  # 한국 2026-11-22 03:15:20
SAFETY = 10.0
AAA = "KRW-AAA"
HOUR = CandleUnit.MINUTE_60
THIS_HOUR = datetime(2026, 1, 5, 3, tzinfo=UTC)
FOUR = tuple(AiChoice(slot, "haiku", Effort.LOW) for slot in AiSlot)


def webhook() -> str:
    return "https://" + "discord.com/api/webhooks/" + "1" * 18 + "/" + "a" * 64


def user_dir(name: str) -> str:
    return "/" + "home" + "/" + name


def row(**changes: Any) -> pr.ProblemRow:
    base: dict[str, Any] = {
        "alert_id": 41,
        "occurred_kst": pr.kst_text(KST_WHEN),
        "app_version": "v0.2.0",
        "mode": "paper",
        "category": "기타",
        "code": "A-30",
        "stage": "AI 보고서",
        "error_name": "RuntimeError",
        "source": "app/ai_threads.py:57",
        "coin": AAA,
        "round_number": 7,
        "repeat_24h": 1,
        "fingerprint": "13914e95324f",
        "message": "예상하지 못한 오류가 났습니다(RuntimeError). · boom",
    }
    base.update(changes)
    return pr.ProblemRow(**base)


def read_rows(path: Path) -> list[list[str]]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith(pr.BOM)
    return list(csv.reader(io.StringIO(text[1:])))


# --- 값과 글 -------------------------------------------------------------------------------------


def test_the_columns_and_the_row_fields_follow_the_spec() -> None:
    assert pr.PROBLEM_COLUMNS == (
        "alert_id", "occurred_kst", "app_version", "mode", "category", "code", "stage",
        "error_name", "source", "coin", "round_number", "repeat_24h", "fingerprint", "message",
    )  # fmt: skip
    assert tuple(pr.ProblemRow.__dataclass_fields__) == pr.PROBLEM_COLUMNS
    assert row(alert_id=None, round_number=None).cells()[0] == ""
    assert row(alert_id=None, round_number=None).cells()[10] == ""
    assert row().cells()[0] == "41"


def test_the_kst_text_is_to_the_second_with_the_offset() -> None:
    assert pr.kst_text(KST_WHEN) == "2026-11-22T03:15:20+09:00"


def test_the_fingerprint_is_the_first_twelve_hex_of_the_sha256_of_the_four_joined_by_a_bar() -> (
    None
):
    assert pr.problem_fingerprint("A-30", "AI 보고서", "RuntimeError", "app/ai_threads.py:57") == (
        "13914e95324f"  # 명세의 예
    )
    assert pr.problem_fingerprint("A-1", "", "", "") != pr.problem_fingerprint("A-1", "", "", "x")
    assert len(pr.problem_fingerprint("A-1", "봉 받기", "네트워크 끊김", "app.data_thread")) == 12


def test_the_source_is_the_last_app_place_of_the_traceback_or_the_module_name() -> None:
    def boom() -> None:
        raise RuntimeError("x")

    with pytest.raises(RuntimeError) as outside:
        boom()
    assert pr.problem_source(outside.value, "coin_report_trader.app.data_thread") == (
        "app.data_thread"  # 시험 파일은 앱 밖이다
    )
    assert pr.problem_source(None, "coin_report_trader.app.data_thread") == "app.data_thread"
    assert pr.problem_source(None, "app.report_round") == "app.report_round"
    with pytest.raises(StorageError) as inside:
        open_database(Path("/nonexistent-folder-for-test/x/y.db"))
    place = pr.problem_source(inside.value, "app.data_thread")
    assert place.startswith("storage/database.py:")
    assert place.split(":")[1].isdigit()
    assert "\\" not in place
    assert not Path(place.split(":")[0]).is_absolute()


def test_the_message_joins_text_and_content_and_masks_in_four_steps() -> None:
    assert pr.problem_message("글", None) == "글"
    assert pr.problem_message("글", "") == "글"
    assert pr.problem_message("글", "내용") == "글 · 내용"
    secret = "Tok" * 5
    out = pr.problem_message("글", f"{webhook()} {secret} 끝", secrets=[secret])
    assert webhook() not in out
    assert secret not in out
    assert out.startswith("글 · (가려서 보임)")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("서버 10.0.0.1 응답", "서버 (가려서 보임) 응답"),
        ("서버 255.255.255.255 응답", "서버 (가려서 보임) 응답"),
        ("버전 1.2.3 그리고 1.2.3.4.5", "버전 1.2.3 그리고 1.2.3.4.5"),
        ("서버 256.1.1.1 응답", "서버 256.1.1.1 응답"),
        ("주소 fe80::1 끝", "주소 (가려서 보임) 끝"),
        ("주소 2001:db8:0:0:0:0:0:1 끝", "주소 (가려서 보임) 끝"),
        ("시각 10:30:00 끝", "시각 10:30:00 끝"),
        ("경로 C:\\Temp\\a.txt 끝", "경로 (경로) 끝"),
        ("경로 C:/Temp/a.txt 끝", "경로 (경로) 끝"),
        ("경로 \\\\server\\share\\a 끝", "경로 (경로) 끝"),
        ("경로 /opt/app/a.txt 끝", "경로 (경로) 끝"),
        ("주소 https://example.com/a/b 끝", "주소 https://example.com/a/b 끝"),
        ("나누기 a/b 끝", "나누기 a/b 끝"),
        ("한 칸 /only 끝", "한 칸 /only 끝"),
        ("줄\r\n바꿈\n과\t탭\r끝", "줄 바꿈 과 탭 끝"),
    ],
)
def test_the_message_masks_addresses_and_paths_and_flattens_lines(raw: str, expected: str) -> None:
    assert pr.problem_message(raw, None) == expected


def test_the_message_uses_the_user_folder_mask_first_and_is_cut_at_500_characters() -> None:
    out = pr.problem_message("글", f"{user_dir('u1')}/data/app.db")
    assert "u1" not in out
    assert (
        out == "글 · ~(경로)"
    )  # 사용자 이름은 `redact`가, 남은 `/data/app.db`는 경로 규칙이 가린다
    long = pr.problem_message("가" * 800, None)
    assert len(long) == 500


# --- 파일 ----------------------------------------------------------------------------------------


def test_append_writes_a_bom_and_header_first_then_crlf_lines(tmp_path: Path) -> None:
    report = pr.ProblemReport(tmp_path)
    report.append(row())
    report.append(row(alert_id=None, message='쉼표, 따옴표 "x"'))
    path = tmp_path / "problems" / "problems-2026-11.csv"
    raw = path.read_bytes()
    assert raw.startswith(pr.BOM.encode("utf-8"))
    assert raw.count(b"\r\n") == 3
    assert b"\n" not in raw.replace(b"\r\n", b"")
    rows = read_rows(path)
    assert tuple(rows[0]) == pr.PROBLEM_COLUMNS
    assert rows[1][0] == "41"
    assert rows[2][0] == ""
    assert rows[2][-1] == '쉼표, 따옴표 "x"'
    assert report.path_for(KST_WHEN) == path


def test_the_month_of_the_file_is_the_korean_month_of_the_row(tmp_path: Path) -> None:
    late = datetime(2026, 11, 30, 15, 30, tzinfo=UTC)  # 한국 12월 1일 00:30
    report = pr.ProblemReport(tmp_path)
    report.append(row(occurred_kst=pr.kst_text(late)))
    assert (tmp_path / "problems" / "problems-2026-12.csv").exists()
    assert not (tmp_path / "problems" / "problems-2026-11.csv").exists()


def test_a_file_whose_first_line_is_not_the_header_is_kept_as_bad_and_started_again(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "problems"
    folder.mkdir()
    path = folder / "problems-2026-11.csv"
    path.write_text("다른 머리줄\r\n1,2\r\n", encoding="utf-8")
    (folder / "problems-2026-11.csv.bad").write_text("옛것", encoding="utf-8")
    pr.ProblemReport(tmp_path).append(row())
    assert (folder / "problems-2026-11.csv.bad").read_text(encoding="utf-8").startswith("다른")
    assert len(read_rows(path)) == 2
    pr.ProblemReport(tmp_path).append(row(alert_id=42))
    assert len(read_rows(path)) == 3
    assert [f.name for f in folder.iterdir()].count("problems-2026-11.csv.bad") == 1


def test_a_failure_to_write_is_an_os_error(tmp_path: Path) -> None:
    blocker = tmp_path / "problems"
    blocker.write_text("파일이라 폴더를 만들 수 없다", encoding="utf-8")
    with pytest.raises(OSError):  # noqa: PT011
        pr.ProblemReport(tmp_path).append(row())


def test_prune_keeps_the_last_three_korean_months_and_touches_nothing_else(tmp_path: Path) -> None:
    folder = tmp_path / "problems"
    folder.mkdir()
    names = [
        "problems-2026-12.csv", "problems-2026-11.csv", "problems-2026-10.csv",
        "problems-2026-09.csv", "problems-2026-08.csv.bad", "problems-2026-10.csv.bad",
        "problems-2025-12.csv", "memo.txt", "problems-2026-9.csv", "problems-2026-09.csv.old",
    ]  # fmt: skip
    for name in names:
        (folder / name).write_text("x", encoding="utf-8")
    now = datetime(2026, 12, 4, 20, 0, tzinfo=UTC)  # 한국 2026-12-05 05:00
    pr.ProblemReport(tmp_path).prune(now)
    left = sorted(f.name for f in folder.iterdir())
    assert left == sorted(
        [
            "problems-2026-12.csv",
            "problems-2026-11.csv",
            "problems-2026-10.csv",
            "problems-2026-10.csv.bad",
            "memo.txt",
            "problems-2026-9.csv",
            "problems-2026-09.csv.old",
        ]
    )
    pr.ProblemReport(tmp_path / "none").prune(now)  # 폴더가 없어도 된다


def test_prune_across_the_new_year(tmp_path: Path) -> None:
    folder = tmp_path / "problems"
    folder.mkdir()
    for name in ("problems-2026-11.csv", "problems-2026-12.csv", "problems-2027-01.csv"):
        (folder / name).write_text("x", encoding="utf-8")
    pr.ProblemReport(tmp_path).prune(datetime(2027, 1, 10, tzinfo=UTC))
    assert sorted(f.name for f in folder.iterdir()) == [
        "problems-2026-11.csv", "problems-2026-12.csv", "problems-2027-01.csv",
    ]  # fmt: skip
    pr.ProblemReport(tmp_path).prune(datetime(2027, 2, 10, tzinfo=UTC))
    assert sorted(f.name for f in folder.iterdir()) == [
        "problems-2026-12.csv",
        "problems-2027-01.csv",
    ]


def test_count_recent_counts_same_fingerprint_lines_in_the_last_24_hours(tmp_path: Path) -> None:
    report = pr.ProblemReport(tmp_path)
    base = datetime(2026, 12, 1, 3, 0, tzinfo=UTC)  # 한국 12-01 12:00

    def put(moment: datetime, fingerprint: str = "f" * 12) -> None:
        report.append(row(occurred_kst=pr.kst_text(moment), fingerprint=fingerprint))

    put(base - timedelta(hours=25))  # 24시간보다 앞
    put(base - timedelta(hours=23))
    put(base - timedelta(hours=1), fingerprint="o" * 12)  # 다른 문제
    put(base - timedelta(hours=1))
    assert report.count_recent("f" * 12, base) == 2  # 이 줄은 세지 않는다
    assert report.count_recent("f" * 12, base - timedelta(hours=24)) == 1  # 25시간 전의 줄뿐
    assert report.count_recent("f" * 12, base + timedelta(hours=1)) == 1  # 23시간 전의 줄은 밖이다
    assert report.count_recent("z" * 12, base) == 0


def test_count_recent_reads_the_previous_month_file(tmp_path: Path) -> None:
    report = pr.ProblemReport(tmp_path)
    before = datetime(2026, 11, 30, 14, 0, tzinfo=UTC)  # 한국 11-30 23:00
    report.append(row(occurred_kst=pr.kst_text(before), fingerprint="f" * 12))
    after = datetime(2026, 11, 30, 16, 0, tzinfo=UTC)  # 한국 12-01 01:00
    assert report.count_recent("f" * 12, after) == 1


def test_count_recent_skips_unreadable_files_and_rows(tmp_path: Path) -> None:
    folder = tmp_path / "problems"
    folder.mkdir()
    (folder / "problems-2026-11.csv").write_bytes(b"\xff\xfe\x00 not utf8")
    assert pr.ProblemReport(tmp_path).count_recent("f" * 12, KST_WHEN) == 0


# --- 자료 스레드가 쓰는 때 -------------------------------------------------------------


class Rig:
    def __init__(self, tmp_path: Path, runner: FakeAiRunner | None = None) -> None:
        self.root = tmp_path
        db = tmp_path / "app.db"
        logs = tmp_path / "logs"
        logs.mkdir()
        conn = open_database(db)
        try:
            migrate(conn)
            view = SettingsView(
                (AAA,), 30, True, candle_unit=HOUR, candle_count=100, ai_choices=FOUR
            )
            write_settings(conn, view)
        finally:
            conn.close()
        self.clock = FakeClock()
        self.quote = FakeQuoteSource(markets=[Market(AAA, "이름", False)], clock=self.clock)
        self.quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 200, last_start=THIS_HOUR))
        self.quote.set_prices({AAA: Decimal("100")})
        self.runner = runner or FakeAiRunner()
        self.logs = logs
        self.db = db
        self.worker = DataWorker(
            database_file=db,
            logs_dir=logs,
            clock=self.clock,
            quote=self.quote,
            sink=lambda update: None,
            ai=self.runner,
            sessions_root=tmp_path / "ai-sessions",
        )

    def start(self) -> None:
        self.worker.start()
        assert self.worker.wait_idle(SAFETY)

    def round(self) -> None:
        due = self.worker._next_due
        assert due is not None
        self.clock.advance((due - self.clock.now()).total_seconds())
        assert self.worker.wait_idle(SAFETY)
        deadline = time.monotonic() + SAFETY
        while self.worker._pending and time.monotonic() < deadline:
            time.sleep(0.005)
        assert self.worker.wait_idle(SAFETY)

    def table(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for path in sorted((self.root / "problems").glob("problems-*.csv")):
            text = path.read_text(encoding="utf-8")[1:]
            rows += list(csv.DictReader(io.StringIO(text)))
        return rows

    def stop(self) -> None:
        self.worker.stop()
        self.worker.join(SAFETY)


@pytest.fixture
def rig(tmp_path: Path) -> Any:
    made: list[Rig] = []

    def factory(runner: FakeAiRunner | None = None) -> Rig:
        item = Rig(tmp_path, runner)
        made.append(item)
        return item

    yield factory
    for item in made:
        item.stop()


def test_an_error_alert_gets_one_line_with_the_table_id_and_the_spec_columns(rig: Any) -> None:
    item = rig()
    item.start()
    item.quote.script(QuoteError(ERROR_NETWORK), kind="price", market=AAA)
    item.round()
    (line,) = item.table()
    assert tuple(line) == pr.PROBLEM_COLUMNS
    assert (line["code"], line["category"], line["mode"]) == ("A-2", "오류", "paper")
    assert (line["stage"], line["error_name"], line["coin"]) == ("현재가 읽기", ERROR_NETWORK, AAA)
    assert line["app_version"] == f"v{__version__}"
    assert line["round_number"] == "1"  # 회차를 쓴 뒤 그 회차 안에서 만든 알림
    assert line["repeat_24h"] == "1"
    assert line["source"] == "app.report_round"
    assert line["fingerprint"] == pr.problem_fingerprint(
        "A-2", "현재가 읽기", ERROR_NETWORK, "app.report_round"
    )
    conn = open_database(item.db)
    try:
        ids = [r[0] for r in conn.execute("SELECT id FROM alerts WHERE code = 'A-2'")]
    finally:
        conn.close()
    assert line["alert_id"] == str(ids[0])
    assert line["occurred_kst"].endswith("+09:00")


def test_a_repeat_that_is_not_made_still_writes_a_line_without_an_id_and_counts_up(
    rig: Any,
) -> None:
    item = rig()
    item.start()
    item.quote.script(*[QuoteError(ERROR_NETWORK)] * 3, kind="price", market=AAA)
    item.round()
    item.round()  # 같은 코인, 같은 오류 이름이라 알림을 만들지 않는다
    first, second = item.table()
    assert (first["alert_id"], second["alert_id"]) == (first["alert_id"], "")
    assert first["alert_id"] != ""
    assert (first["repeat_24h"], second["repeat_24h"]) == ("1", "2")
    assert first["fingerprint"] == second["fingerprint"]
    assert first["message"] == second["message"]


def test_the_report_alert_and_the_non_error_alerts_are_not_written(rig: Any) -> None:
    item = rig()
    item.start()
    item.round()  # 정상 회차: A-26뿐
    assert item.table() == []
    assert not (item.root / "problems").exists() or list((item.root / "problems").iterdir()) == []


def test_an_unexpected_error_line_carries_the_app_place_and_hides_the_answer_text(
    rig: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = rig()
    item.start()

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("답에서 온 글 " + webhook())

    monkeypatch.setattr(report_round.ai_answers, "judge", broken)
    item.round()
    (line,) = item.table()
    assert (line["code"], line["category"], line["error_name"]) == ("A-30", "기타", "ValueError")
    assert line["source"].startswith("app/report_round.py:")
    assert line["round_number"] == "1"
    assert "답에서 온 글" not in line["message"]
    assert webhook() not in json.dumps(line)


def test_a_clean_run_start_prunes_old_files_before_the_alerts_are_moved(
    rig: Any, tmp_path: Path
) -> None:
    folder = tmp_path / "problems"
    folder.mkdir()
    (folder / "problems-2001-01.csv").write_text("x", encoding="utf-8")
    (folder / "notes.txt").write_text("x", encoding="utf-8")
    item = rig()
    item.start()
    assert not (folder / "problems-2001-01.csv").exists()
    assert (folder / "notes.txt").exists()


def test_an_alert_moved_from_the_startup_file_gets_its_table_id_and_source(tmp_path: Path) -> None:
    item = Rig(tmp_path)
    line = startup_errors.startup_line(RuntimeError("창 오류 " + user_dir("u1")), DEFAULT_START)
    old = dict(line)
    del old["source"]  # `source` 키가 없는 앞의 줄
    old["content"] = "앞 줄"
    custom = {**line, "source": "ai/claude_cli.py:142"}
    startup_errors.append_line(item.logs, line)
    startup_errors.append_line(item.logs, old)
    startup_errors.append_line(item.logs, custom)
    item.start()
    try:
        rows = item.table()
    finally:
        item.stop()
    assert [r["source"] for r in rows] == [
        "app.startup_errors",
        "app.startup_errors",
        "ai/claude_cli.py:142",
    ]
    assert [r["code"] for r in rows] == ["A-30", "A-30", "A-30"]
    assert all(r["alert_id"].isdigit() for r in rows)
    assert all(r["round_number"] == "" for r in rows)
    assert "u1" not in json.dumps(rows)


def test_an_alert_that_cannot_be_saved_is_written_with_its_negative_memory_id(
    rig: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = rig()
    item.start()
    from coin_report_trader.app import data_thread

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise StorageError("쓰기 실패")

    monkeypatch.setattr(data_thread.alert_store, "insert_alert", broken)
    item.quote.script(QuoteError(ERROR_NETWORK), kind="price", market=AAA)
    item.round()
    rows = item.table()
    memory = [r for r in rows if r["code"] == "A-29"]
    assert len(memory) == 1
    assert memory[0]["alert_id"].startswith("-")
    assert memory[0]["stage"] == "알림 저장"
    assert memory[0]["source"].startswith("storage/") or memory[0]["source"].startswith("app")


def test_a_failure_to_write_the_table_does_not_stop_the_work(
    rig: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "problems").write_text("폴더가 아니라 파일", encoding="utf-8")
    item = rig()
    item.start()
    item.quote.script(QuoteError(ERROR_NETWORK), kind="price", market=AAA)
    item.round()
    conn = open_database(item.db)
    try:
        codes = [r[0] for r in conn.execute("SELECT code FROM alerts")]
    finally:
        conn.close()
    assert "A-2" in codes  # 알림은 그대로 만든다
