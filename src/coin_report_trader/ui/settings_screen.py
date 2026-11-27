"""설정 화면(설계서 6, M1 명세 4절 15번)."""

from datetime import date

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from coin_report_trader.core import (
    DEFAULT_CANDLE_COUNT,
    SCREEN_TITLES,
    AiChoice,
    CandleUnit,
    LiveSettings,
    LiveStatusView,
    Market,
    Mode,
    ScreenId,
    TradeSettings,
    UpbitKeyView,
)
from coin_report_trader.core.texts import SETTINGS_SAVED
from coin_report_trader.ui import theme
from coin_report_trader.ui.ai_choice import AiChoices
from coin_report_trader.ui.candle_choice import CandleChoice
from coin_report_trader.ui.cards import Card, CardGrid
from coin_report_trader.ui.coin_picker import CoinPicker
from coin_report_trader.ui.info_icon import InfoIcon, info_text
from coin_report_trader.ui.live_cards import ModeCard, UpbitKeyCard
from coin_report_trader.ui.live_fields import LiveFields
from coin_report_trader.ui.retention import RetentionChoice
from coin_report_trader.ui.screens import TITLE_NAME, screen_object_name, title_label
from coin_report_trader.ui.state import Message, Phase, PickerKind, PickerMode, Tone
from coin_report_trader.ui.texts import (
    AI_PARTIAL_WARNING,
    AUTOSTART_INFO,
    AUTOSTART_LABEL,
    AUTOSTART_UNSUPPORTED,
    COINS_LABEL,
    PAPER_ONLY_NOTE,
    RETENTION_LABEL,
    REVERT_TEXT,
    SAVE_TEXT,
    SETTINGS_INTRO,
)
from coin_report_trader.ui.trade_fields import TradeFields
from coin_report_trader.ui.widgets import make_label, set_dim, set_role, show_message

PREFIX = "settings"
MESSAGE_NAME = "settingsMessage"
FORM_NAME = "settingsForm"
SAVE_NAME = "settingsSave"
REVERT_NAME = "settingsRevert"
INTRO_INFO_NAME = "settingsIntroInfo"
AI_WARNING_NAME = "settingsAiWarning"
AUTOSTART_NAME = "settingsAutostart"
AUTOSTART_INFO_NAME = "settingsAutostartInfo"
AUTOSTART_MESSAGE_NAME = "settingsAutostartMessage"


class AutostartChoice(QWidget):
    """ "PC를 켜면 자동으로 시작" 확인란과 그 옆 'ⓘ', 아래의 결과 글(M1 명세 3.16.1).

    `objectName`: `settingsAutostart`(확인란), `settingsAutostartInfo`('ⓘ'. 툴팁은 글 하나이고,
    이 실행에서 쓸 수 없으면 둘째 줄이 더해져 두 줄이 "• "로 시작한다), `settingsAutostartMessage`
    (`autostart_error`가 있을 때만 보이는 '오류' 색 글. `tone`이 "warning").
    쓸 수 없으면(`supported`가 거짓) 확인란은 흐리지만 골라진 값은 그대로 보이고 'ⓘ'는 듣는다.
    """

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._supported = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.LABEL_GAP)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.ICON_GAP)
        self.box = QCheckBox(AUTOSTART_LABEL)
        self.box.setObjectName(AUTOSTART_NAME)
        self.box.clicked.connect(self.changed)
        self._info = InfoIcon(AUTOSTART_LABEL, AUTOSTART_INFO, AUTOSTART_INFO_NAME)
        row.addWidget(self.box)
        row.addWidget(self._info)
        row.addStretch(1)
        layout.addLayout(row)
        self._message = make_label("", AUTOSTART_MESSAGE_NAME)
        self._message.hide()
        layout.addWidget(self._message)

    def value(self) -> bool:
        return self.box.isChecked()

    def supported(self) -> bool:
        return self._supported

    def set_value(self, on: bool) -> None:
        """고른 값을 바꾼다(`changed`를 내지 않는다)."""
        self.box.setChecked(on)

    def set_support(self, supported: bool, error: str | None) -> None:
        """이 실행에서 쓸 수 있는지와 마지막 맞추기의 실패 글을 반영한다."""
        self._supported = supported
        lines = (AUTOSTART_INFO,) if supported else (AUTOSTART_INFO, AUTOSTART_UNSUPPORTED)
        self._info.set_info(info_text(*lines))
        show_message(self._message, Message(error, Tone.WARNING) if error else None)

    def apply_enabled(self, editable: bool) -> None:
        """칸을 고칠 수 있는 때(`editable`)이고 쓸 수 있을 때만 확인란을 켠다. 'ⓘ'는 늘 듣는다."""
        self.box.setEnabled(editable and self._supported)


class SettingsScreen(QWidget):
    """처음 설정을 마친 뒤에만 칸이 채워지고 고칠 수 있다(그 전에는 비어 있고 흐리다).

    저장된 코인, 봉 단위, 봉 개수, 알림 보관 기간이 골라진 채 보인다. "저장"을 누르면
    `save_requested(coins, retention_days, candle_unit, candle_count, autostart, ai_choices, trade,
    live)`
    (`autostart`는 이 실행에서 쓸 수 있을 때만 확인란의 값이고 아니면 `None`. `ai_choices`는
    여덟 칸이 모두 골라져 있을 때만 네 칸이고 아니면 `None`. `trade`는 저장된 3단계 값이 있을 때
    칸을 읽은 `TradeSettings`이고, 3단계 전의 값이면 `None`. 3단계 칸의 꼴이 틀리면 그 칸 아래에
    글이 보이고 아무것도 내지 않는다. `live`는 실거래 설정 칸을 읽은 `LiveSettings`이고, 칸이 모두
    비었거나 실거래 중이라 칸이 흐리면 `None`이다. 꼴이 틀리면 글이 보이고 아무것도 내지
    않는다), "다시 시도"를 누르면
    `retry_requested`가 난다. 저장된 봉 단위가 없으면(옛 기록을 읽은 사이) 봉 단위는 `None`으로
    넘기고, 그러면 저장된 값을 그대로 둔다. 이 화면은 동작을 직접 부르지 않고, 보관 기간을
    줄이는 확인 창도 직접 띄우지 않는다(창이 한다).
    """

    save_requested = Signal(object, int, object, object, object, object, object, object)
    retry_requested = Signal()
    key_set_requested = Signal()
    key_delete_requested = Signal()
    live_on_requested = Signal()
    live_off_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName(screen_object_name(ScreenId.SETTINGS))
        self._phase = Phase.READY
        self._done = False
        self._active = False
        self._saved_coins: tuple[str, ...] = ()
        self._saved_days: int | None = None
        self._saved_unit: CandleUnit | None = None
        self._saved_count: int | None = None
        self._saved_autostart = False
        self._saved_ai: tuple[AiChoice, ...] = ()
        self._saved_trade: TradeSettings | None = None
        self._saved_live: LiveSettings | None = None
        self._mode = Mode.PAPER
        self._paused = False
        self._live_state: tuple | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*(theme.SCREEN_PADDING,) * 4)
        layout.setSpacing(theme.CARD_GAP)
        title_line = QHBoxLayout()
        title_line.setContentsMargins(0, 0, 0, 0)
        title_line.setSpacing(theme.ICON_GAP)
        title_line.addWidget(title_label(SCREEN_TITLES[ScreenId.SETTINGS], TITLE_NAME))
        title_line.addWidget(
            InfoIcon(SCREEN_TITLES[ScreenId.SETTINGS], SETTINGS_INTRO, INTRO_INFO_NAME)
        )
        title_line.addStretch(1)
        layout.addLayout(title_line)
        self._message = make_label("", MESSAGE_NAME)
        self._message.hide()
        layout.addWidget(self._message)

        self._form = QWidget()
        self._form.setObjectName(FORM_NAME)
        form_layout = QVBoxLayout(self._form)
        form_layout.setContentsMargins(0, 0, 0, 0)
        self._picker = CoinPicker(PREFIX, COINS_LABEL)
        self._candle = CandleChoice(PREFIX)
        self._retention = RetentionChoice(PREFIX, RETENTION_LABEL)
        self._autostart = AutostartChoice()
        self._ai = AiChoices(PREFIX, required=False)
        self._ai_warning = make_label(AI_PARTIAL_WARNING, AI_WARNING_NAME, Tone.WARNING)
        self._ai_warning.hide()
        self._fields = TradeFields(PREFIX, required=False)
        self._fields.set_funds_locked(True)  # 모의매매를 시작한 뒤에는 고칠 수 없다(M3 명세 3.7.1)
        # 5단계의 세 묶음: 업비트 키, 실거래 설정, 모드(M5 명세 3.11).
        self._live_fields = LiveFields()
        self._key_card = UpbitKeyCard()
        self._mode_card = ModeCard()
        # 차례는 처음 설정과 같다(PPT 5-4 판 4의 23장). 알림 보관 기간과 자동 시작은 그 밖의 값
        # 카드에 든다.
        budget, ratio, limits, other = self._fields.cards(self._retention, self._autostart)
        cards = []
        for part in (self._picker, budget, ratio, limits, self._candle, self._ai):
            if isinstance(part, Card):
                card = part
            else:
                card = Card()
                card.body.addWidget(part)
                if part is self._ai:
                    card.body.addWidget(self._ai_warning)  # 일부만 골랐을 때(M2 명세 3.12.2)
                if part is not self._picker:
                    # 오른쪽 카드는 내용을 위쪽에 붙이고, 남는 높이는 아래의 빈 자리가 받는다.
                    card.body.addStretch(1)
            cards.append(card)
        cards.append(other)
        # 실거래 중에는 매매 방식과 봉 개수를 바꿀 수 없고 곁에 글이 보인다(M5 명세 3.7.4).
        self._method_note = make_label(PAPER_ONLY_NOTE, "settingsMethodLockNote")
        set_role(self._method_note, theme.ROLE_SUB)
        self._method_note.hide()
        other.body.insertWidget(2, self._method_note)
        self._candle_note = make_label(PAPER_ONLY_NOTE, "settingsCandleLockNote")
        set_role(self._candle_note, theme.ROLE_SUB)
        self._candle_note.hide()
        cards[4].body.insertWidget(1, self._candle_note)
        cards += [self._key_card, self._live_fields.card(), self._mode_card]
        grid = CardGrid(split_first=True, max_columns=2)
        grid.set_cards(cards)
        form_layout.addWidget(grid)
        layout.addWidget(self._form)

        self._save = QPushButton(SAVE_TEXT)
        self._save.setObjectName(SAVE_NAME)
        set_role(self._save, theme.ROLE_PRIMARY)
        self._save.clicked.connect(self._on_save)
        self._revert = QPushButton(REVERT_TEXT)
        self._revert.setObjectName(REVERT_NAME)
        self._revert.clicked.connect(self._on_revert)
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_row.addWidget(self._revert)
        save_row.addWidget(self._save)
        layout.addLayout(save_row)
        layout.addStretch(1)

        self._picker.changed.connect(self._on_picks)
        self._live_fields.changed.connect(self._update_save)
        self._key_card.set_requested.connect(self.key_set_requested)
        self._key_card.delete_requested.connect(self.key_delete_requested)
        self._mode_card.live_on_requested.connect(self.live_on_requested)
        self._mode_card.live_off_requested.connect(self.live_off_requested)
        self._fields.changed.connect(self._update_save)
        self._candle.changed.connect(self._update_save)
        self._retention.changed.connect(self._update_save)
        self._autostart.changed.connect(self._update_save)
        self._ai.changed.connect(self._update_save)
        self._picker.retry_requested.connect(self.retry_requested)
        self.render(phase=Phase.READY, done=False, picker=PickerMode(PickerKind.IDLE), message=None)

    # --- 창이 부르는 것 ---

    def load_saved(
        self,
        coins: tuple[str, ...],
        retention_days: int | None,
        candle_unit: CandleUnit | None = None,
        candle_count: int | None = None,
        autostart: bool = False,
        ai_choices: tuple[AiChoice, ...] = (),
        trade: TradeSettings | None = None,
        live: LiveSettings | None = None,
    ) -> None:
        """저장된 값을 칸에 채운다(고치던 값은 버린다). 칸을 고칠 수 있을 때만 보인다."""
        self._saved_live = live
        self._saved_autostart = autostart
        self._saved_ai = ai_choices
        self._saved_trade = trade
        self._saved_coins = coins
        self._saved_days = retention_days
        self._saved_unit = candle_unit
        self._saved_count = candle_count
        if self._active:
            self._fill_saved()
        self._update_save()

    def _fill_saved(self) -> None:
        self._picker.set_picks(self._saved_coins)
        self._fields.set_coins(self._saved_coins)
        self._fields.set_values(self._saved_trade)
        self._live_fields.set_coins(self._saved_coins)
        self._live_fields.set_values(self._saved_live)
        self._retention.set_value(self._saved_days)
        count = self._saved_count if self._saved_count is not None else DEFAULT_CANDLE_COUNT
        self._candle.set_values(self._saved_unit, count)
        self._autostart.set_value(self._saved_autostart)
        self._ai.set_choices(self._saved_ai)

    def set_autostart_state(self, supported: bool, error: str | None) -> None:
        """이 실행에서 쓸 수 있는지와 실패 글. 사용자가 고르던 값은 건드리지 않는다."""
        self._autostart.set_support(supported, error)
        self._autostart.apply_enabled(self._active)

    def set_markets(self, markets: tuple[Market, ...]) -> None:
        self._picker.set_markets(markets)

    def render(
        self, *, phase: Phase, done: bool, picker: PickerMode, message: Message | None
    ) -> None:
        self._phase = phase
        self._done = done
        active = phase is Phase.READY and done
        if active and not self._active:
            self._fill_saved()
        elif not active:
            # 처음 설정 전, 불러오는 중, 설정을 읽지 못함: 칸이 비어 있고 흐리다.
            self._picker.set_picks(())
            self._fields.set_coins(())
            self._fields.set_values(None)
            self._live_fields.set_coins(())
            self._live_fields.set_values(None)
            self._retention.set_value(None)
            self._candle.set_values(None, None)
            self._autostart.set_value(False)
            self._ai.set_choices(None)
        self._active = active
        set_dim(self._form, not active)  # 흐린 때도 'ⓘ'는 듣는다
        self._autostart.apply_enabled(active)
        self._apply_live_state()
        self._picker.show_mode(picker)
        show_message(self._message, message)
        saved = (
            message is not None and message.text == SETTINGS_SAVED and message.tone is Tone.NORMAL
        )
        self._message.setProperty(theme.OK_PROPERTY, saved)  # "저장했습니다."는 '성공' 색
        self._message.style().unpolish(self._message)
        self._message.style().polish(self._message)
        self._update_save()

    # --- 안쪽 ---

    def _update_save(self) -> None:
        # 3단계 전의 값(`trade`가 없음)이면 3단계 칸을 보지 않는다(M3 명세 3.11.2).
        trade_ready = self._saved_trade is None or self._fields.is_complete()
        self._save.setEnabled(
            self._active
            and bool(self._picker.picks())
            and self._retention.value() is not None
            and trade_ready
        )
        self._revert.setEnabled(self._active)
        # 칸이 일부만 골라져 있으면 그 카드 아래에 경고가 보인다(나머지 값은 저장된다).
        self._ai_warning.setVisible(self._active and self._ai.is_partial())

    def _on_picks(self) -> None:
        self._fields.set_coins(self._picker.picks())
        self._live_fields.set_coins(self._picker.picks())
        self._update_save()

    def set_live_state(
        self,
        *,
        mode: Mode,
        live_status: LiveStatusView | None,
        key: UpbitKeyView | None,
        paused: bool,
        today: date,
        key_message: Message | None,
        mode_message: Message | None,
    ) -> None:
        """5단계의 모습을 반영한다(M5 명세 3.7.4, 3.11). 실거래 중에는 새 코인, 매매 방식, 봉 개수,
        실거래 설정이 흐리고 곁에 글이 보인다. 키 단추는 멈춤 상태(그리고 실거래가 아닐 때)에서만
        켜진다."""
        self._live_state = (mode, live_status, key, paused, today, key_message, mode_message)
        self._apply_live_state()

    def _apply_live_state(self) -> None:
        """`set_live_state`의 값을 칸에 반영한다. 칸을 흐리게 하는 `set_dim` 뒤에도 다시 부른다."""
        if self._live_state is None:
            return
        mode, live_status, key, paused, today, key_message, mode_message = self._live_state
        live = mode is Mode.LIVE
        self._mode, self._paused = mode, paused
        usable = self._active
        self._picker.set_adding_locked(live)
        self._fields.set_method_locked(live, usable=usable)
        self._candle.set_locked(live, usable=usable)
        self._live_fields.set_locked(live, usable=usable)
        self._method_note.setVisible(live)
        self._candle_note.setVisible(live)
        self._key_card.render(key, active=usable, live=live, today=today, message=key_message)
        self._mode_card.render(
            mode, live_status, paused=paused, active=usable, message=mode_message
        )

    def first_error_widget(self) -> QWidget | None:
        """걸린 칸 가운데 맨 위의 것(알림 A-15가 가는 곳). 없으면 `None`."""
        return self._fields.first_error_widget() or self._live_fields.first_error_widget()

    def key_widget(self) -> QWidget:
        """업비트 키 묶음(계정 화면의 "업비트 키 넣으러 가기"가 가는 곳)."""
        return self._key_card

    def coins_widget(self) -> QWidget:
        """코인 고르기(알림 A-16이 가는 곳)."""
        return self._picker

    def show_errors(self, errors) -> None:
        """저장 뒤 검사에 걸린 칸의 글(`SaveResult.field_errors`)을 칸 아래에 보인다."""
        self._fields.show_errors(errors)
        self._live_fields.show_errors(errors)

    def _on_revert(self) -> None:
        """저장하지 않은 고침을 버리고 칸을 마지막으로 받은 값으로 되돌린다(동작은 안 부른다)."""
        if not self._revert.isEnabled():
            return
        self._fill_saved()
        self._update_save()

    def _on_save(self) -> None:
        coins = self._picker.picks()
        days = self._retention.value()
        if not self._save.isEnabled() or not coins or days is None:
            return
        autostart = self._autostart.value() if self._autostart.supported() else None
        trade = None
        if self._saved_trade is not None:
            trade = self._fields.trade()  # 꼴이 틀리면 칸 아래에 글이 보이고 저장은 부르지 않는다
            if trade is None:
                return
        live = None
        if not self._live_fields.is_locked():  # 실거래 중에는 실거래 설정을 저장하지 않는다(3.7.4)
            valid, live = self._live_fields.live()
            if not valid:
                return
        self.save_requested.emit(
            coins,
            days,
            self._candle.unit(),
            self._candle.count(),
            autostart,
            self._ai.choices(),
            trade,
            live,
        )
