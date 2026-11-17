"""알림 보관 기간 고르기: "7일" / "30일" / "90일"과 그 옆의 두 글(설계서 8.5)."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from coin_report_trader.ui.texts import (
    RETENTION_NOTICE_KEEP,
    RETENTION_NOTICE_SHORTEN,
    RETENTION_OPTIONS,
)
from coin_report_trader.ui.widgets import make_label


class RetentionChoice(QWidget):
    """미리 고른 값이 없는 채 시작한다. 사용자가 고르면 `changed`가 난다(`set_value`는 내지 않는다).

    `objectName`(앞의 `prefix`는 `firstSetup`이나 `settings`): `<prefix>RetentionTitle`,
    `<prefix>Retention7`, `<prefix>Retention30`, `<prefix>Retention90`(라디오 버튼),
    `<prefix>RetentionNoticeKeep`, `<prefix>RetentionNoticeShorten`.
    """

    changed = Signal()

    def __init__(self, prefix: str, title: str) -> None:
        super().__init__()
        self.setObjectName(f"{prefix}Retention")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(make_label(title, f"{prefix}RetentionTitle"))
        row = QHBoxLayout()
        self._group = QButtonGroup(self)
        self._buttons: dict[int, QRadioButton] = {}
        for days, text in RETENTION_OPTIONS:
            button = QRadioButton(text)
            button.setObjectName(f"{prefix}Retention{days}")
            button.clicked.connect(self.changed)
            self._group.addButton(button)
            self._buttons[days] = button
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(make_label(RETENTION_NOTICE_KEEP, f"{prefix}RetentionNoticeKeep"))
        layout.addWidget(make_label(RETENTION_NOTICE_SHORTEN, f"{prefix}RetentionNoticeShorten"))

    def value(self) -> int | None:
        for days, button in self._buttons.items():
            if button.isChecked():
                return days
        return None

    def set_value(self, days: int | None) -> None:
        """고른 값을 바꾼다. `None`이나 목록에 없는 값이면 아무것도 고르지 않은 채로 둔다."""
        self._group.setExclusive(False)
        for value, button in self._buttons.items():
            button.setChecked(value == days)
        self._group.setExclusive(True)
