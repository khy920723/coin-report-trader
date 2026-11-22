"""새 UI 기준의 화면(M1 명세 3.14.6, 3.14.7. 화면 설계서 5-0~5-4): 옮긴 안내 글, 카드, 단추의
모양, 오름·내림 색, 화면 이름 목록의 아이콘.

값은 모두 시험이 만든 값이다. 창을 띄우지 않으므로 보이는지는 `isHidden()`으로 본다.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QListWidget,
    QPushButton,
    QWidget,
)

from coin_report_trader.core import (
    AlertCategory,
    BarStats,
    CandleUnit,
    CoinView,
    LoadState,
    SaveResult,
    ScreenId,
    SettingsView,
    UiContext,
    WebhookView,
)
from coin_report_trader.ui import (
    create_main_window,
    screen_widget,
    select_screen,
    theme,
)
from coin_report_trader.ui.cards import MIN_CARD_WIDTH, Card, CardGrid
from coin_report_trader.ui.info_icon import InfoIcon

ADVICE = "이 프로그램은 투자 자문이 아닙니다. AI 판단은 틀릴 수 있습니다."
CANDLE_NOTE = "봉 통계를 셈할 봉의 단위와 개수입니다."
KEEP = "생긴 때부터 고른 기간이 지난 알림은 영구히 지워집니다."
SHORTEN = "기간을 줄이면 새 기간이 지난 알림도 지워집니다."
DISCLAIMER = "봉 통계는 계산 결과일 뿐이고, 이득을 보장하지 않습니다."
NOT_SET = "디스코드 주소를 넣지 않은 분류의 알림은 디스코드로 가지 않습니다."
OFFLINE_START = "프로그램이나 PC가 꺼져 있거나 인터넷이 끊기면 디스코드 알림도 가지 않습니다."
FETCHED_AT = datetime(2026, 1, 2, 1, 30, tzinfo=UTC)
MOVED = (ADVICE, CANDLE_NOTE, KEEP, SHORTEN, DISCLAIMER, NOT_SET)


@pytest.fixture
def window(qapp: QApplication):
    made = create_main_window(UiContext("0.0.1", "X"))
    yield made
    made.close()
    made.deleteLater()


def _stats(market: str, **changes) -> BarStats:
    values = {
        "market": market,
        "unit": CandleUnit.MINUTE_60,
        "requested_count": 5,
        "bar_count": 5,
        "first_start_utc": datetime(2026, 1, 1, 1, 0, tzinfo=UTC),
        "last_start_utc": datetime(2026, 1, 1, 5, 0, tzinfo=UTC),
        "close_last": Decimal("132"),
        "change": Decimal("0.3200"),
        "slope": Decimal("-0.0481"),
        "vs_mean": Decimal("0"),
        "up_ratio": Decimal("0.6000"),
        "volatility": Decimal("0.1535"),
        "max_drawdown": Decimal("-0.1000"),
        "from_high": Decimal("0.0000"),
        "high": Decimal("132"),
        "low": Decimal("99"),
    }
    values.update(changes)
    return BarStats(**values)


def _filled(window):
    window.apply(SettingsView(("KRW-AAA", "KRW-BBB"), 30, True, candle_unit=CandleUnit.MINUTE_60))
    for market in ("KRW-AAA", "KRW-BBB"):
        window.apply(
            CoinView(market, LoadState.READY, stats=_stats(market), fetched_at=FETCHED_AT)
        )
    window.apply(WebhookView(AlertCategory.TRADE, saved=False))
    return window


def _visible_label_texts(window) -> list[str]:
    return [label.text() for label in window.findChildren(QLabel)]


# --- 옮긴 안내 글(3.14.6) ---


def test_moved_texts_are_in_no_label_and_are_in_an_info_icon(window):
    _filled(window)
    labels = _visible_label_texts(window)
    tips = "\n".join(icon.toolTip() for icon in window.findChildren(InfoIcon))
    for text in MOVED:
        assert text not in labels, text
        assert text in tips, text
    assert OFFLINE_START in tips
    assert not any(text.startswith(OFFLINE_START) for text in labels)


def test_every_info_icon_has_the_tooltip_as_its_screen_reader_description(window):
    _filled(window)
    icons = window.findChildren(InfoIcon)
    names = {icon.objectName() for icon in icons}
    assert {
        "firstSetupCandleNote",
        "settingsCandleNote",
        "firstSetupRetentionInfo",
        "settingsRetentionInfo",
        "firstSetupNoticesInfo",
        "statusStatsDisclaimer",
        "statusCoinDisclaimer_KRW-AAA",
        "statusCoinDisclaimer_KRW-BBB",
        "alertWebhookInfo",
    } <= names
    for icon in icons:
        assert icon.toolTip()
        assert icon.accessibleDescription() == icon.toolTip()
        assert icon.accessibleName().endswith(" 설명")
        assert icon.focusPolicy() & Qt.FocusPolicy.TabFocus
        assert (icon.width(), icon.height()) == (16, 16)


def _row_names_with(parent: QWidget, name: str) -> list[str]:
    """`name` 위젯이 든 줄(레이아웃)의 위젯 이름을 놓인 차례대로."""
    outer = parent.layout()
    assert outer is not None
    for index in range(outer.count()):
        row = outer.itemAt(index).layout()
        if row is None:
            continue
        names = [
            row.itemAt(i).widget().objectName()
            for i in range(row.count())
            if row.itemAt(i).widget() is not None
        ]
        if name in names:
            return names
    raise AssertionError(name)


def test_the_icons_sit_right_after_their_titles(window):
    first = screen_widget(window, ScreenId.FIRST_SETUP)
    for title, info in (
        ("firstSetupUnitTitle", "firstSetupCandleNote"),
        ("firstSetupRetentionTitle", "firstSetupRetentionInfo"),
        ("firstSetupNoticesTitle", "firstSetupNoticesInfo"),
    ):
        label = first.findChild(QLabel, title)
        assert label is not None
        names = _row_names_with(label.parentWidget(), title)
        assert names[names.index(title) + 1] == info
    icon = first.findChild(InfoIcon, "firstSetupNoticesInfo")
    assert icon is not None
    assert icon.accessibleName() == "꼭 읽을 글 설명"


def test_the_webhook_icon_shows_only_while_the_address_is_not_set(window):
    _filled(window)
    status = screen_widget(window, ScreenId.STATUS)
    icon = status.findChild(InfoIcon, "alertWebhookInfo")
    assert icon is not None
    assert not icon.isHidden()
    assert icon.toolTip() == NOT_SET
    window.apply(WebhookView(AlertCategory.TRADE, saved=True))
    assert icon.isHidden()
    window.apply(WebhookView(AlertCategory.TRADE, saved=False))
    assert not icon.isHidden()
    # 칸의 "넣지 않음"은 늘 보인다.
    edit = status.findChild(QWidget, "alertWebhookInput")
    assert edit is not None
    assert edit.placeholderText() == "넣지 않음"


# --- 카드와 늘어놓기 ---


def test_a_card_has_the_surface_look_and_the_design_padding(qapp):
    card = Card("c")
    assert card.property(theme.CARD_PROPERTY) is True
    margins = card.body.contentsMargins()
    assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (16,) * 4
    assert f'QFrame[{theme.CARD_PROPERTY}="true"]' in theme.app_stylesheet()


def _grid(count: int, width: int, *, split_first=False, max_columns=3) -> CardGrid:
    grid = CardGrid(split_first=split_first, max_columns=max_columns)
    cards = [Card(f"card{i}") for i in range(count)]
    for card in cards:
        card.body.addWidget(QLabel("글"))
    grid.set_cards(cards)
    grid.resize(width, 200)
    grid.show()
    QApplication.processEvents()
    return grid


def _positions(grid: CardGrid) -> list[tuple[int, int]]:
    layout = grid.layout()
    return [layout.getItemPosition(i)[:2] for i in range(layout.count())]


@pytest.mark.parametrize(
    ("width", "columns"),
    [
        (MIN_CARD_WIDTH - 1, 1),
        (MIN_CARD_WIDTH, 1),
        (2 * MIN_CARD_WIDTH, 2),
        (3 * MIN_CARD_WIDTH, 3),
    ],
)
def test_cards_go_to_the_next_row_when_they_do_not_fit(qapp, width, columns):
    grid = _grid(3, width)
    assert grid.columns() == columns
    assert _positions(grid) == [(i // columns, i % columns) for i in range(3)]
    grid.close()


def test_the_columns_are_capped_and_follow_the_window_width(qapp):
    grid = _grid(3, 10 * MIN_CARD_WIDTH, max_columns=2)
    assert grid.columns() == 2
    grid.resize(MIN_CARD_WIDTH, 200)
    QApplication.processEvents()
    assert grid.columns() == 1
    grid.resize(2 * MIN_CARD_WIDTH, 200)
    QApplication.processEvents()
    assert grid.columns() == 2
    grid.close()


def test_the_first_card_is_long_on_the_left_when_split(qapp):
    wide = _grid(4, 2 * MIN_CARD_WIDTH + 40, split_first=True, max_columns=2)
    layout = wide.layout()
    assert layout.getItemPosition(0) == (0, 0, 3, 1)
    assert [layout.getItemPosition(i)[:2] for i in range(1, 4)] == [(0, 1), (1, 1), (2, 1)]
    wide.resize(MIN_CARD_WIDTH, 200)
    QApplication.processEvents()
    assert _positions(wide) == [(0, 0), (1, 0), (2, 0), (3, 0)]
    wide.close()


def test_the_first_setup_and_settings_cards_are_the_eight_and_the_seven(window):
    first = screen_widget(window, ScreenId.FIRST_SETUP)
    cards = list(first.findChildren(Card))
    # 코인, 예산과 투자 비율, 매수 비율, 손절과 한도, 봉 단위·봉 개수, Claude 모델, 그 밖의 값,
    # 꼭 읽을 글
    assert len(cards) == 8
    settings = screen_widget(window, ScreenId.SETTINGS)
    # 코인, 예산과 투자 비율, 매수 비율, 손절과 한도, 봉 단위·봉 개수, Claude 모델, 그 밖의 값
    assert len(settings.findChildren(Card)) == 7
    status = _filled(window) and screen_widget(window, ScreenId.STATUS)
    # 코인 카드 둘, 봉 통계 카드, 알림센터 카드.
    assert len(status.findChildren(Card)) == 4


# --- 단추와 글의 모양 ---


def test_only_the_main_buttons_are_primary(window):
    _filled(window)
    primary = {
        button.objectName()
        for screen_id in ScreenId
        for button in screen_widget(window, screen_id).findChildren(QPushButton)
        if button.property(theme.ROLE_PROPERTY) == theme.ROLE_PRIMARY
    }
    assert primary == {"firstSetupStart", "settingsSave", "alertWebhookSave"}
    assert f'QPushButton[{theme.ROLE_PROPERTY}="{theme.ROLE_PRIMARY}"]' in theme.app_stylesheet()


def test_the_screen_titles_and_coin_names_use_the_title_and_big_value_roles(window):
    _filled(window)
    first = screen_widget(window, ScreenId.FIRST_SETUP)
    title = first.findChild(QLabel, "screenTitle")
    assert title is not None
    status = screen_widget(window, ScreenId.STATUS)
    name = status.findChild(QLabel, "statusCoinName_KRW-AAA")
    assert name is not None
    assert name.property(theme.ROLE_PROPERTY) == theme.ROLE_BIG


def test_saved_message_is_the_ok_color_and_a_failure_is_the_error_color(window):
    window.apply(SettingsView(("KRW-AAA",), 30, True, candle_unit=CandleUnit.MINUTE_60))
    select_screen(window, ScreenId.SETTINGS)
    settings = screen_widget(window, ScreenId.SETTINGS)
    message = settings.findChild(QLabel, "settingsMessage")
    assert message is not None
    window.apply(SaveResult("settings", ok=True, message="저장했습니다."))
    assert message.text() == "저장했습니다."
    assert message.property(theme.OK_PROPERTY) is True
    assert message.property("tone") == "normal"
    window.apply(SaveResult("settings", ok=False, message="저장하지 못했습니다."))
    assert message.text() == "저장하지 못했습니다."
    assert message.property(theme.OK_PROPERTY) is False
    assert message.property("tone") == "warning"
    assert theme.ERROR in message.styleSheet()


def test_signed_values_are_rise_or_fall_colored_and_zero_has_no_color(window):
    _filled(window)
    status = screen_widget(window, ScreenId.STATUS)

    def trend(row: int) -> object:
        cell = status.findChild(QLabel, f"statusStatsCell_{row}_1")
        assert cell is not None
        return cell.property(theme.TREND_PROPERTY)

    # 4 전체 변화(+), 5 추세 기울기(-), 6 평균 대비(0), 9 최대 낙폭(-, 오름·내림 색 줄이 아님)
    assert trend(4) == theme.TREND_RISE
    assert trend(5) == theme.TREND_FALL
    assert not trend(6)
    assert not trend(9)
    assert f'QLabel[{theme.TREND_PROPERTY}="{theme.TREND_RISE}"]' in theme.app_stylesheet()


def test_the_stats_table_values_are_right_aligned_and_the_head_is_sub_text(window):
    _filled(window)
    status = screen_widget(window, ScreenId.STATUS)
    for row in range(13):
        value = status.findChild(QLabel, f"statusStatsCell_{row}_1")
        name = status.findChild(QLabel, f"statusStatsCell_{row}_0")
        assert value is not None
        assert name is not None
        assert value.alignment() & Qt.AlignmentFlag.AlignRight
        assert not name.alignment() & Qt.AlignmentFlag.AlignRight
    head = status.findChild(QLabel, "statusStatsHeader_1")
    assert head is not None
    assert head.property(theme.ROLE_PROPERTY) == theme.ROLE_SUB
    assert head.alignment() & Qt.AlignmentFlag.AlignRight


def test_the_element_rows_keep_their_text_and_draw_name_left_and_value_right(window):
    _filled(window)
    status = screen_widget(window, ScreenId.STATUS)
    row = status.findChild(QLabel, "statusCoinStatsRow_KRW-AAA_0")
    assert row is not None
    assert row.text() == "전체 변화 +32.00%"
    assert row.minimumSizeHint().height() >= row.fontMetrics().height()
    assert row.minimumSizeHint().width() >= row.fontMetrics().horizontalAdvance(row.text())
    row.resize(400, row.minimumSizeHint().height())
    image = row.grab().toImage()
    assert not image.isNull()


# --- 화면 이름 목록 ---


def test_the_screen_list_has_line_icons_and_the_same_texts_and_order(window):
    nav = window.findChild(QListWidget, "screenNav")
    assert nav is not None
    assert [nav.item(i).text() for i in range(nav.count())] == [
        "처음 설정",
        "현황",
        "보고서",
        "기록",
        "설정",
    ]
    assert nav.iconSize().width() == nav.iconSize().height() == theme.ICON_SIZE
    for row in range(nav.count()):
        icon = nav.item(row).icon()
        assert not icon.isNull()
        normal = icon.pixmap(16, 16).toImage()
        selected = icon.pixmap(16, 16, mode=icon.Mode.Selected).toImage()
        assert normal != selected  # 고른 칸은 글 색, 나머지는 보조 글 색
    assert f"QListWidget#screenNav::item:selected {{ background: {theme.SELECTED}" in (
        theme.app_stylesheet()
    )


def test_the_alert_tabs_mark_the_chosen_one_for_the_accent_underline(window):
    _filled(window)
    status = screen_widget(window, ScreenId.STATUS)
    trade = status.findChild(QPushButton, "alertTab_trade")
    error = status.findChild(QPushButton, "alertTab_error")
    assert trade is not None
    assert error is not None
    assert trade.property("selected") is True
    assert error.property("selected") is False
    error.click()
    assert error.property("selected") is True
    assert trade.property("selected") is False
    assert theme.ACCENT in theme.app_stylesheet()


def _chain(start: QWidget) -> list[str]:
    names = []
    widget = start.nextInFocusChain()
    while widget is not start:
        names.append(widget.objectName())
        widget = widget.nextInFocusChain()
    return names


def test_each_icon_comes_before_the_buttons_of_its_card_in_the_tab_order(window):
    first = screen_widget(window, ScreenId.FIRST_SETUP)
    chain = _chain(first)
    assert chain.index("firstSetupCandleNote") < chain.index("firstSetupUnit_minute30")
    assert chain.index("firstSetupRetentionInfo") < chain.index("firstSetupRetention7")
    assert chain.index("firstSetupRetention90") < chain.index("firstSetupNoticesInfo")
    assert chain.index("firstSetupNoticesInfo") < chain.index("firstSetupStart")
