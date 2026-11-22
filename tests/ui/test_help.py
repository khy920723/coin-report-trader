"""도움말 창(M2 명세 3.16, 27번): 안내 문서를 읽어 보이고, 단추와 F1로 열린다.

안내 문서는 시험이 `tmp_path`에 만든 가짜 글이다. 저장소의 `docs/guide.md`는 자리만 확인한다.
"""

import logging
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QListWidget, QPushButton, QTextBrowser, QWidget

from coin_report_trader.core import ScreenId, UiContext
from coin_report_trader.ui import create_main_window
from coin_report_trader.ui.help import HelpWindow, guide_path, window_size

GUIDE = """# 가짜 안내

첫 글줄입니다. [누르는 주소](https://example.com/x) 와 `코드 글`.

## 꼭 읽을 것

1. 이 프로그램은 투자 자문이 아닙니다.

### 작은 제목

- 목록 하나
"""


@pytest.fixture
def guide(tmp_path: Path) -> Path:
    path = tmp_path / "guide.md"
    path.write_text(GUIDE, encoding="utf-8")
    return path


@pytest.fixture
def help_window(qapp: QApplication, guide: Path):
    made = HelpWindow(guide)
    yield made
    made.close()
    made.deleteLater()


def _text(window: HelpWindow) -> QTextBrowser:
    box = window.findChild(QTextBrowser, "helpText")
    assert box is not None
    return box


def _message(window: HelpWindow) -> QLabel:
    label = window.findChild(QLabel, "helpMessage")
    assert label is not None
    return label


# --- 창의 모습 ---


def test_the_window_is_a_non_modal_dialog_with_the_spec_names(help_window):
    assert help_window.objectName() == "helpWindow"
    assert help_window.windowTitle() == "도움말"
    assert not help_window.isModal()
    box = _text(help_window)
    assert box.isReadOnly()
    assert box.frameShape() == QTextBrowser.Shape.NoFrame
    assert box.document().documentMargin() == 24
    assert help_window.minimumSize().width() == 480
    assert help_window.minimumSize().height() == 360


def test_nothing_is_read_before_the_window_is_opened(qapp, guide, monkeypatch):
    reads: list[Path] = []
    original = Path.read_text

    def spy(self, *args, **kwargs):
        reads.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy)
    made = HelpWindow(guide)
    assert reads == []
    made.open_help()
    assert reads == [guide]
    made.close()
    made.deleteLater()


def test_the_first_size_is_720_by_600_or_ninety_percent_of_a_small_area():
    assert window_size(1920, 1040) == (720, 600)
    assert window_size(1000, 700) == (720, 600)
    assert window_size(700, 560) == (630, 504)
    assert window_size(500, 400) == (480, 360)  # 가장 작은 크기 아래로는 줄이지 않는다


# --- 글 ---


def test_the_guide_text_is_shown_with_the_heading_sizes(help_window):
    help_window.open_help()
    box = _text(help_window)
    assert not box.isHidden()
    assert _message(help_window).isHidden()
    plain = box.toPlainText()
    for expected in (
        "가짜 안내",
        "첫 글줄입니다.",
        "꼭 읽을 것",
        "이 프로그램은 투자 자문이 아닙니다.",
    ):
        assert expected in plain
    sizes = {}
    block = box.document().begin()
    while block.isValid():
        fragment = block.begin().fragment()
        sizes[block.text()] = (
            fragment.charFormat().font().pixelSize(),
            fragment.charFormat().font().bold(),
        )
        block = block.next()
    assert sizes["가짜 안내"] == (20, True)
    assert sizes["꼭 읽을 것"] == (14, True)
    assert sizes["작은 제목"] == (14, True)
    assert sizes["목록 하나"] == (14, False)


def test_the_second_level_heading_has_a_24px_space_above(help_window):
    help_window.open_help()
    block = _text(help_window).document().begin()
    margins = {}
    while block.isValid():
        margins[block.text()] = block.blockFormat().topMargin()
        block = block.next()
    assert margins["꼭 읽을 것"] == 24
    assert margins["작은 제목"] != 24


def test_the_reading_happens_every_time_the_window_is_opened(help_window, guide):
    help_window.open_help()
    assert "첫 글줄입니다." in _text(help_window).toPlainText()
    help_window.close()
    guide.write_text("# 바뀐 안내\n\n새 글입니다.\n", encoding="utf-8")
    help_window.open_help()
    plain = _text(help_window).toPlainText()
    assert "새 글입니다." in plain
    assert "첫 글줄입니다." not in plain


# --- 주소와 바깥 자원 ---


def test_a_click_on_an_address_in_the_text_opens_nothing(help_window, monkeypatch):
    opened: list[object] = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url) or True)
    help_window.open_help()
    QApplication.processEvents()
    box = _text(help_window)
    assert not box.openLinks()
    assert not box.openExternalLinks()
    cursor = QTextCursor(box.document())
    cursor.setPosition(box.toPlainText().index("누르는 주소") + 2)
    point = box.cursorRect(cursor).center()
    assert box.anchorAt(point) == "https://example.com/x"
    before = box.toPlainText()
    QTest.mouseClick(box.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(point))
    QApplication.processEvents()
    assert opened == []
    assert box.source().isEmpty()
    assert box.toPlainText() == before


def test_an_address_is_drawn_in_the_text_color_without_an_underline(help_window):
    help_window.open_help()
    block = _text(help_window).document().begin().next()
    seen = []
    iterator = block.begin()
    while not iterator.atEnd():
        fmt = iterator.fragment().charFormat()
        if fmt.isAnchor():
            seen.append((fmt.foreground().color().name(), fmt.fontUnderline()))
        iterator += 1
    assert seen == [("#eef5f1", False)]


def test_pictures_and_outside_files_are_not_loaded(help_window, tmp_path):
    picture = tmp_path / "x.png"
    picture.write_bytes(b"\x89PNG not really")
    box = _text(help_window)
    assert box.loadResource(2, picture.as_uri()) is None


# --- 못 읽음 ---


def test_a_missing_file_shows_the_unreadable_text_and_logs_without_the_path(qapp, tmp_path, caplog):
    missing = tmp_path / "nowhere" / "guide.md"
    made = HelpWindow(missing)
    with caplog.at_level(logging.WARNING):
        made.open_help()
    message = _message(made)
    assert not message.isHidden()
    assert message.text() == "안내 문서를 읽지 못했습니다."
    assert message.property("tone") == "warning"
    assert _text(made).isHidden()
    assert _text(made).toPlainText() == ""
    assert [record.levelno for record in caplog.records] == [logging.WARNING]
    assert str(tmp_path) not in caplog.text or "~" in caplog.text
    made.close()
    made.deleteLater()


def test_a_file_that_is_not_utf8_shows_the_unreadable_text(qapp, tmp_path):
    bad = tmp_path / "guide.md"
    bad.write_bytes(b"\xff\xfe\x00 not utf-8 \xc3\x28")
    made = HelpWindow(bad)
    made.open_help()
    assert _message(made).text() == "안내 문서를 읽지 못했습니다."
    assert _text(made).isHidden()
    made.close()
    made.deleteLater()


def test_the_text_comes_back_when_the_file_is_there_the_next_time(qapp, tmp_path):
    path = tmp_path / "guide.md"
    made = HelpWindow(path)
    made.open_help()
    assert not _message(made).isHidden()
    made.close()
    path.write_text("# 돌아온 안내\n", encoding="utf-8")
    made.open_help()
    assert _message(made).isHidden()
    assert "돌아온 안내" in _text(made).toPlainText()
    made.close()
    made.deleteLater()


# --- 열기, 닫기, 키보드 ---


def test_opening_again_while_open_keeps_the_same_window_and_does_not_reset_it(help_window, guide):
    help_window.open_help()
    help_window.resize(600, 500)
    guide.write_text("# 열린 동안 바뀜\n", encoding="utf-8")
    help_window.open_help()
    assert help_window.isVisible()
    assert help_window.size().width() == 600
    assert "첫 글줄입니다." in _text(help_window).toPlainText()  # 열려 있는 동안은 다시 읽지 않는다


def test_closing_and_opening_again_starts_from_the_first_size(help_window):
    help_window.open_help()
    help_window.resize(600, 500)
    help_window.close()
    assert not help_window.isVisible()
    help_window.open_help()
    expected = window_size(
        help_window.screen().availableGeometry().width(),
        help_window.screen().availableGeometry().height(),
    )
    assert (help_window.width(), help_window.height()) == expected


def test_escape_closes_the_window(help_window):
    help_window.open_help()
    QTest.keyClick(help_window, Qt.Key.Key_Escape)
    assert not help_window.isVisible()


def test_the_text_takes_the_focus_and_page_down_scrolls_it(qapp, tmp_path):
    long = tmp_path / "guide.md"
    body = "\n\n".join(f"{n}번째 글줄입니다." for n in range(200))
    long.write_text("# 긴 안내\n\n" + body, encoding="utf-8")
    made = HelpWindow(long)
    made.open_help()
    QApplication.processEvents()
    box = _text(made)
    bar = box.verticalScrollBar()
    assert bar.value() == 0
    box.setFocus()
    QTest.keyClick(box, Qt.Key.Key_PageDown)
    assert bar.value() > 0
    QTest.keyClick(box, Qt.Key.Key_End, Qt.KeyboardModifier.ControlModifier)
    QApplication.processEvents()
    assert bar.value() == bar.maximum()
    QTest.keyClick(box, Qt.Key.Key_Home, Qt.KeyboardModifier.ControlModifier)
    QApplication.processEvents()
    assert bar.value() == 0
    made.close()
    made.deleteLater()


# --- 안내 문서의 자리 ---


def test_the_guide_path_is_the_repository_guide_when_not_frozen():
    path = guide_path()
    assert path.parts[-2:] == ("docs", "guide.md")
    assert path.is_file()


def test_the_guide_path_is_next_to_the_exe_when_frozen(monkeypatch, tmp_path):
    exe = tmp_path / "coin-report-trader.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert guide_path() == tmp_path / "guide.md"


# --- 단추와 F1 ---


@pytest.fixture
def window(qapp, guide: Path, monkeypatch):
    monkeypatch.setattr("coin_report_trader.ui.main_window.guide_path", lambda: guide)
    made = create_main_window(UiContext("0.0.1", "X"))
    made.resize(1100, 800)
    made.show()
    yield made
    for child in made.findChildren(HelpWindow):
        child.close()
    made.close()
    made.deleteLater()


def _helps(window) -> list[HelpWindow]:
    return window.findChildren(HelpWindow)


def test_the_help_button_sits_below_the_screen_list_and_is_not_one_of_its_lines(window):
    nav = window.findChild(QListWidget, "screenNav")
    button = window.findChild(QPushButton, "helpButton")
    left = window.findChild(QWidget, "screenLeft")
    assert button is not None
    assert left is not None
    assert button.text() == "도움말"
    assert nav.count() == len(ScreenId) == 5
    assert nav.parentWidget() is left
    assert button.parentWidget() is left
    assert button.mapTo(window, QPoint(0, 0)).y() > nav.mapTo(window, QPoint(0, 0)).y()
    assert button.x() == nav.x() or abs(button.mapTo(left, QPoint(0, 0)).x()) <= 1


def test_the_button_opens_the_window_with_the_guide_text(window):
    assert _helps(window) == []
    window.findChild(QPushButton, "helpButton").click()
    (help_window,) = _helps(window)
    assert help_window.isVisible()
    assert "첫 글줄입니다." in _text(help_window).toPlainText()


def test_f1_opens_the_same_window_and_the_button_again_does_not_make_another(window):
    shortcuts = [s for s in window.findChildren(QShortcut) if s.key() == QKeySequence("F1")]
    assert len(shortcuts) == 1
    shortcuts[0].activated.emit()
    (first,) = _helps(window)
    window.findChild(QPushButton, "helpButton").click()
    shortcuts[0].activated.emit()
    assert _helps(window) == [first]
    assert first.isVisible()


def test_the_window_opens_before_the_first_setup_too(window):
    # 설정을 보내기 전(처음 설정 전)의 모습에서도 열린다.
    window.findChild(QPushButton, "helpButton").click()
    assert _helps(window)[0].isVisible()
    assert _message(_helps(window)[0]).isHidden()


def test_the_window_shows_the_unreadable_text_when_the_guide_is_gone(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "coin_report_trader.ui.main_window.guide_path", lambda: tmp_path / "none" / "guide.md"
    )
    made = create_main_window(UiContext("0.0.1", "X"))
    made.show()
    made.findChild(QPushButton, "helpButton").click()
    (help_window,) = made.findChildren(HelpWindow)
    assert _message(help_window).text() == "안내 문서를 읽지 못했습니다."
    help_window.close()
    made.close()
    made.deleteLater()


def test_ctrl_number_shortcuts_still_open_the_five_screens(window):
    keys = sorted(s.key().toString() for s in window.findChildren(QShortcut))
    assert keys == ["Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4", "Ctrl+5", "F1"]
