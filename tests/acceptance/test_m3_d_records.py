"""M3 명세 6절 D31, D32, D38: 기록 화면과 CSV 내보내기, DB 백업과 스키마를 바꾸기 전의 사본,
모의 성적.

자료 스레드, 가짜 실행기, 가짜 시세, 가짜 시계와 진짜 창으로 본다. 거래소와 진짜 `claude`를 부르지
않는다. 값은 모두 만든 값이다.
"""

import csv
import io
import sqlite3
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from acceptance.m3_d_support import (
    AAA,
    BBB,
    HOUR,
    PARTS,
    Rig,
    advance,
    all_codes,
    answer,
    apply_all,
    close_window,
    d_rig,
    d_trade,
    open_window,
    pauses,
    status,
)
from fakes.ai import answered, good_answers
from fakes.clock import FakeClock
from fakes.discord import FakeDiscord
from fakes.quote import FakeQuoteSource, make_candles, with_close
from fakes.vault import FakeVault
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel

from coin_report_trader.ai.answers import FINAL_JUDGMENT_COLUMNS
from coin_report_trader.ai.csv_files import write_csv
from coin_report_trader.app import run_gui
from coin_report_trader.app.services import Services
from coin_report_trader.config.paths import resolve_app_paths
from coin_report_trader.core import (
    HistoryTab,
    HistoryView,
    PerformanceView,
    QuoteError,
    SaveResult,
    ScreenId,
)
from coin_report_trader.core.texts import ERROR_NETWORK
from coin_report_trader.storage import database
from coin_report_trader.storage.database import open_database, schema_version
from coin_report_trader.ui import screen_widget, select_screen

D = Decimal
DAY = date(2026, 1, 5)
BOM = chr(0xFEFF)
JUDGMENT_HEADER = (
    "round_number,market,direction,predicted_price,actual_price,diff,hit,buy_price,sell_price,"
    "state,failure,predicted_reason"
)


@pytest.fixture
def build(tmp_path: Path) -> Iterator[Callable[..., Rig]]:
    rigs: list[Rig] = []

    def factory(**kwargs: Any) -> Rig:
        rig = d_rig(tmp_path / f"r{len(rigs)}", **kwargs)
        rigs.append(rig)
        return rig

    yield factory
    for rig in rigs:
        rig.stop()


def execute(rig: Rig, sql: str, *args: Any) -> None:
    conn = sqlite3.connect(rig.db)
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


def history(rig: Rig, tab: HistoryTab, market: str | None = None) -> HistoryView:
    rig.sink.items.clear()
    rig.data.load_history(tab, market, DAY)
    rig.settle()
    return rig.sink.of(HistoryView)[-1]


def export(rig: Rig, tab: HistoryTab, market: str | None, path: Path) -> SaveResult:
    before = len(rig.sink.of(SaveResult))
    rig.data.export_history(tab, market, str(path))
    rig.settle()
    results = rig.sink.of(SaveResult)
    assert len(results) == before + 1
    return results[-1]


def read_rows(path: Path) -> list[dict[str, str]]:
    """내보낸 파일의 줄(머리줄을 이름으로). 칸의 앞뒤 빈칸과 글을 그대로 둔다."""
    text = path.read_bytes().decode("utf-8").removeprefix(BOM)
    return list(csv.DictReader(io.StringIO(text, newline="")))


def candles_until(rig: Rig, last: datetime) -> None:
    """60분봉을 `last`에 시작하는 봉까지 받아 둔다(다음 회차 때의 실제 값을 구할 수 있다)."""
    rig.quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 200, last_start=last))


def answer_with_reason(rig: Rig, reason: str, **kwargs: Any) -> None:
    """예측 까닭을 정한 가짜 실행기의 답(그 밖은 `good_answers`의 정상 답)."""
    kwargs.setdefault("predicted", "1010")
    kwargs.setdefault("buy_price", "990")

    def plan(request: Any) -> Any:
        answers = good_answers(AAA, **kwargs)
        row = [
            AAA, kwargs.get("result_text", ""), kwargs.get("direction", "오를 것"),
            kwargs["predicted"], reason, kwargs["buy_price"], kwargs.get("sell_price", ""),
        ]  # fmt: skip
        answers["final_judgment"] = write_csv(FINAL_JUDGMENT_COLUMNS, [row])
        return answered(answers)

    rig.runner.plan(AAA, plan)


def next_bar(rig: Rig, hours: int) -> None:
    """회차마다 그 시각까지 끝난 봉을 받게 한다(처음 봉은 03:00에 시작하고 한 시간씩 늘린다)."""
    candles_until(rig, datetime(2026, 1, 5, 3, tzinfo=UTC) + timedelta(hours=hours))


def three_rounds(rig: Rig) -> None:
    """회차 1은 OK, 회차 2는 쉼(현재가를 읽지 못함), 회차 3은 OK이고 마지막이다."""
    next_bar(rig, 0)
    rig.start()
    answer(rig, direction="오를 것", predicted="1010", buy_price="990")
    rig.round()
    next_bar(rig, 1)
    rig.quote.script(*[QuoteError(ERROR_NETWORK)] * 40, kind="price", market=AAA)
    rig.round()
    rig.quote.clear_script()
    next_bar(rig, 2)
    answer(rig, direction="오를 것", predicted="1010", buy_price="990", result_text="지난 결과",
           compared=PARTS)  # fmt: skip
    rig.round()


# --- D31: 기록 화면의 판단 탭과 CSV ---------------------------------------------------------------


@pytest.mark.scenario("D31")
def test_d31_the_judgments_tab_shows_the_prediction_the_actual_value_and_the_skipped_reason(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    three_rounds(rig)
    view = history(rig, HistoryTab.JUDGMENTS, AAA)
    assert [row.cells[0] for row in view.rows] == ["3", "2", "1"]  # 새것이 위
    last, skipped, first = view.rows
    assert last.cells[4:7] == ("다음 회차 전",) * 3  # 마지막 줄
    assert skipped.cells[2:9] == ("-",) * 7  # 쉰 줄은 "-"와
    assert skipped.cells[9] == "현재가를 읽지 못함"  # 쉰 까닭
    assert first.cells[2] == "오를 것"
    assert first.cells[3] == "1,010"
    assert first.cells[4] != "-"
    assert first.cells[5][0] in "▲▼0"
    assert first.cells[6] in ("맞음", "틀림")
    assert first.report_id is not None  # 판단 줄에서 보고서로 간다


@pytest.mark.scenario("D31")
def test_d31_the_exported_file_has_a_bom_crlf_and_the_fixed_header(
    build: Callable[..., Rig], tmp_path: Path
) -> None:
    rig = build()
    three_rounds(rig)
    path = tmp_path / "judgments.csv"
    result = export(rig, HistoryTab.JUDGMENTS, AAA, path)
    assert (result.target, result.ok, result.message) == ("history_export", True, "내보냈습니다.")
    raw = path.read_bytes()
    assert raw.startswith(BOM.encode("utf-8"))
    text = raw.decode("utf-8")
    assert text.removeprefix(BOM).split("\r\n")[0] == JUDGMENT_HEADER
    assert "\n" not in text.replace("\r\n", "")  # 줄바꿈은 모두 CRLF다
    rows = read_rows(path)
    assert [row["round_number"] for row in rows] == ["1", "2", "3"]  # 오래된 것이 위
    assert rows[1]["state"] == "skipped"
    assert rows[1]["failure"] == "현재가를 읽지 못함"
    assert rows[1]["direction"] == rows[1]["predicted_price"] == ""  # 쉰 줄의 판단 칸은 빈 칸
    assert "A-29" not in all_codes(rig)
    assert status(rig).error_paused is False


@pytest.mark.scenario("D31")
def test_d31_a_path_that_cannot_be_written_fails_without_an_a29_or_an_error_pause(
    build: Callable[..., Rig], tmp_path: Path
) -> None:
    rig = build()
    three_rounds(rig)
    folder = tmp_path / "as-a-file"
    folder.mkdir()  # 폴더에는 쓸 수 없다
    result = export(rig, HistoryTab.JUDGMENTS, AAA, folder)
    assert (result.ok, result.message) == (False, "내보내지 못했습니다.")
    assert "A-29" not in all_codes(rig)
    assert status(rig).error_paused is False
    assert pauses(rig) == []


@pytest.mark.scenario("D31")
def test_d31_a_cell_that_starts_like_a_formula_gets_a_quote_but_a_negative_number_does_not(
    build: Callable[..., Rig], tmp_path: Path
) -> None:
    rig = build()
    next_bar(rig, 0)
    rig.start()
    reasons = ["=1+1", "@A1", "+1", "-1", "탭"]
    for index, reason in enumerate(reasons):
        next_bar(rig, index)
        extra = {} if index == 0 else {"result_text": "지난 결과", "compared": PARTS}
        # 예측 가격 1,200은 다음 봉의 종가(1,03x)보다 높아 차이가 `-0.14…`로 음수다
        answer_with_reason(rig, reason, predicted="1200", **extra)
        rig.round()
    # 답을 읽을 때 글 앞의 빈칸을 지우므로, 탭으로 시작하는 까닭은 DB에 바로 심는다
    execute(rig, "UPDATE reports SET predicted_reason = ? WHERE round_id = 5", "\t탭")
    path = tmp_path / "formulas.csv"
    assert export(rig, HistoryTab.JUDGMENTS, AAA, path).ok
    rows = read_rows(path)
    written = [row["predicted_reason"] for row in rows]
    reasons[-1] = "\t탭"
    assert written == ["'" + reason for reason in reasons]  # 모두 `'`로 시작한다
    diffs = [row["diff"] for row in rows if row["diff"]]
    assert diffs
    assert all(not diff.startswith("'") for diff in diffs)
    assert any(diff.startswith("-") for diff in diffs)


@pytest.mark.scenario("D31")
def test_d31_a_failed_reports_judgment_cells_are_blank_and_text_cells_are_quoted(
    build: Callable[..., Rig], tmp_path: Path
) -> None:
    rig = build()
    rig.start()
    rig.runner.plan(AAA, lambda request: answered({}))  # 판단 표가 비어 `FAILED`가 된다
    rig.round()
    (state,) = rig.rows("SELECT state FROM reports")
    assert state[0] != "ok"
    execute(
        rig, "UPDATE reports SET direction = ?, predicted_price = ?",
        '=HYPERLINK("https://example.com")', "+1+1",
    )  # fmt: skip
    execute(rig, "UPDATE rounds SET state = ?", "=SUM(1)")
    execute(
        rig, "INSERT INTO pauses (mode, kind, cause, detail, sell_all, started_utc) "
        "VALUES ('paper', 'error', 'save', ?, 0, '2026-01-05T04:30:00Z')", "=1+1",
    )  # fmt: skip
    judgments = tmp_path / "failed.csv"
    assert export(rig, HistoryTab.JUDGMENTS, AAA, judgments).ok
    (row,) = read_rows(judgments)
    assert (row["direction"], row["predicted_price"]) == ("", "")  # 그 줄의 두 칸은 빈 칸
    rounds_csv = tmp_path / "rounds.csv"
    assert export(rig, HistoryTab.ROUNDS, None, rounds_csv).ok
    (round_row,) = read_rows(rounds_csv)
    assert round_row["state"] == "'=SUM(1)"
    pauses_csv = tmp_path / "pauses.csv"
    assert export(rig, HistoryTab.PAUSES, None, pauses_csv).ok
    rows = read_rows(pauses_csv)
    assert [row["detail"] for row in rows if row["cause"] == "save"] == ["'=1+1"]


# --- D32: DB 백업 ---------------------------------------------------------------------------------


def _seven_o_clock_seed(root: Path) -> None:
    folder = root / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    for day in range(1, 5):
        (folder / f"app-2026-12-0{day}.db").write_bytes(b"x")


@pytest.mark.scenario("D32")
def test_d32_the_daily_backup_is_made_at_start_and_a_day_later_and_old_ones_are_pruned(
    tmp_path: Path,
) -> None:
    root = tmp_path / "backup"
    _seven_o_clock_seed(root)
    clock = FakeClock(datetime(2026, 12, 5, 0, 10, tzinfo=UTC))  # 한국 2026-12-05 09:10
    rig = d_rig(root, trade=d_trade((AAA,), backup_days=3), clock=clock, fresh=True)
    try:
        rig.start()
        names = sorted(path.name for path in (root / "backups").glob("app-*.db"))
        assert names == ["app-2026-12-03.db", "app-2026-12-04.db", "app-2026-12-05.db"]
        assert (root / "backups" / "app-2026-12-05.db").stat().st_size > 1  # 진짜 DB 사본이다
        advance(rig, 24 * 3600)  # 하루 뒤: 한국 2026-12-06 09:10
        names = sorted(path.name for path in (root / "backups").glob("app-*.db"))
        assert names == ["app-2026-12-04.db", "app-2026-12-05.db", "app-2026-12-06.db"]
    finally:
        rig.stop()


@pytest.mark.scenario("D32")
def test_d32_a_schema_two_database_gets_a_copy_before_it_is_raised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, qapp: Any
) -> None:
    key = "LOCALAPPDATA" if sys.platform == "win32" else "XDG_DATA_HOME"
    monkeypatch.setenv(key, str(tmp_path / "share"))
    (tmp_path / "share").mkdir()
    paths = resolve_app_paths()
    paths.ensure()
    conn = open_database(paths.database_file)
    try:
        for statements in (database._SCHEMA_1, database._SCHEMA_2):
            for statement in statements:
                conn.execute(statement)
        conn.execute("INSERT INTO settings VALUES ('coins', '[\"KRW-AAA\"]')")
        conn.execute("PRAGMA user_version=2")
        conn.commit()
    finally:
        conn.close()
    services = Services(
        quote=FakeQuoteSource(), webhook=FakeDiscord(), vault=FakeVault(), clock=FakeClock()
    )
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(qapp.quit)
    timer.start(0)
    assert run_gui([], services=services) == 0
    timer.stop()
    copies = sorted((paths.root / "backups").glob("before-schema-3-*.db"))
    assert len(copies) == 1  # 올리기 전에 사본이 먼저 생겼다
    check = open_database(copies[0])
    try:
        assert schema_version(check) == 2  # 사본은 올리기 전의 모습이다
    finally:
        check.close()
    live = open_database(paths.database_file)
    try:
        assert schema_version(live) == 3
    finally:
        live.close()


# --- D38: 모의 성적의 예측 차이 평균 --------------------------------------------------------------


def _judged_reports(conn: Any) -> None:
    """`KRW-AAA`의 `OK` 보고서 넷(마감 00:00, 01:00, 02:00, 01:30). 마지막은 그 때의 봉이 없다."""
    for number, close in enumerate(("00:00", "01:00", "02:00", "01:30"), start=1):
        at = f"2026-01-05T{close}:00Z"
        conn.execute(
            "INSERT INTO rounds (mode, number, \"trigger\", unit, close_utc, next_utc, started_utc,"
            " ended_utc, state) VALUES ('paper', ?, 'bar_close', ?, ?, ?, ?, ?, 'done')",
            (number, HOUR.value, at, at, at, at),
        )
        conn.execute(
            "INSERT INTO reports (round_id, mode, market, state, base_price, direction,"
            " predicted_price, created_utc)"
            " VALUES (?, 'paper', ?, 'ok', '1000', '오를 것', '1000', ?)",
            (number, AAA, at),
        )
    conn.commit()


@pytest.mark.scenario("D38")
def test_d38_the_status_screen_shows_the_two_prediction_difference_means(
    build: Callable[..., Rig], qapp: Any
) -> None:
    rig = build(coins=(AAA, BBB), seed=_judged_reports)
    # 마감 00:00, 01:00, 02:00의 다음 봉(그 시각에 시작하는 봉)의 종가로 차이가
    # −0.0098, +0.0200, −0.0040이 된다
    base = make_candles(AAA, HOUR, 200, last_start=datetime(2026, 1, 5, 3, tzinfo=UTC))
    closes = {0: D("990.2"), 1: D("1020"), 2: D("996")}
    candles = tuple(
        with_close(candle, closes[candle.start.hour])
        if candle.start.date() == date(2026, 1, 5) and candle.start.hour in closes
        else candle
        for candle in base
    )
    rig.quote.set_candles(AAA, HOUR, candles)
    rig.start()
    advance(rig, 60)  # 봉을 받은 뒤의 1분 점검이 모의 성적을 다시 보낸다
    view = rig.sink.of(PerformanceView)[-1]
    coin = next(row for row in view.rows if row.market == AAA)
    other = next(row for row in view.rows if row.market == BBB)
    assert (coin.ok_rounds, coin.countable) == (4, 3)  # 셀 수 있는 판단은 실제 값이 있는 셋
    assert coin.diff_mean == D("0.0021")
    assert coin.diff_abs_mean == D("0.0113")
    assert other.diff_mean is None
    assert other.diff_abs_mean is None
    window = open_window(rig)
    try:
        select_screen(window, ScreenId.STATUS)
        status_screen = screen_widget(window, ScreenId.STATUS)
        cells = [
            [_label(status_screen, f"statusPerformanceCell_{row}_{column}") for column in range(9)]
            for row in range(2)
        ]
        assert cells[0][0] == "AAA"
        assert cells[1][0] == "BBB"
        assert cells[0][3:5] == ["▲ +0.21%", "1.13%"]  # `KRW-AAA` 줄
        assert cells[1][3:5] == ["-", "-"]  # `KRW-BBB` 줄은 둘 다 "-"
    finally:
        close_window(window)


def _label(parent: Any, name: str) -> str:
    found = parent.findChild(QLabel, name)
    assert found is not None, name
    return str(found.text())


_UNUSED = (apply_all, timedelta)
