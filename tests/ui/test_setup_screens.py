"""처음 설정과 설정 화면(M1 명세 2의 6~8, 3.4, 3.8, 설계서 3, 6)의 시험.

가짜 `UiActions`는 부른 동작과 인자를 기록한다. 창을 띄우지 않으므로 보이는지는 `isHidden()`과
`QStackedWidget`의 현재 쪽으로 본다.
"""

from datetime import UTC, datetime

import pytest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QWidget,
)

from coin_report_trader.core import (
    CandleUnit,
    LoadState,
    Market,
    MarketsView,
    SaveResult,
    ScreenId,
    SettingsView,
    UiActions,
    UiContext,
)
from coin_report_trader.ui import MainWindow, create_main_window, current_screen, screen_widget
from coin_report_trader.ui import main_window as main_window_module
from coin_report_trader.ui.dialogs import build_confirm_box
from coin_report_trader.ui.info_icon import InfoIcon

AAA = Market("KRW-AAA", "가짜에이", caution=False)
BBB = Market("KRW-BBB", "가짜비", caution=True)
CCC = Market("KRW-CCC", "가짜씨", caution=False)
DDD = Market("KRW-DDD", "가짜디", caution=False)
ALL = (AAA, BBB, CCC, DDD)

LOADING_MARKETS = "코인 목록을 불러오는 중입니다."
FAILED_MARKETS = "코인 목록을 받지 못했습니다. 네트워크를 확인한 뒤 다시 시도하세요."
UNREADABLE = "설정을 읽지 못했습니다. 60분마다 다시 읽습니다."
NOT_LISTED = (
    "코인 목록에 없는 코인이 있어 저장하지 않았습니다. 코인 목록을 다시 받은 뒤 저장하세요."
)
BLOCKED_AT_1030 = "거래소가 잠시 요청을 막았습니다. 10:30 뒤에 다시 시도할 수 있습니다."
UNTIL_1030 = datetime(2026, 1, 1, 1, 30, tzinfo=UTC)  # 한국 시간 10:30
BEFORE_SETUP = "처음 설정을 마치면 값을 고칠 수 있습니다."
SHRINK_TITLE = "알림 보관 기간을 줄일까요?"


class FakeActions:
    """부른 동작을 `calls`에 (이름, 인자...)로 적는다."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.candles: list[tuple] = []
        """`save_settings`가 받은 (봉 단위, 봉 개수). `calls`의 `save_settings` 줄과 같은 차례다."""

    def count(self, name: str) -> int:
        return sum(1 for call in self.calls if call[0] == name)

    def last(self, name: str) -> tuple:
        return [call for call in self.calls if call[0] == name][-1]

    def refresh_markets(self) -> None:
        self.calls.append(("refresh_markets",))

    def save_settings(
        self, coins, retention_days, *, first_setup, candle_unit=None, candle_count=None
    ) -> None:
        self.calls.append(("save_settings", coins, retention_days, first_setup))
        self.candles.append((candle_unit, candle_count))

    def load_reports(self, market) -> None:
        pass  # 2단계의 동작. 화면이 부르기 전까지 아무것도 하지 않는다

    def load_report(self, report_id) -> None:
        pass

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
    """확인 창을 대신하는 함수. 답을 정해 두고, 받은 (제목, 본문)을 적는다."""

    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.asked: list[tuple[str, str]] = []

    def __call__(self, title: str, body: str) -> bool:
        self.asked.append((title, body))
        return self.answer


def _unexpected_confirm(title: str, body: str) -> bool:
    raise AssertionError(f"예상하지 못한 확인 창: {title}")


@pytest.fixture
def make_window(qapp: QApplication):
    made: list[MainWindow] = []

    def factory(actions: UiActions | None = None) -> MainWindow:
        window = create_main_window(UiContext("0.0.1", "X", actions))
        # 확인 창이 필요 없는 시험에서 진짜 모달 창이 떠 멈추지 않게, 기본은 실패로 한다.
        window.set_confirm_handler(_unexpected_confirm)
        made.append(window)
        return window

    yield factory
    for window in made:
        window.deleteLater()


@pytest.fixture
def actions() -> FakeActions:
    return FakeActions()


# --- 읽는 도구 ---


def _find(parent: QWidget, kind: type, name: str):
    found = parent.findChild(kind, name)
    assert found is not None, name
    return found


def _first(window: MainWindow) -> QWidget:
    return screen_widget(window, ScreenId.FIRST_SETUP)


def _settings(window: MainWindow) -> QWidget:
    return screen_widget(window, ScreenId.SETTINGS)


def _tip(screen: QWidget, name: str) -> str | None:
    """'ⓘ'가 보이면 그 툴팁 글, 숨겨져 있으면 `None`. 읽는 설명도 같아야 한다."""
    icon = _find(screen, InfoIcon, name)
    assert icon.accessibleDescription() == icon.toolTip()
    return None if icon.isHidden() else icon.toolTip()


def _flat(layout) -> list[str]:
    """레이아웃 안 위젯의 `objectName`을 놓인 차례대로(안쪽 레이아웃까지)."""
    names: list[str] = []
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item.widget() is not None:
            names.append(item.widget().objectName())
        elif item.layout() is not None:
            names.extend(_flat(item.layout()))
    return names


def _shown_text(screen: QWidget, name: str) -> str | None:
    """라벨이 보이면 그 글, 숨겨져 있으면 `None`."""
    label = _find(screen, QLabel, name)
    return None if label.isHidden() else label.text()


def _tone(screen: QWidget, name: str) -> str:
    return _find(screen, QLabel, name).property("tone")


def _list_shown(screen: QWidget, prefix: str) -> bool:
    return _find(screen, QStackedWidget, f"{prefix}CoinsPages").currentIndex() == 1


def _coin_box(screen: QWidget, prefix: str, market: str) -> QCheckBox:
    return _find(screen, QCheckBox, f"{prefix}Coin_{market}")


def _selected_texts(screen: QWidget, prefix: str) -> list[str]:
    selected = _find(screen, QListWidget, f"{prefix}CoinsSelected")
    return [selected.item(i).text() for i in range(selected.count())]


def _choose(screen: QWidget, prefix: str, *markets: str) -> None:
    for market in markets:
        _coin_box(screen, prefix, market).click()


def _choose_days(screen: QWidget, prefix: str, days: int) -> None:
    _find(screen, QRadioButton, f"{prefix}Retention{days}").click()


def _choose_unit(screen: QWidget, prefix: str, unit: CandleUnit) -> None:
    _find(screen, QRadioButton, f"{prefix}Unit_{unit.value}").click()


def _choose_count(screen: QWidget, prefix: str, count: int) -> None:
    _find(screen, QRadioButton, f"{prefix}Count_{count}").click()


def _days(screen: QWidget, prefix: str) -> int | None:
    for days in (7, 30, 90):
        if _find(screen, QRadioButton, f"{prefix}Retention{days}").isChecked():
            return days
    return None


def _button(screen: QWidget, name: str) -> QPushButton:
    return _find(screen, QPushButton, name)


def _ready_first_setup(window: MainWindow) -> QWidget:
    """처음 설정을 마치기 전, 코인 목록을 받은 처음 설정 화면."""
    window.apply(SettingsView())
    window.apply(MarketsView(LoadState.READY, ALL))
    return _first(window)


def _ready_settings(window: MainWindow, coins=("KRW-AAA", "KRW-CCC"), days=30) -> QWidget:
    """처음 설정을 마친 뒤, 코인 목록을 받은 설정 화면."""
    window.apply(SettingsView(coins, days, True))
    window.apply(MarketsView(LoadState.READY, ALL))
    from coin_report_trader.ui import select_screen

    select_screen(window, ScreenId.SETTINGS)
    return _settings(window)


# --- 창 ---


def test_create_main_window_returns_a_main_window_with_apply(make_window, qapp):
    before = QApplication.instance()
    window = make_window()
    assert isinstance(window, MainWindow)
    assert callable(window.apply)
    assert QApplication.instance() is before
    assert window.objectName() == "mainWindow"
    assert window.windowTitle() == "Coin Report Trader"
    assert not window.isVisible()
    assert current_screen(window) is ScreenId.FIRST_SETUP


def test_fake_actions_follow_the_protocol():
    assert isinstance(FakeActions(), UiActions)


def test_without_actions_nothing_is_called_and_the_first_screen_is_first_setup(make_window):
    window = make_window(None)
    assert current_screen(window) is ScreenId.FIRST_SETUP
    first = _first(window)
    assert _shown_text(first, "firstSetupMessage") is None
    assert _find(first, QWidget, "firstSetupForm").isEnabled()
    # 값을 받아도 예외 없이 그리고, 눌러도 부를 동작이 없다.
    window.apply(SettingsView())
    window.apply(MarketsView(LoadState.READY, ALL))
    _choose(first, "firstSetup", "KRW-AAA")
    _choose_days(first, "firstSetup", 30)
    _choose_unit(first, "firstSetup", CandleUnit.MINUTE_60)
    _button(first, "firstSetupStart").click()
    window.apply(MarketsView(LoadState.FAILED, error_name="시간 초과"))
    _button(first, "firstSetupCoinsRetry").click()
    assert current_screen(window) is ScreenId.FIRST_SETUP


# --- 켜는 순서 ---


def test_before_the_first_settings_view_the_form_is_dim_and_says_loading(make_window, actions):
    window = make_window(actions)
    for screen, prefix, message in (
        (_first(window), "firstSetup", "firstSetupMessage"),
        (_settings(window), "settings", "settingsMessage"),
    ):
        assert _shown_text(screen, message) == "불러오는 중"
        assert not _find(screen, QWidget, f"{prefix}Form").isEnabled()
    assert not _button(_first(window), "firstSetupStart").isEnabled()
    assert not _button(_settings(window), "settingsSave").isEnabled()
    assert current_screen(window) is ScreenId.FIRST_SETUP
    assert actions.calls == []


def test_first_settings_view_without_first_setup_stays_and_asks_for_markets(make_window, actions):
    window = make_window(actions)
    window.apply(SettingsView())
    assert current_screen(window) is ScreenId.FIRST_SETUP
    assert actions.calls == [("refresh_markets",)]
    first = _first(window)
    assert _shown_text(first, "firstSetupMessage") is None
    assert _find(first, QWidget, "firstSetupForm").isEnabled()
    # 요청한 때부터 목록이 올 때까지 "불러오는 중" 줄이 보인다.
    assert not _list_shown(first, "firstSetup")
    assert _shown_text(first, "firstSetupCoinsMessage") == LOADING_MARKETS
    window.apply(MarketsView(LoadState.LOADING))
    assert _shown_text(first, "firstSetupCoinsMessage") == LOADING_MARKETS
    window.apply(MarketsView(LoadState.READY, ALL))
    assert _list_shown(first, "firstSetup")


def test_first_settings_view_with_first_setup_done_goes_to_status_without_asking(
    make_window, actions
):
    window = make_window(actions)
    window.apply(SettingsView(("KRW-AAA",), 30, True))
    assert current_screen(window) is ScreenId.STATUS
    assert actions.calls == []


def test_unreadable_settings_keep_both_screens_dim_and_call_nothing(make_window, actions):
    window = make_window(actions)
    window.apply(SettingsView(state=LoadState.FAILED))
    assert current_screen(window) is ScreenId.FIRST_SETUP
    for screen, prefix, message in (
        (_first(window), "firstSetup", "firstSetupMessage"),
        (_settings(window), "settings", "settingsMessage"),
    ):
        assert _shown_text(screen, message) == UNREADABLE
        assert _tone(screen, message) == "warning"
        assert not _find(screen, QWidget, f"{prefix}Form").isEnabled()
    start = _button(_first(window), "firstSetupStart")
    save = _button(_settings(window), "settingsSave")
    assert not start.isEnabled()
    assert not save.isEnabled()
    start.click()
    save.click()
    # 처음 설정을 마쳤는지 모르므로 처음 설정을 다시 하게 하지 않는다("마친 뒤" 모습도 아니다).
    assert _find(_first(window), QWidget, "firstSetupDonePanel").isHidden()
    assert actions.calls == []
    window.apply(MarketsView(LoadState.READY, ALL))
    assert actions.calls == []


def test_a_ready_settings_view_after_unreadable_counts_as_the_first_one(make_window, actions):
    window = make_window(actions)
    window.apply(SettingsView(state=LoadState.FAILED))
    window.apply(SettingsView(("KRW-AAA",), 7, True))
    assert current_screen(window) is ScreenId.STATUS
    assert actions.calls == []
    assert _shown_text(_first(window), "firstSetupMessage") is None

    other = make_window(FakeActions())
    other.apply(SettingsView(state=LoadState.FAILED))
    other.apply(SettingsView())
    assert current_screen(other) is ScreenId.FIRST_SETUP
    assert other._actions.calls == [("refresh_markets",)]
    assert _find(_first(other), QWidget, "firstSetupForm").isEnabled()


# --- 코인 목록을 부르는 때 ---


def test_refresh_is_called_when_the_picker_is_opened_and_by_retry(make_window, actions):
    from coin_report_trader.ui import select_screen

    window = make_window(actions)
    window.apply(SettingsView(("KRW-AAA",), 30, True))
    assert actions.count("refresh_markets") == 0
    select_screen(window, ScreenId.SETTINGS)
    assert actions.count("refresh_markets") == 1
    select_screen(window, ScreenId.STATUS)
    select_screen(window, ScreenId.SETTINGS)
    assert actions.count("refresh_markets") == 2
    # 같은 화면에서 설정을 다시 받아도 부르지 않는다.
    window.apply(SettingsView(("KRW-AAA",), 30, True))
    assert actions.count("refresh_markets") == 2
    # 처음 설정을 마친 뒤의 처음 설정 화면은 코인 고르기가 없어 부르지 않는다.
    select_screen(window, ScreenId.FIRST_SETUP)
    assert actions.count("refresh_markets") == 2
    # "다시 시도"는 받지 못함에서도 일시 차단 중에서도 부른다.
    select_screen(window, ScreenId.SETTINGS)
    assert actions.count("refresh_markets") == 3
    window.apply(MarketsView(LoadState.FAILED, error_name="시간 초과"))
    _button(_settings(window), "settingsCoinsRetry").click()
    assert actions.count("refresh_markets") == 4
    window.apply(MarketsView(LoadState.FAILED, (), "일시 차단", UNTIL_1030))
    _button(_settings(window), "settingsCoinsRetry").click()
    assert actions.count("refresh_markets") == 5


def test_settings_screen_before_first_setup_is_empty_and_never_calls(make_window, actions):
    from coin_report_trader.ui import select_screen

    window = make_window(actions)
    window.apply(SettingsView())
    assert actions.count("refresh_markets") == 1  # 처음 설정 화면이 부른 것
    select_screen(window, ScreenId.SETTINGS)
    settings = _settings(window)
    assert actions.count("refresh_markets") == 1
    assert _shown_text(settings, "settingsMessage") == BEFORE_SETUP
    assert _tone(settings, "settingsMessage") == "normal"
    assert not _find(settings, QWidget, "settingsForm").isEnabled()
    assert _selected_texts(settings, "settings") == []
    assert _days(settings, "settings") is None
    save = _button(settings, "settingsSave")
    assert not save.isEnabled()
    save.click()
    assert actions.count("save_settings") == 0


# --- 코인 목록의 모습(두 화면 모두) ---


@pytest.fixture(params=["firstSetup", "settings"])
def picker_screen(request, make_window, actions):
    window = make_window(actions)
    if request.param == "firstSetup":
        window.apply(SettingsView())
        return window, _first(window), "firstSetup"
    window.apply(SettingsView(("KRW-AAA", "KRW-CCC"), 30, True))
    from coin_report_trader.ui import select_screen

    select_screen(window, ScreenId.SETTINGS)
    return window, _settings(window), "settings"


def test_markets_loading(picker_screen):
    window, screen, prefix = picker_screen
    window.apply(MarketsView(LoadState.LOADING))
    assert not _list_shown(screen, prefix)
    assert _shown_text(screen, f"{prefix}CoinsMessage") == LOADING_MARKETS
    assert _find(screen, QPushButton, f"{prefix}CoinsRetry").isHidden()


def test_markets_ready_lists_the_markets_in_order_and_marks_caution(picker_screen):
    window, screen, prefix = picker_screen
    window.apply(MarketsView(LoadState.READY, ALL))
    assert _list_shown(screen, prefix)
    boxes = [b.text() for b in screen.findChildren(QCheckBox)]
    assert boxes == ["AAA 가짜에이", "BBB 가짜비", "CCC 가짜씨", "DDD 가짜디"]
    caution = _find(screen, QLabel, f"{prefix}CoinCaution_KRW-BBB")
    assert caution.text() == "유의·주의"
    assert caution.property("tone") == "warning"
    assert screen.findChild(QLabel, f"{prefix}CoinCaution_KRW-AAA") is None
    # 유의·주의 코인도 고를 수 있다.
    _choose(screen, prefix, "KRW-BBB")
    assert "KRW-BBB" in _picks_of(screen, prefix)


def _picks_of(screen: QWidget, prefix: str) -> list[str]:
    """고른 코인의 마켓 코드(체크 상자가 켜진 줄)."""
    return [
        box.objectName().removeprefix(f"{prefix}Coin_")
        for box in screen.findChildren(QCheckBox)
        if box.isChecked()
    ]


def test_markets_ready_with_no_markets_is_an_empty_list_and_no_saving(picker_screen):
    window, screen, prefix = picker_screen
    window.apply(MarketsView(LoadState.READY, ()))
    assert _list_shown(screen, prefix)
    assert screen.findChildren(QCheckBox) == []
    if prefix == "firstSetup":
        _choose_days(screen, prefix, 30)
        assert not _button(screen, "firstSetupStart").isEnabled()


def test_markets_failed_shows_only_the_line_and_retry_and_hides_the_old_list(picker_screen):
    window, screen, prefix = picker_screen
    window.apply(MarketsView(LoadState.READY, ALL))
    assert _list_shown(screen, prefix)
    window.apply(MarketsView(LoadState.FAILED, (), "네트워크 끊김"))
    assert not _list_shown(screen, prefix)
    assert _shown_text(screen, f"{prefix}CoinsMessage") == FAILED_MARKETS
    assert not _find(screen, QPushButton, f"{prefix}CoinsRetry").isHidden()
    assert _find(screen, QPushButton, f"{prefix}CoinsRetry").text() == "다시 시도"


def test_markets_blocked_shows_the_kst_time_and_retry(picker_screen):
    window, screen, prefix = picker_screen
    window.apply(MarketsView(LoadState.READY, ALL))
    window.apply(MarketsView(LoadState.FAILED, (), "일시 차단", UNTIL_1030))
    assert not _list_shown(screen, prefix)
    assert _shown_text(screen, f"{prefix}CoinsMessage") == BLOCKED_AT_1030
    assert not _find(screen, QPushButton, f"{prefix}CoinsRetry").isHidden()


def test_blocked_without_a_time_falls_back_to_the_failed_line(picker_screen):
    window, screen, prefix = picker_screen
    window.apply(MarketsView(LoadState.FAILED, (), "일시 차단", None))
    assert _shown_text(screen, f"{prefix}CoinsMessage") == FAILED_MARKETS


def test_retry_shows_loading_until_a_non_loading_view_arrives(picker_screen, actions):
    window, screen, prefix = picker_screen
    window.apply(MarketsView(LoadState.FAILED, (), "시간 초과"))
    before = actions.count("refresh_markets")
    _button(screen, f"{prefix}CoinsRetry").click()
    assert actions.count("refresh_markets") == before + 1
    # 자료 스레드가 앞 일을 하는 동안에도 "불러오는 중"이 보인다.
    assert _shown_text(screen, f"{prefix}CoinsMessage") == LOADING_MARKETS
    assert _find(screen, QPushButton, f"{prefix}CoinsRetry").isHidden()
    window.apply(MarketsView(LoadState.LOADING))
    assert _shown_text(screen, f"{prefix}CoinsMessage") == LOADING_MARKETS
    window.apply(MarketsView(LoadState.READY, ALL))
    assert _list_shown(screen, prefix)


def test_no_data_state_is_drawn_as_a_failed_list(picker_screen):
    window, screen, prefix = picker_screen
    window.apply(MarketsView(LoadState.NO_DATA))
    assert _shown_text(screen, f"{prefix}CoinsMessage") == FAILED_MARKETS


# --- 코인 고르기 ---


@pytest.fixture(params=["firstSetup", "settings"])
def picker_ready(request, make_window, actions):
    window = make_window(actions)
    if request.param == "firstSetup":
        screen = _ready_first_setup(window)
    else:
        screen = _ready_settings(window, coins=(), days=30)
    return window, screen, request.param


def test_the_fourth_coin_cannot_be_chosen(picker_ready):
    _window, screen, prefix = picker_ready
    _choose(screen, prefix, "KRW-AAA", "KRW-BBB", "KRW-CCC")
    assert _selected_texts(screen, prefix) == ["AAA 가짜에이", "BBB 가짜비", "CCC 가짜씨"]
    fourth = _coin_box(screen, prefix, "KRW-DDD")
    assert not fourth.isEnabled()
    fourth.click()
    assert not fourth.isChecked()
    assert len(_selected_texts(screen, prefix)) == 3
    # 하나를 빼면 다시 고를 수 있다.
    _coin_box(screen, prefix, "KRW-BBB").click()
    assert fourth.isEnabled()


def test_up_down_and_remove_change_the_order(picker_ready, actions):
    _window, screen, prefix = picker_ready
    _choose(screen, prefix, "KRW-AAA", "KRW-BBB", "KRW-CCC")
    selected = _find(screen, QListWidget, f"{prefix}CoinsSelected")
    selected.setCurrentRow(2)
    _button(screen, f"{prefix}CoinUp").click()  # AAA, CCC, BBB
    _button(screen, f"{prefix}CoinUp").click()  # CCC, AAA, BBB
    assert _selected_texts(screen, prefix) == ["CCC 가짜씨", "AAA 가짜에이", "BBB 가짜비"]
    selected.setCurrentRow(0)
    _button(screen, f"{prefix}CoinDown").click()  # AAA, CCC, BBB
    assert _selected_texts(screen, prefix) == ["AAA 가짜에이", "CCC 가짜씨", "BBB 가짜비"]
    selected.setCurrentRow(1)
    _button(screen, f"{prefix}CoinRemove").click()  # AAA, BBB
    assert _selected_texts(screen, prefix) == ["AAA 가짜에이", "BBB 가짜비"]
    assert not _coin_box(screen, prefix, "KRW-CCC").isChecked()
    _choose_days(screen, prefix, 90)
    if prefix == "firstSetup":
        _choose_unit(screen, prefix, CandleUnit.MINUTE_60)
    button = "firstSetupStart" if prefix == "firstSetup" else "settingsSave"
    _button(screen, button).click()
    expected_first = prefix == "firstSetup"
    assert actions.last("save_settings") == (
        "save_settings",
        ("KRW-AAA", "KRW-BBB"),
        90,
        expected_first,
    )


def test_the_end_buttons_of_the_order_are_disabled(picker_ready):
    _window, screen, prefix = picker_ready
    _choose(screen, prefix, "KRW-AAA", "KRW-BBB")
    selected = _find(screen, QListWidget, f"{prefix}CoinsSelected")
    selected.setCurrentRow(0)
    assert not _button(screen, f"{prefix}CoinUp").isEnabled()
    assert _button(screen, f"{prefix}CoinDown").isEnabled()
    selected.setCurrentRow(1)
    assert _button(screen, f"{prefix}CoinUp").isEnabled()
    assert not _button(screen, f"{prefix}CoinDown").isEnabled()


# --- 처음 설정 화면 ---


def test_first_setup_start_needs_both_a_coin_and_a_retention(make_window, actions):
    window = make_window(actions)
    first = _ready_first_setup(window)
    start = _button(first, "firstSetupStart")
    assert start.text() == "저장하고 시작"
    assert not start.isEnabled()
    _choose(first, "firstSetup", "KRW-AAA")
    assert not start.isEnabled()
    _coin_box(first, "firstSetup", "KRW-AAA").click()  # 다시 빼면 코인이 없다
    _choose_days(first, "firstSetup", 7)
    _choose_unit(first, "firstSetup", CandleUnit.MINUTE_60)
    assert not start.isEnabled()
    _choose(first, "firstSetup", "KRW-AAA")
    assert start.isEnabled()
    # 보관 기간은 미리 고른 값이 없고, 처음 고를 때 확인 창이 없다.
    assert actions.count("save_settings") == 0


def test_first_setup_start_calls_save_settings_and_goes_to_status_when_saved(make_window, actions):
    window = make_window(actions)
    first = _ready_first_setup(window)
    _choose(first, "firstSetup", "KRW-CCC", "KRW-AAA")
    _choose_days(first, "firstSetup", 30)
    _choose_unit(first, "firstSetup", CandleUnit.MINUTE_60)
    confirm = Confirm(False)
    window.set_confirm_handler(confirm)
    _button(first, "firstSetupStart").click()
    assert actions.last("save_settings") == ("save_settings", ("KRW-CCC", "KRW-AAA"), 30, True)
    assert confirm.asked == []  # 처음 설정에는 확인 창이 없다
    assert current_screen(window) is ScreenId.FIRST_SETUP  # 결과가 오기 전
    window.apply(SaveResult("settings", ok=True, message="저장했습니다."))
    assert current_screen(window) is ScreenId.STATUS
    window.apply(SettingsView(("KRW-CCC", "KRW-AAA"), 30, True))
    assert current_screen(window) is ScreenId.STATUS
    # 처음 설정 자리의 글은 현황으로 간 뒤에 보이지 않는다("저장했습니다."는 설정 화면의 글이다).
    assert _shown_text(_settings(window), "settingsMessage") is None


def test_first_setup_goes_to_status_whichever_update_comes_first(make_window, actions):
    window = make_window(actions)
    first = _ready_first_setup(window)
    _choose(first, "firstSetup", "KRW-AAA")
    _choose_days(first, "firstSetup", 7)
    _choose_unit(first, "firstSetup", CandleUnit.MINUTE_60)
    _button(first, "firstSetupStart").click()
    window.apply(SettingsView(("KRW-AAA",), 7, True))
    assert current_screen(window) is ScreenId.STATUS
    window.apply(SaveResult("settings", ok=True, message="저장했습니다."))
    assert current_screen(window) is ScreenId.STATUS
    assert _shown_text(_settings(window), "settingsMessage") is None
    assert actions.count("save_settings") == 1


@pytest.mark.parametrize(
    "message",
    [NOT_LISTED, "저장하지 못했습니다."],
)
def test_first_setup_failed_save_stays_and_shows_the_message_in_warning_color(
    make_window, actions, message
):
    window = make_window(actions)
    first = _ready_first_setup(window)
    _choose(first, "firstSetup", "KRW-BBB", "KRW-AAA")
    _choose_days(first, "firstSetup", 90)
    _choose_unit(first, "firstSetup", CandleUnit.MINUTE_60)
    _button(first, "firstSetupStart").click()
    window.apply(SaveResult("settings", ok=False, message=message))
    assert current_screen(window) is ScreenId.FIRST_SETUP
    assert _shown_text(first, "firstSetupMessage") == message
    assert _tone(first, "firstSetupMessage") == "warning"
    # 고른 값은 칸에 남는다.
    assert _selected_texts(first, "firstSetup") == ["BBB 가짜비", "AAA 가짜에이"]
    assert _days(first, "firstSetup") == 90
    assert _button(first, "firstSetupStart").isEnabled()
    # 다시 누르면 앞의 글은 지워진다.
    _button(first, "firstSetupStart").click()
    assert _shown_text(first, "firstSetupMessage") is None
    assert actions.count("save_settings") == 2


def test_first_setup_after_it_is_done_shows_the_texts_and_the_settings_button(make_window, actions):
    window = make_window(actions)
    window.apply(SettingsView(("KRW-AAA",), 30, True))
    first = _first(window)
    from coin_report_trader.ui import select_screen

    select_screen(window, ScreenId.FIRST_SETUP)
    assert _find(first, QWidget, "firstSetupForm").isHidden()
    assert _button(first, "firstSetupStart").isHidden()
    assert not _find(first, QWidget, "firstSetupDonePanel").isHidden()
    assert (
        _find(first, QLabel, "firstSetupDone").text()
        == "처음 설정을 마쳤습니다. 값은 설정 화면에서 고칩니다."
    )
    # 10의 꼭 읽을 글은 그대로 보인다.
    assert not _find(first, QWidget, "firstSetupNotices").isHidden()
    to_settings = _button(first, "firstSetupToSettings")
    assert to_settings.text() == "설정 화면으로"
    to_settings.click()
    assert current_screen(window) is ScreenId.SETTINGS
    # 이 모습에서는 코인 목록을 받지 않는다(설정 화면이 코인 고르기를 보일 때 한 번뿐이다).
    assert actions.count("refresh_markets") == 1


def test_first_setup_read_first_texts_are_the_design_texts(make_window):
    first = _first(make_window())
    # 세 글은 '꼭 읽을 글' 제목 옆 'ⓘ'의 툴팁이고, 옛 글 라벨은 없다(M1 명세 3.14.6).
    assert _find(first, QLabel, "firstSetupNoticesTitle").text() == "꼭 읽을 글"
    assert _tip(first, "firstSetupNoticesInfo") == "\n".join(
        (
            "• 이 프로그램은 투자 자문이 아닙니다. AI 판단은 틀릴 수 있습니다.",
            "• 프로그램이나 PC가 꺼져 있거나 인터넷이 끊기면 디스코드 알림도 가지 않습니다. "
            "인터넷이 끊긴 동안 보내지 못한 알림은 다시 이어져도 디스코드로 가지 않습니다"
            "(알림센터에서 봅니다).",
            "• 디스코드 주소를 넣지 않은 분류의 알림은 디스코드로 가지 않습니다"
            "(오류 알림의 주소가 비어 있으면 오류 멈춤의 "
            '"지금 손절이 동작하지 않음"도 가지 않습니다).',
        )
    )
    for old in ("screenNotice", "noticeDiscordOffline", "noticeDiscordNoAddress"):
        assert first.findChild(QLabel, old) is None
    names = _flat(_find(first, QWidget, "firstSetupNotices").layout())
    assert names == ["firstSetupNoticesTitle", "firstSetupNoticesInfo"]
    # 이 마일스톤에 없는 글과 칸은 없다.
    texts = [label.text() for label in first.findChildren(QLabel)]
    assert not any("모의매매를 시작합니다" in text for text in texts)
    assert not any("손절" in text and "없습니다" in text for text in texts)
    assert first.findChild(QCheckBox, "firstSetupRead") is None


@pytest.mark.parametrize("prefix", ["firstSetup", "settings"])
def test_retention_choice_has_three_options_and_the_two_notices(make_window, prefix):
    window = make_window()
    screen = _first(window) if prefix == "firstSetup" else _settings(window)
    options = [
        _find(screen, QRadioButton, f"{prefix}Retention{days}").text() for days in (7, 30, 90)
    ]
    assert options == ["7일", "30일", "90일"]
    assert _days(screen, prefix) is None  # 미리 고른 값이 없다
    # 두 글은 제목 옆 'ⓘ'의 툴팁이고, 옛 글 라벨은 없다(M1 명세 3.14.6).
    assert _tip(screen, f"{prefix}RetentionInfo") == (
        "• 생긴 때부터 고른 기간이 지난 알림은 영구히 지워집니다.\n"
        "• 기간을 줄이면 새 기간이 지난 알림도 지워집니다."
    )
    assert screen.findChild(QLabel, f"{prefix}RetentionNoticeKeep") is None
    assert screen.findChild(QLabel, f"{prefix}RetentionNoticeShorten") is None
    required = _find(screen, QLabel, f"{prefix}RetentionTitle").text()
    assert required == ("알림 보관 기간*" if prefix == "firstSetup" else "알림 보관 기간")
    assert _find(screen, QLabel, f"{prefix}CoinsTitle").text() == (
        "코인*" if prefix == "firstSetup" else "코인"
    )


# --- 설정 화면 ---


def test_settings_screen_shows_the_saved_values_selected(make_window, actions):
    window = make_window(actions)
    settings = _ready_settings(window, coins=("KRW-CCC", "KRW-AAA"), days=90)
    assert _selected_texts(settings, "settings") == ["CCC 가짜씨", "AAA 가짜에이"]
    assert _days(settings, "settings") == 90
    assert _find(settings, QWidget, "settingsForm").isEnabled()
    assert _shown_text(settings, "settingsMessage") is None
    assert _button(settings, "settingsSave").text() == "저장"
    assert _button(settings, "settingsSave").isEnabled()
    # 안내 글과 되돌리기는 이 마일스톤에서 넣지 않는다.
    assert settings.findChild(QPushButton, "settingsRevert") is None


def test_settings_save_calls_save_settings_with_first_setup_false(make_window, actions):
    window = make_window(actions)
    settings = _ready_settings(window)
    _coin_box(settings, "settings", "KRW-BBB").click()
    _choose_days(settings, "settings", 90)
    confirm = Confirm(True)
    window.set_confirm_handler(confirm)
    _button(settings, "settingsSave").click()
    assert actions.last("save_settings") == (
        "save_settings",
        ("KRW-AAA", "KRW-CCC", "KRW-BBB"),
        90,
        False,
    )
    assert confirm.asked == []  # 길게 바꿀 때는 확인 창이 없다
    assert current_screen(window) is ScreenId.SETTINGS


def test_settings_saved_message_shows_and_goes_away_and_survives_the_new_view(
    make_window, actions, monkeypatch
):
    monkeypatch.setattr(main_window_module, "SAVED_MESSAGE_MS", 50)
    window = make_window(actions)
    settings = _ready_settings(window)
    timer = window._saved_timer

    def let_the_timer_run_out() -> None:
        # 앞서 `app.exec()`를 돌린 시험(`test_application`, A10)이 끝난 뒤에는 이 프로세스에서
        # Qt 타이머가 더 울리지 않으므로, 타이머가 걸려 있는지 보고 만료를 직접 알린다.
        assert timer.isActive()
        assert timer.interval() == 50
        timer.stop()
        timer.timeout.emit()

    _choose_days(settings, "settings", 90)
    _button(settings, "settingsSave").click()
    # 어느 차례로 와도 "저장했습니다."가 곧바로 지워지지 않는다.
    window.apply(SaveResult("settings", ok=True, message="저장했습니다."))
    assert _shown_text(settings, "settingsMessage") == "저장했습니다."
    assert _tone(settings, "settingsMessage") == "normal"
    window.apply(SettingsView(("KRW-AAA", "KRW-CCC"), 90, True))
    assert _shown_text(settings, "settingsMessage") == "저장했습니다."
    assert current_screen(window) is ScreenId.SETTINGS
    let_the_timer_run_out()
    assert _shown_text(settings, "settingsMessage") is None

    _choose_days(settings, "settings", 90)
    _button(settings, "settingsSave").click()
    window.apply(SettingsView(("KRW-AAA", "KRW-CCC"), 90, True))
    window.apply(SaveResult("settings", ok=True, message="저장했습니다."))
    assert _shown_text(settings, "settingsMessage") == "저장했습니다."
    assert current_screen(window) is ScreenId.SETTINGS
    let_the_timer_run_out()
    assert _shown_text(settings, "settingsMessage") is None


@pytest.mark.parametrize(
    "message",
    [NOT_LISTED, "저장하지 못했습니다."],
)
def test_the_saved_timer_does_not_clear_a_later_warning(make_window, actions, monkeypatch, message):
    monkeypatch.setattr(main_window_module, "SAVED_MESSAGE_MS", 50)
    window = make_window(actions)
    settings = _ready_settings(window)
    timer = window._saved_timer
    _choose_days(settings, "settings", 90)
    _button(settings, "settingsSave").click()
    window.apply(SaveResult("settings", ok=True, message="저장했습니다."))
    assert timer.isActive()
    # 시간 안에 다시 저장했는데 거절이나 실패가 왔다.
    _button(settings, "settingsSave").click()
    window.apply(SaveResult("settings", ok=False, message=message))
    timer.timeout.emit()  # 앞 타이머의 만료가 늦게 와도
    assert _shown_text(settings, "settingsMessage") == message
    assert _tone(settings, "settingsMessage") == "warning"
    assert not timer.isActive()


def test_the_saved_timer_does_not_clear_a_warning_that_replaced_the_saved_text(
    make_window, actions
):
    window = make_window(actions)
    settings = _ready_settings(window)
    _choose_days(settings, "settings", 90)
    _button(settings, "settingsSave").click()
    window.apply(SaveResult("settings", ok=True, message="저장했습니다."))
    # 다시 저장하지 않고 거짓 결과가 먼저 와도(차례가 어긋나도) 경고 글은 남는다.
    window.apply(SaveResult("settings", ok=False, message="저장하지 못했습니다."))
    window._saved_timer.timeout.emit()
    assert _shown_text(settings, "settingsMessage") == "저장하지 못했습니다."


def test_settings_failed_save_shows_the_message_in_warning_color_and_keeps_the_values(
    make_window, actions
):
    window = make_window(actions)
    settings = _ready_settings(window)
    _coin_box(settings, "settings", "KRW-DDD").click()
    _choose_days(settings, "settings", 7)
    # 7일은 30일보다 짧으므로 확인 창이 뜬다.
    window.set_confirm_handler(Confirm(True))
    _button(settings, "settingsSave").click()
    window.apply(SaveResult("settings", ok=False, message=NOT_LISTED))
    assert _shown_text(settings, "settingsMessage") == NOT_LISTED
    assert _tone(settings, "settingsMessage") == "warning"
    assert _selected_texts(settings, "settings") == ["AAA 가짜에이", "CCC 가짜씨", "DDD 가짜디"]
    assert _days(settings, "settings") == 7
    window.apply(SaveResult("settings", ok=False, message="저장하지 못했습니다."))
    assert _shown_text(settings, "settingsMessage") == "저장하지 못했습니다."


def test_a_failed_save_then_the_same_settings_view_keeps_the_edits(make_window, actions):
    window = make_window(actions)
    settings = _ready_settings(window)
    _coin_box(settings, "settings", "KRW-BBB").click()
    _choose_days(settings, "settings", 90)
    _button(settings, "settingsSave").click()
    window.apply(SaveResult("settings", ok=False, message="저장하지 못했습니다."))
    window.apply(SettingsView(("KRW-AAA", "KRW-CCC"), 30, True))  # 저장된 값은 그대로
    assert _selected_texts(settings, "settings") == ["AAA 가짜에이", "CCC 가짜씨", "BBB 가짜비"]
    assert _days(settings, "settings") == 90
    assert _shown_text(settings, "settingsMessage") == "저장하지 못했습니다."


def test_a_new_settings_view_replaces_the_values_on_both_screens(make_window, actions):
    window = make_window(actions)
    settings = _ready_settings(window)
    window.apply(SettingsView(("KRW-DDD",), 7, True))
    assert _selected_texts(settings, "settings") == ["DDD 가짜디"]
    assert _days(settings, "settings") == 7


def test_save_result_without_a_message_and_other_targets_do_not_raise(make_window, actions):
    window = make_window(actions)
    settings = _ready_settings(window)
    window.apply(SaveResult("settings", ok=True))
    window.apply(SaveResult("settings", ok=False))
    assert _shown_text(settings, "settingsMessage") is None
    window.apply(SaveResult("alert_delete", ok=False, message="지우지 못했습니다."))
    assert _shown_text(settings, "settingsMessage") is None
    assert _shown_text(_first(window), "firstSetupMessage") is None


def test_updates_for_other_screens_are_ignored_without_errors(make_window, actions):
    from coin_report_trader.core import AlertCategory, AlertsView, CoinView, WebhookView

    window = make_window(actions)
    window.apply(SettingsView())
    window.apply(CoinView("KRW-AAA", LoadState.LOADING))
    window.apply(AlertsView(()))
    window.apply(WebhookView(AlertCategory.OTHER, saved=False))
    assert actions.calls == [("refresh_markets",)]


def test_saving_only_the_retention_while_the_list_is_hidden_sends_the_saved_coins(
    make_window, actions
):
    window = make_window(actions)
    settings = _ready_settings(window, coins=("KRW-CCC", "KRW-AAA"), days=30)
    window.apply(MarketsView(LoadState.FAILED, (), "네트워크 끊김"))
    assert not _list_shown(settings, "settings")
    _choose_days(settings, "settings", 90)
    _button(settings, "settingsSave").click()
    assert actions.last("save_settings") == ("save_settings", ("KRW-CCC", "KRW-AAA"), 90, False)
    # 일시 차단 중이어도 같다.
    window.apply(MarketsView(LoadState.FAILED, (), "일시 차단", UNTIL_1030))
    _choose_days(settings, "settings", 30)
    window.set_confirm_handler(Confirm(True))
    _button(settings, "settingsSave").click()
    assert actions.last("save_settings") == ("save_settings", ("KRW-CCC", "KRW-AAA"), 30, False)


def test_saved_coins_that_are_not_in_the_list_are_kept_and_can_be_removed(make_window, actions):
    window = make_window(actions)
    settings = _ready_settings(window, coins=("KRW-ZZZ", "KRW-AAA"), days=30)
    assert _selected_texts(settings, "settings") == ["ZZZ", "AAA 가짜에이"]
    _button(settings, "settingsSave").click()
    assert actions.last("save_settings")[1] == ("KRW-ZZZ", "KRW-AAA")
    _find(settings, QListWidget, "settingsCoinsSelected").setCurrentRow(0)
    _button(settings, "settingsCoinRemove").click()
    assert _selected_texts(settings, "settings") == ["AAA 가짜에이"]


def test_settings_save_is_disabled_without_a_coin(make_window, actions):
    window = make_window(actions)
    settings = _ready_settings(window, coins=("KRW-AAA",), days=30)
    _coin_box(settings, "settings", "KRW-AAA").click()
    assert not _button(settings, "settingsSave").isEnabled()
    _button(settings, "settingsSave").click()
    assert actions.count("save_settings") == 0


# --- 보관 기간을 줄일 때의 확인 창 ---


def test_shortening_the_retention_asks_and_cancel_calls_nothing_and_keeps_the_edits(
    make_window, actions
):
    window = make_window(actions)
    settings = _ready_settings(window, coins=("KRW-AAA",), days=30)
    _coin_box(settings, "settings", "KRW-BBB").click()  # 같이 고친 코인
    _choose_days(settings, "settings", 7)
    cancel = Confirm(False)
    window.set_confirm_handler(cancel)
    _button(settings, "settingsSave").click()
    assert cancel.asked == [
        (
            SHRINK_TITLE,
            "알림 보관 기간을 30일에서 7일로 줄이면, 생긴 지 7일이 지난 알림이 바로 영구히 "
            "지워집니다. 보고서, 판단, 주문, 체결, 외부 변동의 기록은 지워지지 않습니다.",
        )
    ]
    assert actions.count("save_settings") == 0
    assert _days(settings, "settings") == 7
    assert _selected_texts(settings, "settings") == ["AAA 가짜에이", "BBB 가짜비"]
    assert _shown_text(settings, "settingsMessage") is None

    confirm = Confirm(True)
    window.set_confirm_handler(confirm)
    _button(settings, "settingsSave").click()
    assert len(confirm.asked) == 1
    assert actions.last("save_settings") == ("save_settings", ("KRW-AAA", "KRW-BBB"), 7, False)


@pytest.mark.parametrize("new_days", [30, 90])
def test_the_same_or_a_longer_retention_does_not_ask(make_window, actions, new_days):
    window = make_window(actions)
    settings = _ready_settings(window, days=30)
    confirm = Confirm(False)
    window.set_confirm_handler(confirm)
    _choose_days(settings, "settings", new_days)
    _button(settings, "settingsSave").click()
    assert confirm.asked == []
    assert actions.last("save_settings")[2] == new_days


def test_choosing_a_retention_for_the_first_time_does_not_ask(make_window, actions):
    window = make_window(actions)
    settings = _ready_settings(window, coins=("KRW-AAA",), days=None)
    assert not _button(settings, "settingsSave").isEnabled()
    _choose_days(settings, "settings", 7)
    confirm = Confirm(False)
    window.set_confirm_handler(confirm)
    _button(settings, "settingsSave").click()
    assert confirm.asked == []
    assert actions.last("save_settings") == ("save_settings", ("KRW-AAA",), 7, False)


def test_the_confirm_compares_with_the_newest_saved_retention(make_window, actions):
    window = make_window(actions)
    settings = _ready_settings(window, days=30)
    confirm = Confirm(True)
    window.set_confirm_handler(confirm)
    _choose_days(settings, "settings", 90)
    _button(settings, "settingsSave").click()
    # 저장되어 새 `SettingsView`가 오면 그 값과 견준다(결과가 먼저 와도, 나중에 와도 같다).
    window.apply(SettingsView(("KRW-AAA", "KRW-CCC"), 90, True))
    window.apply(SaveResult("settings", ok=True, message="저장했습니다."))
    assert confirm.asked == []
    _choose_days(settings, "settings", 30)
    _button(settings, "settingsSave").click()
    assert len(confirm.asked) == 1
    assert "90일에서 30일로" in confirm.asked[0][1]


def test_the_real_confirm_box_has_cancel_as_the_default_button(make_window, actions, monkeypatch):
    """진짜 확인 창: 제목, 본문, 버튼 둘, "취소"가 기본 버튼(창은 띄우지 않고 누른 것으로 친다)."""
    window = make_window(actions)
    window.set_confirm_handler(None)  # 진짜 확인 창을 쓴다
    settings = _ready_settings(window, coins=("KRW-AAA",), days=90)
    seen: list[QMessageBox] = []

    def fake_exec(self: QMessageBox, press: str = "confirmCancel") -> int:
        seen.append(self)
        _find(self, QPushButton, seen_press[0]).click()
        return 0

    seen_press = ["confirmCancel"]
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    _choose_days(settings, "settings", 30)
    _button(settings, "settingsSave").click()
    assert actions.count("save_settings") == 0
    (box,) = seen
    assert box.windowTitle() == SHRINK_TITLE
    assert "90일에서 30일로 줄이면" in box.text()
    assert [b.text() for b in box.buttons()] == ["확인", "취소"]
    assert box.defaultButton().objectName() == "confirmCancel"
    assert box.escapeButton().objectName() == "confirmCancel"

    seen_press[0] = "confirmOk"
    _button(settings, "settingsSave").click()
    assert actions.last("save_settings") == ("save_settings", ("KRW-AAA",), 30, False)


def test_build_confirm_box_does_not_show_the_box(qapp):
    box = build_confirm_box(None, "제목", "본문")
    try:
        assert not box.isVisible()
        assert box.text() == "본문"
        assert [b.text() for b in box.buttons()] == ["확인", "취소"]
        assert box.defaultButton().text() == "취소"
    finally:
        box.deleteLater()
