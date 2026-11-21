"""봉 단위와 봉 개수 고르기(M1 명세 3.14.1, 3.14.2). 처음 설정과 설정 화면이 같이 쓴다."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QRadioButton, QVBoxLayout, QWidget

from coin_report_trader.core import (
    CANDLE_COUNTS,
    CANDLE_UNIT_TITLES,
    DEFAULT_CANDLE_COUNT,
    CandleUnit,
)
from coin_report_trader.ui.texts import (
    CANDLE_CHOICE_NOTE,
    COUNT_LABEL,
    COUNT_OPTION_SUFFIX,
    UNIT_LABEL,
    candle_count_tooltip,
)
from coin_report_trader.ui.widgets import make_label


class CandleChoice(QWidget):
    """봉 단위(넷)와 봉 개수(셋) 고르기. 사용자가 고르면 `changed`가 난다.
    `set_values`는 `changed`를 내지 않는다.

    봉 단위는 미리 고른 값이 없는 채 시작하고, 봉 개수는 "200개"가 골라진 채 시작한다. 봉 개수
    값마다 툴팁(`candle_count_tooltip`)이 있고, 봉 단위를 바꾸면 그 단위의 글로 바뀐다.

    `objectName`(앞의 `prefix`는 `firstSetup`이나 `settings`): `<prefix>Unit_<봉 단위 값>`(예:
    `firstSetupUnit_minute60`), `<prefix>Count_<수>`(예: `settingsCount_200`)(둘 다 라디오 버튼),
    `<prefix>UnitTitle`, `<prefix>CountTitle`(칸 이름), `<prefix>CandleNote`(두 칸 아래 글).
    """

    changed = Signal()

    def __init__(self, prefix: str) -> None:
        super().__init__()
        self.setObjectName(f"{prefix}Candle")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(make_label(UNIT_LABEL, f"{prefix}UnitTitle"))
        unit_row = QHBoxLayout()
        self._unit_group = QButtonGroup(self)
        self._unit_buttons: dict[CandleUnit, QRadioButton] = {}
        for unit in CandleUnit:
            button = QRadioButton(CANDLE_UNIT_TITLES[unit])
            button.setObjectName(f"{prefix}Unit_{unit.value}")
            button.clicked.connect(self._on_unit_clicked)
            self._unit_group.addButton(button)
            self._unit_buttons[unit] = button
            unit_row.addWidget(button)
        unit_row.addStretch(1)
        layout.addLayout(unit_row)

        layout.addWidget(make_label(COUNT_LABEL, f"{prefix}CountTitle"))
        count_row = QHBoxLayout()
        self._count_group = QButtonGroup(self)
        self._count_buttons: dict[int, QRadioButton] = {}
        for count in CANDLE_COUNTS:
            button = QRadioButton(f"{count}{COUNT_OPTION_SUFFIX}")
            button.setObjectName(f"{prefix}Count_{count}")
            button.clicked.connect(self.changed)
            self._count_group.addButton(button)
            self._count_buttons[count] = button
            count_row.addWidget(button)
        count_row.addStretch(1)
        layout.addLayout(count_row)

        layout.addWidget(make_label(CANDLE_CHOICE_NOTE, f"{prefix}CandleNote"))
        self._count_buttons[DEFAULT_CANDLE_COUNT].setChecked(True)
        self._refresh_tooltips()

    def unit(self) -> CandleUnit | None:
        for unit, button in self._unit_buttons.items():
            if button.isChecked():
                return unit
        return None

    def count(self) -> int | None:
        for count, button in self._count_buttons.items():
            if button.isChecked():
                return count
        return None

    def set_values(self, unit: CandleUnit | None, count: int | None) -> None:
        """고른 값을 바꾼다. `None`이나 목록에 없는 값이면 아무것도 고르지 않은 채로 둔다."""
        for group, buttons, chosen in (
            (self._unit_group, self._unit_buttons, unit),
            (self._count_group, self._count_buttons, count),
        ):
            group.setExclusive(False)
            for value, button in buttons.items():
                button.setChecked(value == chosen)
            group.setExclusive(True)
        self._refresh_tooltips()

    def _on_unit_clicked(self) -> None:
        self._refresh_tooltips()
        self.changed.emit()

    def _refresh_tooltips(self) -> None:
        unit = self.unit()
        for count, button in self._count_buttons.items():
            button.setToolTip(candle_count_tooltip(unit, count))
