"""처음 설정 화면(설계서 3, M1 명세 2의 6~8)."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from coin_report_trader.core import SCREEN_TITLES, Market, ScreenId
from coin_report_trader.ui.coin_picker import CoinPicker
from coin_report_trader.ui.retention import RetentionChoice
from coin_report_trader.ui.screens import (
    NOTICE_NAME,
    TITLE_NAME,
    screen_object_name,
    title_label,
)
from coin_report_trader.ui.state import Message, Phase, PickerMode
from coin_report_trader.ui.texts import (
    ADVICE_NOTICE,
    COINS_LABEL_REQUIRED,
    FIRST_SETUP_DONE,
    NOTICE_DISCORD_NO_ADDRESS,
    NOTICE_DISCORD_OFFLINE,
    RETENTION_LABEL_REQUIRED,
    START_TEXT,
    TO_SETTINGS_TEXT,
)
from coin_report_trader.ui.widgets import make_label, show_message

PREFIX = "firstSetup"
MESSAGE_NAME = "firstSetupMessage"
FORM_NAME = "firstSetupForm"
NOTICES_NAME = "firstSetupNotices"
NOTICE_OFFLINE_NAME = "noticeDiscordOffline"
NOTICE_NO_ADDRESS_NAME = "noticeDiscordNoAddress"
DONE_PANEL_NAME = "firstSetupDonePanel"
DONE_NAME = "firstSetupDone"
TO_SETTINGS_NAME = "firstSetupToSettings"
START_NAME = "firstSetupStart"


class FirstSetupScreen(QWidget):
    """처음 설정을 마치기 전에는 코인 고르기와 알림 보관 기간, 꼭 읽을 글, "저장하고 시작"이 보인다.
    마친 뒤에는 꼭 읽을 글과 끝났다는 글, "설정 화면으로"만 보인다.

    "저장하고 시작"을 누르면 `start_requested(coins, retention_days)`, "다시 시도"를 누르면
    `retry_requested`, "설정 화면으로"를 누르면 `open_settings_requested`가 난다. 이 화면은 동작을
    직접 부르지 않는다(창이 부른다).
    """

    start_requested = Signal(object, int)
    retry_requested = Signal()
    open_settings_requested = Signal()
    edited = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName(screen_object_name(ScreenId.FIRST_SETUP))
        self._phase = Phase.READY
        self._done = False

        layout = QVBoxLayout(self)
        layout.addWidget(title_label(SCREEN_TITLES[ScreenId.FIRST_SETUP], TITLE_NAME))
        self._message = make_label("", MESSAGE_NAME)
        self._message.hide()
        layout.addWidget(self._message)

        self._form = QWidget()
        self._form.setObjectName(FORM_NAME)
        form_layout = QVBoxLayout(self._form)
        form_layout.setContentsMargins(0, 0, 0, 0)
        self._picker = CoinPicker(PREFIX, COINS_LABEL_REQUIRED)
        self._retention = RetentionChoice(PREFIX, RETENTION_LABEL_REQUIRED)
        form_layout.addWidget(self._picker)
        form_layout.addWidget(self._retention)
        layout.addWidget(self._form)

        notices = QWidget()
        notices.setObjectName(NOTICES_NAME)
        notices_layout = QVBoxLayout(notices)
        notices_layout.setContentsMargins(0, 0, 0, 0)
        # 10 자리: 가, 마, 바 순서(설계서 10.2).
        notices_layout.addWidget(make_label(ADVICE_NOTICE, NOTICE_NAME))
        notices_layout.addWidget(make_label(NOTICE_DISCORD_OFFLINE, NOTICE_OFFLINE_NAME))
        notices_layout.addWidget(make_label(NOTICE_DISCORD_NO_ADDRESS, NOTICE_NO_ADDRESS_NAME))
        layout.addWidget(notices)

        self._done_panel = QWidget()
        self._done_panel.setObjectName(DONE_PANEL_NAME)
        done_layout = QVBoxLayout(self._done_panel)
        done_layout.setContentsMargins(0, 0, 0, 0)
        done_layout.addWidget(make_label(FIRST_SETUP_DONE, DONE_NAME))
        to_settings = QPushButton(TO_SETTINGS_TEXT)
        to_settings.setObjectName(TO_SETTINGS_NAME)
        to_settings.clicked.connect(self.open_settings_requested)
        done_layout.addWidget(to_settings)
        layout.addWidget(self._done_panel)

        self._start = QPushButton(START_TEXT)
        self._start.setObjectName(START_NAME)
        self._start.clicked.connect(self._on_start)
        layout.addWidget(self._start)
        layout.addStretch(1)

        self._picker.changed.connect(self._on_edit)
        self._retention.changed.connect(self._on_edit)
        self._picker.retry_requested.connect(self.retry_requested)
        self._done_panel.hide()
        self._update_start()

    # --- 창이 부르는 것 ---

    def load_saved(self, coins: tuple[str, ...], retention_days: int | None) -> None:
        self._picker.set_picks(coins)
        self._retention.set_value(retention_days)
        self._update_start()

    def set_markets(self, markets: tuple[Market, ...]) -> None:
        self._picker.set_markets(markets)

    def render(
        self, *, phase: Phase, done: bool, picker: PickerMode, message: Message | None
    ) -> None:
        self._phase = phase
        self._done = done
        editing = phase is Phase.READY and not done
        self._form.setVisible(not (phase is Phase.READY and done))
        self._start.setVisible(not (phase is Phase.READY and done))
        self._done_panel.setVisible(phase is Phase.READY and done)
        # 불러오는 중이거나 설정을 읽지 못했으면 칸은 흐리다.
        self._form.setEnabled(editing)
        self._picker.show_mode(picker)
        show_message(self._message, message)
        self._update_start()

    # --- 안쪽 ---

    def _on_edit(self) -> None:
        self._update_start()
        self.edited.emit()

    def _update_start(self) -> None:
        coins = self._picker.picks()
        days = self._retention.value()
        self._start.setEnabled(
            self._phase is Phase.READY and not self._done and bool(coins) and days is not None
        )

    def _on_start(self) -> None:
        coins = self._picker.picks()
        days = self._retention.value()
        if not self._start.isEnabled() or not coins or days is None:
            return
        self.start_requested.emit(coins, days)
