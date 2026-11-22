"""`tests/fakes/autostart.py`의 가짜 시작 앱 등록(M1 명세 3.16.4). 값은 모두 만든 값이다."""

import pytest
from fakes.autostart import FakeAutostart

from coin_report_trader.core import AutostartStore

COMMAND = '"C:\\made\\up\\coin-report-trader.exe"'


def test_the_fake_keeps_the_port_and_starts_supported_and_empty() -> None:
    fake = FakeAutostart()
    assert isinstance(fake, AutostartStore)
    assert fake.supported() is True
    assert fake.read() is None
    assert (fake.writes, fake.deletes) == ([], 0)
    assert FakeAutostart(supported=False).supported() is False
    assert FakeAutostart(registered=COMMAND).read() == COMMAND


def test_a_write_replaces_the_registered_text_and_is_recorded() -> None:
    fake = FakeAutostart(registered="옛 글")
    fake.write(COMMAND)
    assert fake.read() == COMMAND
    assert fake.writes == [COMMAND]


def test_a_delete_clears_it_and_a_missing_one_is_a_success() -> None:
    fake = FakeAutostart(registered=COMMAND)
    fake.delete()
    fake.delete()  # 없어도 성공이다
    assert fake.read() is None
    assert fake.deletes == 2


def test_the_chosen_errors_are_separate_for_write_and_delete_and_change_nothing() -> None:
    fake = FakeAutostart(registered="옛 글", write_error=OSError("쓰기 막힘"))
    with pytest.raises(OSError, match="쓰기 막힘"):
        fake.write(COMMAND)
    assert (fake.read(), fake.writes) == ("옛 글", [])  # 등록된 글은 그대로, 기록도 없다
    fake.delete()  # 지우기는 따로 정한다
    assert fake.read() is None
    fake.registered = "다시 둔 글"
    fake.delete_error = OSError("지우기 막힘")
    with pytest.raises(OSError, match="지우기 막힘"):
        fake.delete()
    assert (fake.read(), fake.deletes) == ("다시 둔 글", 1)
    fake.write_error = None
    fake.write(COMMAND)  # 오류를 빼면 다시 된다
    assert fake.writes == [COMMAND]
