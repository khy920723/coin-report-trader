"""글 라벨과 글 색을 다루는 작은 도구."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QLabel, QWidget

from coin_report_trader.ui import theme
from coin_report_trader.ui.info_icon import InfoIcon
from coin_report_trader.ui.state import Message, Tone

TONE_PROPERTY = "tone"
"""라벨의 동적 속성. 값은 "normal"이나 "warning". 시험은 이 속성으로 경고 색을 알아본다."""
WARNING_COLOR = theme.ERROR
"""경고 글의 색. 5-0 판 3의 '오류' 토큰이다."""


class LineLabel(QLabel):
    """제목처럼 짧은 글의 라벨. 자리가 있으면 한 줄로 놓이고, 자리가 모자랄 때만 줄을 바꾼다.

    줄바꿈이 켜진 `QLabel`의 `sizeHint`는 글을 여러 줄로 접은 꼴의 너비를 돌려주어, 옆에 자리가
    남아도 두 줄로 꺾인다. 이 라벨은 글 전체가 한 줄에 드는 너비를 바라는 너비로 돌려준다.
    줄바꿈은 그대로 켜져 있어서, 레이아웃이 그보다 좁게 주면 낱말 사이에서 줄을 바꾼다.
    """

    def sizeHint(self) -> QSize:
        self.ensurePolished()
        base = super().sizeHint()
        if not self.wordWrap() or not self.text() or "\n" in self.text():
            return base
        pad = 2 * (self.margin() + self.frameWidth())
        width = max(base.width(), self.fontMetrics().horizontalAdvance(self.text()) + pad + 6)
        return QSize(width, max(self.fontMetrics().lineSpacing() + pad, 1))


def make_label(text: str, name: str, tone: Tone = Tone.NORMAL) -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    set_tone(label, tone)
    return label


LOCK_PROPERTY = "dimLocked"
"""참이면 `set_dim`이 다시 켜지 못하는 칸(예: 모의매매를 시작한 뒤의 가상 자금. M3 명세 3.7.1)."""


def set_dim(root: QWidget, dim: bool) -> None:
    """`root` 안의 칸을 흐리게(`dim`이 참) 하거나 다시 고칠 수 있게 한다.

    `root.setEnabled(False)`는 그 안의 'ⓘ'까지 막아, 흐린 모습에서 'ⓘ'가 눌리지도, 초점을 받지도
    못한다. 'ⓘ'는 설명이라 칸이 흐릴 때도 마우스와 키보드가 들어야 하므로, 'ⓘ'가 든 묶음은
    열어 두고 그 안의 다른 위젯만 흐리게 한다. 'ⓘ'가 없는 묶음은 통째로 흐리게 한다.
    """
    for child in root.children():
        if not isinstance(child, QWidget) or isinstance(child, InfoIcon):
            continue
        if child.property(LOCK_PROPERTY):
            child.setEnabled(False)
            continue
        if child.findChildren(InfoIcon):
            child.setEnabled(True)
            set_dim(child, dim)
        else:
            child.setEnabled(not dim)


def make_line_label(text: str, name: str, tone: Tone = Tone.NORMAL) -> LineLabel:
    """`make_label`과 같은 라벨인데, 자리가 있으면 한 줄로 놓인다(`LineLabel`)."""
    label = LineLabel(text)
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


def live_border_style(name: str) -> str:
    """실거래 모드의 눈에 띄는 테두리: `error` 색 2px(M5 명세 3.11. 설계서 2.2, 4.5)."""
    return f"QFrame#{name} {{ border: {theme.DANGER_BORDER}px solid {theme.ERROR}; }}"


def trend_of(text: str) -> str:
    """값의 글이 가리키는 오름·내림: `theme.TREND_RISE`, `theme.TREND_FALL`, 아니면 빈 글.

    "+"나 "▲ "로 시작하면 오름, "-"나 "▼ "로 시작하면 내림이다(M3 명세 3.17). 0이나 부호가 없는
    값(예: "0.00%", "다음 회차 전")과, 값이 없다는 뜻의 "-"는 빈 글이다. 표(기록 화면)도 이 규칙으로
    칠한다.
    """
    if text == "-":  # 값이 없다는 뜻의 "-"는 내림이 아니다
        return ""
    if text.startswith(("+", "▲ ")):
        return theme.TREND_RISE
    if text.startswith(("-", "▼ ")):
        return theme.TREND_FALL
    return ""


def set_trend(label: QLabel, text: str) -> None:
    """부호가 있는 값(+, -)의 오름·내림 색. "▲ "나 "▼ "로 시작하는 값(3단계의 손익과 차이. M3
    명세 3.17)은 그 부호대로 칠한다. 0이나 부호가 없는 값은 칠하지 않는다."""
    trend = trend_of(text)
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
