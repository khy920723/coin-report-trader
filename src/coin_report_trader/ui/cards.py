"""카드와 카드를 늘어놓는 자리(M1 명세 3.14.7, 화면 설계서 5-0 판 3의 18장, 20장)."""

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from coin_report_trader.ui import theme
from coin_report_trader.ui.info_icon import InfoIcon
from coin_report_trader.ui.widgets import make_label, set_role

MIN_CARD_WIDTH = 340
"""카드 하나가 한 줄에 놓이려면 필요한 가장 작은 너비. 이보다 좁으면 아래 줄로 내린다."""


class Card(QFrame):
    """'판' 바탕, 모서리 8px, 안쪽 여백 16px의 카드. 안은 세로로 쌓는다."""

    def __init__(self, name: str = "") -> None:
        super().__init__()
        if name:
            self.setObjectName(name)
        self.setProperty(theme.CARD_PROPERTY, True)
        self.body = QVBoxLayout(self)
        pad = theme.CARD_PADDING
        self.body.setContentsMargins(pad, pad, pad, pad)
        self.body.setSpacing(theme.FIELD_GAP)


def title_row(
    title: str, name: str, info: InfoIcon | None = None, *, role: str = theme.ROLE_GROUP
) -> QHBoxLayout:
    """제목과 그 바로 오른쪽 4px의 'ⓘ'(있으면)가 한 줄에 놓이는 줄."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(theme.ICON_GAP)
    label = make_label(title, name)
    set_role(label, role)
    row.addWidget(label)
    if info is not None:
        row.addWidget(info)
    row.addStretch(1)
    return row


class CardGrid(QWidget):
    """카드를 한 줄에 놓을 수 있는 만큼 놓고 넘치면 아래 줄로 내린다. 카드는 넣은 차례대로 놓인다.

    `split_first`가 참이면 넓을 때 첫 카드는 왼쪽에 길게 놓고 나머지는 오른쪽에 쌓는다.
    """

    def __init__(self, *, split_first: bool = False, max_columns: int = 3) -> None:
        super().__init__()
        self._split_first = split_first
        self._max_columns = max_columns
        self._cards: list[QWidget] = []
        self._columns = 0
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(theme.CARD_GAP)

    def set_cards(self, cards: list[QWidget]) -> None:
        """놓을 카드의 차례를 정한다(이미 놓인 카드는 그 자리에서 다시 놓는다)."""
        self._cards = list(cards)
        self._place(max(self._columns, 1), force=True)

    def columns(self) -> int:
        return max(self._columns, 1)

    def minimumSizeHint(self) -> QSize:
        return QSize(MIN_CARD_WIDTH, super().minimumSizeHint().height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        columns = max(1, min(self._max_columns, event.size().width() // MIN_CARD_WIDTH))
        if columns != self._columns:
            self._place(columns)

    def _place(self, columns: int, *, force: bool = False) -> None:
        if columns == self._columns and not force:
            return
        self._columns = columns
        while self._grid.count():
            self._grid.takeAt(0)
        count = len(self._cards)
        for column in range(self._max_columns):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)
        if self._split_first and columns >= 2 and count > 1:
            self._grid.addWidget(self._cards[0], 0, 0, count - 1, 1)
            for row, card in enumerate(self._cards[1:]):
                self._grid.addWidget(card, row, 1)
            return
        for index, card in enumerate(self._cards):
            self._grid.addWidget(card, index // columns, index % columns)
