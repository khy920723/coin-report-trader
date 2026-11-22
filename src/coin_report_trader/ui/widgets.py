"""글 라벨과 글 색을 다루는 작은 도구."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

from coin_report_trader.ui import theme
from coin_report_trader.ui.state import Message, Tone

TONE_PROPERTY = "tone"
"""라벨의 동적 속성. 값은 "normal"이나 "warning". 시험은 이 속성으로 경고 색을 알아본다."""
WARNING_COLOR = theme.ERROR
"""경고 글의 색. 5-0 판 2의 '오류' 토큰이다."""


def make_label(text: str, name: str, tone: Tone = Tone.NORMAL) -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    set_tone(label, tone)
    return label


def set_role(widget: QWidget, role: str) -> None:
    """글의 크기와 굵기, 단추의 모양을 고르는 동적 속성(`theme.ROLE_PROPERTY`)을 정한다."""
    widget.setProperty(theme.ROLE_PROPERTY, role)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def set_trend(label: QLabel, text: str) -> None:
    """부호가 있는 값(+, -)의 오름·내림 색. 0이나 부호가 없는 값은 칠하지 않는다."""
    if text.startswith("+"):
        trend = theme.TREND_RISE
    elif text.startswith("-"):
        trend = theme.TREND_FALL
    else:
        trend = ""
    if label.property(theme.TREND_PROPERTY) != trend:
        label.setProperty(theme.TREND_PROPERTY, trend)
        label.style().unpolish(label)
        label.style().polish(label)


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
