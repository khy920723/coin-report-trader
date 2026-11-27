"""확인 창(설계서 2.6): 제목, 본문, 버튼 둘("확인", "취소"). "취소"가 기본 버튼이다.

실거래 켜기(SCR-06-9)는 본문 아래에 값의 표(`liveConfirmTable`)가 더 있다(M5 명세 3.11).
"""

from datetime import UTC, datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from coin_report_trader.core import LiveConfirmView, coin_label
from coin_report_trader.core.texts import kst_when_text
from coin_report_trader.ui.texts import (
    CANCEL_TEXT,
    CONFIRM_TEXT,
    LIVE_CONFIRM_BODY,
    LIVE_CONFIRM_OK,
    LIVE_CONFIRM_TITLE,
    live_confirm_lines,
    live_read_at_text,
)
from coin_report_trader.ui.trade_fields import money_text, percent_text

BOX_NAME = "confirmBox"
OK_NAME = "confirmOk"
CANCEL_NAME = "confirmCancel"
LIVE_TABLE_NAME = "liveConfirmTable"
LIVE_READ_AT_NAME = "liveConfirmReadAt"


def build_confirm_box(
    parent: QWidget | None, title: str, body: str, ok_text: str = CONFIRM_TEXT
) -> QMessageBox:
    """확인 창을 만들어 돌려준다(띄우지 않는다). 버튼은 `confirmOk`(글은 `ok_text`),
    `confirmCancel`("취소", 기본 버튼)."""
    box = QMessageBox(parent)
    box.setObjectName(BOX_NAME)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(body)
    ok = box.addButton(ok_text, QMessageBox.ButtonRole.AcceptRole)
    ok.setObjectName(OK_NAME)
    cancel = box.addButton(CANCEL_TEXT, QMessageBox.ButtonRole.RejectRole)
    cancel.setObjectName(CANCEL_NAME)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    return box


def _confirmed(box: QMessageBox) -> bool:
    box.exec()
    clicked = box.clickedButton()
    confirmed = isinstance(clicked, QPushButton) and clicked.objectName() == OK_NAME
    box.deleteLater()
    return confirmed


def ask_confirm(parent: QWidget | None, title: str, body: str, ok_text: str = CONFIRM_TEXT) -> bool:
    """확인 창을 띄워 "확인"을 눌렀을 때만 참을 돌려준다(Enter나 닫기는 "취소")."""
    return _confirmed(build_confirm_box(parent, title, body, ok_text))


def live_confirm_texts(view: LiveConfirmView) -> tuple[str, ...]:
    """SCR-06-9 표의 줄. 금액은 원 아래를 버려 쉼표를 붙이고, 보유가 없으면 "보유 없음"이다."""
    coins = tuple(
        (coin_label(market), percent_text(ratio), money_text(amount))
        for market, ratio, amount in view.coins
    )
    held = {market: (qty, value) for market, qty, value in view.holdings}
    holdings: list[tuple[str, str, str | None]] = []
    for market, _ratio, _amount in view.coins:
        qty, value = held.get(market, (None, None))
        if qty is None or qty == 0:
            holdings.append((coin_label(market), "", None))
        else:
            holdings.append(
                (
                    coin_label(market),
                    format(qty.normalize(), "f"),
                    money_text(value) if value is not None else None,
                )
            )
    return live_confirm_lines(
        money_text(view.budget),
        coins,
        money_text(view.krw_available),
        tuple(holdings),
        money_text(view.total),
    )


def build_live_confirm_box(
    parent: QWidget | None, view: LiveConfirmView, now: datetime | None = None
) -> QDialog:
    """SCR-06-9를 만들어 돌려준다(띄우지 않는다). 창 `confirmBox`에 본문, 그 아래 표
    `liveConfirmTable`(줄마다 한 줄 `liveConfirmTableLine_<n>`)과 "{시각}에 읽은 값"
    `liveConfirmReadAt`, 단추 "실거래 켜기"(`confirmOk`)와 "취소"(`confirmCancel`, 기본)가 있다.
    `now`는 시각 글의 날짜를 가를 지금 시각이다(없으면 실제 지금)."""
    box = QDialog(parent)
    box.setObjectName(BOX_NAME)
    box.setWindowTitle(LIVE_CONFIRM_TITLE)
    box.setModal(True)
    layout = QVBoxLayout(box)
    body = QLabel(LIVE_CONFIRM_BODY)
    body.setObjectName("confirmBody")
    body.setTextFormat(Qt.TextFormat.PlainText)
    body.setWordWrap(True)
    layout.addWidget(body)
    table = QWidget()
    table.setObjectName(LIVE_TABLE_NAME)
    rows = QVBoxLayout(table)
    rows.setContentsMargins(0, 0, 0, 0)
    for index, line in enumerate(live_confirm_texts(view)):
        label = QLabel(line)
        label.setObjectName(f"{LIVE_TABLE_NAME}Line_{index}")
        label.setTextFormat(Qt.TextFormat.PlainText)
        rows.addWidget(label)
    layout.addWidget(table)
    if view.as_of is not None:
        try:
            when = kst_when_text(view.as_of, now or datetime.now(UTC))
        except ValueError:
            when = None
        if when is not None:
            read_at = QLabel(live_read_at_text(when))
            read_at.setObjectName(LIVE_READ_AT_NAME)
            layout.addWidget(read_at)
    ok = QPushButton(LIVE_CONFIRM_OK)
    ok.setObjectName(OK_NAME)
    ok.clicked.connect(box.accept)
    cancel = QPushButton(CANCEL_TEXT)
    cancel.setObjectName(CANCEL_NAME)
    cancel.setDefault(True)
    cancel.clicked.connect(box.reject)
    buttons = QHBoxLayout()
    buttons.addStretch(1)
    buttons.addWidget(ok)
    buttons.addWidget(cancel)
    layout.addLayout(buttons)
    cancel.setFocus()
    return box


def ask_live_confirm(parent: QWidget | None, view: LiveConfirmView) -> bool:
    """SCR-06-9를 띄워 "실거래 켜기"를 눌렀을 때만 참을 돌려준다(Esc나 닫기는 "취소")."""
    box = build_live_confirm_box(parent, view)
    confirmed = box.exec() == QDialog.DialogCode.Accepted
    box.deleteLater()
    return confirmed
