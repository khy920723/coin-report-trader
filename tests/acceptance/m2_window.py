"""M2 수용 시험 가운데 창을 띄우는 시나리오(C1의 스레드 확인, C27)가 함께 쓰는 도구.

`run_gui`를 그대로 돌리고 `services`에 가짜(시세, 실행기, 디스코드, 보관소, 시계)를 넣는다(명세
6절의 아래 글: 창을 띄우는 시험은 `services`에 `FakeAiRunner`와 가짜 시세를 넣어 돈다). 자료
폴더는 `tests/conftest.py`가 시험마다 `tmp_path` 아래로 바꾼다. 사람이 하는 일은 창의 단추와 칸으로
한다.

이 파일은 시험을 담지 않는다(`test_`로 시작하지 않아 모으지 않는다).
"""

import threading
from collections.abc import Callable
from typing import Any

import pytest
from fakes.ai import FakeAiRunner
from fakes.clock import FakeClock
from fakes.discord import FakeDiscord
from fakes.vault import FakeVault
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from coin_report_trader import ui
from coin_report_trader.app import data_thread, run_gui
from coin_report_trader.app.services import Services
from coin_report_trader.config.paths import resolve_app_paths

LIMIT = 20.0
"""한 `run_gui`가 넘지 않을 실제 시간(안전 타이머). 넘으면 남은 차례가 있어 실패로 끝난다."""


class App:
    """한 번의 `run_gui`가 만든 창, 동작, 자료 스레드, 창이 받은 값."""

    def __init__(self) -> None:
        self.window: Any = None
        self.actions: Any = None
        self.workers: list[Any] = []
        self.applied: list[Any] = []
        self.apply_threads: set[str] = set()

    @property
    def worker(self) -> Any:
        return self.workers[0]

    def idle(self) -> bool:
        return bool(self.workers) and self.workers[0].wait_idle(0.002)


def services(
    *, quote: Any, clock: FakeClock, ai: FakeAiRunner, discord: FakeDiscord | None = None
) -> Services:
    return Services(
        quote=quote, webhook=discord or FakeDiscord(), vault=FakeVault(), clock=clock, ai=ai
    )


def run_app(
    monkeypatch: pytest.MonkeyPatch,
    given: Services,
    steps: list[Callable[[App], bool]],
) -> tuple[int, App]:
    """`run_gui`를 돌리며 차례를 하나씩 밟는다. 차례는 참을 돌려줄 때 끝난다. 다 끝나면 닫는다."""
    app = App()
    real_create = ui.create_main_window

    def create(context: Any) -> Any:
        window = real_create(context)
        real_apply = window.apply

        def apply(update: Any) -> None:
            app.applied.append(update)
            app.apply_threads.add(threading.current_thread().name)
            real_apply(update)

        window.apply = apply
        app.window, app.actions = window, context.actions
        return window

    class SeenWorker(data_thread.DataWorker):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            app.workers.append(self)

    monkeypatch.setattr(ui, "create_main_window", create)
    monkeypatch.setattr(data_thread, "DataWorker", SeenWorker)
    pending = list(steps)
    failure: list[BaseException] = []
    poll = QTimer()
    poll.setInterval(10)

    def tick() -> None:
        if not pending:
            poll.stop()
            QApplication.quit()
            return
        if app.window is None:
            return
        try:
            if pending[0](app):
                pending.pop(0)
        except BaseException as exc:  # 슬롯의 예외는 Qt가 삼키므로 시험이 다시 낸다
            failure.append(exc)
            poll.stop()
            QApplication.quit()

    poll.timeout.connect(tick)
    safety = QTimer()
    safety.setSingleShot(True)
    safety.timeout.connect(QApplication.quit)
    armed = {"on": True}

    def begin() -> None:
        if armed["on"]:
            poll.start()
            safety.start(int(LIMIT * 1000))

    QTimer.singleShot(0, begin)
    try:
        code = run_gui([], services=given)
    finally:
        armed["on"] = False
        poll.stop()
        safety.stop()
        monkeypatch.setattr(ui, "create_main_window", real_create)
    if failure:
        raise failure[0]
    assert not pending, f"끝내지 못한 차례가 {len(pending)}개 남았다"
    return code, app


def data_root() -> Any:
    return resolve_app_paths().root
