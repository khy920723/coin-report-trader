"""다섯 화면의 빈 자리를 만든다."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from coin_report_trader.core import SCREEN_TITLES, ScreenId

EMPTY_BODY = "이 화면은 아직 비어 있습니다."
ADVICE_NOTICE = "이 프로그램은 투자 자문이 아닙니다. AI 판단은 틀릴 수 있습니다."

TITLE_NAME = "screenTitle"
BODY_NAME = "screenBody"
NOTICE_NAME = "screenNotice"


def screen_object_name(screen_id: ScreenId) -> str:
    """자리 위젯의 objectName. 예: `screen_first_setup`."""
    return f"screen_{screen_id.value}"


def _label(text: str, name: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def build_screen(screen_id: ScreenId) -> QWidget:
    """화면 이름을 제목으로, 빈 화면 문구를 본문으로 보이는 자리 하나."""
    page = QWidget()
    page.setObjectName(screen_object_name(screen_id))
    layout = QVBoxLayout(page)

    title = _label(SCREEN_TITLES[screen_id], TITLE_NAME)
    title_font = title.font()
    title_font.setBold(True)
    title.setFont(title_font)
    layout.addWidget(title)
    layout.addWidget(_label(EMPTY_BODY, BODY_NAME))
    if screen_id is ScreenId.FIRST_SETUP:
        layout.addWidget(_label(ADVICE_NOTICE, NOTICE_NAME))
    layout.addStretch(1)
    return page
