"""명세 7절 A12: 네트워크 차단이 켜진 채로 A1~A11이 돌고, 막힌 연결 시도가 0건이다.

차단은 `tests/conftest.py`가 세션 내내 건다. 이 시험은 conftest가 수집한 시험의 맨 끝으로 옮긴다.
밖으로 가는 대상은 문서용 주소(192.0.2.0/24, 2001:db8::/32)와 `example.com`이다. 막히므로 실제로
나가지 않는다.
"""

import asyncio
import socket
from collections.abc import Callable

import pytest

CHECKED = [f"A{number}" for number in range(1, 12)]
OUTSIDE_V4 = ("192.0.2.1", 9)
OUTSIDE_V6 = ("2001:db8::1", 9)


def _tcp(family: int = socket.AF_INET) -> socket.socket:
    try:
        return socket.socket(family, socket.SOCK_STREAM)
    except OSError as exc:  # IPv6를 쓸 수 없는 환경
        pytest.skip(f"소켓을 만들 수 없다: {exc}")


def _connect() -> None:
    with _tcp() as sock:
        sock.connect(OUTSIDE_V4)


def _connect_ex() -> None:
    with _tcp() as sock:
        sock.connect_ex(OUTSIDE_V4)


def _connect_v6() -> None:
    with _tcp(socket.AF_INET6) as sock:
        sock.connect(OUTSIDE_V6)


def _udp_sendto() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(b"x", OUTSIDE_V4)


def _udp_sendto_with_flags() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(b"x", 0, OUTSIDE_V4)


def _create_connection() -> None:
    socket.create_connection(OUTSIDE_V4, timeout=1)


def _getaddrinfo() -> None:
    socket.getaddrinfo("example.com", 443)


def _gethostbyname() -> None:
    socket.gethostbyname("example.com")


def _asyncio_open_connection() -> None:
    async def run() -> None:
        await asyncio.wait_for(asyncio.open_connection(*OUTSIDE_V4), timeout=5)

    asyncio.run(run())


BLOCKED: dict[str, Callable[[], None]] = {
    "connect": _connect,
    "connect_ex": _connect_ex,
    "connect-ipv6": _connect_v6,
    "udp-sendto": _udp_sendto,
    "udp-sendto-flags": _udp_sendto_with_flags,
    "create_connection": _create_connection,
    "getaddrinfo": _getaddrinfo,
    "gethostbyname": _gethostbyname,
    "asyncio-open_connection": _asyncio_open_connection,
}


@pytest.mark.scenario("A12")
def test_a12_guard_is_installed(network_guard) -> None:
    assert network_guard.is_installed()


@pytest.mark.scenario("A12")
@pytest.mark.parametrize("attempt", BLOCKED.values(), ids=BLOCKED.keys())
def test_a12_outside_connections_are_blocked(network_guard, attempt: Callable[[], None]) -> None:
    with network_guard.expect_blocked() as caught, pytest.raises(network_guard.Blocked):
        attempt()
    assert caught


@pytest.mark.scenario("A12")
def test_a12_loopback_tcp_by_address_and_by_localhost_is_allowed() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(2)
        port = server.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=5):
            pass
        with socket.create_connection(("localhost", port), timeout=5):
            pass


@pytest.mark.scenario("A12")
def test_a12_loopback_udp_sendto_is_allowed() -> None:
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver,
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender,
    ):
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(5)
        sender.sendto(b"ping", receiver.getsockname())
        assert receiver.recvfrom(16)[0] == b"ping"


@pytest.mark.scenario("A12")
def test_a12_no_blocked_attempt_in_a1_to_a11(network_guard) -> None:
    ran = [nodeid for scenario in CHECKED for nodeid in network_guard.scenarios.get(scenario, [])]
    for nodeid in ran:
        assert nodeid in network_guard.blocked_by_test, f"아직 돌지 않았다: {nodeid}"
        assert network_guard.blocked_by_test[nodeid] == 0, nodeid
