"""봉 단위와 봉 개수 칸, 봉 개수의 툴팁, 예전 기록의 처음 설정(M1 명세 3.14.1~3.14.4)의 시험.

값은 모두 시험이 만든 것이다. 가짜 `UiActions`는 부른 동작과 인자를 기록한다. 창을 띄우지 않으므로
보이는지는 `isHidden()`으로, 흐린지는 `isEnabled()`로 본다.
"""

import pytest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from coin_report_trader.core import (
    CANDLE_COUNTS,
    AiSlot,
    LoadState,
    Market,
    MarketsView,
    SaveResult,
    ScreenId,
    SettingsView,
    UiActions,
    UiContext,
)
from coin_report_trader.core.market import CandleUnit
from coin_report_trader.ui import (
    MainWindow,
    create_main_window,
    current_screen,
    screen_widget,
    select_screen,
)
from coin_report_trader.ui.info_icon import InfoIcon
from coin_report_trader.ui.texts import candle_count_tooltip

AAA = Market("KRW-AAA", "가짜에이", caution=False)
BBB = Market("KRW-BBB", "가짜비", caution=False)
ALL = (AAA, BBB)

NOTE = "봉 통계를 셈할 봉의 단위와 개수입니다."
LEGACY_NOTICE = (
    '봉 단위와 봉 개수가 새로 생겼습니다. 고른 뒤 "저장하고 시작"을 누르세요. '
    "그 전에는 봉을 받지 않습니다."
)
UNITS = (
    CandleUnit.MINUTE_30,
    CandleUnit.MINUTE_60,
    CandleUnit.MINUTE_240,
    CandleUnit.DAY,
)


class FakeActions:
    """부른 동작을 `calls`에, `save_settings`의 봉 단위와 개수를 `candles`에 적는다."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.candles: list[tuple] = []

    def count(self, name: str) -> int:
        return sum(1 for call in self.calls if call[0] == name)

    def refresh_markets(self) -> None:
        self.calls.append(("refresh_markets",))

    def save_settings(
        self,
        coins,
        retention_days,
        *,
        first_setup,
        candle_unit=None,
        candle_count=None,
        ai_choices=None,
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


def _unexpected_confirm(title: str, body: str) -> bool:
    raise AssertionError(f"예상하지 못한 확인 창: {title}")


@pytest.fixture
def make_window(qapp: QApplication):
    made: list[MainWindow] = []

    def factory(actions: UiActions | None = None) -> MainWindow:
        window = create_main_window(UiContext("0.0.1", "X", actions))
        window.set_confirm_handler(_unexpected_confirm)
        made.append(window)
        return window

    yield factory
    for window in made:
        window.deleteLater()


@pytest.fixture
def actions() -> FakeActions:
    return FakeActions()


def _find(parent: QWidget, kind: type, name: str):
    found = parent.findChild(kind, name)
    assert found is not None, name
    return found


def _tip(screen: QWidget, name: str) -> str | None:
    """'ⓘ'가 보이면 그 툴팁 글, 숨겨져 있으면 `None`. 읽는 설명도 같아야 한다."""
    icon = _find(screen, InfoIcon, name)
    assert icon.accessibleDescription() == icon.toolTip()
    return None if icon.isHidden() else icon.toolTip()


def _flat(layout) -> list[str]:
    """레이아웃 안 위젯의 `objectName`을 놓인 차례대로(카드와 안쪽 레이아웃까지)."""
    names: list[str] = []
    for index in range(layout.count()):
        item = layout.itemAt(index)
        widget = item.widget()
        if widget is not None:
            names.append(widget.objectName())
            if widget.layout() is not None:
                names.extend(_flat(widget.layout()))
        elif item.layout() is not None:
            names.extend(_flat(item.layout()))
    return names


def _choose_ai(screen: QWidget, prefix: str = "firstSetup") -> None:
    """여덟 칸(네 칸의 모델과 노력 값)을 모두 고른다(M2 명세 3.12.2)."""
    for slot in AiSlot:
        _find(screen, QComboBox, f"{prefix}AiModel_{slot.value}").setCurrentIndex(1)
        _find(screen, QComboBox, f"{prefix}AiEffort_{slot.value}").setCurrentIndex(1)


def _radio(screen: QWidget, name: str) -> QRadioButton:
    return _find(screen, QRadioButton, name)


def _unit_of(screen: QWidget, prefix: str) -> CandleUnit | None:
    checked = [u for u in UNITS if _radio(screen, f"{prefix}Unit_{u.value}").isChecked()]
    assert len(checked) <= 1
    return checked[0] if checked else None


def _count_of(screen: QWidget, prefix: str) -> int | None:
    checked = [n for n in CANDLE_COUNTS if _radio(screen, f"{prefix}Count_{n}").isChecked()]
    assert len(checked) <= 1
    return checked[0] if checked else None


def _first(window: MainWindow) -> QWidget:
    return screen_widget(window, ScreenId.FIRST_SETUP)


def _settings(window: MainWindow) -> QWidget:
    return screen_widget(window, ScreenId.SETTINGS)


def _text(screen: QWidget, name: str) -> str | None:
    label = _find(screen, QLabel, name)
    return None if label.isHidden() else label.text()


def _pick(screen: QWidget, prefix: str, *markets: str) -> None:
    for market in markets:
        _find(screen, QCheckBox, f"{prefix}Coin_{market}").click()


def _ready_first_setup(window: MainWindow) -> QWidget:
    window.apply(SettingsView())
    window.apply(MarketsView(LoadState.READY, ALL))
    return _first(window)


def _ready_settings(window: MainWindow, view: SettingsView) -> QWidget:
    window.apply(view)
    window.apply(MarketsView(LoadState.READY, ALL))
    select_screen(window, ScreenId.SETTINGS)
    return _settings(window)


# --- 봉 개수의 툴팁(3.14.2) ---

HIT_RATES = {
    None: "50~56%",
    CandleUnit.MINUTE_30: "50~56%",
    CandleUnit.MINUTE_60: "50~55%",
    CandleUnit.MINUTE_240: "50~56%",
    CandleUnit.DAY: "48~53%",
}
MEANINGS = {
    100: "근거가 받쳐 주는 가장 적은 개수입니다.",
    200: "거래소에서 한 번에 받는 가장 많은 개수입니다.",
    500: "더 긴 기간을 보지만 요청이 3번 들고 오래된 구간이 섞입니다.",
}
PERIODS = {
    None: dict.fromkeys(CANDLE_COUNTS, "봉 단위를 고르면 보입니다."),
    CandleUnit.MINUTE_30: {100: "약 2.1일", 200: "약 4.2일", 500: "약 10.4일"},
    CandleUnit.MINUTE_60: {100: "약 4.2일", 200: "약 8.3일", 500: "약 20.8일"},
    CandleUnit.MINUTE_240: {100: "약 16.7일", 200: "약 33일", 500: "약 83일"},
    CandleUnit.DAY: {100: "100일", 200: "약 6.6개월", 500: "약 1.4년"},
}


@pytest.mark.parametrize("unit", [None, *UNITS])
@pytest.mark.parametrize("count", CANDLE_COUNTS)
def test_the_tooltip_has_three_lines_from_the_spec_tables(unit, count):
    lines = candle_count_tooltip(unit, count).split("\n")
    assert lines == [
        f"예상 적중률(참고값): {HIT_RATES[unit]}. 공개 연구에서 오르내림을 맞힌 비율이고, "
        "이 앱이 잰 값이 아닙니다. 수익을 뜻하지 않습니다.",
        f"뜻: {MEANINGS[count]}",
        f"덮는 기간: {PERIODS[unit][count]}",
    ]


def test_the_tooltip_example_of_the_spec():
    assert candle_count_tooltip(CandleUnit.MINUTE_60, 200).split("\n") == [
        "예상 적중률(참고값): 50~55%. 공개 연구에서 오르내림을 맞힌 비율이고, "
        "이 앱이 잰 값이 아닙니다. 수익을 뜻하지 않습니다.",
        "뜻: 거래소에서 한 번에 받는 가장 많은 개수입니다.",
        "덮는 기간: 약 8.3일",
    ]


@pytest.mark.parametrize("count", [0, 99, 150, 1000, -200])
def test_the_tooltip_refuses_a_count_that_cannot_be_chosen(count):
    with pytest.raises(ValueError, match="봉 개수"):
        candle_count_tooltip(CandleUnit.DAY, count)


# --- 처음 설정 화면의 두 칸(3.14.1) ---


def test_first_setup_shows_four_units_and_three_counts_in_order(make_window, actions):
    window = make_window(actions)
    first = _ready_first_setup(window)
    layout = _find(first, QWidget, "firstSetupCandle").layout()
    rows = [layout.itemAt(i) for i in range(layout.count())]
    # 칸 이름 줄(이름과 'ⓘ'), 봉 단위 줄, 칸 이름 줄, 봉 개수 줄의 차례다.
    assert _text(first, "firstSetupUnitTitle") == "봉 단위*"
    assert _text(first, "firstSetupCountTitle") == "봉 개수*"
    assert _tip(first, "firstSetupCandleNote") == NOTE
    assert [item.layout() is not None for item in rows] == [True] * 4
    unit_title_row = rows[0].layout()
    assert [
        unit_title_row.itemAt(i).widget().objectName()
        for i in range(unit_title_row.count())
        if unit_title_row.itemAt(i).widget() is not None
    ] == ["firstSetupUnitTitle", "firstSetupCandleNote"]
    unit_row, count_row = rows[1].layout(), rows[3].layout()
    assert [unit_row.itemAt(i).widget().text() for i in range(4)] == [
        "30분봉",
        "60분봉",
        "240분봉",
        "일봉",
    ]
    assert [unit_row.itemAt(i).widget().objectName() for i in range(4)] == [
        "firstSetupUnit_minute30",
        "firstSetupUnit_minute60",
        "firstSetupUnit_minute240",
        "firstSetupUnit_day",
    ]
    assert [count_row.itemAt(i).widget().text() for i in range(3)] == ["100개", "200개", "500개"]
    assert [count_row.itemAt(i).widget().objectName() for i in range(3)] == [
        "firstSetupCount_100",
        "firstSetupCount_200",
        "firstSetupCount_500",
    ]


def test_first_setup_order_is_coins_then_unit_and_count_then_retention(make_window, actions):
    window = make_window(actions)
    first = _ready_first_setup(window)
    order = _flat(_find(first, QWidget, "firstSetupForm").layout())
    parts = ["firstSetupCoins", "firstSetupCandle", "firstSetupRetention"]
    assert [name for name in order if name in parts] == parts


def test_first_setup_starts_with_no_unit_and_200_counts(make_window, actions):
    window = make_window(actions)
    first = _ready_first_setup(window)
    assert _unit_of(first, "firstSetup") is None
    assert _count_of(first, "firstSetup") == 200


def test_the_unit_and_the_count_are_exclusive_choices(make_window, actions):
    window = make_window(actions)
    first = _ready_first_setup(window)
    _radio(first, "firstSetupUnit_minute240").click()
    _radio(first, "firstSetupUnit_day").click()
    assert _unit_of(first, "firstSetup") is CandleUnit.DAY
    _radio(first, "firstSetupCount_500").click()
    _radio(first, "firstSetupCount_100").click()
    assert _count_of(first, "firstSetup") == 100


def test_first_setup_start_needs_a_coin_a_retention_and_a_unit(make_window, actions):
    window = make_window(actions)
    first = _ready_first_setup(window)
    start = _find(first, QPushButton, "firstSetupStart")
    assert not start.isEnabled()
    _pick(first, "firstSetup", "KRW-AAA")
    _find(first, QRadioButton, "firstSetupRetention30").click()
    _choose_ai(first)
    # 코인과 보관 기간이 있어도 봉 단위가 없으면 누를 수 없다(봉 개수는 처음부터 골라져 있다).
    assert not start.isEnabled()
    start.click()
    assert actions.count("save_settings") == 0
    _radio(first, "firstSetupUnit_minute60").click()
    assert start.isEnabled()
    # 셋 가운데 하나라도 빠지면 다시 눌리지 않는다.
    _pick(first, "firstSetup", "KRW-AAA")  # 코인을 뺀다
    assert not start.isEnabled()
    _pick(first, "firstSetup", "KRW-AAA")
    assert start.isEnabled()


@pytest.mark.parametrize("unit", UNITS)
@pytest.mark.parametrize("count", CANDLE_COUNTS)
def test_first_setup_start_sends_the_chosen_unit_and_count(make_window, actions, unit, count):
    window = make_window(actions)
    first = _ready_first_setup(window)
    _pick(first, "firstSetup", "KRW-BBB", "KRW-AAA")
    _find(first, QRadioButton, "firstSetupRetention7").click()
    _choose_ai(first)
    _radio(first, f"firstSetupUnit_{unit.value}").click()
    _radio(first, f"firstSetupCount_{count}").click()
    _find(first, QPushButton, "firstSetupStart").click()
    assert actions.calls[-1] == ("save_settings", ("KRW-BBB", "KRW-AAA"), 7, True)
    assert actions.candles == [(unit, count)]


def test_first_setup_start_sends_the_default_count_when_it_is_not_touched(make_window, actions):
    window = make_window(actions)
    first = _ready_first_setup(window)
    _pick(first, "firstSetup", "KRW-AAA")
    _find(first, QRadioButton, "firstSetupRetention30").click()
    _choose_ai(first)
    _radio(first, "firstSetupUnit_day").click()
    _find(first, QPushButton, "firstSetupStart").click()
    assert actions.candles == [(CandleUnit.DAY, 200)]


def test_a_failed_save_keeps_the_unit_and_count(make_window, actions):
    window = make_window(actions)
    first = _ready_first_setup(window)
    _pick(first, "firstSetup", "KRW-AAA")
    _find(first, QRadioButton, "firstSetupRetention30").click()
    _choose_ai(first)
    _radio(first, "firstSetupUnit_minute240").click()
    _radio(first, "firstSetupCount_500").click()
    _find(first, QPushButton, "firstSetupStart").click()
    window.apply(SaveResult("settings", ok=False, message="저장하지 못했습니다."))
    assert current_screen(window) is ScreenId.FIRST_SETUP
    assert _unit_of(first, "firstSetup") is CandleUnit.MINUTE_240
    assert _count_of(first, "firstSetup") == 500
    assert _find(first, QPushButton, "firstSetupStart").isEnabled()


# --- 툴팁이 칸에 붙는 모습(3.14.2) ---


@pytest.mark.parametrize("prefix", ["firstSetup", "settings"])
def test_every_count_has_a_tooltip_that_follows_the_chosen_unit(make_window, actions, prefix):
    window = make_window(actions)
    if prefix == "firstSetup":
        screen = _ready_first_setup(window)
    else:
        screen = _ready_settings(window, SettingsView(("KRW-AAA",), 30, True))
    for count in CANDLE_COUNTS:
        assert _radio(screen, f"{prefix}Count_{count}").toolTip() == candle_count_tooltip(
            _unit_of(screen, prefix), count
        )
    for unit in (CandleUnit.MINUTE_30, CandleUnit.DAY, CandleUnit.MINUTE_240):
        _radio(screen, f"{prefix}Unit_{unit.value}").click()
        for count in CANDLE_COUNTS:
            tip = _radio(screen, f"{prefix}Count_{count}").toolTip()
            assert tip == candle_count_tooltip(unit, count)
            assert f"덮는 기간: {PERIODS[unit][count]}" in tip.split("\n")


def test_the_tooltips_before_a_unit_is_chosen_say_so(make_window, actions):
    window = make_window(actions)
    first = _ready_first_setup(window)
    for count in CANDLE_COUNTS:
        lines = _radio(first, f"firstSetupCount_{count}").toolTip().split("\n")
        assert lines[2] == "덮는 기간: 봉 단위를 고르면 보입니다."
        assert lines[0].startswith("예상 적중률(참고값): 50~56%.")


# --- 설정 화면의 두 칸(3.14.1) ---


def test_settings_shows_the_saved_unit_and_count_and_sends_them_on_save(make_window, actions):
    window = make_window(actions)
    settings = _ready_settings(
        window,
        SettingsView(("KRW-AAA",), 30, True, candle_unit=CandleUnit.MINUTE_240, candle_count=500),
    )
    assert _unit_of(settings, "settings") is CandleUnit.MINUTE_240
    assert _count_of(settings, "settings") == 500
    assert _text(settings, "settingsUnitTitle") == "봉 단위*"
    assert _text(settings, "settingsCountTitle") == "봉 개수*"
    assert _tip(settings, "settingsCandleNote") == NOTE
    _find(settings, QPushButton, "settingsSave").click()
    assert actions.candles == [(CandleUnit.MINUTE_240, 500)]
    _radio(settings, "settingsUnit_day").click()
    _radio(settings, "settingsCount_100").click()
    _find(settings, QPushButton, "settingsSave").click()
    assert actions.candles[-1] == (CandleUnit.DAY, 100)
    assert actions.calls[-1] == ("save_settings", ("KRW-AAA",), 30, False)


def test_settings_changing_only_the_count_does_not_ask_for_confirmation(make_window, actions):
    window = make_window(actions)
    settings = _ready_settings(
        window,
        SettingsView(("KRW-AAA",), 30, True, candle_unit=CandleUnit.MINUTE_60, candle_count=200),
    )
    _radio(settings, "settingsCount_500").click()
    _find(settings, QPushButton, "settingsSave").click()
    assert actions.candles == [(CandleUnit.MINUTE_60, 500)]


def test_settings_sends_no_unit_when_none_was_saved_and_none_is_chosen(make_window, actions):
    window = make_window(actions)
    settings = _ready_settings(window, SettingsView(("KRW-AAA",), 30, True))
    assert _unit_of(settings, "settings") is None
    assert _count_of(settings, "settings") == 200
    _find(settings, QPushButton, "settingsSave").click()
    assert actions.candles == [(None, 200)]


def test_a_new_settings_view_replaces_the_unit_and_count(make_window, actions):
    window = make_window(actions)
    settings = _ready_settings(
        window,
        SettingsView(("KRW-AAA",), 30, True, candle_unit=CandleUnit.MINUTE_30, candle_count=100),
    )
    _radio(settings, "settingsUnit_day").click()  # 고치던 값은 새 값이 오면 버려진다
    window.apply(
        SettingsView(("KRW-AAA",), 30, True, candle_unit=CandleUnit.MINUTE_240, candle_count=500)
    )
    assert _unit_of(settings, "settings") is CandleUnit.MINUTE_240
    assert _count_of(settings, "settings") == 500


def test_settings_before_first_setup_has_empty_dim_unit_and_count(make_window, actions):
    window = make_window(actions)
    window.apply(SettingsView())
    select_screen(window, ScreenId.SETTINGS)
    settings = _settings(window)
    assert _unit_of(settings, "settings") is None
    assert _count_of(settings, "settings") is None
    assert not _find(settings, QWidget, "settingsCoins").isEnabled()
    for unit in UNITS:
        assert not _radio(settings, f"settingsUnit_{unit.value}").isEnabled()
    for count in CANDLE_COUNTS:
        assert not _radio(settings, f"settingsCount_{count}").isEnabled()
    assert not _find(settings, QPushButton, "settingsSave").isEnabled()


@pytest.mark.parametrize("prefix", ["firstSetup", "settings"])
def test_the_two_fields_are_dim_while_loading_and_when_the_settings_are_unreadable(
    make_window, actions, prefix
):
    window = make_window(actions)
    screen = _first(window) if prefix == "firstSetup" else _settings(window)
    for unit in UNITS:  # 첫 `SettingsView` 전
        assert not _radio(screen, f"{prefix}Unit_{unit.value}").isEnabled()
    assert not _radio(screen, f"{prefix}Count_200").isEnabled()
    window.apply(SettingsView(state=LoadState.FAILED))
    for unit in UNITS:
        assert not _radio(screen, f"{prefix}Unit_{unit.value}").isEnabled()
    for count in CANDLE_COUNTS:
        assert not _radio(screen, f"{prefix}Count_{count}").isEnabled()


# --- 예전 기록일 때의 처음 설정(3.14.4) ---


def _legacy_window(make_window, actions) -> tuple[MainWindow, QWidget]:
    window = make_window(actions)
    window.apply(SettingsView(("KRW-BBB", "KRW-AAA"), 90, False))
    window.apply(MarketsView(LoadState.READY, ALL))
    return window, _first(window)


def test_a_legacy_record_stays_on_first_setup_with_the_saved_values_filled(make_window, actions):
    window, first = _legacy_window(make_window, actions)
    assert current_screen(window) is ScreenId.FIRST_SETUP
    assert _text(first, "firstSetupLegacyNotice") == LEGACY_NOTICE
    assert _find(first, QCheckBox, "firstSetupCoin_KRW-AAA").isChecked()
    assert _find(first, QCheckBox, "firstSetupCoin_KRW-BBB").isChecked()
    assert _find(first, QRadioButton, "firstSetupRetention90").isChecked()
    assert _unit_of(first, "firstSetup") is None
    assert _count_of(first, "firstSetup") == 200
    # 그 자리는 처음 설정의 2 자리(`firstSetupMessage`) 바로 아래다.
    layout = first.layout()
    names = [
        layout.itemAt(i).widget().objectName()
        for i in range(layout.count())
        if layout.itemAt(i).widget() is not None
    ]
    assert names.index("firstSetupMessage") + 1 == names.index("firstSetupLegacyNotice")
    assert names.index("firstSetupLegacyNotice") < names.index("firstSetupForm")


def test_a_legacy_record_locks_the_coins_and_the_retention_but_not_the_candle_fields(
    make_window, actions
):
    _window, first = _legacy_window(make_window, actions)
    for market in ("KRW-AAA", "KRW-BBB"):
        assert not _find(first, QCheckBox, f"firstSetupCoin_{market}").isEnabled()
    assert not _find(first, QLineEdit, "firstSetupCoinSearch").isEnabled()
    for name in ("firstSetupCoinUp", "firstSetupCoinDown", "firstSetupCoinRemove"):
        assert not _find(first, QPushButton, name).isEnabled()
    for days in (7, 30, 90):
        assert not _find(first, QRadioButton, f"firstSetupRetention{days}").isEnabled()
    for unit in UNITS:
        assert _radio(first, f"firstSetupUnit_{unit.value}").isEnabled()
    for count in CANDLE_COUNTS:
        assert _radio(first, f"firstSetupCount_{count}").isEnabled()


def test_a_legacy_record_cannot_change_the_coins_or_the_retention_by_clicking(make_window, actions):
    _window, first = _legacy_window(make_window, actions)
    _find(first, QCheckBox, "firstSetupCoin_KRW-AAA").click()
    _find(first, QRadioButton, "firstSetupRetention7").click()
    _choose_ai(first)
    assert _find(first, QCheckBox, "firstSetupCoin_KRW-AAA").isChecked()
    assert _find(first, QRadioButton, "firstSetupRetention90").isChecked()
    assert not _find(first, QRadioButton, "firstSetupRetention7").isChecked()


def test_a_legacy_record_starts_with_the_saved_coins_and_retention_and_the_chosen_candles(
    make_window, actions
):
    window, first = _legacy_window(make_window, actions)
    start = _find(first, QPushButton, "firstSetupStart")
    assert start.text() == "저장하고 시작"
    assert not start.isEnabled()  # 봉 단위가 비어 있다
    start.click()
    assert actions.count("save_settings") == 0
    _radio(first, "firstSetupUnit_minute30").click()
    assert not start.isEnabled()  # 여덟 칸도 골라야 한다(M2 명세 3.12.2)
    _choose_ai(first)
    assert start.isEnabled()
    _radio(first, "firstSetupCount_100").click()
    start.click()
    # 저장된 코인(저장된 차례 그대로)과 보관 기간을 그대로 넘긴다.
    assert actions.calls[-1] == ("save_settings", ("KRW-BBB", "KRW-AAA"), 90, True)
    assert actions.candles == [(CandleUnit.MINUTE_30, 100)]
    window.apply(SaveResult("settings", ok=True, message="저장했습니다."))
    assert current_screen(window) is ScreenId.STATUS


def test_the_notice_and_the_locks_go_away_once_the_first_setup_is_done(make_window, actions):
    window, first = _legacy_window(make_window, actions)
    _radio(first, "firstSetupUnit_day").click()
    _choose_ai(first)
    _find(first, QPushButton, "firstSetupStart").click()
    window.apply(
        SettingsView(("KRW-BBB", "KRW-AAA"), 90, True, candle_unit=CandleUnit.DAY, candle_count=200)
    )
    assert _text(first, "firstSetupLegacyNotice") is None
    assert current_screen(window) is ScreenId.STATUS
    assert not _find(first, QWidget, "firstSetupDonePanel").isHidden()
    assert _find(first, QWidget, "firstSetupForm").isHidden()


def test_a_record_without_coins_is_not_a_legacy_record(make_window, actions):
    window = make_window(actions)
    window.apply(SettingsView())
    window.apply(MarketsView(LoadState.READY, ALL))
    first = _first(window)
    assert _text(first, "firstSetupLegacyNotice") is None
    assert _find(first, QCheckBox, "firstSetupCoin_KRW-AAA").isEnabled()
    assert _find(first, QLineEdit, "firstSetupCoinSearch").isEnabled()
    assert _find(first, QRadioButton, "firstSetupRetention7").isEnabled()


def test_an_unreadable_record_is_not_a_legacy_record(make_window, actions):
    window = make_window(actions)
    window.apply(SettingsView(("KRW-AAA",), 30, False, state=LoadState.FAILED))
    first = _first(window)
    assert _text(first, "firstSetupLegacyNotice") is None
    assert not _find(first, QWidget, "firstSetupCoins").isEnabled()


def test_the_notice_is_not_shown_before_the_first_settings_view(make_window, actions):
    window = make_window(actions)
    assert _text(_first(window), "firstSetupLegacyNotice") is None


def test_a_legacy_record_shows_the_saved_coins_in_the_selected_list(make_window, actions):
    _window, first = _legacy_window(make_window, actions)
    from PySide6.QtWidgets import QListWidget

    selected = _find(first, QListWidget, "firstSetupCoinsSelected")
    assert [selected.item(i).text() for i in range(selected.count())] == [
        "BBB 가짜비",
        "AAA 가짜에이",
    ]
    assert isinstance(first.layout(), QVBoxLayout)
