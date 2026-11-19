"""디스코드 보내기 한 번의 마감(M1 명세 3.9의 "연결 5초, 전체 10초")의 시험.

진짜 길(`transport` 없이 만든 `HttpWebhookSender`: `httpx.HTTPTransport`, `httpcore`의 연결 풀과
HTTP/1.1)을 그대로 쓰고, 그 아래 소켓만 가짜 네트워크 백엔드로 바꾼다. 가짜는 소켓을 열지 않고,
기다려야 할 때마다 가짜 시계를 옮긴다(실제로 기다리지 않는다). 웹후크 주소 꼴의 글은 실행 중에
이어 붙여 만든다.
"""

import socket
import threading
import types
from dataclasses import dataclass, field
from typing import Any

import pytest
from fakes.clock import FakeClock

from coin_report_trader.core import WebhookError
from coin_report_trader.core.texts import ERROR_DISCORD_NETWORK, ERROR_DISCORD_TIMEOUT
from coin_report_trader.notify import HttpWebhookSender, RetryingSender
from coin_report_trader.notify import sender as sender_module

TOKEN = "Zy8-_." * 11
URL = "https://" + "discord.com/api/webhooks/" + "2" * 18 + "/" + TOKEN
NO_CONTENT = b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n"


def limited(seconds: int) -> bytes:
    return (
        b"HTTP/1.1 429 Too Many Requests\r\nConnection: close\r\nContent-Length: 0\r\n"
        + f"Retry-After: {seconds}\r\n\r\n".encode()
    )


@dataclass
class Plan:
    """연결 하나가 겪는 일. 숫자는 그 일이 걸리는 초다."""

    connect: float = 0.0
    tls: float = 0.0
    write: float = 0.0
    reads: list[tuple[float, bytes]] = field(default_factory=lambda: [(0.0, NO_CONTENT)])


class World:
    def __init__(self) -> None:
        self.clock = FakeClock()
        self.plans: list[Plan] = []
        self.failing: dict[str, float] = {}
        """주소 → 연결이 실패하기까지 걸리는 초."""
        self.connects: list[tuple[str, float | None]] = []
        self.streams: list[Stream] = []

    def wait(self, seconds: float, timeout: float | None) -> None:
        """`seconds`가 걸리는 일을 `timeout`만큼만 기다린다. 넘으면 그만큼 기다리고 시간 초과."""
        if timeout is not None and seconds > timeout:
            self.clock.advance(timeout)
            raise TimeoutError
        self.clock.advance(seconds)


class Stream:
    def __init__(self, world: World, plan: Plan) -> None:
        self._world, self._plan = world, plan
        self._reads = list(plan.reads)
        self._write = plan.write
        self.closed = False

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        if not self._reads:
            return b""
        delay, data = self._reads.pop(0)
        self._world.wait(delay, timeout)
        return data

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        delay, self._write = self._write, 0.0  # 첫 쓰기에만 걸린다
        self._world.wait(delay, timeout)

    def start_tls(self, ssl_context: Any, server_hostname: Any = None, timeout: Any = None) -> Any:
        self._world.wait(self._plan.tls, timeout)
        return self

    def close(self) -> None:
        self.closed = True

    def get_extra_info(self, info: str) -> Any:
        return None


class Backend:
    def __init__(self, world: World) -> None:
        self._world = world

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: Any = None,
        socket_options: Any = None,
    ) -> Stream:
        world = self._world
        world.connects.append((host, timeout))
        if host in world.failing:
            world.wait(world.failing[host], timeout)
            raise OSError("연결하지 못했다")
        plan = world.plans.pop(0)
        world.wait(plan.connect, timeout)
        stream = Stream(world, plan)
        world.streams.append(stream)
        return stream

    def sleep(self, seconds: float) -> None:
        raise AssertionError("연결을 다시 시도하지 않는다")


def make(
    *plans: Plan, addresses: tuple[str, ...] = ("203.0.113.7",), dns: float = 0.0
) -> tuple[HttpWebhookSender, World]:
    world = World()
    world.plans.extend(plans)

    def resolve(host: str, port: int, type: int = 0) -> list[tuple[Any, ...]]:
        world.clock.advance(dns)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in addresses]

    sender = HttpWebhookSender(
        monotonic=world.clock.monotonic, resolve=resolve, network_backend=Backend(world)
    )
    return sender, world


def elapsed(world: World, since: float) -> float:
    return world.clock.monotonic() - since


def send_and_time(sender: HttpWebhookSender, world: World) -> tuple[str | None, float]:
    start = world.clock.monotonic()
    try:
        sender.send(URL, "글")
    except WebhookError as exc:
        return exc.error_name, elapsed(world, start)
    return None, elapsed(world, start)


def test_a_quick_server_is_a_success_and_the_connection_is_closed() -> None:
    sender, world = make(Plan())
    assert send_and_time(sender, world) == (None, 0.0)
    assert len(world.connects) == 1
    assert [stream.closed for stream in world.streams] == [True]


def test_a_server_that_trickles_is_cut_at_ten_seconds_in_total() -> None:
    # 연결 4초, 쓰기 3초, 머리글을 3초마다 조금씩: 단계마다는 상한 안이지만 합은 10초를 넘는다.
    reads = [(3.0, b"HTTP/1.1 204"), (3.0, b" No Content\r\n"), (3.0, b"\r\n")]
    sender, world = make(Plan(connect=4.0, write=3.0, reads=reads))
    name, seconds = send_and_time(sender, world)
    assert name == ERROR_DISCORD_TIMEOUT
    assert seconds == 10.0
    assert len(world.connects) == 1  # 요청 하나
    assert world.streams[0].closed


def test_a_server_that_never_answers_ends_at_ten_seconds_with_one_request() -> None:
    sender, world = make(Plan(reads=[(10_000.0, NO_CONTENT)]))
    assert send_and_time(sender, world) == (ERROR_DISCORD_TIMEOUT, 10.0)
    assert len(world.connects) == 1
    assert world.streams[0].closed


def test_a_slow_connection_ends_at_five_seconds_with_one_request() -> None:
    sender, world = make(Plan(connect=10_000.0))
    assert send_and_time(sender, world) == (ERROR_DISCORD_TIMEOUT, 5.0)
    assert len(world.connects) == 1
    assert world.connects[0][1] == 5.0  # 연결에 준 시간이 그 시도의 연결 마감이다


def test_the_tls_handshake_shares_the_five_seconds_of_the_connection() -> None:
    sender, world = make(Plan(connect=3.0, tls=10_000.0))
    assert send_and_time(sender, world) == (ERROR_DISCORD_TIMEOUT, 5.0)
    sender, world = make(Plan(connect=2.4, tls=2.4))  # 합이 5초 안이면 된다
    assert send_and_time(sender, world) == (None, pytest.approx(4.8))


def test_the_connections_of_several_addresses_share_the_five_seconds() -> None:
    addresses = ("203.0.113.1", "203.0.113.2", "203.0.113.3")
    sender, world = make(Plan(connect=0.5), addresses=addresses)
    world.failing = {addresses[0]: 2.0, addresses[1]: 2.0}
    assert send_and_time(sender, world) == (None, 4.5)  # 앞 둘이 실패해도 셋째가 남은 시간 안이다
    assert [host for host, _ in world.connects] == list(addresses)
    sender, world = make(Plan(connect=1.5), addresses=addresses)
    world.failing = {addresses[0]: 2.0, addresses[1]: 2.0}
    assert send_and_time(sender, world) == (ERROR_DISCORD_TIMEOUT, 5.0)  # 셋째는 1초만 받는다
    assert world.connects[2][1] == 1.0


def test_a_name_lookup_that_took_too_long_ends_the_attempt_without_connecting() -> None:
    sender, world = make(Plan(), dns=6.0)  # 이름 찾기는 끊을 수 없다. 끝난 뒤에 남은 시간을 잰다.
    assert send_and_time(sender, world) == (ERROR_DISCORD_TIMEOUT, 6.0)
    assert world.connects == []


def test_a_name_lookup_failure_is_a_network_error_not_a_timeout() -> None:
    world = World()

    def refuse(host: str, port: int, type: int = 0) -> list[tuple[Any, ...]]:
        raise socket.gaierror("이름을 찾지 못했다")

    sender = HttpWebhookSender(
        monotonic=world.clock.monotonic, resolve=refuse, network_backend=Backend(world)
    )
    assert send_and_time(sender, world)[0] == ERROR_DISCORD_NETWORK


def test_the_rate_limit_wait_is_not_part_of_the_first_ten_seconds_and_the_retry_has_its_own() -> (
    None
):
    first = Plan(reads=[(9.0, limited(2))])
    second = Plan(reads=[(9.0, NO_CONTENT)])
    sender, world = make(first, second)
    start = world.clock.monotonic()
    RetryingSender(sender, world.clock).send(URL, "글")
    assert world.clock.sleeps == [2.0]
    assert len(world.connects) == 2  # 요청은 둘이다
    assert elapsed(world, start) == 20.0  # 9 + 2 + 9: 둘째 시도도 새 마감을 받았다


def test_the_retry_after_a_timeout_attempt_is_not_made_but_the_next_send_works() -> None:
    sender, world = make(Plan(reads=[(10_000.0, NO_CONTENT)]), Plan())
    before = set(threading.enumerate())
    assert send_and_time(sender, world)[0] == ERROR_DISCORD_TIMEOUT
    assert set(threading.enumerate()) == before  # 시도를 위해 띄운 스레드가 없다
    assert world.streams[0].closed  # 끊은 연결은 닫혔다
    assert send_and_time(sender, world) == (None, 0.0)  # 다음 보내기는 새로 연결한다
    assert len(world.connects) == 2
    assert set(threading.enumerate()) == before


def test_a_timeout_error_carries_no_address_or_token(caplog: pytest.LogCaptureFixture) -> None:
    sender, _ = make(Plan(reads=[(10_000.0, NO_CONTENT)]))
    with caplog.at_level(0), pytest.raises(WebhookError) as caught:
        sender.send(URL, "글")
    error = caught.value
    assert (error.__cause__, error.__context__) == (None, None)
    assert TOKEN not in repr(error) + str(error.args) + repr(sender) + caplog.text
    assert error.error_name == ERROR_DISCORD_TIMEOUT


def test_the_real_sender_puts_the_deadline_backend_under_the_connection_pool() -> None:
    sender = HttpWebhookSender()  # 가짜 없이 만든다(연결하지 않는다)
    try:
        pool = sender._client._transport._pool  # type: ignore[attr-defined]
        assert isinstance(pool._network_backend, sender_module._DeadlineBackend)
        assert (sender_module.CONNECT_TIMEOUT, sender_module.TIMEOUT) == (5.0, 10.0)
    finally:
        sender.close()


def test_without_the_pool_backend_the_sender_refuses_to_start_instead_of_falling_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sender_module.httpx, "HTTPTransport", lambda **kwargs: types.SimpleNamespace()
    )
    with pytest.raises(RuntimeError):
        HttpWebhookSender()
