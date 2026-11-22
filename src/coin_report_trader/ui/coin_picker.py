"""코인 고르기(최대 3개, 순서 있음). 처음 설정과 설정 화면이 같이 쓴다.

화면은 네 가지 모습 가운데 하나다(`PickerMode`):

- 글 자리(`<prefix>CoinsMessage`)와 "다시 시도" 단추(`<prefix>CoinsRetry`): 불러오는 중, 받지 못함,
  일시 차단 중, 보일 것이 없는 때. 이때 고른 코인은 보이지 않지만 `picks()`가 그대로 가지고 있다.
- 목록: 받은 코인마다 한 줄(`<prefix>Coin_<마켓 코드>` 체크 상자, 유의·주의면 옆에
  `<prefix>CoinCaution_<마켓 코드>` 라벨), 고른 코인의 순서 목록(`<prefix>CoinsSelected`)과
  단추 `<prefix>CoinUp`, `<prefix>CoinDown`, `<prefix>CoinRemove`.

목록의 줄은 코인 기호의 ABC 순서다(숫자로 시작하는 기호가 맨 앞. M1 명세 3.14.3). 목록 위의 찾기 칸
`<prefix>CoinSearch`(이름 라벨 `<prefix>CoinSearchTitle`)에 넣은 글이 기호나 한글 이름에 든 줄만
보이고, 맞는 줄이 없으면 `<prefix>CoinsNoMatch`가 보인다. 목록을 보이지 않는 모습(불러오는 중,
받지 못함, 일시 차단 중, 보일 것이 없는 때)에서는 찾기 칸이 흐리다.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from coin_report_trader.core import Market, coin_label
from coin_report_trader.ui import theme
from coin_report_trader.ui.icons import search_icon
from coin_report_trader.ui.state import PickerKind, PickerMode, Tone
from coin_report_trader.ui.texts import (
    CAUTION_LABEL,
    CAUTION_NOTE,
    MOVE_DOWN_TEXT,
    MOVE_UP_TEXT,
    NO_COIN_FOUND,
    REMOVE_TEXT,
    RETRY_TEXT,
    SEARCH_LABEL,
    SEARCH_PLACEHOLDER,
)
from coin_report_trader.ui.widgets import make_label, set_role

MAX_COINS = 3
_MESSAGE_PAGE = 0
_LIST_PAGE = 1


def coin_sort_key(market: Market) -> tuple[int, str, str]:
    """코인 목록의 차례(M1 명세 3.14.3): (첫 글자가 숫자면 0, 아니면 1, 기호의 대문자, 기호)."""
    symbol = coin_label(market.market)
    return (0 if symbol[:1].isdigit() else 1, symbol.upper(), symbol)


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
        self._rows: dict[str, QWidget] = {}
        self._locked = False
        self._search_usable = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.FIELD_GAP)
        coins_title = make_label(title, f"{prefix}CoinsTitle")
        set_role(coins_title, theme.ROLE_GROUP)
        layout.addWidget(coins_title)
        search_row = QHBoxLayout()
        search_title = make_label(SEARCH_LABEL, f"{prefix}CoinSearchTitle")
        set_role(search_title, theme.ROLE_SUB)
        search_row.addWidget(search_title)
        self._search = QLineEdit()
        self._search.setObjectName(f"{prefix}CoinSearch")
        self._search.setPlaceholderText(SEARCH_PLACEHOLDER)
        self._search.addAction(search_icon(), QLineEdit.ActionPosition.LeadingPosition)
        self._search.textChanged.connect(self._apply_filter)
        search_row.addWidget(self._search, 1)
        layout.addLayout(search_row)
        self._pages = QStackedWidget()
        self._pages.setObjectName(f"{prefix}CoinsPages")
        layout.addWidget(self._pages)
        # 유의·주의 코인을 고르면 목록 아래에 보인다(M3 명세 3.17.5).
        self._caution_note = make_label(CAUTION_NOTE, f"{prefix}CautionNote")
        self._caution_note.setStyleSheet(f"color: {theme.CAUTION};")  # 'caution' 색
        self._caution_note.hide()
        layout.addWidget(self._caution_note)

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
        self._no_match = make_label(NO_COIN_FOUND, f"{prefix}CoinsNoMatch")
        self._no_match.hide()
        self._rows_layout.addWidget(self._no_match)
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
        self._update_search()

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
        self._search_usable = mode.kind is PickerKind.READY
        self._update_search()
        if mode.kind is PickerKind.READY:
            self._pages.setCurrentIndex(_LIST_PAGE)
            return
        self._pages.setCurrentIndex(_MESSAGE_PAGE)
        self._message.setText(mode.text)
        self._message.setVisible(bool(mode.text))
        self._retry.setVisible(mode.retry)

    def set_locked(self, locked: bool) -> None:
        """코인 고르기(찾기 칸, 위·아래 단추 포함)를 잠근다. 잠기면 흐리게 보이고 바꿀 수 없다
        (M1 명세 3.14.4). 고른 코인은 그대로 보이고 `picks()`가 가지고 있다."""
        self._locked = locked
        self._update_search()
        self._pages.widget(_LIST_PAGE).setEnabled(not locked)

    # --- 안쪽 ---

    def _update_search(self) -> None:
        self._search.setEnabled(self._search_usable and not self._locked)

    def _apply_filter(self, *_args) -> None:
        """찾기 칸의 글(앞뒤 빈칸을 지운)이 기호나 한글 이름에 든 줄만 보인다.
        빈 글이면 모두 보인다. 고른 상태는 가려진 줄에도 남는다."""
        text = self._search.text().strip().casefold()
        shown = 0
        for market in self._markets:
            row = self._rows.get(market.market)
            if row is None:
                continue
            hit = (
                not text
                or text in coin_label(market.market).casefold()
                or text in market.name.casefold()
            )
            row.setVisible(hit)
            shown += hit
        self._no_match.setVisible(bool(text) and shown == 0)

    def _rebuild_rows(self) -> None:
        for row in self._rows.values():
            # 다시 받은 목록의 줄과 이름이 겹치지 않게 지금 떼어 낸다.
            row.hide()
            row.setParent(None)
            row.deleteLater()
        self._rows = {}
        self._boxes = {}
        for market in sorted(self._markets, key=coin_sort_key):
            row = QWidget()
            row.setObjectName(f"{self._prefix}CoinRow_{market.market}")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            box = QCheckBox(f"{coin_label(market.market)} {market.name}")
            box.setObjectName(f"{self._prefix}Coin_{market.market}")
            box.clicked.connect(lambda checked, code=market.market: self._on_clicked(code, checked))
            row_layout.addWidget(box)
            if market.caution:
                caution = make_label(
                    CAUTION_LABEL, f"{self._prefix}CoinCaution_{market.market}", Tone.WARNING
                )
                caution.setStyleSheet(f"color: {theme.CAUTION};")  # '주의' 색(5-1 판 3)
                row_layout.addWidget(caution)
            row_layout.addStretch(1)
            self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)
            self._rows[market.market] = row
            self._boxes[market.market] = box
        self._apply_filter()

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
        self._update_caution()

    def _update_caution(self) -> None:
        cautious = {m.market for m in self._markets if m.caution}
        self._caution_note.setVisible(any(market in cautious for market in self._picks))

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
