"""창과 다섯 화면의 자리. `core`, PySide6, 표준 라이브러리만 import한다.

조립(`app`)은 `create_main_window(context)`로 창을 받아 띄운다. 창 안을 읽는 방법
(자리의 순서, 지금 보이는 자리, 자리의 글)은 `create_main_window`의 문서 문자열에 있다.
"""

from coin_report_trader.ui.main_window import (
    create_main_window,
    current_screen,
    screen_order,
    screen_widget,
    select_screen,
    status_line_text,
)
from coin_report_trader.ui.screens import ADVICE_NOTICE, EMPTY_BODY

__all__ = [
    "ADVICE_NOTICE",
    "EMPTY_BODY",
    "create_main_window",
    "current_screen",
    "screen_order",
    "screen_widget",
    "select_screen",
    "status_line_text",
]
