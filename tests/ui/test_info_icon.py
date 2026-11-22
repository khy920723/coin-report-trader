"""'ⓘ' 아이콘과 툴팁(M1 명세 3.14.7, 화면 설계서 5-0 판 2의 11장)."""

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QToolTip, QWidget

from coin_report_trader.ui import theme
from coin_report_trader.ui.info_icon import BULLET, InfoIcon, info_text, tooltip_html

TIP = "봉 통계를 셈할 봉의 단위와 개수입니다."


@pytest.fixture
def host(qapp):
    widget = QWidget()
    widget.resize(200, 80)
    yield widget
    QToolTip.hideText()
    widget.close()
    widget.deleteLater()


def _icon(host: QWidget, text: str = TIP, title: str = "봉 단위*") -> InfoIcon:
    icon = InfoIcon(title, text, "someInfo", host)
    icon.move(10, 10)
    host.show()
    icon.setFocus()
    QTest.qWait(0)
    return icon


def test_the_icon_is_16_by_16_and_in_the_tab_order(host):
    icon = _icon(host)
    assert (icon.width(), icon.height()) == (16, 16)
    assert icon.focusPolicy() & Qt.FocusPolicy.TabFocus
    assert icon.objectName() == "someInfo"


def test_the_tooltip_and_the_screen_reader_description_are_the_same_text(host):
    icon = _icon(host)
    assert icon.toolTip() == icon.accessibleDescription() == TIP
    assert icon.accessibleName() == "봉 단위* 설명"
    icon.set_info("바뀐 글", "다른 제목")
    assert icon.toolTip() == icon.accessibleDescription() == "바뀐 글"
    assert icon.accessibleName() == "다른 제목 설명"


def test_two_or_more_texts_are_lines_that_start_with_a_bullet():
    assert info_text("하나") == "하나"
    assert info_text("가", "나", "다") == "• 가\n• 나\n• 다"
    assert BULLET == "• "


@pytest.mark.parametrize("key", [Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_space_and_enter_open_the_tooltip_and_escape_closes_it(host, key):
    icon = _icon(host)
    QToolTip.hideText()
    assert not QToolTip.isVisible()
    QTest.keyClick(icon, key)
    assert QToolTip.isVisible()
    assert QToolTip.text() == tooltip_html(TIP)
    QTest.keyClick(icon, Qt.Key.Key_Escape)
    assert not QToolTip.isVisible()


def test_clicking_opens_the_same_tooltip(host):
    icon = _icon(host)
    QToolTip.hideText()
    QTest.mouseClick(icon, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
    assert QToolTip.isVisible()
    assert QToolTip.text() == tooltip_html(TIP)


def test_other_keys_do_not_open_the_tooltip(host):
    icon = _icon(host)
    QToolTip.hideText()
    QTest.keyClick(icon, Qt.Key.Key_A)
    assert not QToolTip.isVisible()


def test_the_tooltip_is_at_most_320px_wide_and_never_cut(qapp):
    long_text = "가나다라마바사 " * 40
    html = tooltip_html(long_text)
    assert f'width="{theme.TOOLTIP_WIDTH}"' in html
    assert long_text.strip() in html.replace("<br>", "")
    short = tooltip_html("짧은 글")
    width = int(short.split('width="')[1].split('"')[0])
    assert width < theme.TOOLTIP_WIDTH
    lines = tooltip_html("가\n나")
    assert "<br>" in lines
    # 글에 든 꺾쇠와 앰퍼샌드는 글자 그대로 보인다.
    assert "&lt;b&gt;" in tooltip_html("<b>")


def test_the_icon_is_drawn_in_the_sub_color_and_lit_when_focused(host):
    icon = _icon(host)
    icon.clearFocus()
    QTest.qWait(0)
    sub = icon.grab().toImage()
    assert not sub.isNull()
    icon.setFocus()
    QTest.qWait(0)
    lit = icon.grab().toImage()
    assert lit != sub  # 초점이 있으면 글 색과 초점 테두리로 바뀐다
    assert isinstance(QPixmap.fromImage(lit), QPixmap)
