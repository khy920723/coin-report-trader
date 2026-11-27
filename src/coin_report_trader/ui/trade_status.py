"""현황 화면의 3단계 자리(M3 명세 3.17.2, 설계서 4, PPT 5-2 판 4의 23~27장).

오늘 요약, 코인 칸의 3단계 줄, 지금 회차, 모의 성적, 오류 멈춤의 줄. 값은 `TodayView`,
`CoinTradeView`, `RoundNowView`, `PerformanceView`, `TradeStatusView`로 그린다.

새 손익과 차이의 값(오늘 실현 손익, 모의 성적의 실현 손익·평가 손익·자산 변화·예측 차이 평균)은
"▲ +1,234원", "▼ -300원"처럼 ▲▼와 부호를 붙이고 `rise`·`fall`로 칠한다. 0은 "0원"·"0.00%"처럼
▲▼ 없이 `text`다. 금액은 원 아래를 버려 쉼표를 붙인다.

객체 이름:

- 오류 멈춤의 줄 `statusErrorPause`(`error`).
- 오늘 요약 카드 `statusToday`: `statusTodayPnl`(오늘 실현 손익의 값), `statusTodayLimit`,
  `statusTodayUsed`, `statusTodayKrw`. 하루 손실 한도에 닿으면 첫 줄이 `error`다.
- 코인 칸의 3단계 줄 `statusCoinTrade_<마켓 코드>`: `statusCoinPrice_`, `statusCoinHolding_`,
  `statusCoinAvg_`, `statusCoinValue_`, `statusCoinStop_`, `statusCoinDust_`,
  `statusCoinLast_`, `statusCoinOrders_`(안의 줄 `statusCoinOrder_<마켓 코드>_<번호>`),
  `statusCoinBlocked_`, `statusCoinSellAll_`, 코인 이름 옆 `statusCoinCaution_`
  (모두 뒤에 `<마켓 코드>`).
- 지금 회차 `statusRoundNow`(글 하나).
- 모의 성적 카드 `statusPerformanceCard`, 표 `statusPerformance`(칸
  `statusPerformanceCell_<줄>_<칸>`), 제목 옆 'ⓘ' `statusPerformanceInfo`, 첫 회차 전의 글
  `statusPerformanceEmpty`.
"""

from decimal import ROUND_DOWN, Decimal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from coin_report_trader.core import (
    ORDER_KIND_TITLES,
    ORDER_SIDE_TITLES,
    CoinTradeView,
    OrderState,
    PerformanceView,
    RoundNowView,
    RoundStage,
    TodayView,
    TradeStatusView,
    format_percent,
    format_price,
)
from coin_report_trader.core.texts import kst_when_text
from coin_report_trader.ui import theme
from coin_report_trader.ui.cards import Card, title_row
from coin_report_trader.ui.info_icon import InfoIcon
from coin_report_trader.ui.texts import (
    AVG_PRICE_NONE,
    CAUTION_LABEL,
    DUST_TEXT,
    ERROR_PAUSE_KINDS,
    HOLDING_NONE,
    LIVE_KRW_LABEL,
    NO_JUDGMENT_YET,
    NO_OPEN_ORDER,
    NO_VALUE,
    PAUSED_ROUNDS_TEXT,
    PERFORMANCE_COLUMNS,
    PERFORMANCE_INFO,
    PERFORMANCE_TITLE,
    PERFORMANCE_TOTAL,
    ROUND_NOW_TITLE,
    TODAY_TITLE,
    UNCONFIRMED_TEXT,
    error_pause_line,
)
from coin_report_trader.ui.widgets import make_label, set_role, set_trend


def won(value: Decimal) -> str:
    """금액: 원 아래를 버려 쉼표를 붙인다(예: `1,234`). 음수는 0 쪽으로 버린다."""
    return f"{int(value.quantize(Decimal(1), rounding=ROUND_DOWN)):,}"


def signed_won(value: Decimal) -> str:
    """새 손익의 금액: 양수는 "▲ +1,234원", 음수는 "▼ -300원", 0은 "0원"."""
    amount = int(value.quantize(Decimal(1), rounding=ROUND_DOWN))
    if amount > 0:
        return f"▲ +{amount:,}원"
    if amount < 0:
        return f"▼ -{-amount:,}원"
    return "0원"


def signed_percent(ratio: Decimal | None) -> str:
    """새 차이의 비율: "▲ +0.21%", "▼ -0.98%", 0은 "0.00%". 값이 없으면 "-"."""
    if ratio is None:
        return NO_VALUE
    text = format_percent(ratio, signed=True)
    if text.startswith("+"):
        return f"▲ {text}"
    if text.startswith("-"):
        return f"▼ {text}"
    return text


def plain_percent(ratio: Decimal | None) -> str:
    return NO_VALUE if ratio is None else format_percent(ratio, signed=False)


def _qty(value: Decimal) -> str:
    return format(value, "f")


def _paint(label: QLabel, color: str | None) -> None:
    label.setStyleSheet(f"color: {color};" if color else "")


def _row(*labels: QLabel) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(theme.ICON_GAP)
    for label in labels:
        row.addWidget(label)
    row.addStretch(1)
    return row


def error_pause_text(status: TradeStatusView) -> str | None:
    """위쪽 글의 오류 멈춤 줄. 오류 멈춤이 아니면 `None`(3.10.2)."""
    if not status.error_paused:
        return None
    cause = status.error_cause
    kind = status.error_detail if status.error_detail else None
    if kind is None:
        kind = ERROR_PAUSE_KINDS.get(cause.value, NO_VALUE) if cause is not None else NO_VALUE
    return error_pause_line(kind, status.uncanceled)


class TodayPanel(Card):
    """오늘 요약(SCR-02-3)."""

    def __init__(self) -> None:
        super().__init__("statusToday")
        self.body.addLayout(title_row(TODAY_TITLE, "statusTodayTitle"))
        self._pnl_name = make_label("오늘 실현 손익", "statusTodayPnlName")
        self._pnl = make_label("", "statusTodayPnl")
        self._limit = make_label("", "statusTodayLimit")
        self.body.addLayout(_row(self._pnl_name, self._pnl, self._limit))
        self._used = make_label("", "statusTodayUsed")
        self.body.addWidget(self._used)
        self._krw = make_label("", "statusTodayKrw")
        self.body.addWidget(self._krw)
        self.hide()

    def render(self, view: TodayView | None, *, live: bool = False) -> None:
        """실거래에서는 "가상 원화" 줄 대신 "원화 {금액}원"(실제 계정의 주문 가능 원화)이다(M5 명세
        3.11). 하루 손실 한도와 예산, 실현 손익은 백엔드가 실거래 값으로 보낸다."""
        self.setVisible(view is not None)
        if view is None:
            return
        self._pnl.setText(signed_won(view.realized_pnl))
        self._limit.setText(f" / 하루 손실 한도 {won(view.daily_loss_limit)}원")
        self._used.setText(
            f"전체 매수 원가와 걸린 매수 주문 {won(view.used)}원 / 예산 {won(view.budget)}원"
        )
        krw_name = LIVE_KRW_LABEL if live else "가상 원화"
        self._krw.setText(f"{krw_name} {won(view.paper_krw)}원")
        reached = view.limit_reached
        # 하루 손실 한도에 닿았으면 첫 줄 전체가 `error`다. 아니면 손익만 오름·내림 색이다.
        for label in (self._pnl_name, self._limit):
            _paint(label, theme.ERROR if reached else None)
        if reached:
            self._pnl.setProperty(theme.TREND_PROPERTY, "")
            _paint(self._pnl, theme.ERROR)
        else:
            _paint(self._pnl, None)
            set_trend(self._pnl, self._pnl.text())


class RoundNowPanel(Card):
    """지금 회차(SCR-02-4). 글 하나(`statusRoundNow`)."""

    def __init__(self) -> None:
        super().__init__("statusRoundNowCard")
        self.body.addLayout(title_row(ROUND_NOW_TITLE, "statusRoundNowTitle"))
        self._text = make_label("", "statusRoundNow")
        self.body.addWidget(self._text)
        self.hide()

    def render(self, view: RoundNowView | None) -> None:
        self.setVisible(view is not None)
        if view is None:
            return
        paused = view.paused
        if paused:
            text = PAUSED_ROUNDS_TEXT
        elif view.stage is not None and view.round_number is not None:
            stage = "주문 정리" if view.stage is RoundStage.CLEANUP else view.stage.value
            text = f"회차 {view.round_number}: {stage}"
        elif view.next_round_utc is not None:
            text = f"다음 회차 {kst_when_text(view.next_round_utc, view.as_of)}"
        else:
            text = f"다음 회차 {NO_VALUE}"
        self._text.setText(text)
        set_role(self._text, theme.ROLE_SUB if paused else "")


class PerformancePanel(Card):
    """모의 성적(SCR-02-8. 3.16.1의 표). 넓은 표는 카드 안에서 가로로 넘긴다."""

    def __init__(self) -> None:
        super().__init__("statusPerformanceCard")
        info = InfoIcon(PERFORMANCE_TITLE, PERFORMANCE_INFO, "statusPerformanceInfo")
        self.body.addLayout(title_row(PERFORMANCE_TITLE, "statusPerformanceTitle", info))
        self._empty = make_label(NO_JUDGMENT_YET, "statusPerformanceEmpty")
        set_role(self._empty, theme.ROLE_SUB)
        self.body.addWidget(self._empty)
        self._table = QWidget()
        self._table.setObjectName("statusPerformance")
        self._grid = QGridLayout(self._table)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(theme.FIELD_GAP)
        self._grid.setVerticalSpacing(0)
        self._cells: list[list[QLabel]] = []
        for column, title in enumerate(PERFORMANCE_COLUMNS):
            head = make_label(title, f"statusPerformanceHeader_{column}")
            set_role(head, theme.ROLE_SUB)
            if column:
                head.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._grid.addWidget(head, 0, column)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("statusPerformanceScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidget(self._table)
        self.body.addWidget(self._scroll)
        self.hide()

    def render(self, view: PerformanceView | None) -> None:
        self.setVisible(view is not None)
        if view is None:
            return
        rows = view.rows
        judged = any(row.ok_rounds > 0 or row.sells > 0 for row in rows)
        self._empty.setVisible(not judged)
        self._scroll.setVisible(judged)
        if not judged:
            return
        while len(self._cells) < len(rows):
            index = len(self._cells)
            cells = []
            for column in range(len(PERFORMANCE_COLUMNS)):
                cell = make_label("", f"statusPerformanceCell_{index}_{column}")
                cell.setProperty("cell", True)
                if column:
                    cell.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                else:
                    cell.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                self._grid.addWidget(cell, index + 1, column)
                cells.append(cell)
            self._cells.append(cells)
        for index, cells in enumerate(self._cells):
            visible = index < len(rows)
            for cell in cells:
                cell.setVisible(visible)
            if not visible:
                continue
            row = rows[index]
            if row.countable:
                hit = f"{format_percent(Decimal(row.hits) / Decimal(row.countable), signed=False)}"
                hit += f" ({row.hits}/{row.countable})"
            else:
                hit = NO_VALUE
            texts = (
                PERFORMANCE_TOTAL if row.market is None else row.market.removeprefix("KRW-"),
                str(row.ok_rounds),
                hit,
                signed_percent(row.diff_mean),
                plain_percent(row.diff_abs_mean),
                signed_won(row.realized_pnl),
                f"{row.wins}/{row.sells}",
                signed_won(row.unrealized_pnl) if row.unrealized_pnl is not None else NO_VALUE,
                signed_percent(row.asset_change) if row.market is None else "",
            )
            for column, (cell, text) in enumerate(zip(cells, texts, strict=True)):
                cell.setText(text)
                bold = row.market is None
                cell.setStyleSheet("font-weight: bold;" if bold else "")
                set_trend(cell, text if column in _TREND_COLUMNS else "")


_TREND_COLUMNS = (3, 5, 7, 8)
"""▲▼를 붙이는 칸(예측 차이 평균, 실현 손익, 평가 손익, 자산 변화)."""


class CoinTradeBox(QWidget):
    """코인 칸의 3단계 줄(SCR-02-1. 설계서 4.3). 보유가 없으면 "보유 없음" 한 줄이다."""

    def __init__(self, market: str) -> None:
        super().__init__()
        self.setObjectName(f"statusCoinTrade_{market}")
        self._market = market
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.LABEL_GAP)
        self.caution = make_label("", f"statusCoinCaution_{market}")
        self.caution.setStyleSheet(f"color: {theme.CAUTION};")
        self.caution.hide()
        self._price = self._line("statusCoinPrice_", layout)
        self._holding = self._line("statusCoinHolding_", layout)
        self._avg = self._line("statusCoinAvg_", layout)
        self._value = self._line("statusCoinValue_", layout)
        self._stop = self._line("statusCoinStop_", layout)
        self._dust = self._line("statusCoinDust_", layout)
        self._last = self._line("statusCoinLast_", layout)
        self._orders = QWidget()
        self._orders.setObjectName(f"statusCoinOrders_{market}")
        self._orders_layout = QVBoxLayout(self._orders)
        self._orders_layout.setContentsMargins(0, 0, 0, 0)
        self._orders_layout.setSpacing(theme.LABEL_GAP)
        layout.addWidget(self._orders)
        self._blocked = self._line("statusCoinBlocked_", layout)
        self._sell_all = self._line("statusCoinSellAll_", layout)
        self.hide()

    def _line(self, prefix: str, layout: QVBoxLayout) -> QLabel:
        label = make_label("", f"{prefix}{self._market}")
        label.hide()
        layout.addWidget(label)
        return label

    @staticmethod
    def _show(label: QLabel, text: str | None, color: str | None = None) -> None:
        label.setText(text or "")
        label.setVisible(bool(text))
        _paint(label, color)

    def render(self, view: CoinTradeView | None) -> None:
        self.setVisible(view is not None)
        if view is None:
            self.caution.setText("")
            self.caution.hide()
            return
        self.caution.setText(CAUTION_LABEL if view.caution else "")
        self.caution.setVisible(view.caution)
        price = f"현재가 {format_price(view.price)}원" if view.price is not None else "현재가 -"
        self._show(self._price, price)
        holding = view.holding
        if holding is not None and holding.qty > 0:
            self._show(
                self._holding,
                f"보유 {_qty(holding.qty)} · 팔 수 있음 {_qty(view.sellable_qty)}",
            )
            avg = holding.avg_price
            if avg is None or avg == 0:
                self._show(self._avg, AVG_PRICE_NONE, theme.ERROR)
            else:
                self._show(self._avg, f"평균 매수가 {format_price(avg)}원")
            value = f"평가 금액 {won(view.value)}원" if view.value is not None else "평가 금액 -"
            self._show(self._value, value)
            stop = f"손절선 {format_price(view.stop_line)}원" if view.stop_line else "손절선 -"
            self._show(self._stop, stop)
            self._show(self._dust, DUST_TEXT if view.dust else None)
            self._holding.setStyleSheet("")
        else:
            self._show(self._holding, HOLDING_NONE, theme.TEXT_SUB)
            for label in (self._avg, self._value, self._stop, self._dust):
                self._show(label, None)
        if view.last_direction is not None and view.last_round is not None:
            last = f"지난 판단: {view.last_direction.value} (회차 {view.last_round})"
        else:
            last = NO_JUDGMENT_YET
        self._show(self._last, last)
        self._fill_orders(view)
        blocked = ", ".join(view.buy_blocked)
        self._show(self._blocked, f"새 매수 막힘: {blocked}" if blocked else None, theme.CAUTION)
        self._show(self._sell_all, view.sell_all, theme.CAUTION)

    def _fill_orders(self, view: CoinTradeView) -> None:
        while self._orders_layout.count():
            item = self._orders_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        orders = view.open_orders
        if not orders:
            self._orders_layout.addWidget(self._order_label(0, NO_OPEN_ORDER, None))
            return
        for index, order in enumerate(orders):
            if order.state is OrderState.UNCONFIRMED:
                label = self._order_label(index, UNCONFIRMED_TEXT, theme.ERROR)
                self._orders_layout.addWidget(label)
                continue
            head = f"{ORDER_KIND_TITLES[order.kind]} {ORDER_SIDE_TITLES[order.side]} 대기"
            price = f" {format_price(order.price)}원" if order.price is not None else ""
            text = f"{head}{price} × {_qty(order.qty)}"
            self._orders_layout.addWidget(self._order_label(index, text, None))

    def _order_label(self, index: int, text: str, color: str | None) -> QLabel:
        label = make_label(text, f"statusCoinOrder_{self._market}_{index}")
        _paint(label, color)
        return label
