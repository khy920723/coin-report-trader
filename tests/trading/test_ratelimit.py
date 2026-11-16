"""요청 한도 장치(`RequestLimiter`)의 시험. 가짜 시계로 기다리지 않고 돈다."""

import threading

from fakes.clock import FakeClock

from coin_report_trader.trading.quote import RequestLimiter


def sent_times(clock: FakeClock, limiter: RequestLimiter, n: int) -> list[float]:
    times = []
    for _ in range(n):
        times.append(limiter.acquire())
    return times


def test_five_requests_go_at_once_and_the_sixth_waits_past_one_second() -> None:
    clock = FakeClock()
    limiter = RequestLimiter(clock)
    times = sent_times(clock, limiter, 6)
    assert times[:5] == [times[0]] * 5
    assert times[5] - times[0] > 1.0
    assert len(clock.sleeps) == 1


def test_exactly_one_second_later_is_not_enough() -> None:
    clock = FakeClock()
    limiter = RequestLimiter(clock)
    first = sent_times(clock, limiter, 5)[0]
    clock.advance(1.0)
    assert limiter.acquire() - first > 1.0
    assert clock.sleeps  # 꼭 1초 뒤에는 기다렸다


def test_no_wait_when_the_window_has_passed() -> None:
    clock = FakeClock()
    limiter = RequestLimiter(clock)
    sent_times(clock, limiter, 5)
    clock.advance(1.5)
    limiter.acquire()
    assert clock.sleeps == []


def test_spread_requests_never_wait() -> None:
    clock = FakeClock()
    limiter = RequestLimiter(clock)
    for _ in range(20):
        limiter.acquire()
        clock.advance(0.25)
    assert clock.sleeps == []


def test_any_window_of_one_second_has_at_most_five() -> None:
    clock = FakeClock()
    limiter = RequestLimiter(clock)
    times = []
    for i in range(40):
        times.append(limiter.acquire())
        clock.advance((i % 4) * 0.05)
    for t in times:
        assert sum(1 for u in times if t <= u <= t + 1.0) <= 5


class EarlyClock(FakeClock):
    """`sleep`이 부른 초보다 조금 일찍 깨는 시계(실제 시계에서 생길 수 있는 일)."""

    def sleep(self, seconds: float) -> None:
        super().sleep(seconds / 2)


def test_waking_early_is_checked_again() -> None:
    clock = EarlyClock()
    limiter = RequestLimiter(clock)
    times = sent_times(clock, limiter, 6)
    assert times[5] - times[0] > 1.0
    assert len(clock.sleeps) > 1


def test_threads_share_one_limit() -> None:
    clock = FakeClock()
    limiter = RequestLimiter(clock)
    times: list[float] = []
    lock = threading.Lock()

    def work() -> None:
        for _ in range(5):
            sent = limiter.acquire()
            with lock:
                times.append(sent)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    times.sort()
    assert len(times) == 20
    for i in range(len(times) - 5):
        assert times[i + 5] - times[i] > 1.0
