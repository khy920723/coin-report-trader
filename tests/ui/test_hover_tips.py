"""'ⓘ'와 봉 개수 단추에 마우스를 올리거나 누르면 툴팁이 보이는지(고친 판 v0.1.3).

다섯 화면과 알림센터의 'ⓘ'마다 창 위에서 그 자리로 마우스를 옮기고(`QTest.mouseMove`), 거기의
위젯이 그 'ⓘ'인지(`childAt`), 마우스가 올라 있다고 알려지는지(`underMouse`), 툴팁 부름
(`QHelpEvent`)이 그 글을 보이는지 본다. 툴팁이 뜨는 때를 재는 타이머에는 기대지 않는다.
값은 모두 시험이 만든 값이다.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QHelpEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QRadioButton, QScrollArea, QToolTip, QWidget

from coin_report_trader.core import (
    AlertCategory,
    BarStats,
    CandleUnit,
    CoinView,
    LoadState,
    Market,
    MarketsView,
    ScreenId,
    SettingsView,
    UiContext,
    WebhookView,
)
from coin_report_trader.ui import create_main_window, screen_widget, select_screen
from coin_report_trader.ui.info_icon import InfoIcon, tooltip_html

FETCHED_AT = datetime(2026, 1, 2, 1, 30, tzinfo=UTC)
MARKETS = tuple(Market(f"KRW-C{i}", f"가짜{i}", caution=i % 5 == 0) for i in range(40))


@pytest.fixture
def window(qapp):
    made = create_main_window(UiContext("0.0.1", "X"))
    made.resize(1100, 1000)
    made.show()
    QApplication.processEvents()
    yield made
    QToolTip.hideText()
    made.close()
    made.deleteLater()


def _stats(market: str) -> BarStats:
    return BarStats(
        market=market,
        unit=CandleUnit.MINUTE_60,
        requested_count=5,
        bar_count=5,
        first_start_utc=datetime(2026, 1, 1, 1, 0, tzinfo=UTC),
        last_start_utc=datetime(2026, 1, 1, 5, 0, tzinfo=UTC),
        close_last=Decimal("132"),
        change=Decimal("0.3200"),
        slope=Decimal("0.0481"),
        vs_mean=Decimal("0.1538"),
        up_ratio=Decimal("0.6000"),
        volatility=Decimal("0.1535"),
        max_drawdown=Decimal("-0.1000"),
        from_high=Decimal("0.0000"),
        high=Decimal("132"),
        low=Decimal("99"),
    )


def _done(window) -> None:
    """처음 설정을 마친 모습(코인 두 개, 현황의 값, 알림센터의 주소 없음)."""
    window.apply(SettingsView(("KRW-C1", "KRW-C2"), 30, True, candle_unit=CandleUnit.MINUTE_60))
    window.apply(MarketsView(LoadState.READY, MARKETS))
    for market in ("KRW-C1", "KRW-C2"):
        window.apply(
            CoinView(market, LoadState.READY, stats=_stats(market), fetched_at=FETCHED_AT)
        )
    window.apply(WebhookView(AlertCategory.TRADE, saved=False))


def _before_first_setup(window) -> None:
    window.apply(SettingsView())
    window.apply(MarketsView(LoadState.READY, MARKETS))
    window.apply(WebhookView(AlertCategory.TRADE, saved=False))


STATES = {
    "before_first_setup": _before_first_setup,
    "done": _done,
}


def _scroll_to(widget: QWidget) -> None:
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QScrollArea) and parent.widget() is not None:
            if parent.widget().isAncestorOf(widget):
                parent.ensureWidgetVisible(widget)
        parent = parent.parentWidget()
    QApplication.processEvents()


def _hover(window, widget: QWidget) -> QPoint:
    """창 위에서 `widget` 한가운데로 마우스를 옮기고 그 자리(창 기준)를 돌려준다."""
    _scroll_to(widget)
    point = widget.mapTo(window, widget.rect().center())
    QTest.mouseMove(window, QPoint(3, 3))
    QTest.mouseMove(window, point)
    QApplication.processEvents()
    return point


def _ask_tooltip(widget: QWidget, point_in_widget: QPoint) -> None:
    QToolTip.hideText()
    event = QHelpEvent(QEvent.Type.ToolTip, point_in_widget, widget.mapToGlobal(point_in_widget))
    QApplication.sendEvent(widget, event)


def _icons(window, screen_id: ScreenId) -> list[InfoIcon]:
    select_screen(window, screen_id)
    QApplication.processEvents()
    return [
        icon for icon in screen_widget(window, screen_id).findChildren(InfoIcon) if icon.isVisible()
    ]


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("screen_id", list(ScreenId))
def test_hovering_every_visible_icon_shows_its_own_text(window, state, screen_id):
    STATES[state](window)
    icons = _icons(window, screen_id)
    for icon in icons:
        point = _hover(window, icon)
        # 그 자리에 다른 위젯이 덮이지 않는다.
        assert window.childAt(point) is icon, icon.objectName()
        assert icon.underMouse(), icon.objectName()
        _ask_tooltip(icon, icon.rect().center())
        assert QToolTip.text() == tooltip_html(icon.toolTip()), icon.objectName()
        assert icon.toolTip() == icon.accessibleDescription()


def test_the_icons_of_all_screens_are_covered_by_the_hover_test(window):
    _done(window)
    names = set()
    for screen_id in ScreenId:
        names |= {icon.objectName() for icon in _icons(window, screen_id)}
    assert {
        "settingsCandleNote",
        "settingsRetentionInfo",
        "statusCoinDisclaimer_KRW-C1",
        "statusStatsDisclaimer",
        "alertWebhookInfo",
    } <= names
    _before_first_setup_names = set()
    before = create_main_window(UiContext("0.0.1", "X"))
    before.resize(1100, 1000)
    before.show()
    _before_first_setup(before)
    _before_first_setup_names |= {i.objectName() for i in _icons(before, ScreenId.FIRST_SETUP)}
    before.close()
    before.deleteLater()
    assert {
        "firstSetupCandleNote",
        "firstSetupRetentionInfo",
        "firstSetupNoticesInfo",
    } <= _before_first_setup_names


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("screen_id", [ScreenId.FIRST_SETUP, ScreenId.SETTINGS])
def test_hovering_a_count_button_shows_its_tooltip(window, state, screen_id):
    STATES[state](window)
    select_screen(window, screen_id)
    QApplication.processEvents()
    screen = screen_widget(window, screen_id)
    radios = [
        radio
        for radio in screen.findChildren(QRadioButton)
        if "Count_" in radio.objectName() and radio.isVisible()
    ]
    if not radios:  # 처음 설정을 마친 뒤의 처음 설정에는 고르기 칸이 없다
        assert screen_id is ScreenId.FIRST_SETUP
        return
    assert len(radios) == 3
    for radio in radios:
        point = _hover(window, radio)
        assert window.childAt(point) is radio, radio.objectName()
        assert radio.underMouse(), radio.objectName()
        _ask_tooltip(radio, radio.rect().center())
        assert QToolTip.text() == radio.toolTip()
        assert radio.toolTip().startswith("예상 적중률(참고값)")


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("screen_id", list(ScreenId))
def test_pressing_an_icon_shows_the_same_tooltip(window, state, screen_id):
    STATES[state](window)
    for icon in _icons(window, screen_id):
        point = _hover(window, icon)
        QToolTip.hideText()
        # 창에 눌러서(창 안의 위젯을 알아내는 길로) 그 자리의 위젯이 받게 한다.
        QTest.mouseClick(
            window.windowHandle(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point
        )
        assert QToolTip.text() == tooltip_html(icon.toolTip()), icon.objectName()
        assert icon.hasFocus(), icon.objectName()


def test_the_settings_icons_work_after_the_form_was_dim_and_became_editable(window):
    _before_first_setup(window)
    dim = _icons(window, ScreenId.SETTINGS)
    assert dim
    _done(window)
    for icon in _icons(window, ScreenId.SETTINGS):
        assert icon.isEnabled()
        point = _hover(window, icon)
        assert window.childAt(point) is icon
        assert icon.underMouse()
        _ask_tooltip(icon, icon.rect().center())
        assert QToolTip.text() == tooltip_html(icon.toolTip())


def test_the_settings_icons_work_after_resizing_between_one_and_two_columns(window):
    _done(window)
    for size in ((700, 1000), (1500, 1000), (1100, 1000)):
        window.resize(*size)
        QApplication.processEvents()
        for icon in _icons(window, ScreenId.SETTINGS):
            point = _hover(window, icon)
            assert window.childAt(point) is icon, (size, icon.objectName())
            assert icon.underMouse(), (size, icon.objectName())


# --- 흐린 모습에서도 'ⓘ'는 듣는다 ---


def _unreadable(window) -> None:
    window.apply(SettingsView(state=LoadState.FAILED))


@pytest.mark.parametrize(
    ("screen_id", "prepare"),
    [
        # 처음 설정은 설정을 읽지 못했을 때만 흐리다(처음 설정 전에는 고르는 화면이다).
        (ScreenId.FIRST_SETUP, _unreadable),
        (ScreenId.SETTINGS, lambda w: None),
        (ScreenId.SETTINGS, _before_first_setup),
        (ScreenId.SETTINGS, _unreadable),
    ],
    ids=["first_setup-unreadable", "settings-plain", "settings-before", "settings-unreadable"],
)
def test_a_dim_form_keeps_its_icons_alive_and_dims_the_controls(window, screen_id, prepare):
    prepare(window)
    prefix = "firstSetup" if screen_id is ScreenId.FIRST_SETUP else "settings"
    screen = _icons(window, screen_id)
    assert {icon.objectName() for icon in screen} >= {
        f"{prefix}CandleNote",
        f"{prefix}RetentionInfo",
    }
    for icon in screen:
        assert icon.isEnabled(), icon.objectName()
        assert icon.focusPolicy() & Qt.FocusPolicy.TabFocus
    root = screen_widget(window, screen_id)
    for radio in root.findChildren(QRadioButton):
        assert not radio.isEnabled(), radio.objectName()  # 칸은 흐리다
    assert not root.findChild(QWidget, f"{prefix}Coins").isEnabled()


@pytest.mark.parametrize("key", [Qt.Key.Key_Space, Qt.Key.Key_Return])
def test_a_dim_settings_icon_takes_the_keyboard_and_opens_its_tooltip(window, key):
    _before_first_setup(window)
    icon = _icons(window, ScreenId.SETTINGS)[0]
    QToolTip.hideText()
    icon.setFocus()
    assert icon.hasFocus()
    QTest.keyClick(icon, key)
    assert QToolTip.text() == tooltip_html(icon.toolTip())


def test_a_legacy_first_setup_keeps_the_retention_icon_alive(window):
    window.apply(SettingsView(("KRW-C1",), 30, False))  # 예전 기록: 코인이 있고 봉 단위가 없다
    window.apply(MarketsView(LoadState.READY, MARKETS))
    first = screen_widget(window, ScreenId.FIRST_SETUP)
    assert not first.findChild(QWidget, "firstSetupLegacyNotice").isHidden()
    info = first.findChild(InfoIcon, "firstSetupRetentionInfo")
    assert info is not None
    assert info.isEnabled()
    assert not first.findChild(QWidget, "firstSetupRetention7").isEnabled()  # 잠긴 칸
    assert first.findChild(QWidget, "firstSetupUnit_minute60").isEnabled()  # 고를 수 있는 칸
