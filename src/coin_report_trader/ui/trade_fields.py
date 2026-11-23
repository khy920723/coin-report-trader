"""처음 설정과 설정의 3단계 칸(M3 명세 3.11, 3.17.5. PPT 5-1 판 4의 25~29장, 5-4 판 4의 22~25장).

처음 설정(`required=True`. 이름 앞머리 `firstSetup`)과 설정(`required=False`. `settings`)이 같이
쓴다.
카드 넷: 예산과 투자 비율, 매수 비율, 손절과 한도, 그 밖의 값. 객체 이름(`<앞>`은 앞머리):

- 예산 `<앞>Budget`, 코인마다 투자 비율 `<앞>CoinRatio_<마켓 코드>`와 "코인 예산 {금액}원"
  `<앞>CoinBudget_<마켓 코드>`, "합계 {합}%" `<앞>CoinRatioSum`, 제목 옆 'ⓘ' `<앞>BudgetInfo`.
- 매수 비율: `<앞>FirstRatio`(추세추종), `<앞>MrFirstRatio`(평균회귀), `<앞>AddRatio`,
  `<앞>Tolerance`(허용 범위), 코인마다 "첫 매수 금액 {금액}원, 추가 한 번 {금액}원"
  `<앞>CoinBuy_<마켓 코드>`, 제목 옆 'ⓘ' `<앞>RatioInfo`.
- 손절과 한도: `<앞>StopRatio`, `<앞>DailyLoss`, `<앞>OrderInterval`. 처음 설정에는 제목 옆 'ⓘ'
  `firstSetupLimitsInfo`가 있다(설정에는 없다).
- 그 밖의 값: 둥근 단추 `<앞>Method_<값>`, `<앞>PauseStop_<값>`, `<앞>MrSell_yes`와 `<앞>MrSell_no`,
  입력 칸 `<앞>MrLoss`, `<앞>PaperFunds`, `<앞>BackupDays`.
- 칸마다 이름 라벨 `<칸 이름>Label`과 칸 아래 글 `<칸 이름>Error`(걸렸을 때만 보임). 합의 글은
  `<앞>CoinRatioSumError`(키 `coin_ratios`). 카드 제목 `<앞>BudgetTitle`, `<앞>RatioTitle`,
  `<앞>LimitsTitle`, `<앞>OtherTitle`.

화면 쪽 검사(3.11.2): 꼭 넣는 칸이 비었는지와 꼴(수, 정수, 소수 둘째 자리)을 본다. 범위, 하한, 합은
자료 스레드가 본다(`show_errors`가 그 글을 칸 아래에 보인다).
"""

import re
from collections.abc import Callable, Mapping
from decimal import ROUND_FLOOR, Decimal

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from coin_report_trader.core import (
    PAUSE_STOP_LOSS_TITLES,
    TRADE_METHOD_TITLES,
    PauseStopLoss,
    TradeMethod,
    TradeSettings,
    coin_label,
)
from coin_report_trader.ui import theme
from coin_report_trader.ui.cards import Card, title_row
from coin_report_trader.ui.info_icon import InfoIcon, info_text
from coin_report_trader.ui.state import Tone
from coin_report_trader.ui.texts import (
    ADD_RATIO_LABEL,
    BACKUP_DAYS_LABEL,
    BUDGET_INFO,
    BUDGET_LABEL,
    BUDGET_TITLE,
    COIN_RATIO_LABEL,
    DAILY_LOSS_LABEL,
    DEFAULT_DAILY_LOSS,
    DEFAULT_ORDER_INTERVAL,
    DEFAULT_STOP_RATIO,
    FIELD_INTEGER,
    FIELD_NOT_NUMBER,
    FIELD_REQUIRED,
    FIELD_TWO_PLACES,
    FIRST_RATIO_LABEL,
    LIMITS_INFO,
    LIMITS_TITLE,
    METHOD_LABEL,
    METHOD_TIPS,
    MR_FIRST_RATIO_LABEL,
    MR_LOSS_LABEL,
    MR_SELL_LABEL,
    NO_TEXT,
    ORDER_INTERVAL_LABEL,
    OTHER_TITLE,
    PAPER_FUNDS_LABEL,
    PAUSE_STOP_LABEL,
    RATIO_INFO,
    RATIO_TITLE,
    STOP_RATIO_LABEL,
    TOLERANCE_LABEL,
    YES_TEXT,
    buy_amounts_text,
    coin_budget_text,
    ratio_sum_text,
)
from coin_report_trader.ui.widgets import LOCK_PROPERTY, make_label, set_role

_NUMBER = re.compile(r"-?\d+(\.\d+)?")
_ZERO = Decimal(0)
_HUNDRED = Decimal(100)
_CENT = Decimal("0.01")


def money_text(value: Decimal) -> str:
    """원 아래를 버려 쉼표를 붙인다(3.17.5)."""
    return f"{int(value.to_integral_value(rounding=ROUND_FLOOR)):,}"


def percent_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _plain(text: str) -> str:
    return text.strip().replace(",", "").replace(" ", "")


def parse_integer(text: str) -> tuple[int | None, str | None]:
    """정수 글. (값, 걸린 글). 비면 (None, 필수 글)."""
    plain = _plain(text)
    if not plain:
        return None, FIELD_REQUIRED
    if not _NUMBER.fullmatch(plain):
        return None, FIELD_NOT_NUMBER
    if "." in plain:
        return None, FIELD_INTEGER
    return int(plain), None


def parse_percent(text: str) -> tuple[Decimal | None, str | None]:
    """소수 둘째 자리까지의 수 글. 쉼표는 받지 않는다."""
    plain = text.strip()
    if not plain:
        return None, FIELD_REQUIRED
    if not _NUMBER.fullmatch(plain):
        return None, FIELD_NOT_NUMBER
    value = Decimal(plain)
    if value != value.quantize(_CENT):
        return None, FIELD_TWO_PLACES
    return value, None


class NumberField(QWidget):
    """이름 라벨, 입력 칸, 칸 아래 글(걸렸을 때만 보임)을 세로로 쌓은 칸."""

    changed = Signal()

    def __init__(self, name: str, label: str, *, money: bool = False) -> None:
        super().__init__()
        self._base = label
        self._required = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.LABEL_GAP)
        self.label = make_label(label, f"{name}Label")
        set_role(self.label, theme.ROLE_SUB)
        layout.addWidget(self.label)
        self.edit = QLineEdit()
        self.edit.setObjectName(name)
        if money:
            self.edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.edit.textChanged.connect(self._on_edited)  # 코드가 `setText`한 때도 같다
        layout.addWidget(self.edit)
        self.error = make_label("", f"{name}Error", Tone.WARNING)
        self.error.hide()
        layout.addWidget(self.error)

    def set_label(self, label: str, *, required: bool) -> None:
        self._base = label
        self._required = required
        self._show_label()

    def set_required(self, required: bool) -> None:
        self._required = required
        self._show_label()

    def _show_label(self) -> None:
        text = self._base
        if self._required:
            # 별표는 이름 바로 뒤, 단위 앞이다("예산*(원)").
            head, paren, tail = text.partition("(")
            text = f"{head}*{paren}{tail}"
        self.label.setText(text)

    def text(self) -> str:
        return self.edit.text()

    def is_empty(self) -> bool:
        return not self.edit.text().strip()

    def set_text(self, text: str) -> None:
        """글을 바꾼다(`changed`를 내지 않는다). 걸린 글은 걷는다."""
        self.edit.blockSignals(True)
        self.edit.setText(text)
        self.edit.blockSignals(False)
        self.set_error(None)

    def set_error(self, text: str | None) -> None:
        """칸 아래 글을 보이고 칸 테두리를 `error` 색으로 한다. `None`이면 걷는다."""
        self.error.setText(text or "")
        self.error.setVisible(bool(text))
        self.edit.setProperty("invalid", bool(text))
        self.edit.setStyleSheet(f"QLineEdit {{ border: 1px solid {theme.ERROR}; }}" if text else "")

    def _on_edited(self, _text: str) -> None:
        self.set_error(None)
        self.changed.emit()


class _Choice(QWidget):
    """둥근 단추 묶음(처음에는 아무것도 골라져 있지 않다)."""

    changed = Signal()

    def __init__(self, label: str, items: list[tuple[str, str, str]], tips: Mapping[str, str]):
        """`items`는 (값, 단추 글, 객체 이름)."""
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.LABEL_GAP)
        self.label = make_label(label, f"{items[0][2].rsplit('_', 1)[0]}Label")
        set_role(self.label, theme.ROLE_SUB)
        layout.addWidget(self.label)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self._group = QButtonGroup(self)
        self.buttons: dict[str, QRadioButton] = {}
        for value, text, name in items:
            button = QRadioButton(text)
            button.setObjectName(name)
            tip = tips.get(value)
            if tip:
                button.setToolTip(tip)
                button.setAccessibleDescription(tip)  # 화면 읽기 도구의 설명에도(REQ-USE-002)
            button.toggled.connect(self.changed)  # 코드가 고른 때도 같다(`set_value`는 막는다)
            self._group.addButton(button)
            self.buttons[value] = button
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        self._base = label

    def set_required(self, required: bool) -> None:
        self.label.setText(f"{self._base}*" if required else self._base)

    def value(self) -> str | None:
        for value, button in self.buttons.items():
            if button.isChecked():
                return value
        return None

    def set_value(self, value: str | None) -> None:
        """고른 값을 바꾼다(`changed`를 내지 않는다)."""
        self.blockSignals(True)
        for button in self.buttons.values():
            button.blockSignals(True)
        self._group.setExclusive(False)
        for key, button in self.buttons.items():
            button.setChecked(key == value)
        self._group.setExclusive(True)
        for button in self.buttons.values():
            button.blockSignals(False)
        self.blockSignals(False)


class TradeFields(QObject):
    """3단계 칸의 위젯과 값. 사용자가 고치면 `changed`가 난다(`set_values`는 내지 않는다)."""

    changed = Signal()
    method_chosen = Signal()

    def __init__(self, prefix: str, *, required: bool) -> None:
        super().__init__()
        self._prefix = prefix
        self._required = required
        self._coins: tuple[str, ...] = ()
        self._ratio_text: dict[str, str] = {}
        self._ratio_fields: dict[str, NumberField] = {}
        self._budget_labels: dict[str, QLabel] = {}
        self._buy_labels: dict[str, QLabel] = {}
        self._number_fields: list[NumberField] = []

        def number(name: str, label: str, *, money: bool = False) -> NumberField:
            field = NumberField(f"{prefix}{name}", label, money=money)
            field.changed.connect(self._on_edit)
            self._number_fields.append(field)
            return field

        self.budget = number("Budget", BUDGET_LABEL, money=True)
        self.first_ratio = number("FirstRatio", FIRST_RATIO_LABEL)
        self.mr_first_ratio = number("MrFirstRatio", MR_FIRST_RATIO_LABEL)
        self.add_ratio = number("AddRatio", ADD_RATIO_LABEL)
        self.tolerance = number("Tolerance", TOLERANCE_LABEL)
        self.stop_ratio = number("StopRatio", STOP_RATIO_LABEL)
        self.daily_loss = number("DailyLoss", DAILY_LOSS_LABEL, money=True)
        self.order_interval = number("OrderInterval", ORDER_INTERVAL_LABEL)
        self.mr_loss = number("MrLoss", MR_LOSS_LABEL)
        self.paper_funds = number("PaperFunds", PAPER_FUNDS_LABEL, money=True)
        self.backup_days = number("BackupDays", BACKUP_DAYS_LABEL)
        self._method = _Choice(
            METHOD_LABEL,
            [
                (m.value, TRADE_METHOD_TITLES[m], f"{prefix}Method_{m.value}")
                for m in (TradeMethod.MEAN_REVERSION, TradeMethod.TREND)
            ],
            METHOD_TIPS,
        )
        self._pause = _Choice(
            PAUSE_STOP_LABEL,
            [
                (p.value, PAUSE_STOP_LOSS_TITLES[p], f"{prefix}PauseStop_{p.value}")
                for p in PauseStopLoss
            ],
            {},
        )
        self._mr_sell = _Choice(
            MR_SELL_LABEL,
            [("yes", YES_TEXT, f"{prefix}MrSell_yes"), ("no", NO_TEXT, f"{prefix}MrSell_no")],
            {},
        )
        for choice in (self._method, self._pause, self._mr_sell):
            choice.changed.connect(self._on_edit)
        self._method.changed.connect(self._on_method)

        self._ratio_box = QWidget()
        self._ratio_box.setObjectName(f"{prefix}CoinRatios")
        self._ratio_layout = QVBoxLayout(self._ratio_box)
        self._ratio_layout.setContentsMargins(0, 0, 0, 0)
        self._ratio_layout.setSpacing(theme.FIELD_GAP)
        self._sum_label = make_label(ratio_sum_text("0.00"), f"{prefix}CoinRatioSum")
        self._sum_error = make_label("", f"{prefix}CoinRatioSumError", Tone.WARNING)
        self._sum_error.hide()
        self._buy_box = QWidget()
        self._buy_box.setObjectName(f"{prefix}CoinBuys")
        self._buy_layout = QVBoxLayout(self._buy_box)
        self._buy_layout.setContentsMargins(0, 0, 0, 0)
        self._buy_layout.setSpacing(theme.LABEL_GAP)

        self.reset_defaults()
        self._refresh_labels()
        self._recompute()

    # --- 카드 ---

    def cards(self, *other_extra: QWidget) -> list[Card]:
        """카드 넷(예산과 투자 비율, 매수 비율, 손절과 한도, 그 밖의 값). `other_extra`는 그 밖의 값
        카드 아래에 붙는 위젯들(알림 보관 기간 같은 것)이다."""
        prefix = self._prefix
        budget = Card()
        info = InfoIcon(BUDGET_TITLE, info_text(*BUDGET_INFO), f"{prefix}BudgetInfo")
        budget.body.addLayout(title_row(BUDGET_TITLE, f"{prefix}BudgetTitle", info))
        budget.body.addWidget(self.budget)
        budget.body.addWidget(self._ratio_box)
        budget.body.addWidget(self._sum_label)
        budget.body.addWidget(self._sum_error)
        budget.body.addStretch(1)

        ratio = Card()
        info = InfoIcon(RATIO_TITLE, info_text(*RATIO_INFO), f"{prefix}RatioInfo")
        ratio.body.addLayout(title_row(RATIO_TITLE, f"{prefix}RatioTitle", info))
        for field in (self.first_ratio, self.mr_first_ratio, self.add_ratio, self.tolerance):
            ratio.body.addWidget(field)
        ratio.body.addWidget(self._buy_box)
        ratio.body.addStretch(1)

        limits = Card()
        info = (
            InfoIcon(LIMITS_TITLE, LIMITS_INFO, f"{prefix}LimitsInfo") if self._required else None
        )
        limits.body.addLayout(title_row(LIMITS_TITLE, f"{prefix}LimitsTitle", info))
        for field in (self.stop_ratio, self.daily_loss, self.order_interval):
            limits.body.addWidget(field)
        limits.body.addStretch(1)

        other = Card()
        other.body.addLayout(title_row(OTHER_TITLE, f"{prefix}OtherTitle"))
        for widget in (
            self._method,
            self._pause,
            self._mr_sell,
            self.mr_loss,
            self.paper_funds,
            self.backup_days,
            *other_extra,
        ):
            other.body.addWidget(widget)
        other.body.addStretch(1)
        return [budget, ratio, limits, other]

    # --- 값 ---

    def reset_defaults(self) -> None:
        """비운 채로 두되 손절과 한도의 처음 값(5, 3,000, 60)을 채운다."""
        self.set_values(None)
        self.stop_ratio.set_text(DEFAULT_STOP_RATIO)
        self.daily_loss.set_text(DEFAULT_DAILY_LOSS)
        self.order_interval.set_text(DEFAULT_ORDER_INTERVAL)
        self._recompute()

    def set_values(self, trade: TradeSettings | None) -> None:
        """저장된 값을 채운다(`None`이면 모두 비운다). `changed`를 내지 않는다."""
        for field in self._number_fields:
            field.set_text("")
        for field in self._ratio_fields.values():
            field.set_text("")
        self._ratio_text = {}
        self._method.set_value(None)
        self._pause.set_value(None)
        self._mr_sell.set_value(None)
        self.clear_errors()
        if trade is not None:
            self.budget.set_text(money_text(trade.budget))
            self._ratio_text = {m: percent_text(r) for m, r in trade.coin_ratios.items()}
            for market, field in self._ratio_fields.items():
                field.set_text(self._ratio_text.get(market, ""))
            if trade.first_ratio is not None:
                self.first_ratio.set_text(percent_text(trade.first_ratio))
            if trade.mr_first_ratio is not None:
                self.mr_first_ratio.set_text(percent_text(trade.mr_first_ratio))
            self.add_ratio.set_text(percent_text(trade.add_ratio))
            self.tolerance.set_text(percent_text(trade.tolerance))
            self.stop_ratio.set_text(percent_text(trade.stop_ratio))
            self.daily_loss.set_text(money_text(trade.daily_loss_limit))
            self.order_interval.set_text(str(trade.order_interval_s))
            self._method.set_value(trade.method.value)
            self._pause.set_value(trade.pause_stop_loss.value)
            self._mr_sell.set_value("yes" if trade.mr_sell_unknown_fee else "no")
            self.mr_loss.set_text(percent_text(trade.mr_tolerable_loss))
            self.paper_funds.set_text(money_text(trade.paper_funds))
            self.backup_days.set_text(str(trade.backup_days))
        self._refresh_labels()
        self._recompute()

    def set_coins(self, coins: tuple[str, ...]) -> None:
        """고른 코인마다 투자 비율 줄과 계산된 글 줄을 만든다(넣은 값은 코인이 남으면 그대로)."""
        if coins == self._coins:
            return
        for market, field in self._ratio_fields.items():
            self._ratio_text[market] = field.text()
        # 목록에서 뺀 코인의 값은 버린다(다시 고르면 빈 칸이다).
        self._ratio_text = {m: t for m, t in self._ratio_text.items() if m in coins}
        self._coins = coins
        for layout in (self._ratio_layout, self._buy_layout):
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.hide()
                    widget.setParent(None)
                    widget.deleteLater()
        self._ratio_fields = {}
        self._budget_labels = {}
        self._buy_labels = {}
        prefix = self._prefix
        for market in coins:
            field = NumberField(
                f"{prefix}CoinRatio_{market}", COIN_RATIO_LABEL.format(coin=coin_label(market))
            )
            field.set_text(self._ratio_text.get(market, ""))
            field.changed.connect(self._on_edit)
            self._ratio_fields[market] = field
            budget_label = make_label(coin_budget_text(None), f"{prefix}CoinBudget_{market}")
            set_role(budget_label, theme.ROLE_SUB)
            self._budget_labels[market] = budget_label
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(theme.FIELD_GAP)
            row_layout.addWidget(field, 1)
            row_layout.addWidget(budget_label)
            self._ratio_layout.addWidget(row)
            buy_label = make_label(buy_amounts_text(None, None), f"{prefix}CoinBuy_{market}")
            set_role(buy_label, theme.ROLE_SUB)
            self._buy_labels[market] = buy_label
            buy_row = QWidget()
            buy_layout = QHBoxLayout(buy_row)
            buy_layout.setContentsMargins(0, 0, 0, 0)
            buy_layout.setSpacing(theme.FIELD_GAP)
            buy_layout.addWidget(make_label(coin_label(market), f"{prefix}CoinBuyName_{market}"))
            buy_layout.addWidget(buy_label, 1)
            self._buy_layout.addWidget(buy_row)
        self._refresh_labels()
        self._recompute()

    def set_funds_locked(self, locked: bool) -> None:
        """가상 자금 칸을 흐리게 하고 고칠 수 없게 한다(M3 명세 3.7.1). `set_dim`도 못 켠다."""
        self.paper_funds.setProperty(LOCK_PROPERTY, locked)
        self.paper_funds.setEnabled(not locked)

    def set_required_marks(self, required: bool) -> None:
        """별표를 보일지(처음 설정). 설정에서는 늘 끈다."""
        self._required = required
        self._refresh_labels()

    # --- 검사 ---

    def is_complete(self) -> bool:
        """꼭 넣는 칸이 모두 차 있는지(꼴은 보지 않는다)."""
        fields = [self.budget, self.add_ratio, self.tolerance, self.stop_ratio, self.daily_loss]
        fields += [self.order_interval, self.mr_loss, self.paper_funds, self.backup_days]
        fields += list(self._ratio_fields.values())
        method = self._method.value()
        if method is None or self._pause.value() is None or self._mr_sell.value() is None:
            return False
        fields.append(
            self.first_ratio if method == TradeMethod.TREND.value else self.mr_first_ratio
        )
        return bool(self._coins) and not any(f.is_empty() for f in fields)

    def trade(self) -> TradeSettings | None:
        """칸을 읽어 `TradeSettings`를 만든다. 꼭 넣는 칸이 비었거나 꼴이 틀리면 그 칸 아래에 글을
        보이고 `None`을 돌려준다(저장을 부르지 않는다)."""
        self.clear_errors()
        ok = True

        def read(field: NumberField, parse, *, required: bool = True):
            nonlocal ok
            value, error = parse(field.text())
            if error == FIELD_REQUIRED and not required:
                return None
            if error is not None:
                field.set_error(error)
                ok = False
            return value

        budget = read(self.budget, lambda t: _wrap(parse_integer(t)))
        ratios: dict[str, Decimal] = {}
        for market, field in self._ratio_fields.items():
            value = read(field, parse_percent)
            if value is not None:
                ratios[market] = value
        method_value = self._method.value()
        trend = method_value == TradeMethod.TREND.value
        mean = method_value == TradeMethod.MEAN_REVERSION.value
        first = read(self.first_ratio, parse_percent, required=trend)
        mr_first = read(self.mr_first_ratio, parse_percent, required=mean)
        add = read(self.add_ratio, parse_percent)
        tolerance = read(self.tolerance, parse_percent)
        stop = read(self.stop_ratio, parse_percent)
        daily = read(self.daily_loss, lambda t: _wrap(parse_integer(t)))
        interval = read(self.order_interval, parse_integer)
        loss = read(self.mr_loss, parse_percent)
        funds = read(self.paper_funds, lambda t: _wrap(parse_integer(t)))
        days = read(self.backup_days, parse_integer)
        pause_value = self._pause.value()
        sell_value = self._mr_sell.value()
        if method_value is None or pause_value is None or sell_value is None:
            ok = False
        if not ok or not self._coins or len(ratios) != len(self._coins):
            return None
        required_values = (budget, add, tolerance, stop, daily, interval, loss, funds, days)
        if any(value is None for value in required_values) or method_value is None:
            return None
        return TradeSettings(
            budget=budget,
            coin_ratios=ratios,
            first_ratio=first,
            mr_first_ratio=mr_first,
            add_ratio=add,
            tolerance=tolerance,
            stop_ratio=stop,
            daily_loss_limit=daily,
            order_interval_s=interval,
            method=TradeMethod(method_value),
            pause_stop_loss=PauseStopLoss(pause_value),
            mr_sell_unknown_fee=sell_value == "yes",
            mr_tolerable_loss=loss,
            paper_funds=funds,
            backup_days=days,
        )

    # --- 칸 아래 글 ---

    def first_error_widget(self) -> QWidget | None:
        """칸 아래 글이 보이는 칸 가운데 맨 위의 것(카드 차례대로). 없으면 `None`."""
        head = [self.budget, *self._ratio_fields.values()]
        tail = [self.first_ratio, self.mr_first_ratio, self.add_ratio, self.tolerance]
        tail += [self.stop_ratio, self.daily_loss, self.order_interval, self.mr_loss]
        tail += [self.paper_funds, self.backup_days]
        for field in head:
            if not field.error.isHidden():
                return field
        if not self._sum_error.isHidden():  # 합의 글은 투자 비율 줄 바로 아래다
            return self._sum_error
        for field in tail:
            if not field.error.isHidden():
                return field
        return None

    def clear_errors(self) -> None:
        for field in (*self._number_fields, *self._ratio_fields.values()):
            field.set_error(None)
        self._sum_error.setText("")
        self._sum_error.hide()

    def show_errors(self, errors: Mapping[str, str]) -> None:
        """저장 뒤 검사에 걸린 칸의 글(`SaveResult.field_errors`)을 칸 아래에 보인다."""
        self.clear_errors()
        by_key: dict[str, NumberField] = {
            "budget": self.budget,
            "first_ratio": self.first_ratio,
            "mr_first_ratio": self.mr_first_ratio,
            "add_ratio": self.add_ratio,
            "tolerance": self.tolerance,
            "stop_ratio": self.stop_ratio,
            "daily_loss_limit": self.daily_loss,
            "order_interval_s": self.order_interval,
            "mr_tolerable_loss": self.mr_loss,
            "paper_funds": self.paper_funds,
            "backup_days": self.backup_days,
        }
        for market, field in self._ratio_fields.items():
            by_key[f"coin_ratio:{market}"] = field
        for key, text in errors.items():
            if key == "coin_ratios":
                self._sum_error.setText(text)
                self._sum_error.show()
            elif key in by_key:
                by_key[key].set_error(text)

    # --- 안쪽 ---

    def _on_edit(self) -> None:
        self._refresh_labels()
        self._recompute()
        self.changed.emit()

    def _on_method(self) -> None:
        """방식을 고르면 새 방식의 첫 매수 비율 칸이 비었을 때 그 칸 아래에 글이 보인다(3.11.1)."""
        for field in (self.first_ratio, self.mr_first_ratio):
            field.set_error(None)
        value = self._method.value()
        chosen = self.first_ratio if value == TradeMethod.TREND.value else self.mr_first_ratio
        if value is not None and chosen.is_empty():
            chosen.set_error(FIELD_REQUIRED)
        self.method_chosen.emit()

    def _refresh_labels(self) -> None:
        required = self._required
        method = self._method.value()
        for field in (
            self.budget,
            self.add_ratio,
            self.tolerance,
            self.stop_ratio,
            self.daily_loss,
            self.order_interval,
            self.mr_loss,
            self.paper_funds,
            self.backup_days,
            *self._ratio_fields.values(),
        ):
            field.set_required(required)
        # 첫 매수 비율 둘 가운데서는 고른 방식의 칸에만 별표가 붙는다.
        self.first_ratio.set_required(required and method == TradeMethod.TREND.value)
        self.mr_first_ratio.set_required(required and method == TradeMethod.MEAN_REVERSION.value)
        for choice in (self._method, self._pause, self._mr_sell):
            choice.set_required(required)

    def _recompute(self) -> None:
        """코인 예산, 합계, 첫 매수 금액을 칸에서 다시 셈한다."""
        budget = _quiet(self.budget.text(), parse_integer)
        add = _quiet(self.add_ratio.text(), parse_percent)
        method = self._method.value()
        first_field = {
            TradeMethod.TREND.value: self.first_ratio,
            TradeMethod.MEAN_REVERSION.value: self.mr_first_ratio,
        }.get(method or "")
        first = _quiet(first_field.text(), parse_percent) if first_field is not None else None
        total = _ZERO
        for market, field in self._ratio_fields.items():
            ratio = _quiet(field.text(), parse_percent)
            if ratio is not None:
                total += ratio
            coin_budget = None
            if budget is not None and ratio is not None:
                coin_budget = Decimal(budget) * ratio / _HUNDRED
            self._budget_labels[market].setText(
                coin_budget_text(money_text(coin_budget) if coin_budget is not None else None)
            )
            self._buy_labels[market].setText(
                buy_amounts_text(
                    _amount(coin_budget, first),
                    _amount(coin_budget, add),
                )
            )
        self._sum_label.setText(ratio_sum_text(format(total.quantize(_CENT), "f")))


def _quiet(text: str, parse: Callable[[str], tuple[object, str | None]]):
    value, error = parse(text)
    return value if error is None else None


def _amount(coin_budget: Decimal | None, ratio: Decimal | None) -> str | None:
    if coin_budget is None or ratio is None:
        return None
    return money_text(coin_budget * ratio / _HUNDRED)


def _wrap(result: tuple[int | None, str | None]) -> tuple[Decimal | None, str | None]:
    value, error = result
    return (Decimal(value) if value is not None else None), error
