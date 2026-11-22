"""창을 띄운 실행이 끝난 뒤 Qt 객체가 순환 참조로 남지 않는다(hotfix-gc).

창, `UiBridge`, 동작, 자료 스레드는 서로를 붙든다. 이 고리가 남으면 참조 수로는 지워지지 않아
나중의 가비지 수집이 지우는데, 그 수집이 자료 스레드에서 돌면 Qt 객체가 주 스레드 밖에서 지워져
Windows에서 `access violation`으로 죽을 수 있다(창을 띄우는 시험이 가끔 죽던 까닭).
`run_gui`는 끝낼 때 주 스레드에서 고리를 끊고 수집한다.
"""

import gc
import threading

import pytest
import shiboken6
from acceptance.test_m1_b_window_run import services
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication

from coin_report_trader.app import run_gui
from coin_report_trader.app.bridge import UiBridge
from coin_report_trader.core import CoinView, LoadState
from coin_report_trader.ui import MainWindow

RUN_MILLISECONDS = 300


def _qt_objects() -> list[object]:
    """수집을 부르지 않고 지금 살아 있는 창과 다리(수집 전의 쓰레기도 들어 있다)."""
    return [obj for obj in gc.get_objects() if isinstance(obj, (UiBridge, MainWindow))]


@pytest.fixture
def collect_off(qapp: QApplication):
    gc.collect()
    gc.disable()
    try:
        yield
    finally:
        gc.enable()
        gc.collect()


def test_a_run_leaves_no_window_or_bridge_for_a_later_collection(
    qapp: QApplication, collect_off: None
) -> None:
    before = len(_qt_objects())
    QTimer.singleShot(RUN_MILLISECONDS, QApplication.quit)
    assert run_gui([], services=services()) == 0
    # 가비지 수집을 끈 채로도 이 실행의 창과 다리는 이미 지워졌다(남아 있으면 순환 참조다).
    assert len(_qt_objects()) <= before


def test_a_collection_in_another_thread_after_a_run_destroys_no_bridge_there(
    qapp: QApplication, collect_off: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    destroyed_in: list[str] = []
    real_init = UiBridge.__init__

    def watched(self: UiBridge, *args: object, **kwargs: object) -> None:
        real_init(self, *args, **kwargs)
        # `destroyed`는 C++ 쪽을 지우는 스레드에서 난다.
        self.destroyed.connect(lambda *_: destroyed_in.append(threading.current_thread().name))

    monkeypatch.setattr(UiBridge, "__init__", watched)
    QTimer.singleShot(RUN_MILLISECONDS, QApplication.quit)
    assert run_gui([], services=services()) == 0
    stand_in = threading.Thread(target=gc.collect, name="data-thread-stand-in")
    stand_in.start()
    stand_in.join()
    qapp.processEvents()
    assert destroyed_in == [threading.main_thread().name]  # 다리는 주 스레드에서 지워졌다


def test_release_closes_the_bridge_drops_its_function_and_deletes_it_on_the_main_thread(
    qapp: QApplication,
) -> None:
    seen: list[object] = []
    bridge = UiBridge(seen.append)
    bridge.release()
    bridge.post(CoinView("KRW-AAA", LoadState.LOADING))
    qapp.processEvents()
    assert seen == []
    assert bridge._apply is None  # 창을 붙들던 고리를 놓았다
    assert shiboken6.isValid(bridge)  # 지우기는 주 스레드의 이벤트가 올 때다
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(bridge)
