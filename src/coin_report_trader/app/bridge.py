"""자료 스레드가 화면에 `UiUpdate`를 보내는 다리(M1 명세 3.8의 "화면에 보내기").

스레드는 `post`로 값을 넣고, 다리가 Qt의 주 스레드에서 `apply`를 부른다(시그널의 큐 연결).
화면과 이 다리 말고는 Qt 객체를 다른 스레드에서 만지지 않는다. 끝낼 때 `close`를 부르면 그 뒤에
넣은 값과 아직 오지 않은 값은 버린다(10절의 "종료 때는 아무 정리도 기대하지 않는다").
"""

import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, Signal, Slot

from coin_report_trader.core import UiUpdate


class UiBridge(QObject):
    """주 스레드에서 만든다. `post`는 어느 스레드에서나 부른다."""

    _updated = Signal(object)

    def __init__(self, apply: Callable[[UiUpdate], None] | None = None) -> None:
        super().__init__()
        self._apply = apply
        self._lock = threading.Lock()
        self._open = True
        self._updated.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def set_apply(self, apply: Callable[[UiUpdate], None]) -> None:
        """값을 받을 함수(`MainWindow.apply`). 주 스레드에서 정한다."""
        self._apply = apply

    def release(self) -> None:
        """끝낼 때(주 스레드) 닫고, 값을 받을 함수를 놓고, 주 스레드에서 지우도록 맡긴다.

        다리를 붙든 자료 스레드와 창은 서로를 붙든 순환 참조라 가비지 수집이 지운다. 그 수집이 다른
        스레드에서 돌면 이 `QObject`가 그 스레드에서 지워져 Windows에서 `access violation`으로 죽을
        수 있다. `deleteLater`로 C++ 쪽을 주 스레드가 지우게 하면 그 뒤의 수집은 빈 껍데기만 만진다.
        """
        self.close()
        self._apply = None
        self.deleteLater()

    def post(self, update: UiUpdate) -> None:
        with self._lock:
            if self._open:
                self._updated.emit(update)

    def close(self) -> None:
        with self._lock:
            self._open = False

    @Slot(object)
    def _deliver(self, update: object) -> None:
        if self._open and self._apply is not None:
            self._apply(update)  # type: ignore[arg-type]
