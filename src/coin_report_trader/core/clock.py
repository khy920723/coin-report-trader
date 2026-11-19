"""시계와 한국 시간. 운영체제의 시간대 설정을 읽지 않는다."""

import math
import threading
import time
from datetime import UTC, datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

KST = timezone(timedelta(hours=9))
"""한국 시간. Ubuntu와 Windows에서 같은 결과가 나오게 이 값으로만 바꾼다."""


@runtime_checkable
class Clock(Protocol):
    """시각을 읽고 기다리는 길. 시험은 가짜 시계로 시각을 움직인다."""

    def now(self) -> datetime:
        """지금 시각(UTC가 붙은 `datetime`)."""
        ...

    def monotonic(self) -> float:
        """줄어들지 않는 시각(초)."""
        ...

    def sleep(self, seconds: float) -> None:
        """`seconds`초 기다린다."""
        ...

    def wait(self, event: threading.Event, seconds: float) -> bool:
        """`event`가 서거나 이 시계로 `seconds`가 흐를 때까지 기다린다.

        `event`가 섰으면 참, 시간이 다 흘렀으면 거짓이다. `seconds`가 0이면 기다리지 않고
        `event`의 상태를 돌려준다. 음수, NaN, 무한대는 `ValueError`다.
        """
        ...


class SystemClock:
    """진짜 시계."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def wait(self, event: threading.Event, seconds: float) -> bool:
        """실제 시간으로 기다린다. `threading.TIMEOUT_MAX`보다 큰 초도 나누어 끝까지 기다린다."""
        total = float(seconds)
        if math.isnan(total) or math.isinf(total) or total < 0:
            raise ValueError(f"wait: 0 이상의 유한한 초여야 한다: {seconds!r}")
        if total == 0:
            return event.is_set()
        deadline = time.monotonic() + total
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return event.is_set()
            if event.wait(min(remaining, threading.TIMEOUT_MAX)):
                return True
