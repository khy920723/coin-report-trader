"""가짜 Claude 실행기 `FakeAiRunner`(`AiRunner`. M2 명세 3.15). 값은 모두 만든 값이다.

코인마다 정한 결과를 낸다: 정한 CSV의 `ANSWERED`, `TIMEOUT`, `LIMIT`, `FAILED`, 틀린 머리줄, 정한
`tools`, `check()`의 다섯 상태, `run`이 예외를 냄. 늦은 세션은 시험이 세우는 `threading.Event`를
기다렸다 돌아온다(실제 시간을 재지 않는다). 받은 `SessionRequest`(파일 글 포함)를 기록한다.
"""

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from coin_report_trader.ai.answers import (
    CANDLE_JUDGMENT_COLUMNS,
    FINAL_JUDGMENT_COLUMNS,
    SEARCH_JUDGMENT_COLUMNS,
)
from coin_report_trader.ai.csv_files import ROWS_COLUMNS, write_csv
from coin_report_trader.core import (
    AiSlot,
    ClaudeCheck,
    ClaudeState,
    ModelTokens,
    SessionOutcome,
    SessionRequest,
    SessionResult,
    TokenUsage,
)

WAIT_SAFETY_SECONDS = 30.0
"""`wait` 이벤트를 기다리는 가장 긴 실제 시간(안전 시간. 이벤트를 안 세워도 멈추지 않게)."""

DEFAULT_TOOLS: dict[AiSlot, tuple[str, ...]] = {
    AiSlot.SESSION: ("Agent",),
    AiSlot.SEARCH: ("Read", "WebSearch"),
    AiSlot.CANDLE: ("Read",),
    AiSlot.FINAL: ("Read",),
}
DEFAULT_TOKENS = TokenUsage(1000, 200, 300, 0)


def _row(market: str, kind: str, seq: int, text: str, **cells: str) -> list[str]:
    row = dict.fromkeys(ROWS_COLUMNS, "")
    row.update(market=market, kind=kind, seq=str(seq), text=text, **cells)
    return [row[column] for column in ROWS_COLUMNS]


def good_answers(
    market: str,
    *,
    direction: str = "오를 것",
    predicted: str = "102",
    buy_price: str = "99",
    sell_price: str = "",
    result_text: str = "",
    compared: Sequence[tuple[str, int]] = (),
) -> dict[str, str]:
    """3.8의 정상 답(필드 여섯). `compared`는 지난 판단의 근거 줄마다의 (부분 이름, 번호)이다."""
    final_rows = [
        _row(market, "견준 결과", i, "앞 근거", part=part, ref_seq=str(ref), verdict="맞음")
        for i, (part, ref) in enumerate(compared, start=1)
    ]
    final_rows += [
        _row(market, "검증 목록", 1, "항목", belief="높음", compared="2", hits="1"),
        _row(market, "근거", 1, "최종 근거"),
    ]
    return {
        "search_judgment": write_csv(SEARCH_JUDGMENT_COLUMNS, [[market, "긍정", "성공"]]),
        "search_rows": write_csv(
            ROWS_COLUMNS,
            [
                _row(market, "근거", 1, "검색 근거"),
                _row(market, "출처", 1, "제목", url="https://example.com/a"),
            ],
        ),
        "candle_judgment": write_csv(CANDLE_JUDGMENT_COLUMNS, [[market, "오름"]]),
        "candle_rows": write_csv(ROWS_COLUMNS, [_row(market, "근거", 1, "봉 근거")]),
        "final_rows": write_csv(ROWS_COLUMNS, final_rows),
        "final_judgment": write_csv(
            FINAL_JUDGMENT_COLUMNS,
            [[market, result_text, direction, predicted, "까닭", buy_price, sell_price]],
        ),
    }


def wrong_header_answers(market: str) -> dict[str, str]:
    """검색 판별의 판단 표의 머리줄이 틀린 답."""
    answers = good_answers(market)
    answers["search_judgment"] = write_csv(
        ("market", "judgment", "status"), [[market, "긍정", "성공"]]
    )
    return answers


def answered(
    answers: Mapping[str, str],
    *,
    tokens: TokenUsage | None = DEFAULT_TOKENS,
    tools: Mapping[AiSlot, tuple[str, ...]] | None = None,
) -> SessionResult:
    model = ModelTokens("claude-test-model", tokens) if tokens is not None else None
    return SessionResult(
        SessionOutcome.ANSWERED,
        dict(answers),
        tokens,
        () if model is None else (model,),
        None,
        dict(DEFAULT_TOOLS if tools is None else tools),
    )


def timeout() -> SessionResult:
    return SessionResult(SessionOutcome.TIMEOUT, {}, None, (), None, {})


def limit() -> SessionResult:
    return SessionResult(SessionOutcome.LIMIT, {}, None, (), None, {})


def failed(detail: str = "끝 코드 1") -> SessionResult:
    return SessionResult(SessionOutcome.FAILED, {}, None, (), detail, {})


type Plan = SessionResult | Callable[[SessionRequest], SessionResult]


@dataclass
class _CoinPlan:
    result: Plan | None = None
    raises: BaseException | None = None
    wait: threading.Event | None = None
    started: threading.Event = field(default_factory=threading.Event)


class FakeAiRunner:
    """`AiRunner`. 정하지 않은 코인은 그 코인의 정상 답(`good_answers`)을 낸다."""

    def __init__(
        self, *, state: ClaudeState = ClaudeState.READY, version: str | None = "2.1.314"
    ) -> None:
        self._lock = threading.Lock()
        self._plans: dict[str, _CoinPlan] = {}
        self.state = state
        self.version = version
        self.requests: list[SessionRequest] = []
        self.check_calls = 0
        self.stop_calls = 0

    def set_check(self, state: ClaudeState, version: str | None = "2.1.314") -> None:
        """`check()`가 낼 상태(다섯 가운데 하나)와 버전."""
        self.state = state
        self.version = version

    def plan(
        self,
        market: str,
        result: Plan | None = None,
        *,
        raises: BaseException | None = None,
        wait: threading.Event | None = None,
    ) -> threading.Event:
        """그 코인의 세션이 낼 결과. `raises`면 `run`이 그 예외를 낸다. `wait`가 있으면 그 이벤트가
        서기까지 `run`이 돌아오지 않는다. 돌려주는 이벤트는 그 세션이 시작되면 선다."""
        plan = _CoinPlan(result, raises, wait)
        with self._lock:
            self._plans[market] = plan
        return plan.started

    # --- AiRunner ----------------------------------------------------------------------------

    def check(self) -> ClaudeCheck:
        with self._lock:
            self.check_calls += 1
            return ClaudeCheck(self.state, self.version)

    def run(self, request: SessionRequest) -> SessionResult:
        with self._lock:
            self.requests.append(request)
            plan = self._plans.get(request.market, _CoinPlan())
        plan.started.set()
        if plan.wait is not None:
            plan.wait.wait(WAIT_SAFETY_SECONDS)
        if plan.raises is not None:
            raise plan.raises
        if plan.result is None:
            return answered(good_answers(request.market))
        if callable(plan.result):
            return plan.result(request)
        return plan.result

    def stop_all(self) -> None:
        with self._lock:
            self.stop_calls += 1


def price(value: str) -> Decimal:
    """만든 가격."""
    return Decimal(value)
