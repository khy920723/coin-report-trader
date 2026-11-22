"""M2 명세 6절의 창을 띄우는 시나리오: C1의 스레드 확인, C27(3.5, 3.12, 6절의 아래 글).

`run_gui`를 그대로 돌리고 `services`에 `FakeAiRunner`와 가짜 시세를 넣는다(`acceptance.m2_window`).
자료 폴더는 `tests/conftest.py`가 시험마다 `tmp_path` 아래로 바꾼다. 값은 모두 만든 값이다.
"""

import sqlite3
import threading
from typing import Any

import pytest
from acceptance.m2_support import AAA, BBB, fake_quote, seed_db
from acceptance.m2_window import App, data_root, run_app, services
from fakes.ai import FakeAiRunner, answered, good_answers
from fakes.clock import FakeClock
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton

from coin_report_trader.app.ai_threads import AI_THREAD_PREFIX
from coin_report_trader.app.data_thread import DATA_THREAD_NAME
from coin_report_trader.core import AiSlot, AiStatus, AiStatusView, ScreenId
from coin_report_trader.storage.database import open_database
from coin_report_trader.ui import current_screen, screen_widget, select_screen
from coin_report_trader.ui.texts import AI_NOT_CHOSEN


def read(sql: str) -> list[tuple[Any, ...]]:
    conn = open_database(data_root() / "data" / "app.db")
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def to_next_due(app: App, clock: FakeClock, extra: float = 0.0) -> None:
    due = app.worker._next_due
    assert due is not None
    clock.advance((due - clock.now()).total_seconds() + extra)


# --- C1: 스레드 -------------------------------------------------------------------------------


@pytest.mark.scenario("C1")
def test_c1_the_ai_threads_do_not_open_the_db_and_apply_runs_only_on_the_main_thread(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_db(data_root(), coins=(AAA, BBB))
    clock = FakeClock()
    quote = fake_quote(clock, (AAA, BBB))
    ai = FakeAiRunner()
    session_threads: set[str] = set()

    def plan(request: Any) -> Any:
        session_threads.add(threading.current_thread().name)
        return answered(good_answers(request.market))

    ai.plan(AAA, plan)
    ai.plan(BBB, plan)
    sqlite_threads: set[str] = set()
    real_connect = sqlite3.connect
    watching = {"on": True}

    def connect(*args: Any, **kwargs: Any) -> Any:
        if watching["on"]:
            sqlite_threads.add(threading.current_thread().name)
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)

    def round_time(app: App) -> bool:
        if not app.idle():
            return False
        to_next_due(app, clock, 10)  # 다음 봉이 끝나는 때 + 10초
        return True

    def reports_done(app: App) -> bool:
        if len(ai.requests) < 2 or app.worker._pending or not app.idle():
            return False
        watching["on"] = False  # 시험이 읽는 것은 세지 않는다
        try:
            return read("SELECT state FROM reports ORDER BY market") == [("ok",), ("ok",)]
        finally:
            watching["on"] = True

    code, app = run_app(
        monkeypatch, services(quote=quote, clock=clock, ai=ai), [round_time, reports_done]
    )
    watching["on"] = False
    assert code == 0
    assert len(session_threads) == 2
    assert all(name.startswith(AI_THREAD_PREFIX) for name in session_threads)
    assert not {name for name in sqlite_threads if name.startswith(AI_THREAD_PREFIX)}
    # 켜는 순서 1(주 스레드)과 자료 스레드만 DB를 연다.
    assert sqlite_threads - {"MainThread"} == {DATA_THREAD_NAME}
    assert app.apply_threads == {"MainThread"}  # `apply`는 주 스레드에서만 불린다


# --- C27 --------------------------------------------------------------------------------------


@pytest.mark.scenario("C27")
def test_c27_an_older_record_without_the_four_slots_starts_on_status_and_saving_them_turns_ai_on(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_db(data_root(), coins=(AAA,), choices=())  # 2단계 전에 처음 설정을 마친 DB
    clock = FakeClock()
    quote = fake_quote(clock, (AAA,))
    ai = FakeAiRunner()
    seen: dict[str, Any] = {}

    def top_line(app: App) -> QLabel:
        label = screen_widget(app.window, ScreenId.REPORTS).findChild(QLabel, "reportAiStatus")
        assert label is not None
        return label

    def started(app: App) -> bool:
        if not app.idle():
            return False
        statuses = [u for u in app.applied if isinstance(u, AiStatusView)]
        if not statuses:
            return False
        seen["screen"] = current_screen(app.window)
        seen["status"] = statuses[-1].status
        select_screen(app.window, ScreenId.REPORTS)
        QApplication.processEvents()
        label = top_line(app)
        seen["top"] = None if label.isHidden() else label.text()
        return True

    def bar_time(app: App) -> bool:
        to_next_due(app, clock)
        return True

    def no_round(app: App) -> bool:
        if not app.idle():
            return False
        seen["rounds_before"] = read("SELECT COUNT(*) FROM rounds")[0][0]
        seen["checks_before"] = ai.check_calls
        seen["due"] = app.worker._next_due
        return True

    def save_slots(app: App) -> bool:
        select_screen(app.window, ScreenId.SETTINGS)
        screen = screen_widget(app.window, ScreenId.SETTINGS)
        for slot in AiSlot:
            for kind in ("Model", "Effort"):
                combo = screen.findChild(QComboBox, f"settingsAi{kind}_{slot.value}")
                assert combo is not None
                combo.setCurrentIndex(1)
        save = screen.findChild(QPushButton, "settingsSave")
        assert save is not None
        save.click()
        return True

    def turned_on(app: App) -> bool:
        if not app.idle():
            return False
        on = [u for u in app.applied if isinstance(u, AiStatusView) and u.status is AiStatus.ON]
        if not on:
            return False
        select_screen(app.window, ScreenId.REPORTS)
        QApplication.processEvents()
        seen["top_after"] = top_line(app).isHidden()
        seen["checks_after"] = ai.check_calls
        seen["clock_before_due"] = clock.now() < app.worker._next_due
        return True

    def next_bar(app: App) -> bool:
        to_next_due(app, clock)
        return True

    def round_ran(app: App) -> bool:
        if not ai.requests or app.worker._pending or not app.idle():
            return False
        seen["rounds_after"] = read("SELECT number, state FROM rounds")
        return True

    steps = [started, bar_time, no_round, save_slots, turned_on, next_bar, round_ran]
    code, _ = run_app(monkeypatch, services(quote=quote, clock=clock, ai=ai), steps)
    assert code == 0
    assert seen["screen"] is ScreenId.STATUS  # 처음 설정이 다시 보이지 않는다
    assert seen["status"] is AiStatus.NOT_CHOSEN
    assert seen["top"] == AI_NOT_CHOSEN  # 보고서 화면 맨 위의 NOT_CHOSEN의 글
    assert seen["rounds_before"] == 0  # 봉의 때에 회차가 없다
    assert seen["checks_before"] == 0
    assert seen["checks_after"] == 1  # 여덟 칸을 저장한 그때 check()가 한 번
    assert seen["top_after"] is True  # 봉의 때 전에 맨 위 줄이 사라진다
    assert seen["clock_before_due"] is True
    assert seen["rounds_after"] == [(1, "done")]  # 그 뒤의 봉의 때에 회차가 돈다
