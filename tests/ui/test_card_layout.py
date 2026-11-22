"""카드 안의 놓임(고친 판 v0.1.3): 오른쪽 카드는 내용을 위에 붙이고, 짧은 제목은 한 줄이다.

값은 모두 시험이 만든 값이다. 창을 실제로 띄워(`show`) 크기가 정해진 뒤의 위젯을 잰다.
"""

import pytest
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QWidget,
)

from coin_report_trader.core import (
    AlertCategory,
    CandleUnit,
    LoadState,
    Market,
    MarketsView,
    ScreenId,
    SettingsView,
    UiContext,
    WebhookView,
)
from coin_report_trader.ui import create_main_window, screen_widget, select_screen
from coin_report_trader.ui.cards import Card
from coin_report_trader.ui.widgets import LineLabel, make_line_label

MARKETS = tuple(Market(f"KRW-C{i}", f"가짜{i}", caution=False) for i in range(60))


@pytest.fixture
def window(qapp):
    made = create_main_window(UiContext("0.0.1", "X"))
    made.resize(1100, 1000)
    made.show()
    yield made
    made.close()
    made.deleteLater()


def _done(window) -> None:
    window.apply(SettingsView(("KRW-C1",), 30, True, candle_unit=CandleUnit.MINUTE_60))
    window.apply(MarketsView(LoadState.READY, MARKETS))
    window.apply(WebhookView(AlertCategory.TRADE, saved=False))
    QApplication.processEvents()


def _before_first_setup(window) -> None:
    window.apply(SettingsView())
    window.apply(MarketsView(LoadState.READY, MARKETS))
    QApplication.processEvents()


# --- 설정 화면 오른쪽의 두 카드 ---


@pytest.mark.parametrize("prefix", ["settings", "firstSetup"])
def test_the_right_hand_cards_keep_their_content_at_the_top(window, prefix):
    screen_id = ScreenId.SETTINGS if prefix == "settings" else ScreenId.FIRST_SETUP
    if prefix == "settings":
        _done(window)
    else:
        _before_first_setup(window)
    select_screen(window, screen_id)
    QApplication.processEvents()
    screen = screen_widget(window, screen_id)
    coins = screen.findChild(QWidget, f"{prefix}Coins")
    assert coins is not None
    # 오른쪽 열에 카드가 여럿이라(M2, M3) 남는 높이가 없다. 이 시험은 남는 높이가 있는 모습(v0.1.3의
    # 고장이 나던 때)을 만들려고 시험할 카드 말고 오른쪽 카드를 숨긴다.
    cards = [c for c in screen.findChildren(Card) if not c.isAncestorOf(coins)]
    for name in (f"{prefix}Candle", f"{prefix}Retention"):
        content = screen.findChild(QWidget, name)
        assert content is not None
        card = content.parentWidget()
        assert isinstance(card, Card)
        for other in cards:
            other.setVisible(other is card)
        QApplication.processEvents()
        # 카드가 내용보다 훨씬 높아도(왼쪽 코인 카드가 길어서) 내용은 제 높이 그대로다.
        assert card.height() > content.sizeHint().height() + 40, name
        assert content.height() == content.sizeHint().height(), name
        # 카드의 내용은 위쪽(안쪽 여백 바로 아래)에 붙는다. 첫 줄은 내용 자신이거나 제목 줄이다.
        first = card.layout().itemAt(0)
        top = first.geometry().y() if first.widget() is None else first.widget().y()
        assert top == card.layout().contentsMargins().top(), name
    for other in cards:
        other.show()


def test_the_content_of_a_right_hand_card_does_not_spread_when_the_window_grows(window):
    _done(window)
    select_screen(window, ScreenId.SETTINGS)
    screen = screen_widget(window, ScreenId.SETTINGS)
    candle = screen.findChild(QWidget, "settingsCandle")
    assert candle is not None
    heights = []
    for height in (800, 1000, 1400):
        window.resize(1100, height)
        QApplication.processEvents()
        heights.append(candle.height())
    assert heights == [candle.sizeHint().height()] * 3


# --- 한 줄로 놓이는 제목 ---


def _in_row(label: QLabel, width: int) -> QWidget:
    host = QWidget()
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(label)
    row.addStretch(1)
    host.resize(width, 200)
    host.show()
    QApplication.processEvents()
    return host


TITLE = "매매 알림 디스코드 웹후크 주소"


def _in_row(label: QLabel, width: int) -> QWidget:
    """제목 줄처럼 라벨 하나와 늘어나는 빈 자리가 한 줄에 놓인 틀."""
    host = QWidget()
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(label)
    row.addStretch(1)
    host.resize(width, 200)
    host.show()
    QApplication.processEvents()
    return host


def test_a_line_label_is_one_line_when_there_is_room_and_wraps_only_when_not(qapp):
    label = make_line_label(TITLE, "t")
    assert isinstance(label, LineLabel)
    one_line = label.fontMetrics().lineSpacing()
    host = _in_row(label, 600)
    assert label.width() >= label.fontMetrics().horizontalAdvance(TITLE)
    assert label.heightForWidth(label.width()) <= one_line + 4
    host.resize(label.fontMetrics().horizontalAdvance(TITLE) // 2, 200)
    QApplication.processEvents()
    assert label.heightForWidth(label.width()) >= 2 * one_line - 4
    assert label.text() == TITLE  # 글은 그대로다
    host.close()


def test_the_line_label_prefers_the_width_of_the_whole_text_and_one_line_of_height(qapp):
    label = make_line_label(TITLE, "t")
    hint = label.sizeHint()
    assert hint.width() >= label.fontMetrics().horizontalAdvance(TITLE)
    assert hint.height() <= label.fontMetrics().lineSpacing() + 4
    assert make_line_label("", "e").sizeHint().height() >= 0
    # 줄바꿈이 든 글은 `QLabel`의 바라는 크기 그대로다.
    two_lines = make_line_label("가\n나", "n")
    assert two_lines.sizeHint().height() >= 2 * two_lines.fontMetrics().lineSpacing() - 2


# --- 알림센터의 주소 칸 이름 ---


@pytest.mark.parametrize("category", list(AlertCategory))
def test_the_webhook_title_is_one_line_when_the_window_has_room(window, category):
    _done(window)
    window.apply(WebhookView(category, saved=False))
    select_screen(window, ScreenId.STATUS)
    screen = screen_widget(window, ScreenId.STATUS)
    tab = screen.findChild(QWidget, f"alertTab_{category.value}")
    assert tab is not None
    tab.click()
    QApplication.processEvents()
    title = screen.findChild(QLabel, "alertWebhookTitle")
    assert title is not None
    assert title.text().endswith("디스코드 웹후크 주소")
    assert title.height() <= title.fontMetrics().lineSpacing() + 4, title.text()


def test_the_webhook_title_wraps_only_when_the_card_is_too_narrow(window):
    _done(window)
    select_screen(window, ScreenId.STATUS)
    window.resize(1024, 800)
    QApplication.processEvents()
    screen = screen_widget(window, ScreenId.STATUS)
    title = screen.findChild(QLabel, "alertWebhookTitle")
    assert title is not None
    assert title.height() <= title.fontMetrics().lineSpacing() + 4
    # 라벨이 글보다 좁게 놓이면 낱말 사이에서 줄을 바꾸고 글은 잘리지 않는다.
    assert title.minimumSizeHint().width() < title.fontMetrics().horizontalAdvance(title.text())
