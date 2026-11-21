"""수용 시험 폴더의 픽스처. 위의 `tests/conftest.py`(차단 장치, 시나리오의 차례)는 그대로 걸린다."""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from acceptance.m1_support import Rig, make_rig


@pytest.fixture
def rigs(tmp_path: Path) -> Iterator[Callable[..., Rig]]:
    """`make_rig`로 만든 두 스레드를 시험이 끝날 때 멈춘다. 다시 켠 것은 `track`으로 넘긴다."""
    made: list[Rig] = []

    def factory(**kwargs: Any) -> Rig:
        rig = make_rig(tmp_path, **kwargs)
        made.append(rig)
        return rig

    factory.track = made.append  # type: ignore[attr-defined]
    yield factory
    for rig in made:
        rig.stop()
