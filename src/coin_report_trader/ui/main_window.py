"""창 하나와 여섯 화면의 자리, 그리고 조립(`app`)과의 연결점(`MainWindow.apply`)."""

from collections.abc import Callable
from datetime import date, datetime

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from coin_report_trader.core import (
    ALERT_CATEGORY_TITLES,
    KST,
    SAVE_TARGET_ALERT_DELETE,
    SAVE_TARGET_EXPORT,
    SAVE_TARGET_REPORT,
    SAVE_TARGET_SETTINGS,
    SCREEN_TITLES,
    WINDOW_TITLE,
    AccountState,
    AccountView,
    AiChoice,
    AiStatusView,
    AlertCategory,
    AlertsView,
    AlertView,
    BelowStopView,
    CandleUnit,
    CoinTradeView,
    CoinView,
    HistoryTab,
    HistoryView,
    LiveConfirmView,
    LiveSettings,
    LiveStatusView,
    LoadState,
    MarketsView,
    Mode,
    PerformanceView,
    ReportListView,
    ReportView,
    RoundNowView,
    SaveResult,
    ScreenId,
    SettingsView,
    TodayView,
    TradeSettings,
    TradeStatusView,
    UiActions,
    UiContext,
    UiUpdate,
    UpbitKeyView,
    WebhookView,
    coin_label,
)
from coin_report_trader.core.texts import (
    ERROR_BLOCKED,
    MARKETS_FAILED,
    MARKETS_LOADING,
    REPORT_UNREADABLE,
    SETTINGS_UNREADABLE,
    STAGE_SETTINGS_SAVE,
    STAGE_WEBHOOK_DELETE,
    STAGE_WEBHOOK_SAVE,
    blocked_markets_text,
    kst_when_text,
)
from coin_report_trader.ui import theme
from coin_report_trader.ui.account_screen import AccountScreen
from coin_report_trader.ui.dialogs import ask_confirm, ask_live_confirm
from coin_report_trader.ui.first_setup import FirstSetupScreen
from coin_report_trader.ui.help import HelpWindow, guide_path
from coin_report_trader.ui.history_screen import HistoryScreen, history_day_of
from coin_report_trader.ui.icons import screen_icon
from coin_report_trader.ui.live_cards import UpbitKeyDialog
from coin_report_trader.ui.report_screen import ReportScreen
from coin_report_trader.ui.screens import build_screen, screen_object_name
from coin_report_trader.ui.settings_screen import SettingsScreen
from coin_report_trader.ui.state import Message, Phase, PickerKind, PickerMode, Tone
from coin_report_trader.ui.status_screen import StatusScreen
from coin_report_trader.ui.texts import (
    ALERT_CLEAR_TITLE,
    ALERT_DELETE_BODY,
    ALERT_DELETE_TITLE,
    BELOW_STOP_TITLE,
    HISTORY_EXPORT_FILTER,
    HISTORY_EXPORT_TEXT,
    HISTORY_NONE,
    HOLDING_REMOVE_TITLE,
    KEY_DELETE_CONFIRM_BODY,
    KEY_DELETE_CONFIRM_OK,
    KEY_DELETE_CONFIRM_TITLE,
    LIVE_CONFIRM_BODY,
    LIVE_CONFIRM_TITLE,
    LIVE_OFF_CONFIRM_BODY,
    LIVE_OFF_CONFIRM_OK,
    LIVE_OFF_CONFIRM_TITLE,
    LOADING_TEXT,
    REPORT_BEFORE_FIRST_SETUP,
    REPORT_NONE,
    RETENTION_SHRINK_TITLE,
    SELL_ALL_BODY,
    SELL_ALL_TITLE,
    SETTINGS_BEFORE_FIRST_SETUP,
    STATUS_BEFORE_FIRST_SETUP,
    STATUS_BEFORE_SETUP_MIDDLE,
    STATUS_PAUSED_MIDDLE,
    WEBHOOK_DELETE_TITLE,
    alert_clear_body,
    below_stop_body,
    holding_remove_body,
    price_text,
    retention_shrink_body,
    status_line,
    status_next_round_middle,
    webhook_delete_body,
)
from coin_report_trader.ui.trade_band import TradeBand

WINDOW_NAME = "mainWindow"
NAV_NAME = "screenNav"
LEFT_NAME = "screenLeft"
HELP_BUTTON_NAME = "helpButton"
HELP_BUTTON_TEXT = "도움말"
HELP_KEY = "F1"
STACK_NAME = "screenStack"
STATUS_LINE_NAME = "statusLine"

COIN_CARD_CODES = (
    "A-1", "A-2", "A-3", "A-4", "A-8", "A-9", "A-10", "A-11", "A-13", "A-17", "A-24", "A-25",
    "A-30",
)  # fmt: skip
"""누르면 현황 화면의 그 코인 칸으로 가는 알림(M2 명세 3.12.6, M3 명세 3.17.6)."""
SAVED_MESSAGE_MS = 3000
"""설정 화면의 "저장했습니다."가 보이는 시간(밀리초). 시험은 이 값을 줄여 쓴다."""

ConfirmHandler = Callable[[str, str], bool]
"""확인 창을 대신하는 함수. `(제목, 본문)`을 받아 "확인"이면 참, "취소"면 거짓을 돌려준다."""
SAVE_TARGET_UPBIT_KEY = "upbit_key"
"""업비트 키 넣기와 지우기의 결과(`SaveResult.target`. M5 명세 3.4)."""
SAVE_TARGET_LIVE_ON = "live_on"
"""실거래 켜기의 결과(`SaveResult.target`. M5 명세 3.4)."""
SaveDialogHandler = Callable[[str], str]
"""파일 저장 창을 대신하는 함수. 처음 이름을 받아 고른 경로(고르지 않으면 빈 글)를 돌려준다."""


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


def status_line_text(
    context: UiContext, status: TradeStatusView | None = None, *, before_setup: bool = False
) -> str:
    """상태 줄(SCR-00-2. M3 명세 3.17.1): "판 v{판} · 다음 예정 시각 {시각} · 자료 폴더 {경로}".

    멈춤이나 오류 멈춤이면 가운데가 "멈춤 동안 회차를 돌리지 않음", 처음 설정 전이면 "처음 설정
    전"이다. 값이 오기 전의 시각은 "-"다.
    """
    if before_setup:
        middle = STATUS_BEFORE_SETUP_MIDDLE
    elif status is not None and (status.user_paused or status.error_paused):
        middle = STATUS_PAUSED_MIDDLE
    else:
        when = None
        if status is not None and status.next_round_utc is not None:
            try:
                when = kst_when_text(status.next_round_utc, status.as_of)
            except ValueError:
                when = None
        middle = status_next_round_middle(when)
    return status_line(context.version, middle, context.data_dir_display)


class MainWindow(QMainWindow):
    """창. 조립이 만든 값은 `apply`로 받고, 사용자의 동작은 `context.actions`로만 부른다.

    `actions`가 `None`이면 어느 동작도 부르지 않는다. `apply`는 Qt의 주 스레드에서만 부른다.
    """

    def __init__(self, context: UiContext) -> None:
        super().__init__()
        self._actions: UiActions | None = context.actions
        self._confirm: ConfirmHandler | None = None
        self._save_dialog: SaveDialogHandler | None = None

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
        self._ai_status: AiStatusView | None = None
        self._report_lists: dict[str, ReportListView] = {}
        self._report_views: dict[int, ReportView] = {}
        self._report_unreadable = False
        self._report_selected: int | None = None
        self._report_wanted: int | None = None
        """다른 화면에서 열 때 고를 `report_id`. 받은 목록에 있을 때만 쓴다."""
        self._trade_views: dict[str, CoinTradeView] = {}
        self._trade_status: TradeStatusView | None = None
        self._today: TodayView | None = None
        self._round_now: RoundNowView | None = None
        self._performance: PerformanceView | None = None
        self._last_save: dict[str, object] | None = None
        """마지막으로 부른 `save_settings`의 인자. 손절선 아래 확인 뒤 다시 저장할 때 쓴다."""
        self._accepted_below_stop: tuple[str, ...] = ()
        self._report_coins: tuple[str, ...] | None = None
        self._report_rounds_key: object = None
        self._account_view: AccountView | None = None
        self._account_visible = False
        """마지막으로 `account_visible`로 알린 값(M5 명세 3.15.4)."""
        self._live_status: LiveStatusView | None = None
        self._key_view: UpbitKeyView | None = None
        self._key_message: Message | None = None
        self._mode_message: Message | None = None
        self._live_asked = False
        """"실거래 켜기"를 눌러 `prepare_live_on`을 불렀고 `LiveConfirmView`를 기다린다."""
        self._live_turning = False
        """SCR-06-9를 확인해 `turn_live_on`을 불렀다(그 사이 `BelowStopView`가 올 수 있다)."""
        self._key_dialog: UpbitKeyDialog | None = None
        self._history_view: HistoryView | None = None
        self._history_pending = False
        """기록을 부르려 했는데 아직 부를 수 없었다(처음 설정 전이거나 설정을 읽기 전)."""

        self.setObjectName(WINDOW_NAME)
        self.setWindowTitle(WINDOW_TITLE)
        self.setStyleSheet(theme.app_stylesheet())

        nav = QListWidget()
        nav.setObjectName(NAV_NAME)
        stack = QStackedWidget()
        stack.setObjectName(STACK_NAME)
        self._first_setup = FirstSetupScreen()
        self._settings = SettingsScreen()
        self._status = StatusScreen()
        self._reports = ReportScreen()
        self._history = HistoryScreen()
        self._account_screen = AccountScreen()
        for screen_id in ScreenId:
            nav.addItem(QListWidgetItem(screen_icon(screen_id), SCREEN_TITLES[screen_id]))
            if screen_id is ScreenId.FIRST_SETUP:
                stack.addWidget(_scroll_page(screen_id, self._first_setup))
            elif screen_id is ScreenId.SETTINGS:
                stack.addWidget(_scroll_page(screen_id, self._settings))
            elif screen_id is ScreenId.STATUS:
                stack.addWidget(_scroll_page(screen_id, self._status))
            elif screen_id is ScreenId.REPORTS:
                stack.addWidget(_scroll_page(screen_id, self._reports))
            elif screen_id is ScreenId.HISTORY:
                stack.addWidget(_scroll_page(screen_id, self._history))
            elif screen_id is ScreenId.ACCOUNT:
                stack.addWidget(_scroll_page(screen_id, self._account_screen))
            else:
                stack.addWidget(_scroll_page(screen_id, build_screen(screen_id)))
        nav.currentRowChanged.connect(stack.setCurrentIndex)
        nav.itemClicked.connect(self._on_nav_clicked)
        nav.setCurrentRow(0)
        nav.setMaximumWidth(160)
        nav.setIconSize(QSize(theme.ICON_SIZE, theme.ICON_SIZE))
        nav.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._nav = nav
        self._stack = stack

        # 왼쪽 자리: 화면 이름 목록과 그 아래의 "도움말" 단추(M2 명세 3.16). 단추는 목록의 줄이
        # 아니다(여섯 화면에 더하지 않는다).
        help_button = QPushButton(HELP_BUTTON_TEXT)
        help_button.setObjectName(HELP_BUTTON_NAME)
        help_button.clicked.connect(self._open_help)
        left = QWidget()
        left.setObjectName(LEFT_NAME)
        left.setMaximumWidth(160)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, theme.FIELD_GAP)
        left_layout.setSpacing(theme.FIELD_GAP)
        left_layout.addWidget(nav, 1)
        left_layout.addWidget(help_button)
        self._help_button = help_button
        self._help: HelpWindow | None = None

        # 오른쪽: 상태 띠(화면 자리와 함께 넘기지 않고 늘 보인다) 아래에 화면 자리(M3 명세 3.17.1).
        self._band = TradeBand()
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(theme.FIELD_GAP)
        right_layout.addWidget(self._band)
        right_layout.addWidget(stack, 1)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(theme.FIELD_GAP, theme.FIELD_GAP, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(left)
        layout.addWidget(right, 1)
        self.setCentralWidget(central)

        self._context = context
        status = QLabel(status_line_text(context))
        status.setObjectName(STATUS_LINE_NAME)
        status.setTextFormat(Qt.TextFormat.PlainText)
        self.statusBar().addWidget(status, 1)
        self._status_label = status

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
        self._status.report_requested.connect(self._open_report)
        self._reports.coin_chosen.connect(self._on_report_coin)
        self._reports.round_chosen.connect(self._on_report_round)
        self._settings.key_set_requested.connect(self._on_key_set)
        self._settings.key_delete_requested.connect(self._on_key_delete)
        self._settings.live_on_requested.connect(self._on_live_on)
        self._settings.live_off_requested.connect(self._on_live_off)
        self._reports.mode_chosen.connect(self._on_report_mode)
        self._account_screen.refresh_requested.connect(self._on_account_refresh)
        self._account_screen.go_key_requested.connect(self._go_account_key)
        self._history.selection_changed.connect(self._on_history_selection)
        self._history.report_requested.connect(self._open_report)
        self._history.export_requested.connect(self._on_history_export)
        center = self._status.alert_center
        center.seen_requested.connect(self._on_seen)
        center.delete_alert_requested.connect(self._on_alert_delete)
        center.clear_requested.connect(self._on_alert_clear)
        center.save_webhook_requested.connect(self._on_webhook_save)
        center.delete_webhook_requested.connect(self._on_webhook_delete)
        center.send_test_requested.connect(self._on_send_test)
        center.go_requested.connect(self._go_alert)
        self._band.pause_requested.connect(self._on_pause)
        self._band.sell_all_requested.connect(self._on_sell_all)
        self._band.resume_user_requested.connect(self._on_resume_user)
        self._band.resume_error_requested.connect(self._on_resume_error)
        self._band.alerts_requested.connect(lambda: self._open_screen(ScreenId.STATUS))
        stack.currentChanged.connect(self._on_screen_changed)
        for index, screen_id in enumerate(ScreenId):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
            shortcut.activated.connect(lambda sid=screen_id: self._open_screen(sid))
        f1 = QShortcut(QKeySequence(HELP_KEY), self)
        f1.activated.connect(self._open_help)

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
        self._notify_account_visible()
        if self._frame_fitted:
            return
        self._frame_fitted = True
        frame = self.frameGeometry().size() - self.geometry().size()
        if (frame.width() > 0 or frame.height() > 0) and self.size() == self._auto_size:
            self._fit_to_screen(frame)

    def hideEvent(self, event) -> None:
        """트레이로 숨으면 계정 화면은 보이지 않는 것이다(M5 명세 3.15.4)."""
        super().hideEvent(event)
        self._notify_account_visible()

    def changeEvent(self, event) -> None:
        """창을 최소화하거나 되돌릴 때 계정 화면의 보임을 다시 알린다."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._notify_account_visible()

    def _notify_account_visible(self) -> None:
        """계정 화면이 골라져 있고 창이 최소화되거나 숨지 않았을 때만 '보인다'. 바뀔 때마다 한 번
        `account_visible`로 알린다(보이게 되면 읽기 한 번, 보이지 않으면 읽지 않는다)."""
        visible = (
            self._actions is not None
            and self.isVisible()
            and not self.isMinimized()
            and current_screen(self) is ScreenId.ACCOUNT
        )
        if visible == self._account_visible:
            return
        self._account_visible = visible
        if self._actions is not None:
            self._actions.account_visible(visible)

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
        elif isinstance(update, AiStatusView):
            self._ai_status = update
            self._update()
        elif isinstance(update, ReportListView):
            self._on_report_list(update)
        elif isinstance(update, ReportView):
            self._on_report_view(update)
        elif isinstance(update, CoinTradeView):
            self._trade_views[update.market] = update  # 코인 칸과 보유 코인을 뺄 때의 확인(3.11.4)
            self._update()
        elif isinstance(update, BelowStopView):
            self._on_below_stop(update)
        elif isinstance(update, TradeStatusView):
            self._trade_status = update
            self._update()
        elif isinstance(update, TodayView):
            self._today = update
            self._update()
        elif isinstance(update, RoundNowView):
            self._round_now = update
            self._update()
        elif isinstance(update, PerformanceView):
            self._performance = update
            self._update()
        elif isinstance(update, HistoryView):
            self._history_view = update
            self._update()
        elif isinstance(update, AccountView):
            self._account_view = update
            self._update()
        elif isinstance(update, LiveStatusView):
            self._live_asked = False  # 막힌 까닭이 왔으면 확인 창 없이 끝난 것이다
            self._live_status = update
            self._update()
        elif isinstance(update, LiveConfirmView):
            self._on_live_confirm(update)
        elif isinstance(update, UpbitKeyView):
            self._key_view = update
            self._key_message = Message(update.message, Tone.WARNING) if update.message else None
            self._update()

    def set_confirm_handler(self, handler: ConfirmHandler | None) -> None:
        """확인 창 대신 `handler(제목, 본문)`을 부르게 한다. `None`이면 진짜 확인 창을 띄운다.

        시험이 모달 창에서 멈추지 않고 "확인"이나 "취소"를 고르는 길이다.
        """
        self._confirm = handler

    def set_save_dialog_handler(self, handler: SaveDialogHandler | None) -> None:
        """파일 저장 창 대신 `handler(처음 이름)`을 부르게 한다. `None`이면 진짜 저장 창을 띄운다.

        시험이 모달 창에서 멈추지 않고 경로를 고르거나(경로를 돌려줌) 취소하는(빈 글) 길이다.
        """
        self._save_dialog = handler

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
            view.autostart,
            view.ai_choices,
            view.trade,
            view.live,
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
                old.autostart,
                old.ai_choices,
                old.trade,
                old.live,
            )
        )
        if key != old_key:
            self._first_setup.load_saved(
                view.coins,
                view.retention_days,
                view.candle_unit,
                view.candle_count,
                view.ai_choices,
                view.trade,
            )
            self._settings.load_saved(
                view.coins,
                view.retention_days,
                view.candle_unit,
                view.candle_count,
                view.autostart,
                view.ai_choices,
                view.trade,
                view.live,
            )
        self._settings.set_autostart_state(view.autostart_supported, view.autostart_error)
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
        if result.target == SAVE_TARGET_UPBIT_KEY:
            # 키 넣기와 지우기의 결과: 묶음 아래의 글. 키의 값은 이 값에도 들지 않는다.
            self._key_message = (
                Message(result.message, Tone.NORMAL if result.ok else Tone.WARNING)
                if result.message
                else None
            )
            self._update()
            return
        if result.target == SAVE_TARGET_LIVE_ON:
            self._live_turning = False
            self._mode_message = (
                Message(result.message, Tone.NORMAL if result.ok else Tone.WARNING)
                if result.message
                else None
            )
            self._update()
            return
        if result.target == SAVE_TARGET_EXPORT:
            self._history.set_export_result(result)  # 단추 아래의 결과 글(M3 명세 3.15.2)
            return
        if result.target == SAVE_TARGET_REPORT:
            # 보고서를 읽지 못함: 오른쪽에 글 하나가 보인다. 다시 고르면 다시 부른다(3.12.1).
            if not result.ok:
                self._report_unreadable = True
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
        self._last_save = None
        self._accepted_below_stop = ()
        # 받지 않는 값의 글은 걸린 칸 아래에 보인다(비어 있으면 이전 글을 걷는다. 3.11.2).
        screen = self._first_setup if origin is ScreenId.FIRST_SETUP else self._settings
        screen.show_errors(result.field_errors)
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
        self,
        coins: tuple[str, ...],
        days: int,
        unit: CandleUnit | None,
        count: int | None,
        ai_choices: tuple[AiChoice, ...] | None = None,
        trade: TradeSettings | None = None,
    ) -> None:
        self._submit(
            ScreenId.FIRST_SETUP, coins, days, unit, count, ai_choices=ai_choices, trade=trade
        )

    def _on_save(
        self,
        coins: tuple[str, ...],
        days: int,
        unit: CandleUnit | None,
        count: int | None,
        autostart: bool | None = None,
        ai_choices: tuple[AiChoice, ...] | None = None,
        trade: TradeSettings | None = None,
        live: LiveSettings | None = None,
    ) -> None:
        saved_days = self._saved.retention_days if self._saved is not None else None
        # 저장된 값이 없을 때(처음 고를 때)와 같거나 길게 바꿀 때는 확인 창이 없다.
        shrinking = saved_days is not None and days < saved_days
        if shrinking and not self._ask_confirm(
            RETENTION_SHRINK_TITLE, retention_shrink_body(saved_days, days)
        ):
            return
        # 가상 보유가 있는 코인을 빼면 SCR-06-6(3.11.4). 하나라도 "취소"면 아무것도 저장하지 않는다.
        for market in self._removed_held_coins(coins):
            if not self._ask_confirm(HOLDING_REMOVE_TITLE, holding_remove_body(coin_label(market))):
                return
        self._submit(
            ScreenId.SETTINGS, coins, days, unit, count, autostart, ai_choices, trade, live
        )

    def _removed_held_coins(self, coins: tuple[str, ...]) -> list[str]:
        if self._saved is None:
            return []
        held = []
        for market in self._saved.coins:
            view = self._trade_views.get(market)
            holding = view.holding if view is not None else None
            if market not in coins and holding is not None and holding.qty > 0:
                held.append(market)
        return held

    def _submit(
        self,
        origin: ScreenId,
        coins: tuple[str, ...],
        days: int,
        unit: CandleUnit | None,
        count: int | None,
        autostart: bool | None = None,
        ai_choices: tuple[AiChoice, ...] | None = None,
        trade: TradeSettings | None = None,
        live: LiveSettings | None = None,
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
        extra: dict[str, object] = {}
        if autostart is not None:  # 쓸 수 없으면 넘기지 않는다(M1 명세 3.16.1)
            extra["autostart"] = autostart
        if ai_choices is not None:  # 여덟 칸이 다 골라져 있을 때만 넘긴다(M2 명세 3.12.2)
            extra["ai_choices"] = ai_choices
        if trade is not None:  # 3단계 칸이 있을 때만 넘긴다(M3 명세 3.11.2)
            extra["trade"] = trade
        if live is not None:  # 실거래 설정 칸을 채웠을 때만 넘긴다(M5 명세 3.7.5)
            extra["live"] = live
        self._last_save = {
            "coins": coins,
            "retention_days": days,
            "first_setup": first_setup,
            "candle_unit": unit,
            "candle_count": count,
            **extra,
        }
        self._accepted_below_stop = ()
        self._actions.save_settings(
            coins, days, first_setup=first_setup, candle_unit=unit, candle_count=count, **extra
        )
        self._update()

    def _on_below_stop(self, view: BelowStopView) -> None:
        """손절선 아래인 보유(3.11.4): 저장하지 않고 SCR-06-5를 띄운다. "확인"이면 그 코인을
        `accept_below_stop`에 넣어 다시 저장하고, "취소"면 아무것도 저장하지 않고 고친 값은 칸에
        남긴다(그 뒤의 모습은 M3 명세 7절의 물을 것 2)."""
        if self._live_turning and self._actions is not None:
            # 실거래를 켜는 중의 손절선 아래 보유(M5 명세 3.7.2의 뒤 차례 2): "취소"면 켜지 않는다.
            if not self._ask_confirm(BELOW_STOP_TITLE, self._below_stop_text(view)):
                self._live_turning = False
                self._update()
                return
            self._actions.turn_live_on()
            return
        pending = self._last_save
        if pending is None or self._actions is None:
            return
        body = below_stop_body(
            coin_label(view.market),
            price_text(view.avg_price),
            price_text(view.price),
            price_text(view.stop_line),
        )
        if not self._ask_confirm(BELOW_STOP_TITLE, body):
            if self._pending_saves:
                self._pending_saves.pop(0)
            self._last_save = None
            self._accepted_below_stop = ()
            self._update()
            return
        if view.market not in self._accepted_below_stop:
            self._accepted_below_stop = (*self._accepted_below_stop, view.market)
        arguments = dict(pending)
        coins = arguments.pop("coins")
        days = arguments.pop("retention_days")
        self._actions.save_settings(
            coins,  # type: ignore[arg-type]
            days,  # type: ignore[arg-type]
            accept_below_stop=self._accepted_below_stop,
            **arguments,  # type: ignore[arg-type]
        )

    def _below_stop_text(self, view: BelowStopView) -> str:
        return below_stop_body(
            coin_label(view.market),
            price_text(view.avg_price),
            price_text(view.price),
            price_text(view.stop_line),
        )

    # --- 업비트 키와 모드(M5 명세 3.8, 3.11) ---

    def _today_kst(self) -> date:
        return datetime.now(KST).date()

    def _mode(self) -> Mode:
        """지금 모드: 상태 띠의 값(`TradeStatusView.mode`)이 있으면 그것, 없으면 `LiveStatusView`의
        것, 둘 다 없으면 모의매매다."""
        if self._trade_status is not None:
            return self._trade_status.mode
        if self._live_status is not None:
            return self._live_status.mode
        return Mode.PAPER

    def _settings_mode(self) -> Mode:
        """설정 화면이 보이는 모드. 모드를 바꾸는 쪽이므로 `LiveStatusView`가 있으면 그것을 먼저
        본다."""
        if self._live_status is not None:
            return self._live_status.mode
        return self._mode()

    def _on_key_set(self) -> None:
        """"키 넣기": 키 넣기 창을 연다(모달). 저장하면 `save_upbit_key`를 부른다."""
        if self._actions is None or self._phase is not Phase.READY:
            return
        dialog = UpbitKeyDialog(self, self._today_kst())
        dialog.key_chosen.connect(self._on_key_chosen)
        dialog.finished.connect(lambda _result, d=dialog: self._drop_key_dialog(d))
        self._key_dialog = dialog
        dialog.open()

    def _drop_key_dialog(self, dialog: UpbitKeyDialog) -> None:
        if self._key_dialog is dialog:
            self._key_dialog = None
        dialog.deleteLater()

    def _on_key_chosen(self, access: str, secret: str, created: date) -> None:
        if self._actions is None:
            return
        self._key_message = None
        self._actions.save_upbit_key(access, secret, created)
        self._update()

    def _on_key_delete(self) -> None:
        """"키 지우기": SCR-06-11 뒤에 지운다."""
        if self._actions is None or self._phase is not Phase.READY:
            return
        if not self._ask_confirm(
            KEY_DELETE_CONFIRM_TITLE, KEY_DELETE_CONFIRM_BODY, KEY_DELETE_CONFIRM_OK
        ):
            return
        self._key_message = None
        self._actions.delete_upbit_key()
        self._update()

    def _on_live_on(self) -> None:
        """"실거래 켜기": `prepare_live_on`을 부른다. 통과하면 `LiveConfirmView`가 와서 SCR-06-9가
        뜬다."""
        if self._actions is None or self._phase is not Phase.READY:
            return
        self._mode_message = None
        self._live_asked = True
        self._actions.prepare_live_on()
        self._update()

    def _on_live_confirm(self, view: LiveConfirmView) -> None:
        """SCR-06-9: "실거래 켜기"를 눌렀을 때만 `turn_live_on`을 부른다. "취소"면 그대로
        모의매매다."""
        if not self._live_asked or self._actions is None:
            return
        self._live_asked = False
        if self._confirm is not None:
            confirmed = self._confirm(LIVE_CONFIRM_TITLE, LIVE_CONFIRM_BODY)
        else:
            confirmed = ask_live_confirm(self, view)
        if not confirmed:
            return
        self._live_turning = True
        self._actions.turn_live_on()

    def _on_live_off(self) -> None:
        """"모의매매로 바꾸기": SCR-06-10 뒤에 `turn_live_off`를 부른다."""
        if self._actions is None or self._phase is not Phase.READY:
            return
        if not self._ask_confirm(
            LIVE_OFF_CONFIRM_TITLE, LIVE_OFF_CONFIRM_BODY, LIVE_OFF_CONFIRM_OK
        ):
            return
        self._mode_message = None
        self._actions.turn_live_off()
        self._update()

    def _on_pause(self) -> None:
        """"멈춤": 확인 창 없이 바로 멈춘다(M3 명세 3.10.1)."""
        if self._actions is not None:
            self._actions.pause()

    def _on_sell_all(self) -> None:
        """"멈추고 전량 매도": SCR-06-7 뒤에 `pause_and_sell_all()`(M3 명세 3.10.3)."""
        if self._actions is None:
            return
        if self._ask_confirm(SELL_ALL_TITLE, SELL_ALL_BODY):
            self._actions.pause_and_sell_all()

    def _on_resume_user(self) -> None:
        if self._actions is not None:
            self._actions.resume_user()

    def _on_resume_error(self) -> None:
        if self._actions is not None:
            self._actions.resume_error()

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

    def _ask_confirm(self, title: str, body: str, ok_text: str | None = None) -> bool:
        if self._confirm is not None:
            return self._confirm(title, body)
        if ok_text is None:
            return ask_confirm(self, title, body)
        return ask_confirm(self, title, body, ok_text)

    def _request_markets(self) -> None:
        if self._actions is None:
            return
        self._markets_loading = True
        self._actions.refresh_markets()

    def _on_screen_changed(self, index: int) -> None:
        if list(ScreenId)[index] is ScreenId.REPORTS:
            self._request_reports()  # 보고서 화면이 보일 때 부른다(M2 명세 3.12.1)
        elif list(ScreenId)[index] is ScreenId.HISTORY:
            self._history.reset_day()  # 처음에는 오늘(M3 명세 3.17.4)
            self._request_history()  # 화면을 열 때 지금 고른 것으로 한 번 부른다
        self._notify_account_visible()  # 계정 화면을 고르거나 떠날 때(M5 명세 3.15.4)
        self._update()

    # --- 계정 화면(M5 명세 3.15) ---

    def _account_state(self) -> AccountState:
        """처음 설정 전이면 그 상태, 값이 오면 그 값의 상태, 오기 전이면 키가 없는지 본다."""
        if not self._first_setup_done():
            return AccountState.BEFORE_SETUP
        if self._account_view is not None:
            return self._account_view.state
        if self._key_view is not None and not self._key_view.present:
            return AccountState.NO_KEY
        return AccountState.LOADING

    def _render_account(self) -> None:
        state = self._account_state()
        view = self._account_view if state not in (
            AccountState.BEFORE_SETUP,
            AccountState.NO_KEY,
            AccountState.LOADING,
        ) else None
        self._account_screen.render(state=state, view=view)

    def _on_account_refresh(self) -> None:
        """"새로 고침": 읽는 중이면 단추가 흐려 눌리지 않는다."""
        if self._actions is not None and self._phase is Phase.READY:
            self._actions.refresh_account()

    def _go_account_key(self) -> None:
        """"업비트 키 넣으러 가기": 설정 화면의 '업비트 키' 묶음으로 간다."""
        select_screen(self, ScreenId.SETTINGS)
        self._scroll_page_to(self._settings, self._settings.key_widget())

    # --- 보고서 화면(M2 명세 3.12.1) ---

    def _reports_ready(self) -> bool:
        return (
            self._actions is not None
            and self._phase is Phase.READY
            and self._first_setup_done()
            and self._reports.chosen_market() is not None
        )

    def _request_reports(self) -> None:
        """고른 코인의 회차 목록을 부른다. 다시 부르는 것이므로 읽지 못함의 글은 걷는다."""
        if not self._reports_ready():
            return
        market = self._reports.chosen_market()
        if market is None or self._actions is None:
            return
        self._report_unreadable = False
        chosen = self._reports.chosen_mode()
        if chosen is self._mode():
            self._actions.load_reports(market)
        else:  # 지금 모드가 아닌 기록(M5 명세 3.11)
            self._actions.load_reports(market, mode=chosen)

    def _request_report(self, report_id: int) -> None:
        if self._actions is None or self._phase is not Phase.READY:
            return
        self._report_unreadable = False
        self._actions.load_report(report_id)

    def _on_report_coin(self, _market: str) -> None:
        """사용자가 코인을 골랐다."""
        self._report_selected = None
        self._report_wanted = None
        self._request_reports()
        self._update()

    def _on_report_mode(self, _mode: object) -> None:
        """"모의매매 기록" / "실거래 기록"을 골랐다: 받아 둔 목록과 보고서를 버리고 다시 부른다."""
        self._clear_report_cache()
        self._request_reports()
        self._update()

    def _clear_report_cache(self) -> None:
        self._report_lists = {}
        self._report_views = {}
        self._report_selected = None
        self._report_wanted = None
        self._report_rounds_key = None

    def _on_report_round(self, report_id: int) -> None:
        """사용자가 회차를 골랐다."""
        self._report_selected = report_id
        self._report_wanted = None
        self._request_report(report_id)
        self._update()

    def _on_report_list(self, view: ReportListView) -> None:
        old = self._report_lists.get(view.market)
        self._report_lists[view.market] = view
        shown = current_screen(self) is ScreenId.REPORTS
        if shown and view.market == self._reports.chosen_market() and view.state is LoadState.READY:
            self._choose_round(view, old)
        self._update()

    def _choose_round(self, view: ReportListView, old: ReportListView | None) -> None:
        """받은 목록에서 볼 회차를 정한다. 주어진 번호(다른 화면에서 열 때)가 있으면 그것, 없으면
        보던 것, 그것도 없으면 맨 위다. 보던 줄의 `state`가 바뀌면 보고서를 다시 부른다."""
        ids = {item.report_id: item for item in view.items}
        if not ids:
            self._report_selected = None
            return
        if self._report_wanted in ids:
            chosen = self._report_wanted
        elif self._report_selected in ids:
            chosen = self._report_selected
        else:
            chosen = view.items[0].report_id
        self._report_wanted = None
        previous = self._report_selected
        changed = chosen != previous
        if not changed and old is not None:
            before = next((i for i in old.items if i.report_id == chosen), None)
            changed = before is None or before.state != ids[chosen].state
        self._report_selected = chosen
        if changed:
            self._request_report(chosen)

    def _on_report_view(self, view: ReportView) -> None:
        self._report_views[view.report_id] = view
        if view.report_id == self._report_selected:
            self._report_unreadable = False
        self._update()

    def _open_report(self, market: str, report_id: int | None = None) -> None:
        """다른 화면에서 보고서 화면을 연다: 그 코인을 고르고 목록을 부른다. `report_id`가 받은
        목록에 있으면 그 줄을, 아니면 맨 위 줄을 고른다."""
        self._reports.choose_market(market)
        self._report_selected = None
        self._report_wanted = report_id
        if current_screen(self) is ScreenId.REPORTS:
            self._request_reports()
            self._update()
        else:
            select_screen(self, ScreenId.REPORTS)

    # --- 기록 화면(M3 명세 3.15, 3.17.4) ---

    def _history_ready(self) -> bool:
        return (
            self._actions is not None and self._phase is Phase.READY and self._first_setup_done()
        )

    def _request_history(self) -> None:
        """지금 고른 탭, 코인, 날짜로 `load_history`를 부른다. 아직 부를 수 없으면 벼른다."""
        if not self._history_ready() or self._actions is None:
            self._history_pending = True
            return
        self._history_pending = False
        history = self._history
        arguments = (history.chosen_tab(), history.chosen_market(), history.chosen_day())
        if history.chosen_mode() is self._mode():
            self._actions.load_history(*arguments)
        else:  # 지금 모드가 아닌 기록(M5 명세 3.11)
            self._actions.load_history(*arguments, mode=history.chosen_mode())

    def _on_history_selection(self) -> None:
        """사용자가 코인, 날짜, 탭, 모드를 골랐다. 받아 둔 값은 다른 고르기의 것이다."""
        self._history_view = None
        self._request_history()
        self._update()

    def _on_history_export(self, tab: str, market: object, default_name: str) -> None:
        """"CSV로 내보내기": 파일 저장 창에서 경로를 고르면 `export_history`를 부른다."""
        if self._actions is None or self._phase is not Phase.READY:
            return
        if self._save_dialog is not None:
            path = self._save_dialog(default_name)
        else:
            path, _filter = QFileDialog.getSaveFileName(
                self, HISTORY_EXPORT_TEXT, default_name, HISTORY_EXPORT_FILTER
            )
        if path:
            self._actions.export_history(
                HistoryTab(tab), market if isinstance(market, str) else None, path
            )

    def _open_history(
        self, tab: HistoryTab, market: str | None, day: date, mode: Mode | None = None
    ) -> None:
        """다른 화면(알림센터)에서 기록 화면을 연다: 그 탭, 코인, 날짜(와 알림의 모드)를 고른다."""
        self._history_view = None
        self._history.choose(tab, market, day, mode)
        if current_screen(self) is ScreenId.HISTORY:
            self._request_history()
            self._update()
        else:
            select_screen(self, ScreenId.HISTORY)

    def _history_message(self) -> Message | None:
        """표 자리에 글 하나만 보이는 모습. 없으면 `None`(받은 값으로 그린다)."""
        if self._phase is Phase.WAITING:
            return Message(LOADING_TEXT)
        if self._phase is Phase.UNREADABLE:
            return Message(SETTINGS_UNREADABLE, Tone.WARNING)
        if self._actions is None or not self._first_setup_done():
            return Message(HISTORY_NONE)
        return None

    def _sync_record_modes(self) -> None:
        """지금 모드의 기록이 먼저 골라져 있다(사용자가 고르기 전에만. M5 명세 3.11)."""
        mode = self._mode()
        shown = current_screen(self)
        if self._reports.set_default_mode(mode):
            self._clear_report_cache()
            if shown is ScreenId.REPORTS:
                self._request_reports()
        if self._history.set_default_mode(mode):
            self._history_view = None
            if shown is ScreenId.HISTORY:
                self._request_history()

    def _render_history(self) -> None:
        coins = self._saved.coins if self._saved is not None and self._first_setup_done() else ()
        coin_lost = self._history.set_coins(coins)
        shown = current_screen(self) is ScreenId.HISTORY
        if shown and (coin_lost or self._history_pending):
            self._request_history()
        self._history.render(message=self._history_message(), view=self._history_view)

    def _open_help(self) -> None:
        """ "도움말" 단추와 F1: 도움말 창을 연다(열려 있으면 앞으로 낸다). 처음 설정 전에도 된다."""
        if self._help is None:
            self._help = HelpWindow(guide_path(), self)
        self._help.open_help()

    def _open_screen(self, screen_id: ScreenId) -> None:
        """Ctrl+1~6: 화면 이름 목록에서 그 줄을 누른 것과 같다."""
        select_screen(self, screen_id)
        if screen_id is ScreenId.STATUS:
            self._hold_seen = False
            self._status.alert_center.select_first_unseen_tab()

    def _report_message(self) -> Message | None:
        """오른쪽에 글 하나만 보이는 모습. 없으면 `None`(고른 회차의 보고서를 그린다)."""
        if self._phase is Phase.WAITING:
            return Message(LOADING_TEXT)
        if self._phase is Phase.UNREADABLE:
            return Message(SETTINGS_UNREADABLE, Tone.WARNING)
        if not self._first_setup_done():
            return Message(REPORT_BEFORE_FIRST_SETUP)
        market = self._reports.chosen_market()
        listing = self._report_lists.get(market) if market is not None else None
        if listing is None or listing.state is LoadState.LOADING:
            return Message(LOADING_TEXT)
        if listing.state is LoadState.FAILED:
            return Message(REPORT_UNREADABLE, Tone.WARNING)
        if not listing.items:
            return Message(REPORT_NONE)
        if self._report_unreadable:
            return Message(REPORT_UNREADABLE, Tone.WARNING)
        if self._report_selected is None or self._report_selected not in self._report_views:
            return Message(LOADING_TEXT)
        return None

    def _render_reports(self) -> None:
        coins = self._saved.coins if self._saved is not None and self._first_setup_done() else ()
        if coins != self._report_coins:
            self._report_coins = coins
            self._reports.set_coins(coins)
        self._reports.set_ai_status(self._ai_status)
        market = self._reports.chosen_market()
        listing = self._report_lists.get(market) if market is not None else None
        items = listing.items if listing is not None and listing.state is LoadState.READY else ()
        key = (market, items, self._report_selected)
        if key != self._report_rounds_key:
            self._report_rounds_key = key
            self._reports.set_rounds(items, self._report_selected)
        message = self._report_message()
        view = None
        if message is None and self._report_selected is not None:
            view = self._report_views.get(self._report_selected)
        self._reports.render(message=message, view=view)

    # --- 알림의 곳으로 가기(M2 명세 3.12.6) ---

    def _go_alert(self, alert: AlertView) -> None:
        """알림이 가리키는 곳으로 화면만 바꾼다(알림을 지우거나 다른 동작을 부르지 않는다)."""
        code = alert.code
        done = self._first_setup_done()
        setup = ScreenId.SETTINGS if done else ScreenId.FIRST_SETUP
        if code in COIN_CARD_CODES:
            self._go_status_coin(alert.coin)
        elif code == "A-12":  # 현황 화면의 오늘 요약(M3 명세 3.17.6)
            select_screen(self, ScreenId.STATUS)
            self._scroll_page_to(self._status, self._status.today_widget())
        elif code == "A-23":  # 현황 화면 맨 위
            select_screen(self, ScreenId.STATUS)
            self._scroll_page_to(self._status, None)
        elif code == "A-15":  # 설정 화면의 걸린 칸
            select_screen(self, ScreenId.SETTINGS)
            self._scroll_page_to(self._settings, self._settings.first_error_widget())
        elif code == "A-16":  # 설정 화면의 코인과 예산
            select_screen(self, ScreenId.SETTINGS)
            self._scroll_page_to(self._settings, self._settings.coins_widget())
        elif code in ("A-27", "A-28"):  # 기록 화면의 주문·체결 탭(그 알림의 날짜, 그 코인)
            tab = HistoryTab.ORDERS if code == "A-27" else HistoryTab.FILLS
            self._open_history(tab, alert.coin, history_day_of(alert.created_at), alert.mode)
        elif code in ("A-18", "A-19"):
            select_screen(self, ScreenId.REPORTS)
        elif code == "A-26":
            if alert.coin is not None:
                self._open_report(alert.coin, alert.report_id)
            else:
                select_screen(self, ScreenId.REPORTS)
        elif code == "A-29":
            stage = alert.detail.stage if alert.detail is not None else None
            if stage == STAGE_SETTINGS_SAVE:
                select_screen(self, setup)
            elif stage in (STAGE_WEBHOOK_SAVE, STAGE_WEBHOOK_DELETE):
                select_screen(self, ScreenId.STATUS)  # 알림센터(탭은 바꾸지 않는다)
                self._scroll_status_to(self._status.alert_center)
            else:
                self._go_status_coin(alert.coin)
        elif code == "A-31":
            select_screen(self, setup)

    def _go_status_coin(self, market: str | None) -> None:
        """현황 화면의 그 코인 칸으로 간다. 그 칸이 없으면 현황 화면 맨 위로 간다."""
        select_screen(self, ScreenId.STATUS)
        card = self._status.card_of(market) if market is not None else None
        self._scroll_status_to(card)

    def _scroll_status_to(self, widget: QWidget | None) -> None:
        self._scroll_page_to(self._status, widget)

    @staticmethod
    def _scroll_page_to(screen: QWidget, widget: QWidget | None) -> None:
        """그 화면의 자리를 `widget`이 보이게(없으면 맨 위로) 넘긴다."""
        page = screen.parentWidget()
        scroll = page.parentWidget() if page is not None else None
        if not isinstance(scroll, QScrollArea):
            return
        if widget is None:
            scroll.verticalScrollBar().setValue(0)
        else:
            scroll.ensureWidgetVisible(widget)

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
        self._sync_record_modes()
        self._render_account()
        self._render_reports()
        self._render_history()
        paused = self._trade_status is not None and (
            self._trade_status.user_paused or self._trade_status.error_paused
        )
        self._settings.set_live_state(
            mode=self._settings_mode(),
            live_status=self._live_status,
            key=self._key_view,
            paused=paused,
            today=self._today_kst(),
            key_message=self._key_message,
            mode_message=self._mode_message,
        )
        self._settings.render(
            phase=self._phase, done=done, picker=mode, message=self._settings_message()
        )
        self._status.render(
            message=self._status_message(),
            coins=self._saved.coins if self._saved is not None else (),
            views=self._coin_views,
            trade_views=self._trade_views,
            today=self._today,
            round_now=self._round_now,
            performance=self._performance,
            status=self._trade_status,
            live=self._mode() is Mode.LIVE,
        )
        # 화면 이름 목록에는 " ({n})"이 없고, 보지 않은 알림의 수는 상태 띠의 "알림 {n}건"이다.
        unseen = sum(self._alerts.unseen.values()) if self._alerts is not None else 0
        self._band.render(self._trade_status, setup_done=done, unseen=unseen)
        self._reports.set_paused(
            self._trade_status is not None
            and (self._trade_status.user_paused or self._trade_status.error_paused)
        )
        before_setup = self._saved is not None and not done
        self._status_label.setText(
            status_line_text(self._context, self._trade_status, before_setup=before_setup)
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
      여섯 자리 모두 제목 라벨 `screenTitle`(글은 `SCREEN_TITLES`)이 있다.
      빈 화면 문구 라벨 `screenBody`는 어느 자리에도 없다(여섯 자리 모두 채워졌다).
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
      `firstSetupNoticesTitle`('꼭 읽을 글' 카드의 제목),
      `firstSetupDone`(처음 설정을 마친 뒤의 글).
    - 'ⓘ'(`InfoIcon`. 툴팁과 읽는 설명이 같고 읽는 이름은 "{제목} 설명"): `firstSetupNoticesInfo`
      (제목 옆. 툴팁이 10의 가, 마, 바 세 글을 "• "로 시작하는 줄로 잇는다), `firstSetupCandleNote`,
      `firstSetupRetentionInfo`. 설정 자리에는 `settingsCandleNote`, `settingsRetentionInfo`가 있다.
    - 묶음: `firstSetupForm`(칸 전체), `firstSetupNotices`, `firstSetupDonePanel`.
    - 단추: `firstSetupStart`("저장하고 시작"), `firstSetupToSettings`("설정 화면으로").
    - 코인 고르기(`firstSetupCoins`, 찾기 칸 `firstSetupCoinSearch`), 봉 단위와 봉 개수(라디오
      버튼 `firstSetupUnit_<봉 단위 값>`, `firstSetupCount_<수>`),
      보관 기간(`firstSetupRetention`)의 안쪽 이름은 `coin_picker.py`, `candle_choice.py`,
      `retention.py`의 문서 문자열에 있다. 앞이 `firstSetup`이다.
    - 2단계(M2 명세 3.12.2~3.12.3): 모델과 노력 값 드롭다운 `firstSetupAiModel_<칸>`,
      `firstSetupAiEffort_<칸>`(`<칸>`은 `AiSlot` 값. 라벨 뒤 '*'), 칸 이름 옆 'ⓘ'
      `firstSetupAiInfo`, 제목 옆 'ⓘ' `firstSetupIntroInfo`(2 자리의 글. 처음 설정을 마친 뒤에는
      숨김), 시작할 수 없는 까닭 `firstSetupStartReason`(단추가 눌리지 않을 때만 보임).
      "꼭 읽을 글" 'ⓘ'는 다섯 글(가, 마, 바, 사, 아)을 "• "로 잇는다.

    - 3단계(M3 명세 3.17.5. 이름은 `trade_fields.py`의 문서 문자열에 모두 있다. 앞은
      `firstSetup`이나 `settings`): 예산 `<앞>Budget`, 투자 비율 `<앞>CoinRatio_<마켓 코드>`,
      "코인 예산 {금액}원"
      `<앞>CoinBudget_<마켓 코드>`, "합계 {합}%" `<앞>CoinRatioSum`(받지 않는 합의 글
      `<앞>CoinRatioSumError`), `<앞>FirstRatio`, `<앞>MrFirstRatio`, `<앞>AddRatio`,
      `<앞>Tolerance`, 코인마다의 "첫 매수 금액 …" `<앞>CoinBuy_<마켓 코드>`, `<앞>StopRatio`,
      `<앞>DailyLoss`, `<앞>OrderInterval`, 둥근 단추 `<앞>Method_<값>`, `<앞>PauseStop_<값>`,
      `<앞>MrSell_yes`·`<앞>MrSell_no`, `<앞>MrLoss`, `<앞>PaperFunds`, `<앞>BackupDays`, 칸 아래 글
      `<칸 객체 이름>Error`, 'ⓘ' `<앞>BudgetInfo`, `<앞>RatioInfo`, `firstSetupLimitsInfo`
      (설정에는 없다), 유의·주의 코인을 고르면 `<앞>CautionNote`.
    - 처음 설정의 확인란 `firstSetupRead`("꼭 읽을 글을 읽었습니다". '꼭 읽을 글'의 제목과 'ⓘ' 바로
      아래)와 단추 `firstSetupStart`("모의매매 시작"). 꼭 넣는 칸이 모두 차고 확인란을 골랐을 때만
      눌리고, 아니면 곁에 `firstSetupStartReason`이 보인다. `firstSetupNoticesInfo`의 툴팁은
      일곱 글(가, 나, 라, 마, 바, 사, 아)이다. 누르면 칸의 꼴을 보고, 걸리면 그 칸 아래에 글이
      보이고 `save_settings`를 부르지 않는다. 걸리지 않으면 `trade=TradeSettings`를 넘긴다.
      저장 뒤 검사에 걸린 글(`SaveResult.field_errors`)은 걸린 칸 아래에 보인다.
    - 예전 기록(1·2단계를 마침)은 저장된 값이 있는 칸(코인, 알림 보관 기간, 봉 단위와 봉 개수,
      모델과 노력 값)이 채워진 채 잠기고, 3단계 칸만 고른다(M3 명세 3.11.3).
    - 설정의 가상 자금 `settingsPaperFunds`는 늘 흐리다(`set_dim`이 다시 켜지 못한다). 설정의
      "저장"은 저장된 3단계 값이 있으면 3단계의 꼭 넣는 칸이 모두 차야 눌리고 `trade`를 넘긴다.
      보유가 있는 코인을 빼고 "저장"을 누르면 SCR-06-6을 띄우고(`CoinTradeView.holding.qty > 0`),
      `BelowStopView`가 오면 SCR-06-5를 띄운다. "확인"이면 그 코인을 `accept_below_stop`에 넣어
      다시 저장하고, "취소"면 아무것도 저장하지 않는다.

    설정 자리(`screen_settings`)의 `objectName`:

    - 글 라벨: `settingsMessage`(1 자리), 단추: `settingsSave`("저장"), 묶음: `settingsForm`.
    - 자동 시작(M1 명세 3.16.1. "그 밖의 값" 카드의 알림 보관 기간 아래): 확인란 `settingsAutostart`
      ("PC를 켜면 자동으로 시작"), 그 옆 'ⓘ' `settingsAutostartInfo`(툴팁이 글 하나. 이 실행에서
      쓸 수 없으면 둘째 줄이 더해져 두 줄이 "• "로 시작), 결과 글 `settingsAutostartMessage`
      (`SettingsView.autostart_error`가 있을 때만 보이고 `tone`이 "warning"). 쓸 수 없으면
      (`autostart_supported`가 거짓) 확인란은 흐리고 "저장"이 `autostart`를 넘기지 않는다.
    - 코인 고르기(찾기 칸 `settingsCoinSearch`), 봉 단위(`settingsUnit_<봉 단위 값>`)와 봉 개수
      (`settingsCount_<수>`), 보관 기간의 안쪽 이름은 앞이 `settings`다.
    - 2단계: 모델과 노력 값 `settingsAiModel_<칸>`, `settingsAiEffort_<칸>`, 'ⓘ' `settingsAiInfo`,
      네 칸 중 일부만 골랐을 때의 경고 `settingsAiWarning`, "되돌리기" `settingsRevert`(저장된
      값으로 칸을 되돌리고 동작은 부르지 않는다), 제목 옆 'ⓘ' `settingsIntroInfo`.
      "저장"은 여덟 값이 모두 골라졌을 때만 `ai_choices`를 넘긴다.

    현황 자리(`screen_status`)의 `objectName`(`<m>`은 마켓 코드, 자세한 것은 `status_screen.py`):

    - 글 하나만 보이는 모습(설정을 불러오는 중, 처음 설정 전, 설정을 읽지 못함): 라벨
      `statusMessage`가 보이고 묶음 `statusContent`가 숨겨진다.
      설정을 읽지 못함은 `tone`이 "warning"이다.
    - 코인 칸: 묶음 `statusCoins` 안에 고른 코인마다 `statusCoin_<m>`(설정 목록 순서, 최대 셋).
      그 안에 `statusCoinName_<m>`, 값 자리의 글 `statusCoinNote_<m>`(글이 없으면 숨김),
      "다시 시도" `statusCoinRetry_<m>`(받지 못함과 일시 차단 중에만 보임),
      `statusCoinIncomplete_<m>`(미완성 봉 글), `statusCoinDisclaimer_<m>`('ⓘ'. 툴팁이
      보장 아님 글).
    - 봉 통계(`CoinView.stats`. M1 명세 3.14.5): 묶음 `statusStats`, 코인 고르기
      `statusStatsCoin`(`QComboBox`), 표 자리의 글 `statusStatsNote`, 표 위 글 `statusStatsSummary`,
      표 묶음 `statusStatsTable`(머리 `statusStatsHeader_<0~1>`,
      칸 `statusStatsCell_<줄 0~12>_<칸 0~1>`. 칸 0은 항목, 칸 1은 값),
      제목 옆 'ⓘ' `statusStatsDisclaimer`(툴팁이 보장 아님 글),
      `statusStatsFetched`("마지막으로 받은 때" 줄),
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
    - 코인 칸의 "보고서 보기" `statusCoinReport_<m>`(늘 보이고, 누르면 보고서 화면에서 그 코인을
      연다). 칸의 빈 곳을 눌러도 같다.
    - 알림센터 2단계: "가 보기" `alertGo_<id>`(오류·기타 알림을 펼쳤을 때), 줄은 Tab으로 초점을
      받고 Enter·Space로 누른 것과 같다, 주소를 읽지 못함 `alertWebhookUnreadable`(경고 색).
    - 화면 이름 목록의 현황 줄: `screenNav`의 `ScreenId` 차례의 줄(`item(1)`)의 글이 "현황"이나
      "현황 ({n})"이다. 사용자가 그 줄을 누른 것은 `screenNav.itemClicked`로 알린다(시험은
      `itemClicked.emit(item)`). `select_screen`은 누른 것이 아니라서 마지막에 고른 탭을
      그대로 둔다.
    - 왼쪽 자리 `screenLeft`(M2 명세 3.16): 화면 이름 목록 `screenNav`(줄은 여섯 그대로)과 그 아래의
      "도움말" 단추 `helpButton`. 단추나 F1은 도움말 창(`ui/help.py`의 `HelpWindow`, 객체 이름
      `helpWindow`, 글 칸 `helpText`, 못 읽음의 글 `helpMessage`)을 연다. 이미 열려 있으면 그 창을
      앞으로 낸다. 글은 `guide_path()`의 파일이고, 창을 열 때마다 읽는다.
    - Ctrl+1~6은 화면 이름 목록의 위에서 1~6째 줄을 누른 것과 같다(현황 다음이 계정).
    - 계정 자리(`screen_account`, M5 명세 3.15.3): "새로 고침" `accountRefresh`, 글
      `accountMessage`, `accountGoKey`, 묶음 `accountSummary`('ⓘ' `accountSummaryInfo`, 값
      `accountKrwAvailable`, `accountKrwLocked`, `accountHoldingsValue`, `accountTotalAssets`,
      `accountTotalBuy`, `accountTotalPnl`, `accountTotalPnlRate`, `accountReadAt`,
      `accountExcluded`), 표 `accountTable`과 표 자리의 글 `accountTableNote`. 창이 계정 자리를
      고르거나 떠나고 최소화·숨음·복원될 때 `account_visible`을 부른다.
    - 보고서 자리(`screen_reports`, M2 명세 3.12.1): 맨 위 줄 `reportAiStatus`, 코인 `reportCoin`
      (`QListWidget`), 회차 `reportRounds`(`QListWidget`), 글 하나만 보이는 모습 `reportMessage`,
      `reportAiMark`, 오른쪽 `reportDetailScroll`(`QScrollArea`)의 묶음 `reportLastResult`,
      `reportChecks`, `reportDecision`, `reportEvidence`, `reportSources`, `reportUsage`,
      `reportNotices`, 'ⓘ' `reportUsageInfo`, `reportNoticesInfo`(판단의 'ⓘ'는 3단계에서 없앴다).
    - 3단계의 공통 틀(M3 명세 3.17.1): 화면 자리 위의 상태 띠 `tradeBand`(칸 `tradeBandMode`,
      `tradeBandPause`, `tradeBandStopOff`, `tradeBandStarting`, 누르는 칸 `tradeBandAlerts`
      "알림 {n}건"), 단추 `pauseButton`, `sellAllButton`(SCR-06-7 뒤에 `pause_and_sell_all`),
      `resumeUserButton`, `resumeErrorButton`, 곁의 글 `tradeBandResumeNote`,
      `tradeBandSellAllNote`. 화면 이름 목록에는 " ({n})"이 없다.
    - 3단계의 현황(M3 명세 3.17.2): 제목 옆 'ⓘ' `statusIntroInfo`, 오류 멈춤의 줄
      `statusErrorPause`, 오늘 요약 `statusToday`, 코인 칸의 3단계 줄 `statusCoinTrade_<m>`,
      지금 회차 `statusRoundNow`, 모의 성적 `statusPerformance`와 'ⓘ' `statusPerformanceInfo`
      (이름은 `trade_status.py`의 문서 문자열에 모두 있다).
    - 3단계의 보고서(M3 명세 3.17.3): 판단으로 낸 주문 `reportOrder`, 주문까지 걸린 시간
      `reportOrderElapsed`, 멈춤의 맨 위 줄 `reportPause`.
    - 기록 자리(`screen_history`, M3 명세 3.17.4): 코인 `historyCoin`(`QComboBox`), 날짜
      `historyDay`(`QDateEdit`), 탭 `historyTab_<rounds|judgments|orders|fills|pauses>`, 표
      `historyTable`(`QTableWidget`), 표 자리의 글 하나 `historyMessage`, 자세한 글 `historyDetail`,
      "보고서 보기" `historyReport`, "CSV로 내보내기" `historyExport`와 결과 글
      `historyExportMessage`. 파일 저장 창은 `window.set_save_dialog_handler(handler)`로
      `handler(처음 이름) -> 경로`를 걸어 대신한다(빈 글이면 취소).
    - 5단계의 설정(M5 명세 3.11): 업비트 키 묶음 `settingsUpbitKey`(제목 옆 'ⓘ'
      `settingsUpbitKeyInfo`, 상태 `settingsUpbitKeyState`, 만든 날
      `settingsUpbitKeyCreated`, 만료일 `settingsUpbitKeyExpires`, 단추 `settingsUpbitKeySet`,
      `settingsUpbitKeyDelete`, 곁의 글 `settingsUpbitKeyLockNote`, 결과 글
      `settingsUpbitKeyMessage`), 키 넣기 창 `upbitKeyDialog`(칸
      `upbitKeyAccess`, `upbitKeySecret`, `upbitKeyCreated`, 칸 아래 글 `<칸 이름>Error`, 단추
      `upbitKeySave`, `upbitKeyCancel`. 창의 `findChild`로 `upbitKeyDialog`를 찾는다),
      실거래 설정 묶음 `settingsLive`('ⓘ' `settingsLiveInfo`, 칸 `settingsLiveBudget`,
      `settingsLiveCoinRatio_<m>`, `settingsLiveCoinBudget_<m>`, `settingsLiveCoinRatioSum`,
      `settingsLiveDailyLoss`, 칸 아래 글 `<칸 이름>Error`, 흐릴 때 곁의 글 `settingsLiveLockNote`),
      모드 묶음 `settingsMode`('ⓘ' `settingsModeInfo`, 글 `settingsModeState`, 단추
      `settingsLiveOn`, `settingsLiveOff`, 막힌 까닭 `settingsLiveBlocks`, 센 수
      `settingsLiveCounts`, 전량 매도 기록 `settingsLiveSellAll`, N `settingsLiveRoundsN`, 결과 글
      `settingsModeMessage`). 실거래 중의 곁의 글은 `settingsCoinsLiveNote`,
      `settingsMethodLockNote`, `settingsCandleLockNote`다. SCR-06-9는 `ui/dialogs.py`의
      `build_live_confirm_box`가 만들고 표 `liveConfirmTable`, "{시각}에 읽은 값"
      `liveConfirmReadAt`, 단추 `confirmOk`와 `confirmCancel`이 있다. 확인 창 대신
      `set_confirm_handler`를 걸면 SCR-06-9~06-11도 그 `handler(제목, 본문)`이 답한다.
    - 5단계의 기록과 보고서(M5 명세 3.11): 모드 고르기 `historyMode_<paper|live>`,
      `reportsMode_<paper|live>`, 기록의 외부 변동 탭 `historyTab_external`. 실거래 기록이면 기록
      화면의 표 카드 `historyCard`와 보고서 화면의 목록 카드 `reportLeftCard`가 `error` 테두리다.
      실거래이면 상태 띠 `tradeBand`와 현황의 코인 칸 `statusCoin_<m>`도 같고, 오늘 요약의
      `statusTodayKrw`는 "원화 {금액}원"이며 모의 성적 `statusPerformanceCard`는 숨는다.
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
