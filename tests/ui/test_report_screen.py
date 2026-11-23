"""보고서 화면(M2 명세 3.12.1, 화면 설계서 5-5)과 그 화면을 부르는 동작.

값은 모두 시험이 만든 값이다. AI가 쓴 글의 `<b>`나 링크 꼴이 글자 그대로 보이는지도 본다.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QLabel, QListWidget, QPushButton, QScrollArea, QWidget

from coin_report_trader.core import (
    AiSlot,
    AiStatus,
    AiStatusView,
    Belief,
    CandleJudgment,
    ChecklistItem,
    ComparedItem,
    Direction,
    EvidenceItem,
    LastResult,
    LoadState,
    ReportListView,
    ReportState,
    ReportView,
    RoundItem,
    SaveResult,
    ScreenId,
    SearchJudgment,
    SettingsView,
    SourceItem,
    TokenUsage,
    UiContext,
    Verdict,
)
from coin_report_trader.ui import (
    create_main_window,
    current_screen,
    screen_widget,
    select_screen,
)
from coin_report_trader.ui.info_icon import InfoIcon

CLOSE = datetime(2026, 1, 2, 1, 30, tzinfo=UTC)  # 한국 시간 2026-01-02 10:30
COINS = ("KRW-AAA", "KRW-BBB")


class Actions:
    def __init__(self) -> None:
        self.reports: list[str] = []
        self.report: list[int] = []
        self.others: list[str] = []

    def refresh_markets(self) -> None:
        self.others.append("refresh_markets")

    def save_settings(self, coins, retention_days, **kwargs) -> None:
        self.others.append("save_settings")

    def load_reports(self, market) -> None:
        self.reports.append(market)

    def load_report(self, report_id) -> None:
        self.report.append(report_id)

    def retry_candles(self) -> None:
        self.others.append("retry_candles")

    def mark_seen(self, alert_ids) -> None:
        self.others.append("mark_seen")

    def delete_alert(self, alert_id) -> None:
        self.others.append("delete_alert")

    def clear_alerts(self, category) -> None:
        self.others.append("clear_alerts")

    def save_webhook(self, category, url) -> None:
        self.others.append("save_webhook")

    def delete_webhook(self, category) -> None:
        self.others.append("delete_webhook")

    def send_test(self, category) -> None:
        self.others.append("send_test")


@pytest.fixture
def actions() -> Actions:
    return Actions()


@pytest.fixture
def window(qapp: QApplication, actions: Actions):
    made = create_main_window(UiContext("0.0.1", "X", actions))
    made.resize(1100, 800)
    made.show()
    yield made
    made.close()
    made.deleteLater()


def _round(number: int, state: ReportState = ReportState.OK, **changes) -> RoundItem:
    values = {
        "report_id": number * 10,
        "round_number": number,
        "close_utc": CLOSE,
        "state": state,
        "direction": Direction.UP if state is ReportState.OK else None,
        "failure": None,
    }
    values.update(changes)
    return RoundItem(**values)


def _listing(market: str, *items: RoundItem, state=LoadState.READY) -> ReportListView:
    return ReportListView(market, tuple(items), state)


def _report(item: RoundItem, **changes) -> ReportView:
    values = {
        "report_id": item.report_id,
        "market": "KRW-AAA",
        "round_number": item.round_number,
        "close_utc": item.close_utc,
        "state": item.state,
        "failure": item.failure,
        "base_price": Decimal("1234"),
        "search_judgment": SearchJudgment.POSITIVE,
        "candle_judgment": CandleJudgment.UP,
        "direction": Direction.UP,
        "predicted_price": Decimal("1260"),
        "predicted_reason": "봉 통계의 오름 쪽 요소가 많습니다.",
        "buy_price": Decimal("1240"),
        "sell_price": None,
        "result_text": "회차 11의 예측은 맞았습니다.",
        "last_result": LastResult(
            round_number=11,
            predicted_price=Decimal("1234"),
            actual_price=Decimal("1250"),
            diff=Decimal("0.0130"),
            hit=Verdict.HIT,
        ),
        "compared": (
            ComparedItem(AiSlot.CANDLE, 1, "오른 봉 비율이 절반을 넘음", Verdict.HIT),
            ComparedItem(AiSlot.FINAL, 1, "두 부분이 같은 쪽", Verdict.UNKNOWN),
        ),
        "checklist": (
            ChecklistItem(1, "추세 기울기가 오름 쪽", Belief.HIGH, 6, 5),
            ChecklistItem(2, "오른 봉 비율이 절반을 넘음", Belief.LOW, 4, 1),
        ),
        "evidence": (
            EvidenceItem(AiSlot.SEARCH, 1, "공개 글에서 새 상장 소식이 이어졌습니다."),
            EvidenceItem(AiSlot.CANDLE, 1, "오른 봉 비율이 절반을 넘습니다."),
            EvidenceItem(AiSlot.FINAL, 1, "두 부분이 같은 쪽입니다."),
        ),
        "sources": (SourceItem(1, "예시 글 하나", "https://example.com/a"),),
        "tokens": TokenUsage(12345, 2345, 34567, 4567),
        "elapsed_report_seconds": 192,
    }
    values.update(changes)
    return ReportView(**values)


def _ready(window, coins=COINS) -> QWidget:
    window.apply(SettingsView(coins, 30, True))
    return screen_widget(window, ScreenId.REPORTS)


def _open(window) -> QWidget:
    screen = _ready(window)
    select_screen(window, ScreenId.REPORTS)
    QApplication.processEvents()
    return screen


def _text(screen: QWidget, name: str) -> str | None:
    label = screen.findChild(QLabel, name)
    assert label is not None, name
    return None if label.isHidden() else label.text()


def _lines(screen: QWidget, section: str) -> list[str]:
    widget = screen.findChild(QWidget, section)
    assert widget is not None, section
    assert not widget.isHidden(), section
    return [label.text() for label in widget.findChildren(QLabel) if label.text()]


def _rounds(screen: QWidget) -> list[str]:
    box = screen.findChild(QListWidget, "reportRounds")
    assert box is not None
    return [box.item(i).text() for i in range(box.count())]


def _coin_choices(screen: QWidget) -> list[str]:
    box = screen.findChild(QListWidget, "reportCoin")
    assert box is not None
    return [box.item(i).text() for i in range(box.count())]


# --- 틀 ---


def test_the_screen_has_the_spec_names_and_no_empty_body_text(window):
    screen = _ready(window)
    assert screen.objectName() == "screen_reports"
    assert screen.parentWidget().parentWidget().objectName() == "screenScroll_reports"
    assert screen.findChild(QLabel, "screenTitle").text() == "보고서"
    assert screen.findChild(QLabel, "screenBody") is None
    for name, kind in (
        ("reportAiStatus", QLabel),
        ("reportCoin", QListWidget),
        ("reportRounds", QListWidget),
        ("reportMessage", QLabel),
        ("reportAiMark", QLabel),
        ("reportDetailScroll", QScrollArea),
    ):
        assert screen.findChild(kind, name) is not None, name
    for name in (
        "reportLastResult",
        "reportChecks",
        "reportDecision",
        "reportEvidence",
        "reportSources",
        "reportUsage",
        "reportNotices",
    ):
        assert screen.findChild(QWidget, name) is not None, name


def test_the_coin_choice_lists_the_chosen_coins_in_order_and_the_first_is_chosen(window):
    screen = _ready(window, ("KRW-BBB", "KRW-AAA"))
    assert _coin_choices(screen) == ["BBB", "AAA"]
    box = screen.findChild(QListWidget, "reportCoin")
    assert box.currentRow() == 0


def test_before_the_first_setup_the_coin_choice_is_empty_and_the_text_says_so(window, actions):
    window.apply(SettingsView())
    screen = screen_widget(window, ScreenId.REPORTS)
    assert _coin_choices(screen) == []
    assert _text(screen, "reportMessage") == "처음 설정을 마치면 보고서를 볼 수 있습니다."
    select_screen(window, ScreenId.REPORTS)
    assert actions.reports == []  # 처음 설정을 마치기 전에는 부르지 않는다


def test_sections_are_hidden_while_there_is_only_a_message(window):
    screen = _open(window)
    assert _text(screen, "reportMessage") == "불러오는 중"
    for name in ("reportLastResult", "reportChecks", "reportDecision", "reportEvidence"):
        assert screen.findChild(QWidget, name).isHidden()
    assert screen.findChild(QLabel, "reportAiMark").isHidden()


# --- 부르는 때 ---


def test_the_reports_are_asked_when_the_screen_is_shown_and_not_when_coins_are_filled(
    window, actions
):
    _ready(window)
    assert actions.reports == []  # 코인 고르기를 채울 때는 부르지 않는다
    select_screen(window, ScreenId.REPORTS)
    assert actions.reports == ["KRW-AAA"]
    select_screen(window, ScreenId.STATUS)
    select_screen(window, ScreenId.REPORTS)
    assert actions.reports == ["KRW-AAA", "KRW-AAA"]
    window.apply(SettingsView(COINS, 30, True, candle_count=100))  # 설정을 다시 채워도
    assert actions.reports == ["KRW-AAA", "KRW-AAA"]


def test_choosing_a_coin_asks_for_that_coins_reports_and_the_first_round_is_chosen(window, actions):
    screen = _open(window)
    window.apply(_listing("KRW-AAA", _round(2), _round(1)))
    assert _rounds(screen) == [
        "회차 2 · 2026-01-02 10:30 · 오를 것",
        "회차 1 · 2026-01-02 10:30 · 오를 것",
    ]
    assert screen.findChild(QListWidget, "reportRounds").currentRow() == 0
    assert actions.report == [20]  # 처음에는 맨 위가 골라진다
    screen.findChild(QListWidget, "reportCoin").setCurrentRow(1)
    assert actions.reports == ["KRW-AAA", "KRW-BBB"]
    window.apply(_listing("KRW-BBB", _round(5, report_id=51)))
    assert actions.report == [20, 51]
    assert _rounds(screen) == ["회차 5 · 2026-01-02 10:30 · 오를 것"]


def test_choosing_a_round_asks_for_that_report(window, actions):
    screen = _open(window)
    window.apply(_listing("KRW-AAA", _round(2), _round(1)))
    screen.findChild(QListWidget, "reportRounds").setCurrentRow(1)
    assert actions.report == [20, 10]


def test_a_list_for_another_coin_does_not_change_what_is_shown(window, actions):
    screen = _open(window)
    window.apply(_listing("KRW-BBB", _round(7, report_id=70)))
    assert _rounds(screen) == []
    assert actions.report == []


def test_the_report_is_asked_again_when_the_state_of_the_shown_round_changes(window, actions):
    screen = _open(window)
    running = _round(3, ReportState.RUNNING)
    window.apply(_listing("KRW-AAA", running, _round(2)))
    assert actions.report == [30]
    assert _rounds(screen)[0].endswith("만드는 중")
    done = _round(3)
    window.apply(_listing("KRW-AAA", done, _round(2)))  # 같은 번호의 `state`가 바뀌었다
    assert actions.report == [30, 30]
    window.apply(_listing("KRW-AAA", done, _round(2)))  # 같은 목록은 다시 부르지 않는다
    assert actions.report == [30, 30]


def test_a_new_round_on_top_does_not_take_the_chosen_round_away(window, actions):
    screen = _open(window)
    window.apply(_listing("KRW-AAA", _round(2), _round(1)))
    screen.findChild(QListWidget, "reportRounds").setCurrentRow(1)
    actions.report.clear()
    window.apply(_listing("KRW-AAA", _round(3), _round(2), _round(1)))
    assert screen.findChild(QListWidget, "reportRounds").currentRow() == 2
    assert actions.report == []


# --- 줄의 글 ---


@pytest.mark.parametrize(
    ("item", "end"),
    [
        (_round(1), "오를 것"),
        (_round(1, direction=Direction.DOWN), "내릴 것"),
        (_round(1, ReportState.FAILED, failure="시간 초과"), "받지 못함"),
        (_round(1, ReportState.ERROR, failure="저장 실패"), "받지 못함"),
        (_round(1, ReportState.SKIPPED), "쉼"),
        (_round(1, ReportState.INTERRUPTED), "끊김"),
        (_round(1, ReportState.RUNNING), "만드는 중"),
    ],
)
def test_the_round_line_ends_by_its_state(window, item, end):
    screen = _open(window)
    window.apply(_listing("KRW-AAA", item))
    assert _rounds(screen) == [f"회차 1 · 2026-01-02 10:30 · {end}"]


# --- 상태별 모습 ---


def _show(window, item: RoundItem, **changes) -> QWidget:
    screen = _open(window)
    window.apply(_listing("KRW-AAA", item))
    window.apply(_report(item, **changes))
    return screen


def test_no_reports_says_so_and_hides_the_detail(window):
    screen = _open(window)
    window.apply(_listing("KRW-AAA"))
    assert _text(screen, "reportMessage") == "아직 받은 보고서가 없습니다."
    assert screen.findChild(QWidget, "reportDecision").isHidden()


def test_a_list_that_cannot_be_read_says_the_report_is_unreadable(window):
    screen = _open(window)
    window.apply(_listing("KRW-AAA", state=LoadState.FAILED))
    assert _text(screen, "reportMessage") == "보고서를 읽지 못했습니다."


def test_loading_says_so_until_the_report_comes(window):
    screen = _open(window)
    window.apply(_listing("KRW-AAA", _round(1)))
    assert _text(screen, "reportMessage") == "불러오는 중"
    window.apply(_report(_round(1)))
    assert _text(screen, "reportMessage") is None


def test_a_report_that_could_not_be_read_shows_the_error_text_and_asking_again_clears_it(
    window, actions
):
    screen = _open(window)
    window.apply(_listing("KRW-AAA", _round(1), _round(0, report_id=5)))
    window.apply(_report(_round(1)))
    window.apply(SaveResult("report", ok=False))
    message = screen.findChild(QLabel, "reportMessage")
    assert message.text() == "보고서를 읽지 못했습니다."
    assert message.property("tone") == "warning"
    assert screen.findChild(QWidget, "reportDecision").isHidden()
    screen.findChild(QListWidget, "reportRounds").setCurrentRow(1)  # 다시 고르면 다시 부른다
    assert actions.report[-1] == 5
    assert _text(screen, "reportMessage") == "불러오는 중"


def test_running_failed_skipped_and_interrupted_show_one_text_instead_of_the_detail(window):
    cases = [
        (_round(1, ReportState.RUNNING), "이 회차의 보고서를 만드는 중입니다."),
        (
            _round(1, ReportState.FAILED, failure="시간 초과"),
            "이 회차에 이 코인의 보고서를 받지 못했습니다: 시간 초과",
        ),
        (
            _round(1, ReportState.ERROR, failure="저장 실패"),
            "이 회차에 이 코인의 보고서를 받지 못했습니다: 저장 실패",
        ),
        (
            _round(1, ReportState.SKIPPED, failure="봉 통계를 만들지 못함"),
            "이 회차에 이 코인은 쉬었습니다: 봉 통계를 만들지 못함",
        ),
        (_round(1, ReportState.INTERRUPTED), "이 회차는 끝까지 가지 못했습니다(끊긴 회차)."),
    ]
    for item, expected in cases:
        screen = _show(window, item, failure=item.failure)
        assert _text(screen, "reportMessage") == expected, item.state
        for name in ("reportLastResult", "reportChecks", "reportDecision", "reportEvidence"):
            assert screen.findChild(QWidget, name).isHidden(), (item.state, name)
        assert screen.findChild(QLabel, "reportAiMark").isHidden()
        assert screen.findChild(QWidget, "reportNotices").isHidden()


def test_a_failed_round_still_shows_the_usage_and_the_failure_in_the_error_color(window):
    item = _round(1, ReportState.FAILED, failure="한도 초과")
    screen = _show(window, item, failure="한도 초과")
    message = screen.findChild(QLabel, "reportMessage")
    assert message.property("tone") == "warning"
    assert "한도 초과" in message.text()
    usage = _lines(screen, "reportUsage")
    assert usage[1].startswith("세션 전체 토큰: 입력 12,345")
    # 만드는 중, 쉼, 끊김에는 사용량이 없다.
    for state in (ReportState.RUNNING, ReportState.SKIPPED, ReportState.INTERRUPTED):
        again = _show(window, _round(1, state), failure="까닭")
        assert again.findChild(QWidget, "reportUsage").isHidden()


# --- 보고서의 요소 ---


def test_the_detail_shows_the_ai_mark_and_every_section_in_the_spec_order(window):
    screen = _show(window, _round(11))
    assert _text(screen, "reportAiMark") == "AI가 쓴 글입니다. 틀릴 수 있습니다."
    names = [
        "reportLastResult",
        "reportChecks",
        "reportDecision",
        "reportEvidence",
        "reportSources",
        "reportUsage",
        "reportNotices",
    ]
    detail = screen.findChild(QScrollArea, "reportDetailScroll").widget()
    layout = detail.layout()
    items = [layout.itemAt(i).widget() for i in range(layout.count())]
    order = [item.objectName() for item in items if item is not None]
    assert [n for n in order if n in names] == names  # 5, 11, 6, 8, 9, 12, 10
    assert screen.findChild(QLabel, "reportMessage").isHidden()


def test_the_last_result_shows_the_text_the_compared_lines_and_the_program_values(window):
    screen = _show(window, _round(12))
    lines = _lines(screen, "reportLastResult")
    assert lines == [
        "지난 판단의 결과",
        "회차 11의 예측은 맞았습니다.",
        "근거를 견준 결과(회차 11의 근거)",
        "봉 판별 1. 오른 봉 비율이 절반을 넘음 → 맞음",
        "최종 예측 1. 두 부분이 같은 쪽 → 가릴 수 없음",
        "프로그램이 계산한 값",
        "지난 예측 가격(회차 11가 예측한 다음 회차 때의 값): 1,234원",
        "다음 회차 때의 실제 값: 1,250원",
        "차이: +1.30%",
        "오름·내림 예측: 맞음",
    ]


def test_missing_values_of_the_last_result_are_a_dash(window):
    last = LastResult(11, Decimal("1234"), None, None, None)
    screen = _show(window, _round(12), last_result=last, compared=())
    lines = _lines(screen, "reportLastResult")
    assert "다음 회차 때의 실제 값: -" in lines
    assert "차이: -" in lines
    assert "오름·내림 예측: -" in lines
    assert not any("근거를 견준 결과" in line for line in lines)


def test_there_is_no_last_result_block_when_there_is_none(window):
    screen = _show(window, _round(12), last_result=None)
    assert screen.findChild(QWidget, "reportLastResult").isHidden()


def test_the_check_list_has_the_four_columns_and_a_row_per_item(window):
    screen = _show(window, _round(12))
    assert _lines(screen, "reportChecks") == [
        "검증 목록",
        "항목",
        "믿음",
        "견준 횟수",
        "맞은 횟수",
        "추세 기울기가 오름 쪽",
        "높음",
        "6",
        "5",
        "오른 봉 비율이 절반을 넘음",
        "낮음",
        "4",
        "1",
    ]


def test_the_decision_shows_the_values_and_a_dash_for_a_missing_price(window):
    screen = _show(window, _round(12))
    assert _lines(screen, "reportDecision") == [
        "판단",
        "최종 판단: 오를 것",
        "예측 가격(다음 회차 때의 값): 1,260원",
        "예측 까닭: 봉 통계의 오름 쪽 요소가 많습니다.",
        "매수 제안가: 1,240원",
        "매도 제안가: -",
    ]
    nothing = _show(
        window,
        _round(13),
        direction=None,
        predicted_price=None,
        predicted_reason=None,
        buy_price=None,
    )
    lines = _lines(nothing, "reportDecision")
    assert "최종 판단: -" in lines
    assert "예측 가격(다음 회차 때의 값): -" in lines
    assert "예측 까닭: -" in lines
    assert "매수 제안가: -" in lines


def test_the_evidence_has_a_subtitle_per_part_with_its_lines(window):
    screen = _show(window, _round(12))
    assert _lines(screen, "reportEvidence") == [
        "근거",
        "검색 판별: 긍정",
        "1. 공개 글에서 새 상장 소식이 이어졌습니다.",
        "봉 판별: 오름",
        "1. 오른 봉 비율이 절반을 넘습니다.",
        "최종 예측",
        "1. 두 부분이 같은 쪽입니다.",
    ]


def test_the_sources_are_selectable_plain_text_and_never_links(window):
    screen = _show(
        window,
        _round(12),
        sources=(SourceItem(1, "예시 <b>글</b> 하나", "https://example.com/a?x=<i>"),),
    )
    lines = _lines(screen, "reportSources")
    assert lines == ["출처", "예시 <b>글</b> 하나 · https://example.com/a?x=<i>"]
    label = next(
        label
        for label in screen.findChild(QWidget, "reportSources").findChildren(QLabel)
        if label.text().startswith("예시")
    )
    assert label.textFormat() == Qt.TextFormat.PlainText
    assert label.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    assert not label.openExternalLinks()


def test_ai_written_texts_are_plain_text_so_markup_is_shown_as_it_is(window):
    markup = "<b>굵게</b> <a href='https://example.com'>링크</a>"
    screen = _show(
        window,
        _round(12),
        result_text=markup,
        predicted_reason=markup,
        evidence=(EvidenceItem(AiSlot.SEARCH, 1, markup),),
        checklist=(ChecklistItem(1, markup, Belief.HIGH, 1, 1),),
        compared=(ComparedItem(AiSlot.CANDLE, 1, markup, Verdict.HIT),),
    )
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()
    shown = [
        label
        for label in screen.findChildren(QLabel)
        if markup in label.text() and label.isVisibleTo(screen)
    ]
    assert len(shown) >= 5
    for label in shown:
        assert label.textFormat() == Qt.TextFormat.PlainText
        assert not label.openExternalLinks()


def test_the_usage_shows_the_tokens_with_commas_and_the_elapsed_time(window):
    screen = _show(window, _round(12))
    assert _lines(screen, "reportUsage") == [
        "사용량과 걸린 시간",
        "세션 전체 토큰: 입력 12,345 · 출력 2,345 · 캐시 읽기 34,567 · 캐시 만들기 4,567",
        "보고서까지 걸린 시간: 3분 12초",
    ]
    empty = _show(window, _round(13), tokens=None, elapsed_report_seconds=None)
    assert _lines(empty, "reportUsage")[1:] == [
        "세션 전체 토큰: -",
        "보고서까지 걸린 시간: -",
    ]


def test_no_empty_section_titles_are_left_when_there_is_nothing_to_show(window):
    screen = _show(
        window, _round(12), checklist=(), evidence=(), sources=(), last_result=None, compared=()
    )
    for name in ("reportLastResult", "reportChecks", "reportEvidence", "reportSources"):
        assert screen.findChild(QWidget, name).isHidden(), name
    assert not screen.findChild(QWidget, "reportDecision").isHidden()


# --- 'ⓘ'와 색 ---


def test_the_two_icons_hold_the_moved_texts(window):
    screen = _show(window, _round(12))
    # 판단의 'ⓘ'(`reportDecisionInfo`)는 3단계에서 없앴다(M3 명세 3.17.3).
    assert screen.findChild(InfoIcon, "reportDecisionInfo") is None
    for name, title, text in (
        ("reportUsageInfo", "사용량과 걸린 시간", "부분마다의 토큰은 나눠 재지 못했습니다."),
        (
            "reportNoticesInfo",
            "꼭 읽을 글",
            "이 프로그램은 투자 자문이 아닙니다. AI 판단은 틀릴 수 있습니다.",
        ),
    ):
        icon = screen.findChild(InfoIcon, name)
        assert icon is not None, name
        assert icon.toolTip() == icon.accessibleDescription() == text
        assert icon.accessibleName() == f"{title} 설명"
    texts = [label.text() for label in screen.findChildren(QLabel)]
    assert "예측 가격은 보여 주는 값이고 주문을 정하는 데 쓰지 않습니다." not in texts
    assert "부분마다의 토큰은 나눠 재지 못했습니다." not in texts


def test_no_value_in_the_report_is_painted_with_the_rise_or_fall_color(window):
    screen = _show(window, _round(12))
    for label in screen.findChildren(QLabel):
        assert not label.property("trend"), label.text()


# --- 맨 위 줄 ---


@pytest.mark.parametrize(
    ("status", "text", "warning"),
    [
        (
            AiStatus.NOT_CHOSEN,
            "설정에서 네 칸의 모델과 노력 값을 고르면 다음 회차부터 보고서를 만듭니다.",
            False,
        ),
        (AiStatus.NOT_FOUND, "Claude Code를 찾지 못했거나 버전을 읽지 못해 AI를 껐습니다.", True),
        (AiStatus.VERSION_OFF, "Claude Code 버전이 시험한 범위 밖이라 AI를 껐습니다.", True),
        (
            AiStatus.ERROR_OFF,
            "예상하지 못한 오류로 AI를 껐습니다. 프로그램을 다시 켜면 AI가 다시 돕니다.",
            True,
        ),
    ],
)
def test_the_top_line_shows_the_ai_status_and_keeps_the_old_reports(window, status, text, warning):
    screen = _show(window, _round(12))
    assert _text(screen, "reportAiStatus") is None
    window.apply(AiStatusView(status, None))
    top = screen.findChild(QLabel, "reportAiStatus")
    assert not top.isHidden()
    assert top.text() == text
    assert (top.property("tone") == "warning") is warning
    assert _lines(screen, "reportDecision")[1] == "최종 판단: 오를 것"  # 지난 보고서는 그대로


def test_the_top_line_disappears_when_the_ai_is_on(window):
    screen = _open(window)
    window.apply(AiStatusView(AiStatus.NOT_FOUND, None))
    assert _text(screen, "reportAiStatus") is not None
    window.apply(AiStatusView(AiStatus.ON, "2.1.314"))
    assert _text(screen, "reportAiStatus") is None


# --- 배치 ---


def test_the_left_and_right_sides_sit_side_by_side_and_only_the_right_scrolls(window):
    window.resize(1024, 640)
    screen = _show(window, _round(12))
    QApplication.processEvents()
    coin = screen.findChild(QListWidget, "reportCoin")
    rounds = screen.findChild(QListWidget, "reportRounds")
    scroll = screen.findChild(QScrollArea, "reportDetailScroll")
    left = coin.mapTo(screen, coin.rect().topLeft())
    right = scroll.mapTo(screen, scroll.rect().topLeft())
    assert left.x() + coin.width() <= right.x()  # 나란히
    assert abs(left.y() - right.y()) < 200  # 같은 높이 쪽에서 시작한다
    assert rounds.parentWidget().width() < scroll.width() + 300
    assert scroll.widgetResizable()


# --- 다른 화면에서 열기 ---


def test_open_from_the_status_report_button_chooses_that_coin_and_asks_for_it(window, actions):
    window.apply(SettingsView(COINS, 30, True, candle_unit=None))
    status = screen_widget(window, ScreenId.STATUS)
    select_screen(window, ScreenId.STATUS)
    button = status.findChild(QPushButton, "statusCoinReport_KRW-BBB")
    assert button is not None
    assert button.text() == "보고서 보기"
    assert not button.isHidden()
    button.click()
    assert current_screen(window) is ScreenId.REPORTS
    coin = screen_widget(window, ScreenId.REPORTS).findChild(QListWidget, "reportCoin")
    assert coin.currentRow() == 1
    assert actions.reports == ["KRW-BBB"]
    window.apply(_listing("KRW-BBB", _round(4, report_id=40), _round(3, report_id=30)))
    assert actions.report == [40]  # 마지막 보고서(목록의 맨 위)를 연다


def test_the_report_button_is_always_there_whatever_the_coin_state(window):
    window.apply(SettingsView(COINS, 30, True))
    status = screen_widget(window, ScreenId.STATUS)
    for market in COINS:
        assert not status.findChild(QPushButton, f"statusCoinReport_{market}").isHidden()
