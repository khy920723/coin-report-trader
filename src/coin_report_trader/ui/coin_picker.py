"""코인 고르기(최대 3개, 순서 있음). 처음 설정과 설정 화면이 같이 쓴다.

화면은 네 가지 모습 가운데 하나다(`PickerMode`):

- 글 자리(`<prefix>CoinsMessage`)와 "다시 시도" 단추(`<prefix>CoinsRetry`): 불러오는 중, 받지 못함,
  일시 차단 중, 보일 것이 없는 때. 이때 고른 코인은 보이지 않지만 `picks()`가 그대로 가지고 있다.
- 목록: 받은 코인마다 한 줄(`<prefix>Coin_<마켓 코드>` 체크 상자, 유의·주의면 옆에
  `<prefix>CoinCaution_<마켓 코드>` 라벨), 고른 코인의 순서 목록(`<prefix>CoinsSelected`)과
  단추 `<prefix>CoinUp`, `<prefix>CoinDown`, `<prefix>CoinRemove`.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QListWidget,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from coin_report_trader.core import Market, coin_label
from coin_report_trader.ui.state import PickerKind, PickerMode, Tone
from coin_report_trader.ui.texts import (
    CAUTION_LABEL,
    MOVE_DOWN_TEXT,
    MOVE_UP_TEXT,
    REMOVE_TEXT,
    RETRY_TEXT,
)
from coin_report_trader.ui.widgets import make_label

MAX_COINS = 3
_MESSAGE_PAGE = 0
_LIST_PAGE = 1


class CoinPicker(QWidget):
    """사용자가 고르거나 순서를 바꾸면 `changed`, "다시 시도"를 누르면 `retry_requested`가 난다."""

    changed = Signal()
    retry_requested = Signal()

    def __init__(self, prefix: str, title: str) -> None:
        super().__init__()
        self._prefix = prefix
        self.setObjectName(f"{prefix}Coins")
        self._picks: list[str] = []
        self._markets: tuple[Market, ...] = ()
        self._names: dict[str, str] = {}
        self._boxes: dict[str, QCheckBox] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(make_label(title, f"{prefix}CoinsTitle"))
        self._pages = QStackedWidget()
        self._pages.setObjectName(f"{prefix}CoinsPages")
        layout.addWidget(self._pages)

        message_page = QWidget()
        message_layout = QVBoxLayout(message_page)
        message_layout.setContentsMargins(0, 0, 0, 0)
        self._message = make_label("", f"{prefix}CoinsMessage")
        self._retry = QPushButton(RETRY_TEXT)
        self._retry.setObjectName(f"{prefix}CoinsRetry")
        self._retry.clicked.connect(self.retry_requested)
        message_layout.addWidget(self._message)
        message_layout.addWidget(self._retry)
        message_layout.addStretch(1)
        self._pages.addWidget(message_page)

        list_page = QWidget()
        list_layout = QHBoxLayout(list_page)
        list_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_holder = QWidget()
        self._rows_holder.setObjectName(f"{prefix}CoinsList")
        self._rows_layout = QVBoxLayout(self._rows_holder)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._rows_holder)
        list_layout.addWidget(scroll, 2)

        side = QVBoxLayout()
        self._selected = QListWidget()
        self._selected.setObjectName(f"{prefix}CoinsSelected")
        self._selected.currentRowChanged.connect(self._update_buttons)
        side.addWidget(self._selected)
        self._up = self._button(MOVE_UP_TEXT, f"{prefix}CoinUp", self._move_up)
        self._down = self._button(MOVE_DOWN_TEXT, f"{prefix}CoinDown", self._move_down)
        self._remove = self._button(REMOVE_TEXT, f"{prefix}CoinRemove", self._remove_current)
        for button in (self._up, self._down, self._remove):
            side.addWidget(button)
        list_layout.addLayout(side, 1)
        self._pages.addWidget(list_page)

        self._sync()

    def _button(self, text: str, name: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(name)
        button.clicked.connect(slot)
        return button

    # --- 바깥에서 쓰는 것 ---

    def picks(self) -> tuple[str, ...]:
        """고른 마켓 코드. 고른 순서(위아래 단추로 바꾼 순서)가 설정 목록의 순서다."""
        return tuple(self._picks)

    def set_picks(self, coins: tuple[str, ...]) -> None:
        """고른 코인을 바꾼다(`changed`를 내지 않는다). 3개까지만 받는다."""
        picks: list[str] = []
        for market in coins:
            if market not in picks and len(picks) < MAX_COINS:
                picks.append(market)
        self._picks = picks
        self._sync()

    def set_markets(self, markets: tuple[Market, ...]) -> None:
        """받은 코인 목록으로 줄을 다시 만든다. 이미 고른 코인은 목록에 없어도 남는다."""
        self._markets = markets
        self._names.update({m.market: m.name for m in markets})
        self._rebuild_rows()
        self._sync()

    def show_mode(self, mode: PickerMode) -> None:
        if mode.kind is PickerKind.READY:
            self._pages.setCurrentIndex(_LIST_PAGE)
            return
        self._pages.setCurrentIndex(_MESSAGE_PAGE)
        self._message.setText(mode.text)
        self._message.setVisible(bool(mode.text))
        self._retry.setVisible(mode.retry)

    # --- 안쪽 ---

    def _rebuild_rows(self) -> None:
        for box in self._boxes.values():
            box.parentWidget().deleteLater()
        self._boxes = {}
        for market in self._markets:
            row = QWidget()
            row.setObjectName(f"{self._prefix}CoinRow_{market.market}")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            box = QCheckBox(f"{coin_label(market.market)} {market.name}")
            box.setObjectName(f"{self._prefix}Coin_{market.market}")
            box.clicked.connect(lambda checked, code=market.market: self._on_clicked(code, checked))
            row_layout.addWidget(box)
            if market.caution:
                row_layout.addWidget(
                    make_label(
                        CAUTION_LABEL, f"{self._prefix}CoinCaution_{market.market}", Tone.WARNING
                    )
                )
            row_layout.addStretch(1)
            self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)
            self._boxes[market.market] = box

    def _on_clicked(self, market: str, checked: bool) -> None:
        if checked and market not in self._picks and len(self._picks) < MAX_COINS:
            self._picks.append(market)
        elif not checked and market in self._picks:
            self._picks.remove(market)
        self._sync()
        self.changed.emit()

    def _label_of(self, market: str) -> str:
        name = self._names.get(market)
        return f"{coin_label(market)} {name}" if name else coin_label(market)

    def _sync(self, select: int | None = None) -> None:
        full = len(self._picks) >= MAX_COINS
        for market, box in self._boxes.items():
            box.setChecked(market in self._picks)
            # 4개째는 고를 수 없다.
            box.setEnabled(box.isChecked() or not full)
        self._selected.blockSignals(True)
        current = self._selected.currentRow() if select is None else select
        self._selected.clear()
        self._selected.addItems([self._label_of(market) for market in self._picks])
        if self._picks:
            self._selected.setCurrentRow(min(max(current, 0), len(self._picks) - 1))
        self._selected.blockSignals(False)
        self._update_buttons()

    def _update_buttons(self, *_args) -> None:
        row = self._selected.currentRow()
        count = len(self._picks)
        self._up.setEnabled(0 < row < count)
        self._down.setEnabled(0 <= row < count - 1)
        self._remove.setEnabled(0 <= row < count)

    def _move_up(self) -> None:
        self._move(-1)

    def _move_down(self) -> None:
        self._move(1)

    def _move(self, step: int) -> None:
        row = self._selected.currentRow()
        target = row + step
        if not (0 <= row < len(self._picks) and 0 <= target < len(self._picks)):
            return
        self._picks[row], self._picks[target] = self._picks[target], self._picks[row]
        self._sync(select=target)
        self.changed.emit()

    def _remove_current(self) -> None:
        row = self._selected.currentRow()
        if not 0 <= row < len(self._picks):
            return
        del self._picks[row]
        self._sync(select=row)
        self.changed.emit()
