"""시계와 한국 시간. 운영체제의 시간대 설정을 읽지 않는다."""

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


class SystemClock:
    """진짜 시계."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)
