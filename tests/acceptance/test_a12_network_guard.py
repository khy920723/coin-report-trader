"""명세 7절 A12: 네트워크 차단이 켜진 채로 A1~A11이 돌고, 막힌 연결 시도가 0건이다.

차단은 `tests/conftest.py`가 세션 내내 건다. 이 시험은 conftest가 수집한 시험의 맨 끝으로 옮긴다.
"""

import socket

import pytest

CHECKED = [f"A{number}" for number in range(1, 12)]


@pytest.mark.scenario("A12")
def test_a12_guard_is_on_and_blocks_outside_connections(network_guard) -> None:
    assert network_guard.is_installed()
    # 문서용 주소(192.0.2.0/24)와 이름. 막히므로 실제로 나가지 않는다.
    with network_guard.expect_blocked() as caught:
        with pytest.raises(network_guard.Blocked):
            socket.create_connection(("192.0.2.1", 9), timeout=1)
        with pytest.raises(network_guard.Blocked):
            socket.getaddrinfo("example.com", 443)
        with (
            socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock,
            pytest.raises(network_guard.Blocked),
        ):
            sock.connect(("192.0.2.1", 9))
    assert len(caught) == 3


@pytest.mark.scenario("A12")
def test_a12_loopback_is_still_allowed(network_guard) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=5):
            pass


@pytest.mark.scenario("A12")
def test_a12_no_blocked_attempt_in_a1_to_a11(network_guard) -> None:
    ran = [nodeid for scenario in CHECKED for nodeid in network_guard.scenarios.get(scenario, [])]
    for nodeid in ran:
        assert nodeid in network_guard.blocked_by_test, f"아직 돌지 않았다: {nodeid}"
        assert network_guard.blocked_by_test[nodeid] == 0, nodeid
