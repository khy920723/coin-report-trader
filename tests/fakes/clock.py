"""가짜 시계(M1 명세 3.13). `core.Clock`(3.4)의 약속을 지킨다.

- `now()`는 UTC가 붙은 `datetime`, `monotonic()`은 초 단위 `float`이다. 둘 다 시험이 움직일 때만
  바뀐다(실제 시각을 읽지 않는다).
- `sleep(seconds)`는 실제로 기다리지 않고, 두 시각을 `seconds`만큼 함께 옮긴 뒤 바로 돌아온다.
  부른 값은 `sleeps`에 차례로 남는다.
- `advance(seconds)`는 시험이 두 시각을 함께 옮기는 길이다(`sleeps`에는 남지 않는다).
- `set_now(when)`은 벽시계(`now()`)만 그 시각으로 옮긴다. 단조 시각은 그대로다(PC 시계를 바꾼 경우).
- 음수, NaN, 무한대의 초와 시간대가 없는 시각은 `ValueError`다.
- 여러 스레드가 함께 써도 된다(잠금 하나로 지킨다).
"""

import math
import threading
from datetime import UTC, datetime, timedelta

DEFAULT_START = datetime(2026, 1, 5, 3, 0, tzinfo=UTC)
"""처음 시각: 2026-01-05 03:00 UTC(한국 시간 월요일 12:00). 일봉이 바뀌는 09:00 KST가 아니다."""

DEFAULT_MONOTONIC = 1000.0
"""처음 단조 시각. 0이 아니어서 "0부터 센다"고 기대하는 코드를 잡는다."""


def _seconds(value: float, what: str) -> float:
    seconds = float(value)
    if math.isnan(seconds) or math.isinf(seconds) or seconds < 0:
        raise ValueError(f"{what}: 0 이상의 유한한 초여야 한다: {value!r}")
    return seconds


def _utc(when: datetime) -> datetime:
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError(f"시간대가 붙은 시각이어야 한다: {when!r}")
    return when.astimezone(UTC)


class FakeClock:
    """`Clock`의 가짜. `FakeClock(start=DEFAULT_START, *, monotonic_start=DEFAULT_MONOTONIC)`."""

    def __init__(
        self, start: datetime = DEFAULT_START, *, monotonic_start: float = DEFAULT_MONOTONIC
    ) -> None:
        self._lock = threading.Lock()
        self._now = _utc(start)
        self._monotonic = float(monotonic_start)
        if math.isnan(self._monotonic) or math.isinf(self._monotonic):
            raise ValueError(f"monotonic_start: 유한한 초여야 한다: {monotonic_start!r}")
        self.sleeps: list[float] = []
        """`sleep`에 넘어온 초(부른 차례대로)."""

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def monotonic(self) -> float:
        with self._lock:
            return self._monotonic

    def sleep(self, seconds: float) -> None:
        """실제로 기다리지 않는다. 두 시각을 `seconds`만큼 옮기고 `sleeps`에 남긴다."""
        step = _seconds(seconds, "sleep")
        with self._lock:
            self.sleeps.append(step)
            self._move(step)

    def advance(self, seconds: float) -> None:
        """시험이 두 시각을 `seconds`만큼 함께 옮긴다."""
        step = _seconds(seconds, "advance")
        with self._lock:
            self._move(step)

    def set_now(self, when: datetime) -> None:
        """벽시계만 `when`(UTC로 바꾼다)으로 옮긴다. 앞으로도 뒤로도 옮길 수 있다."""
        target = _utc(when)
        with self._lock:
            self._now = target

    def _move(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        self._monotonic += seconds
