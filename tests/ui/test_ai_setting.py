"""모델·노력 값 칸, 처음 설정의 시작 조건·까닭, 설정의 안내 글·되돌리기·경고(M2 명세 3.12.2~3).

값은 모두 시험이 만든 값이다.
"""

import pytest
from acceptance.m3_support import fill_trade_fields
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QPushButton,
    QRadioButton,
    QWidget,
)

from coin_report_trader.core import (
    AI_SLOT_TITLES,
    CLAUDE_MODELS,
    AiChoice,
    AiSlot,
    CandleUnit,
    Effort,
    LoadState,
    Market,
    MarketsView,
    SaveResult,
    ScreenId,
    SettingsView,
    UiContext,
)
from coin_report_trader.ui import create_main_window, screen_widget, select_screen
from coin_report_trader.ui.info_icon import InfoIcon

MARKETS = tuple(Market(f"KRW-C{i}", f"가짜{i}", caution=False) for i in range(4))
USAGE = "앱의 AI 사용은 이 PC 사용자의 Claude 구독 사용 한도에서 빠집니다."
REASON = "꼭 넣는 칸을 모두 채우고 '꼭 읽을 글을 읽었습니다'를 고르면 누를 수 있습니다."
INTRO = '아래 값을 고른 뒤 "모의매매 시작"을 누르세요. 별표(*)가 붙은 칸은 꼭 골라야 합니다.'
SAVED = tuple(AiChoice(slot, "sonnet", Effort.HIGH) for slot in AiSlot)


class Actions:
    def __init__(self) -> None:
        self.saves: list[tuple[tuple, dict]] = []
        self.calls: list[str] = []

    def refresh_markets(self) -> None:
        self.calls.append("refresh_markets")

    def save_settings(self, coins, retention_days, **kwargs) -> None:
        self.saves.append(((coins, retention_days), kwargs))

    def load_reports(self, market) -> None:
        self.calls.append("load_reports")

    def load_report(self, report_id) -> None:
        self.calls.append("load_report")

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
    made.set_confirm_handler(lambda _title, _body: True)
    made.resize(1100, 1000)
    made.show()
    yield made
    made.close()
    made.deleteLater()


def _combo(screen: QWidget, prefix: str, kind: str, slot: AiSlot) -> QComboBox:
    combo = screen.findChild(QComboBox, f"{prefix}Ai{kind}_{slot.value}")
    assert combo is not None, (prefix, kind, slot)
    return combo


def _choose_all(screen: QWidget, prefix: str, model_index: int = 1, effort_index: int = 1) -> None:
    for slot in AiSlot:
        _combo(screen, prefix, "Model", slot).setCurrentIndex(model_index)
        _combo(screen, prefix, "Effort", slot).setCurrentIndex(effort_index)


def _first(window, *, ready: bool = True) -> QWidget:
    window.apply(SettingsView())
    window.apply(MarketsView(LoadState.READY, MARKETS))
    return screen_widget(window, ScreenId.FIRST_SETUP)


def _settings(window, **changes) -> QWidget:
    values = {
        "coins": ("KRW-C1",),
        "retention_days": 30,
        "first_setup_done": True,
        "candle_unit": CandleUnit.MINUTE_60,
        "candle_count": 200,
        "ai_choices": SAVED,
    }
    values.update(changes)
    window.apply(SettingsView(**values))
    window.apply(MarketsView(LoadState.READY, MARKETS))
    select_screen(window, ScreenId.SETTINGS)
    QApplication.processEvents()
    return screen_widget(window, ScreenId.SETTINGS)


def _button(screen: QWidget, name: str) -> QPushButton:
    found = screen.findChild(QPushButton, name)
    assert found is not None, name
    return found


def _label(screen: QWidget, name: str) -> QLabel:
    found = screen.findChild(QLabel, name)
    assert found is not None, name
    return found


def _ready_start(screen: QWidget) -> None:
    screen.findChild(QWidget, "firstSetupCoin_KRW-C1").click()
    screen.findChild(QRadioButton, "firstSetupRetention30").click()
    screen.findChild(QRadioButton, "firstSetupUnit_minute60").click()


# --- 칸 ---


@pytest.mark.parametrize("prefix", ["firstSetup", "settings"])
def test_eight_drop_downs_with_a_blank_first_item_then_the_models_and_efforts(window, prefix):
    screen = _first(window) if prefix == "firstSetup" else _settings(window, ai_choices=())
    for slot in AiSlot:
        model = _combo(screen, prefix, "Model", slot)
        effort = _combo(screen, prefix, "Effort", slot)
        assert [model.itemText(i) for i in range(model.count())] == ["", *CLAUDE_MODELS]
        assert [effort.itemText(i) for i in range(effort.count())] == [
            "",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        ]
        assert model.currentIndex() == 0  # 처음에는 빈 항목이다
        assert effort.currentIndex() == 0


def test_captions_are_the_slot_names_with_a_star_only_in_the_first_setup(window):
    first = _first(window)
    texts = {label.text() for label in first.findChildren(QLabel)}
    for slot in AiSlot:
        assert f"{AI_SLOT_TITLES[slot]} 모델*" in texts
        assert f"{AI_SLOT_TITLES[slot]} 노력 값*" in texts
    settings = _settings(window, ai_choices=())
    texts = {label.text() for label in settings.findChildren(QLabel)}
    for slot in AiSlot:
        assert f"{AI_SLOT_TITLES[slot]} 모델" in texts
        assert f"{AI_SLOT_TITLES[slot]} 노력 값" in texts
        assert f"{AI_SLOT_TITLES[slot]} 모델*" not in texts
    assert _label(first, "firstSetupAiTitle").text() == "Claude 모델*"
    assert _label(settings, "settingsAiTitle").text() == "Claude 모델"


@pytest.mark.parametrize("prefix", ["firstSetup", "settings"])
def test_the_usage_note_is_an_icon_next_to_the_card_title(window, prefix):
    screen = _first(window) if prefix == "firstSetup" else _settings(window)
    info = screen.findChild(InfoIcon, f"{prefix}AiInfo")
    assert info is not None
    assert info.toolTip() == info.accessibleDescription() == USAGE
    title = "Claude 모델*" if prefix == "firstSetup" else "Claude 모델"
    assert info.accessibleName() == f"{title} 설명"
    assert not any(label.text() == USAGE for label in screen.findChildren(QLabel))


# --- 처음 설정 ---


def test_the_intro_icon_next_to_the_title_hides_after_the_first_setup_is_done(window):
    first = _first(window)
    info = first.findChild(InfoIcon, "firstSetupIntroInfo")
    assert info is not None
    assert info.toolTip() == INTRO
    assert info.accessibleName() == "처음 설정 설명"
    assert not info.isHidden()
    window.apply(SettingsView(("KRW-C1",), 30, True, candle_unit=CandleUnit.DAY, candle_count=200))
    assert info.isHidden()


def test_start_needs_all_eight_values_and_the_reason_is_shown_until_then(window, actions):
    first = _first(window)
    start = _button(first, "firstSetupStart")
    reason = _label(first, "firstSetupStartReason")
    _ready_start(first)
    assert not start.isEnabled()
    assert not reason.isHidden()
    assert reason.text() == REASON
    start.click()
    assert actions.saves == []
    for index, slot in enumerate(AiSlot):
        _combo(first, "firstSetup", "Model", slot).setCurrentIndex(index + 1)
        assert not start.isEnabled(), "일곱 칸이 골라진 채로는 누를 수 없다"
        if index < len(AiSlot) - 1:
            _combo(first, "firstSetup", "Effort", slot).setCurrentIndex(1)
    _combo(first, "firstSetup", "Effort", AiSlot.SESSION).setCurrentIndex(2)
    assert not start.isEnabled()  # 여덟 칸만으로는 안 된다: 3단계 칸과 확인란이 남았다
    assert not reason.isHidden()
    fill_trade_fields(first)
    assert start.isEnabled()
    assert reason.isHidden()  # 누를 수 있으면 보이지 않는다
    # 하나를 다시 비우면 다시 눌리지 않고 까닭이 돌아온다.
    _combo(first, "firstSetup", "Effort", AiSlot.SEARCH).setCurrentIndex(0)
    assert not start.isEnabled()
    assert not reason.isHidden()


def test_start_passes_the_four_choices_in_the_slot_order(window, actions):
    first = _first(window)
    _ready_start(first)
    models = {AiSlot.SEARCH: 1, AiSlot.CANDLE: 2, AiSlot.FINAL: 3, AiSlot.SESSION: 4}
    efforts = {AiSlot.SEARCH: 5, AiSlot.CANDLE: 4, AiSlot.FINAL: 3, AiSlot.SESSION: 1}
    for slot in AiSlot:
        _combo(first, "firstSetup", "Model", slot).setCurrentIndex(models[slot])
        _combo(first, "firstSetup", "Effort", slot).setCurrentIndex(efforts[slot])
    fill_trade_fields(first)
    _button(first, "firstSetupStart").click()
    ((_args, kwargs),) = actions.saves
    assert kwargs["ai_choices"] == (
        AiChoice(AiSlot.SEARCH, "fable", Effort.MAX),
        AiChoice(AiSlot.CANDLE, "opus", Effort.XHIGH),
        AiChoice(AiSlot.FINAL, "sonnet", Effort.HIGH),
        AiChoice(AiSlot.SESSION, "haiku", Effort.LOW),
    )
    assert kwargs["first_setup"] is True


def test_a_legacy_record_does_not_lock_the_eight_values(window, actions):
    window.apply(SettingsView(("KRW-C1",), 30, False))
    window.apply(MarketsView(LoadState.READY, MARKETS))
    first = screen_widget(window, ScreenId.FIRST_SETUP)
    for slot in AiSlot:
        assert _combo(first, "firstSetup", "Model", slot).isEnabled()
        assert _combo(first, "firstSetup", "Effort", slot).isEnabled()
        assert _combo(first, "firstSetup", "Model", slot).currentIndex() == 0
    start = _button(first, "firstSetupStart")
    first.findChild(QRadioButton, "firstSetupUnit_minute60").click()
    assert not start.isEnabled()  # 봉 단위만으로는 안 된다
    _choose_all(first, "firstSetup")
    assert not start.isEnabled()  # 3단계 칸과 확인란이 남았다
    fill_trade_fields(first)
    assert start.isEnabled()


def test_the_eight_values_are_dim_while_the_settings_cannot_be_read(window):
    window.apply(SettingsView(state=LoadState.FAILED))
    first = screen_widget(window, ScreenId.FIRST_SETUP)
    for slot in AiSlot:
        assert not _combo(first, "firstSetup", "Model", slot).isEnabled()
        assert not _combo(first, "firstSetup", "Effort", slot).isEnabled()
    assert first.findChild(InfoIcon, "firstSetupAiInfo").isEnabled()


# --- 설정 ---


def test_settings_shows_the_saved_choices_and_an_older_record_shows_blanks(window):
    settings = _settings(window)
    for slot in AiSlot:
        assert _combo(settings, "settings", "Model", slot).currentText() == "sonnet"
        assert _combo(settings, "settings", "Effort", slot).currentText() == "high"
    window.apply(SettingsView(("KRW-C1",), 30, True, candle_unit=CandleUnit.DAY, ai_choices=()))
    for slot in AiSlot:
        assert _combo(settings, "settings", "Model", slot).currentIndex() == 0
        assert _combo(settings, "settings", "Effort", slot).currentIndex() == 0
    assert _label(settings, "settingsAiWarning").isHidden()  # 모두 비었으면 경고가 없다


def test_settings_save_passes_the_eight_values_only_when_all_are_chosen(window, actions):
    settings = _settings(window)
    _combo(settings, "settings", "Effort", AiSlot.FINAL).setCurrentIndex(5)  # max
    _button(settings, "settingsSave").click()
    ((_args, kwargs),) = actions.saves
    assert kwargs["ai_choices"][2] == AiChoice(AiSlot.FINAL, "sonnet", Effort.MAX)
    assert len(kwargs["ai_choices"]) == 4


def test_a_partial_choice_shows_the_warning_and_saves_the_rest_without_ai_choices(window, actions):
    settings = _settings(window)
    warning = _label(settings, "settingsAiWarning")
    assert warning.isHidden()
    _combo(settings, "settings", "Model", AiSlot.CANDLE).setCurrentIndex(0)
    assert not warning.isHidden()
    assert warning.text() == "네 칸의 모델과 노력 값을 모두 골라야 저장됩니다."
    assert warning.property("tone") == "warning"
    save = _button(settings, "settingsSave")
    assert save.isEnabled()  # 나머지 값은 저장된다
    save.click()
    ((_args, kwargs),) = actions.saves
    assert "ai_choices" not in kwargs
    # 다시 모두 고르면 경고가 사라진다.
    _combo(settings, "settings", "Model", AiSlot.CANDLE).setCurrentIndex(2)
    assert warning.isHidden()


def test_all_blank_saves_without_ai_choices_and_without_a_warning(window, actions):
    settings = _settings(window, ai_choices=())
    assert _label(settings, "settingsAiWarning").isHidden()
    _button(settings, "settingsSave").click()
    ((_args, kwargs),) = actions.saves
    assert "ai_choices" not in kwargs


def test_the_intro_icon_is_next_to_the_settings_title(window):
    settings = _settings(window)
    info = settings.findChild(InfoIcon, "settingsIntroInfo")
    assert info is not None
    assert info.toolTip() == "값을 고친 뒤 저장을 누르면 적용됩니다."
    assert info.accessibleName() == "설정 설명"


def test_revert_puts_back_every_field_and_calls_nothing(window, actions):
    settings = _settings(window)
    calls_before = list(actions.calls)
    _combo(settings, "settings", "Model", AiSlot.SEARCH).setCurrentIndex(4)
    _combo(settings, "settings", "Effort", AiSlot.SESSION).setCurrentIndex(0)
    settings.findChild(QRadioButton, "settingsRetention90").click()
    settings.findChild(QRadioButton, "settingsUnit_day").click()
    assert not _label(settings, "settingsAiWarning").isHidden()
    revert = _button(settings, "settingsRevert")
    assert revert.text() == "되돌리기"
    assert revert.isEnabled()
    revert.click()
    assert _combo(settings, "settings", "Model", AiSlot.SEARCH).currentText() == "sonnet"
    assert _combo(settings, "settings", "Effort", AiSlot.SESSION).currentText() == "high"
    assert settings.findChild(QRadioButton, "settingsRetention30").isChecked()
    assert settings.findChild(QRadioButton, "settingsUnit_minute60").isChecked()
    assert _label(settings, "settingsAiWarning").isHidden()
    assert actions.saves == []
    assert actions.calls == calls_before  # `UiActions`를 부르지 않는다


def test_revert_is_dim_before_the_first_setup_and_while_unreadable(window):
    window.apply(SettingsView())
    settings = screen_widget(window, ScreenId.SETTINGS)
    assert not _button(settings, "settingsRevert").isEnabled()
    assert not _button(settings, "settingsSave").isEnabled()
    window.apply(SaveResult("settings", ok=True))
    for slot in AiSlot:
        assert not _combo(settings, "settings", "Model", slot).isEnabled()
    assert settings.findChild(InfoIcon, "settingsAiInfo").isEnabled()


def test_revert_goes_back_to_the_latest_view_not_the_first_one(window):
    settings = _settings(window)
    window.apply(
        SettingsView(
            ("KRW-C1",),
            30,
            True,
            candle_unit=CandleUnit.MINUTE_60,
            candle_count=200,
            ai_choices=tuple(AiChoice(slot, "haiku", Effort.LOW) for slot in AiSlot),
        )
    )
    _combo(settings, "settings", "Model", AiSlot.FINAL).setCurrentIndex(1)
    _button(settings, "settingsRevert").click()
    assert _combo(settings, "settings", "Model", AiSlot.FINAL).currentText() == "haiku"
