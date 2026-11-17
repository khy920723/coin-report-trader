"""확인 창(설계서 2.6): 제목, 본문, 버튼 둘("확인", "취소"). "취소"가 기본 버튼이다."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QPushButton, QWidget

from coin_report_trader.ui.texts import CANCEL_TEXT, CONFIRM_TEXT

BOX_NAME = "confirmBox"
OK_NAME = "confirmOk"
CANCEL_NAME = "confirmCancel"


def build_confirm_box(parent: QWidget | None, title: str, body: str) -> QMessageBox:
    """확인 창을 만들어 돌려준다(띄우지 않는다). 버튼은 `confirmOk`, `confirmCancel`."""
    box = QMessageBox(parent)
    box.setObjectName(BOX_NAME)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(body)
    ok = box.addButton(CONFIRM_TEXT, QMessageBox.ButtonRole.AcceptRole)
    ok.setObjectName(OK_NAME)
    cancel = box.addButton(CANCEL_TEXT, QMessageBox.ButtonRole.RejectRole)
    cancel.setObjectName(CANCEL_NAME)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    return box


def ask_confirm(parent: QWidget | None, title: str, body: str) -> bool:
    """확인 창을 띄워 "확인"을 눌렀을 때만 참을 돌려준다(Enter나 닫기는 "취소")."""
    box = build_confirm_box(parent, title, body)
    box.exec()
    clicked = box.clickedButton()
    confirmed = isinstance(clicked, QPushButton) and clicked.objectName() == OK_NAME
    box.deleteLater()
    return confirmed
