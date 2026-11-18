"""가짜 디스코드(M1 명세 3.13). `core.WebhookSender`(3.4)의 약속을 진짜와 같게 지킨다.

- `send(url, text)`는 받은 것을 `sent`에 차례로 남기고, 정해 둔 실패가 있으면 `WebhookError`를 낸다.
  실패한 보내기도 남긴다(B16은 시간 초과에서 한 번만 시도한 것을, B25는 성공 하나와 실패 하나의
  확인용 글 둘이 기록된 것을 본다).
  - 기록 `SentMessage(url, text, kind, error_name)`: `kind`는 글의 첫머리가 `TEST_MESSAGE_PREFIX`
    ("[Coin Report Trader] 확인용 글")이면 `TEST`, 아니면 `ALERT`다. `error_name`은 낸 오류의 이름
    (성공이면 `None`). 주소(시험이 만든 글)도 기록하므로 "다른 분류의 주소로 보내지 않는다"(B15)를
    `urls`로 본다.
  - 거르는 도우미: `alerts`(알림만), `tests`(확인용 글만), `texts`, `urls`(받은 차례의 목록).
- 결과 정하기(`script`): 보내기 하나 단위로 정한다(`tests/fakes/quote.py`의 `script`와 같은 꼴).
  - `script(*outcomes, url=None)`이 줄 하나를 더한다. `url`을 주면 그 주소로 가는 보내기만 그 줄을
    쓴다. 결과는 `WebhookError`(그 오류를 낸다)나 `OK`(성공)다. 맞는 줄이 없거나 다 썼으면 성공이다.
  - 예: B16은 `script(WebhookError(ERROR_DISCORD_TIMEOUT))`, B25는 `script(OK, WebhookError(…))`.
  - `fail(error_name, *, retry_after=None, times=None)`: 지금부터 모든 보내기(또는 `times`번)가
    그 오류로 실패한다. `heal()`은 `script`와 `fail`을 모두 푼다.
- 막아 두기: `hold()`를 부르면 그 뒤의 보내기는 `release()`를 부를 때까지 안에서 멈춘다(보내기
  스레드가 막혀도 자료 스레드의 받기가 늦어지지 않는 것을 보려는 길). 멈춰 있는 보내기가 있으면
  `entered`가 선다. 시험이 `release()`를 잊어도 `HOLD_LIMIT`초 뒤에는 `AssertionError`로 풀린다.
  막힌 보내기도 풀린 뒤에 같은 규칙(기록, 정해 둔 실패)으로 처리한다.
- `wait_for(count, timeout)`은 기록이 `count`개 이상이 될 때까지 기다린다(실제 시간, 안전 시간).
- 여러 스레드가 함께 써도 된다(잠금 하나. 보내기는 보내기 스레드가 부른다).
- 값은 `repr`에 두지 않는다. 이 파일은 시험만 import한다. `src/`는 이 폴더를 import하지 않는다.
"""

import threading
from dataclasses import dataclass
from typing import Final

from coin_report_trader.core import WebhookError
from coin_report_trader.core.texts import TEST_MESSAGE_PREFIX

ALERT: Final = "alert"
TEST: Final = "test"
HOLD_LIMIT: Final = 10.0
"""`hold()`로 멈춘 보내기가 `release()`를 기다리는 가장 긴 실제 시간(초)."""


class _Ok:
    """성공하는 결과."""

    def __repr__(self) -> str:
        return "OK"


OK: Final = _Ok()

type Outcome = WebhookError | _Ok


@dataclass(frozen=True)
class SentMessage:
    """받은 보내기 하나의 기록."""

    url: str
    text: str
    kind: str
    """`ALERT`나 `TEST`."""
    error_name: str | None
    """낸 `WebhookError`의 오류 이름. 성공이면 `None`."""


@dataclass
class _Script:
    url: str | None
    outcomes: list[Outcome]

    def matches(self, url: str) -> bool:
        return bool(self.outcomes) and (self.url is None or self.url == url)


@dataclass
class _Always:
    error_name: str
    retry_after: float | None
    remaining: int | None  # `None`이면 `heal()`까지


class FakeDiscord:
    """`WebhookSender`의 가짜(모듈 설명)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._scripts: list[_Script] = []
        self._always: _Always | None = None
        self._gate = threading.Event()
        self._gate.set()
        self.entered = threading.Event()
        """`hold()` 때문에 멈춰 있는 보내기가 있으면 선다."""
        self.sent: list[SentMessage] = []
        """받은 보내기(차례대로. 실패한 것도)."""

    def __repr__(self) -> str:
        return f"FakeDiscord(보내기 {len(self.sent)}번)"

    # --- 시험이 정하는 것 ------------------------------------------------------------------

    def script(self, *outcomes: Outcome, url: str | None = None) -> None:
        for outcome in outcomes:
            if not isinstance(outcome, WebhookError | _Ok):
                raise TypeError("script: 결과는 WebhookError나 OK다")
        with self._lock:
            self._scripts.append(_Script(url, list(outcomes)))

    def fail(
        self, error_name: str, *, retry_after: float | None = None, times: int | None = None
    ) -> None:
        if times is not None and (isinstance(times, bool) or times < 1):
            raise ValueError("times는 1 이상의 정수이거나 None이다")
        with self._lock:
            self._always = _Always(error_name, retry_after, times)

    def heal(self) -> None:
        with self._lock:
            self._scripts.clear()
            self._always = None

    def hold(self) -> None:
        self._gate.clear()

    def release(self) -> None:
        self._gate.set()

    # --- 기록을 보는 도우미 ---------------------------------------------------------------

    @property
    def alerts(self) -> list[SentMessage]:
        with self._lock:
            return [m for m in self.sent if m.kind == ALERT]

    @property
    def tests(self) -> list[SentMessage]:
        with self._lock:
            return [m for m in self.sent if m.kind == TEST]

    @property
    def texts(self) -> list[str]:
        with self._lock:
            return [m.text for m in self.sent]

    @property
    def urls(self) -> list[str]:
        with self._lock:
            return [m.url for m in self.sent]

    def wait_for(self, count: int, timeout: float = 10.0) -> bool:
        waiter = threading.Event()
        for _ in range(max(1, int(timeout / 0.002))):
            with self._lock:
                if len(self.sent) >= count:
                    return True
            waiter.wait(0.002)
        return False

    # --- WebhookSender ---------------------------------------------------------------------

    def send(self, url: str, text: str) -> None:
        if not self._gate.is_set():
            self.entered.set()
            if not self._gate.wait(HOLD_LIMIT):
                raise AssertionError("FakeDiscord: hold()를 풀지 않았다(release()를 잊었다)")
        with self._lock:
            error = self._next_error(url)
            kind = TEST if text.startswith(TEST_MESSAGE_PREFIX) else ALERT
            error_name = None if error is None else error.error_name
            self.sent.append(SentMessage(url, text, kind, error_name))
        if error is not None:
            raise WebhookError(error.error_name, error.retry_after)

    def _next_error(self, url: str) -> WebhookError | None:
        """잠금 안에서 부른다. 이번 보내기의 정해 둔 실패(없으면 `None`)."""
        for line in self._scripts:
            if line.matches(url):
                outcome = line.outcomes.pop(0)
                return outcome if isinstance(outcome, WebhookError) else None
        always = self._always
        if always is None:
            return None
        if always.remaining is not None:
            always.remaining -= 1
            if always.remaining == 0:
                self._always = None
        return WebhookError(always.error_name, always.retry_after)
