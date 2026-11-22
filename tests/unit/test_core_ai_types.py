"""2단계 공용 타입(M2 명세 3.4, 1번): AI의 값, 화면이 받는 새 값, 경계, 글, 동작의 서명.
값은 모두 만든 값이다."""

import dataclasses
import inspect
import typing
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import coin_report_trader.core as core
from coin_report_trader.app.actions import AppActions
from coin_report_trader.app.data_thread import DataWorker
from coin_report_trader.app.services import Services
from coin_report_trader.core import (
    AI_PARTS,
    AI_SLOT_TITLES,
    CLAUDE_MODELS,
    CLAUDE_VERSION_LIMIT,
    CLAUDE_VERSION_MIN,
    EFFORT_TITLES,
    REPORT_LIST_LIMIT,
    SAVE_TARGET_REPORT,
    SESSION_TIMEOUT_SECONDS,
    AiChoice,
    AiRunner,
    AiSlot,
    AiStatus,
    AiStatusView,
    AlertView,
    Belief,
    CandleJudgment,
    ChecklistItem,
    ClaudeCheck,
    ClaudeState,
    ComparedItem,
    Direction,
    Effort,
    EvidenceItem,
    FailureKind,
    LastResult,
    LoadState,
    ModelTokens,
    PriceSource,
    ReportListView,
    ReportState,
    ReportView,
    RoundItem,
    RoundTrigger,
    SearchJudgment,
    SessionOutcome,
    SessionRequest,
    SessionResult,
    SettingsView,
    SkipReason,
    SourceItem,
    TokenUsage,
    UiActions,
    UiUpdate,
    Verdict,
    WebhookView,
    texts,
)

NOW = datetime(2026, 1, 5, 3, 0, tzinfo=UTC)


def test_the_slots_follow_the_screen_order_and_have_titles() -> None:
    assert [(slot.name, slot.value) for slot in AiSlot] == [
        ("SEARCH", "search"),
        ("CANDLE", "candle"),
        ("FINAL", "final"),
        ("SESSION", "session"),
    ]
    assert [AI_SLOT_TITLES[slot] for slot in AiSlot] == [
        "검색 판별",
        "봉 판별",
        "최종 예측",
        "코인 세션",
    ]
    assert AI_PARTS == (AiSlot.SEARCH, AiSlot.CANDLE, AiSlot.FINAL)  # 세 부분


def test_models_and_efforts() -> None:
    assert CLAUDE_MODELS == ("fable", "opus", "sonnet", "haiku")
    assert [(e.name, e.value) for e in Effort] == [
        ("LOW", "low"),
        ("MEDIUM", "medium"),
        ("HIGH", "high"),
        ("XHIGH", "xhigh"),
        ("MAX", "max"),
    ]
    assert [EFFORT_TITLES[e] for e in Effort] == ["low", "medium", "high", "xhigh", "max"]


def test_a_choice_has_no_default_and_is_frozen() -> None:
    choice = AiChoice(AiSlot.FINAL, "opus", Effort.HIGH)
    assert (choice.slot, choice.model, choice.effort) == (AiSlot.FINAL, "opus", Effort.HIGH)
    fields = dataclasses.fields(AiChoice)
    assert [f.name for f in fields] == ["slot", "model", "effort"]
    assert all(f.default is dataclasses.MISSING for f in fields)  # 미리 골라 둔 값은 없다
    with pytest.raises(dataclasses.FrozenInstanceError):
        choice.model = "haiku"  # type: ignore[misc]


def test_the_judgment_and_result_words_are_the_csv_and_screen_text() -> None:
    assert [j.value for j in SearchJudgment] == ["긍정", "부정"]
    assert [j.value for j in CandleJudgment] == ["오름", "내림"]
    assert [d.value for d in Direction] == ["오를 것", "내릴 것"]  # '보류'는 없다
    assert [v.value for v in Verdict] == ["맞음", "틀림", "가릴 수 없음"]
    assert [b.value for b in Belief] == ["높음", "보통", "낮음"]
    assert [f.value for f in FailureKind] == [
        "한도 초과",
        "로그인 만료",
        "형식 오류",
        "시간 초과",
        "검색 실패",
    ]
    assert [(s.name, s.value) for s in ReportState] == [
        ("RUNNING", "running"),
        ("OK", "ok"),
        ("FAILED", "failed"),
        ("SKIPPED", "skipped"),
        ("INTERRUPTED", "interrupted"),
        ("ERROR", "error"),
        ("DISCARDED", "discarded"),
    ]
    assert [r.value for r in SkipReason] == ["봉 통계를 만들지 못함", "현재가를 읽지 못함"]
    assert [(t.name, t.value) for t in RoundTrigger] == [("BAR_CLOSE", "bar_close")]
    assert [(s.name, s.value) for s in AiStatus] == [
        ("ON", "on"),
        ("NOT_CHOSEN", "not_chosen"),
        ("NOT_FOUND", "not_found"),
        ("VERSION_OFF", "version_off"),
        ("ERROR_OFF", "error_off"),
    ]


def test_the_constants() -> None:
    assert SESSION_TIMEOUT_SECONDS == 900
    assert CLAUDE_VERSION_MIN == (2, 1, 314)
    assert CLAUDE_VERSION_LIMIT == (2, 2, 0)
    assert REPORT_LIST_LIMIT == 200


def test_token_values_are_frozen() -> None:
    usage = TokenUsage(
        input_tokens=1, output_tokens=2, cache_read_tokens=3, cache_creation_tokens=4
    )
    assert [f.name for f in dataclasses.fields(TokenUsage)] == [
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
    ]
    assert ModelTokens("claude-test-model", usage).usage is usage
    with pytest.raises(dataclasses.FrozenInstanceError):
        usage.input_tokens = 5  # type: ignore[misc]


def test_the_new_views_have_the_fields_of_the_spec_in_order() -> None:
    def names(cls: type) -> list[str]:
        return [f.name for f in dataclasses.fields(cls)]

    assert names(AiStatusView) == ["status", "version", "as_of"]
    assert names(RoundItem) == [
        "report_id", "round_number", "close_utc", "state", "direction", "failure",
    ]  # fmt: skip
    assert names(ReportListView) == ["market", "items", "state"]
    assert names(EvidenceItem) == ["part", "seq", "text"]
    assert names(ComparedItem) == ["part", "seq", "text", "verdict"]
    assert names(ChecklistItem) == ["seq", "text", "belief", "compared", "hits"]
    assert names(SourceItem) == ["seq", "title", "url"]
    assert names(LastResult) == ["round_number", "predicted_price", "actual_price", "diff", "hit"]
    assert names(ReportView) == [
        "report_id", "market", "round_number", "close_utc", "state", "failure", "base_price",
        "search_judgment", "candle_judgment", "direction", "predicted_price", "predicted_reason",
        "buy_price", "sell_price", "result_text", "last_result", "compared", "checklist",
        "evidence", "sources", "tokens", "elapsed_report_seconds", "as_of", "order_summary",
        "no_order_reason", "elapsed_order_seconds",
    ]  # fmt: skip
    assert AiStatusView(AiStatus.ON, "2.1.314").as_of is None
    assert ReportView.__dataclass_fields__["as_of"].default is None


def test_the_old_views_get_their_new_fields_last_with_defaults() -> None:
    assert [f.name for f in dataclasses.fields(SettingsView)][-8:-5] == [
        "candle_unit",
        "candle_count",
        "ai_choices",
    ]
    assert SettingsView().ai_choices == ()
    assert [f.name for f in dataclasses.fields(AlertView)][-1] == "report_id"
    alert = AlertView(1, NOW, core.Mode.PAPER, core.AlertCategory.ERROR, "A-1", None, "글")
    assert alert.report_id is None
    assert [f.name for f in dataclasses.fields(WebhookView)][-1] == "unreadable"
    assert WebhookView(core.AlertCategory.ERROR, False).unreadable is False
    assert SAVE_TARGET_REPORT == "report"


def test_the_new_views_are_screen_updates() -> None:
    args = set(typing.get_args(UiUpdate))
    assert {AiStatusView, ReportListView, ReportView} <= args
    item = RoundItem(1, 3, NOW, ReportState.OK, Direction.UP, None)
    assert ReportListView("KRW-AAA", (item,), LoadState.READY).items == (item,)


def test_the_ports_have_the_shape_of_the_spec() -> None:
    class Price:
        def get_price(self, market: str) -> Decimal:
            return Decimal(1)

    class Runner:
        def check(self) -> ClaudeCheck:
            return ClaudeCheck(ClaudeState.READY, "2.1.314")

        def run(self, request: SessionRequest) -> SessionResult:
            return SessionResult(SessionOutcome.FAILED, {}, None, (), None, {})

        def stop_all(self) -> None: ...

    assert isinstance(Price(), PriceSource)
    assert not isinstance(object(), PriceSource)
    assert isinstance(Runner(), AiRunner)
    assert not isinstance(Price(), AiRunner)
    assert [s.name for s in ClaudeState] == [
        "READY",
        "NOT_FOUND",
        "VERSION_UNREADABLE",
        "VERSION_OUT_OF_RANGE",
        "LOGGED_OUT",
    ]
    assert [s.name for s in SessionOutcome] == ["ANSWERED", "TIMEOUT", "LIMIT", "FAILED"]
    assert [f.name for f in dataclasses.fields(ClaudeCheck)] == ["state", "version"]
    assert [f.name for f in dataclasses.fields(SessionRequest)] == [
        "key", "market", "files", "choices", "timeout_seconds",
    ]  # fmt: skip
    assert [f.name for f in dataclasses.fields(SessionResult)] == [
        "outcome", "answers", "tokens", "model_tokens", "detail", "tools",
    ]  # fmt: skip


def test_the_quote_source_is_not_changed_by_the_price_source() -> None:
    assert not hasattr(core.QuoteSource, "get_price")  # 따로 둔다(M2 명세 3.4)


def test_the_ui_actions_get_two_loads_and_the_ai_choices() -> None:
    names = {name for name in dir(UiActions) if not name.startswith("_")}
    assert {"load_reports", "load_report"} <= names
    assert list(inspect.signature(UiActions.load_reports).parameters) == ["self", "market", "mode"]
    assert list(inspect.signature(UiActions.load_report).parameters) == ["self", "report_id"]
    parameter = inspect.signature(UiActions.save_settings).parameters["ai_choices"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is None


def test_the_app_actions_and_the_data_worker_follow_the_port(tmp_path: Path) -> None:
    for owner in (AppActions, DataWorker):
        for name in ("save_settings", "load_reports", "load_report"):
            mine = inspect.signature(getattr(owner, name))
            port = inspect.signature(getattr(UiActions, name))
            assert list(mine.parameters) == list(port.parameters), (owner, name)
            assert {n: p.default for n, p in mine.parameters.items()} == {
                n: p.default for n, p in port.parameters.items()
            }, (owner, name)


class Spy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def save_settings(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("save_settings", args, kwargs))

    def load_reports(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("load_reports", args, kwargs))

    def load_report(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("load_report", args, kwargs))


def test_the_actions_hand_the_new_calls_to_the_data_thread() -> None:
    data = Spy()
    actions = AppActions(data, Spy())  # type: ignore[arg-type]
    choices = (AiChoice(AiSlot.SEARCH, "haiku", Effort.LOW),)
    actions.save_settings(("KRW-AAA",), 7, first_setup=False, ai_choices=choices)
    actions.load_reports("KRW-AAA")
    actions.load_report(12)
    assert data.calls == [
        (
            "save_settings",
            (("KRW-AAA",), 7),
            {
                "first_setup": False,
                "candle_unit": None,
                "candle_count": None,
                "ai_choices": choices,
                "autostart": None,
                "trade": None,
                "accept_below_stop": (),
                "live": None,
            },
        ),
        ("load_reports", ("KRW-AAA", None), {}),
        ("load_report", (12,), {}),
    ]


def test_the_data_thread_queues_the_new_calls(tmp_path: Path) -> None:
    from coin_report_trader.core import SystemClock

    worker = DataWorker(
        database_file=tmp_path / "app.db",
        logs_dir=tmp_path,
        clock=SystemClock(),
        quote=None,
        sink=lambda update: None,
    )
    choices = (AiChoice(AiSlot.SEARCH, "haiku", Effort.LOW),)
    worker.save_settings(("KRW-AAA",), 7, first_setup=False, ai_choices=choices)
    worker.load_reports("KRW-AAA")
    worker.load_report(3)
    saved, reports, report = list(worker._queue)  # 15번부터 세 동작 모두 자료 스레드의 큐에 든다
    assert (saved.candle_unit, saved.candle_count) == (None, None)
    assert saved.ai_choices == choices
    assert (reports.market, report.report_id) == ("KRW-AAA", 3)


def test_the_services_get_ai_last_and_none_by_default() -> None:
    assert [f.name for f in dataclasses.fields(Services)] == [
        "quote", "webhook", "vault", "clock", "ai", "autostart", "book", "min_order",
        "exchange_transport",
    ]  # fmt: skip
    assert Services().ai is None


def test_the_new_texts_follow_the_spec() -> None:
    assert texts.STAGE_PRICE == "현재가 읽기"
    assert texts.STAGE_REPORT == "AI 보고서"
    assert texts.STAGE_REPORT_SAVE == "보고서 저장"
    assert texts.STAGES[-11:-8] == ("현재가 읽기", "AI 보고서", "보고서 저장")
    assert [
        texts.CODE_PRICE_FAILED,
        texts.CODE_REPORT_FAILED,
        texts.CODE_LOGGED_OUT,
        texts.CODE_CLAUDE_OFF,
        texts.CODE_REPORT_DONE,
    ] == ["A-2", "A-17", "A-18", "A-19", "A-26"]
    assert texts.ERROR_CLAUDE_NOT_FOUND == "Claude Code를 찾지 못함"
    assert texts.ERROR_VERSION_UNREADABLE == "버전을 읽지 못함"
    assert texts.error_version_out_of_range("2.2.1") == "버전 범위 밖(2.2.1)"
    assert texts.AI_MARK_TEXT == "AI가 쓴 글입니다. 틀릴 수 있습니다."
    assert texts.REPORT_UNREADABLE == "보고서를 읽지 못했습니다."


def test_the_new_names_are_exported_from_core() -> None:
    for name in (
        "AI_PARTS", "AI_SLOT_TITLES", "CLAUDE_MODELS", "CLAUDE_VERSION_LIMIT",
        "CLAUDE_VERSION_MIN", "EFFORT_TITLES", "REPORT_LIST_LIMIT", "SAVE_TARGET_REPORT",
        "SESSION_TIMEOUT_SECONDS", "AiChoice", "AiRunner", "AiSlot", "AiStatus", "AiStatusView",
        "Belief", "CandleJudgment", "ChecklistItem", "ClaudeCheck", "ClaudeState", "ComparedItem",
        "Direction", "Effort", "EvidenceItem", "FailureKind", "LastResult", "ModelTokens",
        "PriceSource", "ReportListView", "ReportState", "ReportView", "RoundItem", "RoundTrigger",
        "SearchJudgment", "SessionOutcome", "SessionRequest", "SessionResult", "SkipReason",
        "SourceItem", "TokenUsage", "Verdict",
    ):  # fmt: skip
        assert name in core.__all__, name
        assert hasattr(core, name), name
    assert len(core.__all__) == len(set(core.__all__))
