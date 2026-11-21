"""M1 명세 6절 B22: 네트워크 차단과 keyring 장치가 켜진 채로 B1~B21, B23~B44와 M0의 A1~A11이 돈다.

막힌 연결 시도 0건, keyring의 `get_password`, `set_password`, `delete_password`, `get_credential`
호출 0건이다. 두 장치는 `tests/conftest.py`가 세션 내내 걸고, 시험마다 정리 단계에서 기록을
`blocked_by_test`에 남긴다. 이 시험은 conftest가 A12 다음, 수집한 시험의 맨 끝으로 옮긴다
(`LAST_SCENARIOS`). M0의 A12를 넓힌다.
"""

import re
from pathlib import Path

import pytest

CHECKED = (
    [f"A{number}" for number in range(1, 12)]
    + [f"B{number}" for number in range(1, 22)]
    + [f"B{number}" for number in range(23, 45)]
)
ACCEPTANCE = Path(__file__).parent
MARKER = re.compile(r"""@pytest\.mark\.scenario\(\s*["']([AB]\d+)["']\s*\)""")


def _ran(network_guard) -> list[str]:
    return [nodeid for scenario in CHECKED for nodeid in network_guard.scenarios.get(scenario, [])]


@pytest.mark.scenario("B22")
def test_b22_every_checked_scenario_has_an_acceptance_test() -> None:
    # 고른 시험만 돌릴 때(-k 등)도 빠진 번호가 없는지는 파일에서 본다.
    found = {
        name
        for path in ACCEPTANCE.glob("test_*.py")
        for name in MARKER.findall(path.read_text(encoding="utf-8"))
    }
    assert [scenario for scenario in CHECKED if scenario not in found] == []


@pytest.mark.scenario("B22")
def test_b22_both_guards_are_installed(network_guard, keyring_guard) -> None:
    assert network_guard.is_installed()
    assert keyring_guard.is_installed()


@pytest.mark.scenario("B22")
def test_b22_no_blocked_connection_in_the_checked_scenarios(network_guard) -> None:
    for nodeid in _ran(network_guard):
        assert nodeid in network_guard.blocked_by_test, f"아직 돌지 않았다: {nodeid}"
        assert network_guard.blocked_by_test[nodeid] == 0, nodeid


@pytest.mark.scenario("B22")
def test_b22_no_keyring_call_in_the_checked_scenarios(network_guard, keyring_guard) -> None:
    for nodeid in _ran(network_guard):
        assert nodeid in keyring_guard.blocked_by_test, f"아직 돌지 않았다: {nodeid}"
        assert keyring_guard.blocked_by_test[nodeid] == 0, nodeid
