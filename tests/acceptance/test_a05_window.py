"""명세 7절 A5: 창과 화면(5단계부터 여섯)의 자리."""

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from coin_report_trader.core import ScreenId, UiContext
from coin_report_trader.ui import create_main_window, current_screen, screen_order, screen_widget
from coin_report_trader.ui.info_icon import InfoIcon

# 명세 2절의 글. 앱의 상수를 가져오지 않고 명세의 글자를 그대로 쓴다.
TITLES = ["처음 설정", "현황", "계정", "보고서", "기록", "설정"]
EMPTY_BODY = "이 화면은 아직 비어 있습니다."
ADVICE = "이 프로그램은 투자 자문이 아닙니다. AI 판단은 틀릴 수 있습니다."
OBJECT_NAMES = [
    "screen_first_setup",
    "screen_status",
    "screen_account",
    "screen_reports",
    "screen_history",
    "screen_settings",
]


# M1 명세 3.1: 처음 설정, 현황, 설정은 설계서대로 채운다(17번 뒤). 보고서는 2단계, 기록은 3단계
# (M3 명세 3.17.4)에서 채워져 빈 글이 남은 자리가 없다.
FILLED_SCREENS = (
    ScreenId.FIRST_SETUP,
    ScreenId.STATUS,
    ScreenId.ACCOUNT,
    ScreenId.SETTINGS,
    ScreenId.REPORTS,
    ScreenId.HISTORY,
)
# 설계서 4.5: `actions` 없이 만든 창의 현황 자리는 "처음 설정 전"의 글 하나만 보인다.
STATUS_BEFORE_SETUP = "처음 설정을 마치면 여기에 코인별 현황이 보입니다."


def _texts(widget) -> list[str]:
    return [label.text() for label in widget.findChildren(QLabel)]


def _info_tips(widget) -> list[str]:
    """화면의 'ⓘ'(M1 명세 3.14.6)의 툴팁 글. 투자 자문 글은 '꼭 읽을 글' 옆 'ⓘ'에 있다."""
    return [
        line.removeprefix("• ")
        for icon in widget.findChildren(InfoIcon)
        for line in icon.toolTip().split("\n")
    ]


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
            # M1 명세 3.1: 처음 설정, 현황, 설정 자리는 설계서대로 채워져 빈 글이 없다.
            # 보고서도 2단계에서, 기록도 3단계에서 채워진다.
            assert (EMPTY_BODY in texts) is (screen_id not in FILLED_SCREENS)
            assert (STATUS_BEFORE_SETUP in texts) is (screen_id is ScreenId.STATUS)
            # 투자 자문 글은 처음 설정과 보고서(M2 명세 3.12.1)의 '꼭 읽을 글' 옆 'ⓘ'에 있고, 설정의
            # '모드' 묶음의 'ⓘ'에도 있다(M5 명세 3.11).
            assert (ADVICE in _info_tips(widget)) is (
                screen_id in (ScreenId.FIRST_SETUP, ScreenId.REPORTS, ScreenId.SETTINGS)
            )
            assert ADVICE not in texts  # 늘 보이는 글이 아니다
        assert current_screen(window) is ScreenId.FIRST_SETUP
    finally:
        window.close()
        window.deleteLater()
