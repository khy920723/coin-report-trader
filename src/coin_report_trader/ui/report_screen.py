"""보고서 화면(M2 명세 3.12.1, 화면 설계서 5-5).

왼쪽에 1 코인 고르기(`reportCoin`)와 3 회차 목록(`reportRounds`), 오른쪽에 4~12(`reportDetailScroll`
안에 넘긴다). 이 화면은 동작을 직접 부르지 않는다. 사용자가 코인이나 회차를 고르면 `coin_chosen`,
`round_chosen`이 나고, 창이 `load_reports`와 `load_report`를 부른다.

`objectName`: 화면 `screen_reports`(제목 라벨 `screenTitle`), 맨 위 줄 `reportAiStatus`,
`reportCoin`, `reportRounds`, 상태별 글 `reportMessage`, `reportAiMark`(4),
`reportLastResult`(5), `reportChecks`(11), `reportDecision`(6), `reportEvidence`(8),
`reportSources`(9), `reportUsage`(12), `reportNotices`(10), 4~12를 담는 넘김 `reportDetailScroll`.
'ⓘ'는 `reportUsageInfo`, `reportNoticesInfo`다(판단의 'ⓘ'는 3단계에서 없앴다).
판단 아래 `reportOrder`, 사용량의 `reportOrderElapsed`, 맨 위의 멈춤 줄 `reportPause`는 3단계의
자리다. AI가 쓴 글과 출처는 서식 없는 글로만 보인다(링크로 바꾸지 않는다).
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from coin_report_trader.core import (
    AI_SLOT_TITLES,
    SCREEN_TITLES,
    AiSlot,
    AiStatus,
    AiStatusView,
    ComparedItem,
    ReportState,
    ReportView,
    RoundItem,
    ScreenId,
    coin_label,
    format_percent,
    format_price,
)
from coin_report_trader.core.texts import AI_MARK_TEXT, kst_datetime_text
from coin_report_trader.ui import theme
from coin_report_trader.ui.cards import Card, title_row
from coin_report_trader.ui.info_icon import InfoIcon
from coin_report_trader.ui.screens import TITLE_NAME, screen_object_name, title_label
from coin_report_trader.ui.state import Message, Tone
from coin_report_trader.ui.texts import (
    ADVICE_NOTICE,
    AI_ERROR_OFF,
    AI_NOT_CHOSEN,
    AI_NOT_FOUND,
    AI_VERSION_OFF,
    NO_VALUE,
    REPORT_CHECK_COLUMNS,
    REPORT_CHECKS_TITLE,
    REPORT_COIN_TITLE,
    REPORT_DECISION_TITLE,
    REPORT_DISCARDED,
    REPORT_EVIDENCE_TITLE,
    REPORT_FINAL_PART,
    REPORT_INTERRUPTED,
    REPORT_LAST_RESULT_TITLE,
    REPORT_NOTICES_TITLE,
    REPORT_PAUSE_LINE,
    REPORT_PROGRAM_VALUES,
    REPORT_ROUNDS_TITLE,
    REPORT_RUNNING,
    REPORT_SOURCES_TITLE,
    REPORT_USAGE_NOTE,
    REPORT_USAGE_TITLE,
    report_compared_title,
    report_elapsed_text,
    report_failed_text,
    report_last_price_text,
    report_no_order_text,
    report_order_elapsed_text,
    report_order_text,
    report_round_text,
    report_skipped_text,
    report_tokens_text,
)
from coin_report_trader.ui.widgets import make_label, set_role

AI_STATUS_TEXTS = {
    AiStatus.NOT_CHOSEN: AI_NOT_CHOSEN,
    AiStatus.NOT_FOUND: AI_NOT_FOUND,
    AiStatus.VERSION_OFF: AI_VERSION_OFF,
    AiStatus.ERROR_OFF: AI_ERROR_OFF,
}
LEFT_WIDTH = 280
LIST_MIN_HEIGHT = 240
_PARTS = (AiSlot.SEARCH, AiSlot.CANDLE, AiSlot.FINAL)


def round_end_text(item: RoundItem) -> str:
    """회차 줄의 끝(M2 명세 3.12.1의 3)."""
    state = item.state
    if state is ReportState.OK:
        return item.direction.value if item.direction is not None else NO_VALUE
    if state in (ReportState.FAILED, ReportState.ERROR):
        return "받지 못함"
    if state is ReportState.SKIPPED:
        return "쉼"
    if state is ReportState.INTERRUPTED:
        return "끊김"
    if state is ReportState.DISCARDED:
        return "멈춤으로 버림"
    return "만드는 중"


def round_line_text(item: RoundItem) -> str:
    try:
        when = kst_datetime_text(item.close_utc)
    except ValueError:
        when = NO_VALUE
    return report_round_text(item.round_number, when, round_end_text(item))


def _price_text(value) -> str:
    if value is None:
        return NO_VALUE
    try:
        return f"{format_price(value)}원"
    except ValueError:
        return NO_VALUE


def _clear(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())


def _line(text: str, *, sub: bool = False, big: bool = False, group: bool = False) -> QLabel:
    label = make_label(text, "")
    if sub:
        set_role(label, theme.ROLE_SUB)
    elif big:
        set_role(label, theme.ROLE_BIG)
    elif group:
        set_role(label, theme.ROLE_GROUP)
    return label


class _Section(QWidget):
    """제목(과 'ⓘ')과 그 아래 줄들을 쌓는 묶음. 줄은 `render`마다 새로 만든다."""

    def __init__(self, name: str, title: str, info: InfoIcon | None = None) -> None:
        super().__init__()
        self.setObjectName(name)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.LABEL_GAP)
        outer.addLayout(title_row(title, "", info))
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(theme.LABEL_GAP)
        outer.addLayout(self.body)

    def reset(self) -> QVBoxLayout:
        _clear(self.body)
        return self.body


class ReportScreen(QWidget):
    coin_chosen = Signal(str)
    round_chosen = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName(screen_object_name(ScreenId.REPORTS))
        self._rounds: tuple[RoundItem, ...] = ()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*(theme.SCREEN_PADDING,) * 4)
        layout.setSpacing(theme.CARD_GAP)
        layout.addWidget(title_label(SCREEN_TITLES[ScreenId.REPORTS], TITLE_NAME))
        self._ai_status = make_label("", "reportAiStatus")
        self._ai_status.hide()
        layout.addWidget(self._ai_status)
        # 멈춤이나 오류 멈춤이면 맨 위 줄(SCR-03-13. M3 명세 3.17.3). 'caution' 테두리와 글이다.
        self._pause_line = make_label(REPORT_PAUSE_LINE, "reportPause")
        self._pause_line.setStyleSheet(
            f"color: {theme.CAUTION}; border: 1px solid {theme.CAUTION};"
            f" border-radius: {theme.BUTTON_RADIUS}px; padding: 2px {theme.FIELD_GAP}px;"
        )
        self._pause_line.hide()
        layout.addWidget(self._pause_line)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(theme.CARD_GAP)
        body.addWidget(self._build_left(), 0)
        body.addWidget(self._build_right(), 1)
        layout.addLayout(body, 1)

    # --- 만들기 ---

    def _build_left(self) -> QWidget:
        left = Card()
        left.setFixedWidth(LEFT_WIDTH)
        left.body.addWidget(_line(REPORT_COIN_TITLE, group=True))
        self._coins = self._list("reportCoin")
        self._coins.setFixedHeight(theme.ROW_HEIGHT * 3 + 8)
        self._coins.currentRowChanged.connect(self._on_coin_row)
        left.body.addWidget(self._coins)
        left.body.addWidget(_line(REPORT_ROUNDS_TITLE, group=True))
        self._round_list = self._list("reportRounds")
        self._round_list.setMinimumHeight(LIST_MIN_HEIGHT)
        self._round_list.currentRowChanged.connect(self._on_round_row)
        left.body.addWidget(self._round_list, 1)
        return left

    def _list(self, name: str) -> QListWidget:
        widget = QListWidget()
        widget.setObjectName(name)
        widget.setUniformItemSizes(True)
        widget.setStyleSheet(f"QListWidget::item {{ height: {theme.ROW_HEIGHT}px; }}")
        return widget

    def _build_right(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setObjectName("reportDetailScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        detail = QWidget()
        column = QVBoxLayout(detail)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.CARD_GAP)
        self._message = make_label("", "reportMessage")
        self._message.hide()
        column.addWidget(self._message)
        self._ai_mark = make_label(AI_MARK_TEXT, "reportAiMark")
        self._ai_mark.setStyleSheet(
            f"color: {theme.CAUTION}; border: 1px solid {theme.CAUTION};"
            f" border-radius: {theme.BUTTON_RADIUS}px; padding: 2px {theme.FIELD_GAP}px;"
        )
        mark_row = QHBoxLayout()
        mark_row.setContentsMargins(0, 0, 0, 0)
        mark_row.addWidget(self._ai_mark)
        mark_row.addStretch(1)
        column.addLayout(mark_row)
        self._last = _Section("reportLastResult", REPORT_LAST_RESULT_TITLE)
        self._checks = _Section("reportChecks", REPORT_CHECKS_TITLE)
        # 판단의 'ⓘ'(`reportDecisionInfo`)는 3단계에서 없앤다(M3 명세 3.17.3).
        self._decision = _Section("reportDecision", REPORT_DECISION_TITLE)
        self._order = make_label("", "reportOrder")  # SCR-03-14. 판단 바로 아래
        self._order.hide()
        self._evidence = _Section("reportEvidence", REPORT_EVIDENCE_TITLE)
        self._sources = _Section("reportSources", REPORT_SOURCES_TITLE)
        self._usage = _Section(
            "reportUsage",
            REPORT_USAGE_TITLE,
            InfoIcon(REPORT_USAGE_TITLE, REPORT_USAGE_NOTE, "reportUsageInfo"),
        )
        self._notices = _Section(
            "reportNotices",
            REPORT_NOTICES_TITLE,
            InfoIcon(REPORT_NOTICES_TITLE, ADVICE_NOTICE, "reportNoticesInfo"),
        )
        self._sections = (
            self._last,
            self._checks,
            self._decision,
            self._evidence,
            self._sources,
            self._usage,
            self._notices,
        )
        for section in self._sections:
            column.addWidget(section)
            if section is self._decision:
                column.addWidget(self._order)
        column.addStretch(1)
        scroll.setWidget(detail)
        self._hide_detail()
        return scroll

    # --- 창이 부르는 것 ---

    def set_ai_status(self, view: AiStatusView | None) -> None:
        """맨 위 줄. `ON`이거나 아직 값이 없으면 줄이 없다."""
        text = AI_STATUS_TEXTS.get(view.status) if view is not None else None
        if text is None:
            self._ai_status.setText("")
            self._ai_status.hide()
            return
        self._ai_status.setText(text)
        self._ai_status.setProperty("tone", "normal")
        warning = view is not None and view.status is not AiStatus.NOT_CHOSEN
        self._ai_status.setStyleSheet(f"color: {theme.ERROR};" if warning else "")
        self._ai_status.setProperty("tone", "warning" if warning else "normal")
        self._ai_status.show()

    def set_paused(self, paused: bool) -> None:
        """멈춤이나 오류 멈춤이면 맨 위에 "멈춤 동안 새 보고서를 받지 않습니다."가 보인다."""
        self._pause_line.setVisible(paused)

    def set_coins(self, coins: tuple[str, ...]) -> None:
        """코인 고르기를 채운다(신호를 내지 않는다). 고르던 코인이 아직 있으면 그대로 둔다."""
        current = self.chosen_market()
        self._coins.blockSignals(True)
        self._coins.clear()
        for market in coins:
            item = QListWidgetItem(coin_label(market))
            item.setData(Qt.ItemDataRole.UserRole, market)
            self._coins.addItem(item)
        row = next((i for i, market in enumerate(coins) if market == current), 0)
        self._coins.setCurrentRow(row if coins else -1)
        self._coins.blockSignals(False)

    def chosen_market(self) -> str | None:
        item = self._coins.currentItem()
        data = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return data if isinstance(data, str) else None

    def choose_market(self, market: str) -> bool:
        """그 코인을 고른다(신호를 내지 않는다). 고르기에 없으면 `False`."""
        for row in range(self._coins.count()):
            item = self._coins.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == market:
                self._coins.blockSignals(True)
                self._coins.setCurrentRow(row)
                self._coins.blockSignals(False)
                return True
        return False

    def set_rounds(self, items: tuple[RoundItem, ...], selected_id: int | None) -> None:
        """회차 목록을 채운다(신호를 내지 않는다). `selected_id`의 줄을 고른다."""
        self._rounds = items
        self._round_list.blockSignals(True)
        self._round_list.clear()
        for item in items:
            row = QListWidgetItem(round_line_text(item))
            row.setData(Qt.ItemDataRole.UserRole, item.report_id)
            self._round_list.addItem(row)
        index = next((i for i, item in enumerate(items) if item.report_id == selected_id), -1)
        self._round_list.setCurrentRow(index)
        self._round_list.blockSignals(False)

    def selected_report_id(self) -> int | None:
        item = self._round_list.currentItem()
        data = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return data if isinstance(data, int) else None

    def render(self, *, message: Message | None, view: ReportView | None) -> None:
        """오른쪽을 그린다. `message`가 있으면 4~12 대신 그 글 하나만 보인다."""
        if message is not None:
            self._show_message(message)
            self._hide_detail()
            return
        if view is None:
            self._show_message(None)
            self._hide_detail()
            return
        state = view.state
        if state is ReportState.RUNNING:
            self._show_message(Message(REPORT_RUNNING))
            self._hide_detail()
        elif state in (ReportState.FAILED, ReportState.ERROR):
            self._show_message(Message(report_failed_text(view.failure), Tone.WARNING))
            self._hide_detail()
            self._fill_usage(view)
            self._usage.show()
        elif state is ReportState.SKIPPED:
            self._show_message(Message(report_skipped_text(view.failure)))
            self._hide_detail()
        elif state is ReportState.INTERRUPTED:
            self._show_message(Message(REPORT_INTERRUPTED))
            self._hide_detail()
        elif state is ReportState.DISCARDED:
            # 4~12 대신 이 글 하나. 12(사용량과 걸린 시간)는 보인다(M3 명세 3.17.3).
            self._show_message(Message(REPORT_DISCARDED))
            self._hide_detail()
            self._fill_usage(view)
            self._usage.show()
        else:
            self._show_message(None)
            self._fill_detail(view)

    # --- 안쪽 ---

    def _on_coin_row(self, row: int) -> None:
        market = self.chosen_market()
        if row >= 0 and market is not None:
            self.coin_chosen.emit(market)

    def _on_round_row(self, row: int) -> None:
        report_id = self.selected_report_id()
        if row >= 0 and report_id is not None:
            self.round_chosen.emit(report_id)

    def _show_message(self, message: Message | None) -> None:
        if message is None or not message.text:
            self._message.setText("")
            self._message.hide()
            return
        self._message.setText(message.text)
        warning = message.tone is Tone.WARNING
        self._message.setProperty("tone", "warning" if warning else "normal")
        self._message.setStyleSheet(f"color: {theme.ERROR};" if warning else "")
        set_role(self._message, theme.ROLE_SUB if not warning else "")
        self._message.show()

    def _hide_detail(self) -> None:
        self._ai_mark.hide()
        self._order.hide()
        for section in self._sections:
            section.hide()

    def _fill_detail(self, view: ReportView) -> None:
        self._ai_mark.show()
        self._fill_last(view)
        self._fill_checks(view)
        self._fill_decision(view)
        self._fill_order(view)
        self._fill_evidence(view)
        self._fill_sources(view)
        self._fill_usage(view)
        self._usage.show()
        self._notices.show()

    def _fill_last(self, view: ReportView) -> None:
        last = view.last_result
        self._last.setVisible(last is not None)
        if last is None:
            return
        body = self._last.reset()
        if view.result_text:
            body.addWidget(_line(view.result_text))
        if view.compared:
            body.addWidget(_line(report_compared_title(last.round_number), group=True))
            for item in view.compared:
                body.addWidget(_line(self._compared_text(item)))
        body.addWidget(_line(REPORT_PROGRAM_VALUES, group=True))
        body.addWidget(
            _line(report_last_price_text(last.round_number, _price_text(last.predicted_price)))
        )
        body.addWidget(_line(f"다음 회차 때의 실제 값: {_price_text(last.actual_price)}"))
        diff = NO_VALUE if last.diff is None else self._percent(last.diff)
        body.addWidget(_line(f"차이: {diff}"))
        hit = NO_VALUE if last.hit is None else last.hit.value
        body.addWidget(_line(f"오름·내림 예측: {hit}"))

    @staticmethod
    def _percent(value) -> str:
        try:
            return format_percent(value, signed=True)
        except (ValueError, ArithmeticError):
            return NO_VALUE

    @staticmethod
    def _compared_text(item: ComparedItem) -> str:
        return f"{AI_SLOT_TITLES[item.part]} {item.seq}. {item.text} → {item.verdict.value}"

    def _fill_checks(self, view: ReportView) -> None:
        self._checks.setVisible(bool(view.checklist))
        if not view.checklist:
            return
        body = self._checks.reset()
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(theme.FIELD_GAP)
        grid.setColumnStretch(0, 1)
        for column, title in enumerate(REPORT_CHECK_COLUMNS):
            head = _line(title, sub=True)
            grid.addWidget(head, 0, column)
        for row, item in enumerate(view.checklist, start=1):
            for column, text in enumerate(
                (item.text, item.belief.value, str(item.compared), str(item.hits))
            ):
                grid.addWidget(_line(text), row, column)
        body.addWidget(grid_host)

    def _fill_decision(self, view: ReportView) -> None:
        self._decision.show()
        body = self._decision.reset()
        direction = view.direction.value if view.direction is not None else NO_VALUE
        body.addWidget(_line(f"최종 판단: {direction}", big=True))
        price = _price_text(view.predicted_price)
        body.addWidget(_line(f"예측 가격(다음 회차 때의 값): {price}"))
        reason = view.predicted_reason if view.predicted_reason else NO_VALUE
        body.addWidget(_line(f"예측 까닭: {reason}"))
        body.addWidget(_line(f"매수 제안가: {_price_text(view.buy_price)}"))
        body.addWidget(_line(f"매도 제안가: {_price_text(view.sell_price)}"))

    def _fill_order(self, view: ReportView) -> None:
        """SCR-03-14: "이 판단으로 낸 주문: …"나 "주문하지 않은 까닭: …"(둘 다 없으면 숨김)."""
        if view.order_summary:
            self._order.setText(report_order_text(view.order_summary))
        elif view.no_order_reason:
            self._order.setText(report_no_order_text(view.no_order_reason))
        else:
            self._order.setText("")
        self._order.setVisible(bool(self._order.text()))

    def _fill_evidence(self, view: ReportView) -> None:
        self._evidence.setVisible(bool(view.evidence))
        if not view.evidence:
            return
        body = self._evidence.reset()
        for part in _PARTS:
            if part is AiSlot.SEARCH:
                value = view.search_judgment.value if view.search_judgment else NO_VALUE
                head = f"{AI_SLOT_TITLES[part]}: {value}"
            elif part is AiSlot.CANDLE:
                value = view.candle_judgment.value if view.candle_judgment else NO_VALUE
                head = f"{AI_SLOT_TITLES[part]}: {value}"
            else:
                head = REPORT_FINAL_PART
            body.addWidget(_line(head, group=True))
            for item in sorted((e for e in view.evidence if e.part is part), key=lambda e: e.seq):
                body.addWidget(_line(f"{item.seq}. {item.text}"))

    def _fill_sources(self, view: ReportView) -> None:
        self._sources.setVisible(bool(view.sources))
        if not view.sources:
            return
        body = self._sources.reset()
        for source in view.sources:
            label = _line(f"{source.title} · {source.url}")
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard
            )
            body.addWidget(label)

    def _fill_usage(self, view: ReportView) -> None:
        body = self._usage.reset()
        tokens = view.tokens
        if tokens is None:
            body.addWidget(_line(f"세션 전체 토큰: {NO_VALUE}"))
        else:
            body.addWidget(
                _line(
                    report_tokens_text(
                        tokens.input_tokens,
                        tokens.output_tokens,
                        tokens.cache_read_tokens,
                        tokens.cache_creation_tokens,
                    )
                )
            )
        if view.elapsed_report_seconds is None:
            body.addWidget(_line(f"보고서까지 걸린 시간: {NO_VALUE}"))
        else:
            body.addWidget(_line(report_elapsed_text(view.elapsed_report_seconds)))
        if view.elapsed_order_seconds is not None:  # 없으면 이 줄을 보이지 않는다(REQ-PERF-001)
            order = _line(report_order_elapsed_text(view.elapsed_order_seconds))
            order.setObjectName("reportOrderElapsed")
            body.addWidget(order)
