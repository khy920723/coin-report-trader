"""M2 명세 3.12.6~3.12.8: 알림의 곳으로 가기, 알림센터의 키보드, 탭 이름, Ctrl+1~5, 접근성 설명.

값은 모두 시험이 만든 값이다.
"""

from datetime import UTC, datetime

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QListWidget,
    QPushButton,
    QRadioButton,
    QWidget,
)

from coin_report_trader.core import (
    AlertCategory,
    AlertDetail,
    AlertsView,
    AlertView,
    CandleUnit,
    Direction,
    LoadState,
    Mode,
    ReportListView,
    ReportState,
    RoundItem,
    ScreenId,
    SettingsView,
    UiContext,
    WebhookView,
)
from coin_report_trader.core.texts import (
    STAGE_SETTINGS_SAVE,
    STAGE_WEBHOOK_DELETE,
    STAGE_WEBHOOK_SAVE,
)
from coin_report_trader.ui import create_main_window, current_screen, screen_widget, select_screen

CREATED = datetime(2026, 1, 2, 1, 30, tzinfo=UTC)
COINS = ("KRW-AAA", "KRW-BBB")


class Actions:
    def __init__(self) -> None:
        self.reports: list[str] = []
        self.report: list[int] = []
        self.calls: list[str] = []

    def refresh_markets(self) -> None:
        self.calls.append("refresh_markets")

    def save_settings(self, coins, retention_days, **kwargs) -> None:
        self.calls.append("save_settings")

    def load_reports(self, market) -> None:
        self.reports.append(market)

    def load_report(self, report_id) -> None:
        self.report.append(report_id)

    def retry_candles(self) -> None:
        self.calls.append("retry_candles")

    def mark_seen(self, alert_ids) -> None:
        self.calls.append("mark_seen")

    def delete_alert(self, alert_id) -> None:
        self.calls.append("delete_alert")

    def clear_alerts(self, category) -> None:
        self.calls.append("clear_alerts")

    def save_webhook(self, category, url) -> None:
        self.calls.append("save_webhook")

    def delete_webhook(self, category) -> None:
        self.calls.append("delete_webhook")

    def send_test(self, category) -> None:
        self.calls.append("send_test")


@pytest.fixture
def actions() -> Actions:
    return Actions()


@pytest.fixture
def window(qapp: QApplication, actions: Actions):
    made = create_main_window(UiContext("0.0.1", "X", actions))
    made.resize(1100, 800)
    made.show()
    made.apply(SettingsView(COINS, 30, True))
    yield made
    made.close()
    made.deleteLater()


def _alert(alert_id: int, category: AlertCategory, code: str, coin=None, **changes) -> AlertView:
    values = {
        "id": alert_id,
        "created_at": CREATED,
        "mode": Mode.PAPER,
        "category": category,
        "code": code,
        "coin": coin,
        "text": f"글 {alert_id}",
        "seen": True,
    }
    values.update(changes)
    return AlertView(**values)


def _stage(stage: str, coin=None) -> AlertDetail:
    return AlertDetail(stage, coin, "오류 이름", "내용", "그 뒤에 한 일")


def _show(window, *alerts: AlertView, category=AlertCategory.TRADE) -> QWidget:
    unseen: dict = {}
    for alert in alerts:
        if not alert.seen:
            unseen[alert.category] = unseen.get(alert.category, 0) + 1
    window.apply(AlertsView(alerts, unseen, LoadState.READY))
    select_screen(window, ScreenId.STATUS)
    status = screen_widget(window, ScreenId.STATUS)
    status.findChild(QPushButton, f"alertTab_{category.value}").click()
    QApplication.processEvents()
    return status


def _click_line(status: QWidget, alert_id: int) -> None:
    QTest.mouseClick(status.findChild(QLabel, f"alertLine_{alert_id}"), Qt.MouseButton.LeftButton)


def _chosen_coin(window) -> str:
    box = screen_widget(window, ScreenId.REPORTS).findChild(QListWidget, "reportCoin")
    return COINS[box.currentRow()]


# --- 알림의 곳으로 가기 ---


@pytest.mark.parametrize(
    ("code", "category"),
    [
        ("A-1", AlertCategory.TRADE),
        ("A-2", AlertCategory.TRADE),
        ("A-17", AlertCategory.LIMIT),
        ("A-30", AlertCategory.ERROR),
    ],
)
def test_alerts_about_a_coin_go_to_that_coins_card_on_the_status_screen(
    window, actions, code, category
):
    status = _show(window, _alert(1, category, code, "KRW-BBB"), category=category)
    select_screen(window, ScreenId.SETTINGS)
    _click_line(status, 1)
    assert current_screen(window) is ScreenId.STATUS
    assert actions.calls.count("delete_alert") == 0


def test_a_line_without_detail_goes_on_one_click_and_does_not_mark_or_delete(window, actions):
    status = _show(window, _alert(1, AlertCategory.REPORT, "A-18"), category=AlertCategory.REPORT)
    _click_line(status, 1)
    assert current_screen(window) is ScreenId.REPORTS
    assert "delete_alert" not in actions.calls


def _list(market: str, *ids: int) -> ReportListView:
    items = tuple(
        RoundItem(report_id, 10 - index, CREATED, ReportState.OK, Direction.UP, None)
        for index, report_id in enumerate(ids)
    )
    return ReportListView(market, items, LoadState.READY)


def test_a_report_alert_with_a_coin_opens_that_coin_and_the_named_report(window, actions):
    alert = _alert(1, AlertCategory.REPORT, "A-26", "KRW-BBB", report_id=42)
    status = _show(window, alert, category=AlertCategory.REPORT)
    _click_line(status, 1)
    assert current_screen(window) is ScreenId.REPORTS
    assert _chosen_coin(window) == "KRW-BBB"
    assert actions.reports[-1] == "KRW-BBB"
    window.apply(_list("KRW-BBB", 90, 42, 7))  # 42는 맨 위가 아니다
    rounds = screen_widget(window, ScreenId.REPORTS).findChild(QListWidget, "reportRounds")
    assert rounds.currentRow() == 1
    assert actions.report[-1] == 42


def test_a_report_alert_whose_report_is_not_in_the_list_opens_the_top_round(window, actions):
    alert = _alert(1, AlertCategory.REPORT, "A-26", "KRW-BBB", report_id=42)
    status = _show(window, alert, category=AlertCategory.REPORT)
    _click_line(status, 1)
    window.apply(_list("KRW-BBB", 90, 7))  # 42가 없다
    rounds = screen_widget(window, ScreenId.REPORTS).findChild(QListWidget, "reportRounds")
    assert rounds.currentRow() == 0
    assert actions.report[-1] == 90


def test_a_report_alert_without_a_coin_just_opens_the_reports_screen(window):
    status = _show(
        window, _alert(1, AlertCategory.REPORT, "A-26", None), category=AlertCategory.REPORT
    )
    _click_line(status, 1)
    assert current_screen(window) is ScreenId.REPORTS


def test_error_alerts_unfold_and_the_go_button_goes_to_the_place_by_the_stage(window):
    cases = [
        (STAGE_SETTINGS_SAVE, ScreenId.SETTINGS),
        (STAGE_WEBHOOK_SAVE, ScreenId.STATUS),
        (STAGE_WEBHOOK_DELETE, ScreenId.STATUS),
    ]
    for index, (stage, expected) in enumerate(cases, start=1):
        alert = _alert(
            index, AlertCategory.ERROR, "A-29", "KRW-AAA", detail=_stage(stage, "KRW-AAA")
        )
        status = _show(window, alert, category=AlertCategory.ERROR)
        go = status.findChild(QPushButton, f"alertGo_{index}")
        assert go is not None
        assert go.isHidden()  # 펼치기 전에는 보이지 않는다
        _click_line(status, index)
        QApplication.processEvents()
        assert not go.isHidden()
        select_screen(window, ScreenId.REPORTS)
        go.click()
        assert current_screen(window) is expected, stage


def test_an_error_alert_before_the_first_setup_goes_to_the_first_setup(qapp):
    made = create_main_window(UiContext("0.0.1", "X", Actions()))
    made.show()
    made.apply(SettingsView())
    alert = _alert(1, AlertCategory.ERROR, "A-29", None, detail=_stage(STAGE_SETTINGS_SAVE))
    made.apply(AlertsView((alert,), {}, LoadState.READY))
    status = screen_widget(made, ScreenId.STATUS)
    status.findChild(QPushButton, "alertTab_error").click()
    _click_line(status, 1)
    status.findChild(QPushButton, "alertGo_1").click()
    assert current_screen(made) is ScreenId.FIRST_SETUP
    made.close()
    made.deleteLater()


# --- 알림센터 키보드 ---


def test_a_line_takes_focus_with_tab_and_enter_or_space_acts_like_a_click(window):
    alert = _alert(
        1, AlertCategory.ERROR, "A-29", "KRW-AAA", detail=_stage(STAGE_SETTINGS_SAVE, "KRW-AAA")
    )
    status = _show(window, alert, category=AlertCategory.ERROR)
    line = status.findChild(QLabel, "alertLine_1")
    assert line.focusPolicy() == Qt.FocusPolicy.StrongFocus
    go = status.findChild(QPushButton, "alertGo_1")
    assert go.isHidden()
    QTest.keyClick(line, Qt.Key.Key_Return)
    assert not go.isHidden()
    QTest.keyClick(line, Qt.Key.Key_Space)
    assert go.isHidden()
    QTest.keyClick(line, Qt.Key.Key_Enter)
    assert not go.isHidden()
    QTest.keyClick(line, Qt.Key.Key_A)  # 다른 키는 아무것도 하지 않는다
    assert not go.isHidden()


def test_enter_on_a_line_without_detail_goes_to_its_place(window):
    status = _show(window, _alert(1, AlertCategory.REPORT, "A-19"), category=AlertCategory.REPORT)
    QTest.keyClick(status.findChild(QLabel, "alertLine_1"), Qt.Key.Key_Return)
    assert current_screen(window) is ScreenId.REPORTS


# --- 주소를 읽지 못함, 탭 이름 ---


def test_the_unreadable_webhook_text_shows_only_for_a_view_marked_unreadable(window):
    status = _show(window)
    label = status.findChild(QLabel, "alertWebhookUnreadable")
    assert label is not None
    assert label.isHidden()
    window.apply(WebhookView(AlertCategory.TRADE, saved=False, unreadable=True))
    assert not label.isHidden()
    assert label.text() == "주소를 읽지 못했습니다."
    assert label.property("tone") == "warning"
    window.apply(WebhookView(AlertCategory.TRADE, saved=False, unreadable=False))
    assert label.isHidden()


def test_a_tab_name_has_the_unseen_count_in_parentheses_after_a_space(window):
    status = _show(
        window,
        _alert(1, AlertCategory.TRADE, "A-1", seen=False),
        _alert(2, AlertCategory.TRADE, "A-1", seen=False),
        _alert(3, AlertCategory.ERROR, "A-30", seen=False),
    )
    assert status.findChild(QPushButton, "alertTab_trade").text() == "매매 알림 (2)"
    assert status.findChild(QPushButton, "alertTab_error").text() == "오류 알림 (1)"
    assert status.findChild(QPushButton, "alertTab_report").text() == "보고서 알림"


# --- Ctrl+1~5 ---


def test_ctrl_1_to_5_open_the_screens_in_the_list_order(window):
    shortcuts = {shortcut.key().toString(): shortcut for shortcut in window.findChildren(QShortcut)}
    assert sorted(shortcuts) == [f"Ctrl+{n}" for n in range(1, 6)]
    for number, screen_id in enumerate(ScreenId, start=1):
        select_screen(
            window, ScreenId.REPORTS if screen_id is not ScreenId.REPORTS else ScreenId.SETTINGS
        )
        shortcuts[QKeySequence(f"Ctrl+{number}").toString()].activated.emit()
        assert current_screen(window) is screen_id


# --- 접근성 설명 ---


def test_the_count_radios_carry_the_tooltip_as_their_accessible_description(window):
    window.apply(SettingsView(COINS, 30, True, candle_unit=CandleUnit.MINUTE_60))
    settings = screen_widget(window, ScreenId.SETTINGS)
    radios = [
        radio
        for radio in settings.findChildren(QRadioButton)
        if radio.objectName().startswith("settingsCount_")
    ]
    assert radios
    for radio in radios:
        assert radio.toolTip()
        assert radio.accessibleDescription() == radio.toolTip()


# --- A-31, 현황 코인 칸의 키보드 ---


def _a31(window) -> QWidget:
    alert = _alert(1, AlertCategory.ERROR, "A-31", None, detail=_stage("켜기"))
    window.apply(AlertsView((alert,), {}, LoadState.READY))
    status = screen_widget(window, ScreenId.STATUS)
    select_screen(window, ScreenId.STATUS)
    status.findChild(QPushButton, "alertTab_error").click()
    _click_line(status, 1)
    return status


def test_a31_goes_to_the_settings_screen_after_the_first_setup(window):
    status = _a31(window)
    select_screen(window, ScreenId.REPORTS)
    status.findChild(QPushButton, "alertGo_1").click()
    assert current_screen(window) is ScreenId.SETTINGS


def test_a31_goes_to_the_first_setup_screen_before_the_first_setup(qapp):
    made = create_main_window(UiContext("0.0.1", "X", Actions()))
    made.show()
    made.apply(SettingsView())
    status = _a31(made)
    select_screen(made, ScreenId.REPORTS)
    status.findChild(QPushButton, "alertGo_1").click()
    assert current_screen(made) is ScreenId.FIRST_SETUP
    made.close()
    made.deleteLater()


def test_enter_and_space_on_a_status_coin_card_choose_that_coin_for_the_stats(window):
    select_screen(window, ScreenId.STATUS)
    status = screen_widget(window, ScreenId.STATUS)
    card = status.findChild(QWidget, "statusCoin_KRW-BBB")
    chooser = status.findChild(QComboBox, "statusStatsCoin")
    assert card.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert chooser.currentIndex() == 0
    for key in (Qt.Key.Key_Return, Qt.Key.Key_Space, Qt.Key.Key_Enter):
        chooser.setCurrentIndex(0)
        QTest.keyClick(card, key)
        assert chooser.currentIndex() == 1, key
    chooser.setCurrentIndex(0)
    QTest.keyClick(card, Qt.Key.Key_A)  # 다른 키는 아무것도 하지 않는다
    assert chooser.currentIndex() == 0
