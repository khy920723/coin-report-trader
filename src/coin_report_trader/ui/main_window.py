"""창 하나와 다섯 화면의 자리, 그리고 조립(`app`)과의 연결점(`MainWindow.apply`)."""

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QStackedWidget,
    QWidget,
)

from coin_report_trader.core import (
    SAVE_TARGET_SETTINGS,
    SCREEN_TITLES,
    WINDOW_TITLE,
    LoadState,
    MarketsView,
    SaveResult,
    ScreenId,
    SettingsView,
    UiActions,
    UiContext,
    UiUpdate,
)
from coin_report_trader.core.texts import (
    ERROR_BLOCKED,
    MARKETS_FAILED,
    MARKETS_LOADING,
    SETTINGS_UNREADABLE,
    blocked_markets_text,
    kst_time_text,
)
from coin_report_trader.ui.dialogs import ask_confirm
from coin_report_trader.ui.first_setup import FirstSetupScreen
from coin_report_trader.ui.screens import build_screen, screen_object_name
from coin_report_trader.ui.settings_screen import SettingsScreen
from coin_report_trader.ui.state import Message, Phase, PickerKind, PickerMode, Tone
from coin_report_trader.ui.texts import (
    LOADING_TEXT,
    RETENTION_SHRINK_TITLE,
    SETTINGS_BEFORE_FIRST_SETUP,
    retention_shrink_body,
)

WINDOW_NAME = "mainWindow"
NAV_NAME = "screenNav"
STACK_NAME = "screenStack"
STATUS_LINE_NAME = "statusLine"

SAVED_MESSAGE_MS = 3000
"""설정 화면의 "저장했습니다."가 보이는 시간(밀리초). 시험은 이 값을 줄여 쓴다."""

ConfirmHandler = Callable[[str, str], bool]
"""확인 창을 대신하는 함수. `(제목, 본문)`을 받아 "확인"이면 참, "취소"면 거짓을 돌려준다."""


def status_line_text(context: UiContext) -> str:
    return f"바탕 버전 {context.version} · 매매 기능 없음 · 자료 폴더 {context.data_dir_display}"


class MainWindow(QMainWindow):
    """창. 조립이 만든 값은 `apply`로 받고, 사용자의 동작은 `context.actions`로만 부른다.

    `actions`가 `None`이면 어느 동작도 부르지 않는다. `apply`는 Qt의 주 스레드에서만 부른다.
    """

    def __init__(self, context: UiContext) -> None:
        super().__init__()
        self._actions: UiActions | None = context.actions
        self._confirm: ConfirmHandler | None = None

        # 조립이 보낸 것을 모아 둔 값. 화면은 이 값에서 매번 새로 그린다.
        self._phase = Phase.READY if self._actions is None else Phase.WAITING
        self._saved: SettingsView | None = None
        self._first_settings_handled = False
        self._markets: MarketsView | None = None
        self._markets_loading = False
        self._pending_saves: list[ScreenId] = []
        self._messages: dict[ScreenId, Message] = {}
        self._showing: ScreenId | None = None

        self.setObjectName(WINDOW_NAME)
        self.setWindowTitle(WINDOW_TITLE)

        nav = QListWidget()
        nav.setObjectName(NAV_NAME)
        stack = QStackedWidget()
        stack.setObjectName(STACK_NAME)
        self._first_setup = FirstSetupScreen()
        self._settings = SettingsScreen()
        for screen_id in ScreenId:
            nav.addItem(SCREEN_TITLES[screen_id])
            if screen_id is ScreenId.FIRST_SETUP:
                stack.addWidget(self._first_setup)
            elif screen_id is ScreenId.SETTINGS:
                stack.addWidget(self._settings)
            else:
                stack.addWidget(build_screen(screen_id))
        nav.currentRowChanged.connect(stack.setCurrentIndex)
        nav.setCurrentRow(0)
        nav.setMaximumWidth(160)
        self._nav = nav
        self._stack = stack

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.addWidget(nav)
        layout.addWidget(stack, 1)
        self.setCentralWidget(central)

        status = QLabel(status_line_text(context))
        status.setObjectName(STATUS_LINE_NAME)
        status.setTextFormat(Qt.TextFormat.PlainText)
        self.statusBar().addWidget(status, 1)

        self._saved_timer = QTimer(self)
        self._saved_timer.setSingleShot(True)
        self._saved_timer.timeout.connect(self._clear_saved_message)

        self._first_setup.start_requested.connect(self._on_start)
        self._first_setup.retry_requested.connect(self._retry_markets)
        self._first_setup.open_settings_requested.connect(
            lambda: select_screen(self, ScreenId.SETTINGS)
        )
        self._settings.save_requested.connect(self._on_save)
        self._settings.retry_requested.connect(self._retry_markets)
        stack.currentChanged.connect(self._on_screen_changed)

        self._update()

    # --- 바깥에서 쓰는 것 ---

    def apply(self, update: UiUpdate) -> None:
        """조립이 보낸 값을 받는다. 이 화면이 다루지 않는 값은 아무 일도 하지 않는다."""
        if isinstance(update, SettingsView):
            self._on_settings_view(update)
        elif isinstance(update, MarketsView):
            self._on_markets_view(update)
        elif isinstance(update, SaveResult):
            self._on_save_result(update)

    def set_confirm_handler(self, handler: ConfirmHandler | None) -> None:
        """확인 창 대신 `handler(제목, 본문)`을 부르게 한다. `None`이면 진짜 확인 창을 띄운다.

        시험이 모달 창에서 멈추지 않고 "확인"이나 "취소"를 고르는 길이다.
        """
        self._confirm = handler

    # --- 조립이 보낸 값 ---

    def _on_settings_view(self, view: SettingsView) -> None:
        if view.state is LoadState.FAILED:
            # 읽은 적이 있으면 마지막으로 읽은 값을 그대로 둔다. 켤 때 읽지 못한 때만 이 모습이다.
            if self._saved is None:
                self._phase = Phase.UNREADABLE
                self._update()
            return
        old = self._saved
        self._saved = view
        self._phase = Phase.READY
        key = (view.coins, view.retention_days, view.first_setup_done)
        if old is None or key != (old.coins, old.retention_days, old.first_setup_done):
            self._first_setup.load_saved(view.coins, view.retention_days)
            self._settings.load_saved(view.coins, view.retention_days)
        if not self._first_settings_handled:
            self._first_settings_handled = True
            if view.first_setup_done:
                select_screen(self, ScreenId.STATUS)
        elif view.first_setup_done and ScreenId.FIRST_SETUP in self._pending_saves:
            select_screen(self, ScreenId.STATUS)
        self._update()

    def _on_markets_view(self, view: MarketsView) -> None:
        self._markets = view
        if view.state is not LoadState.LOADING:
            self._markets_loading = False
        if view.state is LoadState.READY:
            self._first_setup.set_markets(view.markets)
            self._settings.set_markets(view.markets)
        self._update()

    def _on_save_result(self, result: SaveResult) -> None:
        if result.target != SAVE_TARGET_SETTINGS:
            return
        if self._pending_saves:
            origin = self._pending_saves.pop(0)
        else:
            origin = ScreenId.SETTINGS if self._first_setup_done() else ScreenId.FIRST_SETUP
        self._messages.pop(origin, None)
        if result.ok:
            if origin is ScreenId.FIRST_SETUP:
                select_screen(self, ScreenId.STATUS)
            elif result.message:
                self._messages[origin] = Message(result.message, Tone.NORMAL)
                self._saved_timer.start(SAVED_MESSAGE_MS)
        elif result.message:
            self._messages[origin] = Message(result.message, Tone.WARNING)
        self._update()

    def _clear_saved_message(self) -> None:
        self._messages.pop(ScreenId.SETTINGS, None)
        self._update()

    # --- 사용자의 동작 ---

    def _on_start(self, coins: tuple[str, ...], days: int) -> None:
        self._submit(ScreenId.FIRST_SETUP, coins, days)

    def _on_save(self, coins: tuple[str, ...], days: int) -> None:
        saved_days = self._saved.retention_days if self._saved is not None else None
        # 저장된 값이 없을 때(처음 고를 때)와 같거나 길게 바꿀 때는 확인 창이 없다.
        shrinking = saved_days is not None and days < saved_days
        if shrinking and not self._ask_confirm(
            RETENTION_SHRINK_TITLE, retention_shrink_body(saved_days, days)
        ):
            return
        self._submit(ScreenId.SETTINGS, coins, days)

    def _submit(self, origin: ScreenId, coins: tuple[str, ...], days: int) -> None:
        first_setup = origin is ScreenId.FIRST_SETUP
        if (
            self._actions is None
            or self._phase is not Phase.READY
            or first_setup is self._first_setup_done()
        ):
            return
        self._messages.pop(origin, None)
        self._pending_saves.append(origin)
        self._actions.save_settings(coins, days, first_setup=first_setup)
        self._update()

    def _retry_markets(self) -> None:
        self._request_markets()
        self._update()

    def _ask_confirm(self, title: str, body: str) -> bool:
        if self._confirm is not None:
            return self._confirm(title, body)
        return ask_confirm(self, title, body)

    def _request_markets(self) -> None:
        if self._actions is None:
            return
        self._markets_loading = True
        self._actions.refresh_markets()

    def _on_screen_changed(self, _index: int) -> None:
        self._update()

    # --- 그리기 ---

    def _first_setup_done(self) -> bool:
        return self._saved is not None and self._saved.first_setup_done

    def _showing_picker(self) -> ScreenId | None:
        """코인 고르기가 지금 보이는 화면. 없으면 `None`."""
        if self._phase is not Phase.READY:
            return None
        current = current_screen(self)
        done = self._first_setup_done()
        if current is ScreenId.FIRST_SETUP and not done:
            return current
        if current is ScreenId.SETTINGS and done:
            return current
        return None

    def _picker_mode(self) -> PickerMode:
        if self._phase is not Phase.READY:
            return PickerMode(PickerKind.IDLE)
        view = self._markets
        if self._markets_loading or (view is not None and view.state is LoadState.LOADING):
            return PickerMode(PickerKind.LOADING, MARKETS_LOADING)
        if view is None:
            return PickerMode(PickerKind.IDLE)
        if view.state is LoadState.READY:
            return PickerMode(PickerKind.READY)
        return PickerMode(PickerKind.FAILED, self._failed_text(view), retry=True)

    @staticmethod
    def _failed_text(view: MarketsView) -> str:
        if view.error_name == ERROR_BLOCKED and view.blocked_until is not None:
            try:
                return blocked_markets_text(kst_time_text(view.blocked_until))
            except ValueError:
                return MARKETS_FAILED
        return MARKETS_FAILED

    def _first_setup_message(self) -> Message | None:
        if self._phase is Phase.WAITING:
            return Message(LOADING_TEXT)
        if self._phase is Phase.UNREADABLE:
            return Message(SETTINGS_UNREADABLE, Tone.WARNING)
        return self._messages.get(ScreenId.FIRST_SETUP)

    def _settings_message(self) -> Message | None:
        if self._phase is Phase.WAITING:
            return Message(LOADING_TEXT)
        if self._phase is Phase.UNREADABLE:
            return Message(SETTINGS_UNREADABLE, Tone.WARNING)
        if not self._first_setup_done():
            return Message(SETTINGS_BEFORE_FIRST_SETUP)
        return self._messages.get(ScreenId.SETTINGS)

    def _update(self) -> None:
        showing = self._showing_picker()
        if showing is not None and showing != self._showing:
            # 코인 고르기가 보이게 된 때(화면을 열었거나, 설정을 읽어 처음 설정이 보이게 된 때).
            self._showing = showing
            self._request_markets()
        else:
            self._showing = showing
        done = self._first_setup_done()
        mode = self._picker_mode()
        self._first_setup.render(
            phase=self._phase, done=done, picker=mode, message=self._first_setup_message()
        )
        self._settings.render(
            phase=self._phase, done=done, picker=mode, message=self._settings_message()
        )


def create_main_window(context: UiContext) -> MainWindow:
    """창을 만들어 돌려준다. 띄우지(`show`) 않는다.

    부르기 전에 `QApplication`이 있어야 한다. 이 함수는 `QApplication`을 만들지 않고,
    파일, DB, 네트워크를 쓰지 않는다. 동작은 `context.actions`로만 부르고(`None`이면 부르지 않는다),
    결과는 `MainWindow.apply`로만 받는다. 스레드를 만들지 않는다.

    바깥(`--self-test`, 수용 시험)에서 읽고 움직이는 방법:

    - 창: `objectName`이 `mainWindow`, 제목이 `WINDOW_TITLE`, 상태 줄은 `statusLine` 라벨.
    - 자리의 순서: `screen_order(window)`. `ScreenId` 순서의 튜플이다.
    - 자리 위젯: `screen_widget(window, screen_id)`. `objectName`은 `screen_<ScreenId 값>`이다.
      다섯 자리 모두 제목 라벨 `screenTitle`(글은 `SCREEN_TITLES`)이 있다.
      현황, 보고서, 기록 자리에는 빈 화면 문구 라벨 `screenBody`가 있다.
    - 지금 보이는 자리: `current_screen(window)`. 켰을 때는 `ScreenId.FIRST_SETUP`이다.
      자리를 바꾸려면 `select_screen(window, screen_id)`나 `screenNav` 목록에서 고른다.
    - 경고 색: 글 라벨의 동적 속성 `tone`("normal" 또는 "warning")으로 알아본다
      (`coin_report_trader.ui.widgets.TONE_PROPERTY`). 글이 없는 라벨은 숨겨져 있다(`isHidden()`).
    - 확인 창: `window.set_confirm_handler(handler)`로 `handler(제목, 본문) -> bool`을 걸면 창을
      띄우지 않고 그 값("확인"이면 참)으로 답한다. 걸지 않으면 진짜 확인 창이 뜬다. 창의 모양은
      `coin_report_trader.ui.dialogs.build_confirm_box`가 띄우지 않고 만들어 주며, 버튼은
      `confirmOk`("확인"), `confirmCancel`("취소", 기본 버튼)이다.

    처음 설정 자리(`screen_first_setup`)의 `objectName`:

    - 글 라벨: `firstSetupMessage`(2 자리. 불러오는 중, 설정을 읽지 못함, 저장 거절·실패),
      `screenNotice`(10의 가), `noticeDiscordOffline`(마), `noticeDiscordNoAddress`(바),
      `firstSetupDone`(처음 설정을 마친 뒤의 글).
    - 묶음: `firstSetupForm`(칸 전체), `firstSetupNotices`, `firstSetupDonePanel`.
    - 단추: `firstSetupStart`("저장하고 시작"), `firstSetupToSettings`("설정 화면으로").
    - 코인 고르기(`firstSetupCoins`), 보관 기간(`firstSetupRetention`)의 안쪽 이름은
      `coin_picker.py`, `retention.py`의 문서 문자열에 있다. 앞이 `firstSetup`이다.

    설정 자리(`screen_settings`)의 `objectName`:

    - 글 라벨: `settingsMessage`(1 자리), 단추: `settingsSave`("저장"), 묶음: `settingsForm`.
    - 코인 고르기와 보관 기간의 안쪽 이름은 앞이 `settings`다.
    """
    return MainWindow(context)


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
