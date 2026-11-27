"""M3 명세 6절 D27~D29, D34: 처음 설정과 설정 화면, 예전 기록, 코인 빼기, 저장된 설정의 검사.

진짜 창과 진짜 자료 스레드(`DataWorker`)를 잇고 바깥은 가짜(시세, 실행기, 디스코드, 보관소,
시계)로 둔다. 거래소와 진짜 `claude`를 부르지 않는다. 사람이 하는 일은 창의 칸과 단추로 한다.
값은 모두 만든 값이다.
"""

from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from acceptance.m3_d_support import (
    AAA,
    BBB,
    Rig,
    advance,
    all_codes,
    answer,
    apply_all,
    blank_rig,
    choose_first_setup,
    close_window,
    d_rig,
    d_trade,
    holding_seed,
    open_window,
    order_seed,
    orders,
    seeds,
    visible_texts,
)
from acceptance.m3_support import LINE_VALUES, fill_trade_fields
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
)

from coin_report_trader.core import (
    AiSlot,
    OrderKind,
    OrderSide,
    ScreenId,
    SettingsView,
    TradeMethod,
)
from coin_report_trader.ui import current_screen, screen_widget, select_screen

D = Decimal
READ_MORE = "꼭 넣는 칸을 모두 채우고 '꼭 읽을 글을 읽었습니다'를 고르면 누를 수 있습니다."


@pytest.fixture
def windows(tmp_path: Path, qapp: Any) -> Iterator[Callable[..., tuple[Rig, Any]]]:
    made: list[tuple[Rig, Any]] = []

    def factory(rig: Rig | None = None) -> tuple[Rig, Any]:
        rig = rig or blank_rig(tmp_path / f"r{len(made)}")
        rig.start()
        window = open_window(rig)
        made.append((rig, window))
        return rig, window

    yield factory
    for rig, window in made:
        close_window(window)
        rig.stop()


def line(parent: Any, name: str) -> QLineEdit:
    found = parent.findChild(QLineEdit, name)
    assert found is not None, name
    return found


def first_screen(window: Any) -> Any:
    return screen_widget(window, ScreenId.FIRST_SETUP)


def start_button(first: Any) -> QPushButton:
    found = first.findChild(QPushButton, "firstSetupStart")
    assert found is not None
    return found


def ready_first(rig: Rig, window: Any, *, method: str = "trend") -> Any:
    """코인 둘과 3단계 칸을 채운 처음 설정. 방식은 `method`(`trend`나 `mean_reversion`)."""
    apply_all(window, rig)
    first = first_screen(window)
    choose_first_setup(first, AAA, BBB)
    fill_trade_fields(first)
    if method != "trend":
        first.findChild(QRadioButton, f"firstSetupMethod_{method}").click()
    return first


def settings_rows(rig: Rig) -> dict[str, str]:
    return dict(rig.rows("SELECT key, value FROM settings"))


# --- D27: 처음 설정 -------------------------------------------------------------------------------


@pytest.mark.scenario("D27")
@pytest.mark.parametrize("name", [name for name, _ in LINE_VALUES if name != "MrFirstRatio"])
def test_d27_a_blank_required_field_keeps_the_start_button_off(
    windows: Callable[..., tuple[Rig, Any]], name: str
) -> None:
    rig, window = windows()
    first = ready_first(rig, window)
    assert start_button(first).isEnabled()  # 다 채우면 눌린다
    line(first, f"firstSetup{name}").setText("")
    assert not start_button(first).isEnabled()
    reason = first.findChild(QLabel, "firstSetupStartReason")
    assert reason is not None
    assert reason.text() == READ_MORE
    assert settings_rows(rig).get("first_setup_level") is None  # 저장되지 않았다


@pytest.mark.scenario("D27")
def test_d27_the_other_methods_first_ratio_may_stay_blank_but_the_chosen_ones_may_not(
    windows: Callable[..., tuple[Rig, Any]],
) -> None:
    rig, window = windows()
    first = ready_first(rig, window)  # 추세추종
    line(first, "firstSetupMrFirstRatio").setText("")
    assert start_button(first).isEnabled()  # 고르지 않은 방식의 칸은 비어도 된다
    first.findChild(QRadioButton, "firstSetupMethod_mean_reversion").click()
    assert not start_button(first).isEnabled()  # 평균회귀를 고르면 그 칸이 꼭 필요하다
    line(first, "firstSetupMrFirstRatio").setText("20")
    line(first, "firstSetupFirstRatio").setText("")
    assert start_button(first).isEnabled()  # 평균회귀면 추세추종의 칸이 비어도 시작한다


@pytest.mark.scenario("D27")
def test_d27_without_the_read_box_or_a_chosen_button_the_start_button_is_off(
    windows: Callable[..., tuple[Rig, Any]],
) -> None:
    rig, window = windows()
    apply_all(window, rig)
    first = first_screen(window)
    choose_first_setup(first, AAA, BBB)
    fill_trade_fields(first, read=False)
    assert not start_button(first).isEnabled()  # 다 채우고 읽음을 고르지 않음
    first.findChild(QCheckBox, "firstSetupRead").setChecked(True)
    assert start_button(first).isEnabled()  # 다 채우고 고름
    for name in ("firstSetupMethod_trend", "firstSetupPauseStop_keep", "firstSetupMrSell_no"):
        button = first.findChild(QRadioButton, name)
        group = button.group()
        group.setExclusive(False)
        button.setChecked(False)
        group.setExclusive(True)
        assert not start_button(first).isEnabled(), name  # 고르기 단추도 꼭 골라야 한다
        button.click()
        assert start_button(first).isEnabled(), name


@pytest.mark.scenario("D27")
@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (  # 예산 100,000원, 투자 비율 50%·50%에 첫 매수 10%(5,000원)
            {"Budget": "100,000", "CoinRatio_KRW-AAA": "50", "CoinRatio_KRW-BBB": "50",
             "FirstRatio": "10"},
            "첫 매수 금액(5,000원)이 하한(5,790원)보다 작습니다.",
        ),
        (
            {"CoinRatio_KRW-AAA": "50", "CoinRatio_KRW-BBB": "49.99"},
            "코인별 투자 비율의 합이 100%여야 합니다(지금 99.99%).",
        ),
        ({"Tolerance": "2.01"}, "0%에서 2% 사이로 넣으세요."),
    ],
    ids=["first-amount", "ratio-sum", "tolerance"],
)
def test_d27_a_value_that_is_not_accepted_shows_the_text_and_is_not_saved(
    windows: Callable[..., tuple[Rig, Any]], changes: dict[str, str], message: str
) -> None:
    rig, window = windows()
    first = ready_first(rig, window)
    for name, value in changes.items():
        line(first, f"firstSetup{name}").setText(value)
    assert start_button(first).isEnabled()
    start_button(first).click()
    rig.settle()
    apply_all(window, rig)
    assert any(message in text for text in visible_texts(first)), visible_texts(first)
    assert settings_rows(rig).get("first_setup_level") is None  # 저장되지 않는다
    assert current_screen(window) is ScreenId.FIRST_SETUP


@pytest.mark.scenario("D27")
def test_d27_a_correct_start_saves_level_three_and_creates_the_virtual_account(
    windows: Callable[..., tuple[Rig, Any]],
) -> None:
    rig, window = windows()
    first = ready_first(rig, window)
    start_button(first).click()
    rig.settle()
    apply_all(window, rig)
    assert settings_rows(rig)["first_setup_level"] == "3"
    ((funds, krw),) = rig.rows("SELECT funds, krw FROM paper_accounts")
    assert (D(funds), D(krw)) == (D(1_000_000), D(1_000_000))  # 가상 계정이 생긴다
    assert current_screen(window) is not ScreenId.FIRST_SETUP
    assert rig.sink.of(SettingsView)[-1].first_setup_done is True


# --- D28: 예전 기록(3단계 앞의 처음 설정) ---------------------------------------------------------


def _level(value: str) -> Callable[[Any], None]:
    def seed(conn: Any) -> None:
        conn.execute("UPDATE settings SET value = ? WHERE key = 'first_setup_level'", (value,))
        conn.commit()

    return seed


@pytest.mark.scenario("D28")
def test_d28_a_level_two_record_shows_the_first_setup_again_with_the_saved_choices(
    windows: Callable[..., tuple[Rig, Any]], tmp_path: Path
) -> None:
    rig, window = windows(d_rig(tmp_path / "old", coins=(AAA, BBB), seed=_level("2")))
    apply_all(window, rig)
    assert current_screen(window) is ScreenId.FIRST_SETUP
    first = first_screen(window)
    for market in (AAA, BBB):  # 저장된 코인이 채워져 있다
        box = first.findChild(QCheckBox, f"firstSetupCoin_{market}")
        assert box is not None
        assert box.isChecked()
    for slot in AiSlot:  # 저장된 모델과 노력(`FOUR`의 haiku, low)이 채워져 있고 잠겨 있다
        for kind, value in (("Model", "haiku"), ("Effort", "low")):
            combo = first.findChild(QComboBox, f"firstSetupAi{kind}_{slot.value}")
            assert combo is not None
            assert combo.currentText() == value, (slot, kind)
            assert not combo.isEnabled(), (slot, kind)
    notice = first.findChild(QLabel, "firstSetupLegacyNotice")
    assert notice is not None
    assert not notice.isHidden()
    assert not start_button(first).isEnabled()  # 3단계 칸을 넣기 전에는 누를 수 없다
    # 마치기 전에는 회차가 없다
    answer(rig, direction="오를 것", buy_price="990")
    advance(rig, 3 * 3600)
    assert rig.rows("SELECT number FROM rounds") == []
    assert orders(rig) == []


# --- D29: 보유 중인 코인 빼기 ---------------------------------------------------------------------


@pytest.mark.scenario("D29")
def test_d29_removing_a_held_coin_asks_first_and_cancel_saves_nothing(
    windows: Callable[..., tuple[Rig, Any]], tmp_path: Path
) -> None:
    program_buy = order_seed(BBB, OrderSide.BUY, OrderKind.LIMIT, D(990), "10")
    seed = seeds(holding_seed(BBB, "10", "900"), program_buy)
    rig, window = windows(d_rig(tmp_path / "held", coins=(AAA, BBB), seed=seed))
    rig.data.refresh_markets()  # 설정 화면의 코인 목록
    rig.settle()
    apply_all(window, rig)
    select_screen(window, ScreenId.SETTINGS)
    settings = screen_widget(window, ScreenId.SETTINGS)
    asked: list[tuple[str, str]] = []
    answers = iter([False, True])

    def handler(title: str, body: str) -> bool:
        asked.append((title, body))
        return next(answers)

    window.set_confirm_handler(handler)
    box = settings.findChild(QCheckBox, f"settingsCoin_{BBB}")
    assert box is not None
    assert box.isChecked()
    box.click()  # 코인을 뺀다
    line(settings, "settingsCoinRatio_KRW-AAA").setText("100")
    save = settings.findChild(QPushButton, "settingsSave")
    assert save is not None
    assert save.isEnabled()
    save.click()
    rig.settle()
    assert len(asked) == 1  # SCR-06-6
    assert rig.rows("SELECT value FROM settings WHERE key = 'coins'") != []
    coins = str(rig.rows("SELECT value FROM settings WHERE key = 'coins'"))
    assert BBB in coins  # 취소: 목록에 남는다
    assert [row[4] for row in orders(rig)] == ["open"]
    assert "A-16" not in all_codes(rig)
    save.click()  # 확인
    rig.settle()
    assert len(asked) == 2
    assert BBB not in str(rig.rows("SELECT value FROM settings WHERE key = 'coins'"))
    assert [row[4] for row in orders(rig)] == ["canceled"]  # 그 코인의 프로그램 주문이 취소되고
    assert all_codes(rig).count("A-16") == 1


# --- D34: 저장된 설정이 더 큰 최소 주문 금액에 걸림 -----------------------------------------------


@pytest.mark.scenario("D34")
def test_d34_a_saved_setting_that_fails_the_minimum_order_blocks_buys_until_it_is_fixed(
    windows: Callable[..., tuple[Rig, Any]], tmp_path: Path
) -> None:
    trade = d_trade((AAA,), method=TradeMethod.TREND, first_ratio=D(20), mr_first_ratio=D(6))
    trade = trade.__class__(**{**trade.__dict__, "budget": D(100_000)})
    held = holding_seed(AAA, "10", "900")
    rig = d_rig(tmp_path / "min", trade=trade, min_order=D(5200), seed=held)
    rig, window = windows(rig)
    assert all_codes(rig).count("A-15") == 1  # 평균회귀 첫 매수 6,000원이 하한(6,021.05…)보다 작다
    answer(rig, direction="오를 것", buy_price="990")
    rig.round()
    assert [row[4] for row in orders(rig)] == []  # 새 매수가 막힌다
    reason = rig.rows("SELECT no_order_reason FROM reports")[0][0]
    assert reason == "설정이 검사에 걸림"
    apply_all(window, rig)
    select_screen(window, ScreenId.SETTINGS)
    settings = screen_widget(window, ScreenId.SETTINGS)
    texts = visible_texts(settings)
    # 그 칸 아래의 글
    assert any("첫 매수 금액" in text and "하한" in text for text in texts), texts
    line(settings, "settingsMrFirstRatio").setText("7")
    save = settings.findChild(QPushButton, "settingsSave")
    assert save is not None
    save.click()
    rig.settle()
    apply_all(window, rig)
    answer(rig, direction="오를 것", buy_price="990", result_text="지난 결과",
           compared=[("검색 판별", 1), ("봉 판별", 1), ("최종 예측", 1)])  # fmt: skip
    rig.round()
    # 막음이 풀린다: 7%라 하한을 넘는 첫 매수가 지정가 매수 한 건으로 걸린다(보유 중이면 추가 매수)
    assert [(row[1], row[2], row[3], row[4]) for row in orders(rig)] == [
        ("buy", "limit", "judgment", "open")
    ]


@pytest.mark.scenario("D34")
def test_d34_a_sell_still_goes_out_while_buys_are_blocked_by_the_settings_check(
    windows: Callable[..., tuple[Rig, Any]], tmp_path: Path
) -> None:
    trade = d_trade((AAA,), method=TradeMethod.TREND, mr_first_ratio=D(6))
    held = holding_seed(AAA, "10", "900")
    rig, _ = windows(d_rig(tmp_path / "sell", trade=trade, min_order=D(5200), seed=held))
    assert all_codes(rig).count("A-15") == 1
    answer(rig, direction="내릴 것", sell_price="1010")
    rig.round()
    assert [(row[1], row[2], row[4]) for row in orders(rig)] == [("sell", "limit", "open")]
