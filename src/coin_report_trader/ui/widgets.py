"""글 라벨과 글 색을 다루는 작은 도구."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from coin_report_trader.ui.state import Message, Tone

TONE_PROPERTY = "tone"
"""라벨의 동적 속성. 값은 "normal"이나 "warning". 시험은 이 속성으로 경고 색을 알아본다."""
WARNING_COLOR = "#b3261e"


def make_label(text: str, name: str, tone: Tone = Tone.NORMAL) -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    set_tone(label, tone)
    return label


def set_tone(label: QLabel, tone: Tone) -> None:
    label.setProperty(TONE_PROPERTY, tone.value)
    label.setStyleSheet(f"color: {WARNING_COLOR};" if tone is Tone.WARNING else "")


def show_message(label: QLabel, message: Message | None) -> None:
    """글이 있으면 보이고, 없으면 글을 비우고 숨긴다."""
    if message is None or not message.text:
        label.setText("")
        set_tone(label, Tone.NORMAL)
        label.hide()
        return
    label.setText(message.text)
    set_tone(label, message.tone)
    label.show()
