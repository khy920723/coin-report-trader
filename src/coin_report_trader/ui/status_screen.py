"""현황 화면(설계서 4, M1 명세 4절 17번): 코인 칸의 지표와 기간별 봉 통계.

알림센터(설계서 4.2의 5)는 25번이 이 화면에 더한다. 보유, 주문, 손절선, 오늘 요약, 지금 회차, 모의
운영은 M3에서 생긴다.
"""

from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from coin_report_trader.core import (
    INSUFFICIENT_TEXT,
    SCREEN_TITLES,
    CandleIndicator,
    CoinView,
    LoadState,
    PeriodStats,
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
from coin_report_trader.ui.screens import TITLE_NAME, screen_object_name, title_label
from coin_report_trader.ui.state import Message
from coin_report_trader.ui.texts import (
    INDICATOR_DISCLAIMER,
    LAST_CANDLE_INCOMPLETE,
    LOADING_TEXT,
    NO_CANDLES_YET,
    RETRY_TEXT,
    STATS_COLUMNS,
    STATS_TITLE,
    candles_failed_text,
    count_text,
    fetched_text,
    indicator_row_text,
)
from coin_report_trader.ui.widgets import make_label, show_message

MESSAGE_NAME = "statusMessage"
CONTENT_NAME = "statusContent"
COINS_NAME = "statusCoins"
STATS_NAME = "statusStats"
STATS_CHOOSER_NAME = "statusStatsCoin"
STATS_NOTE_NAME = "statusStatsNote"
STATS_TABLE_NAME = "statusStatsTable"
STATS_FETCHED_NAME = "statusStatsFetched"
STATS_INCOMPLETE_NAME = "statusStatsIncomplete"
STATS_RETRY_NAME = "statusStatsRetry"

MAX_COINS = 3
_PERIOD_ROWS = 5
_INDICATOR_ROWS = 7


@dataclass(frozen=True)
class Look:
    """한 코인의 `CoinView`를 그리는 모습. 코인 칸과 기간별 통계 표가 같이 쓴다."""

    note: str | None
    """값 자리의 글(불러오는 중, 아직 받은 봉이 없음, 봉이 모자람, 받는 중, 받지 못함, 차단)."""
    values: bool
    """표의 줄과 지표 항목을 그리는지(받아 둔 값이 있을 때)."""
    dim: bool
    """받아 둔 값을 흐리게 남기는지(받기 실패와 일시 차단 중)."""
    retry: bool
    """코인 칸의 지표 자리에 "다시 시도"가 있는지."""
    fetched: bool
    incomplete: bool
    disclaimer: bool


def has_values(view: CoinView) -> bool:
    """ "받아 둔 값이 있다": 다섯 기간 가운데 쓴 봉 수가 1 이상인 기간이 있다(M1 명세 3.4)."""
    return any(period.count >= 1 for period in view.periods)


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


def row_cells(period: PeriodStats) -> tuple[str, ...]:
    """기간별 통계 표의 한 줄(설계서 4.2의 8, M1 명세 3.5·3.6의 꼴)."""
    return (
        period.period,
        count_text(period.count),
        format_percent(period.change, signed=True),
        _price_text(period.high),
        _price_text(period.low),
        format_percent(period.from_high, signed=True),
        format_percent(period.max_drawdown, signed=True),
        format_percent(period.volatility, signed=False),
        format_percent(period.up_ratio, signed=False),
    )


class CoinCard(QWidget):
    """고른 코인 하나의 칸: 코인, 값 자리의 글, 지표, 미완성 봉 글, 보장 아님 글.

    `objectName`(`<m>`은 마켓 코드): `statusCoin_<m>`(칸), `statusCoinName_<m>`(코인 글),
    `statusCoinNote_<m>`(값 자리의 글), `statusCoinRetry_<m>`("다시 시도"),
    `statusCoinIndicator_<m>`(지표 묶음. 흐리게 남기면 `isEnabled()`가 거짓),
    `statusCoinIndicatorRow_<m>_<0~6>`(지표 한 줄), `statusCoinIncomplete_<m>`,
    `statusCoinDisclaimer_<m>`.
    """

    retry_requested = Signal()

    def __init__(self, market: str) -> None:
        super().__init__()
        self.market = market
        self.setObjectName(f"statusCoin_{market}")
        layout = QVBoxLayout(self)
        layout.addWidget(title_label(coin_label(market), f"statusCoinName_{market}"))
        self._note = make_label("", f"statusCoinNote_{market}")
        layout.addWidget(self._note)
        self._retry = QPushButton(RETRY_TEXT)
        self._retry.setObjectName(f"statusCoinRetry_{market}")
        self._retry.clicked.connect(self.retry_requested)
        layout.addWidget(self._retry)
        self._indicator = QWidget()
        self._indicator.setObjectName(f"statusCoinIndicator_{market}")
        indicator_layout = QVBoxLayout(self._indicator)
        indicator_layout.setContentsMargins(0, 0, 0, 0)
        self._rows = [
            make_label("", f"statusCoinIndicatorRow_{market}_{index}")
            for index in range(_INDICATOR_ROWS)
        ]
        for row in self._rows:
            indicator_layout.addWidget(row)
        layout.addWidget(self._indicator)
        self._incomplete = make_label(LAST_CANDLE_INCOMPLETE, f"statusCoinIncomplete_{market}")
        layout.addWidget(self._incomplete)
        self._disclaimer = make_label(INDICATOR_DISCLAIMER, f"statusCoinDisclaimer_{market}")
        layout.addWidget(self._disclaimer)
        layout.addStretch(1)
        self.render(None)

    def render(self, view: CoinView | None) -> None:
        look = look_of(view)
        show_message(self._note, Message(look.note) if look.note else None)
        self._retry.setVisible(look.retry)
        self._indicator.setVisible(look.values)
        self._indicator.setEnabled(not look.dim)
        if view is not None and look.values:
            indicator = view.indicator if view.indicator is not None else CandleIndicator()
            for label, (title, value) in zip(self._rows, indicator.items(), strict=True):
                label.setText(indicator_row_text(title, value))
        self._incomplete.setVisible(look.incomplete)
        self._disclaimer.setVisible(look.disclaimer)


class StatsPanel(QWidget):
    """기간별 봉 통계(설계서 4.2의 8): 코인 고르기, 표, 표 아래 글과 "다시 시도".

    `objectName`: `statusStats`(묶음), `statusStatsCoin`(코인 고르기 `QComboBox`. 글은 코인 글, 현재
    값은 마켓 코드가 아니라 인덱스), `statusStatsNote`(표 자리의 글), `statusStatsTable`(표 묶음.
    흐리게 남기면 `isEnabled()`가 거짓), `statusStatsHeader_<0~8>`(머리 칸),
    `statusStatsCell_<줄 0~4>_<칸 0~8>`(칸. 칸 0은 기간 이름), `statusStatsFetched`("마지막으로 받은
    때"), `statusStatsIncomplete`, `statusStatsRetry`(표 아래의 하나뿐인 "다시 시도").
    """

    retry_requested = Signal()
    chosen = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName(STATS_NAME)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(title_label(STATS_TITLE, "statusStatsTitle"))
        self._chooser = QComboBox()
        self._chooser.setObjectName(STATS_CHOOSER_NAME)
        self._chooser.currentIndexChanged.connect(lambda _index: self.chosen.emit())
        layout.addWidget(self._chooser)
        self._note = make_label("", STATS_NOTE_NAME)
        layout.addWidget(self._note)

        self._table = QWidget()
        self._table.setObjectName(STATS_TABLE_NAME)
        grid = QGridLayout(self._table)
        grid.setContentsMargins(0, 0, 0, 0)
        for column, title in enumerate(STATS_COLUMNS):
            grid.addWidget(make_label(title, f"statusStatsHeader_{column}"), 0, column)
        self._cells = [
            [
                make_label("", f"statusStatsCell_{row}_{column}")
                for column in range(len(STATS_COLUMNS))
            ]
            for row in range(_PERIOD_ROWS)
        ]
        for row, cells in enumerate(self._cells, start=1):
            for column, cell in enumerate(cells):
                grid.addWidget(cell, row, column)
        layout.addWidget(self._table)

        self._fetched = make_label("", STATS_FETCHED_NAME)
        layout.addWidget(self._fetched)
        self._incomplete = make_label(LAST_CANDLE_INCOMPLETE, STATS_INCOMPLETE_NAME)
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

    def render(self, view: CoinView | None) -> None:
        look = look_of(view)
        show_message(self._note, Message(look.note) if look.note else None)
        self._table.setVisible(look.values)
        self._table.setEnabled(not look.dim)
        if view is not None and look.values:
            periods = view.periods
            for row, cells in enumerate(self._cells):
                if row < len(periods):
                    for cell, text in zip(cells, row_cells(periods[row]), strict=True):
                        cell.setText(text)
                for cell in cells:
                    cell.setVisible(row < len(periods))
        fetched = _fetched_line(view) if look.fetched else None
        show_message(self._fetched, Message(fetched) if fetched else None)
        self._incomplete.setVisible(look.incomplete)


class StatusScreen(QWidget):
    """현황 화면. 설정을 불러오는 중, 처음 설정 전, 설정을 읽지 못함에는 글 하나만 보인다."""

    retry_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName(screen_object_name(ScreenId.STATUS))
        self._coins: tuple[str, ...] = ()
        self._views: dict[str, CoinView] = {}
        self._cards: dict[str, CoinCard] = {}

        layout = QVBoxLayout(self)
        layout.addWidget(title_label(SCREEN_TITLES[ScreenId.STATUS], TITLE_NAME))
        self._message = make_label("", MESSAGE_NAME)
        self._message.hide()
        layout.addWidget(self._message)

        self._content = QWidget()
        self._content.setObjectName(CONTENT_NAME)
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_holder = QWidget()
        self._cards_holder.setObjectName(COINS_NAME)
        self._cards_layout = QHBoxLayout(self._cards_holder)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addWidget(self._cards_holder)
        self._stats = StatsPanel()
        self._stats.retry_requested.connect(self.retry_requested)
        self._stats.chosen.connect(self._render_stats)
        content_layout.addWidget(self._stats)
        layout.addWidget(self._content)
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
                self._cards[market] = card
        for market in self._coins:
            self._cards_layout.removeWidget(self._cards[market])
        for market in self._coins:
            self._cards_layout.addWidget(self._cards[market])

    def _render_stats(self) -> None:
        market = self._stats.chosen_market()
        self._stats.render(self._views.get(market) if market is not None else None)
