"""M5 명세 3.12: 시험의 네트워크 막기(`tests/conftest.py`)가 `api.upbit.com`을 막는다.

세션과 CI는 업비트를 부르지 않는다. 막기는 이름을 풀거나 연결하기 전에 막으므로 실제로 나가지
않는다. 진짜 운반(`HttpxUpbitTransport`)도 같은 길로 막힌다. 연결을 막았는데 시험이 그것을 모르고
넘기지 않도록 `expect_blocked()` 안에서 막힌 시도가 기록에 남았는지 본다.
"""

import socket
from collections.abc import Callable

import pytest

from coin_report_trader.trading.upbit.transport import DEFAULT_BASE_URL, HttpxUpbitTransport

HOST = "api.upbit.com"


def _getaddrinfo() -> None:
    socket.getaddrinfo(HOST, 443)


def _gethostbyname() -> None:
    socket.gethostbyname(HOST)


def _create_connection() -> None:
    socket.create_connection((HOST, 443), timeout=1)


def _real_transport() -> None:
    transport = HttpxUpbitTransport()
    try:
        transport.request("GET", "/v1/market/all", query="", body=None, headers={})
    finally:
        transport.close()


ATTEMPTS: dict[str, Callable[[], None]] = {
    "getaddrinfo": _getaddrinfo,
    "gethostbyname": _gethostbyname,
    "create_connection": _create_connection,
    "real-transport": _real_transport,
}


def test_the_real_transport_points_at_the_upbit_host() -> None:
    assert f"https://{HOST}" == DEFAULT_BASE_URL


@pytest.mark.parametrize("attempt", ATTEMPTS.values(), ids=ATTEMPTS.keys())
def test_connecting_to_the_upbit_host_is_blocked(
    network_guard, attempt: Callable[[], None]
) -> None:
    assert network_guard.is_installed()  # 막기가 걸려 있어야 아래 시도가 나가지 않는다
    with network_guard.expect_blocked() as caught, pytest.raises(network_guard.Blocked):
        attempt()
    assert any(HOST in item for item in caught), caught
