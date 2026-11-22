"""창과 다섯 화면의 자리(M0 명세 7절 A5, M1 명세 3.1의 바뀐 약속)."""

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
from coin_report_trader.ui.info_icon import InfoIcon

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


def test_each_screen_shows_its_title(window):
    for screen_id in ScreenId:
        widget = screen_widget(window, screen_id)
        assert _text(widget, "screenTitle") == SCREEN_TITLES[screen_id]


def test_only_unfilled_screens_show_the_empty_body(window):
    assert EMPTY_BODY == "이 화면은 아직 비어 있습니다."
    for screen_id in ScreenId:
        widget = screen_widget(window, screen_id)
        body = widget.findChild(QLabel, "screenBody")
        # M1 명세 3.1: 처음 설정, 현황, 설정은 채워져 빈 글이 없다. 보고서, 기록은 그대로다.
        if screen_id in (ScreenId.FIRST_SETUP, ScreenId.STATUS, ScreenId.SETTINGS):
            assert body is None
        else:
            assert body is not None
            assert body.text() == EMPTY_BODY


def test_only_first_setup_shows_advice_notice(window):
    assert ADVICE_NOTICE == "이 프로그램은 투자 자문이 아닙니다. AI 판단은 틀릴 수 있습니다."
    for screen_id in ScreenId:
        widget = screen_widget(window, screen_id)
        # 늘 보이는 글 라벨은 없고, 처음 설정의 '꼭 읽을 글' 제목 옆 'ⓘ'의 툴팁이 가장 앞에 둔다
        # (M1 명세 3.14.6). 10 자리의 글은 가(투자 자문), 마, 바 순서다(설계서 10.2).
        assert widget.findChild(QLabel, "screenNotice") is None
        info = widget.findChild(InfoIcon, "firstSetupNoticesInfo")
        if screen_id is ScreenId.FIRST_SETUP:
            assert info is not None
            lines = info.toolTip().split("\n")
            assert lines[0] == f"• {ADVICE_NOTICE}"
            assert len(lines) == 3
            assert info.accessibleDescription() == info.toolTip()
            assert info.accessibleName() == "꼭 읽을 글 설명"
        else:
            assert info is None


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
    expected = "판 v0.0.1 · 매매 기능 없음 · 자료 폴더 X"
    assert status_line_text(CONTEXT) == expected
    assert _text(window, "statusLine") == expected


def test_status_line_keeps_display_path_as_is(qapp):
    context = UiContext("1.2.3", "%LOCALAPPDATA%\\coin-report-trader")
    made = create_main_window(context)
    assert _text(made, "statusLine") == (
        "판 v1.2.3 · 매매 기능 없음 · 자료 폴더 %LOCALAPPDATA%\\coin-report-trader"
    )
    made.deleteLater()


def test_status_line_writes_the_version_with_a_v_and_never_the_old_base_text(qapp):
    """M1 명세 3.15: "판 v{판} · …"의 {판}은 `UiContext.version` 그대로이고 앞에 `v`만 붙는다."""
    for version in ("0.1.0", "12.34.56"):
        context = UiContext(version, "경로")
        expected = f"판 v{version} · 매매 기능 없음 · 자료 폴더 경로"
        assert status_line_text(context) == expected
        made = create_main_window(context)
        assert _text(made, "statusLine") == expected
        assert "바탕 버전" not in _text(made, "statusLine")
        made.deleteLater()


def test_does_not_create_application_or_show(qapp):
    from PySide6.QtWidgets import QApplication

    before = QApplication.instance()
    made = create_main_window(CONTEXT)
    assert QApplication.instance() is before
    assert not made.isVisible()
    made.deleteLater()
