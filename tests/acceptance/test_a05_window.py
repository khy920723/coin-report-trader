"""명세 7절 A5: 창과 다섯 화면의 빈 자리."""

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from coin_report_trader.core import ScreenId, UiContext
from coin_report_trader.ui import create_main_window, current_screen, screen_order, screen_widget

# 명세 2절의 글. 앱의 상수를 가져오지 않고 명세의 글자를 그대로 쓴다.
TITLES = ["처음 설정", "현황", "보고서", "기록", "설정"]
EMPTY_BODY = "이 화면은 아직 비어 있습니다."
ADVICE = "이 프로그램은 투자 자문이 아닙니다. AI 판단은 틀릴 수 있습니다."
OBJECT_NAMES = [
    "screen_first_setup",
    "screen_status",
    "screen_reports",
    "screen_history",
    "screen_settings",
]


def _texts(widget) -> list[str]:
    return [label.text() for label in widget.findChildren(QLabel)]


@pytest.mark.scenario("A5")
def test_a5_window_has_five_empty_screens_in_order(qapp: QApplication) -> None:
    assert QApplication.platformName() == "offscreen"
    window = create_main_window(UiContext("0.0.1", "X"))
    try:
        assert window.windowTitle() == "Coin Report Trader"
        assert not window.isVisible()
        order = screen_order(window)
        assert [f"screen_{screen.value}" for screen in order] == OBJECT_NAMES
        assert list(order) == list(ScreenId)
        for screen_id, object_name, title in zip(ScreenId, OBJECT_NAMES, TITLES, strict=True):
            widget = screen_widget(window, screen_id)
            assert widget.objectName() == object_name
            texts = _texts(widget)
            assert title in texts
            assert EMPTY_BODY in texts
            assert (ADVICE in texts) is (screen_id is ScreenId.FIRST_SETUP)
        assert current_screen(window) is ScreenId.FIRST_SETUP
    finally:
        window.close()
        window.deleteLater()
