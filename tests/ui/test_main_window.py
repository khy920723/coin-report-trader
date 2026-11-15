"""명세 7절 A5: 창과 다섯 화면의 빈 자리."""

import os

import pytest
from PySide6.QtWidgets import QLabel, QListWidget, QMainWindow

from coin_report_trader.core import SCREEN_TITLES, WINDOW_TITLE, ScreenId, UiContext
from coin_report_trader.ui import (
    ADVICE_NOTICE,
    EMPTY_BODY,
    create_main_window,
    current_screen,
    screen_order,
    screen_widget,
    select_screen,
    status_line_text,
)

# QApplication은 qapp 픽스처가 처음 쓸 때 만들어지므로, 그 전에 offscreen을 고른다.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

CONTEXT = UiContext("0.0.1", "X")


@pytest.fixture
def window(qapp) -> QMainWindow:
    created = create_main_window(CONTEXT)
    yield created
    created.deleteLater()


def _text(widget, name: str) -> str:
    label = widget.findChild(QLabel, name)
    assert label is not None, name
    return label.text()


def test_window_basics(window):
    assert window.objectName() == "mainWindow"
    assert window.windowTitle() == WINDOW_TITLE == "Coin Report Trader"
    assert not window.isVisible()


def test_five_screens_in_order_with_object_names(window):
    assert screen_order(window) == tuple(ScreenId)
    assert [s.value for s in ScreenId] == [
        "first_setup",
        "status",
        "reports",
        "history",
        "settings",
    ]
    for screen_id in ScreenId:
        assert screen_widget(window, screen_id).objectName() == f"screen_{screen_id.value}"


def test_each_screen_shows_title_and_empty_body(window):
    for screen_id in ScreenId:
        widget = screen_widget(window, screen_id)
        assert _text(widget, "screenTitle") == SCREEN_TITLES[screen_id]
        assert _text(widget, "screenBody") == EMPTY_BODY == "이 화면은 아직 비어 있습니다."


def test_only_first_setup_shows_advice_notice(window):
    assert ADVICE_NOTICE == "이 프로그램은 투자 자문이 아닙니다. AI 판단은 틀릴 수 있습니다."
    for screen_id in ScreenId:
        widget = screen_widget(window, screen_id)
        notice = widget.findChild(QLabel, "screenNotice")
        if screen_id is ScreenId.FIRST_SETUP:
            assert notice is not None
            assert notice.text() == ADVICE_NOTICE
            # 명세 2절 4번: 투자 자문 문구는 빈 화면 문구 밑에 있다.
            body = widget.findChild(QLabel, "screenBody")
            layout = widget.layout()
            assert layout.indexOf(body) < layout.indexOf(notice)
        else:
            assert notice is None


def test_first_screen_is_first_setup(window):
    assert current_screen(window) is ScreenId.FIRST_SETUP


def test_nav_lists_titles_in_order_and_switches_screen(window):
    nav = window.findChild(QListWidget, "screenNav")
    assert [nav.item(i).text() for i in range(nav.count())] == [
        "처음 설정",
        "현황",
        "보고서",
        "기록",
        "설정",
    ]
    for row, screen_id in enumerate(ScreenId):
        nav.setCurrentRow(row)
        assert current_screen(window) is screen_id
    select_screen(window, ScreenId.HISTORY)
    assert current_screen(window) is ScreenId.HISTORY


def test_status_line(window):
    expected = "바탕 버전 0.0.1 · 매매 기능 없음 · 자료 폴더 X"
    assert status_line_text(CONTEXT) == expected
    assert _text(window, "statusLine") == expected


def test_status_line_keeps_display_path_as_is(qapp):
    context = UiContext("1.2.3", "%LOCALAPPDATA%\\coin-report-trader")
    made = create_main_window(context)
    assert _text(made, "statusLine") == (
        "바탕 버전 1.2.3 · 매매 기능 없음 · 자료 폴더 %LOCALAPPDATA%\\coin-report-trader"
    )
    made.deleteLater()


def test_does_not_create_application_or_show(qapp):
    from PySide6.QtWidgets import QApplication

    before = QApplication.instance()
    made = create_main_window(CONTEXT)
    assert QApplication.instance() is before
    assert not made.isVisible()
    made.deleteLater()
