"""M1 명세 6절의 화면 시나리오: B28, B39, B40(3.8, 3.14.2, 3.14.3, 설계서 3.4·6.7·6.8·8).

가짜 `UiActions`로 창을 만들고(띄우지 않는다) 조립이 보내는 값(`apply`)을 직접 준다. 확인 창은
`set_confirm_handler`로 "확인"이나 "취소"를 고른다. 보이는지는 `isVisibleTo(창)`으로 본다.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from PySide6.QtWidgets import QAbstractButton, QApplication, QLabel, QLineEdit, QListWidget

from coin_report_trader.core import (
    AlertCategory,
    AlertsView,
    AlertView,
    CandleUnit,
    LoadState,
    Market,
    MarketsView,
    Mode,
    SaveResult,
    ScreenId,
    SettingsView,
    UiContext,
    WebhookView,
)
from coin_report_trader.core.texts import SETTINGS_UNREADABLE
from coin_report_trader.ui import create_main_window

AAA = "KRW-AAA"
HOUR = CandleUnit.MINUTE_60
CREATED = datetime(2026, 1, 2, 1, 30, tzinfo=UTC)


class Actions:
    """부른 동작을 적는 가짜 `UiActions`."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]

    def refresh_markets(self) -> None:
        self.calls.append(("refresh_markets",))

    def save_settings(self, coins, retention_days, *, first_setup, candle_unit=None,
                      candle_count=None) -> None:  # fmt: skip
        self.calls.append(("save_settings", coins, retention_days, first_setup))

    def retry_candles(self) -> None:
        self.calls.append(("retry_candles",))

    def mark_seen(self, alert_ids) -> None:
        self.calls.append(("mark_seen", alert_ids))

    def delete_alert(self, alert_id) -> None:
        self.calls.append(("delete_alert", alert_id))

    def clear_alerts(self, category) -> None:
        self.calls.append(("clear_alerts", category))

    def save_webhook(self, category, url) -> None:
        self.calls.append(("save_webhook", category))

    def delete_webhook(self, category) -> None:
        self.calls.append(("delete_webhook", category))

    def send_test(self, category) -> None:
        self.calls.append(("send_test", category))


class Confirm:
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.asked: list[str] = []

    def __call__(self, title: str, body: str) -> bool:
        self.asked.append(title)
        return self.answer


def no_confirm(title: str, body: str) -> bool:
    raise AssertionError(f"확인 창이 뜨면 안 된다: {title}")


@pytest.fixture
def make_window(qapp: QApplication):
    made: list[Any] = []

    def factory(actions: Actions) -> Any:
        window = create_main_window(UiContext("0.0.1", "X", actions))
        window.set_confirm_handler(no_confirm)
        made.append(window)
        return window

    yield factory
    for window in made:
        window.deleteLater()


def find(window: Any, kind: type, name: str) -> Any:
    found = window.findChild(kind, name)
    assert found is not None, name
    return found


def click(window: Any, name: str) -> None:
    find(window, QAbstractButton, name).click()


def go(window: Any, screen: ScreenId) -> None:
    find(window, QListWidget, "screenNav").setCurrentRow(list(ScreenId).index(screen))


def shown(window: Any, kind: type, name: str) -> bool:
    widget = window.findChild(kind, name)
    return widget is not None and widget.isVisibleTo(window)


def ready(window: Any, *, retention: int = 30) -> None:
    window.apply(SettingsView((AAA,), retention, True, candle_unit=HOUR, candle_count=100))
    window.apply(MarketsView(LoadState.READY, (Market(AAA, "에이", False),)))


def alert(alert_id: int, category: AlertCategory = AlertCategory.ERROR) -> AlertView:
    return AlertView(alert_id, CREATED, Mode.PAPER, category, "A-1", None, f"글 {alert_id}")


# --- B28: 확인 창, 저장 결과, 설정을 읽지 못함, "다시 시도" ---------------------------------------


@pytest.mark.scenario("B28")
@pytest.mark.parametrize("answer", [False, True], ids=["취소", "확인"])
def test_b28_four_confirmations_call_the_action_only_on_confirm(make_window, answer) -> None:
    actions = Actions()
    window = make_window(actions)
    ready(window)
    window.apply(AlertsView((alert(5),), {AlertCategory.ERROR: 0}))
    window.apply(WebhookView(AlertCategory.ERROR, True))
    confirm = Confirm(answer)
    window.set_confirm_handler(confirm)
    go(window, ScreenId.STATUS)
    click(window, f"alertTab_{AlertCategory.ERROR.value}")
    click(window, "alertWebhookDelete")  # 주소 지우기
    click(window, "alertDelete_5")  # 한 줄 지우기
    click(window, "alertClear")  # 탭 전체 지우기
    go(window, ScreenId.SETTINGS)
    click(window, "settingsRetention7")  # 보관 기간 줄이기
    click(window, "settingsSave")
    assert len(confirm.asked) == 4
    called = [n for n in actions.names() if n in ("delete_webhook", "delete_alert",
                                                   "clear_alerts", "save_settings")]  # fmt: skip
    expected = ["delete_webhook", "delete_alert", "clear_alerts", "save_settings"]
    assert called == (expected if answer else [])


@pytest.mark.scenario("B28")
def test_b28_choosing_a_retention_first_or_longer_asks_nothing(make_window) -> None:
    actions = Actions()
    window = make_window(actions)
    window.apply(SettingsView((), None, False))
    window.apply(MarketsView(LoadState.READY, (Market(AAA, "에이", False),)))
    click(window, f"firstSetupCoin_{AAA}")
    click(window, "firstSetupRetention30")  # 처음 고르기
    click(window, f"firstSetupUnit_{HOUR.value}")
    click(window, "firstSetupStart")
    window.apply(SettingsView((AAA,), 30, True, candle_unit=HOUR, candle_count=100))
    go(window, ScreenId.SETTINGS)
    click(window, "settingsRetention90")  # 길게 바꾸기
    click(window, "settingsSave")
    saves = [call for call in actions.calls if call[0] == "save_settings"]
    assert [(c[2], c[3]) for c in saves] == [(30, True), (90, False)]  # 창 없이 불린다


@pytest.mark.scenario("B28")
def test_b28_a_refused_save_shows_its_message_as_a_warning_and_keeps_the_values(
    make_window,
) -> None:
    window = make_window(Actions())
    ready(window)
    go(window, ScreenId.SETTINGS)
    click(window, "settingsRetention90")
    message = "코인 목록에 없는 코인이 있어 저장하지 않았습니다."
    window.apply(SaveResult("settings", False, message))
    label = find(window, QLabel, "settingsMessage")
    assert label.isVisibleTo(window)
    assert label.text() == message
    assert label.property("tone") == "warning"  # 경고 색
    assert find(window, QAbstractButton, "settingsRetention90").isChecked()  # 값이 남는다
    assert find(window, QAbstractButton, f"settingsCoin_{AAA}").isChecked()


@pytest.mark.scenario("B28")
def test_b28_settings_that_cannot_be_read_show_the_line_and_call_nothing(make_window) -> None:
    actions = Actions()
    window = make_window(actions)
    window.apply(SettingsView((), None, False, state=LoadState.FAILED))
    texts = [w.text() for w in window.findChildren(QLabel) if w.isVisibleTo(window)]
    for screen in (ScreenId.FIRST_SETUP, ScreenId.SETTINGS):
        go(window, screen)
        texts += [w.text() for w in window.findChildren(QLabel) if w.isVisibleTo(window)]
    assert SETTINGS_UNREADABLE in texts
    click(window, "settingsSave")
    click(window, "firstSetupStart")
    assert "refresh_markets" not in actions.names()
    assert "save_settings" not in actions.names()


@pytest.mark.scenario("B28")
@pytest.mark.parametrize(
    "settings",
    [None, SettingsView((AAA,), 30, False), SettingsView((), None, False, state=LoadState.FAILED)],
    ids=["첫 SettingsView 전", "처음 설정 전", "읽지 못함"],
)
def test_b28_the_status_retry_is_hidden_until_first_setup_is_done(make_window, settings) -> None:
    actions = Actions()
    window = make_window(actions)
    if settings is not None:
        window.apply(settings)
    go(window, ScreenId.STATUS)
    assert not shown(window, QAbstractButton, "statusStatsRetry")
    assert not shown(window, QAbstractButton, f"statusCoinRetry_{AAA}")
    assert "retry_candles" not in actions.names()


# --- B39: 코인 목록의 차례와 찾기 -----------------------------------------------------------------

LISTED = (
    Market("KRW-ZZA", "가나", False),
    Market("KRW-AAB", "다라", False),
    Market("KRW-1AA", "마바", False),
    Market("KRW-BBC", "사아", False),
)


def rows_shown(window: Any) -> list[str]:
    names = [box.objectName() for box in window.findChildren(QAbstractButton)]
    return [
        name.removeprefix("firstSetupCoin_KRW-")
        for name in names
        if name.startswith("firstSetupCoin_KRW-") and shown(window, QAbstractButton, name)
    ]


@pytest.mark.scenario("B39")
def test_b39_the_list_is_sorted_and_searched(make_window) -> None:
    window = make_window(Actions())
    window.apply(SettingsView((), None, False))
    window.apply(MarketsView(LoadState.READY, LISTED))
    assert rows_shown(window) == ["1AA", "AAB", "BBC", "ZZA"]
    click(window, "firstSetupCoin_KRW-BBC")
    search = find(window, QLineEdit, "firstSetupCoinSearch")
    search.setText("b")
    assert rows_shown(window) == ["AAB", "BBC"]
    search.setText("사")
    assert rows_shown(window) == ["BBC"]
    search.setText("가")  # 고른 BBC가 가려진다
    assert rows_shown(window) == ["ZZA"]
    assert find(window, QAbstractButton, "firstSetupCoin_KRW-BBC").isChecked()  # 고른 상태는 남는다
    search.setText("없음")
    assert rows_shown(window) == []
    assert shown(window, QLabel, "firstSetupCoinsNoMatch")
    assert find(window, QLabel, "firstSetupCoinsNoMatch").text() == "찾는 코인이 없습니다."


# --- B40: 봉 개수의 툴팁 -------------------------------------------------------------------

LINE1 = (
    "예상 적중률(참고값): {}. 공개 연구에서 오르내림을 맞힌 비율이고, 이 앱이 잰 값이 아닙니다. "
    "수익을 뜻하지 않습니다."
)
MEANINGS = {
    100: "근거가 받쳐 주는 가장 적은 개수입니다.",
    200: "거래소에서 한 번에 받는 가장 많은 개수입니다.",
    500: "더 긴 기간을 보지만 요청이 3번 들고 오래된 구간이 섞입니다.",
}
TABLE = {  # 명세 3.14.2의 표: 봉 단위 → (적중률, 개수마다의 덮는 기간)
    None: ("50~56%", dict.fromkeys(MEANINGS, "봉 단위를 고르면 보입니다.")),
    CandleUnit.MINUTE_60: ("50~55%", {100: "약 4.2일", 200: "약 8.3일", 500: "약 20.8일"}),
    CandleUnit.DAY: ("48~53%", {100: "100일", 200: "약 6.6개월", 500: "약 1.4년"}),
}


@pytest.mark.scenario("B40")
def test_b40_the_count_tooltips_follow_the_table(make_window) -> None:
    window = make_window(Actions())
    window.apply(SettingsView((), None, False))
    for unit, (rate, periods) in TABLE.items():
        if unit is not None:
            click(window, f"firstSetupUnit_{unit.value}")
        for count, meaning in MEANINGS.items():
            tip = find(window, QAbstractButton, f"firstSetupCount_{count}").toolTip()
            assert tip.split("\n") == [
                LINE1.format(rate),
                f"뜻: {meaning}",
                f"덮는 기간: {periods[count]}",
            ], (unit, count)
            assert "수익을 뜻하지 않습니다." in tip.split("\n")[0]
