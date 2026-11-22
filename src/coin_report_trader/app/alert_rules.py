"""알림 만들기(M1 명세 3.9): 글, 되풀이를 세는 상태. 저장은 자료 스레드가 한다.

되풀이를 세는 상태(A-1·A-31·A-29의 성공 전, A-30의 60분)는 메모리에만 두고 한 실행 안에서만
센다. 다시 켜면 처음부터 센다.
"""

from dataclasses import dataclass
from typing import Final

from coin_report_trader.core import (
    CLAUDE_VERSION_LIMIT,
    CLAUDE_VERSION_MIN,
    AlertCategory,
    AlertDetail,
    Direction,
)
from coin_report_trader.core.texts import (
    AFTER_MARKETS_FAILED,
    AFTER_SAVE_FAILED,
    AFTER_UNEXPECTED,
    CODE_CANDLES_FAILED,
    CODE_CLAUDE_OFF,
    CODE_LOGGED_OUT,
    CODE_MARKETS_FAILED,
    CODE_PRICE_FAILED,
    CODE_REPORT_DONE,
    CODE_REPORT_FAILED,
    CODE_SAVE_FAILED,
    CODE_UNEXPECTED,
    ERROR_CLAUDE_NOT_FOUND,
    ERROR_STORAGE,
    ERROR_VERSION_UNREADABLE,
    STAGE_MARKETS,
    STAGE_PRICE,
    STAGE_REPORT,
    STAGE_REPORT_SAVE,
    text_candles_failed,
    text_markets_failed,
    text_save_failed,
    text_unexpected,
)

ALERT_WRITE_INSERT: Final = "insert"
"""알림 한 줄을 표에 쓰기."""
ALERT_WRITE_SEEN: Final = "seen"
"""보았다는 표시를 쓰기(`mark_seen`)."""
ALERT_WRITE_STATE: Final = "state"
"""`discord_state`를 쓰기(보낸 결과, 켤 때 남은 `"not_sent"` 바꾸기)."""

A30_QUIET_SECONDS: Final = 3600.0
"""같은 단계에 같은 예외 종류의 A-30은 이 초 안에 다시 만들지 않는다."""


@dataclass(frozen=True)
class AlertSpec:
    """만들 알림 하나(생긴 때를 뺀 것)."""

    category: AlertCategory
    code: str
    coin: str | None
    text: str
    stage: str
    error_name: str
    content: str
    after: str

    @property
    def detail(self) -> AlertDetail | None:
        """보고서 알림(A-26)은 오류 알림이 아니므로 자세한 글이 없다(M2 명세 3.11)."""
        if self.category is AlertCategory.REPORT:
            return None
        return AlertDetail(self.stage, self.coin, self.error_name, self.content, self.after)


def candles_failed(coin: str, stage: str, error_name: str, content: str, after: str) -> AlertSpec:
    """A-1(오류 알림)."""
    return AlertSpec(
        AlertCategory.ERROR, CODE_CANDLES_FAILED, coin, text_candles_failed(error_name),
        stage, error_name, content, after,
    )  # fmt: skip


def save_failed(stage: str, coin: str | None, content: str) -> AlertSpec:
    """A-29(오류 알림). 오류 이름은 늘 "DB 오류"다."""
    return AlertSpec(
        AlertCategory.ERROR, CODE_SAVE_FAILED, coin, text_save_failed(stage, ERROR_STORAGE),
        stage, ERROR_STORAGE, content, AFTER_SAVE_FAILED[stage],
    )  # fmt: skip


def vault_failed(stage: str, error_name: str, content: str) -> AlertSpec:
    """A-29(오류 알림): 보관소에 주소를 저장하지 못함, 지우지 못함. 코인은 없고 주소는 글에 없다.

    오류 이름은 보관소의 것("보관소를 쓸 수 없음", "보관소 오류")이다.
    """
    return AlertSpec(
        AlertCategory.ERROR, CODE_SAVE_FAILED, None, text_save_failed(stage, error_name),
        stage, error_name, content, AFTER_SAVE_FAILED[stage],
    )  # fmt: skip


def unexpected(
    stage: str, coin: str | None, error_name: str, content: str, after: str = AFTER_UNEXPECTED
) -> AlertSpec:
    """A-30(기타 알림). AI 보고서의 일이면 `after`는 AI를 껐다는 글이다(M2 명세 3.11)."""
    return AlertSpec(
        AlertCategory.OTHER, CODE_UNEXPECTED, coin, text_unexpected(error_name),
        stage, error_name, content, after,
    )  # fmt: skip


def _version_text(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


AFTER_CLAUDE_OFF: Final = "AI를 껐습니다. 범위 안의 버전이 되면 다음 회차부터 다시 돕니다."
AFTER_AI_OFF_ERROR: Final = AFTER_SAVE_FAILED[STAGE_REPORT_SAVE]


def price_failed(coin: str, error_name: str, next_text: str) -> AlertSpec:
    """A-2(오류 알림): 현재가를 읽지 못해 그 코인의 회차를 쉬었다(M2 명세 3.11)."""
    return AlertSpec(
        AlertCategory.ERROR, CODE_PRICE_FAILED, coin,
        "현재가를 읽지 못해 이번 회차를 쉬었습니다.", STAGE_PRICE, error_name,
        "AI 보고서를 만들기 전에 현재가를 읽지 못했습니다.",
        "이번 회차에 이 코인의 AI를 부르지 않았습니다."
        f" 다음 회차({next_text})에 다시 합니다.",
    )  # fmt: skip


def report_failed(coin: str, kind: str, reason: str, next_text: str) -> AlertSpec:
    """A-17(오류 알림): 보고서가 `FAILED`(로그인 만료 말고). `reason`은 정한 까닭의 글이다."""
    return AlertSpec(
        AlertCategory.ERROR, CODE_REPORT_FAILED, coin,
        f"AI 보고서를 받지 못했습니다({kind}).", STAGE_REPORT, kind, reason,
        "이번 회차에 이 코인의 보고서를 만들지 않았습니다."
        f" 다음 회차({next_text})에 다시 부릅니다.",
    )  # fmt: skip


def logged_out() -> AlertSpec:
    """A-18(오류 알림, 코인 없음): 로그인되지 않음."""
    return AlertSpec(
        AlertCategory.ERROR, CODE_LOGGED_OUT, None,
        "Claude 로그인이 만료되어 AI 보고서를 받지 못했습니다. 다시 로그인하세요.",
        STAGE_REPORT, "로그인 만료", "claude auth status가 로그인되지 않았다고 알렸습니다.",
        "이번 회차에 AI 보고서를 만들지 않았습니다. Claude Code에 다시 로그인하면 다음 회차부터"
        " 다시 부릅니다.",
    )  # fmt: skip


def claude_off(error_name: str) -> AlertSpec:
    """A-19(오류 알림, 코인 없음): 실행 파일을 찾지 못함, 버전을 읽지 못함, 버전이 범위 밖."""
    if error_name in (ERROR_CLAUDE_NOT_FOUND, ERROR_VERSION_UNREADABLE):
        text = "Claude Code를 찾지 못했거나 버전을 읽지 못해 AI를 껐습니다."
        content = "Claude Code를 설치하고 로그인했는지 확인하세요."
    else:
        text = "Claude Code 버전이 시험한 범위 밖이라 AI를 껐습니다."
        content = (
            f"시험한 범위: {_version_text(CLAUDE_VERSION_MIN)} 이상"
            f" {_version_text(CLAUDE_VERSION_LIMIT)} 미만"
        )
    return AlertSpec(
        AlertCategory.ERROR, CODE_CLAUDE_OFF, None, text, STAGE_REPORT, error_name, content,
        AFTER_CLAUDE_OFF,
    )  # fmt: skip


def report_done(coin: str, round_number: int, direction: Direction) -> AlertSpec:
    """A-26(보고서 알림): 보고서가 `OK`. 자세한 글이 없다."""
    return AlertSpec(
        AlertCategory.REPORT, CODE_REPORT_DONE, coin,
        f"회차 {round_number}의 보고서를 받았습니다. 최종 판단: {Direction(direction).value}.",
        "", "", "", "",
    )  # fmt: skip


def markets_failed(error_name: str, content: str) -> AlertSpec:
    """A-31(오류 알림). 코인은 없다."""
    return AlertSpec(
        AlertCategory.ERROR, CODE_MARKETS_FAILED, None, text_markets_failed(error_name),
        STAGE_MARKETS, error_name, content, AFTER_MARKETS_FAILED,
    )  # fmt: skip


class RepeatRules:
    """되풀이(3.9). `want_*`는 만들어도 되면 참을 돌려주고 그 알림을 센다."""

    def __init__(self) -> None:
        self._a1: dict[str, set[str]] = {}
        self._a29: set[tuple[str, str | None]] = set()
        self._a29_webhook: set[tuple[str, AlertCategory]] = set()
        self._alert_save: set[str] = set()
        self._a31: set[str] = set()
        self._a30: dict[tuple[str, str | None, str], float] = {}
        self._a2: dict[str, set[str]] = {}
        self._a17: dict[str, set[str]] = {}
        self._a18 = False
        self._a19 = False

    def want_a1(self, coin: str, error_name: str) -> bool:
        """같은 코인에 같은 오류 이름은 그 코인의 받기가 성공하기 전에는 다시 만들지 않는다."""
        names = self._a1.setdefault(coin, set())
        if error_name in names:
            return False
        names.add(error_name)
        return True

    def a1_succeeded(self, coin: str) -> None:
        """받기, 저장, 계산이 끝나고 다섯 기간 가운데 하나 이상에 값이 났다."""
        self._a1.pop(coin, None)

    def want_a29(self, stage: str, coin: str | None) -> bool:
        """같은 단계(코인마다)는 그 저장이 한 번 성공하기 전에는 다시 만들지 않는다."""
        key = (stage, coin)
        if key in self._a29:
            return False
        self._a29.add(key)
        return True

    def a29_succeeded(self, stage: str, coin: str | None) -> None:
        self._a29.discard((stage, coin))

    def want_alert_save(self, kind: str) -> bool:
        """A-29("알림 저장")는 실패한 쓰기의 종류(`ALERT_WRITE_*`)마다 따로 센다. 같은 종류의
        쓰기가 한 번 성공하기 전에는 다시 만들지 않는다. 다른 종류의 쓰기(그 A-29 자신을 표에 쓴
        것 포함)의 성공은 풀지 않는다."""
        if kind in self._alert_save:
            return False
        self._alert_save.add(kind)
        return True

    def alert_save_succeeded(self, kind: str) -> None:
        self._alert_save.discard(kind)

    def want_a29_webhook(self, stage: str, category: AlertCategory) -> bool:
        """주소 저장·지우기의 A-29는 분류마다 따로 센다. 그 저장(지우기)이 성공하기 전에는 다시
        만들지 않는다."""
        key = (stage, AlertCategory(category))
        if key in self._a29_webhook:
            return False
        self._a29_webhook.add(key)
        return True

    def a29_webhook_succeeded(self, stage: str, category: AlertCategory) -> None:
        self._a29_webhook.discard((stage, AlertCategory(category)))

    def want_a31(self, error_name: str) -> bool:
        """같은 오류 이름은 코인 목록 받기가 한 번 성공하기 전에는 다시 만들지 않는다."""
        if error_name in self._a31:
            return False
        self._a31.add(error_name)
        return True

    def a31_succeeded(self) -> None:
        self._a31.clear()

    def want_a30(self, stage: str, coin: str | None, kind: str, monotonic: float) -> bool:
        """같은 단계(코인마다)에 같은 예외 종류는 60분 안에 다시 만들지 않는다."""
        key = (stage, coin, kind)
        last = self._a30.get(key)
        if last is not None and monotonic - last < A30_QUIET_SECONDS:
            return False
        self._a30[key] = monotonic
        return True

    def want_a2(self, coin: str, error_name: str) -> bool:
        """같은 코인, 같은 오류 이름은 그 코인의 현재가 읽기가 한 번 성공하기 전에는 다시 만들지
        않는다(M2 명세 3.11)."""
        names = self._a2.setdefault(coin, set())
        if error_name in names:
            return False
        names.add(error_name)
        return True

    def a2_succeeded(self, coin: str) -> None:
        self._a2.pop(coin, None)

    def want_a17(self, coin: str, kind: str) -> bool:
        """같은 코인, 같은 실패 종류는 그 코인의 보고서가 한 번 `OK`가 되기 전에는 다시 만들지
        않는다."""
        kinds = self._a17.setdefault(coin, set())
        if kind in kinds:
            return False
        kinds.add(kind)
        return True

    def want_a18(self) -> bool:
        """어느 코인이든 보고서가 한 번 `OK`가 되기 전에는 다시 만들지 않는다."""
        if self._a18:
            return False
        self._a18 = True
        return True

    def report_ok(self, coin: str) -> None:
        """그 코인의 보고서가 `OK`가 됐다: 그 코인의 A-17과 모든 코인의 A-18의 되풀이를 푼다."""
        self._a17.pop(coin, None)
        self._a18 = False

    def want_a19(self) -> bool:
        """`AiStatus`가 `ON`으로 돌아오기 전에는 다시 만들지 않는다."""
        if self._a19:
            return False
        self._a19 = True
        return True

    def ai_on(self) -> None:
        self._a19 = False
