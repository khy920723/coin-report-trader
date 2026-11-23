"""기록 화면(M3 명세 3.15, 3.17.4, 화면 설계서 5-6 판 2의 2~6장).

위 줄에 코인 고르기 `historyCoin`("모든 코인"과 설정의 코인들. `QComboBox`)과 날짜 고르기
`historyDay`(`QDateEdit`. 하루는 한국 09:00에 바뀌고 처음에는 오늘), 오른쪽에
"CSV로 내보내기" `historyExport`와 그 아래 결과 글 `historyExportMessage`. 그 아래에 탭 다섯
`historyTab_<탭 값>`(`rounds`, `judgments`, `orders`, `fills`, `pauses`), 표 `historyTable`
(`QTableWidget`. 넓으면 안에서 가로로 넘기고 수와 금액과 시각은 오른쪽 맞춤), 표 자리에 보이는
상태 글 `historyMessage`("불러오는 중", "이 날의 기록이 없습니다.", "기록을 읽지 못했습니다."),
고른 줄의 자세한 글 `historyDetail`과 판단 줄의 "보고서 보기" `historyReport`가 있다.

이 화면은 동작을 직접 부르지 않는다. 사용자가 고르기나 탭을 바꾸면 `selection_changed`,
"보고서 보기"는 `report_requested(코인, report_id)`, 내보내기 단추는
`export_requested(탭, 코인, 처음 이름)`을 낸다. 창이 `load_history`, 파일 저장 창,
`export_history`를 부른다.
"""

from collections.abc import Callable
from datetime import date, datetime, timedelta

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QScrollArea,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from coin_report_trader.core import (
    KST,
    SCREEN_TITLES,
    HistoryRow,
    HistoryTab,
    HistoryView,
    LoadState,
    SaveResult,
    ScreenId,
    coin_label,
)
from coin_report_trader.ui import theme
from coin_report_trader.ui.cards import Card
from coin_report_trader.ui.screens import TITLE_NAME, screen_object_name, title_label
from coin_report_trader.ui.state import Message, Tone
from coin_report_trader.ui.texts import (
    HISTORY_ALL_COINS,
    HISTORY_BEFORE_NEXT,
    HISTORY_COIN_CAPTION,
    HISTORY_DAY_CAPTION,
    HISTORY_EXPORT_FAILED,
    HISTORY_EXPORT_OK,
    HISTORY_EXPORT_TEXT,
    HISTORY_FAILED,
    HISTORY_LOADING,
    HISTORY_NONE,
    HISTORY_REPORT_TEXT,
    HISTORY_STILL_PAUSED,
    HISTORY_TAB_TITLES,
    NO_VALUE,
    UNCONFIRMED_TEXT,
    history_export_name,
)
from coin_report_trader.ui.widgets import make_label, set_role, set_tone, trend_of

DAY_START_HOUR = 9
"""하루가 바뀌는 한국 시각(M3 명세 2절)."""
TABS = (
    HistoryTab.ROUNDS,
    HistoryTab.JUDGMENTS,
    HistoryTab.ORDERS,
    HistoryTab.FILLS,
    HistoryTab.PAUSES,
)
RIGHT_COLUMNS = {
    HistoryTab.ROUNDS: (0, 1),
    HistoryTab.JUDGMENTS: (0, 3, 4, 5, 7, 8),
    HistoryTab.ORDERS: (0, 4, 5),
    HistoryTab.FILLS: (0, 3, 4, 5, 6),
    HistoryTab.PAUSES: (0, 2),
}
"""오른쪽에 맞추는 칸(수, 금액, 시각. 설계서 5-6의 4장)."""
TREND_COLUMNS = {HistoryTab.JUDGMENTS: (5,), HistoryTab.FILLS: (6,)}
"""부호와 ▲▼로 오름·내림 색을 칠하는 칸(판단 탭의 차이, 체결 탭의 실현 손익)."""
UNCONFIRMED_COLUMN = 7
"""주문 탭의 상태 칸."""
STILL_PAUSED_COLUMN = 2
"""멈춤 탭의 푼 시각 칸."""
DETAIL_MAX_HEIGHT = 160


def history_day_of(moment: datetime) -> date:
    """그 시각이 든 기록의 하루(한국 시간 09:00에 바뀐다)."""
    return (moment.astimezone(KST) - timedelta(hours=DAY_START_HOUR)).date()


def _qdate(day: date) -> QDate:
    return QDate(day.year, day.month, day.day)


def _day(value: QDate) -> date:
    return date(value.year(), value.month(), value.day())


def _date_edit_style() -> str:
    """날짜 칸과 그 달력 창의 꼴. 앱 스타일시트는 `QDateEdit`을 다루지 않으므로 `theme`의 값으로
    칸(코인 고르기와 같은 꼴)과 달력(어두운 바탕, 고른 날은 `accent`)을 칠한다."""
    return (
        f"QDateEdit {{ background: {theme.SURFACE}; color: {theme.TEXT};"
        f" border: 1px solid {theme.BORDER}; border-radius: {theme.BUTTON_RADIUS}px;"
        f" min-height: {theme.BUTTON_HEIGHT - 4}px; padding: 0 {theme.FIELD_GAP}px;"
        f" selection-background-color: {theme.ACCENT}; }}"
        f"QDateEdit:focus {{ border: 2px solid {theme.FOCUS}; }}"
        f"QDateEdit::drop-down {{ border: 0; width: {theme.ICON_SIZE + theme.FIELD_GAP}px; }}"
        f"QCalendarWidget QWidget {{ background: {theme.SELECTED}; color: {theme.TEXT}; }}"
        f"QCalendarWidget QToolButton {{ color: {theme.TEXT}; background: transparent;"
        f" border: 0; padding: 0 {theme.FIELD_GAP}px; }}"
        f"QCalendarWidget QAbstractItemView:enabled {{ background: {theme.SURFACE};"
        f" color: {theme.TEXT}; selection-background-color: {theme.ACCENT};"
        f" selection-color: {theme.TEXT}; }}"
        f"QCalendarWidget QAbstractItemView:disabled {{ color: {theme.TEXT_DISABLED}; }}"
    )


def _system_now() -> datetime:
    return datetime.now(KST)


class _KeepColorDelegate(QStyledItemDelegate):
    """고른 줄에서도 칸의 글 색(오름·내림, 보조 글, 경고)을 그대로 보인다. 기본은 고른 줄의 글을
    모두 한 색으로 바꾼다."""

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        brush = index.data(Qt.ItemDataRole.ForegroundRole)
        if isinstance(brush, QBrush):
            option.palette.setBrush(QPalette.ColorRole.HighlightedText, brush)


class HistoryScreen(QWidget):
    selection_changed = Signal()
    report_requested = Signal(str, int)
    export_requested = Signal(str, object, str)

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        super().__init__()
        self.setObjectName(screen_object_name(ScreenId.HISTORY))
        self._now = now or _system_now
        self._tab = HistoryTab.ROUNDS
        self._rows: tuple[HistoryRow, ...] = ()
        self._selected_key: str | None = None
        self._day_touched = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*(theme.SCREEN_PADDING,) * 4)
        layout.setSpacing(theme.CARD_GAP)
        layout.addWidget(title_label(SCREEN_TITLES[ScreenId.HISTORY], TITLE_NAME))
        layout.addLayout(self._build_top())
        layout.addLayout(self._build_tabs())
        layout.addWidget(self._build_table(), 1)
        layout.addWidget(self._build_detail())
        self._sync_tabs()

    # --- 만들기 ---

    def _build_top(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.FIELD_GAP)
        coin_caption = make_label(HISTORY_COIN_CAPTION, "historyCoinCaption")
        self._coin = QComboBox()
        self._coin.setObjectName("historyCoin")
        self._coin.addItem(HISTORY_ALL_COINS, None)
        self._coin.currentIndexChanged.connect(self._on_changed)
        coin_caption.setBuddy(self._coin)
        day_caption = make_label(HISTORY_DAY_CAPTION, "historyDayCaption")
        self._day_edit = QDateEdit()
        self._day_edit.setObjectName("historyDay")
        self._day_edit.setCalendarPopup(True)
        self._day_edit.setDisplayFormat("yyyy-MM-dd")
        self._day_edit.setStyleSheet(_date_edit_style())
        self._day_edit.setDate(_qdate(self._default_day()))
        self._day_edit.dateChanged.connect(self._on_day_changed)
        day_caption.setBuddy(self._day_edit)
        for widget in (coin_caption, self._coin, day_caption, self._day_edit):
            row.addWidget(widget)
        row.addStretch(1)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(theme.LABEL_GAP)
        self._export = QPushButton(HISTORY_EXPORT_TEXT)
        self._export.setObjectName("historyExport")
        self._export.clicked.connect(self._on_export)
        self._export_message = make_label("", "historyExportMessage")
        self._export_message.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._export_message.hide()
        right.addWidget(self._export, 0, Qt.AlignmentFlag.AlignRight)
        right.addWidget(self._export_message)
        row.addLayout(right)
        return row

    def _build_tabs(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self._tab_buttons: dict[HistoryTab, QPushButton] = {}
        for tab in TABS:
            button = QPushButton(HISTORY_TAB_TITLES[tab])
            button.setObjectName(f"historyTab_{tab.value}")
            button.setFlat(True)
            button.setProperty("tab", True)
            button.clicked.connect(lambda _checked=False, t=tab: self.select_tab(t))
            self._tab_buttons[tab] = button
            row.addWidget(button)
        row.addStretch(1)
        return row

    def _build_table(self) -> QWidget:
        card = Card("historyCard")
        self._message = make_label("", "historyMessage")
        set_role(self._message, theme.ROLE_SUB)
        card.body.addWidget(self._message)
        table = QTableWidget(0, 0)
        table.setObjectName("historyTable")
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.setTextElideMode(Qt.TextElideMode.ElideNone)
        table.setFrameShape(QFrame.Shape.NoFrame)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(theme.ROW_HEIGHT)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)
        header.setHighlightSections(False)
        table.setStyleSheet(
            f"QTableWidget#historyTable {{ background: {theme.SURFACE}; border: 0; outline: 0; }}"
            f"QTableWidget#historyTable::item {{ border-bottom: 1px solid {theme.DIVIDER};"
            f" padding: 0 {theme.FIELD_GAP}px; }}"
            f"QTableWidget#historyTable::item:selected {{ background: {theme.SELECTED}; }}"
            f"QTableWidget#historyTable:focus {{ border: 2px solid {theme.FOCUS}; }}"
            f"QTableCornerButton::section {{ background: {theme.SURFACE}; border: 0; }}"
            f"QHeaderView {{ background: {theme.SURFACE}; border: 0; }}"
            f"QHeaderView::section {{ background: {theme.SURFACE}; color: {theme.TEXT_SUB};"
            f" border: 0; border-bottom: 1px solid {theme.DIVIDER}; font-weight: bold;"
            f" font-size: {theme.FONT_SUB}px; padding: 0 {theme.FIELD_GAP}px;"
            f" min-height: {theme.ROW_HEIGHT}px; }}"
        )
        table.setItemDelegate(_KeepColorDelegate(table))
        table.itemSelectionChanged.connect(self._on_row_selected)
        self._table = table
        card.body.addWidget(table, 1)
        return card

    def _build_detail(self) -> QWidget:
        card = Card("historyDetailCard")
        card.setMaximumHeight(DETAIL_MAX_HEIGHT + 2 * theme.CARD_PADDING)
        scroll = QScrollArea()
        scroll.setObjectName("historyDetailScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMaximumHeight(DETAIL_MAX_HEIGHT)
        self._detail = make_label("", "historyDetail")
        self._detail.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        scroll.setWidget(self._detail)
        card.body.addWidget(scroll)
        self._report = QPushButton(HISTORY_REPORT_TEXT)
        self._report.setObjectName("historyReport")
        self._report.clicked.connect(self._on_report)
        self._report.hide()
        card.body.addWidget(self._report, 0, Qt.AlignmentFlag.AlignLeft)
        return card

    # --- 창이 부르는 것 ---

    def set_clock(self, now: Callable[[], datetime]) -> None:
        """"오늘"을 읽는 시계를 바꾼다(시험). 날짜는 바꾸지 않는다(`reset_day`가 맞춘다)."""
        self._now = now

    def reset_day(self) -> None:
        """사용자가 날짜를 고르기 전이면 날짜를 오늘로 맞춘다(신호 없이). 화면을 열 때 부른다."""
        if self._day_touched:
            return
        self._set_day(self._default_day())

    def set_coins(self, coins: tuple[str, ...]) -> bool:
        """코인 고르기를 채운다(신호를 내지 않는다). 고르던 코인이 아직 있으면 그대로 두고, 사라져
        "모든 코인"으로 바뀌면 참을 돌려준다."""
        current = self.chosen_market()
        listed = tuple(self._coin.itemData(i) for i in range(1, self._coin.count()))
        if listed == coins:
            return False
        self._coin.blockSignals(True)
        self._coin.clear()
        self._coin.addItem(HISTORY_ALL_COINS, None)
        for market in coins:
            self._coin.addItem(coin_label(market), market)
        index = next((i for i, market in enumerate(coins, 1) if market == current), 0)
        self._coin.setCurrentIndex(index)
        self._coin.blockSignals(False)
        return self.chosen_market() != current

    def chosen_market(self) -> str | None:
        data = self._coin.currentData()
        return data if isinstance(data, str) else None

    def chosen_day(self) -> date:
        return _day(self._day_edit.date())

    def chosen_tab(self) -> HistoryTab:
        return self._tab

    def select_tab(self, tab: HistoryTab) -> None:
        """탭을 고른다(`selection_changed`를 낸다)."""
        if tab is self._tab:
            return
        self._tab = tab
        self._selected_key = None
        self._sync_tabs()
        self.selection_changed.emit()

    def choose(self, tab: HistoryTab, market: str | None, day: date) -> None:
        """다른 화면에서 열 때 탭, 코인, 날짜를 고른다(신호를 내지 않는다). 고르기에 없는 코인은
        "모든 코인"이다."""
        self._tab = tab
        self._selected_key = None
        self._sync_tabs()
        self._coin.blockSignals(True)
        index = self._coin.findData(market) if market is not None else 0
        self._coin.setCurrentIndex(max(index, 0))
        self._coin.blockSignals(False)
        self._day_touched = True
        self._set_day(day)

    def render(self, *, message: Message | None, view: HistoryView | None) -> None:
        """표 자리를 그린다. `message`가 있으면 그 글 하나만 보이고, 없으면 `view`가 지금 고른 것의
        값일 때 표를 보인다(아니면 "불러오는 중")."""
        if message is not None:
            self._show_message(message.text, message.tone)
            return
        if view is None or not self._matches(view) or view.state is LoadState.LOADING:
            self._show_message(HISTORY_LOADING)
        elif view.state is LoadState.FAILED:
            self._show_message(HISTORY_FAILED, Tone.WARNING)
        elif view.state is LoadState.NO_DATA or not view.rows:
            self._show_message(HISTORY_NONE)
        else:
            self._show_table(view)

    def set_export_result(self, result: SaveResult) -> None:
        """내보내기의 결과 글: 성공이면 `ok`, 실패면 `error` 색. 경로는 어디에도 보이지 않는다."""
        text = result.message or (HISTORY_EXPORT_OK if result.ok else HISTORY_EXPORT_FAILED)
        self._export_message.setText(text)
        self._export_message.setProperty(theme.OK_PROPERTY, result.ok)
        set_tone(self._export_message, Tone.NORMAL if result.ok else Tone.WARNING)
        self._export_message.style().unpolish(self._export_message)
        self._export_message.style().polish(self._export_message)
        self._export_message.show()

    # --- 안쪽 ---

    def _default_day(self) -> date:
        return history_day_of(self._now())

    def _set_day(self, day: date) -> None:
        self._day_edit.blockSignals(True)
        self._day_edit.setDate(_qdate(day))
        self._day_edit.blockSignals(False)

    def _matches(self, view: HistoryView) -> bool:
        return (
            view.tab is self._tab
            and view.market == self.chosen_market()
            and view.day == self.chosen_day()
        )

    def _sync_tabs(self) -> None:
        for tab, button in self._tab_buttons.items():
            selected = tab is self._tab
            button.setProperty("selected", selected)
            button.style().unpolish(button)
            button.style().polish(button)
            font = button.font()
            font.setBold(selected)
            font.setUnderline(selected)
            button.setFont(font)

    def _on_changed(self, _index: int = 0) -> None:
        self._selected_key = None
        self.selection_changed.emit()

    def _on_day_changed(self, _value: QDate) -> None:
        self._day_touched = True
        self._on_changed()

    def _on_export(self) -> None:
        market = self.chosen_market()
        symbol = coin_label(market) if market is not None else None
        name = history_export_name(
            HISTORY_TAB_TITLES[self._tab], symbol, self._now().strftime("%Y%m%d")
        )
        self._export_message.hide()
        self._export_message.setText("")
        self.export_requested.emit(self._tab.value, market, name)

    def _on_report(self) -> None:
        row = self._selected_row()
        if row is not None and row.market is not None and row.report_id is not None:
            self.report_requested.emit(row.market, row.report_id)

    def _show_message(self, text: str, tone: Tone = Tone.NORMAL) -> None:
        self._rows = ()
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        self._table.blockSignals(False)
        self._table.hide()
        self._message.setText(text)
        set_tone(self._message, tone)
        self._message.show()
        self._update_detail()

    def _show_table(self, view: HistoryView) -> None:
        self._message.setText("")
        self._message.hide()
        table = self._table
        table.blockSignals(True)
        table.clear()
        table.setColumnCount(len(view.columns))
        table.setHorizontalHeaderLabels(list(view.columns))
        table.setRowCount(len(view.rows))
        self._rows = view.rows
        right = RIGHT_COLUMNS[view.tab]
        trend = TREND_COLUMNS.get(view.tab, ())
        keep = None
        for index, row in enumerate(view.rows):
            for column in range(len(view.columns)):
                text = row.cells[column] if column < len(row.cells) else ""
                item = QTableWidgetItem(text)
                align = Qt.AlignmentFlag.AlignVCenter | (
                    Qt.AlignmentFlag.AlignRight if column in right else Qt.AlignmentFlag.AlignLeft
                )
                item.setTextAlignment(align)
                color = self._cell_color(view.tab, column, text, trend)
                if color is not None:
                    item.setForeground(QColor(color))
                table.setItem(index, column, item)
            if row.key == self._selected_key:
                keep = index
        table.blockSignals(False)
        header = table.horizontalHeader()
        for column in range(len(view.columns)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        table.show()
        if keep is not None:
            table.selectRow(keep)
        else:
            self._selected_key = None
            table.clearSelection()
        self._update_detail()

    @staticmethod
    def _cell_color(tab: HistoryTab, column: int, text: str, trend: tuple[int, ...]) -> str | None:
        if column in trend:
            rule = trend_of(text)
            if rule == theme.TREND_RISE:
                return theme.RISE
            if rule == theme.TREND_FALL:
                return theme.FALL
        if text in (NO_VALUE, HISTORY_BEFORE_NEXT):
            return theme.TEXT_SUB
        if tab is HistoryTab.ORDERS and column == UNCONFIRMED_COLUMN and text == UNCONFIRMED_TEXT:
            return theme.ERROR
        still = tab is HistoryTab.PAUSES and column == STILL_PAUSED_COLUMN
        if still and text == HISTORY_STILL_PAUSED:
            return theme.CAUTION
        return None

    def _selected_row(self) -> HistoryRow | None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return None
        index = rows[0].row()
        return self._rows[index] if 0 <= index < len(self._rows) else None

    def _on_row_selected(self) -> None:
        row = self._selected_row()
        self._selected_key = row.key if row is not None else None
        self._update_detail()

    def _update_detail(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        row = self._rows[rows[0].row()] if rows and rows[0].row() < len(self._rows) else None
        if row is None:
            self._detail.setText("")
            self._report.hide()
            return
        self._detail.setText("\n".join(f"{name}: {value}" for name, value in row.detail))
        self._report.setVisible(row.market is not None and row.report_id is not None)
