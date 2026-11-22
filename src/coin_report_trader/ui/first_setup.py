"""처음 설정 화면(설계서 3, M1 명세 2의 6~8)."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from coin_report_trader.core import SCREEN_TITLES, AiChoice, CandleUnit, Market, ScreenId
from coin_report_trader.ui import theme
from coin_report_trader.ui.ai_choice import AiChoices
from coin_report_trader.ui.candle_choice import CandleChoice
from coin_report_trader.ui.cards import Card, CardGrid, title_row
from coin_report_trader.ui.coin_picker import CoinPicker
from coin_report_trader.ui.info_icon import InfoIcon, info_text
from coin_report_trader.ui.retention import RetentionChoice
from coin_report_trader.ui.screens import (
    TITLE_NAME,
    screen_object_name,
    title_label,
)
from coin_report_trader.ui.state import Message, Phase, PickerMode
from coin_report_trader.ui.texts import (
    ADVICE_NOTICE,
    AI_USAGE_NOTE,
    COINS_LABEL_REQUIRED,
    FIRST_SETUP_DONE,
    FIRST_SETUP_INTRO,
    LEGACY_NOTICE,
    NOTICE_DISCORD_NO_ADDRESS,
    NOTICE_DISCORD_OFFLINE,
    NOTICE_PERSONAL,
    NOTICES_TITLE,
    RETENTION_LABEL_REQUIRED,
    START_REASON,
    START_TEXT,
    TO_SETTINGS_TEXT,
)
from coin_report_trader.ui.widgets import make_label, set_dim, set_role, show_message

PREFIX = "firstSetup"
MESSAGE_NAME = "firstSetupMessage"
FORM_NAME = "firstSetupForm"
NOTICES_NAME = "firstSetupNotices"
NOTICES_TITLE_NAME = "firstSetupNoticesTitle"
NOTICES_INFO_NAME = "firstSetupNoticesInfo"
LEGACY_NOTICE_NAME = "firstSetupLegacyNotice"
DONE_PANEL_NAME = "firstSetupDonePanel"
DONE_NAME = "firstSetupDone"
TO_SETTINGS_NAME = "firstSetupToSettings"
START_NAME = "firstSetupStart"
START_REASON_NAME = "firstSetupStartReason"
INTRO_INFO_NAME = "firstSetupIntroInfo"


class FirstSetupScreen(QWidget):
    """처음 설정을 마치기 전에는 코인 고르기, 봉 단위, 봉 개수, 알림 보관 기간, 꼭 읽을 글,
    "저장하고 시작"이 보인다. 마친 뒤에는 꼭 읽을 글과 끝났다는 글, "설정 화면으로"만 보인다.

    "저장하고 시작"은 코인, 알림 보관 기간, 봉 단위가 골라져 있을 때만 눌린다(봉 개수는 처음부터
    "200개"가 골라져 있다). 누르면 `start_requested(coins, retention_days, candle_unit,
    candle_count, ai_choices)`, "다시 시도"를 누르면 `retry_requested`, "설정 화면으로"를 누르면
    `open_settings_requested`가 난다. 이 화면은 동작을 직접 부르지 않는다(창이 부른다).

    예전 기록(1단계 보완 전에 처음 설정을 마침. M1 명세 3.14.4)이면 코인 고르기와 알림 보관 기간
    칸이 잠기고 저장된 값이 채워지며, 그 위에 `firstSetupLegacyNotice`가 보인다.

    카드(M1 명세 3.14.7): 코인, 봉 단위·봉 개수, 알림 보관 기간, 꼭 읽을 글. 꼭 읽을 글 카드의 제목
    `firstSetupNoticesTitle` 옆 'ⓘ' `firstSetupNoticesInfo`의 툴팁이 세 글(투자 자문이 아님,
    디스코드 끊김, 디스코드 주소 없음)을 "• "로 시작하는 줄로 잇는다.
    옛 글 라벨은 없다(M1 명세 3.14.6).
    """

    start_requested = Signal(object, int, object, int, object)
    retry_requested = Signal()
    open_settings_requested = Signal()
    edited = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName(screen_object_name(ScreenId.FIRST_SETUP))
        self._phase = Phase.READY
        self._done = False
        self._legacy = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*(theme.SCREEN_PADDING,) * 4)
        layout.setSpacing(theme.CARD_GAP)
        title_line = QHBoxLayout()
        title_line.setContentsMargins(0, 0, 0, 0)
        title_line.setSpacing(theme.ICON_GAP)
        title_line.addWidget(title_label(SCREEN_TITLES[ScreenId.FIRST_SETUP], TITLE_NAME))
        # 2 자리의 안내 글은 제목 옆 'ⓘ'의 툴팁이다. 처음 설정을 마친 뒤에는 숨긴다(M2 명세 3.12.5).
        self._intro_info = InfoIcon(
            SCREEN_TITLES[ScreenId.FIRST_SETUP], FIRST_SETUP_INTRO, INTRO_INFO_NAME
        )
        title_line.addWidget(self._intro_info)
        title_line.addStretch(1)
        layout.addLayout(title_line)
        self._message = make_label("", MESSAGE_NAME)
        self._message.hide()
        layout.addWidget(self._message)
        # 예전 기록일 때만 2 자리 바로 아래에 보인다(M1 명세 3.14.4).
        self._legacy_notice = make_label(LEGACY_NOTICE, LEGACY_NOTICE_NAME)
        self._legacy_notice.hide()
        layout.addWidget(self._legacy_notice)

        self._form = QWidget()
        self._form.setObjectName(FORM_NAME)
        form_layout = QVBoxLayout(self._form)
        form_layout.setContentsMargins(0, 0, 0, 0)
        self._picker = CoinPicker(PREFIX, COINS_LABEL_REQUIRED)
        self._candle = CandleChoice(PREFIX)
        self._retention = RetentionChoice(PREFIX, RETENTION_LABEL_REQUIRED)
        self._ai = AiChoices(PREFIX, required=True)
        cards = []
        for part in (self._picker, self._candle, self._retention, self._ai):
            card = Card()
            card.body.addWidget(part)
            if part is not self._picker:
                # 오른쪽 카드는 내용을 위쪽에 붙이고, 남는 높이는 아래의 빈 자리가 받는다.
                card.body.addStretch(1)
            cards.append(card)
        grid = CardGrid(split_first=True, max_columns=2)
        grid.set_cards(cards)
        form_layout.addWidget(grid)
        layout.addWidget(self._form)

        notices = Card(NOTICES_NAME)
        # 10 자리: 가, 마, 바, 사, 아 순서(M2 명세 3.12.3). 다섯 글은 제목 옆 'ⓘ'의 툴팁이다.
        notices_info = InfoIcon(
            NOTICES_TITLE,
            info_text(
                ADVICE_NOTICE,
                NOTICE_DISCORD_OFFLINE,
                NOTICE_DISCORD_NO_ADDRESS,
                NOTICE_PERSONAL,
                AI_USAGE_NOTE,
            ),
            NOTICES_INFO_NAME,
        )
        notices.body.addLayout(title_row(NOTICES_TITLE, NOTICES_TITLE_NAME, notices_info))
        layout.addWidget(notices)

        self._done_panel = QWidget()
        self._done_panel.setObjectName(DONE_PANEL_NAME)
        done_layout = QVBoxLayout(self._done_panel)
        done_layout.setContentsMargins(0, 0, 0, 0)
        done_layout.setSpacing(theme.FIELD_GAP)
        done_layout.addWidget(make_label(FIRST_SETUP_DONE, DONE_NAME))
        to_settings = QPushButton(TO_SETTINGS_TEXT)
        to_settings.setObjectName(TO_SETTINGS_NAME)
        to_settings.clicked.connect(self.open_settings_requested)
        done_layout.addWidget(to_settings, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._done_panel)

        self._start = QPushButton(START_TEXT)
        self._start.setObjectName(START_NAME)
        set_role(self._start, theme.ROLE_PRIMARY)
        self._start.clicked.connect(self._on_start)
        self._start_reason = make_label(START_REASON, START_REASON_NAME)
        set_role(self._start_reason, theme.ROLE_SUB)
        start_row = QHBoxLayout()
        start_row.addStretch(1)
        start_row.addWidget(self._start_reason)
        start_row.addWidget(self._start)
        layout.addLayout(start_row)
        layout.addStretch(1)

        self._picker.changed.connect(self._on_edit)
        self._candle.changed.connect(self._on_edit)
        self._retention.changed.connect(self._on_edit)
        self._ai.changed.connect(self._on_edit)
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
        self,
        *,
        phase: Phase,
        done: bool,
        picker: PickerMode,
        message: Message | None,
        legacy: bool = False,
    ) -> None:
        self._phase = phase
        self._done = done
        self._legacy = legacy and not done
        editing = phase is Phase.READY and not done
        self._form.setVisible(not (phase is Phase.READY and done))
        self._start.setVisible(not (phase is Phase.READY and done))
        self._done_panel.setVisible(phase is Phase.READY and done)
        self._intro_info.setVisible(not (phase is Phase.READY and done))
        # 불러오는 중이거나 설정을 읽지 못했으면 칸은 흐리다('ⓘ'는 흐린 때도 듣는다).
        set_dim(self._form, not editing)
        self._picker.show_mode(picker)
        # 예전 기록: 코인 고르기와 알림 보관 기간은 저장된 값 그대로 잠근다.
        # 고를 수 있는 것은 봉 단위와 봉 개수뿐이다.
        self._picker.set_locked(self._legacy)
        set_dim(self._retention, not editing or self._legacy)
        self._legacy_notice.setVisible(self._legacy)
        show_message(self._message, message)
        self._update_start()

    # --- 안쪽 ---

    def _on_edit(self) -> None:
        self._update_start()
        self.edited.emit()

    def _update_start(self) -> None:
        ready = self._ready_values() is not None
        self._start.setEnabled(ready)
        # 누를 수 없을 때 단추 곁에 까닭이 늘 보인다(누를 수 있으면 보이지 않는다).
        self._start_reason.setVisible(not ready and not self._start.isHidden())

    def _ready_values(
        self,
    ) -> tuple[tuple[str, ...], int, CandleUnit, int, tuple[AiChoice, ...]] | None:
        """ "저장하고 시작"을 누를 수 있는 값(코인, 보관 기간, 봉 단위, 봉 개수, 네 칸의
        `AiChoice`). 하나라도 비면 `None`."""
        coins = self._picker.picks()
        days = self._retention.value()
        unit = self._candle.unit()
        count = self._candle.count()
        ai = self._ai.choices()
        if (
            self._phase is not Phase.READY
            or self._done
            or not coins
            or days is None
            or unit is None
            or count is None
            or ai is None
        ):
            return None
        return coins, days, unit, count, ai

    def _on_start(self) -> None:
        values = self._ready_values()
        if not self._start.isEnabled() or values is None:
            return
        self.start_requested.emit(*values)
