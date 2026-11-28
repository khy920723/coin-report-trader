"""M3 명세 6절: D1~D38이 모두 수용 시험으로 있다(5절의 "D1~D38 표시가 붙은 시험을 모두 모으고").

고른 시험만 돌릴 때(-k 등)도 빠진 번호가 없는지는 파일에서 본다. 네트워크 차단, keyring 장치,
`claude` 실행 장치는 `tests/conftest.py`가 세션 내내 걸고 시험마다 정리 단계에서 기록을
보므로(막힌 시도가 있으면 그 시험이 실패한다), D 시나리오도 같은 장치 아래에서 돈다.
"""

import re
from pathlib import Path

NUMBERS = [f"D{number}" for number in range(1, 39)]
ACCEPTANCE = Path(__file__).parent
MARKER = re.compile(r"""@pytest\.mark\.scenario\(\s*["'](D\d+)["']\s*\)""")


def _found() -> set[str]:
    return {
        name
        for path in ACCEPTANCE.glob("test_*.py")
        for name in MARKER.findall(path.read_text(encoding="utf-8"))
    }


def test_every_d_scenario_from_d1_to_d38_has_an_acceptance_test() -> None:
    assert [number for number in NUMBERS if number not in _found()] == []


def test_no_marker_names_a_scenario_outside_the_spec() -> None:
    assert sorted(_found() - set(NUMBERS)) == []


def test_the_guards_are_installed_for_the_d_scenarios(
    network_guard, keyring_guard, claude_guard
) -> None:
    assert network_guard.is_installed()
    assert keyring_guard.is_installed()
    assert claude_guard.is_installed()
