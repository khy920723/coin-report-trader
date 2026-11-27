"""계정 화면(SCR-08. M5 명세 3.15.3).

키로 읽은 실제 계정(원화, 보유 코인)을 보기만 한다. 주문하지 않고 내보내기 단추도 없다.

위에서 아래로: 제목 "계정"과 "새로 고침" `accountRefresh`, 상태와 오류의 글 `accountMessage`,
키 없음일 때 "업비트 키 넣으러 가기" `accountGoKey`, '실제 계정' 묶음 `accountSummary`(제목 옆 'ⓘ'
`accountSummaryInfo`, 원화 주문 가능 `accountKrwAvailable`, 원화 주문에 묶임 `accountKrwLocked`,
보유 코인 평가 합 `accountHoldingsValue`, 총자산 `accountTotalAssets`, 총 매수 금액
`accountTotalBuy`, 총 평가 손익 `accountTotalPnl`, 수익률 `accountTotalPnlRate`, "{시각}에 읽음"
`accountReadAt`, 뺀 줄의 글 `accountExcluded`), 코인별 표 `accountTable`(표 자리의 글
`accountTableNote`).

이 화면은 동작을 직접 부르지 않는다. "새로 고침"은 `refresh_requested`, "업비트 키 넣으러 가기"는
`go_key_requested`를 낸다(창이 부른다). 값은 `render(state, view)`로 받고 셈은 하지 않는다.
"""

from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from coin_report_trader.core import (
    SCREEN_TITLES,
    AccountRow,
    AccountState,
    AccountView,
    ScreenId,
    format_price,
)
from coin_report_trader.core.texts import kst_when_text
from coin_report_trader.ui import theme
from coin_report_trader.ui.cards import Card, title_row
from coin_report_trader.ui.history_screen import make_data_table
from coin_report_trader.ui.info_icon import InfoIcon, info_text
from coin_report_trader.ui.screens import TITLE_NAME, screen_object_name, title_label
from coin_report_trader.ui.state import Tone
from coin_report_trader.ui.texts import (
    ACCOUNT_AVG_MODIFIED_TIP,
    ACCOUNT_BEFORE_SETUP,
    ACCOUNT_BLOCKED,
    ACCOUNT_GO_KEY_TEXT,
    ACCOUNT_NO_HOLDINGS,
    ACCOUNT_NO_KEY,
    ACCOUNT_PICKED_TEXT,
    ACCOUNT_REFRESH_TEXT,
    ACCOUNT_SUMMARY_INFO,
    ACCOUNT_SUMMARY_LABELS,
    ACCOUNT_SUMMARY_TITLE,
    ACCOUNT_TABLE_COLUMNS,
    LOADING_TEXT,
    NO_VALUE,
    account_excluded_text,
    account_failed_text,
    account_qty_text,
    account_read_at_text,
)
from coin_report_trader.ui.trade_status import signed_percent, signed_won, won
from coin_report_trader.ui.widgets import (
    make_label,
    set_dim,
    set_role,
    set_tone,
    set_trend,
    trend_of,
)

SUMMARY_NAMES = {
    "krw_available": "accountKrwAvailable",
    "krw_locked": "accountKrwLocked",
    "holdings_value": "accountHoldingsValue",
    "total_assets": "accountTotalAssets",
    "total_buy": "accountTotalBuy",
    "total_pnl": "accountTotalPnl",
    "total_pnl_rate": "accountTotalPnlRate",
}
"""요약 칸의 값 라벨 이름(3.15.3). 칸 이름 라벨은 `<값 라벨 이름>Label`이다."""
RIGHT_COLUMNS = (1, 2, 3, 4, 5, 6, 7)
"""오른쪽에 맞추는 칸(수량, 가격, 금액, 손익, 수익률)."""
_QTY_STEP = Decimal("1E-8")


def qty_text(value: Decimal) -> str:
    """수량: 소수 여덟째 자리까지, 뒤의 0은 쓰지 않는다."""
    cut = value.quantize(_QTY_STEP, rounding=ROUND_DOWN)
    text = format(cut, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _money(value: Decimal | None) -> str:
    return NO_VALUE if value is None else f"{won(value)}원"


def _price(value: Decimal | None) -> str:
    return NO_VALUE if value is None else format_price(value)


def row_cells(row: AccountRow) -> tuple[str, ...]:
    """표 한 줄의 글(칸 차례는 `ACCOUNT_TABLE_COLUMNS`)."""
    qty = account_qty_text(qty_text(row.qty), qty_text(row.locked) if row.locked > 0 else None)
    average = _price(row.avg_buy_price)
    if row.avg_modified and row.avg_buy_price is not None:
        average += "*"
    return (
        row.currency,
        qty,
        average,
        _price(row.price),
        _money(row.buy_amount),
        _money(row.value),
        NO_VALUE if row.pnl is None else signed_won(row.pnl),
        signed_percent(row.pnl_rate),
        ACCOUNT_PICKED_TEXT if row.picked else "",
        row.note or "",
    )


class AccountScreen(QWidget):
    refresh_requested = Signal()
    go_key_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName(screen_object_name(ScreenId.ACCOUNT))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*(theme.SCREEN_PADDING,) * 4)
        layout.setSpacing(theme.CARD_GAP)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(title_label(SCREEN_TITLES[ScreenId.ACCOUNT], TITLE_NAME))
        head.addStretch(1)
        self._refresh = QPushButton(ACCOUNT_REFRESH_TEXT)
        self._refresh.setObjectName("accountRefresh")
        self._refresh.clicked.connect(self.refresh_requested)
        head.addWidget(self._refresh)
        layout.addLayout(head)
        self._message = make_label("", "accountMessage")
        self._message.hide()
        layout.addWidget(self._message)
        self._go_key = QPushButton(ACCOUNT_GO_KEY_TEXT)
        self._go_key.setObjectName("accountGoKey")
        self._go_key.clicked.connect(self.go_key_requested)
        self._go_key.hide()
        go_row = QHBoxLayout()
        go_row.setContentsMargins(0, 0, 0, 0)
        go_row.addWidget(self._go_key)
        go_row.addStretch(1)
        layout.addLayout(go_row)
        layout.addWidget(self._build_summary())
        layout.addWidget(self._build_table(), 1)
        self.render(state=AccountState.LOADING, view=None)

    # --- 만들기 ---

    def _build_summary(self) -> Card:
        card = Card("accountSummary")
        info = InfoIcon(
            ACCOUNT_SUMMARY_TITLE, info_text(*ACCOUNT_SUMMARY_INFO), "accountSummaryInfo"
        )
        card.body.addLayout(title_row(ACCOUNT_SUMMARY_TITLE, "accountSummaryTitle", info))
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(theme.CARD_GAP)
        grid.setVerticalSpacing(theme.LABEL_GAP)
        self._values: dict[str, QLabel] = {}
        for row, (key, name) in enumerate(SUMMARY_NAMES.items()):
            caption = make_label(ACCOUNT_SUMMARY_LABELS[key], f"{name}Label")
            set_role(caption, theme.ROLE_SUB)
            value = make_label(NO_VALUE, name)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(caption, row, 0)
            grid.addWidget(value, row, 1)
            self._values[key] = value
        grid.setColumnStretch(0, 1)
        card.body.addLayout(grid)
        self._read_at = make_label("", "accountReadAt")
        set_role(self._read_at, theme.ROLE_SUB)
        card.body.addWidget(self._read_at)
        self._excluded = make_label("", "accountExcluded")
        set_role(self._excluded, theme.ROLE_SUB)
        self._excluded.hide()
        card.body.addWidget(self._excluded)
        self._summary = card
        return card

    def _build_table(self) -> Card:
        card = Card("accountTableCard")
        self._note = make_label("", "accountTableNote")
        set_role(self._note, theme.ROLE_SUB)
        self._note.hide()
        card.body.addWidget(self._note)
        self._table = make_data_table("accountTable")
        self._table.setColumnCount(len(ACCOUNT_TABLE_COLUMNS))
        self._table.setHorizontalHeaderLabels(list(ACCOUNT_TABLE_COLUMNS))
        card.body.addWidget(self._table, 1)
        self._table_card = card
        return card

    # --- 창이 부르는 것 ---

    def render(self, *, state: AccountState, view: AccountView | None) -> None:
        """상태별 모습(3.15.3). `view`는 값이 있을 때만 준다(처음 읽기 전, 설정 전, 키 없음에서는
        `None`). 읽기 실패와 일시 차단은 마지막 값을 흐리게 둔다."""
        has_values = view is not None and view.krw_available is not None
        show_values = has_values and state in (
            AccountState.OK,
            AccountState.FAILED,
            AccountState.BLOCKED,
        )
        dim = state in (AccountState.FAILED, AccountState.BLOCKED)
        message, tone = self._message_of(state, view)
        self._message.setText(message)
        set_tone(self._message, tone)
        self._message.setVisible(bool(message))
        self._go_key.setVisible(state is AccountState.NO_KEY)
        reading = view is not None and view.reading
        self._refresh.setEnabled(
            state in (AccountState.OK, AccountState.FAILED) and not reading
        )
        self._summary.setVisible(show_values)
        self._table_card.setVisible(show_values)
        if not show_values or view is None:
            return
        self._fill_summary(view)
        set_dim(self._summary, dim)  # 흐린 때도 'ⓘ'는 듣는다
        self._fill_table(view)
        self._table.setEnabled(not dim)

    # --- 안쪽 ---

    @staticmethod
    def _message_of(state: AccountState, view: AccountView | None) -> tuple[str, Tone]:
        if state is AccountState.BEFORE_SETUP:
            return ACCOUNT_BEFORE_SETUP, Tone.NORMAL
        if state is AccountState.NO_KEY:
            return ACCOUNT_NO_KEY, Tone.NORMAL
        if state is AccountState.LOADING:
            return LOADING_TEXT, Tone.NORMAL
        if state is AccountState.BLOCKED:
            return ACCOUNT_BLOCKED, Tone.NORMAL
        if state is AccountState.FAILED:
            error = view.error_name if view is not None else None
            return account_failed_text(_when(view), error), Tone.WARNING
        return "", Tone.NORMAL

    def _fill_summary(self, view: AccountView) -> None:
        texts = {
            "krw_available": _money(view.krw_available),
            "krw_locked": _money(view.krw_locked),
            "holdings_value": _money(view.holdings_value),
            "total_assets": _money(view.total_assets),
            "total_buy": _money(view.total_buy),
            "total_pnl": NO_VALUE if view.total_pnl is None else signed_won(view.total_pnl),
            "total_pnl_rate": signed_percent(view.total_pnl_rate),
        }
        for key, label in self._values.items():
            label.setText(texts[key])
        set_trend(self._values["total_pnl"], texts["total_pnl"])
        set_trend(self._values["total_pnl_rate"], texts["total_pnl_rate"])
        when = _when(view)
        self._read_at.setText(account_read_at_text(when) if view.read_utc is not None else "")
        self._read_at.setVisible(view.read_utc is not None)
        excluded = account_excluded_text(view.excluded_value, view.excluded_pnl)
        self._excluded.setText(excluded or "")
        self._excluded.setVisible(excluded is not None)

    def _fill_table(self, view: AccountView) -> None:
        table = self._table
        table.blockSignals(True)
        table.setRowCount(0)
        empty = not view.rows
        self._note.setText(ACCOUNT_NO_HOLDINGS if empty else "")
        self._note.setVisible(empty)
        table.setVisible(not empty)
        table.setRowCount(len(view.rows))
        for index, row in enumerate(view.rows):
            cells = row_cells(row)
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                align = Qt.AlignmentFlag.AlignVCenter | (
                    Qt.AlignmentFlag.AlignRight
                    if column in RIGHT_COLUMNS
                    else Qt.AlignmentFlag.AlignLeft
                )
                item.setTextAlignment(align)
                color = _color(column, text)
                if color is not None:
                    item.setForeground(QColor(color))
                if column == 2 and row.avg_modified and row.avg_buy_price is not None:
                    item.setToolTip(ACCOUNT_AVG_MODIFIED_TIP)
                table.setItem(index, column, item)
        table.blockSignals(False)


def _when(view: AccountView | None) -> str:
    """"마지막으로 읽은 시각"의 글. 읽은 적이 없으면 "-"."""
    if view is None or view.read_utc is None:
        return NO_VALUE
    try:
        return kst_when_text(view.read_utc, view.as_of or datetime.now(UTC))
    except ValueError:
        return NO_VALUE


def _color(column: int, text: str) -> str | None:
    if column in (6, 7):
        trend = trend_of(text)
        if trend == theme.TREND_RISE:
            return theme.RISE
        if trend == theme.TREND_FALL:
            return theme.FALL
    if text == NO_VALUE:
        return theme.TEXT_SUB
    return None
