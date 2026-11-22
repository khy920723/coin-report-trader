"""설정 화면(설계서 6, M1 명세 4절 15번)."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from coin_report_trader.core import (
    DEFAULT_CANDLE_COUNT,
    SCREEN_TITLES,
    CandleUnit,
    Market,
    ScreenId,
)
from coin_report_trader.core.texts import SETTINGS_SAVED
from coin_report_trader.ui import theme
from coin_report_trader.ui.candle_choice import CandleChoice
from coin_report_trader.ui.cards import Card, CardGrid
from coin_report_trader.ui.coin_picker import CoinPicker
from coin_report_trader.ui.info_icon import InfoIcon, info_text
from coin_report_trader.ui.retention import RetentionChoice
from coin_report_trader.ui.screens import TITLE_NAME, screen_object_name, title_label
from coin_report_trader.ui.state import Message, Phase, PickerKind, PickerMode, Tone
from coin_report_trader.ui.texts import (
    AUTOSTART_INFO,
    AUTOSTART_LABEL,
    AUTOSTART_UNSUPPORTED,
    COINS_LABEL,
    RETENTION_LABEL,
    SAVE_TEXT,
)
from coin_report_trader.ui.widgets import make_label, set_dim, set_role, show_message

PREFIX = "settings"
MESSAGE_NAME = "settingsMessage"
FORM_NAME = "settingsForm"
SAVE_NAME = "settingsSave"
AUTOSTART_NAME = "settingsAutostart"
AUTOSTART_INFO_NAME = "settingsAutostartInfo"
AUTOSTART_MESSAGE_NAME = "settingsAutostartMessage"


class AutostartChoice(QWidget):
    """"PC를 켜면 자동으로 시작" 확인란과 그 옆 'ⓘ', 아래의 결과 글(M1 명세 3.16.1).

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
    `save_requested(coins, retention_days, candle_unit, candle_count, autostart)`(`autostart`는
    이 실행에서 쓸 수 있을 때만 확인란의 값이고, 아니면 `None`), "다시 시도"를 누르면
    `retry_requested`가 난다. 저장된 봉 단위가 없으면(옛 기록을 읽은 사이) 봉 단위는 `None`으로
    넘기고, 그러면 저장된 값을 그대로 둔다. 이 화면은 동작을 직접 부르지 않고, 보관 기간을
    줄이는 확인 창도 직접 띄우지 않는다(창이 한다).
    """

    save_requested = Signal(object, int, object, object, object)
    retry_requested = Signal()

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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*(theme.SCREEN_PADDING,) * 4)
        layout.setSpacing(theme.CARD_GAP)
        layout.addWidget(title_label(SCREEN_TITLES[ScreenId.SETTINGS], TITLE_NAME))
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
        cards = []
        for part in (self._picker, self._candle, self._retention):
            card = Card()
            card.body.addWidget(part)
            if part is self._retention:
                card.body.addWidget(self._autostart)  # 알림 보관 기간 아래(M1 명세 3.16.1)
            if part is not self._picker:
                # 오른쪽 카드는 내용을 위쪽에 붙이고, 남는 높이는 아래의 빈 자리가 받는다.
                card.body.addStretch(1)
            cards.append(card)
        grid = CardGrid(split_first=True, max_columns=2)
        grid.set_cards(cards)
        form_layout.addWidget(grid)
        layout.addWidget(self._form)

        self._save = QPushButton(SAVE_TEXT)
        self._save.setObjectName(SAVE_NAME)
        set_role(self._save, theme.ROLE_PRIMARY)
        self._save.clicked.connect(self._on_save)
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_row.addWidget(self._save)
        layout.addLayout(save_row)
        layout.addStretch(1)

        self._picker.changed.connect(self._update_save)
        self._candle.changed.connect(self._update_save)
        self._retention.changed.connect(self._update_save)
        self._autostart.changed.connect(self._update_save)
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
    ) -> None:
        """저장된 값을 칸에 채운다(고치던 값은 버린다). 칸을 고칠 수 있을 때만 보인다."""
        self._saved_autostart = autostart
        self._saved_coins = coins
        self._saved_days = retention_days
        self._saved_unit = candle_unit
        self._saved_count = candle_count
        if self._active:
            self._fill_saved()
        self._update_save()

    def _fill_saved(self) -> None:
        self._picker.set_picks(self._saved_coins)
        self._retention.set_value(self._saved_days)
        count = self._saved_count if self._saved_count is not None else DEFAULT_CANDLE_COUNT
        self._candle.set_values(self._saved_unit, count)
        self._autostart.set_value(self._saved_autostart)

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
            self._retention.set_value(None)
            self._candle.set_values(None, None)
            self._autostart.set_value(False)
        self._active = active
        set_dim(self._form, not active)  # 흐린 때도 'ⓘ'는 듣는다
        self._autostart.apply_enabled(active)
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
        self._save.setEnabled(
            self._active and bool(self._picker.picks()) and self._retention.value() is not None
        )

    def _on_save(self) -> None:
        coins = self._picker.picks()
        days = self._retention.value()
        if not self._save.isEnabled() or not coins or days is None:
            return
        autostart = self._autostart.value() if self._autostart.supported() else None
        self.save_requested.emit(coins, days, self._candle.unit(), self._candle.count(), autostart)
