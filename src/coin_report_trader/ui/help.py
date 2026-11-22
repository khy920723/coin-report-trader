"""도움말 창(M2 명세 3.16, 화면 설계서 5-5 판 2의 마지막 장).

글의 원본은 저장소의 안내 문서 하나(`docs/guide.md`)이고, 이 창은 그 파일을 읽어 보이기만 한다.
네트워크를 쓰지 않고, 글 속의 주소를 눌러도 열리지 않으며, 그림과 바깥 자원을 불러오지 않는다.

객체 이름: 창 `helpWindow`(제목 "도움말"), 글 칸 `helpText`(`QTextBrowser`, 읽기만),
못 읽음의 글 `helpMessage`(`tone`이 "warning"이고 `error` 색).
"""

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QKeyEvent,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
)
from PySide6.QtWidgets import QDialog, QLabel, QTextBrowser, QVBoxLayout, QWidget

from coin_report_trader.core import redact
from coin_report_trader.ui import theme

WINDOW_NAME = "helpWindow"
TEXT_NAME = "helpText"
MESSAGE_NAME = "helpMessage"
WINDOW_TITLE = "도움말"
UNREADABLE_TEXT = "안내 문서를 읽지 못했습니다."
GUIDE_NAME = "guide.md"
INITIAL_SIZE = (720, 600)
MINIMUM_SIZE = (480, 360)
AREA_RATIO = 0.9
TEXT_MARGIN = 24
H2_TOP_MARGIN = 24

_LOG = logging.getLogger(__name__)


def guide_path() -> Path:
    """안내 문서의 자리. exe로 돌 때는 exe 옆의 `guide.md`, 그 밖에는 저장소의 `docs/guide.md`.

    다른 자리는 찾지 않는다. 읽지는 않고 자리만 돌려준다.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / GUIDE_NAME
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "docs" / GUIDE_NAME
        if candidate.is_file():
            return candidate
    return here.parents[3] / "docs" / GUIDE_NAME


class _GuideBrowser(QTextBrowser):
    """그림과 바깥 자원을 불러오지 않는 글 칸."""

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Ctrl+Home과 Ctrl+End는 글의 맨 위와 맨 아래로 넘긴다(PageUp, PageDown은 기본 동작)."""
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            bar = self.verticalScrollBar()
            if event.key() == Qt.Key.Key_Home:
                bar.setValue(bar.minimum())
                event.accept()
                return
            if event.key() == Qt.Key.Key_End:
                bar.setValue(bar.maximum())
                event.accept()
                return
        super().keyPressEvent(event)

    def loadResource(self, type: int, name: QUrl) -> None:
        return None


def window_size(available_width: int, available_height: int) -> tuple[int, int]:
    """창의 처음 크기: 720 × 600. 쓸 수 있는 영역보다 크면 그 영역의 90%(가장 작은 크기 아래로는
    줄이지 않는다)."""
    width, height = INITIAL_SIZE
    if width > available_width or height > available_height:
        width = min(width, int(available_width * AREA_RATIO))
        height = min(height, int(available_height * AREA_RATIO))
    return max(width, MINIMUM_SIZE[0]), max(height, MINIMUM_SIZE[1])


class HelpWindow(QDialog):
    """따로 된 창(모달이 아니다). `open_help()`가 열 때마다 파일을 읽고, 이미 열려 있으면 그 창을
    앞으로 낸다. 다시 열 때 크기와 자리를 기억하지 않는다."""

    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._path = path
        self.setObjectName(WINDOW_NAME)
        self.setWindowTitle(WINDOW_TITLE)
        self.setModal(False)
        self.setMinimumSize(*MINIMUM_SIZE)
        self.setStyleSheet(theme.app_stylesheet() + _extra_style())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._message = QLabel(UNREADABLE_TEXT)
        self._message.setObjectName(MESSAGE_NAME)
        self._message.setTextFormat(Qt.TextFormat.PlainText)
        self._message.setProperty("tone", "warning")
        self._message.setContentsMargins(TEXT_MARGIN, TEXT_MARGIN, TEXT_MARGIN, 0)
        self._message.hide()
        layout.addWidget(self._message)
        self._text = _GuideBrowser()
        self._text.setObjectName(TEXT_NAME)
        self._text.setReadOnly(True)
        self._text.setOpenLinks(False)
        self._text.setOpenExternalLinks(False)
        self._text.document().setDocumentMargin(TEXT_MARGIN)
        self._text.setFrameShape(QTextBrowser.Shape.NoFrame)
        layout.addWidget(self._text, 1)
        self.setFocusProxy(self._text)

    # --- 바깥에서 쓰는 것 ---

    def open_help(self) -> None:
        """이미 열려 있으면 앞으로 내고, 아니면 글을 읽어 처음 크기와 자리로 연다."""
        if self.isVisible():
            self.raise_()
            self.activateWindow()
            return
        self.reload()
        self._place()
        self.show()
        self.raise_()
        self.activateWindow()
        self._text.setFocus()

    def reload(self) -> None:
        """파일을 읽는다. 읽지 못하면 글 대신 "안내 문서를 읽지 못했습니다."가 보인다."""
        try:
            text = self._path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            detail = redact(f"{type(error).__name__}: {error}")
            _LOG.warning("안내 문서를 읽지 못했습니다: %s", detail)
            self._text.clear()
            self._text.hide()
            self._message.show()
            return
        self._message.hide()
        self._text.show()
        self._text.setMarkdown(text)
        _style_document(self._text)

    # --- 안쪽 ---

    def _place(self) -> None:
        parent = self.parentWidget()
        screen = parent.screen() if parent is not None else QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else None
        width, height = INITIAL_SIZE
        if area is not None:
            width, height = window_size(area.width(), area.height())
        self.resize(width, height)
        if parent is not None and parent.isVisible():
            center = parent.frameGeometry().center()
        elif area is not None:
            center = area.center()
        else:
            return
        top_left = QPoint(center.x() - width // 2, center.y() - height // 2)
        if area is not None:
            top_left.setX(max(area.left(), min(top_left.x(), area.right() - width + 1)))
            top_left.setY(max(area.top(), min(top_left.y(), area.bottom() - height + 1)))
        self.move(top_left)


def _extra_style() -> str:
    return f"""
QTextBrowser#{TEXT_NAME} {{ background: {theme.BG}; color: {theme.TEXT}; border: 0; }}
QLabel#{MESSAGE_NAME} {{ color: {theme.ERROR}; font-size: {theme.FONT_BODY}px; }}
"""


def _font(pixel_size: int, *, bold: bool, mono: bool = False) -> QFont:
    font = QFont(theme.FONT_FAMILY)
    font.setPixelSize(pixel_size)
    font.setBold(bold)
    if mono:
        font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def _style_document(browser: QTextBrowser) -> None:
    """글의 모양: 본문과 목록 14px, '#' 제목 20px 굵게, '##'·'###' 제목 14px 굵게('##' 위 24px),
    코드 글은 같은 글꼴에 '판' 바탕(`SURFACE`), 주소 글은 '글' 색에 밑줄 없음."""
    document = browser.document()
    document.setDefaultFont(_font(theme.FONT_BODY, bold=False))
    # 고치는 동안 쪼각의 순서가 바뀌므로, 먼저 모두 모은 뒤 고친다.
    heads: list[tuple[int, int, int, QTextCharFormat]] = []
    spans: list[tuple[int, int, bool, bool]] = []
    block = document.begin()
    while block.isValid():
        start = block.position()
        length = block.length() - 1
        level = block.blockFormat().headingLevel()
        if level:
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                heads.append((fragment.position(), fragment.length(), level, fragment.charFormat()))
                iterator += 1
        else:
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                char = fragment.charFormat()
                code = char.fontFixedPitch() or char.font().styleHint() == QFont.StyleHint.Monospace
                if char.isAnchor() or code:
                    spans.append((fragment.position(), fragment.length(), char.isAnchor(), code))
                iterator += 1
        block = block.next()
    for start, length, level, old in heads:
        fmt = QTextCharFormat(old)
        # 가져올 때 붙은 '제목의 키움'은 크기를 곱하므로 없앤다.
        fmt.clearProperty(QTextFormat.Property.FontSizeAdjustment)
        fmt.merge(_heading_format(level))
        cursor = QTextCursor(document)
        cursor.setPosition(start)
        cursor.setPosition(start + length, QTextCursor.MoveMode.KeepAnchor)
        cursor.setCharFormat(fmt)
        if level == 2:
            cursor = QTextCursor(document)
            cursor.setPosition(start)
            block_format = cursor.blockFormat()
            block_format.setTopMargin(H2_TOP_MARGIN)
            cursor.setBlockFormat(block_format)
    for start, length, anchor, code in spans:
        fmt = QTextCharFormat()
        if anchor:
            fmt.setForeground(QColor(theme.TEXT))
            fmt.setFontUnderline(False)
        if code:
            fmt.setFont(_font(theme.FONT_BODY, bold=False, mono=True))
            fmt.setBackground(QColor(theme.SURFACE))
        _merge(document, start, length, fmt)


def _heading_format(level: int) -> QTextCharFormat:
    size = theme.FONT_SCREEN_TITLE if level == 1 else theme.FONT_GROUP_TITLE
    fmt = QTextCharFormat()
    fmt.setFont(_font(size, bold=True))
    fmt.setForeground(QColor(theme.TEXT))
    return fmt


def _merge(document: QTextDocument, start: int, length: int, fmt: QTextCharFormat) -> None:
    if length <= 0:
        return
    cursor = QTextCursor(document)
    cursor.setPosition(start)
    cursor.setPosition(start + length, QTextCursor.MoveMode.KeepAnchor)
    cursor.mergeCharFormat(fmt)
