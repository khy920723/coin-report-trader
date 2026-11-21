"""설정 화면(설계서 6, M1 명세 4절 15번)."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from coin_report_trader.core import (
    DEFAULT_CANDLE_COUNT,
    SCREEN_TITLES,
    CandleUnit,
    Market,
    ScreenId,
)
from coin_report_trader.ui.candle_choice import CandleChoice
from coin_report_trader.ui.coin_picker import CoinPicker
from coin_report_trader.ui.retention import RetentionChoice
from coin_report_trader.ui.screens import TITLE_NAME, screen_object_name, title_label
from coin_report_trader.ui.state import Message, Phase, PickerKind, PickerMode
from coin_report_trader.ui.texts import COINS_LABEL, RETENTION_LABEL, SAVE_TEXT
from coin_report_trader.ui.widgets import make_label, show_message

PREFIX = "settings"
MESSAGE_NAME = "settingsMessage"
FORM_NAME = "settingsForm"
SAVE_NAME = "settingsSave"


class SettingsScreen(QWidget):
    """처음 설정을 마친 뒤에만 칸이 채워지고 고칠 수 있다(그 전에는 비어 있고 흐리다).

    저장된 코인, 봉 단위, 봉 개수, 알림 보관 기간이 골라진 채 보인다. "저장"을 누르면
    `save_requested(coins, retention_days, candle_unit, candle_count)`, "다시 시도"를 누르면
    `retry_requested`가 난다. 저장된 봉 단위가 없으면(옛 기록을 읽은 사이) 봉 단위는 `None`으로
    넘기고, 그러면 저장된 값을 그대로 둔다. 이 화면은 동작을 직접 부르지 않고, 보관 기간을
    줄이는 확인 창도 직접 띄우지 않는다(창이 한다).
    """

    save_requested = Signal(object, int, object, object)
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

        layout = QVBoxLayout(self)
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
        form_layout.addWidget(self._picker)
        form_layout.addWidget(self._candle)
        form_layout.addWidget(self._retention)
        layout.addWidget(self._form)

        self._save = QPushButton(SAVE_TEXT)
        self._save.setObjectName(SAVE_NAME)
        self._save.clicked.connect(self._on_save)
        layout.addWidget(self._save)
        layout.addStretch(1)

        self._picker.changed.connect(self._update_save)
        self._candle.changed.connect(self._update_save)
        self._retention.changed.connect(self._update_save)
        self._picker.retry_requested.connect(self.retry_requested)
        self.render(phase=Phase.READY, done=False, picker=PickerMode(PickerKind.IDLE), message=None)

    # --- 창이 부르는 것 ---

    def load_saved(
        self,
        coins: tuple[str, ...],
        retention_days: int | None,
        candle_unit: CandleUnit | None = None,
        candle_count: int | None = None,
    ) -> None:
        """저장된 값을 칸에 채운다(고치던 값은 버린다). 칸을 고칠 수 있을 때만 보인다."""
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
        self._active = active
        self._form.setEnabled(active)
        self._picker.show_mode(picker)
        show_message(self._message, message)
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
        self.save_requested.emit(coins, days, self._candle.unit(), self._candle.count())
