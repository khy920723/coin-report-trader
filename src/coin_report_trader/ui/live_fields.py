"""설정의 '실거래 설정' 묶음(`settingsLive`. M5 명세 3.7.5, 3.11).

실거래에서만 쓰는 예산, 코인별 투자 비율(합 100%), 하루 손실 한도다. 모의매매의 값과 따로 넣고
따로 저장한다. 처음 칸은 셋 모두 비어 있고 별표가 없다(모의매매에는 꼭 넣지 않는다).

객체 이름: 카드 `settingsLive`, 제목 옆 'ⓘ' `settingsLiveInfo`, 칸을 담는 `settingsLiveFields`,
예산 `settingsLiveBudget`, 코인마다 `settingsLiveCoinRatio_<마켓 코드>`와 "코인 예산 {금액}원"
`settingsLiveCoinBudget_<마켓 코드>`, "합계 {합}%" `settingsLiveCoinRatioSum`, 하루 손실 한도
`settingsLiveDailyLoss`. 칸 아래 글은 `<칸 이름>Error`(합의 글은 `settingsLiveCoinRatioSumError`),
실거래 중 곁의 글은 `settingsLiveLockNote`.

화면 쪽 검사는 꼴(수, 정수, 소수 둘째 자리)만 본다. 0 이하, 합, 하한은 자료 스레드가 보고
(`show_errors`가 그 글을 칸 아래에 보인다) 키는 `live_budget`, `live_coin_ratio:<마켓 코드>`,
`live_coin_ratios`, `live_daily_loss_limit`이다.
"""

from collections.abc import Mapping
from decimal import Decimal

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from coin_report_trader.core import LiveSettings, coin_label
from coin_report_trader.ui import theme
from coin_report_trader.ui.cards import Card, title_row
from coin_report_trader.ui.info_icon import InfoIcon, info_text
from coin_report_trader.ui.state import Tone
from coin_report_trader.ui.texts import (
    LIVE_BUDGET_LABEL,
    LIVE_COIN_RATIO_LABEL,
    LIVE_DAILY_LOSS_LABEL,
    LIVE_INFO,
    LIVE_LOCKED_NOTE,
    LIVE_TITLE,
    coin_budget_text,
    ratio_sum_text,
)
from coin_report_trader.ui.trade_fields import (
    NumberField,
    money_text,
    parse_integer,
    parse_percent,
    percent_text,
)
from coin_report_trader.ui.widgets import LOCK_PROPERTY, make_label, set_role

PREFIX = "settingsLive"
_HUNDRED = Decimal(100)
_CENT = Decimal("0.01")


class LiveFields(QObject):
    """실거래 설정의 위젯과 값. 사용자가 고치면 `changed`가 난다(`set_values`는 내지 않는다)."""

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._coins: tuple[str, ...] = ()
        self._ratio_text: dict[str, str] = {}
        self._ratio_fields: dict[str, NumberField] = {}
        self._budget_labels: dict[str, QLabel] = {}
        self.budget = NumberField(f"{PREFIX}Budget", LIVE_BUDGET_LABEL, money=True)
        self.daily_loss = NumberField(f"{PREFIX}DailyLoss", LIVE_DAILY_LOSS_LABEL, money=True)
        for field in (self.budget, self.daily_loss):
            field.changed.connect(self._on_edit)
        self._ratio_box = QWidget()
        self._ratio_box.setObjectName(f"{PREFIX}CoinRatios")
        self._ratio_layout = QVBoxLayout(self._ratio_box)
        self._ratio_layout.setContentsMargins(0, 0, 0, 0)
        self._ratio_layout.setSpacing(theme.FIELD_GAP)
        self._sum_label = make_label(ratio_sum_text("0.00"), f"{PREFIX}CoinRatioSum")
        self._sum_error = make_label("", f"{PREFIX}CoinRatioSumError", Tone.WARNING)
        self._sum_error.hide()
        self._fields_box = QWidget()
        self._fields_box.setObjectName(f"{PREFIX}Fields")
        box = QVBoxLayout(self._fields_box)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.FIELD_GAP)
        for widget in (
            self.budget,
            self._ratio_box,
            self._sum_label,
            self._sum_error,
            self.daily_loss,
        ):
            box.addWidget(widget)
        self._note = make_label(LIVE_LOCKED_NOTE, f"{PREFIX}LockNote")
        set_role(self._note, theme.ROLE_SUB)
        self._note.hide()
        self._recompute()

    # --- 카드 ---

    def card(self) -> Card:
        card = Card(PREFIX)
        info = InfoIcon(LIVE_TITLE, info_text(*LIVE_INFO), f"{PREFIX}Info")
        card.body.addLayout(title_row(LIVE_TITLE, f"{PREFIX}Title", info))
        card.body.addWidget(self._fields_box)
        card.body.addWidget(self._note)
        card.body.addStretch(1)
        return card

    # --- 값 ---

    def set_values(self, live: LiveSettings | None) -> None:
        """저장된 값을 채운다(`None`이면 모두 비운다). `changed`를 내지 않는다."""
        self.budget.set_text("")
        self.daily_loss.set_text("")
        for field in self._ratio_fields.values():
            field.set_text("")
        self._ratio_text = {}
        self.clear_errors()
        if live is not None:
            if live.budget is not None:
                self.budget.set_text(money_text(live.budget))
            if live.daily_loss_limit is not None:
                self.daily_loss.set_text(money_text(live.daily_loss_limit))
            self._ratio_text = {m: percent_text(r) for m, r in live.coin_ratios.items()}
            for market, field in self._ratio_fields.items():
                field.set_text(self._ratio_text.get(market, ""))
        self._recompute()

    def set_coins(self, coins: tuple[str, ...]) -> None:
        """고른 코인마다 투자 비율 줄을 만든다(넣은 값은 코인이 남으면 그대로)."""
        if coins == self._coins:
            return
        for market, field in self._ratio_fields.items():
            self._ratio_text[market] = field.text()
        self._ratio_text = {m: t for m, t in self._ratio_text.items() if m in coins}
        self._coins = coins
        while self._ratio_layout.count():
            item = self._ratio_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._ratio_fields = {}
        self._budget_labels = {}
        for market in coins:
            field = NumberField(
                f"{PREFIX}CoinRatio_{market}", LIVE_COIN_RATIO_LABEL.format(coin=coin_label(market))
            )
            field.set_text(self._ratio_text.get(market, ""))
            field.changed.connect(self._on_edit)
            self._ratio_fields[market] = field
            budget_label = make_label(coin_budget_text(None), f"{PREFIX}CoinBudget_{market}")
            set_role(budget_label, theme.ROLE_SUB)
            self._budget_labels[market] = budget_label
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(theme.FIELD_GAP)
            row_layout.addWidget(field, 1)
            row_layout.addWidget(budget_label)
            self._ratio_layout.addWidget(row)
        self._recompute()

    def set_locked(self, locked: bool, *, usable: bool = True) -> None:
        """실거래 중에는 칸이 흐리고 곁에 "실거래를 끈 뒤에 고칠 수 있습니다."가 보인다(3.7.4).
        `set_dim`이 다시 켜지 못한다. `usable`은 칸을 쓸 수 있는 때다."""
        self._fields_box.setProperty(LOCK_PROPERTY, locked)
        self._fields_box.setEnabled(usable and not locked)
        self._note.setVisible(locked)

    def is_locked(self) -> bool:
        return bool(self._fields_box.property(LOCK_PROPERTY))

    def is_empty(self) -> bool:
        """셋 모두 비었는지."""
        fields = (self.budget, self.daily_loss, *self._ratio_fields.values())
        return all(field.is_empty() for field in fields)

    def live(self) -> tuple[bool, LiveSettings | None]:
        """칸을 읽는다. (꼴이 맞는가, 값). 꼴이 틀린 칸은 그 칸 아래에 글이 보이고 `False`다. 칸이
        모두 비었으면 (`True`, `None`)이다(저장된 값을 그대로 둔다). 채운 칸만 값에 든다."""
        self.clear_errors()
        if self.is_empty():
            return True, None
        ok = True
        budget = None
        if not self.budget.is_empty():
            value, error = parse_integer(self.budget.text())
            if error is not None:
                self.budget.set_error(error)
                ok = False
            elif value is not None:
                budget = Decimal(value)
        daily = None
        if not self.daily_loss.is_empty():
            value, error = parse_integer(self.daily_loss.text())
            if error is not None:
                self.daily_loss.set_error(error)
                ok = False
            elif value is not None:
                daily = Decimal(value)
        ratios: dict[str, Decimal] = {}
        for market, field in self._ratio_fields.items():
            if field.is_empty():
                continue
            ratio, error = parse_percent(field.text())
            if error is not None:
                field.set_error(error)
                ok = False
            elif ratio is not None:
                ratios[market] = ratio
        if not ok:
            return False, None
        return True, LiveSettings(budget=budget, coin_ratios=ratios, daily_loss_limit=daily)

    # --- 칸 아래 글 ---

    def first_error_widget(self) -> QWidget | None:
        """칸 아래 글이 보이는 칸 가운데 맨 위의 것. 없으면 `None`."""
        for field in (self.budget, *self._ratio_fields.values()):
            if not field.error.isHidden():
                return field
        if not self._sum_error.isHidden():
            return self._sum_error
        if not self.daily_loss.error.isHidden():
            return self.daily_loss
        return None

    def clear_errors(self) -> None:
        for field in (self.budget, self.daily_loss, *self._ratio_fields.values()):
            field.set_error(None)
        self._sum_error.setText("")
        self._sum_error.hide()

    def show_errors(self, errors: Mapping[str, str]) -> None:
        """저장 뒤 검사에 걸린 칸의 글(`SaveResult.field_errors`의 `live_…` 키)을 칸 아래에 보인다.
        """
        self.clear_errors()
        by_key: dict[str, NumberField] = {
            "live_budget": self.budget,
            "live_daily_loss_limit": self.daily_loss,
        }
        for market, field in self._ratio_fields.items():
            by_key[f"live_coin_ratio:{market}"] = field
        for key, text in errors.items():
            if key == "live_coin_ratios":
                self._sum_error.setText(text)
                self._sum_error.show()
            elif key in by_key:
                by_key[key].set_error(text)

    # --- 안쪽 ---

    def _on_edit(self) -> None:
        self._recompute()
        self.changed.emit()

    def _recompute(self) -> None:
        """코인 예산과 합계를 칸에서 다시 셈한다."""
        budget_value, error = parse_integer(self.budget.text())
        budget = budget_value if error is None else None
        total = Decimal(0)
        for market, field in self._ratio_fields.items():
            ratio, ratio_error = parse_percent(field.text())
            ratio_value = ratio if ratio_error is None else None
            if ratio_value is not None:
                total += ratio_value
            amount = None
            if budget is not None and ratio_value is not None:
                amount = Decimal(budget) * ratio_value / _HUNDRED
            self._budget_labels[market].setText(
                coin_budget_text(money_text(amount) if amount is not None else None)
            )
        self._sum_label.setText(ratio_sum_text(format(total.quantize(_CENT), "f")))
