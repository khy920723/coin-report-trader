"""M2 명세 6절 C30: 네트워크 차단, keyring 장치, `claude` 실행 장치(3.15)가 켜진 채로 C1~C29,
C31~C33과 M0·M1의 수용 시험 전부가 돈다.

막힌 연결 시도 0건, keyring 호출 0건(M1의 B22와 같다), 이름이 `claude`인 실행 파일을 띄운 일 0건이다
(가짜 실행기와 `[sys.executable, 가짜 실행 파일]`만 쓴다). 세 장치는 `tests/conftest.py`가 세션 내내
걸고, 시험마다 정리 단계에서 기록을 `blocked_by_test`에 남긴다. 이 시험은 conftest가 A12, B22 다음,
수집한 시험의 맨 끝으로 옮긴다(`LAST_SCENARIOS`). M1의 B22를 넓힌다.
"""

import re
from pathlib import Path

import pytest

CHECKED = (
    [f"A{number}" for number in range(1, 13)]
    + [f"B{number}" for number in range(1, 45)]
    + [f"C{number}" for number in range(1, 30)]
    + [f"C{number}" for number in range(31, 34)]
)
ACCEPTANCE = Path(__file__).parent
MARKER = re.compile(r"""@pytest\.mark\.scenario\(\s*["']([ABC]\d+)["']\s*\)""")


def _ran(network_guard) -> list[str]:
    return [nodeid for scenario in CHECKED for nodeid in network_guard.scenarios.get(scenario, [])]


@pytest.mark.scenario("C30")
def test_c30_every_checked_scenario_has_an_acceptance_test() -> None:
    # 고른 시험만 돌릴 때(-k 등)도 빠진 번호가 없는지는 파일에서 본다.
    found = {
        name
        for path in ACCEPTANCE.glob("test_*.py")
        for name in MARKER.findall(path.read_text(encoding="utf-8"))
    }
    assert [scenario for scenario in CHECKED if scenario not in found] == []


@pytest.mark.scenario("C30")
def test_c30_the_three_guards_are_installed(network_guard, keyring_guard, claude_guard) -> None:
    assert network_guard.is_installed()
    assert keyring_guard.is_installed()
    assert claude_guard.is_installed()


@pytest.mark.scenario("C30")
def test_c30_no_blocked_connection_in_the_checked_scenarios(network_guard) -> None:
    for nodeid in _ran(network_guard):
        assert nodeid in network_guard.blocked_by_test, f"아직 돌지 않았다: {nodeid}"
        assert network_guard.blocked_by_test[nodeid] == 0, nodeid


@pytest.mark.scenario("C30")
def test_c30_no_keyring_call_in_the_checked_scenarios(network_guard, keyring_guard) -> None:
    for nodeid in _ran(network_guard):
        assert nodeid in keyring_guard.blocked_by_test, f"아직 돌지 않았다: {nodeid}"
        assert keyring_guard.blocked_by_test[nodeid] == 0, nodeid


@pytest.mark.scenario("C30")
def test_c30_no_claude_executable_was_started_in_the_checked_scenarios(
    network_guard, claude_guard
) -> None:
    for nodeid in _ran(network_guard):
        assert nodeid in claude_guard.blocked_by_test, f"아직 돌지 않았다: {nodeid}"
        assert claude_guard.blocked_by_test[nodeid] == 0, nodeid


@pytest.mark.scenario("C30")
def test_c30_runs_after_the_other_scenarios(request: pytest.FixtureRequest, network_guard) -> None:
    order = [item.nodeid for item in request.session.items]
    mine = order.index(request.node.nodeid)
    for nodeid in _ran(network_guard):
        assert order.index(nodeid) < mine, nodeid
