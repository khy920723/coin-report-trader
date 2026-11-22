"""업비트 비공개 API의 요청 한도(M5 명세 3.5.3)의 시험: `Remaining-Req`, 429 뒤 다음 초, 418.

가짜 시계로 기다리지 않고 돈다. 값은 모두 만든 숫자다.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fakes.clock import FakeClock
from fakes.upbit import TEST_ACCESS_KEY, TEST_SECRET_KEY, FakeUpbitTransport, Reply

from coin_report_trader.core.texts import ERROR_BLOCKED, ERROR_RATE_LIMIT
from coin_report_trader.trading.upbit import (
    ErrorBranch,
    UpbitClient,
    UpbitError,
    UpbitKeys,
    UpbitLimiter,
    parse_remaining_req,
)

KEYS = UpbitKeys(TEST_ACCESS_KEY, TEST_SECRET_KEY)
M = "KRW-AAA"
START = datetime(2026, 1, 5, 3, 0, 0, 250_000, tzinfo=UTC)
"""벽시계의 초 아래가 0.25인 때. 기다림은 PC 시계의 초 아래와 상관없이 1초 + 여유(0.01초)다."""
PAUSE = 1.01


def setup() -> tuple[UpbitClient, FakeUpbitTransport, FakeClock]:
    clock = FakeClock(START)
    fake = FakeUpbitTransport(clock=clock)
    fake.set_account(krw=Decimal(100_000))
    fake.set_chance(M)
    return UpbitClient(KEYS, fake, clock), fake, clock


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("group=default; min=600; sec=13", ("default", 13)),
        ("group=order;sec=0", ("order", 0)),
        ("GROUP=order; SEC=3", ("order", 3)),
        ("group=default; min=600", ("default", None)),
        ("group=default; sec=x", ("default", None)),
        ("group=default; sec=9999999", ("default", None)),
        ("", (None, None)),
        (None, (None, None)),
        ("garbage", (None, None)),
    ],
)
def test_parse_remaining_req(value: str | None, expected: tuple) -> None:
    assert parse_remaining_req(value) == expected


# --- 처음 한도: 그룹마다 초당 5회 ---------------------------------------------------------------


def test_five_per_second_per_group_and_groups_are_counted_apart() -> None:
    clock = FakeClock(START)
    limiter = UpbitLimiter(clock)
    times = [limiter.acquire("default") for _ in range(6)]
    assert times[:5] == [times[0]] * 5
    assert times[5] - times[0] > 1.0
    # 주문 그룹은 따로 센다: 기다리지 않는다.
    sleeps = len(clock.sleeps)
    limiter.acquire("order")
    assert len(clock.sleeps) == sleeps


def test_unknown_group_is_refused() -> None:
    with pytest.raises(ValueError, match="그룹"):
        UpbitLimiter(FakeClock()).acquire("market")


def test_twelve_requests_never_more_than_five_in_a_second() -> None:
    client, fake, _ = setup()
    for _ in range(12):
        client.accounts()
    times = [r.monotonic for r in fake.requests]
    for i in range(len(times) - 5):
        assert times[i + 5] - times[i] > 1.0


# --- sec 0과 429: 다음 초 ----------------------------------------------------------------------


def test_sec_zero_pauses_that_group_until_the_next_second() -> None:
    client, fake, _ = setup()
    fake.set_remaining("default", 0)
    client.accounts()
    first = fake.requests[0].monotonic
    fake.set_remaining("default", 13)
    client.accounts()
    second = fake.requests[1].monotonic
    assert second - first == pytest.approx(PAUSE)


@pytest.mark.parametrize(
    "wall", [START, START.replace(microsecond=0), START.replace(microsecond=999_000)]
)
def test_the_pause_does_not_depend_on_the_pc_clock(wall: datetime) -> None:
    # PC 시계의 초 아래 자리가 무엇이든 기다림은 같다(PC 시계를 믿지 않는다).
    clock = FakeClock(START)
    clock.set_now(wall)
    limiter = UpbitLimiter(clock)
    limiter.pause_until_next_second("default")
    assert limiter.paused_until("default") == pytest.approx(clock.monotonic() + PAUSE)


def test_sec_zero_of_another_group_in_the_header_pauses_that_group() -> None:
    clock = FakeClock(START)
    limiter = UpbitLimiter(clock)
    limiter.observe("default", "group=order; min=600; sec=0")
    assert limiter.paused_until("order") == pytest.approx(clock.monotonic() + PAUSE)
    assert limiter.paused_until("default") == float("-inf")


def test_sec_zero_of_an_unknown_group_pauses_the_request_group() -> None:
    clock = FakeClock(START)
    limiter = UpbitLimiter(clock)
    limiter.observe("order", "group=made-up; min=600; sec=0")
    assert limiter.paused_until("order") == pytest.approx(clock.monotonic() + PAUSE)
    assert limiter.paused_until("default") == float("-inf")


@pytest.mark.parametrize("value", ["group=default; min=600; sec=1", "group=default", None])
def test_sec_above_zero_or_missing_does_not_pause(value: str | None) -> None:
    clock = FakeClock(START)
    limiter = UpbitLimiter(clock)
    limiter.observe("default", value)
    assert limiter.paused_until("default") == float("-inf")
    assert limiter.paused_until("order") == float("-inf")


def test_sec_zero_of_the_order_group_does_not_stop_the_other_group() -> None:
    client, fake, clock = setup()
    fake.set_remaining("order", 0)
    client.create_order(M, "bid", "limit", Decimal(10), Decimal(1000), "uid-1")
    client.accounts()
    assert clock.sleeps == []
    assert client.limiter.paused_until("order") > clock.monotonic()


def test_429_waits_until_the_next_second_then_sends_once_more() -> None:
    client, fake, clock = setup()
    fake.script(Reply(429), path="/v1/accounts")
    assert client.accounts()[0].currency == "KRW"
    assert len(fake.requests) == 2
    assert fake.requests[1].monotonic - fake.requests[0].monotonic == pytest.approx(PAUSE)
    assert clock.now() == START + timedelta(seconds=PAUSE)
    # 다시 보낸 요청은 새 토큰(새 nonce)이라 인증을 통과한다.
    assert [r.auth for r in fake.requests] == [None, None]


def test_second_429_is_rate_limit_error() -> None:
    client, fake, _ = setup()
    fake.script(Reply(429), Reply(429), path="/v1/accounts")
    with pytest.raises(UpbitError) as caught:
        client.accounts()
    assert caught.value.error_name == ERROR_RATE_LIMIT
    assert caught.value.branch is ErrorBranch.TRANSIENT
    assert len(fake.requests) == 2


def test_429_on_an_order_is_not_sent_again() -> None:
    client, fake, clock = setup()
    fake.script(Reply(429), path="/v1/orders")
    with pytest.raises(UpbitError) as caught:
        client.create_order(M, "bid", "limit", Decimal(10), Decimal(1000), "uid-2")
    assert caught.value.error_name == ERROR_RATE_LIMIT
    assert caught.value.unknown is True  # 보냈는지 모르므로 조회로 간다(3.6.3)
    assert len(fake.requests) == 1
    # 그 그룹은 다음 초까지 멈춘다.
    assert client.limiter.paused_until("order") == pytest.approx(clock.monotonic() + PAUSE)


# --- 418: 일시 차단 ------------------------------------------------------------------------


def test_418_is_blocked_with_the_time_it_ends() -> None:
    client, fake, _ = setup()
    fake.script(Reply(418, headers={"Retry-After": "600"}))
    with pytest.raises(UpbitError) as caught:
        client.accounts()
    error = caught.value
    assert error.error_name == ERROR_BLOCKED
    assert error.branch is ErrorBranch.BLOCKING
    assert error.blocked_until == START + timedelta(seconds=600)
    assert len(fake.requests) == 1  # 다시 보내지 않는다


def test_418_without_a_time_has_no_blocked_until() -> None:
    client, fake, _ = setup()
    fake.script(Reply(418, code="made_up"))
    with pytest.raises(UpbitError) as caught:
        client.order_chance(M)
    assert caught.value.error_name == ERROR_BLOCKED
    assert caught.value.blocked_until is None
