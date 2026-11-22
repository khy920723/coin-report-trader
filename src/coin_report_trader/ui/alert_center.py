"""알림센터(설계서 8, M1 명세 4절 25번): 탭 여섯, 알림 줄, 디스코드 웹후크 주소 칸.

현황 화면 안에 놓인다. 알림과 주소 상태는 조립(27번)이 `AlertsView`, `WebhookView`로 보내고,
이 위젯은 받은 값만 그린다. 동작은 시그널로 알리고, 확인 창과 `UiActions` 호출은
창(`MainWindow`)이 한다.
웹후크 주소는 `save_webhook_requested`의 인자로만 이 위젯을 떠나고, 어디에도 남기지 않는다.

`objectName`(`<c>`는 `AlertCategory`의 값 `trade`, `report`, `limit`, `holding`, `error`, `other`):

- 묶음 `alertCenter`, 탭 `alertTab_<c>`(글은 "이름"이나 "이름 ({n})".
  골라진 탭은 굵은 글과 밑줄),
- 목록 자리의 글 `alertListMessage`, 지우기 결과 글 `alertDeleteMessage`(경고 색),
  목록 묶음 `alertList`(골라진 탭의 줄만 들어 있다), "이 탭 모두 지우기" `alertClear`,
- 줄 `alertRow_<id>`(음수 id도 그대로), 줄 글 `alertLine_<id>`(보지 않은 줄은 "● "로 시작하고
  굵은 글),
  "지우기" `alertDelete_<id>`, 디스코드로 보내지 못함 `alertDiscordFailed_<id>`(경고 색),
  펼친 글 `alertDetail_<id>`(펼쳤을 때만 보임),
- 주소 칸 `alertWebhookTitle`, `alertWebhookInput`(점으로 보이는 입력 칸), 단추 `alertWebhookSave`,
  `alertWebhookChange`, `alertWebhookDelete`, `alertWebhookCancel`, `alertWebhookTest`,
  칸 아래 글 `alertWebhookMessage`(저장·지우기·형식), `alertWebhookTestMessage`(확인용 글).
"""

from collections.abc import Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from coin_report_trader.core import (
    ALERT_CATEGORY_TITLES,
    MASK,
    MODE_TITLES,
    AlertCategory,
    AlertsView,
    AlertView,
    LoadState,
    WebhookView,
    coin_label,
    is_webhook_url,
)
from coin_report_trader.core.texts import (
    TEST_FAILED,
    TEST_SENDING,
    WEBHOOK_BAD_FORMAT,
    WEBHOOK_DELETE_FAILED,
    WEBHOOK_NOT_SET,
    WEBHOOK_SAVE_FAILED,
    kst_datetime_text,
)
from coin_report_trader.ui import theme
from coin_report_trader.ui.info_icon import InfoIcon
from coin_report_trader.ui.state import Message, Tone
from coin_report_trader.ui.texts import (
    ALERT_CLEAR_TEXT,
    ALERT_DELETE_TEXT,
    ALERTS_UNREADABLE,
    DETAIL_AFTER,
    DETAIL_COIN,
    DETAIL_CONTENT,
    DETAIL_ERROR_NAME,
    DETAIL_STAGE,
    DISCORD_FAILED_TEXT,
    LOADING_TEXT,
    UNSEEN_DOT,
    WEBHOOK_CANCEL_TEXT,
    WEBHOOK_CHANGE_TEXT,
    WEBHOOK_DELETE_TEXT,
    WEBHOOK_EMPTY_TEXT,
    WEBHOOK_SAVE_TEXT,
    WEBHOOK_SAVED_SUFFIX,
    WEBHOOK_TEST_TEXT,
    no_alerts_text,
    tab_title_text,
    webhook_title_text,
)
from coin_report_trader.ui.widgets import make_label, set_role, show_message

CATEGORIES = tuple(AlertCategory)
"""탭의 차례(`AlertCategory`의 정의 순서)."""
DETAIL_CATEGORIES = (AlertCategory.ERROR, AlertCategory.OTHER)
"""줄을 누르면 자세한 글이 펼쳐지는 분류(설계서 8.3)."""
VISIBLE_ROWS = 5
_FAILURE_MESSAGES = frozenset({WEBHOOK_BAD_FORMAT, WEBHOOK_SAVE_FAILED, WEBHOOK_DELETE_FAILED})


class ClickableLabel(QLabel):
    """누르면 `clicked`가 나는 라벨."""

    clicked = Signal()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


def line_text(alert: AlertView) -> str:
    """알림 줄의 글: "{시각} · {모드} · {코인}: {글}"(코인이 없으면 " · {코인}"을 뺀다)."""
    try:
        when = kst_datetime_text(alert.created_at)
    except ValueError:
        when = "-"
    head = f"{when} · {MODE_TITLES[alert.mode]}"
    if alert.coin:
        head += f" · {coin_label(alert.coin)}"
    return f"{head}: {alert.text}"


def detail_text(alert: AlertView) -> str | None:
    """오류 알림과 기타 알림의 자세한 글. 자세한 글이 없으면 `None`."""
    detail = alert.detail
    if detail is None:
        return None
    lines = [f"{DETAIL_STAGE}: {detail.stage}"]
    if detail.coin:
        lines.append(f"{DETAIL_COIN}: {coin_label(detail.coin)}")
    lines.append(f"{DETAIL_ERROR_NAME}: {detail.error_name}")
    lines.append(f"{DETAIL_CONTENT}: {detail.content}")
    lines.append(f"{DETAIL_AFTER}: {detail.after}")
    return "\n".join(lines)


def _style(label: QLabel, *, bold: bool = False, underline: bool = False) -> None:
    font = label.font()
    font.setBold(bold)
    font.setUnderline(underline)
    label.setFont(font)


class AlertCenter(QWidget):
    """알림센터. `render`로 마지막 `AlertsView`와 `WebhookView`를 그린다."""

    seen_requested = Signal(object)
    """`(id, ...)`: 아직 보지 않은 줄이 목록에 보였다."""
    delete_alert_requested = Signal(int)
    clear_requested = Signal(object, int)
    """`(분류, 그 탭의 알림 수)`"""
    save_webhook_requested = Signal(object, str)
    delete_webhook_requested = Signal(object)
    send_test_requested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("alertCenter")
        self._selected = AlertCategory.TRADE
        self._alerts: AlertsView | None = None
        self._webhooks: dict[AlertCategory, WebhookView] = {}
        self._active = False
        self._hold = False
        self._requested: set[int] = set()
        self._expanded: set[int] = set()
        self._rows: list[QWidget] = []
        self._changing: set[AlertCategory] = set()
        self._pending: set[AlertCategory] = set()
        self._bad_format: set[AlertCategory] = set()
        self._delete_message: Message | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        tab_row = QHBoxLayout()
        self._tabs: dict[AlertCategory, QPushButton] = {}
        for category in CATEGORIES:
            tab = QPushButton(ALERT_CATEGORY_TITLES[category])
            tab.setObjectName(f"alertTab_{category.value}")
            tab.setFlat(True)
            tab.setProperty("tab", True)
            tab.clicked.connect(lambda _checked=False, c=category: self.select_tab(c))
            self._tabs[category] = tab
            tab_row.addWidget(tab)
        tab_row.addStretch(1)
        self._clear = QPushButton(ALERT_CLEAR_TEXT)
        self._clear.setObjectName("alertClear")
        self._clear.clicked.connect(self._on_clear)
        tab_row.addWidget(self._clear)
        layout.addLayout(tab_row)

        self._webhook_title = make_label("", "alertWebhookTitle")
        set_role(self._webhook_title, theme.ROLE_GROUP)
        self._webhook_info = InfoIcon("", WEBHOOK_NOT_SET, "alertWebhookInfo")
        title_line = QHBoxLayout()
        title_line.setContentsMargins(0, 0, 0, 0)
        title_line.setSpacing(theme.ICON_GAP)
        title_line.addWidget(self._webhook_title)
        title_line.addWidget(self._webhook_info)
        title_line.addStretch(1)
        layout.addLayout(title_line)
        webhook_row = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setObjectName("alertWebhookInput")
        self._input.setEchoMode(QLineEdit.EchoMode.Password)  # 넣는 동안 점으로 보인다.
        self._input.textEdited.connect(self._on_input_edited)
        webhook_row.addWidget(self._input, 1)
        self._save = self._button(WEBHOOK_SAVE_TEXT, "alertWebhookSave", self._on_save)
        set_role(self._save, theme.ROLE_PRIMARY)
        self._change = self._button(WEBHOOK_CHANGE_TEXT, "alertWebhookChange", self._on_change)
        self._delete = self._button(WEBHOOK_DELETE_TEXT, "alertWebhookDelete", self._on_delete)
        self._cancel = self._button(WEBHOOK_CANCEL_TEXT, "alertWebhookCancel", self._on_cancel)
        self._test = self._button(WEBHOOK_TEST_TEXT, "alertWebhookTest", self._on_test)
        for button in (self._save, self._change, self._delete, self._cancel, self._test):
            webhook_row.addWidget(button)
        layout.addLayout(webhook_row)
        self._webhook_message = make_label("", "alertWebhookMessage")
        self._test_message = make_label("", "alertWebhookTestMessage")
        layout.addWidget(self._webhook_message)
        layout.addWidget(self._test_message)

        self._list_message = make_label("", "alertListMessage")
        layout.addWidget(self._list_message)
        self._list = QWidget()
        self._list.setObjectName("alertList")
        self._list_layout = QVBoxLayout(self._list)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.addStretch(1)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._list)
        self._scroll.setMinimumHeight(VISIBLE_ROWS * (self.fontMetrics().height() + 12))
        layout.addWidget(self._scroll)
        self._delete_label = make_label("", "alertDeleteMessage")
        layout.addWidget(self._delete_label)
        self._refresh()

    def _button(self, text: str, name: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(name)
        button.clicked.connect(slot)
        return button

    # --- 창이 부르는 것 ---

    def selected_tab(self) -> AlertCategory:
        return self._selected

    def select_tab(self, category: AlertCategory) -> None:
        """탭을 고른다. 그 분류의 주소 칸과 목록이 보이고, 보지 않은 줄은 본 것으로 알린다.

        탭이 정해졌으므로 누르는 동안 미룬 본 알림 알림(`hold_seen`)도 푼다.
        """
        self._hold = False
        if category is not self._selected:
            self._selected = category
            self._requested.clear()
            self._input.setText("")
        self._refresh()

    def select_first_unseen_tab(self) -> None:
        """보지 않은 알림이 있는 탭 가운데 맨 앞 탭을 고른다(설계서 8.2). 없으면 그대로 둔다.

        사용자가 현황을 누른 때 부른다. 누르는 동안 미룬 본 알림 알림(`hold_seen`)을 이 탭이 정해진
        뒤에 푼다.
        """
        self._hold = False
        for category in CATEGORIES:
            if self._unseen(category) > 0:
                self.select_tab(category)
                return
        self._refresh()

    def render(
        self,
        *,
        alerts: AlertsView | None,
        webhooks: Mapping[AlertCategory, WebhookView],
        active: bool,
        delete_message: Message | None,
        hold_seen: bool = False,
    ) -> None:
        """`active`는 현황 화면이 지금 보이는 자리인지다. 아닐 때는 본 알림으로 알리지 않는다.

        `hold_seen`이 참이면 보이는 줄이 있어도 본 알림으로 알리지 않는다(사용자가 현황을 누르는
        중이라 어느 탭이 열릴지 아직 모른다).
        """
        if active and not self._active:
            self._requested.clear()
        self._active = active
        self._hold = hold_seen
        self._alerts = alerts
        for category, view in webhooks.items():
            if self._webhooks.get(category) is not view:
                self._pending.discard(category)
        self._webhooks = dict(webhooks)
        self._delete_message = delete_message
        self._refresh()

    # --- 그리기 ---

    def _unseen(self, category: AlertCategory) -> int:
        if self._alerts is None:
            return 0
        return int(self._alerts.unseen.get(category, 0))

    def _tab_alerts(self) -> list[AlertView]:
        if self._alerts is None:
            return []
        return [a for a in self._alerts.alerts if a.category is self._selected]

    def _refresh(self) -> None:
        for category, tab in self._tabs.items():
            tab.setText(tab_title_text(ALERT_CATEGORY_TITLES[category], self._unseen(category)))
            _style_button(tab, selected=category is self._selected)
        self._render_list()
        self._render_webhook()
        show_message(self._delete_label, self._delete_message)
        self._mark_seen()

    def _render_list(self) -> None:
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows = []
        alerts = self._tab_alerts()
        view = self._alerts
        title = ALERT_CATEGORY_TITLES[self._selected]
        if view is None or view.state is LoadState.LOADING:
            note: Message | None = Message(LOADING_TEXT)
            alerts = []
        elif view.state is LoadState.FAILED:
            note = Message(ALERTS_UNREADABLE, Tone.WARNING)
            alerts = []
        elif not alerts:
            note = Message(no_alerts_text(title))
        else:
            note = None
        show_message(self._list_message, note)
        self._scroll.setVisible(bool(alerts))
        self._clear.setEnabled(bool(alerts))
        present = {alert.id for alert in alerts}
        self._expanded &= present
        for alert in alerts:
            row = self._build_row(alert)
            self._rows.append(row)
            self._list_layout.insertWidget(self._list_layout.count() - 1, row)

    def _build_row(self, alert: AlertView) -> QWidget:
        row = QWidget()
        row.setObjectName(f"alertRow_{alert.id}")
        row.setProperty("alertRow", True)
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(row)
        outer.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        text = line_text(alert)
        if not alert.seen:
            text = f"{UNSEEN_DOT} {text}"
        line = ClickableLabel(text)
        line.setObjectName(f"alertLine_{alert.id}")
        line.setTextFormat(Qt.TextFormat.PlainText)
        line.setWordWrap(True)
        _style(line, bold=not alert.seen)
        top.addWidget(line, 1)
        if alert.discord_failed:
            top.addWidget(
                make_label(DISCORD_FAILED_TEXT, f"alertDiscordFailed_{alert.id}", Tone.WARNING)
            )
        delete = QPushButton(ALERT_DELETE_TEXT)
        delete.setObjectName(f"alertDelete_{alert.id}")
        delete.clicked.connect(
            lambda _checked=False, i=alert.id: self.delete_alert_requested.emit(i)
        )
        top.addWidget(delete)
        outer.addLayout(top)
        detail = detail_text(alert) if alert.category in DETAIL_CATEGORIES else None
        label = make_label(detail or "", f"alertDetail_{alert.id}")
        set_role(label, theme.ROLE_SUB)
        label.setVisible(detail is not None and alert.id in self._expanded)
        outer.addWidget(label)
        if detail is not None:
            line.clicked.connect(lambda i=alert.id, w=label: self._toggle(i, w))
        return row

    def _toggle(self, alert_id: int, detail: QLabel) -> None:
        if alert_id in self._expanded:
            self._expanded.discard(alert_id)
        else:
            self._expanded.add(alert_id)
        detail.setVisible(alert_id in self._expanded)

    def _mark_seen(self) -> None:
        """현황 화면이 보이고 그 탭이 골라진 동안, 목록에 든 보지 않은 줄을 한 번씩 알린다.

        다섯 줄 밖으로 넘겨 가린 줄도 목록에 든 것으로 본다. 같은 id는 탭을 바꾸거나 현황 화면을
        다시 열기 전에는 다시 알리지 않는다.
        """
        if self._hold or not self._active:
            return
        if self._alerts is None or self._alerts.state is not LoadState.READY:
            return
        ids = tuple(a.id for a in self._tab_alerts() if not a.seen and a.id not in self._requested)
        if not ids:
            return
        self._requested.update(ids)
        self.seen_requested.emit(ids)

    # --- 주소 칸 ---

    def _view(self) -> WebhookView | None:
        return self._webhooks.get(self._selected)

    def _render_webhook(self) -> None:
        category = self._selected
        view = self._view()
        title = ALERT_CATEGORY_TITLES[category]
        self._webhook_title.setText(webhook_title_text(title))
        self._webhook_info.set_info(WEBHOOK_NOT_SET, self._webhook_title.text())
        pending = category in self._pending
        changing = category in self._changing
        loaded = view is not None
        saved = bool(view and view.saved)
        locked = saved and not changing
        self._input.setEnabled(loaded and not locked and not pending)
        if not loaded:
            self._input.setPlaceholderText(LOADING_TEXT)
        elif locked:
            self._input.setPlaceholderText(f"{MASK}{WEBHOOK_SAVED_SUFFIX}")
        elif changing:
            self._input.setPlaceholderText("")
        else:
            self._input.setPlaceholderText(WEBHOOK_EMPTY_TEXT)
        self._save.setVisible(not locked)
        self._save.setEnabled(loaded and not pending)
        self._change.setVisible(locked)
        self._change.setEnabled(loaded and not pending)
        self._delete.setVisible(saved)
        self._delete.setEnabled(loaded and not pending)
        self._cancel.setVisible(changing)
        in_flight = view is not None and view.test_message == TEST_SENDING
        self._test.setEnabled(saved and not pending and not in_flight)
        first = self._first_line(view, category)
        # 주소가 없을 때의 글은 칸 이름 옆 'ⓘ'의 툴팁이다(M1 명세 3.14.6).
        # 칸의 "넣지 않음"은 늘 보인다.
        not_set = first is not None and first.text == WEBHOOK_NOT_SET
        self._webhook_info.setVisible(not_set)
        show_message(self._webhook_message, None if not_set else first)
        show_message(self._test_message, self._second_line(view))

    def _first_line(self, view: WebhookView | None, category: AlertCategory) -> Message | None:
        if category in self._bad_format:
            return Message(WEBHOOK_BAD_FORMAT, Tone.WARNING)
        if view is None:
            return None
        if view.save_message:
            tone = Tone.WARNING if view.save_message in _FAILURE_MESSAGES else Tone.NORMAL
            return Message(view.save_message, tone)
        if not view.saved:
            return Message(WEBHOOK_NOT_SET)
        return None

    @staticmethod
    def _second_line(view: WebhookView | None) -> Message | None:
        if view is None or not view.test_message:
            return None
        tone = Tone.WARNING if view.test_message == TEST_FAILED else Tone.NORMAL
        return Message(view.test_message, tone)

    def _on_input_edited(self, _text: str) -> None:
        if self._selected in self._bad_format:
            self._bad_format.discard(self._selected)
            self._render_webhook()

    def _on_save(self) -> None:
        category = self._selected
        text = self._input.text().strip()
        if not text:
            return  # 빈 칸과 빈칸만 든 칸은 아무 일도 없다. 경고도 없다.
        if not is_webhook_url(text):
            self._bad_format.add(category)
            self._render_webhook()
            return
        self._bad_format.discard(category)
        self._input.setText("")
        self._changing.discard(category)
        self._pending.add(category)
        self._render_webhook()
        self.save_webhook_requested.emit(category, text)

    def _on_change(self) -> None:
        self._changing.add(self._selected)
        self._input.setText("")
        self._render_webhook()

    def _on_cancel(self) -> None:
        self._changing.discard(self._selected)
        self._bad_format.discard(self._selected)
        self._input.setText("")
        self._render_webhook()

    def _on_delete(self) -> None:
        self.delete_webhook_requested.emit(self._selected)

    def mark_delete_requested(self, category: AlertCategory) -> None:
        """주소 지우기를 확인했다: 결과가 올 때까지 단추를 누를 수 없다."""
        self._pending.add(category)
        self._changing.discard(category)
        self._input.setText("")
        self._render_webhook()

    def _on_test(self) -> None:
        view = self._view()
        if view is not None and view.saved:
            self.send_test_requested.emit(self._selected)

    def _on_clear(self) -> None:
        alerts = self._tab_alerts()
        if alerts and self._clear.isEnabled():
            self.clear_requested.emit(self._selected, len(alerts))


def _style_button(button: QPushButton, *, selected: bool) -> None:
    button.setProperty("selected", selected)
    button.style().unpolish(button)
    button.style().polish(button)
    font = button.font()
    font.setBold(selected)
    font.setUnderline(selected)
    button.setFont(font)
