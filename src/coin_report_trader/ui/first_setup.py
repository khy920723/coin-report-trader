"""처음 설정 화면(설계서 3, M1 명세 2의 6~8)."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from coin_report_trader.core import (
    SCREEN_TITLES,
    AiChoice,
    CandleUnit,
    Market,
    ScreenId,
    TradeSettings,
)
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
    NOTICE_AUTO_ORDER,
    NOTICE_DISCORD_NO_ADDRESS,
    NOTICE_DISCORD_OFFLINE,
    NOTICE_PERSONAL,
    NOTICE_STOP_OFF,
    NOTICES_TITLE,
    READ_LABEL,
    RETENTION_LABEL_REQUIRED,
    START_REASON,
    START_TEXT,
    TO_SETTINGS_TEXT,
)
from coin_report_trader.ui.trade_fields import TradeFields
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
READ_NAME = "firstSetupRead"


class FirstSetupScreen(QWidget):
    """처음 설정을 마치기 전에는 코인 고르기, 3단계 칸(예산과 투자 비율, 매수 비율, 손절과 한도,
    그 밖의 값), 봉 단위, 봉 개수, 알림 보관 기간, 꼭 읽을 글과 확인란, "모의매매 시작"이 보인다.
    마친 뒤에는 꼭 읽을 글과 끝났다는 글, "설정 화면으로"만 보인다.

    "모의매매 시작"은 꼭 넣는 칸(3단계 칸과 모델·노력 값 포함)이 모두 차 있고 "꼭 읽을 글을
    읽었습니다"(`firstSetupRead`)가 골라져 있을 때만 눌린다(봉 개수는 처음부터 "200개"). 누르면
    칸의 꼴을 보고(틀리면 그 칸 아래에 글이 보이고 아무것도 내지 않는다) `start_requested(coins,
    retention_days, candle_unit, candle_count, ai_choices, trade)`, "다시 시도"를 누르면
    `retry_requested`, "설정 화면으로"를 누르면 `open_settings_requested`가 난다. 이 화면은 동작을
    직접 부르지 않는다(창이 부른다). 저장 뒤 검사에 걸린 글은 `show_errors`가 칸 아래에 보인다.

    예전 기록(1·2단계를 마친 기록. M3 명세 3.11.3)이면 저장된 값이 있는 칸(코인, 알림 보관 기간,
    봉 단위와 봉 개수, 모델과 노력 값)이 채워진 채 잠기고, 그 위에 `firstSetupLegacyNotice`가
    보인다. 저장되지 않은 칸(3단계 칸)만 고른다.

    카드(M3 명세 3.17.5): 코인, 예산과 투자 비율, 매수 비율, 손절과 한도, 봉 단위·봉 개수,
    Claude 모델, 그 밖의 값(알림 보관 기간 포함), 꼭 읽을 글. 꼭 읽을 글 카드의 제목
    `firstSetupNoticesTitle` 옆 'ⓘ' `firstSetupNoticesInfo`의 툴팁이 일곱 글(가, 나, 라, 마, 바,
    사, 아)을 "• "로 시작하는 줄로 잇고, 그 아래에 확인란 `firstSetupRead`가 있다.
    옛 글 라벨은 없다(M1 명세 3.14.6).
    """

    start_requested = Signal(object, int, object, int, object, object)
    retry_requested = Signal()
    open_settings_requested = Signal()
    edited = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName(screen_object_name(ScreenId.FIRST_SETUP))
        self._phase = Phase.READY
        self._done = False
        self._legacy = False
        self._saved_unit: CandleUnit | None = None
        self._saved_ai: tuple[AiChoice, ...] = ()

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
        self._fields = TradeFields(PREFIX, required=True)
        # 차례(PPT 5-1 판 4의 26~27장): 코인 → 예산과 투자 비율 → 매수 비율 → 손절과 한도 → 봉 단위·
        # 봉 개수 → Claude 모델 → 그 밖의 값(알림 보관 기간 포함).
        budget, ratio, limits, other = self._fields.cards(self._retention)
        cards = []
        for part in (self._picker, budget, ratio, limits, self._candle, self._ai):
            if isinstance(part, Card):
                card = part
            else:
                card = Card()
                card.body.addWidget(part)
                if part is not self._picker:
                    # 오른쪽 카드는 내용을 위쪽에 붙이고, 남는 높이는 아래의 빈 자리가 받는다.
                    card.body.addStretch(1)
            cards.append(card)
        cards.append(other)
        grid = CardGrid(split_first=True, max_columns=2)
        grid.set_cards(cards)
        form_layout.addWidget(grid)
        layout.addWidget(self._form)

        notices = Card(NOTICES_NAME)
        # 10 자리: 가, 나, 라, 마, 바, 사, 아 순서(M3 명세 3.17). 일곱 글은 제목 옆 'ⓘ'의 툴팁이다.
        notices_info = InfoIcon(
            NOTICES_TITLE,
            info_text(
                ADVICE_NOTICE,
                NOTICE_STOP_OFF,
                NOTICE_AUTO_ORDER,
                NOTICE_DISCORD_OFFLINE,
                NOTICE_DISCORD_NO_ADDRESS,
                NOTICE_PERSONAL,
                AI_USAGE_NOTE,
            ),
            NOTICES_INFO_NAME,
        )
        notices.body.addLayout(title_row(NOTICES_TITLE, NOTICES_TITLE_NAME, notices_info))
        # 확인란은 '꼭 읽을 글'의 제목과 'ⓘ' 바로 아래다(M3 명세 3.17.5).
        self._read = QCheckBox(READ_LABEL)
        self._read.setObjectName(READ_NAME)
        self._read.toggled.connect(self._on_edit)
        notices.body.addWidget(self._read)
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

        self._picker.changed.connect(self._on_picks)
        self._fields.changed.connect(self._on_edit)
        self._candle.changed.connect(self._on_edit)
        self._retention.changed.connect(self._on_edit)
        self._ai.changed.connect(self._on_edit)
        self._picker.retry_requested.connect(self.retry_requested)
        self._done_panel.hide()
        self._update_start()

    # --- 창이 부르는 것 ---

    def load_saved(
        self,
        coins: tuple[str, ...],
        retention_days: int | None,
        candle_unit: CandleUnit | None = None,
        candle_count: int | None = None,
        ai_choices: tuple[AiChoice, ...] = (),
        trade: TradeSettings | None = None,
    ) -> None:
        """저장된 값을 칸에 채운다. 예전 기록(3.11.3)은 저장된 칸이 채워진 채 잠긴다(`render`)."""
        self._picker.set_picks(coins)
        self._fields.set_coins(coins)
        self._retention.set_value(retention_days)
        self._saved_unit = candle_unit
        self._saved_ai = ai_choices
        if candle_unit is not None:
            self._candle.set_values(candle_unit, candle_count)
        if ai_choices:
            self._ai.set_choices(ai_choices)
        if trade is not None:
            self._fields.set_values(trade)
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
        # 저장된 값이 있는 칸은 채워 둔 채 잠그고, 저장되지 않은 칸만 고른다(3.11.3).
        set_dim(self._candle, not editing or (self._legacy and self._saved_unit is not None))
        set_dim(self._ai, not editing or (self._legacy and bool(self._saved_ai)))
        self._legacy_notice.setVisible(self._legacy)
        show_message(self._message, message)
        self._update_start()

    # --- 안쪽 ---

    def _on_picks(self) -> None:
        self._fields.set_coins(self._picker.picks())
        self._on_edit()

    def _on_edit(self) -> None:
        self._update_start()
        self.edited.emit()

    def show_errors(self, errors) -> None:
        """저장 뒤 검사에 걸린 칸의 글(`SaveResult.field_errors`)을 칸 아래에 보인다."""
        self._fields.show_errors(errors)

    def _update_start(self) -> None:
        ready = self._ready_values() is not None
        self._start.setEnabled(ready)
        # 누를 수 없을 때 단추 곁에 까닭이 늘 보인다(누를 수 있으면 보이지 않는다).
        self._start_reason.setVisible(not ready and not self._start.isHidden())

    def _ready_values(
        self,
    ) -> tuple[tuple[str, ...], int, CandleUnit, int, tuple[AiChoice, ...]] | None:
        """ "모의매매 시작"을 누를 수 있는 값(코인, 보관 기간, 봉 단위, 봉 개수, 네 칸의
        `AiChoice`). 3단계 칸이 모두 차 있고 "꼭 읽을 글을 읽었습니다"가 골라져 있어야 한다.
        하나라도 비면 `None`."""
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
            or not self._read.isChecked()
            or not self._fields.is_complete()
        ):
            return None
        return coins, days, unit, count, ai

    def _on_start(self) -> None:
        values = self._ready_values()
        if not self._start.isEnabled() or values is None:
            return
        trade = self._fields.trade()  # 꼴이 틀리면 그 칸 아래에 글이 보이고 저장은 부르지 않는다
        if trade is None:
            return
        self.start_requested.emit(*values, trade)
