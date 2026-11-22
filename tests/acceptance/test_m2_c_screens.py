"""M2 명세 6절의 화면 시나리오: C28(3.12.1~3.12.4).

창을 가짜 `UiActions`로 만들어 화면 값을 `apply`로 넣고, 사람이 하는 일은 칸과 단추로 한다. 객체
이름은 3.12.1, 3.12.2, 3.12.5의 이름이다. 값은 모두 만든 값이다.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from PySide6.QtCore import Qt
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
    AiChoice,
    AiSlot,
    AlertCategory,
    AlertsView,
    AlertView,
    Belief,
    CandleJudgment,
    ChecklistItem,
    ComparedItem,
    Direction,
    Effort,
    EvidenceItem,
    LastResult,
    LoadState,
    Market,
    MarketsView,
    Mode,
    ReportListView,
    ReportState,
    ReportView,
    RoundItem,
    ScreenId,
    SearchJudgment,
    SettingsView,
    SourceItem,
    TokenUsage,
    UiContext,
    Verdict,
)
from coin_report_trader.ui import create_main_window, current_screen, screen_widget, select_screen
from coin_report_trader.ui.info_icon import InfoIcon

AAA, BBB = "KRW-AAA", "KRW-BBB"
COINS = (AAA, BBB)
CLOSE = datetime(2026, 1, 5, 4, tzinfo=UTC)
NOTICE_SA = (
    "개인용입니다. 쓰는 사람은 각자 자기 업비트 키와 자기 Claude Code 로그인으로, 자기 책임으로 "
    "씁니다. 아무것도 보증하지 않습니다. 팔거나 돈을 받고 넘기지 않습니다."
)
NOTICE_A = "앱의 AI 사용은 이 PC 사용자의 Claude 구독 사용 한도에서 빠집니다."


class Actions:
    """`UiActions`의 가짜. 부른 동작과 인자를 적는다."""

    def __init__(self) -> None:
        self.saves: list[dict[str, Any]] = []
        self.reports: list[str] = []
        self.report: list[int] = []
        self.calls: list[str] = []

    def save_settings(self, coins: Any, retention_days: Any, **kwargs: Any) -> None:
        self.saves.append({"coins": coins, "retention_days": retention_days, **kwargs})

    def load_reports(self, market: str) -> None:
        self.reports.append(market)

    def load_report(self, report_id: int) -> None:
        self.report.append(report_id)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *args, **kwargs: self.calls.append(name)


@pytest.fixture
def actions() -> Actions:
    return Actions()


@pytest.fixture
def window(qapp: QApplication, actions: Actions) -> Any:
    made = create_main_window(UiContext("0.0.1", "X", actions))
    made.set_confirm_handler(lambda _title, _body: True)
    made.resize(1100, 1000)
    made.show()
    yield made
    made.close()
    made.deleteLater()


def find(parent: QWidget, kind: type, name: str) -> Any:
    found = parent.findChild(kind, name)
    assert found is not None, name
    return found


def combos(screen: QWidget) -> list[QComboBox]:
    return [
        find(screen, QComboBox, f"firstSetupAi{kind}_{slot.value}")
        for slot in AiSlot
        for kind in ("Model", "Effort")
    ]


def first_setup(window: Any) -> QWidget:
    window.apply(SettingsView())
    window.apply(MarketsView(LoadState.READY, (Market(AAA, "이름 AAA", False),)))
    screen = screen_widget(window, ScreenId.FIRST_SETUP)
    find(screen, QWidget, f"firstSetupCoin_{AAA}").click()
    find(screen, QRadioButton, "firstSetupRetention30").click()
    find(screen, QRadioButton, "firstSetupUnit_minute60").click()
    return screen


def item(number: int, state: ReportState, **changes: Any) -> RoundItem:
    values: dict[str, Any] = {
        "report_id": number * 10,
        "round_number": number,
        "close_utc": CLOSE,
        "state": state,
        "direction": Direction.UP if state is ReportState.OK else None,
        "failure": None,
    }
    values.update(changes)
    return RoundItem(**values)


def ok_view(round_item: RoundItem) -> ReportView:
    return ReportView(
        report_id=round_item.report_id,
        market=AAA,
        round_number=round_item.round_number,
        close_utc=round_item.close_utc,
        state=ReportState.OK,
        failure=None,
        base_price=Decimal("100"),
        search_judgment=SearchJudgment.POSITIVE,
        candle_judgment=CandleJudgment.UP,
        direction=Direction.UP,
        predicted_price=Decimal("102"),
        predicted_reason="오름 쪽 요소가 많습니다.",
        buy_price=Decimal("99"),
        sell_price=None,
        result_text="회차 1의 예측은 맞았습니다.",
        last_result=LastResult(1, Decimal("102"), Decimal("101"), Decimal("-0.0098"), Verdict.HIT),
        compared=(ComparedItem(AiSlot.CANDLE, 1, "오른 봉이 많음", Verdict.HIT),),
        checklist=(ChecklistItem(1, "기울기가 오름 쪽", Belief.HIGH, 2, 1),),
        evidence=(EvidenceItem(AiSlot.FINAL, 1, "두 부분이 같은 쪽입니다."),),
        sources=(SourceItem(1, "예시 글", "https://example.com/a"),),
        tokens=TokenUsage(1500, 300, 300, 0),
        elapsed_report_seconds=10,
    )


def failed_view(round_item: RoundItem) -> ReportView:
    return ReportView(
        report_id=round_item.report_id,
        market=AAA,
        round_number=round_item.round_number,
        close_utc=round_item.close_utc,
        state=round_item.state,
        failure=round_item.failure,
        base_price=None,
        search_judgment=None,
        candle_judgment=None,
        direction=None,
        predicted_price=None,
        predicted_reason=None,
        buy_price=None,
        sell_price=None,
        result_text=None,
        last_result=None,
        compared=(),
        checklist=(),
        evidence=(),
        sources=(),
        tokens=None,
        elapsed_report_seconds=None,
    )


def open_reports(window: Any) -> QWidget:
    window.apply(SettingsView(COINS, 30, True))
    screen = screen_widget(window, ScreenId.REPORTS)
    select_screen(window, ScreenId.REPORTS)
    QApplication.processEvents()
    return screen


def visible_texts(screen: QWidget) -> list[str]:
    return [label.text() for label in screen.findChildren(QLabel) if label.isVisibleTo(screen)]


# --- 처음 설정의 여덟 칸 ------------------------------------------------------------------------


@pytest.mark.scenario("C28")
def test_c28_the_eight_slots_are_blank_combo_boxes_and_start_needs_all_of_them(
    window: Any, actions: Actions
) -> None:
    screen = first_setup(window)
    boxes = combos(screen)
    assert len(boxes) == 8
    assert all(box.currentText() == "" for box in boxes)  # 처음에는 빈 항목이다
    start = find(screen, QPushButton, "firstSetupStart")
    reason = find(screen, QLabel, "firstSetupStartReason")
    for box in boxes[:-1]:
        box.setCurrentIndex(1)
    assert not start.isEnabled()  # 하나라도 비면 눌리지 않는다
    assert reason.isVisibleTo(screen)
    assert reason.text()
    start.click()
    assert actions.saves == []
    boxes[-1].setCurrentIndex(1)
    assert start.isEnabled()
    start.click()
    (saved,) = actions.saves
    choices = saved["ai_choices"]
    assert len(choices) == 4
    assert all(isinstance(choice, AiChoice) for choice in choices)
    assert {choice.slot for choice in choices} == set(AiSlot)
    assert all(isinstance(choice.effort, Effort) for choice in choices)


# --- 현황에서 보고서 화면으로 -------------------------------------------------------------------


@pytest.mark.scenario("C28")
def test_c28_the_report_button_and_the_a26_line_open_that_coin_and_round(
    window: Any, actions: Actions
) -> None:
    window.apply(SettingsView(COINS, 30, True))
    status = screen_widget(window, ScreenId.STATUS)
    find(status, QPushButton, f"statusCoinReport_{BBB}").click()
    assert current_screen(window) is ScreenId.REPORTS
    coin = find(screen_widget(window, ScreenId.REPORTS), QListWidget, "reportCoin")
    assert COINS[coin.currentRow()] == BBB
    assert actions.reports[-1] == BBB
    select_screen(window, ScreenId.STATUS)
    alert = AlertView(
        id=1, created_at=CLOSE, mode=Mode.PAPER, category=AlertCategory.REPORT, code="A-26",
        coin=AAA, text="회차 2의 보고서를 받았습니다. 최종 판단: 오를 것.", seen=True,
        report_id=20,
    )  # fmt: skip
    window.apply(AlertsView((alert,), {}, LoadState.READY))
    find(status, QPushButton, f"alertTab_{AlertCategory.REPORT.value}").click()
    QApplication.processEvents()
    QTest.mouseClick(find(status, QLabel, "alertLine_1"), Qt.MouseButton.LeftButton)
    assert current_screen(window) is ScreenId.REPORTS
    assert COINS[coin.currentRow()] == AAA
    window.apply(ReportListView(AAA, (item(3, ReportState.OK), item(2, ReportState.OK)),
                                LoadState.READY))  # fmt: skip
    rounds = find(screen_widget(window, ScreenId.REPORTS), QListWidget, "reportRounds")
    assert rounds.currentRow() == 1  # 그 회차(보고서 20)가 골라진다
    assert actions.report[-1] == 20


# --- 보고서 화면의 상태별 글 -------------------------------------------------------------------


@pytest.mark.scenario("C28")
def test_c28_each_report_state_shows_the_spec_text(window: Any) -> None:
    screen = open_reports(window)
    window.apply(ReportListView(AAA, (), LoadState.READY))
    assert find(screen, QLabel, "reportMessage").text() == "아직 받은 보고서가 없습니다."
    cases = [
        (item(1, ReportState.RUNNING), "이 회차의 보고서를 만드는 중입니다."),
        (item(1, ReportState.FAILED, failure="형식 오류"),
         "이 회차에 이 코인의 보고서를 받지 못했습니다: 형식 오류"),
        (item(1, ReportState.SKIPPED, failure="현재가를 읽지 못함"),
         "이 회차에 이 코인은 쉬었습니다: 현재가를 읽지 못함"),
        (item(1, ReportState.INTERRUPTED), "이 회차는 끝까지 가지 못했습니다(끊긴 회차)."),
    ]  # fmt: skip
    for round_item, expected in cases:
        window.apply(ReportListView(AAA, (round_item,), LoadState.READY))
        window.apply(failed_view(round_item))
        QApplication.processEvents()
        message = find(screen, QLabel, "reportMessage")
        assert message.isVisibleTo(screen), round_item.state
        assert message.text() == expected, round_item.state
        for name in ("reportLastResult", "reportDecision", "reportEvidence", "reportChecks"):
            assert not find(screen, QWidget, name).isVisibleTo(screen), (round_item.state, name)


@pytest.mark.scenario("C28")
def test_c28_an_ok_report_with_a_last_judgment_shows_the_sections_without_opinion_or_hold(
    window: Any,
) -> None:
    screen = open_reports(window)
    ok = item(2, ReportState.OK)
    window.apply(ReportListView(AAA, (ok,), LoadState.READY))
    window.apply(ok_view(ok))
    QApplication.processEvents()
    assert not find(screen, QLabel, "reportMessage").isVisibleTo(screen)
    for name in ("reportLastResult", "reportChecks", "reportDecision", "reportEvidence",
                 "reportSources", "reportUsage", "reportNotices"):  # fmt: skip
        assert find(screen, QWidget, name).isVisibleTo(screen), name
    texts = "\n".join(visible_texts(screen))
    assert "차이: -0.98%" in texts
    assert "의견" not in texts  # 의견 칸이 없다(REQ-AI-023)
    assert "보류" not in texts  # '보류'의 글이 없다


# --- 처음 설정의 10 자리 'ⓘ' ------------------------------------------------------------------


@pytest.mark.scenario("C28")
def test_c28_the_read_first_icon_carries_the_five_notices_that_are_not_in_any_label(
    window: Any,
) -> None:
    screen = first_setup(window)
    icon = find(screen, InfoIcon, "firstSetupNoticesInfo")
    assert icon.accessibleDescription() == icon.toolTip()  # 읽는 설명이 툴팁과 같다
    lines = icon.toolTip().split("\n")
    assert len(lines) == 5  # 가, 마, 바, 사, 아
    assert "투자 자문이 아닙니다" in lines[0]  # 가
    assert lines[3].endswith(NOTICE_SA)  # 사
    assert lines[4].endswith(NOTICE_A)  # 아
    shown = "\n".join(visible_texts(screen))
    for line in lines:
        assert line.lstrip("• ") not in shown  # 늘 보이는 라벨에 없다
