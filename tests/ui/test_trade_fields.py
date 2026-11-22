"""처음 설정과 설정의 3단계 칸(M3 명세 3.11, 3.17.5)의 시험. 값은 모두 시험이 만든 값이다."""

from decimal import Decimal

import pytest
from acceptance.m3_support import LINE_VALUES, fill_trade_fields
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QWidget,
)

from coin_report_trader.core import (
    AiChoice,
    AiSlot,
    BelowStopView,
    CandleUnit,
    CoinTradeView,
    Effort,
    LoadState,
    Market,
    MarketsView,
    PaperHolding,
    PauseStopLoss,
    SaveResult,
    ScreenId,
    SettingsView,
    TradeMethod,
    TradeSettings,
    UiContext,
)
from coin_report_trader.ui import create_main_window, current_screen, screen_widget, select_screen
from coin_report_trader.ui.cards import Card
from coin_report_trader.ui.info_icon import InfoIcon
from coin_report_trader.ui.widgets import set_dim

AAA = Market("KRW-AAA", "가짜에이", caution=False)
BBB = Market("KRW-BBB", "가짜비", caution=True)
CCC = Market("KRW-CCC", "가짜씨", caution=False)
MARKETS = (AAA, BBB, CCC)
AI = tuple(AiChoice(slot, "sonnet", Effort.HIGH) for slot in AiSlot)


def make_trade(**changes) -> TradeSettings:
    values = {
        "budget": Decimal("1000000"),
        "coin_ratios": {"KRW-AAA": Decimal("60"), "KRW-CCC": Decimal("40")},
        "first_ratio": Decimal("20"),
        "mr_first_ratio": Decimal("15.5"),
        "add_ratio": Decimal("10"),
        "tolerance": Decimal("1"),
        "stop_ratio": Decimal("5"),
        "daily_loss_limit": Decimal("3000"),
        "order_interval_s": 60,
        "method": TradeMethod.TREND,
        "pause_stop_loss": PauseStopLoss.KEEP,
        "mr_sell_unknown_fee": False,
        "mr_tolerable_loss": Decimal("5"),
        "paper_funds": Decimal("1000000"),
        "backup_days": 7,
    }
    values.update(changes)
    return TradeSettings(**values)


class Actions:
    def __init__(self) -> None:
        self.saves: list[tuple[tuple, dict]] = []

    def refresh_markets(self) -> None: ...

    def save_settings(self, coins, retention_days, **kwargs) -> None:
        self.saves.append(((coins, retention_days), kwargs))

    def load_reports(self, market, mode=None) -> None: ...

    def load_report(self, report_id) -> None: ...

    def retry_candles(self) -> None: ...

    def mark_seen(self, alert_ids) -> None: ...

    def delete_alert(self, alert_id) -> None: ...

    def clear_alerts(self, category) -> None: ...

    def save_webhook(self, category, url) -> None: ...

    def delete_webhook(self, category) -> None: ...

    def send_test(self, category) -> None: ...


class Confirm:
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.asked: list[tuple[str, str]] = []

    def __call__(self, title: str, body: str) -> bool:
        self.asked.append((title, body))
        return self.answer


@pytest.fixture
def actions() -> Actions:
    return Actions()


@pytest.fixture
def window(qapp: QApplication, actions: Actions):
    made = create_main_window(UiContext("0.0.1", "X", actions))
    made.resize(1100, 900)
    made.show()
    yield made
    made.close()
    made.deleteLater()


def _find(parent: QWidget, kind: type, name: str):
    found = parent.findChild(kind, name)
    assert found is not None, name
    return found


def _first(window) -> QWidget:
    window.apply(SettingsView())
    window.apply(MarketsView(LoadState.READY, MARKETS))
    return screen_widget(window, ScreenId.FIRST_SETUP)


def _settings(window, trade: TradeSettings | None = None, **changes) -> QWidget:
    values = {
        "coins": ("KRW-AAA", "KRW-CCC"),
        "retention_days": 30,
        "first_setup_done": True,
        "candle_unit": CandleUnit.MINUTE_60,
        "candle_count": 200,
        "ai_choices": AI,
        "trade": trade,
    }
    values.update(changes)
    window.apply(SettingsView(**values))
    window.apply(MarketsView(LoadState.READY, MARKETS))
    select_screen(window, ScreenId.SETTINGS)
    return screen_widget(window, ScreenId.SETTINGS)


def _pick(screen: QWidget, prefix: str, *markets: str) -> None:
    for market in markets:
        _find(screen, QCheckBox, f"{prefix}Coin_{market}").click()


def _text(screen: QWidget, name: str) -> str:
    return _find(screen, QLabel, name).text()


def _line(screen: QWidget, name: str) -> QLineEdit:
    return _find(screen, QLineEdit, name)


def _ready_first(window) -> QWidget:
    """코인 하나, 보관 기간, 봉 단위, 여덟 칸까지 고른 처음 설정(3단계 칸과 확인란만 남는다)."""
    first = _first(window)
    _pick(first, "firstSetup", "KRW-AAA")
    _find(first, QRadioButton, "firstSetupRetention30").click()
    _find(first, QRadioButton, "firstSetupUnit_minute60").click()
    for slot in AiSlot:
        _find(first, QComboBox, f"firstSetupAiModel_{slot.value}").setCurrentIndex(1)
        _find(first, QComboBox, f"firstSetupAiEffort_{slot.value}").setCurrentIndex(1)
    return first


# --- 모양: 이름, 별표, 처음 값, 'ⓘ' ---


def test_first_setup_has_the_spec_widgets_with_stars_and_the_first_values(window):
    first = _first(window)
    _pick(first, "firstSetup", "KRW-AAA", "KRW-CCC")
    for name in (
        "Budget",
        "FirstRatio",
        "MrFirstRatio",
        "AddRatio",
        "Tolerance",
        "StopRatio",
        "DailyLoss",
        "OrderInterval",
        "MrLoss",
        "PaperFunds",
        "BackupDays",
        "CoinRatio_KRW-AAA",
        "CoinRatio_KRW-CCC",
    ):
        assert _line(first, f"firstSetup{name}") is not None
    for name in (
        "Method_mean_reversion",
        "Method_trend",
        "PauseStop_keep",
        "PauseStop_stop",
        "MrSell_yes",
        "MrSell_no",
    ):
        assert _find(first, QRadioButton, f"firstSetup{name}") is not None
        assert not _find(first, QRadioButton, f"firstSetup{name}").isChecked()  # 미리 고른 값 없음
    # 처음 값은 칸에 채워 보일 뿐이다(손절 비율 5, 하루 손실 한도 3,000, 주문 간격 60).
    assert _line(first, "firstSetupStopRatio").text() == "5"
    assert _line(first, "firstSetupDailyLoss").text() == "3,000"
    assert _line(first, "firstSetupOrderInterval").text() == "60"
    for name in ("Budget", "AddRatio", "Tolerance", "MrLoss", "PaperFunds", "BackupDays"):
        assert _line(first, f"firstSetup{name}").text() == ""
    # 별표: 예산, 투자 비율, 허용 범위, 추가 한 번의 비율. 방식을 고르기 전에는 첫 매수 비율
    # 둘에 없다.
    assert _text(first, "firstSetupBudgetLabel") == "예산*(원)"
    assert _text(first, "firstSetupCoinRatio_KRW-AAALabel") == "AAA 투자 비율*(%)"
    assert _text(first, "firstSetupToleranceLabel") == "허용 범위*(%)"
    assert _text(first, "firstSetupAddRatioLabel") == "추가 한 번의 비율*(%)"
    assert _text(first, "firstSetupFirstRatioLabel") == "추세추종 첫 매수 비율(%)"
    assert _text(first, "firstSetupMrFirstRatioLabel") == "평균회귀 첫 매수 비율(%)"
    _find(first, QRadioButton, "firstSetupMethod_trend").click()
    assert _text(first, "firstSetupFirstRatioLabel") == "추세추종 첫 매수 비율*(%)"
    assert _text(first, "firstSetupMrFirstRatioLabel") == "평균회귀 첫 매수 비율(%)"
    _find(first, QRadioButton, "firstSetupMethod_mean_reversion").click()
    assert _text(first, "firstSetupFirstRatioLabel") == "추세추종 첫 매수 비율(%)"
    assert _text(first, "firstSetupMrFirstRatioLabel") == "평균회귀 첫 매수 비율*(%)"


def test_settings_has_the_same_fields_without_stars_and_without_the_limits_info(window):
    settings = _settings(window, make_trade())
    assert _text(settings, "settingsBudgetLabel") == "예산(원)"
    assert _text(settings, "settingsCoinRatio_KRW-AAALabel") == "AAA 투자 비율(%)"
    assert _text(settings, "settingsAddRatioLabel") == "추가 한 번의 비율(%)"
    assert settings.findChild(InfoIcon, "settingsLimitsInfo") is None  # 설정에는 없다
    assert settings.findChild(InfoIcon, "settingsBudgetInfo") is not None
    assert settings.findChild(InfoIcon, "settingsRatioInfo") is not None


def test_the_info_icons_hold_the_decided_texts(window):
    first = _first(window)
    budget = _find(first, InfoIcon, "firstSetupBudgetInfo")
    assert budget.toolTip() == "\n".join(
        (
            "• 코인 예산은 예산 × 투자 비율입니다.",
            "• 고른 코인의 투자 비율을 합하면 100%여야 합니다. "
            "예산이 고른 코인 전체의 매수 상한입니다.",
        )
    )
    ratio = _find(first, InfoIcon, "firstSetupRatioInfo")
    lines = ratio.toolTip().split("\n")
    assert len(lines) == 4
    assert lines[0].startswith(
        "• 허용 범위: 판단의 주문 가격(추세추종은 제안가, 평균회귀는 예측 가격)에서"
    )
    assert lines[1] == "• 0%이면 주문 가격 그대로 겁니다. 0%에서 2%까지 넣습니다."
    assert (
        lines[2]
        == "• 모의 성적의 '예측 차이 평균'에서 예측 가격과 실제 값의 차이를 볼 수 있습니다."
    )
    assert lines[3] == "• 모의매매 수수료율: 살 때와 팔 때 각각 0.05%"
    limits = _find(first, InfoIcon, "firstSetupLimitsInfo")
    assert limits.toolTip() == "모의매매를 시작하기 위한 예시 값이고 투자 판단이 아닙니다."
    for icon, title in (
        (budget, "예산과 투자 비율"),
        (ratio, "매수 비율"),
        (limits, "손절과 한도"),
    ):
        assert icon.accessibleDescription() == icon.toolTip()
        assert icon.accessibleName() == f"{title} 설명"
    assert _text(first, "firstSetupBudgetTitle") == "예산과 투자 비율"
    assert _text(first, "firstSetupRatioTitle") == "매수 비율"
    assert _text(first, "firstSetupLimitsTitle") == "손절과 한도"
    assert _text(first, "firstSetupOtherTitle") == "그 밖의 값"


def test_the_two_method_values_have_the_decided_tooltips_and_accessible_descriptions(window):
    first = _first(window)
    tips = {
        "mean_reversion": (
            "싸게 사서 비싸게 팔기. 보유 중에 '내릴 것'이면 손실 중이어도 예산 안에서 더 삽니다."
        ),
        "trend": (
            "오를 때 사고 내릴 때 팔기. 보유 중에는 '오를 것'이고 현재가가 평균 매수가보다 "
            "높을 때만 더 삽니다."
        ),
    }
    for value, tip in tips.items():
        button = _find(first, QRadioButton, f"firstSetupMethod_{value}")
        assert button.toolTip() == tip
        assert button.accessibleDescription() == tip
    assert _find(first, QRadioButton, "firstSetupMethod_trend").text() == "추세추종"
    assert _find(first, QRadioButton, "firstSetupMethod_mean_reversion").text() == "평균회귀"
    assert _find(first, QRadioButton, "firstSetupPauseStop_keep").text() == "손절 계속"
    assert _find(first, QRadioButton, "firstSetupPauseStop_stop").text() == "손절 중지"
    assert _find(first, QRadioButton, "firstSetupMrSell_yes").text() == "예"
    assert _find(first, QRadioButton, "firstSetupMrSell_no").text() == "아니오"


def test_the_seven_notices_and_the_read_box_under_the_title(window):
    first = _first(window)
    info = _find(first, InfoIcon, "firstSetupNoticesInfo")
    lines = info.toolTip().split("\n")
    assert len(lines) == 7
    assert lines[0] == "• 이 프로그램은 투자 자문이 아닙니다. AI 판단은 틀릴 수 있습니다."
    assert lines[1] == "• PC가 꺼져 있으면 손절이 없습니다."
    assert lines[2] == "• 주문 전에 확인하는 단계 없이, 판단에 따라 자동으로 주문합니다."
    assert lines[6] == "• 앱의 AI 사용은 이 PC 사용자의 Claude 구독 사용 한도에서 빠집니다."
    read = _find(first, QCheckBox, "firstSetupRead")
    assert read.text() == "꼭 읽을 글을 읽었습니다"
    assert not read.isChecked()
    assert _find(first, QPushButton, "firstSetupStart").text() == "모의매매 시작"
    assert _find(first, InfoIcon, "firstSetupIntroInfo").toolTip() == (
        '아래 값을 고른 뒤 "모의매매 시작"을 누르세요. 별표(*)가 붙은 칸은 꼭 골라야 합니다.'
    )


# --- 계산되어 보이는 글 ---


def test_coin_budget_sum_and_buy_amounts_are_computed_as_the_fields_change(window):
    first = _first(window)
    _pick(first, "firstSetup", "KRW-AAA", "KRW-CCC")
    assert _text(first, "firstSetupCoinBudget_KRW-AAA") == "코인 예산 -"
    assert _text(first, "firstSetupCoinRatioSum") == "합계 0.00%"
    _line(first, "firstSetupBudget").setText("1,000,001")
    _line(first, "firstSetupCoinRatio_KRW-AAA").setText("33.33")
    _line(first, "firstSetupCoinRatio_KRW-CCC").setText("66.67")
    # 원 아래는 버린다(1,000,001 × 33.33% = 333,300.33...).
    assert _text(first, "firstSetupCoinBudget_KRW-AAA") == "코인 예산 333,300원"
    assert _text(first, "firstSetupCoinBudget_KRW-CCC") == "코인 예산 666,700원"
    assert _text(first, "firstSetupCoinRatioSum") == "합계 100.00%"
    _line(first, "firstSetupCoinRatio_KRW-CCC").setText("66.6")
    assert _text(first, "firstSetupCoinRatioSum") == "합계 99.93%"
    assert _text(first, "firstSetupCoinBuy_KRW-AAA") == "첫 매수 금액 -, 추가 한 번 -"
    _find(first, QRadioButton, "firstSetupMethod_trend").click()
    _line(first, "firstSetupFirstRatio").setText("20")
    _line(first, "firstSetupAddRatio").setText("10")
    assert _text(first, "firstSetupCoinBuy_KRW-AAA") == "첫 매수 금액 66,660원, 추가 한 번 33,330원"
    # 고르지 않은 방식의 첫 매수 비율은 셈에 쓰지 않는다.
    _line(first, "firstSetupMrFirstRatio").setText("50")
    assert _text(first, "firstSetupCoinBuy_KRW-AAA") == "첫 매수 금액 66,660원, 추가 한 번 33,330원"
    _find(first, QRadioButton, "firstSetupMethod_mean_reversion").click()
    assert (
        _text(first, "firstSetupCoinBuy_KRW-AAA") == "첫 매수 금액 166,650원, 추가 한 번 33,330원"
    )
    # 받는 꼴이 아니면 "코인 예산 -".
    _line(first, "firstSetupBudget").setText("abc")
    assert _text(first, "firstSetupCoinBudget_KRW-AAA") == "코인 예산 -"


def test_a_coin_that_leaves_the_list_drops_its_row_and_its_value(window):
    first = _first(window)
    _pick(first, "firstSetup", "KRW-AAA", "KRW-CCC")
    _line(first, "firstSetupCoinRatio_KRW-AAA").setText("70")
    _pick(first, "firstSetup", "KRW-AAA")  # 뺀다
    assert first.findChild(QLineEdit, "firstSetupCoinRatio_KRW-AAA") is None
    assert _text(first, "firstSetupCoinRatioSum") == "합계 0.00%"
    _pick(first, "firstSetup", "KRW-AAA")
    assert _line(first, "firstSetupCoinRatio_KRW-AAA").text() == ""


def test_choosing_a_caution_coin_shows_the_notice_under_the_list(window):
    first = _first(window)
    note = _find(first, QLabel, "firstSetupCautionNote")
    assert note.isHidden()
    _pick(first, "firstSetup", "KRW-BBB")
    assert not note.isHidden()
    assert note.text() == "이 코인은 유의·주의 표시가 있어 새로 사지 않습니다."
    _pick(first, "firstSetup", "KRW-BBB")
    assert note.isHidden()


# --- 시작 조건과 보내는 값 ---


def test_start_needs_every_required_field_and_the_read_box_and_shows_the_reason(window, actions):
    first = _ready_first(window)
    start = _find(first, QPushButton, "firstSetupStart")
    reason = _find(first, QLabel, "firstSetupStartReason")
    assert not start.isEnabled()
    assert not reason.isHidden()
    assert (
        reason.text()
        == "꼭 넣는 칸을 모두 채우고 '꼭 읽을 글을 읽었습니다'를 고르면 누를 수 있습니다."
    )
    fill_trade_fields(first, read=False)
    assert not start.isEnabled()  # 확인란이 남았다
    _find(first, QCheckBox, "firstSetupRead").setChecked(True)
    assert start.isEnabled()
    assert reason.isHidden()
    _line(first, "firstSetupBackupDays").setText("")
    assert not start.isEnabled()
    assert not reason.isHidden()
    assert actions.saves == []


def test_start_sends_the_chosen_values_as_a_trade_settings(window, actions):
    first = _ready_first(window)
    fill_trade_fields(first)
    _find(first, QPushButton, "firstSetupStart").click()
    ((args, kwargs),) = actions.saves
    assert args == (("KRW-AAA",), 30)
    assert kwargs["first_setup"] is True
    trade = kwargs["trade"]
    values = dict(LINE_VALUES)
    assert trade == TradeSettings(
        budget=Decimal("1000000"),
        coin_ratios={"KRW-AAA": Decimal("100.00")},
        first_ratio=Decimal(values["FirstRatio"]),
        mr_first_ratio=Decimal(values["MrFirstRatio"]),
        add_ratio=Decimal("10"),
        tolerance=Decimal("1"),
        stop_ratio=Decimal("5"),
        daily_loss_limit=Decimal("3000"),
        order_interval_s=60,
        method=TradeMethod.TREND,
        pause_stop_loss=PauseStopLoss.KEEP,
        mr_sell_unknown_fee=False,
        mr_tolerable_loss=Decimal("5"),
        paper_funds=Decimal("1000000"),
        backup_days=7,
    )
    assert "accept_below_stop" not in kwargs  # 처음 저장에는 없다


def test_an_empty_first_ratio_of_the_other_method_is_none(window, actions):
    first = _ready_first(window)
    fill_trade_fields(first)
    _line(first, "firstSetupMrFirstRatio").setText("")
    _find(first, QPushButton, "firstSetupStart").click()
    ((_args, kwargs),) = actions.saves
    assert kwargs["trade"].mr_first_ratio is None
    assert kwargs["trade"].first_ratio == Decimal("20")


@pytest.mark.parametrize(
    ("name", "text", "message"),
    [
        ("Budget", "백만", "숫자로 넣으세요."),
        ("Budget", "1000.5", "정수로 넣으세요."),
        ("AddRatio", "ten", "숫자로 넣으세요."),
        ("AddRatio", "10.123", "소수 둘째 자리까지 넣으세요."),
        ("Tolerance", "1e1", "숫자로 넣으세요."),
        ("OrderInterval", "6.5", "정수로 넣으세요."),
        ("BackupDays", "x", "숫자로 넣으세요."),
        ("MrLoss", "1,5", "숫자로 넣으세요."),
    ],
)
def test_a_wrong_form_shows_the_text_under_the_field_and_saves_nothing(
    window, actions, name, text, message
):
    first = _ready_first(window)
    fill_trade_fields(first)
    box = _line(first, f"firstSetup{name}")
    box.setText(text)
    _find(first, QPushButton, "firstSetupStart").click()
    error = _find(first, QLabel, f"firstSetup{name}Error")
    assert not error.isHidden()
    assert error.text() == message
    assert "error" in box.styleSheet() or box.property("invalid") is True
    assert actions.saves == []
    # 그 칸을 고치면 글이 걷힌다.
    box.setText("1")
    assert error.isHidden()


def test_choosing_a_method_with_an_empty_first_ratio_shows_the_required_text(window):
    first = _first(window)
    error = _find(first, QLabel, "firstSetupFirstRatioError")
    assert error.isHidden()
    _find(first, QRadioButton, "firstSetupMethod_trend").click()
    assert error.text() == "이 값을 넣어야 합니다."
    assert not error.isHidden()
    _find(first, QRadioButton, "firstSetupMethod_mean_reversion").click()
    assert error.isHidden()
    assert _find(first, QLabel, "firstSetupMrFirstRatioError").text() == "이 값을 넣어야 합니다."


def test_field_errors_of_a_save_are_shown_under_their_fields_and_cleared_next_time(window, actions):
    first = _ready_first(window)
    fill_trade_fields(first)
    _find(first, QPushButton, "firstSetupStart").click()
    window.apply(
        SaveResult(
            "settings",
            ok=False,
            field_errors={
                "coin_ratios": "코인별 투자 비율의 합이 100%여야 합니다(지금 99.99%).",
                "coin_ratio:KRW-AAA": "AAA: 첫 매수 금액(1원)이 하한(5,790원)보다 작습니다.\n"
                "첫 매수와 추가 한 번을 합친 금액이 AAA의 예산보다 큽니다.",
                "tolerance": "0%에서 2% 사이로 넣으세요.",
            },
        )
    )
    assert current_screen(window) is ScreenId.FIRST_SETUP
    assert _text(first, "firstSetupCoinRatioSumError") == (
        "코인별 투자 비율의 합이 100%여야 합니다(지금 99.99%)."
    )
    assert "\n" in _text(first, "firstSetupCoinRatio_KRW-AAAError")
    assert _text(first, "firstSetupToleranceError") == "0%에서 2% 사이로 넣으세요."
    assert not _find(first, QLabel, "firstSetupToleranceError").isHidden()
    # 고른 값과 넣은 값은 칸에 남는다.
    assert _line(first, "firstSetupTolerance").text() == "1"
    assert _find(first, QPushButton, "firstSetupStart").isEnabled()
    # 고친 칸의 글만 걷힌다.
    _line(first, "firstSetupTolerance").setText("1.5")
    assert _find(first, QLabel, "firstSetupToleranceError").isHidden()
    assert not _find(first, QLabel, "firstSetupCoinRatioSumError").isHidden()
    # 다시 누르면 앞의 글이 모두 걷히고, 저장되면 글이 없다.
    _find(first, QPushButton, "firstSetupStart").click()
    assert _find(first, QLabel, "firstSetupCoinRatioSumError").isHidden()
    window.apply(SaveResult("settings", ok=True, message="저장했습니다."))
    assert _find(first, QLabel, "firstSetupCoinRatio_KRW-AAAError").isHidden()


# --- 설정 ---


def test_settings_shows_the_saved_trade_values(window):
    settings = _settings(window, make_trade())
    assert _line(settings, "settingsBudget").text() == "1,000,000"
    assert _line(settings, "settingsCoinRatio_KRW-AAA").text() == "60"
    assert _line(settings, "settingsCoinRatio_KRW-CCC").text() == "40"
    assert _line(settings, "settingsMrFirstRatio").text() == "15.5"
    assert _line(settings, "settingsDailyLoss").text() == "3,000"
    assert _line(settings, "settingsPaperFunds").text() == "1,000,000"
    assert _find(settings, QRadioButton, "settingsMethod_trend").isChecked()
    assert _find(settings, QRadioButton, "settingsPauseStop_keep").isChecked()
    assert _find(settings, QRadioButton, "settingsMrSell_no").isChecked()
    assert _text(settings, "settingsCoinBudget_KRW-AAA") == "코인 예산 600,000원"
    assert _text(settings, "settingsCoinRatioSum") == "합계 100.00%"
    assert (
        _text(settings, "settingsCoinBuy_KRW-AAA") == "첫 매수 금액 120,000원, 추가 한 번 60,000원"
    )


def test_the_paper_funds_are_dim_and_stay_dim_when_the_form_is_enabled_again(window):
    settings = _settings(window, make_trade())
    funds = _line(settings, "settingsPaperFunds")
    assert not funds.isEnabled()
    form = _find(settings, QWidget, "settingsForm")
    set_dim(form, True)
    set_dim(form, False)  # 다시 켜도
    assert not funds.isEnabled()
    assert _line(settings, "settingsBudget").isEnabled()
    # 칸이 흐린 모습(처음 설정 전)에서 돌아와도 흐리다.
    window.apply(SettingsView(state=LoadState.READY))
    _settings(window, make_trade())
    assert not _line(settings, "settingsPaperFunds").isEnabled()


def test_settings_save_needs_the_required_fields_only_when_a_trade_is_saved(window, actions):
    settings = _settings(window, make_trade())
    save = _find(settings, QPushButton, "settingsSave")
    assert save.isEnabled()
    _line(settings, "settingsBudget").setText("")
    assert not save.isEnabled()
    _line(settings, "settingsBudget").setText("2,000,000")
    assert save.isEnabled()
    save.click()
    ((_args, kwargs),) = actions.saves
    assert kwargs["trade"].budget == Decimal("2000000")
    assert kwargs["first_setup"] is False


def test_settings_before_the_trade_values_exist_save_without_a_trade(window, actions):
    settings = _settings(window, None)
    for name in ("Budget", "AddRatio", "Tolerance", "MrLoss", "PaperFunds", "BackupDays"):
        assert _line(settings, f"settings{name}").text() == ""  # 비어 있다
    save = _find(settings, QPushButton, "settingsSave")
    assert save.isEnabled()  # M2 명세 3.12.2의 조건대로 눌린다
    save.click()
    ((_args, kwargs),) = actions.saves
    assert "trade" not in kwargs


def test_settings_wrong_form_shows_the_text_and_saves_nothing(window, actions):
    settings = _settings(window, make_trade())
    _line(settings, "settingsAddRatio").setText("1.234")
    _find(settings, QPushButton, "settingsSave").click()
    assert _text(settings, "settingsAddRatioError") == "소수 둘째 자리까지 넣으세요."
    assert actions.saves == []


def test_settings_revert_brings_the_saved_trade_values_back(window, actions):
    settings = _settings(window, make_trade())
    _line(settings, "settingsBudget").setText("5")
    _find(settings, QRadioButton, "settingsMethod_mean_reversion").click()
    _find(settings, QPushButton, "settingsRevert").click()
    assert _line(settings, "settingsBudget").text() == "1,000,000"
    assert _find(settings, QRadioButton, "settingsMethod_trend").isChecked()
    assert actions.saves == []


def test_settings_field_errors_from_the_data_thread_show_under_the_fields(window):
    settings = _settings(window, make_trade())
    window.apply(
        SaveResult(
            "settings",
            ok=False,
            field_errors={"first_ratio": "첫 매수 비율이 모자랍니다.", "coin_ratio:KRW-CCC": "x"},
        )
    )
    assert _text(settings, "settingsFirstRatioError") == "첫 매수 비율이 모자랍니다."
    assert _text(settings, "settingsCoinRatio_KRW-CCCError") == "x"


# --- 예전 기록(3.11.3) ---


def test_a_second_stage_record_is_locked_where_it_was_saved_and_open_where_it_was_not(window):
    window.apply(
        SettingsView(
            ("KRW-AAA", "KRW-CCC"),
            30,
            False,
            candle_unit=CandleUnit.DAY,
            candle_count=100,
            ai_choices=AI,
        )
    )
    window.apply(MarketsView(LoadState.READY, MARKETS))
    first = screen_widget(window, ScreenId.FIRST_SETUP)
    notice = _find(first, QLabel, "firstSetupLegacyNotice")
    assert not notice.isHidden()
    assert notice.text() == (
        '모의매매에 필요한 칸이 새로 생겼습니다. 빈 칸을 넣고 "꼭 읽을 글을 읽었습니다"를 고른 뒤 '
        '"모의매매 시작"을 누르세요. 그 전에는 회차를 돌리지 않습니다.'
    )
    # 저장된 값은 채워진 채 잠긴다.
    assert _find(first, QRadioButton, "firstSetupUnit_day").isChecked()
    assert not _find(first, QRadioButton, "firstSetupUnit_day").isEnabled()
    assert _find(first, QRadioButton, "firstSetupCount_100").isChecked()
    assert not _find(first, QRadioButton, "firstSetupCount_100").isEnabled()
    assert _find(first, QComboBox, "firstSetupAiModel_search").currentText() == "sonnet"
    for slot in AiSlot:
        assert not _find(first, QComboBox, f"firstSetupAiModel_{slot.value}").isEnabled()
        assert not _find(first, QComboBox, f"firstSetupAiEffort_{slot.value}").isEnabled()
    assert not _find(first, QCheckBox, "firstSetupCoin_KRW-AAA").isEnabled()
    assert not _find(first, QRadioButton, "firstSetupRetention30").isEnabled()
    # 저장되지 않은 3단계 칸만 고른다.
    assert _line(first, "firstSetupBudget").isEnabled()
    assert _find(first, QRadioButton, "firstSetupMethod_trend").isEnabled()
    assert _find(first, QCheckBox, "firstSetupRead").isEnabled()
    fill_trade_fields(first)
    start = _find(first, QPushButton, "firstSetupStart")
    assert start.isEnabled()  # 저장된 값은 이미 채워져 있어 3단계 칸과 확인란만 있으면 된다


def test_the_legacy_locks_go_away_once_the_third_stage_is_saved(window):
    window.apply(
        SettingsView(
            ("KRW-AAA",), 30, False, candle_unit=CandleUnit.DAY, candle_count=100, ai_choices=AI
        )
    )
    window.apply(MarketsView(LoadState.READY, MARKETS))
    first = screen_widget(window, ScreenId.FIRST_SETUP)
    assert not _find(first, QRadioButton, "firstSetupUnit_day").isEnabled()
    window.apply(
        SettingsView(
            ("KRW-AAA",),
            30,
            True,
            candle_unit=CandleUnit.DAY,
            candle_count=100,
            ai_choices=AI,
            trade=make_trade(coin_ratios={"KRW-AAA": Decimal("100")}),
        )
    )
    assert _find(first, QLabel, "firstSetupLegacyNotice").isHidden()
    assert _find(first, QWidget, "firstSetupForm").isHidden()


# --- 확인 창: 보유 코인 빼기(SCR-06-6), 손절선 아래인 보유(SCR-06-5) ---


def _holding(market: str, qty: str) -> CoinTradeView:
    holding = PaperHolding(
        market, Decimal(qty), Decimal(0), Decimal("1000") if Decimal(qty) else None,
        Decimal("1000"), Decimal(qty),
    )  # fmt: skip
    return CoinTradeView(
        market, Decimal("1000"), None, holding, Decimal(qty), Decimal("1000"), None, False,
        None, None, (), (), False, None,
    )  # fmt: skip


def test_removing_a_coin_with_a_holding_asks_scr_06_6_and_cancel_saves_nothing(window, actions):
    settings = _settings(window, make_trade())
    window.apply(_holding("KRW-AAA", "2.5"))
    confirm = Confirm(False)
    window.set_confirm_handler(confirm)
    _pick(settings, "settings", "KRW-AAA")  # 뺀다
    _find(settings, QPushButton, "settingsSave").click()
    assert confirm.asked == [
        (
            "보유 중인 코인을 뺄까요?",
            "AAA을 목록에서 빼면 프로그램이 건 주문을 취소하고 이 코인을 더는 다루지 않습니다. "
            "보유는 그대로 남고, 이 코인의 손절도 없어집니다.",
        )
    ]
    assert actions.saves == []
    assert _line(settings, "settingsBudget").text() == "1,000,000"  # 고친 값은 칸에 남는다


def test_removing_a_coin_with_a_holding_saves_when_confirmed(window, actions):
    settings = _settings(window, make_trade())
    window.apply(_holding("KRW-AAA", "2.5"))
    window.set_confirm_handler(Confirm(True))
    _pick(settings, "settings", "KRW-AAA")
    _line(settings, "settingsCoinRatio_KRW-CCC").setText("100")
    _find(settings, QPushButton, "settingsSave").click()
    ((args, _kwargs),) = actions.saves
    assert args[0] == ("KRW-CCC",)


def test_removing_a_coin_without_a_holding_asks_nothing(window, actions):
    settings = _settings(window, make_trade())
    window.apply(_holding("KRW-AAA", "0"))
    window.set_confirm_handler(Confirm(False))
    _pick(settings, "settings", "KRW-AAA")
    _line(settings, "settingsCoinRatio_KRW-CCC").setText("100")
    _find(settings, QPushButton, "settingsSave").click()
    assert len(actions.saves) == 1


def test_below_stop_asks_scr_06_5_and_confirm_saves_again_with_the_coin(window, actions):
    settings = _settings(window, make_trade())
    _find(settings, QPushButton, "settingsSave").click()
    ((first_args, first_kwargs),) = actions.saves
    confirm = Confirm(True)
    window.set_confirm_handler(confirm)
    window.apply(BelowStopView("KRW-BBB", Decimal("1000"), Decimal("900"), Decimal("950")))
    assert confirm.asked == [
        (
            "손절선 아래인 보유가 있습니다",
            "BBB의 보유가 손절선 아래에 있습니다(평균 매수가 1,000원, 현재가 900원, 손절선 950원). "
            "이 코인을 다루기 시작하면 바로 시장가로 팔립니다. 다루기 시작할까요?",
        )
    ]
    assert len(actions.saves) == 2
    args, kwargs = actions.saves[1]
    assert args == first_args
    assert kwargs["accept_below_stop"] == ("KRW-BBB",)
    assert kwargs["trade"] == first_kwargs["trade"]
    # 결과가 오면 그 저장이 끝난다(저장했습니다.).
    window.apply(SaveResult("settings", ok=True, message="저장했습니다."))
    assert _text(settings, "settingsMessage") == "저장했습니다."


def test_below_stop_cancel_saves_nothing_and_keeps_the_values(window, actions):
    settings = _settings(window, make_trade())
    _find(settings, QPushButton, "settingsSave").click()
    window.set_confirm_handler(Confirm(False))
    window.apply(BelowStopView("KRW-BBB", Decimal("1000"), Decimal("900"), Decimal("950")))
    assert len(actions.saves) == 1  # 다시 저장하지 않는다
    assert _line(settings, "settingsBudget").text() == "1,000,000"
    # 다음 저장의 결과가 앞의 저장 것으로 잘못 짝지어지지 않는다.
    window.set_confirm_handler(Confirm(True))
    _find(settings, QPushButton, "settingsSave").click()
    window.apply(SaveResult("settings", ok=True, message="저장했습니다."))
    assert _text(settings, "settingsMessage") == "저장했습니다."


def test_the_card_count_of_the_first_setup_is_eight(window):
    first = _first(window)
    assert len(first.findChildren(Card)) == 8
