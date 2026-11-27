"""화면 이름 목록과 찾기 칸의 선 아이콘. 이 앱이 그린다(그림 파일을 두지 않는다)."""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from coin_report_trader.core import ScreenId
from coin_report_trader.ui import theme

_SCALE = 2


def _draw(color: str, paint) -> QPixmap:
    pixmap = QPixmap(theme.ICON_SIZE * _SCALE, theme.ICON_SIZE * _SCALE)
    pixmap.setDevicePixelRatio(_SCALE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    paint(painter)
    painter.end()
    return pixmap


def _gear(p: QPainter) -> None:
    p.drawEllipse(QRectF(5, 5, 6, 6))
    for x1, y1, x2, y2 in ((8, 1.5, 8, 4), (8, 12, 8, 14.5), (1.5, 8, 4, 8), (12, 8, 14.5, 8)):
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _flag(p: QPainter) -> None:
    p.drawLine(QPointF(4, 2), QPointF(4, 14))
    p.drawPolyline([QPointF(4, 3), QPointF(12, 3), QPointF(10, 6), QPointF(12, 9), QPointF(4, 9)])


def _chart(p: QPainter) -> None:
    p.drawPolyline([QPointF(2, 12), QPointF(6, 7), QPointF(9, 10), QPointF(14, 3)])


def _document(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(3, 2, 10, 12), 1.5, 1.5)
    p.drawLine(QPointF(5.5, 6), QPointF(10.5, 6))
    p.drawLine(QPointF(5.5, 9), QPointF(10.5, 9))


def _clock(p: QPainter) -> None:
    p.drawEllipse(QRectF(2, 2, 12, 12))
    p.drawPolyline([QPointF(8, 5), QPointF(8, 8), QPointF(10.5, 9.5)])


def _wallet(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(2, 4, 12, 9), 1.5, 1.5)
    p.drawLine(QPointF(2.5, 6.5), QPointF(12, 2.5))
    p.drawEllipse(QRectF(10.5, 7.5, 2, 2))


_SHAPES = {
    ScreenId.FIRST_SETUP: _flag,
    ScreenId.STATUS: _chart,
    ScreenId.ACCOUNT: _wallet,
    ScreenId.REPORTS: _document,
    ScreenId.HISTORY: _clock,
    ScreenId.SETTINGS: _gear,
}


def screen_icon(screen_id: ScreenId) -> QIcon:
    """화면 이름 목록의 선 아이콘. 고른 칸은 글 색, 나머지는 보조 글 색이다."""
    icon = QIcon()
    shape = _SHAPES[screen_id]
    icon.addPixmap(_draw(theme.TEXT_SUB, shape), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(_draw(theme.TEXT, shape), QIcon.Mode.Selected, QIcon.State.Off)
    return icon


def search_icon() -> QIcon:
    """찾기 칸의 돋보기."""

    def magnifier(p: QPainter) -> None:
        p.drawEllipse(QRectF(2, 2, 9, 9))
        p.drawLine(QPointF(10, 10), QPointF(14, 14))

    return QIcon(_draw(theme.TEXT_SUB, magnifier))
