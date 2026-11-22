"""현황 화면(설계서 4, M1 명세 4절 17번, 25번, 37번): 코인 칸의 봉 통계, 봉 통계 자리, 알림센터.

`CoinView.stats`(M1 명세 3.14.5)의 봉 통계를 그린다. `None`이면 받아 둔 값이 없는 쪽이다.

알림센터(설계서 4.2의 5)는 `alert_center.py`다. 보유, 주문, 손절선, 오늘 요약, 지금 회차, 모의
운영은 M3에서 생긴다.
"""

from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from coin_report_trader.core import (
    BAR_STATS_TITLES,
    CANDLE_UNIT_TITLES,
    INSUFFICIENT_TEXT,
    SCREEN_TITLES,
    BarStats,
    CoinView,
    LoadState,
    ScreenId,
    coin_label,
    format_percent,
    format_price,
)
from coin_report_trader.core.texts import (
    CANDLES_LOADING,
    ERROR_BLOCKED,
    blocked_candles_text,
    kst_datetime_text,
    kst_when_text,
)
from coin_report_trader.ui import theme
from coin_report_trader.ui.alert_center import AlertCenter
from coin_report_trader.ui.cards import Card, CardGrid, title_row
from coin_report_trader.ui.info_icon import InfoIcon
from coin_report_trader.ui.screens import TITLE_NAME, screen_object_name, title_label
from coin_report_trader.ui.state import Message
from coin_report_trader.ui.texts import (
    INDICATOR_DISCLAIMER,
    LAST_CANDLE_INCOMPLETE,
    LOADING_TEXT,
    NO_CANDLES_YET,
    REPORT_VIEW_TEXT,
    RETRY_TEXT,
    STATS_TABLE_COLUMNS,
    STATS_TITLE,
    bar_count_text,
    candles_failed_text,
    fetched_text,
    indicator_row_text,
    stats_header_text,
    stats_short_header_text,
    stats_summary_text,
)
from coin_report_trader.ui.widgets import make_label, set_role, set_trend, show_message

MESSAGE_NAME = "statusMessage"
CONTENT_NAME = "statusContent"
COINS_NAME = "statusCoins"
STATS_NAME = "statusStats"
STATS_CHOOSER_NAME = "statusStatsCoin"
STATS_NOTE_NAME = "statusStatsNote"
STATS_TABLE_NAME = "statusStatsTable"
STATS_FETCHED_NAME = "statusStatsFetched"
STATS_INCOMPLETE_NAME = "statusStatsIncomplete"
STATS_SUMMARY_NAME = "statusStatsSummary"
STATS_DISCLAIMER_NAME = "statusStatsDisclaimer"
STATS_RETRY_NAME = "statusStatsRetry"

MAX_COINS = 3

_CARD_ELEMENTS: tuple[tuple[str, bool], ...] = (
    ("change", True),
    ("slope", True),
    ("vs_mean", True),
    ("up_ratio", False),
    ("volatility", False),
)
"""코인 칸의 다섯 요소(열 이름, 부호를 붙이는지. M1 명세 3.5.1의 화면의 글)."""

_TABLE_ROWS: tuple[tuple[str, str], ...] = (
    ("bar_count", "count"),
    ("first_start_utc", "time"),
    ("last_start_utc", "time"),
    ("close_last", "price"),
    ("change", "signed"),
    ("slope", "signed"),
    ("vs_mean", "signed"),
    ("up_ratio", "plain"),
    ("volatility", "plain"),
    ("max_drawdown", "signed"),
    ("from_high", "signed"),
    ("high", "price"),
    ("low", "price"),
)
"""봉 통계 자리 표의 줄(열 이름, 값의 꼴. 3.5.1의 `bar_count`부터 `low`까지의 차례)."""


@dataclass(frozen=True)
class Look:
    """한 코인의 `CoinView`를 그리는 모습. 코인 칸과 봉 통계 표가 같이 쓴다."""

    note: str | None
    """값 자리의 글(불러오는 중, 아직 받은 봉이 없음, 봉이 모자람, 받는 중, 받지 못함, 차단)."""
    values: bool
    """표의 줄과 요소를 그리는지(받아 둔 값이 있을 때)."""
    dim: bool
    """받아 둔 값을 흐리게 남기는지(받기 실패와 일시 차단 중)."""
    retry: bool
    """코인 칸에 "다시 시도"가 있는지."""
    fetched: bool
    incomplete: bool
    disclaimer: bool


def has_values(view: CoinView) -> bool:
    """ "받아 둔 값이 있다": `stats.bar_count`가 1 이상이다(M1 명세 3.4)."""
    return view.stats is not None and view.stats.bar_count >= 1


def is_short(stats: BarStats) -> bool:
    """ "봉이 모자람": 완성된 봉이 고른 개수보다 적다(M1 명세 3.5)."""
    return stats.bar_count < stats.requested_count


def _blocked_note(view: CoinView) -> str | None:
    if view.blocked_until is None:
        return None
    try:
        return blocked_candles_text(kst_when_text(view.blocked_until, view.as_of))
    except ValueError:
        return None


def look_of(view: CoinView | None) -> Look:
    """`CoinView`(없으면 첫 값이 오기 전)를 설계서 4.5의 한 모습으로 가른다."""
    if view is None:
        return Look(LOADING_TEXT, False, False, False, False, False, False)
    values = has_values(view)
    fetched = view.fetched_at is not None
    incomplete = view.last_incomplete
    if view.state is LoadState.LOADING:
        return Look(CANDLES_LOADING, values, False, False, fetched, incomplete, True)
    if view.state is LoadState.FAILED:
        note = _blocked_note(view) if view.error_name == ERROR_BLOCKED else None
        if note is None:
            note = candles_failed_text(view.error_name)
        return Look(note, values, values, True, fetched, incomplete, True)
    if values:
        return Look(None, True, False, False, fetched, incomplete, True)
    note = INSUFFICIENT_TEXT if fetched else NO_CANDLES_YET
    return Look(note, False, False, False, fetched, incomplete, True)


def _price_text(value) -> str:
    if value is None:
        return INSUFFICIENT_TEXT
    try:
        return f"{format_price(value)}원"
    except ValueError:
        return INSUFFICIENT_TEXT


def _fetched_line(view: CoinView | None) -> str | None:
    if view is None or view.fetched_at is None:
        return None
    try:
        return fetched_text(kst_datetime_text(view.fetched_at))
    except ValueError:
        return None


def _time_text(value) -> str:
    if value is None:
        return INSUFFICIENT_TEXT
    try:
        return kst_datetime_text(value)
    except ValueError:
        return INSUFFICIENT_TEXT


def element_rows(stats: BarStats) -> tuple[str, ...]:
    """코인 칸의 다섯 요소 줄 "{이름} {값}"(M1 명세 3.14.5). 값이 없으면 "봉이 모자람"이다."""
    return tuple(
        indicator_row_text(
            BAR_STATS_TITLES[column], format_percent(getattr(stats, column), signed=signed)
        )
        for column, signed in _CARD_ELEMENTS
    )


def table_rows(stats: BarStats) -> tuple[tuple[str, str], ...]:
    """봉 통계 자리 표의 (항목, 값) 줄(`bar_count`부터 `low`까지. M1 명세 3.14.5)."""
    rows = []
    for column, kind in _TABLE_ROWS:
        value = getattr(stats, column)
        if kind == "count":
            text = bar_count_text(stats.bar_count, stats.requested_count, short=is_short(stats))
        elif kind == "time":
            text = _time_text(value)
        elif kind == "price":
            text = _price_text(value)
        else:
            text = format_percent(value, signed=kind == "signed")
        rows.append((BAR_STATS_TITLES[column], text))
    return tuple(rows)


_TREND_COLUMNS = frozenset({"change", "slope", "vs_mean"})
"""부호가 있어 오름·내림 색을 입히는 요소(M1 명세 3.14.7)."""


def element_pairs(stats: BarStats) -> tuple[tuple[str, str, bool], ...]:
    """코인 칸의 다섯 요소 (이름, 값, 오름·내림 색을 입히는지)."""
    return tuple(
        (
            BAR_STATS_TITLES[column],
            format_percent(getattr(stats, column), signed=signed),
            column in _TREND_COLUMNS,
        )
        for column, signed in _CARD_ELEMENTS
    )


class ElementRow(QLabel):
    """코인 칸의 요소 한 줄. 글은 "{이름} {값}"(`text()`)이고, 이름은 왼쪽 보조 글, 값은 오른쪽 맞춤
    글 색으로 그린다. 부호가 있는 값은 오름·내림 색이다."""

    def __init__(self, name: str) -> None:
        super().__init__("")
        self.setObjectName(name)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self._name = ""
        self._value = ""
        self._trend = False

    def set_row(self, name: str, value: str, *, trend: bool) -> None:
        self._name, self._value, self._trend = name, value, trend
        self.setText(indicator_row_text(name, value))
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 24, self._height())

    def minimumSizeHint(self) -> QSize:
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 24, self._height())

    def _height(self) -> int:
        return max(self.fontMetrics().height() + 2 * theme.UNIT, theme.ROW_HEIGHT - 2 * theme.UNIT)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        enabled = self.isEnabled()
        sub = QColor(theme.TEXT_SUB if enabled else theme.TEXT_DISABLED)
        plain = QColor(theme.TEXT if enabled else theme.TEXT_DISABLED)
        if enabled and self._trend and self._value.startswith("+"):
            plain = QColor(theme.RISE)
        elif enabled and self._trend and self._value.startswith("-"):
            plain = QColor(theme.FALL)
        rect = self.rect()
        painter.setPen(sub)
        painter.drawText(
            rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._name
        )
        painter.setPen(plain)
        painter.drawText(
            rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._value
        )
        painter.setPen(QColor(theme.DIVIDER))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())


class CoinCard(Card):
    """고른 코인 하나의 카드: 코인(큰 값), 값 자리의 글, 봉 통계의 요소, 미완성 봉 글.

    `objectName`(`<m>`은 마켓 코드): `statusCoin_<m>`(카드), `statusCoinName_<m>`(코인 글),
    `statusCoinNote_<m>`(값 자리의 글), `statusCoinRetry_<m>`("다시 시도"),
    `statusCoinStatsHeader_<m>`(첫 줄 "60분봉 200개"), `statusCoinDisclaimer_<m>`(첫 줄 옆 'ⓘ'.
    툴팁이 보장 아님 글), `statusCoinStats_<m>`(다섯 요소 묶음. 흐리게 남기면 `isEnabled()`가 거짓),
    `statusCoinStatsRow_<m>_<0~4>`(요소 한 줄 "이름 값". 이름은 왼쪽 보조 글, 값은 오른쪽 맞춤),
    `statusCoinIncomplete_<m>`.
    """

    retry_requested = Signal()
    report_requested = Signal(str)
    """"보고서 보기"를 눌렀다(M2 명세 3.12.4)."""
    chosen = Signal(str)
    """코인 칸의 빈 곳이나 글을 눌렀다(봉 통계 자리의 코인 고르기가 이 코인으로 바뀐다)."""

    def __init__(self, market: str) -> None:
        super().__init__(f"statusCoin_{market}")
        self.market = market
        self.setProperty("clickable", True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout = self.body
        name = title_label(coin_label(market), f"statusCoinName_{market}")
        set_role(name, theme.ROLE_BIG)
        layout.addWidget(name)
        self._note = make_label("", f"statusCoinNote_{market}")
        layout.addWidget(self._note)
        self._retry = QPushButton(RETRY_TEXT)
        self._retry.setObjectName(f"statusCoinRetry_{market}")
        self._retry.clicked.connect(self.retry_requested)
        layout.addWidget(self._retry, 0, Qt.AlignmentFlag.AlignLeft)
        # 첫 줄 "60분봉 200개"와 그 옆 'ⓘ'(M1 명세 3.14.7).
        # 'ⓘ'는 옛 보장 아님 글이 보이던 때 보인다.
        self._stats_header = make_label("", f"statusCoinStatsHeader_{market}")
        set_role(self._stats_header, theme.ROLE_GROUP)
        self._info = InfoIcon("", INDICATOR_DISCLAIMER, f"statusCoinDisclaimer_{market}")
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(theme.ICON_GAP)
        header_row.addWidget(self._stats_header)
        header_row.addWidget(self._info)
        header_row.addStretch(1)
        layout.addLayout(header_row)
        self._stats_box = QWidget()
        self._stats_box.setObjectName(f"statusCoinStats_{market}")
        stats_layout = QVBoxLayout(self._stats_box)
        stats_layout.setContentsMargins(0, 0, 0, 0)
        stats_layout.setSpacing(0)
        self._stats_rows = [
            ElementRow(f"statusCoinStatsRow_{market}_{index}")
            for index in range(len(_CARD_ELEMENTS))
        ]
        for row in self._stats_rows:
            stats_layout.addWidget(row)
        layout.addWidget(self._stats_box)
        self._incomplete = make_label(LAST_CANDLE_INCOMPLETE, f"statusCoinIncomplete_{market}")
        set_role(self._incomplete, theme.ROLE_SUB)
        layout.addWidget(self._incomplete)
        layout.addStretch(1)
        # 맨 아래의 "보고서 보기"(M2 명세 3.12.4). 코인 칸이 있는 동안 상태와 상관없이 보인다.
        self._report = QPushButton(REPORT_VIEW_TEXT)
        self._report.setObjectName(f"statusCoinReport_{market}")
        self._report.clicked.connect(lambda: self.report_requested.emit(self.market))
        layout.addWidget(self._report, 0, Qt.AlignmentFlag.AlignLeft)
        self.render(None)

    def mousePressEvent(self, event) -> None:
        """단추 말고 칸의 빈 곳이나 글을 누르면 봉 통계 자리의 코인이 이 코인으로 바뀐다."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.chosen.emit(self.market)
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.chosen.emit(self.market)
            event.accept()
            return
        super().keyPressEvent(event)

    def render(self, view: CoinView | None) -> None:
        look = look_of(view)
        show_message(self._note, Message(look.note) if look.note else None)
        self._retry.setVisible(look.retry)
        stats = view.stats if view is not None else None
        shown = look.values and stats is not None
        self._stats_box.setVisible(shown)
        self._stats_box.setEnabled(not look.dim)
        self._stats_header.setVisible(shown)
        self._stats_header.setEnabled(not look.dim)
        self._info.setVisible(look.disclaimer)
        if stats is not None and look.values:
            unit_title = CANDLE_UNIT_TITLES[stats.unit]
            header = (
                stats_short_header_text(unit_title, stats.bar_count, stats.requested_count)
                if is_short(stats)
                else stats_header_text(unit_title, stats.requested_count)
            )
            self._stats_header.setText(header)
            self._info.set_info(INDICATOR_DISCLAIMER, header)
            for row, (name, value, trend) in zip(
                self._stats_rows, element_pairs(stats), strict=True
            ):
                row.set_row(name, value, trend=trend)
        self._incomplete.setVisible(look.incomplete)


class StatsPanel(Card):
    """봉 통계 카드(설계서 4.2의 8, M1 명세 3.14.5, 3.14.7): 제목과 'ⓘ', 코인 고르기, 표.

    `objectName`: `statusStats`(카드), `statusStatsTitle`(제목 "봉 통계"),
    `statusStatsDisclaimer`(제목 옆 'ⓘ'. 툴팁이 보장 아님 글), `statusStatsCoin`(코인 고르기
    `QComboBox`. 글은 코인 글, 현재 값은 마켓 코드가 아니라 인덱스), `statusStatsNote`(표 자리의
    글), `statusStatsSummary`(표 위 "60분봉 · 고른 개수 200개"), `statusStatsTable`(표 묶음. 흐리게
    남기면 `isEnabled()`가 거짓), `statusStatsHeader_<0~1>`(머리 칸 "항목", "값". 보조 글),
    `statusStatsCell_<줄 0~12>_<칸 0~1>`(칸. 칸 0은 항목, 칸 1은 값, 오른쪽 맞춤),
    `statusStatsFetched`("마지막으로 받은 때"), `statusStatsIncomplete`,
    `statusStatsRetry`(표 아래의 하나뿐인 "다시 시도").
    """

    retry_requested = Signal()
    chosen = Signal()

    def __init__(self) -> None:
        super().__init__(STATS_NAME)
        layout = self.body
        self._info = InfoIcon(STATS_TITLE, INDICATOR_DISCLAIMER, STATS_DISCLAIMER_NAME)
        layout.addLayout(title_row(STATS_TITLE, "statusStatsTitle", self._info))
        self._chooser = QComboBox()
        self._chooser.setObjectName(STATS_CHOOSER_NAME)
        self._chooser.currentIndexChanged.connect(lambda _index: self.chosen.emit())
        layout.addWidget(self._chooser)
        self._note = make_label("", STATS_NOTE_NAME)
        layout.addWidget(self._note)

        self._summary = make_label("", STATS_SUMMARY_NAME)
        set_role(self._summary, theme.ROLE_SUB)
        layout.addWidget(self._summary)
        self._table = QWidget()
        self._table.setObjectName(STATS_TABLE_NAME)
        self._grid = QGridLayout(self._table)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(theme.FIELD_GAP)
        self._grid.setVerticalSpacing(0)
        self._grid.setColumnStretch(0, 1)
        for column, title in enumerate(STATS_TABLE_COLUMNS):
            head = make_label(title, f"statusStatsHeader_{column}")
            set_role(head, theme.ROLE_SUB)
            if column == 1:
                head.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._grid.addWidget(head, 0, column)
        self._cells: list[list[QLabel]] = []
        for row in range(len(_TABLE_ROWS)):
            cells = []
            for column in range(len(STATS_TABLE_COLUMNS)):
                cell = make_label("", f"statusStatsCell_{row}_{column}")
                cell.setProperty("cell", True)
                if column == 1:
                    cell.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                else:
                    cell.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                cells.append(cell)
                self._grid.addWidget(cell, row + 1, column)
            self._cells.append(cells)
        layout.addWidget(self._table)

        self._fetched = make_label("", STATS_FETCHED_NAME)
        layout.addWidget(self._fetched)
        self._incomplete = make_label(LAST_CANDLE_INCOMPLETE, STATS_INCOMPLETE_NAME)
        set_role(self._incomplete, theme.ROLE_SUB)
        layout.addWidget(self._incomplete)
        row_of_buttons = QHBoxLayout()
        self._retry = QPushButton(RETRY_TEXT)
        self._retry.setObjectName(STATS_RETRY_NAME)
        self._retry.clicked.connect(self.retry_requested)
        row_of_buttons.addWidget(self._retry)
        row_of_buttons.addStretch(1)
        layout.addLayout(row_of_buttons)
        self.render(None)

    def set_coins(self, coins: tuple[str, ...]) -> None:
        """코인 고르기의 목록을 바꾼다. 고르던 코인이 아직 있으면 그대로 고른다."""
        current = self.chosen_market()
        self._chooser.blockSignals(True)
        self._chooser.clear()
        for market in coins:
            self._chooser.addItem(coin_label(market), market)
        index = self._chooser.findData(current) if current is not None else -1
        self._chooser.setCurrentIndex(max(index, 0) if coins else -1)
        self._chooser.blockSignals(False)
        self.setVisible(bool(coins))

    def chosen_market(self) -> str | None:
        data = self._chooser.currentData()
        return data if isinstance(data, str) else None

    def choose(self, market: str) -> bool:
        """코인 고르기에서 그 코인을 고른다(사용자가 고른 것과 같다. `chosen`이 난다)."""
        index = self._chooser.findData(market)
        if index < 0:
            return False
        self._chooser.setCurrentIndex(index)
        return True

    def render(self, view: CoinView | None) -> None:
        look = look_of(view)
        show_message(self._note, Message(look.note) if look.note else None)
        stats = view.stats if view is not None else None
        self._table.setVisible(look.values)
        self._table.setEnabled(not look.dim)
        self._summary.setVisible(look.values and stats is not None)
        if stats is not None and look.values:
            self._summary.setText(
                stats_summary_text(CANDLE_UNIT_TITLES[stats.unit], stats.requested_count)
            )
            for cells, column, (name, value) in zip(
                self._cells, (c for c, _ in _TABLE_ROWS), table_rows(stats), strict=True
            ):
                cells[0].setText(name)
                cells[1].setText(value)
                if column in _TREND_COLUMNS:
                    set_trend(cells[1], value)
                else:
                    set_trend(cells[1], "")
        self._info.setVisible(look.disclaimer)
        fetched = _fetched_line(view) if look.fetched else None
        show_message(self._fetched, Message(fetched) if fetched else None)
        self._incomplete.setVisible(look.incomplete)


class StatusScreen(QWidget):
    """현황 화면. 설정을 불러오는 중, 처음 설정 전, 설정을 읽지 못함에는 글 하나만 보인다."""

    retry_requested = Signal()
    report_requested = Signal(str)
    """코인 칸의 "보고서 보기"를 눌렀다(그 코인의 마켓 코드)."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName(screen_object_name(ScreenId.STATUS))
        self._coins: tuple[str, ...] = ()
        self._views: dict[str, CoinView] = {}
        self._cards: dict[str, CoinCard] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*(theme.SCREEN_PADDING,) * 4)
        layout.setSpacing(theme.CARD_GAP)
        layout.addWidget(title_label(SCREEN_TITLES[ScreenId.STATUS], TITLE_NAME))
        self._message = make_label("", MESSAGE_NAME)
        self._message.hide()
        layout.addWidget(self._message)

        self._content = QWidget()
        self._content.setObjectName(CONTENT_NAME)
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(theme.CARD_GAP)
        self._cards_holder = CardGrid(max_columns=MAX_COINS)
        self._cards_holder.setObjectName(COINS_NAME)
        content_layout.addWidget(self._cards_holder)
        self._stats = StatsPanel()
        self._stats.retry_requested.connect(self.retry_requested)
        self._stats.chosen.connect(self._render_stats)
        content_layout.addWidget(self._stats)
        layout.addWidget(self._content)
        # 알림센터는 글 하나만 보이는 모습(설정을 불러오는 중 등)에서도 그대로 보인다(설계서 4.5).
        self.alert_center = AlertCenter()
        alert_card = Card("statusAlertCard")
        alert_card.body.addWidget(self.alert_center)
        layout.addWidget(alert_card)
        layout.addStretch(1)
        self._content.hide()

    def render(
        self,
        *,
        message: Message | None,
        coins: tuple[str, ...],
        views: dict[str, CoinView],
    ) -> None:
        """`message`가 있으면 그 글 하나만 보인다. 없으면 고른 코인의 칸과 표를 그린다."""
        show_message(self._message, message)
        self._content.setVisible(message is None)
        coins = coins[:MAX_COINS]
        self._views = views
        if coins != self._coins:
            self._coins = coins
            self._rebuild_cards()
            self._stats.set_coins(coins)
        for market, card in self._cards.items():
            card.render(views.get(market))
        self._render_stats()

    def _rebuild_cards(self) -> None:
        for market in list(self._cards):
            if market not in self._coins:
                card = self._cards.pop(market)
                card.setParent(None)
                card.deleteLater()
        for market in self._coins:
            if market not in self._cards:
                card = CoinCard(market)
                card.retry_requested.connect(self.retry_requested)
                card.report_requested.connect(self.report_requested)
                card.chosen.connect(self._stats.choose)
                self._cards[market] = card
        self._cards_holder.set_cards([self._cards[market] for market in self._coins])

    def card_of(self, market: str) -> QWidget | None:
        """그 코인의 칸. 고른 코인에 없으면 `None`."""
        return self._cards.get(market)

    def _render_stats(self) -> None:
        market = self._stats.chosen_market()
        self._stats.render(self._views.get(market) if market is not None else None)
