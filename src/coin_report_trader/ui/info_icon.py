"""'ⓘ' 아이콘과 툴팁(M1 명세 3.14.7, 화면 설계서 5-0 판 2의 11장).

16 × 16px의 선 그림을 이 앱이 그린다. 마우스를 올리면 툴팁이 보이고, 눌러도 같은 툴팁이 보인다.
Tab 차례에 들고 Space나 Enter로 툴팁을 열고 Esc로 닫는다. 툴팁 글과 화면 읽기 도구의 설명이 같다.
"""

from html import escape

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QToolTip, QWidget

from coin_report_trader.ui import theme

BULLET = "• "


def info_text(*texts: str) -> str:
    """툴팁 글. 글이 하나면 그대로, 둘 이상이면 줄마다 "• "로 시작해 `\\n`으로 잇는다."""
    if len(texts) == 1:
        return texts[0]
    return "\n".join(f"{BULLET}{text}" for text in texts)


def tooltip_html(text: str, *, font: QFont | None = None) -> str:
    """툴팁을 가장 넓게 320px로 줄바꿈해 보이게 하는 글. 길어도 자르지 않는다."""
    tip_font = font if font is not None else QFont(theme.FONT_FAMILY)
    tip_font.setPixelSize(theme.FONT_SUB)
    metrics = QFontMetrics(tip_font)
    widest = max((metrics.horizontalAdvance(line) for line in text.split("\n")), default=0)
    body = "<br>".join(escape(line) for line in text.split("\n"))
    width = theme.TOOLTIP_WIDTH if widest > theme.TOOLTIP_WIDTH else widest + 4
    return f'<table width="{width}"><tr><td>{body}</td></tr></table>'


class InfoIcon(QAbstractButton):
    """제목 바로 오른쪽에 놓는 'ⓘ'. `title`은 자리 제목이고 읽는 이름은 "{자리 제목} 설명"이다."""

    def __init__(self, title: str, text: str, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(name)
        self._title = title
        self.setFixedSize(theme.ICON_SIZE, theme.ICON_SIZE)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.set_info(text)
        self.clicked.connect(self.show_tip)

    def set_info(self, text: str, title: str | None = None) -> None:
        """툴팁 글을 바꾼다(읽는 설명도 같이). 자리 제목이 바뀌면 `title`로 알린다."""
        if title is not None:
            self._title = title
        self.setToolTip(text)
        self.setAccessibleName(f"{self._title} 설명")
        self.setAccessibleDescription(text)

    def show_tip(self) -> None:
        QToolTip.showText(
            self.mapToGlobal(QPoint(0, self.height())), tooltip_html(self.toolTip()), self
        )

    def hide_tip(self) -> None:
        QToolTip.hideText()

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            self.show_tip()
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.show_tip()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.hide_tip()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        self.hide_tip()
        super().focusOutEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        lit = self.underMouse() or self.hasFocus()
        color = QColor(theme.TEXT if lit else theme.TEXT_SUB)
        pen = QPen(color, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        size = float(theme.ICON_SIZE)
        painter.drawEllipse(QRectF(1.5, 1.5, size - 3, size - 3))
        painter.drawPoint(QPoint(int(size / 2), 5))
        painter.drawLine(QPoint(int(size / 2), 7), QPoint(int(size / 2), 12))
        if self.hasFocus():
            focus = QPen(QColor(theme.FOCUS), 2)
            painter.setPen(focus)
            painter.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), 4, 4)
