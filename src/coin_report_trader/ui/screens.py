"""화면 자리의 공통 부분과, 아직 내용이 없는 자리(현황, 보고서, 기록)의 빈 글."""

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from coin_report_trader.core import SCREEN_TITLES, ScreenId
from coin_report_trader.ui.texts import ADVICE_NOTICE, EMPTY_BODY
from coin_report_trader.ui.widgets import make_label

TITLE_NAME = "screenTitle"
BODY_NAME = "screenBody"
NOTICE_NAME = "screenNotice"

__all__ = [
    "ADVICE_NOTICE",
    "BODY_NAME",
    "EMPTY_BODY",
    "NOTICE_NAME",
    "TITLE_NAME",
    "build_screen",
    "screen_object_name",
    "title_label",
]


def screen_object_name(screen_id: ScreenId) -> str:
    """자리 위젯의 objectName. 예: `screen_first_setup`."""
    return f"screen_{screen_id.value}"


def title_label(text: str, name: str = TITLE_NAME) -> QLabel:
    """자리의 제목 라벨(굵게)."""
    title = make_label(text, name)
    font = title.font()
    font.setBold(True)
    title.setFont(font)
    return title


def build_screen(screen_id: ScreenId) -> QWidget:
    """화면 이름을 제목으로, 빈 화면 문구를 본문으로 보이는 자리 하나."""
    page = QWidget()
    page.setObjectName(screen_object_name(screen_id))
    layout = QVBoxLayout(page)
    layout.addWidget(title_label(SCREEN_TITLES[screen_id]))
    layout.addWidget(make_label(EMPTY_BODY, BODY_NAME))
    layout.addStretch(1)
    return page
