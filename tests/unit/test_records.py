"""3단계 잇기 2(M3 명세 4절 25번): 기록 화면, CSV 내보내기, 모의 성적, DB 백업.

자료 스레드, 가짜 실행기, 가짜 시세, 가짜 시계로 본다. 값은 모두 만든 값이다.
"""

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fakes.clock import FakeClock
from fakes.quote import make_candles
from fakes.trade_rig import (
    AAA,
    BBB,
    HOUR,
    PARTS,
    Rig,
    holding_seed,
    make_rig,
    rig_builder,
)

from coin_report_trader.ai.csv_files import read_csv
from coin_report_trader.app.records import HISTORY_COLUMNS, _csv_cell
from coin_report_trader.core import (
    HistoryTab,
    HistoryView,
    LoadState,
    PerformanceView,
    SaveResult,
)

DAY = date(2026, 1, 5)
AFTER_ROUND = datetime(2026, 1, 5, 4, tzinfo=UTC)
BOM = chr(0xFEFF)


@pytest.fixture
def build(tmp_path: Path) -> Iterator[Callable[..., Rig]]:
    yield from rig_builder(tmp_path)


def history(rig: Rig, tab: HistoryTab, market: str | None = None) -> HistoryView:
    rig.sink.items.clear()
    rig.worker.load_history(tab, market, DAY)
    rig.settle()
    return rig.sink.of(HistoryView)[-1]


def one_round_with_an_order(rig: Rig) -> None:
    rig.start()
    rig.answer(direction="오를 것", buy_price="99")
    rig.round()


# --- 기록 화면(3.15.1) -------------------------------------------------------------------------


def test_the_rounds_tab_shows_the_number_the_stage_and_the_result(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    one_round_with_an_order(rig)
    view = history(rig, HistoryTab.ROUNDS)
    assert view.columns == ("번호", "시작 시각", "끝난 단계", "결과")
    assert view.state is LoadState.READY
    (row,) = view.rows
    assert (row.cells[0], row.cells[2], row.cells[3]) == ("1", "기록", "끝남")


def test_a_discarded_round_is_shown_as_discarded_by_the_pause(build: Callable[..., Rig]) -> None:
    import threading

    rig = build()
    rig.start()
    hold = threading.Event()
    started = rig.runner.plan(AAA, wait=hold)
    due = rig.worker._next_due
    assert due is not None
    rig.clock.advance((due - rig.clock.now()).total_seconds())
    assert started.wait(10)
    rig.worker.pause()
    rig.settle()
    (row,) = history(rig, HistoryTab.ROUNDS).rows
    assert row.cells[3] == "멈춤으로 버림"
    hold.set()
    rig.settle()


def test_the_judgments_tab_shows_before_next_round_and_the_dash_of_a_failed_report(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    one_round_with_an_order(rig)
    view = history(rig, HistoryTab.JUDGMENTS)
    assert view.columns[3:6] == ("예측 가격", "다음 회차 때의 실제 값", "차이(%)")
    (row,) = view.rows
    assert row.cells == (
        "1", "AAA", "오를 것", "102", "다음 회차 전", "다음 회차 전", "다음 회차 전", "99", "-", ""
    )  # fmt: skip
    assert row.market == AAA
    assert row.report_id is not None
    assert row.detail == (("예측 까닭", "까닭"),)
    # 쉰 줄(받지 못함): 최종 판단과 값 칸이 "-"이고 마지막 칸에 까닭이 보인다
    rig.execute("UPDATE reports SET state = 'skipped', failure = '현재가를 읽지 못함'")
    (skipped,) = history(rig, HistoryTab.JUDGMENTS).rows
    assert skipped.cells == ("1", "AAA", *["-"] * 7, "현재가를 읽지 못함")
    assert skipped.detail == ()


def test_the_judgments_tab_shows_the_actual_value_the_difference_and_the_hit(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    one_round_with_an_order(rig)
    # 첫 판단이 가리킨 때(04:00)에 시작하는 봉이 저장되어 있다
    rig.quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 200, last_start=AFTER_ROUND))
    rig.advance(3600)  # 다음 회차 때가 왔다
    first = next(r for r in history(rig, HistoryTab.JUDGMENTS).rows if r.cells[0] == "1")
    assert first.cells[4] not in ("다음 회차 전", "-")
    assert first.cells[6] in ("맞음", "틀림")
    assert first.cells[5][0] in "▲▼0"  # 차이는 부호와 ▲▼로 보인다


def test_the_orders_and_fills_and_pauses_tabs(build: Callable[..., Rig]) -> None:
    rig = build(seed=holding_seed(AAA, "100", "90"))
    one_round_with_an_order(rig)
    rig.worker.pause_and_sell_all()
    rig.settle()
    orders = history(rig, HistoryTab.ORDERS)
    assert orders.columns[:3] == ("시각", "코인", "매수·매도")
    sell, buy = orders.rows  # 새것이 위
    assert sell.cells[1:4] == ("AAA", "매도", "시장가")
    assert sell.cells[6:] == ("긴급 매도", "체결")
    assert buy.cells[1:4] == ("AAA", "매수", "지정가")
    assert buy.cells[4] == "99"
    assert buy.cells[6:] == ("판단", "취소")
    assert dict(buy.detail)["식별값"]
    (fill,) = history(rig, HistoryTab.FILLS).rows
    assert fill.cells[1:3] == ("AAA", "매도")
    assert fill.cells[6].startswith("▲ +")  # 평균 매수가 90에 사서 100에 팔았다
    pauses = history(rig, HistoryTab.PAUSES)
    (row,) = pauses.rows
    assert (row.cells[1], row.cells[2]) == ("사용자", "멈춰 있음")
    rig.worker.resume_user()
    rig.settle()
    (released,) = history(rig, HistoryTab.PAUSES).rows
    assert released.cells[2] != "멈춰 있음"


def test_an_empty_day_and_the_external_tab_are_no_data(build: Callable[..., Rig]) -> None:
    rig = build()
    rig.start()
    assert history(rig, HistoryTab.FILLS).state is LoadState.NO_DATA
    assert history(rig, HistoryTab.EXTERNAL).state is LoadState.NO_DATA


def test_a_history_read_failure_is_a_failed_view_an_a30_and_an_error_pause(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    from coin_report_trader.core import StorageError
    from coin_report_trader.storage import trading as trade_store

    rig = build()
    rig.start()

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise StorageError("읽기 실패")

    monkeypatch.setattr(trade_store, "list_orders", broken)
    assert history(rig, HistoryTab.ORDERS).state is LoadState.FAILED
    assert rig.codes().count("A-30") == 1
    assert rig.status().error_paused is True


# --- CSV 내보내기(3.15.2) ----------------------------------------------------------------------


def exported(rig: Rig, tab: HistoryTab, tmp_path: Path) -> tuple[bytes, list[dict[str, str]]]:
    path = tmp_path / f"{tab.value}.csv"
    rig.sink.items.clear()
    rig.worker.export_history(tab, None, str(path))
    rig.settle()
    assert rig.sink.of(SaveResult)[-1] == SaveResult("history_export", True, "내보냈습니다.")
    raw = path.read_bytes()
    header, rows = read_csv(raw.decode("utf-8").lstrip(BOM))
    assert header == HISTORY_COLUMNS[tab]
    return raw, [dict(zip(header, row, strict=True)) for row in rows]


def test_the_export_writes_a_bom_crlf_and_the_fixed_header_of_every_tab(
    build: Callable[..., Rig], tmp_path: Path
) -> None:
    rig = build()
    one_round_with_an_order(rig)
    rig.worker.pause()
    rig.settle()
    for tab in (HistoryTab.ROUNDS, HistoryTab.JUDGMENTS, HistoryTab.ORDERS, HistoryTab.PAUSES):
        raw, rows = exported(rig, tab, tmp_path)
        assert raw.startswith(BOM.encode("utf-8"))
        assert b"\r\n" in raw
        assert len(rows) == 1
    _, (order,) = exported(rig, HistoryTab.ORDERS, tmp_path)
    assert (order["side"], order["kind"], order["reason"], order["state"]) == (
        "buy", "limit", "judgment", "canceled",
    )  # fmt: skip
    assert order["price"] == "99"
    assert order["uid"]
    _, (pause,) = exported(rig, HistoryTab.PAUSES, tmp_path)
    assert (pause["kind"], pause["sell_all"], pause["released_utc"]) == ("user", "0", "")


def test_the_export_guards_formulas_and_blanks_the_judgment_of_a_report_that_is_not_ok(
    build: Callable[..., Rig], tmp_path: Path
) -> None:
    rig = build()
    one_round_with_an_order(rig)
    rig.execute("UPDATE reports SET predicted_reason = '=1+1'")
    _, (row,) = exported(rig, HistoryTab.JUDGMENTS, tmp_path)
    assert row["predicted_reason"] == "'=1+1"
    assert row["direction"] == "오를 것"
    rig.execute(
        "UPDATE reports SET state = 'failed', failure = '=SUM(A1)',"
        " direction = '=HYPERLINK(\"https://example.com\")', predicted_price = '+1+1'"
    )
    _, (failed,) = exported(rig, HistoryTab.JUDGMENTS, tmp_path)
    assert failed["direction"] == failed["predicted_price"] == ""
    assert failed["predicted_reason"] == failed["actual_price"] == failed["hit"] == ""
    assert (failed["state"], failed["failure"]) == ("failed", "'=SUM(A1)")


def test_the_formula_guard_leaves_values_numbers_and_times_alone() -> None:
    assert _csv_cell("diff", "-0.0098") == "-0.0098"
    assert _csv_cell("price", "-5") == "-5"
    assert _csv_cell("predicted_reason", "-1") == "'-1"
    assert _csv_cell("predicted_reason", "@A1") == "'@A1"
    assert _csv_cell("predicted_reason", "\t=1") == "'\t=1"
    assert _csv_cell("direction", "=cmd") == "'=cmd"
    assert _csv_cell("price", "+1+1") == "'+1+1"
    assert _csv_cell("state", "filled") == "filled"
    assert _csv_cell("started_utc", "2026-01-05T03:00:00Z") == "2026-01-05T03:00:00Z"
    assert _csv_cell("stage", "=정리") == "'=정리"
    assert _csv_cell("detail", "") == ""


def test_an_export_that_cannot_be_written_is_a_failed_result_without_an_a29_or_an_error_pause(
    build: Callable[..., Rig], tmp_path: Path
) -> None:
    rig = build()
    one_round_with_an_order(rig)
    rig.sink.items.clear()
    rig.worker.export_history(HistoryTab.ORDERS, None, str(tmp_path))  # 폴더라 쓸 수 없다
    rig.settle()
    assert rig.sink.of(SaveResult)[-1] == SaveResult(
        "history_export", False, "내보내지 못했습니다."
    )
    assert "A-29" not in rig.codes()
    assert rig.worker._pause.error is False


# --- 모의 성적(3.16.1) -------------------------------------------------------------------------


def test_the_performance_view_has_a_row_per_coin_and_a_total(build: Callable[..., Rig]) -> None:
    rig = build(coins=(AAA, BBB))
    rig.start()
    view = rig.sink.of(PerformanceView)[-1]
    assert [row.market for row in view.rows] == [AAA, BBB, None]
    assert view.rows[-1].asset_change == 0


def test_the_performance_view_counts_the_ok_rounds_and_the_realized_sells(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=holding_seed(AAA, "100", "90"))
    one_round_with_an_order(rig)
    assert rig.sink.of(PerformanceView)[-1].rows[0].ok_rounds == 1
    rig.worker.pause_and_sell_all()
    rig.settle()
    row = rig.sink.of(PerformanceView)[-1].rows[0]
    assert (row.sells, row.wins) == (1, 1)
    assert row.realized_pnl > 0
    assert row.diff_mean is None  # 셀 수 있는 판단이 없다


def test_the_performance_view_gives_the_mean_difference_of_the_countable_judgments(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    one_round_with_an_order(rig)
    rig.quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 200, last_start=AFTER_ROUND))
    rig.answer(direction="오를 것", buy_price="99", result_text="지난 결과", compared=PARTS)
    rig.round()  # 두 번째 회차: 첫 판단의 실제 값이 생긴다
    rows = rig.sink.of(PerformanceView)[-1].rows
    assert rows[0].ok_rounds == 2
    assert rows[0].countable == 1
    assert rows[0].diff_mean is not None
    assert rows[0].diff_abs_mean is not None


# --- DB 백업(3.13) -----------------------------------------------------------------------------


def seed_backups(root: Path, days: list[str]) -> Path:
    folder = root / "backups"
    folder.mkdir()
    for day in days:
        (folder / f"app-{day}.db").write_bytes(b"x")
    return folder


def test_the_daily_backup_is_made_at_start_and_when_the_day_changes_and_old_ones_are_deleted(
    tmp_path: Path,
) -> None:
    clock = FakeClock(datetime(2026, 12, 5, 0, 10, tzinfo=UTC))  # 한국 2026-12-05 09:10
    root = tmp_path / "r"
    root.mkdir()
    folder = seed_backups(root, ["2026-12-01", "2026-12-02", "2026-12-03", "2026-12-04"])
    (folder / "before-schema-3-20261201-000000.db").write_bytes(b"x")
    rig = make_rig(root, clock=clock, backup_days=3)
    try:
        rig.start()
        names = sorted(p.name for p in folder.iterdir())
        assert names == [
            "app-2026-12-03.db",
            "app-2026-12-04.db",
            "app-2026-12-05.db",
            "before-schema-3-20261201-000000.db",
        ]
        rig.advance(24 * 3600)  # 하루가 지났다(한국 09:10)
        names = sorted(p.name for p in folder.iterdir())
        assert names == [
            "app-2026-12-04.db",
            "app-2026-12-05.db",
            "app-2026-12-06.db",
            "before-schema-3-20261201-000000.db",
        ]
    finally:
        rig.worker.stop()
        rig.worker.join(10)


def test_the_daily_backup_is_not_made_before_the_first_setup_is_done(tmp_path: Path) -> None:
    from fakes.quote import FakeQuoteSource

    from coin_report_trader.app.data_thread import DataWorker
    from coin_report_trader.core import SettingsView
    from coin_report_trader.storage.database import migrate, open_database
    from coin_report_trader.storage.settings import write_settings

    db = tmp_path / "app.db"
    logs = tmp_path / "logs"
    logs.mkdir()
    conn = open_database(db)
    try:
        migrate(conn)
        write_settings(conn, SettingsView((AAA,), 30, False))
    finally:
        conn.close()
    clock = FakeClock()
    worker = DataWorker(
        database_file=db,
        logs_dir=logs,
        clock=clock,
        quote=FakeQuoteSource(markets=[], clock=clock),
        sink=lambda update: None,
    )
    worker.start()
    try:
        assert worker.wait_idle(10)
        assert not (tmp_path / "backups").exists()
    finally:
        worker.stop()
        worker.join(10)


def test_a_backup_that_cannot_be_made_is_only_an_a29_and_the_rounds_and_stop_loss_go_on(
    tmp_path: Path,
) -> None:
    clock = FakeClock(datetime(2026, 12, 5, 0, 10, tzinfo=UTC))
    root = tmp_path / "r"
    root.mkdir()
    (root / "backups").write_text("파일이 폴더 자리에 있다", encoding="utf-8")
    rig = make_rig(root, clock=clock, seed=holding_seed(AAA, "100", "100"))
    try:
        rig.start()
        assert rig.codes().count("A-29") == 1
        (a29,) = [a for a in rig.alerts() if a.code == "A-29"]
        assert a29.detail is not None
        assert (a29.detail.stage, a29.detail.error_name) == ("DB 백업", "DB 오류")
        assert a29.detail.after == (
            "백업을 만들거나 오래된 백업을 지우지 못했습니다. 다음 하루가 바뀌는 때에 다시 합니다."
        )
        assert rig.status().error_paused is False
        from decimal import Decimal

        rig.quote.set_prices({AAA: Decimal(90)})
        rig.advance(60)
        assert [o[3] for o in rig.orders()] == ["stop_loss"]  # 손절이 그대로 돈다
    finally:
        rig.worker.stop()
        rig.worker.join(10)


def test_a_history_day_is_the_korean_day_that_changes_at_nine() -> None:
    from coin_report_trader.app.records import day_bounds

    start, end = day_bounds(DAY)
    assert start == datetime(2026, 1, 5, 0, 0, tzinfo=UTC)  # 한국 09:00
    assert end - start == timedelta(days=1)
