"""2단계 AI 보고서의 공용 타입(M2 명세 3.4). 표준 라이브러리만 쓴다.

값 이름, 필드, 글은 명세 그대로다. 값이 곧 CSV와 화면의 글이다(3.8).
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class AiSlot(StrEnum):
    """AI의 칸. 정의 순서가 화면의 차례다."""

    SEARCH = "search"
    CANDLE = "candle"
    FINAL = "final"
    SESSION = "session"


AI_SLOT_TITLES: Final[dict[AiSlot, str]] = {
    AiSlot.SEARCH: "검색 판별",
    AiSlot.CANDLE: "봉 판별",
    AiSlot.FINAL: "최종 예측",
    AiSlot.SESSION: "코인 세션",
}

AI_PARTS: Final = (AiSlot.SEARCH, AiSlot.CANDLE, AiSlot.FINAL)
"""세 부분(8절)."""

CLAUDE_MODELS: Final = ("fable", "opus", "sonnet", "haiku")
"""고를 수 있는 모델. Claude Code 문서의 모델 별칭이다(3.7.6)."""


class Effort(StrEnum):
    """서브 에이전트의 `effort`가 받는 다섯 값(3.7.6)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


EFFORT_TITLES: Final[dict[Effort, str]] = {
    Effort.LOW: "low",
    Effort.MEDIUM: "medium",
    Effort.HIGH: "high",
    Effort.XHIGH: "xhigh",
    Effort.MAX: "max",
}


@dataclass(frozen=True)
class AiChoice:
    """한 칸의 모델과 노력. 미리 골라 둔 값은 없다(6절, REQ-SETUP-004)."""

    slot: AiSlot
    model: str
    """`CLAUDE_MODELS`의 하나."""
    effort: Effort


class SearchJudgment(StrEnum):
    POSITIVE = "긍정"
    NEGATIVE = "부정"


class CandleJudgment(StrEnum):
    UP = "오름"
    DOWN = "내림"


class Direction(StrEnum):
    """'보류'는 없다(2절)."""

    UP = "오를 것"
    DOWN = "내릴 것"


class Verdict(StrEnum):
    HIT = "맞음"
    MISS = "틀림"
    UNKNOWN = "가릴 수 없음"


class Belief(StrEnum):
    HIGH = "높음"
    MEDIUM = "보통"
    LOW = "낮음"


class FailureKind(StrEnum):
    """8절의 실패 다섯."""

    LIMIT = "한도 초과"
    LOGGED_OUT = "로그인 만료"
    FORMAT = "형식 오류"
    TIMEOUT = "시간 초과"
    SEARCH = "검색 실패"


class ReportState(StrEnum):
    RUNNING = "running"
    """만드는 중."""
    OK = "ok"
    """정상으로 받음."""
    FAILED = "failed"
    """8절의 실패."""
    SKIPPED = "skipped"
    """쉼."""
    INTERRUPTED = "interrupted"
    """끊긴 회차."""
    ERROR = "error"
    """만들거나 저장하다 오류가 나 AI를 끔(3.5.4)."""


class SkipReason(StrEnum):
    NO_STATS = "봉 통계를 만들지 못함"
    NO_PRICE = "현재가를 읽지 못함"


class RoundTrigger(StrEnum):
    """놓친 회차의 값은 4단계가 더한다(REQ-PC-004)."""

    BAR_CLOSE = "bar_close"


class AiStatus(StrEnum):
    ON = "on"
    NOT_CHOSEN = "not_chosen"
    """네 칸을 다 고르지 않음."""
    NOT_FOUND = "not_found"
    """실행 파일을 찾지 못하거나 버전을 읽지 못함."""
    VERSION_OFF = "version_off"
    """버전이 범위 밖."""
    ERROR_OFF = "error_off"
    """3.5.4."""


SESSION_TIMEOUT_SECONDS: Final = 900
"""3.7.4."""
CLAUDE_VERSION_MIN: Final = (2, 1, 314)
CLAUDE_VERSION_LIMIT: Final = (2, 2, 0)
"""3.7.1."""
REPORT_LIST_LIMIT: Final = 200
"""3.12.1."""


@dataclass(frozen=True)
class TokenUsage:
    """세션 전체의 합이다(3.7.5)."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int


@dataclass(frozen=True)
class ModelTokens:
    model: str
    """Claude Code가 적은 모델 이름 그대로."""
    usage: TokenUsage


class ClaudeState(StrEnum):
    """`AiRunner.check()`의 결과(3.4, 3.7.1)."""

    READY = "ready"
    NOT_FOUND = "not_found"
    VERSION_UNREADABLE = "version_unreadable"
    VERSION_OUT_OF_RANGE = "version_out_of_range"
    LOGGED_OUT = "logged_out"


class SessionOutcome(StrEnum):
    """`AiRunner.run()`의 결과(3.4, 3.7.5)."""

    ANSWERED = "answered"
    TIMEOUT = "timeout"
    LIMIT = "limit"
    """구독 사용 한도 초과."""
    FAILED = "failed"
