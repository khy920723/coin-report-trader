"""가짜 시계(M1 명세 3.13)의 시험: `now`, `monotonic`, `sleep`, `advance`, `set_now`, `wait`."""

import math
import threading
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fakes.clock import DEFAULT_MONOTONIC, DEFAULT_START, FakeClock

from coin_report_trader.core import Clock

SAFETY = 5.0  # 멈추지 않게 하는 실제 안전 시간(초)


def test_defaults_and_the_clock_protocol() -> None:
    clock = FakeClock()
    assert isinstance(clock, Clock)
    assert clock.now() == DEFAULT_START
    assert clock.monotonic() == DEFAULT_MONOTONIC
    assert clock.sleeps == []
    assert clock.waits == []


def test_start_values_and_validation() -> None:
    start = datetime(2026, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=9)))
    clock = FakeClock(start=start, monotonic_start=5.5)
    assert clock.now() == start.astimezone(UTC)
    assert clock.now().tzinfo is UTC
    assert clock.monotonic() == 5.5
    with pytest.raises(ValueError, match="시간대"):
        FakeClock(start=datetime(2026, 1, 1))
    for bad in (math.nan, math.inf):
        with pytest.raises(ValueError, match="monotonic_start"):
            FakeClock(monotonic_start=bad)


def test_time_moves_only_when_the_test_moves_it() -> None:
    clock = FakeClock()
    assert clock.now() == clock.now()
    assert clock.monotonic() == clock.monotonic()
    clock.advance(90)
    assert clock.now() == DEFAULT_START + timedelta(seconds=90)
    assert clock.monotonic() == DEFAULT_MONOTONIC + 90
    assert clock.sleeps == []


def test_sleep_moves_both_times_and_records_without_waiting() -> None:
    clock = FakeClock()
    clock.sleep(2.5)
    clock.sleep(0)
    assert clock.sleeps == [2.5, 0.0]
    assert clock.now() == DEFAULT_START + timedelta(seconds=2.5)
    assert clock.monotonic() == DEFAULT_MONOTONIC + 2.5


def test_set_now_moves_only_the_wall_clock() -> None:
    clock = FakeClock()
    target = datetime(2030, 1, 1, tzinfo=timezone(timedelta(hours=9)))
    clock.set_now(target)
    assert clock.now() == target.astimezone(UTC)
    assert clock.monotonic() == DEFAULT_MONOTONIC
    clock.set_now(DEFAULT_START - timedelta(days=1))  # 뒤로도 옮긴다
    assert clock.now() == DEFAULT_START - timedelta(days=1)
    with pytest.raises(ValueError, match="시간대"):
        clock.set_now(datetime(2026, 1, 1))


@pytest.mark.parametrize("bad", [-1, -0.001, math.nan, math.inf, -math.inf])
def test_bad_seconds_are_refused(bad: float) -> None:
    clock = FakeClock()
    for call in (clock.sleep, clock.advance):
        with pytest.raises(ValueError, match="0 이상"):
            call(bad)
    with pytest.raises(ValueError, match="0 이상"):
        clock.wait(threading.Event(), bad)
    assert clock.sleeps == []
    assert clock.waits == []
    assert clock.now() == DEFAULT_START


# --- wait ----------------------------------------------------------------------------------


def _waiter(
    clock: FakeClock, event: threading.Event, seconds: float, out: list[bool]
) -> threading.Thread:
    def run() -> None:
        out.append(clock.wait(event, seconds))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _until(predicate: object, what: str) -> None:
    assert callable(predicate)
    done = threading.Event()
    for _ in range(int(SAFETY / 0.002)):
        if predicate():
            return
        done.wait(0.002)
    raise AssertionError(f"안전 시간 안에 {what}가 일어나지 않았다")


def test_wait_with_zero_seconds_returns_the_event_state() -> None:
    clock = FakeClock()
    event = threading.Event()
    assert clock.wait(event, 0) is False
    event.set()
    assert clock.wait(event, 0) is True
    assert clock.waits == [0.0, 0.0]
    assert clock.monotonic() == DEFAULT_MONOTONIC


def test_wait_returns_true_at_once_when_the_event_is_already_set() -> None:
    clock = FakeClock()
    event = threading.Event()
    event.set()
    assert clock.wait(event, 3600) is True
    assert clock.monotonic() == DEFAULT_MONOTONIC


def test_wait_blocks_until_the_event_is_set() -> None:
    clock = FakeClock()
    event = threading.Event()
    out: list[bool] = []
    thread = _waiter(clock, event, 3600, out)
    _until(lambda: clock.waits == [3600.0], "wait 부름")
    assert out == []
    assert thread.is_alive()
    event.set()
    thread.join(SAFETY)
    assert out == [True]
    assert clock.monotonic() == DEFAULT_MONOTONIC  # 스스로 시각을 옮기지 않는다


def test_wait_wakes_with_false_when_the_monotonic_time_moves_enough() -> None:
    clock = FakeClock()
    event = threading.Event()
    out: list[bool] = []
    thread = _waiter(clock, event, 60, out)
    _until(lambda: clock.waits == [60.0], "wait 부름")
    clock.advance(59.9)
    thread.join(0.05)
    assert out == []  # 아직 모자란다
    clock.advance(0.1)
    thread.join(SAFETY)
    assert out == [False]


def test_wait_wakes_when_another_thread_sleeps() -> None:
    clock = FakeClock()
    out: list[bool] = []
    thread = _waiter(clock, threading.Event(), 10, out)
    _until(lambda: clock.waits == [10.0], "wait 부름")
    clock.sleep(10)
    thread.join(SAFETY)
    assert out == [False]
    assert clock.sleeps == [10.0]


def test_wait_counts_from_the_call_not_from_the_start() -> None:
    clock = FakeClock()
    clock.advance(100)
    out: list[bool] = []
    thread = _waiter(clock, threading.Event(), 30, out)
    _until(lambda: clock.waits == [30.0], "wait 부름")
    clock.advance(29)
    thread.join(0.05)
    assert out == []
    clock.advance(1)
    thread.join(SAFETY)
    assert out == [False]


def test_the_wall_clock_alone_does_not_wake_wait() -> None:
    clock = FakeClock()
    out: list[bool] = []
    thread = _waiter(clock, threading.Event(), 10, out)
    _until(lambda: clock.waits == [10.0], "wait 부름")
    clock.set_now(DEFAULT_START + timedelta(days=30))
    thread.join(0.05)
    assert out == []
    clock.advance(10)
    thread.join(SAFETY)
    assert out == [False]


def test_wait_accepts_a_huge_finite_number_of_seconds() -> None:
    clock = FakeClock()
    huge = float(threading.TIMEOUT_MAX) * 10
    event = threading.Event()
    out: list[bool] = []
    thread = _waiter(clock, event, huge, out)
    _until(lambda: clock.waits == [huge], "wait 부름")
    event.set()
    thread.join(SAFETY)
    assert out == [True]


def test_waits_record_every_call_in_order() -> None:
    clock = FakeClock()
    event = threading.Event()
    event.set()
    for seconds in (5, 0, 1800.5):
        clock.wait(event, seconds)
    assert clock.waits == [5.0, 0.0, 1800.5]


def test_several_threads_can_use_the_clock_together() -> None:
    clock = FakeClock()
    outcomes: list[bool] = []
    threads = [_waiter(clock, threading.Event(), 5, outcomes) for _ in range(4)]
    _until(lambda: len(clock.waits) == 4, "wait 넷")
    clock.advance(5)
    for thread in threads:
        thread.join(SAFETY)
    assert outcomes == [False] * 4
