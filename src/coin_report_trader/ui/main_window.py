"""창 하나와 다섯 화면의 자리, 그리고 조립(`app`)과의 연결점(`MainWindow.apply`)."""

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QScrollArea,
    QStackedWidget,
    QWidget,
)

from coin_report_trader.core import (
    ALERT_CATEGORY_TITLES,
    SAVE_TARGET_ALERT_DELETE,
    SAVE_TARGET_SETTINGS,
    SCREEN_TITLES,
    WINDOW_TITLE,
    AlertCategory,
    AlertsView,
    CandleUnit,
    CoinView,
    LoadState,
    MarketsView,
    SaveResult,
    ScreenId,
    SettingsView,
    UiActions,
    UiContext,
    UiUpdate,
    WebhookView,
)
from coin_report_trader.core.texts import (
    ERROR_BLOCKED,
    MARKETS_FAILED,
    MARKETS_LOADING,
    SETTINGS_UNREADABLE,
    blocked_markets_text,
    kst_when_text,
)
from coin_report_trader.ui.dialogs import ask_confirm
from coin_report_trader.ui.first_setup import FirstSetupScreen
from coin_report_trader.ui.screens import build_screen, screen_object_name
from coin_report_trader.ui.settings_screen import SettingsScreen
from coin_report_trader.ui.state import Message, Phase, PickerKind, PickerMode, Tone
from coin_report_trader.ui.status_screen import StatusScreen
from coin_report_trader.ui.texts import (
    ALERT_CLEAR_TITLE,
    ALERT_DELETE_BODY,
    ALERT_DELETE_TITLE,
    LOADING_TEXT,
    RETENTION_SHRINK_TITLE,
    SETTINGS_BEFORE_FIRST_SETUP,
    STATUS_BEFORE_FIRST_SETUP,
    WEBHOOK_DELETE_TITLE,
    alert_clear_body,
    retention_shrink_body,
    status_nav_text,
    webhook_delete_body,
)

WINDOW_NAME = "mainWindow"
NAV_NAME = "screenNav"
STACK_NAME = "screenStack"
STATUS_LINE_NAME = "statusLine"

SAVED_MESSAGE_MS = 3000
"""설정 화면의 "저장했습니다."가 보이는 시간(밀리초). 시험은 이 값을 줄여 쓴다."""

ConfirmHandler = Callable[[str, str], bool]
"""확인 창을 대신하는 함수. `(제목, 본문)`을 받아 "확인"이면 참, "취소"면 거짓을 돌려준다."""


SCROLL_PREFIX = "screenScroll_"
INITIAL_SIZE = (1280, 800)
"""창의 처음 크기(M2 명세 3.12.5). 쓸 수 있는 영역이 더 작으면 그 영역의 90%다."""
SMALL_SCREEN_RATIO = 0.9


def initial_size(
    available_width: int, available_height: int, frame_width: int = 0, frame_height: int = 0
) -> tuple[int, int]:
    """쓸 수 있는 영역의 크기로 창 안쪽(틀을 뺀)의 처음 크기를 정한다.

    틀(제목 줄과 테두리)의 크기를 넣은 창이 영역에 들어갈 때만 1280 × 800이고, 아니면 영역의 90%다.
    어느 쪽이든 틀을 넣은 창이 영역 안에 든다.
    """
    width, height = INITIAL_SIZE
    if available_width < width + frame_width or available_height < height + frame_height:
        width = min(int(available_width * SMALL_SCREEN_RATIO), available_width - frame_width)
        height = min(int(available_height * SMALL_SCREEN_RATIO), available_height - frame_height)
    return max(width, 1), max(height, 1)


def _scroll_page(screen_id: ScreenId, screen: QWidget) -> QScrollArea:
    """화면 하나를 세로로 스크롤되는 자리에 담는다. 내용은 제 높이보다 작게 눌리지 않는다."""
    page = QScrollArea()
    page.setObjectName(SCROLL_PREFIX + screen_id.value)
    page.setFrameShape(QScrollArea.Shape.NoFrame)
    page.setWidgetResizable(True)
    page.setWidget(screen)
    return page


def status_line_text(context: UiContext) -> str:
    return f"판 v{context.version} · 매매 기능 없음 · 자료 폴더 {context.data_dir_display}"


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
        self._coin_views: dict[str, CoinView] = {}
        self._alerts: AlertsView | None = None
        self._webhooks: dict[AlertCategory, WebhookView] = {}
        self._delete_message: Message | None = None
        self._status_was_active = False
        self._hold_seen = False
        self._markets_loading = False
        self._pending_saves: list[ScreenId] = []
        self._messages: dict[ScreenId, Message] = {}
        self._saved_message: Message | None = None
        """타이머가 지울 "저장했습니다." 글. 1 자리의 글이 이것일 때만 지운다."""
        self._showing: ScreenId | None = None

        self.setObjectName(WINDOW_NAME)
        self.setWindowTitle(WINDOW_TITLE)

        nav = QListWidget()
        nav.setObjectName(NAV_NAME)
        stack = QStackedWidget()
        stack.setObjectName(STACK_NAME)
        self._first_setup = FirstSetupScreen()
        self._settings = SettingsScreen()
        self._status = StatusScreen()
        for screen_id in ScreenId:
            nav.addItem(SCREEN_TITLES[screen_id])
            if screen_id is ScreenId.FIRST_SETUP:
                stack.addWidget(_scroll_page(screen_id, self._first_setup))
            elif screen_id is ScreenId.SETTINGS:
                stack.addWidget(_scroll_page(screen_id, self._settings))
            elif screen_id is ScreenId.STATUS:
                stack.addWidget(_scroll_page(screen_id, self._status))
            else:
                stack.addWidget(_scroll_page(screen_id, build_screen(screen_id)))
        nav.currentRowChanged.connect(stack.setCurrentIndex)
        nav.itemClicked.connect(self._on_nav_clicked)
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

        self._auto_size = QSize()
        self._fit_to_screen()
        self._frame_fitted = False

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
        self._status.retry_requested.connect(self._retry_candles)
        center = self._status.alert_center
        center.seen_requested.connect(self._on_seen)
        center.delete_alert_requested.connect(self._on_alert_delete)
        center.clear_requested.connect(self._on_alert_clear)
        center.save_webhook_requested.connect(self._on_webhook_save)
        center.delete_webhook_requested.connect(self._on_webhook_delete)
        center.send_test_requested.connect(self._on_send_test)
        stack.currentChanged.connect(self._on_screen_changed)

        self._update()

    def _fit_to_screen(self, frame: QSize | None = None) -> None:
        """처음 크기와 자리를 화면의 쓸 수 있는 영역 안에 둔다(M2 명세 3.12.5의 처음 크기).

        `frame`은 제목 줄과 테두리가 더하는 크기다. 창이 보이기 전에는 모르므로 비워 두고, 처음 보일
        때 다시 맞춘다. 자리(`move`)는 틀을 넣은 창의 왼쪽 위다.
        """
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        frame = frame if frame is not None else QSize(0, 0)
        area = screen.availableGeometry()
        width, height = initial_size(area.width(), area.height(), frame.width(), frame.height())
        self.resize(width, height)
        self._auto_size = QSize(width, height)
        self.move(
            area.x() + (area.width() - width - frame.width()) // 2,
            area.y() + (area.height() - height - frame.height()) // 2,
        )

    def showEvent(self, event) -> None:
        """처음 보일 때 틀의 크기를 읽어 처음 크기를 다시 맞춘다(사용자가 바꾼 크기는 두고)."""
        super().showEvent(event)
        if self._frame_fitted:
            return
        self._frame_fitted = True
        frame = self.frameGeometry().size() - self.geometry().size()
        if (frame.width() > 0 or frame.height() > 0) and self.size() == self._auto_size:
            self._fit_to_screen(frame)

    # --- 바깥에서 쓰는 것 ---

    def apply(self, update: UiUpdate) -> None:
        """조립이 보낸 값을 받는다. 이 화면이 다루지 않는 값은 아무 일도 하지 않는다."""
        if isinstance(update, SettingsView):
            self._on_settings_view(update)
        elif isinstance(update, MarketsView):
            self._on_markets_view(update)
        elif isinstance(update, CoinView):
            self._on_coin_view(update)
        elif isinstance(update, AlertsView):
            self._alerts = update
            self._update()
        elif isinstance(update, WebhookView):
            self._webhooks[update.category] = update
            self._update()
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
        key = (
            view.coins,
            view.retention_days,
            view.first_setup_done,
            view.candle_unit,
            view.candle_count,
        )
        old_key = (
            None
            if old is None
            else (
                old.coins,
                old.retention_days,
                old.first_setup_done,
                old.candle_unit,
                old.candle_count,
            )
        )
        if key != old_key:
            self._first_setup.load_saved(view.coins, view.retention_days)
            self._settings.load_saved(
                view.coins, view.retention_days, view.candle_unit, view.candle_count
            )
        # 고른 코인에 없는 코인의 마지막 `CoinView`는 버린다(다시 고르면 새 값이 올 때까지
        # "불러오는 중").
        self._coin_views = {m: v for m, v in self._coin_views.items() if m in view.coins}
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

    def _on_coin_view(self, view: CoinView) -> None:
        # 첫 `SettingsView`가 온 뒤에는 고른 코인의 값만 받는다. 그 전에 온 것은 두었다가
        # 그때 거른다.
        if self._saved is not None and view.market not in self._saved.coins:
            return
        self._coin_views[view.market] = view
        self._update()

    def _on_save_result(self, result: SaveResult) -> None:
        if result.target == SAVE_TARGET_ALERT_DELETE:
            # 지우기의 결과: 지우지 못했으면 알림센터 아래에 경고 색으로 보이고, 지웠으면 지운다.
            failed = not result.ok and bool(result.message)
            self._delete_message = Message(result.message, Tone.WARNING) if failed else None
            self._update()
            return
        if result.target != SAVE_TARGET_SETTINGS:
            return
        if self._pending_saves:
            origin = self._pending_saves.pop(0)
        else:
            origin = ScreenId.SETTINGS if self._first_setup_done() else ScreenId.FIRST_SETUP
        self._messages.pop(origin, None)
        self._saved_timer.stop()
        self._saved_message = None
        if result.ok:
            if origin is ScreenId.FIRST_SETUP:
                select_screen(self, ScreenId.STATUS)
            elif result.message:
                self._saved_message = Message(result.message, Tone.NORMAL)
                self._messages[origin] = self._saved_message
                self._saved_timer.start(SAVED_MESSAGE_MS)
        elif result.message:
            self._messages[origin] = Message(result.message, Tone.WARNING)
        self._update()

    def _clear_saved_message(self) -> None:
        # 타이머를 켠 "저장했습니다." 글만 지운다. 1 자리가 그 사이 경고 글로 바뀌었으면 둔다.
        shown = self._messages.get(ScreenId.SETTINGS)
        if shown is not None and shown is self._saved_message:
            del self._messages[ScreenId.SETTINGS]
        self._saved_message = None
        self._update()

    # --- 사용자의 동작 ---

    def _on_start(
        self, coins: tuple[str, ...], days: int, unit: CandleUnit | None, count: int | None
    ) -> None:
        self._submit(ScreenId.FIRST_SETUP, coins, days, unit, count)

    def _on_save(
        self, coins: tuple[str, ...], days: int, unit: CandleUnit | None, count: int | None
    ) -> None:
        saved_days = self._saved.retention_days if self._saved is not None else None
        # 저장된 값이 없을 때(처음 고를 때)와 같거나 길게 바꿀 때는 확인 창이 없다.
        shrinking = saved_days is not None and days < saved_days
        if shrinking and not self._ask_confirm(
            RETENTION_SHRINK_TITLE, retention_shrink_body(saved_days, days)
        ):
            return
        self._submit(ScreenId.SETTINGS, coins, days, unit, count)

    def _submit(
        self,
        origin: ScreenId,
        coins: tuple[str, ...],
        days: int,
        unit: CandleUnit | None,
        count: int | None,
    ) -> None:
        first_setup = origin is ScreenId.FIRST_SETUP
        if (
            self._actions is None
            or self._phase is not Phase.READY
            or first_setup is self._first_setup_done()
        ):
            return
        self._messages.pop(origin, None)
        if origin is ScreenId.SETTINGS:
            self._saved_timer.stop()
            self._saved_message = None
        self._pending_saves.append(origin)
        self._actions.save_settings(
            coins, days, first_setup=first_setup, candle_unit=unit, candle_count=count
        )
        self._update()

    def _on_nav_clicked(self, item) -> None:
        """사용자가 화면 이름 목록의 줄을 눌렀다. 현황이면 보지 않은 알림이 있는 맨 앞 탭을 연다."""
        if self._nav.row(item) == list(ScreenId).index(ScreenId.STATUS):
            # 누르는 동안 미룬 본 알림 알림은 열릴 탭이 정해진 뒤에 푼다.
            self._hold_seen = False
            self._status.alert_center.select_first_unseen_tab()

    def _on_seen(self, ids: object) -> None:
        if self._actions is not None:
            self._actions.mark_seen(tuple(ids))  # type: ignore[arg-type]

    def _on_alert_delete(self, alert_id: int) -> None:
        if self._actions is None:
            return
        if self._ask_confirm(ALERT_DELETE_TITLE, ALERT_DELETE_BODY):
            self._delete_message = None
            self._actions.delete_alert(alert_id)
            self._update()

    def _on_alert_clear(self, category: AlertCategory, count: int) -> None:
        if self._actions is None:
            return
        title = ALERT_CATEGORY_TITLES[category]
        if self._ask_confirm(ALERT_CLEAR_TITLE, alert_clear_body(title, count)):
            self._delete_message = None
            self._actions.clear_alerts(category)
            self._update()

    def _on_webhook_save(self, category: AlertCategory, url: str) -> None:
        if self._actions is not None:
            self._actions.save_webhook(category, url)

    def _on_webhook_delete(self, category: AlertCategory) -> None:
        if self._actions is None:
            return
        title = ALERT_CATEGORY_TITLES[category]
        if self._ask_confirm(WEBHOOK_DELETE_TITLE, webhook_delete_body(title)):
            self._status.alert_center.mark_delete_requested(category)
            self._actions.delete_webhook(category)

    def _on_send_test(self, category: AlertCategory) -> None:
        if self._actions is not None:
            self._actions.send_test(category)

    def _retry_markets(self) -> None:
        self._request_markets()
        self._update()

    def _retry_candles(self) -> None:
        # 첫 `SettingsView` 전, 처음 설정 전, 설정을 읽지 못했을 때는 단추가 보이지 않고
        # 부르지 않는다.
        if self._actions is None or self._phase is not Phase.READY or not self._first_setup_done():
            return
        self._actions.retry_candles()

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

    def _legacy_record(self) -> bool:
        """예전 기록: 첫 `SettingsView`가 `READY`이고 처음 설정을 마치지 않았는데 코인이 있다
        (M1 명세 3.14.4)."""
        return (
            self._phase is Phase.READY
            and self._saved is not None
            and not self._saved.first_setup_done
            and bool(self._saved.coins)
        )

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
                return blocked_markets_text(kst_when_text(view.blocked_until, view.as_of))
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

    def _status_message(self) -> Message | None:
        if self._phase is Phase.WAITING:
            return Message(LOADING_TEXT)
        if self._phase is Phase.UNREADABLE:
            return Message(SETTINGS_UNREADABLE, Tone.WARNING)
        if not self._first_setup_done():
            return Message(STATUS_BEFORE_FIRST_SETUP)
        return None

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
            phase=self._phase,
            done=done,
            picker=mode,
            message=self._first_setup_message(),
            legacy=self._legacy_record(),
        )
        self._settings.render(
            phase=self._phase, done=done, picker=mode, message=self._settings_message()
        )
        self._status.render(
            message=self._status_message(),
            coins=self._saved.coins if self._saved is not None else (),
            views=self._coin_views,
        )
        status_index = list(ScreenId).index(ScreenId.STATUS)
        unseen = sum(self._alerts.unseen.values()) if self._alerts is not None else 0
        self._nav.item(status_index).setText(
            status_nav_text(SCREEN_TITLES[ScreenId.STATUS], unseen)
        )
        active = current_screen(self) is ScreenId.STATUS
        pressed = bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton)
        if active and not self._status_was_active and pressed:
            # 사용자가 현황을 누르는 중이다. 손을 뗄 때(`itemClicked`) 열릴 탭이 정해지므로,
            # 그 전에 이전에 고른 탭의 줄을 본 것으로 알리지 않는다.
            self._hold_seen = True
        elif self._hold_seen and not pressed:
            self._hold_seen = False  # 다른 곳에서 손을 뗐다(`itemClicked`가 오지 않는다).
        self._status_was_active = active
        self._status.alert_center.render(
            alerts=self._alerts,
            webhooks=self._webhooks,
            active=active,
            delete_message=self._delete_message,
            hold_seen=self._hold_seen,
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
      보고서, 기록 자리에는 빈 화면 문구 라벨 `screenBody`가 있다(처음 설정, 현황, 설정에는 없다).
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
      `firstSetupLegacyNotice`(예전 기록일 때만 보임. 그 아래 2 자리),
      `screenNotice`(10의 가), `noticeDiscordOffline`(마), `noticeDiscordNoAddress`(바),
      `firstSetupDone`(처음 설정을 마친 뒤의 글).
    - 묶음: `firstSetupForm`(칸 전체), `firstSetupNotices`, `firstSetupDonePanel`.
    - 단추: `firstSetupStart`("저장하고 시작"), `firstSetupToSettings`("설정 화면으로").
    - 코인 고르기(`firstSetupCoins`, 찾기 칸 `firstSetupCoinSearch`), 봉 단위와 봉 개수(라디오
      버튼 `firstSetupUnit_<봉 단위 값>`, `firstSetupCount_<수>`),
      보관 기간(`firstSetupRetention`)의 안쪽 이름은 `coin_picker.py`, `candle_choice.py`,
      `retention.py`의 문서 문자열에 있다. 앞이 `firstSetup`이다.

    설정 자리(`screen_settings`)의 `objectName`:

    - 글 라벨: `settingsMessage`(1 자리), 단추: `settingsSave`("저장"), 묶음: `settingsForm`.
    - 코인 고르기(찾기 칸 `settingsCoinSearch`), 봉 단위(`settingsUnit_<봉 단위 값>`)와 봉 개수
      (`settingsCount_<수>`), 보관 기간의 안쪽 이름은 앞이 `settings`다.

    현황 자리(`screen_status`)의 `objectName`(`<m>`은 마켓 코드, 자세한 것은 `status_screen.py`):

    - 글 하나만 보이는 모습(설정을 불러오는 중, 처음 설정 전, 설정을 읽지 못함): 라벨
      `statusMessage`가 보이고 묶음 `statusContent`가 숨겨진다.
      설정을 읽지 못함은 `tone`이 "warning"이다.
    - 코인 칸: 묶음 `statusCoins` 안에 고른 코인마다 `statusCoin_<m>`(설정 목록 순서, 최대 셋).
      그 안에 `statusCoinName_<m>`, 값 자리의 글 `statusCoinNote_<m>`(글이 없으면 숨김),
      "다시 시도" `statusCoinRetry_<m>`(받지 못함과 일시 차단 중에만 보임),
      `statusCoinIncomplete_<m>`(미완성 봉 글), `statusCoinDisclaimer_<m>`(보장 아님 글).
    - 봉 통계(`CoinView.stats`. M1 명세 3.14.5): 묶음 `statusStats`, 코인 고르기
      `statusStatsCoin`(`QComboBox`), 표 자리의 글 `statusStatsNote`, 표 위 글 `statusStatsSummary`,
      표 묶음 `statusStatsTable`(머리 `statusStatsHeader_<0~1>`,
      칸 `statusStatsCell_<줄 0~12>_<칸 0~1>`. 칸 0은 항목, 칸 1은 값),
      보장 아님 글 `statusStatsDisclaimer`, `statusStatsFetched`("마지막으로 받은 때" 줄),
      `statusStatsIncomplete`, 표 아래의 하나뿐인 "다시 시도" `statusStatsRetry`.
      코인 칸 안은 `statusCoinStats_<m>`(묶음. 흐리게 남기면 `isEnabled()`가 거짓),
      `statusCoinStatsHeader_<m>`(첫 줄), `statusCoinStatsRow_<m>_<0~4>`("이름 값").
    - 흐리게 남긴 값(받기 실패, 일시 차단 중의 받아 둔 값)은 그 묶음(`statusStatsTable`,
      `statusCoinStats_<m>`)의 `isEnabled()`가 거짓이다.
      표의 줄과 요소를 그리지 않는 모습은
      그 묶음이 숨겨져 있다(`isHidden()`).
    - 알림센터(현황 자리 안, `alert_center.py`에 모든 `objectName`이 있다): 묶음 `alertCenter`,
      탭 `alertTab_<분류 값>`(여섯, `AlertCategory` 차례. 글은 "이름"이나 "이름 ({n})"이고
      골라진 탭은
      `font().bold()`와 `font().underline()`이 참), 줄 `alertRow_<id>`(골라진 탭의 줄만 있다.
      음수 id도 그대로), 줄 글 `alertLine_<id>`(보지 않은 줄은 "● "로 시작하고 `font().bold()`가 참.
      누르려면 `QTest.mouseClick`), `alertDelete_<id>`, `alertDiscordFailed_<id>`(경고 색),
      펼친 글 `alertDetail_<id>`, 목록 자리의 글 `alertListMessage`,
      "이 탭 모두 지우기" `alertClear`,
      주소 칸 `alertWebhookInput`(점으로 보임)과 단추 `alertWebhookSave`, `alertWebhookChange`,
      `alertWebhookDelete`, `alertWebhookCancel`, `alertWebhookTest`, 칸 아래 글
      `alertWebhookMessage`와 `alertWebhookTestMessage`, 지우기 결과 글 `alertDeleteMessage`.
      주소가 저장된 분류의 칸은 비어 있고 `placeholderText()`가 "(가려서 보임) 저장됨"이다.
      흐린 것은 `isEnabled()`가 거짓이다. 주소는 `UiActions.save_webhook`의 인자로만 창을 떠난다.
    - 화면 이름 목록의 현황 줄: `screenNav`의 `ScreenId` 차례의 줄(`item(1)`)의 글이 "현황"이나
      "현황 ({n})"이다. 사용자가 그 줄을 누른 것은 `screenNav.itemClicked`로 알린다(시험은
      `itemClicked.emit(item)`). `select_screen`은 누른 것이 아니라서 마지막에 고른 탭을
      그대로 둔다.
    - 현황 자리의 "다시 시도"는 `retry_candles()`를 부른다. 처음 설정을 마치고 설정을 읽은 뒤에만
      불리고, 그 밖의 때에는 단추가 보이지 않는다.
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
        ScreenId(_screen_of(stack.widget(i)).objectName().removeprefix(prefix))
        for i in range(stack.count())
    )


def _screen_of(page: QWidget) -> QWidget:
    """스크롤 자리 안의 화면(스크롤 자리가 아니면 그 위젯)."""
    if isinstance(page, QScrollArea):
        inner = page.widget()
        if inner is not None:
            return inner
    return page


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
