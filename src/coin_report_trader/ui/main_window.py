"""창 하나와 다섯 화면의 자리."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QStackedWidget,
    QWidget,
)

from coin_report_trader.core import SCREEN_TITLES, WINDOW_TITLE, ScreenId, UiContext
from coin_report_trader.ui.screens import build_screen, screen_object_name

WINDOW_NAME = "mainWindow"
NAV_NAME = "screenNav"
STACK_NAME = "screenStack"
STATUS_LINE_NAME = "statusLine"


def status_line_text(context: UiContext) -> str:
    return f"바탕 버전 {context.version} · 매매 기능 없음 · 자료 폴더 {context.data_dir_display}"


def create_main_window(context: UiContext) -> QMainWindow:
    """창을 만들어 돌려준다. 띄우지(`show`) 않는다.

    부르기 전에 `QApplication`이 있어야 한다. 이 함수는 `QApplication`을 만들지 않고,
    파일, DB, 네트워크를 쓰지 않는다.

    바깥(`--self-test`, 수용 시험)에서 읽는 방법:

    - 창: `objectName`이 `mainWindow`, 제목이 `WINDOW_TITLE`.
    - 자리의 순서: `screen_order(window)`. `ScreenId` 순서의 튜플이다.
    - 자리 위젯: `screen_widget(window, screen_id)`. `objectName`은 `screen_<ScreenId 값>`이다.
      자리 안의 글은 `screenTitle`(화면 이름), `screenBody`(빈 화면 문구) 라벨이고,
      처음 설정 자리에는 `screenNotice`(투자 자문 문구) 라벨이 더 있다.
    - 지금 보이는 자리: `current_screen(window)`. 켰을 때는 `ScreenId.FIRST_SETUP`이다.
    - 상태 줄: `statusLine` 라벨의 글.
    - 자리를 바꾸는 방법: `screenNav` 목록에서 고르거나 `select_screen(window, screen_id)`.
    """
    window = QMainWindow()
    window.setObjectName(WINDOW_NAME)
    window.setWindowTitle(WINDOW_TITLE)

    nav = QListWidget()
    nav.setObjectName(NAV_NAME)
    stack = QStackedWidget()
    stack.setObjectName(STACK_NAME)
    for screen_id in ScreenId:
        nav.addItem(SCREEN_TITLES[screen_id])
        stack.addWidget(build_screen(screen_id))
    nav.currentRowChanged.connect(stack.setCurrentIndex)
    nav.setCurrentRow(0)
    nav.setMaximumWidth(160)

    central = QWidget()
    layout = QHBoxLayout(central)
    layout.addWidget(nav)
    layout.addWidget(stack, 1)
    window.setCentralWidget(central)

    status = QLabel(status_line_text(context))
    status.setObjectName(STATUS_LINE_NAME)
    status.setTextFormat(Qt.TextFormat.PlainText)
    window.statusBar().addWidget(status, 1)
    return window


def _stack(window: QMainWindow) -> QStackedWidget:
    stack = window.findChild(QStackedWidget, STACK_NAME)
    if stack is None:
        raise LookupError("화면 자리 묶음이 없는 창이다")
    return stack


def screen_order(window: QMainWindow) -> tuple[ScreenId, ...]:
    """자리의 순서. `objectName`을 읽어 `ScreenId`로 돌려준다."""
    stack = _stack(window)
    prefix = screen_object_name(ScreenId.FIRST_SETUP).removesuffix(ScreenId.FIRST_SETUP.value)
    return tuple(
        ScreenId(stack.widget(i).objectName().removeprefix(prefix)) for i in range(stack.count())
    )


def screen_widget(window: QMainWindow, screen_id: ScreenId) -> QWidget:
    widget = window.findChild(QWidget, screen_object_name(screen_id))
    if widget is None:
        raise LookupError(f"{screen_id.value} 자리가 없다")
    return widget


def current_screen(window: QMainWindow) -> ScreenId:
    """지금 보이는 자리(창을 띄우지 않았어도 현재 고른 자리)."""
    return screen_order(window)[_stack(window).currentIndex()]


def select_screen(window: QMainWindow, screen_id: ScreenId) -> None:
    nav = window.findChild(QListWidget, NAV_NAME)
    if nav is None:
        raise LookupError("화면 이름 목록이 없는 창이다")
    nav.setCurrentRow(list(ScreenId).index(screen_id))
