"""가짜 디스코드(`tests/fakes/discord.py`)의 시험(M1 명세 3.13).

27번의 조립과 29번의 수용 시험이 이 가짜를 고치지 않고 쓰므로, 약속(기록, 정해 둔 실패, 막아 두기)을
여기서 확인한다.
"""

import threading

import pytest
from fakes.discord import ALERT, OK, TEST, FakeDiscord, SentMessage

from coin_report_trader.core import WebhookError, WebhookSender, texts
from coin_report_trader.core.texts import (
    ERROR_DISCORD_RATE_LIMIT,
    ERROR_DISCORD_TIMEOUT,
    TEST_MESSAGE_PREFIX,
)

URL = "https://example.invalid/hook-1"
OTHER = "https://example.invalid/hook-2"


def test_it_is_a_webhook_sender() -> None:
    assert isinstance(FakeDiscord(), WebhookSender)


def test_it_records_alerts_and_test_messages_apart() -> None:
    fake = FakeDiscord()
    fake.send(URL, "[모의매매] 오류 알림\nAAA: 글")
    fake.send(OTHER, texts.test_message_text("오류 알림"))
    fake.send(URL, f"{TEST_MESSAGE_PREFIX}")
    assert fake.sent == [
        SentMessage(URL, "[모의매매] 오류 알림\nAAA: 글", ALERT, None),
        SentMessage(OTHER, texts.test_message_text("오류 알림"), TEST, None),
        SentMessage(URL, TEST_MESSAGE_PREFIX, TEST, None),
    ]
    assert [m.text for m in fake.alerts] == ["[모의매매] 오류 알림\nAAA: 글"]
    assert len(fake.tests) == 2
    assert fake.urls == [URL, OTHER, URL]
    assert fake.texts[0].startswith("[모의매매]")


def test_a_text_that_only_contains_the_prefix_later_is_an_alert() -> None:
    fake = FakeDiscord()
    fake.send(URL, f"앞 {TEST_MESSAGE_PREFIX}")
    assert fake.sent[0].kind == ALERT


def test_a_scripted_failure_is_raised_and_recorded() -> None:
    fake = FakeDiscord()
    fake.script(WebhookError(ERROR_DISCORD_TIMEOUT))
    with pytest.raises(WebhookError) as caught:
        fake.send(URL, "글")
    assert caught.value.error_name == ERROR_DISCORD_TIMEOUT
    assert caught.value.retry_after is None
    fake.send(URL, "다음 글")  # 줄을 다 썼으면 성공이다
    assert [m.error_name for m in fake.sent] == [ERROR_DISCORD_TIMEOUT, None]  # 실패도 남는다


def test_one_success_then_one_failure_like_the_two_test_messages() -> None:
    fake = FakeDiscord()
    fake.script(OK, WebhookError(ERROR_DISCORD_TIMEOUT))
    fake.send(URL, texts.test_message_text("매매 알림"))
    with pytest.raises(WebhookError):
        fake.send(URL, texts.test_message_text("오류 알림"))
    assert [(m.kind, m.error_name) for m in fake.sent] == [
        (TEST, None),
        (TEST, ERROR_DISCORD_TIMEOUT),
    ]


def test_a_script_line_can_be_limited_to_one_address() -> None:
    fake = FakeDiscord()
    fake.script(WebhookError(ERROR_DISCORD_TIMEOUT), url=OTHER)
    fake.send(URL, "글")  # 다른 주소의 줄은 쓰지 않는다
    with pytest.raises(WebhookError):
        fake.send(OTHER, "글")
    assert [m.error_name for m in fake.sent] == [None, ERROR_DISCORD_TIMEOUT]


def test_the_retry_after_of_a_scripted_error_is_kept_and_a_new_error_is_raised_each_time() -> None:
    error = WebhookError(ERROR_DISCORD_RATE_LIMIT, 2.5)
    fake = FakeDiscord()
    fake.script(error, error)
    raised = []
    for _ in range(2):
        with pytest.raises(WebhookError) as caught:
            fake.send(URL, "글")
        raised.append(caught.value)
    assert [e.retry_after for e in raised] == [2.5, 2.5]
    assert raised[0] is not error
    assert raised[0] is not raised[1]


def test_fail_lasts_until_healed_or_for_a_number_of_sends() -> None:
    fake = FakeDiscord()
    fake.fail(ERROR_DISCORD_TIMEOUT)
    for _ in range(3):
        with pytest.raises(WebhookError):
            fake.send(URL, "글")
    fake.heal()
    fake.send(URL, "글")
    fake.fail(ERROR_DISCORD_RATE_LIMIT, retry_after=1.5, times=2)
    for _ in range(2):
        with pytest.raises(WebhookError) as caught:
            fake.send(URL, "글")
        assert caught.value.retry_after == 1.5
    fake.send(URL, "글")  # 정한 횟수가 지나면 성공이다
    assert [m.error_name for m in fake.sent][-3:] == [ERROR_DISCORD_RATE_LIMIT] * 2 + [None]


def test_heal_clears_scripts_too() -> None:
    fake = FakeDiscord()
    fake.script(WebhookError(ERROR_DISCORD_TIMEOUT))
    fake.heal()
    fake.send(URL, "글")


def test_bad_arguments_are_refused() -> None:
    fake = FakeDiscord()
    with pytest.raises(TypeError, match="script"):
        fake.script("실패")  # type: ignore[arg-type]
    for times in (0, -1, True, 1.5, 2.0, "2"):
        with pytest.raises(ValueError, match="times"):
            fake.fail(ERROR_DISCORD_TIMEOUT, times=times)  # type: ignore[arg-type]


def test_hold_stops_a_send_until_release_and_the_send_is_recorded_after() -> None:
    fake = FakeDiscord()
    fake.hold()
    done = threading.Event()

    def sending() -> None:
        fake.send(URL, "글")
        done.set()

    thread = threading.Thread(target=sending, daemon=True)
    thread.start()
    assert fake.entered.wait(10)
    assert not done.wait(0.05)  # 멈춰 있다
    assert fake.sent == []
    fake.release()
    assert done.wait(10)
    thread.join(10)
    assert len(fake.sent) == 1


def test_a_held_send_that_is_never_released_ends_with_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fakes import discord

    monkeypatch.setattr(discord, "HOLD_LIMIT", 0.05)
    fake = FakeDiscord()
    fake.hold()
    with pytest.raises(AssertionError, match="release"):
        fake.send(URL, "글")
    assert fake.sent == []


def test_wait_for_counts_the_records() -> None:
    fake = FakeDiscord()
    assert fake.wait_for(1, timeout=0.02) is False
    threading.Timer(0.02, fake.send, args=(URL, "글")).start()
    assert fake.wait_for(1) is True


def test_the_address_is_not_in_the_repr() -> None:
    fake = FakeDiscord()
    fake.send(URL, "글")
    assert URL not in repr(fake)


def test_several_threads_can_send_together() -> None:
    fake = FakeDiscord()

    def work(index: int) -> None:
        for step in range(50):
            fake.send(URL, f"글 {index}-{step}")

    threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert len(fake.sent) == 200
    assert len(set(fake.texts)) == 200


def test_entered_is_set_only_by_a_send_stopped_after_the_latest_hold() -> None:
    fake = FakeDiscord()
    fake.hold()
    first = threading.Thread(target=fake.send, args=(URL, "첫째"), daemon=True)
    first.start()
    assert fake.entered.wait(10)
    fake.release()
    first.join(10)
    assert not first.is_alive()
    assert not fake.entered.is_set()  # 풀었으면 지운다
    fake.hold()
    assert not fake.entered.is_set()  # 아직 멈춘 보내기가 없다
    second = threading.Thread(target=fake.send, args=(URL, "둘째"), daemon=True)
    second.start()
    assert fake.entered.wait(10)  # 새로 멈춘 보내기가 세운다
    fake.release()
    second.join(10)
    assert not second.is_alive()
    assert len(fake.sent) == 2
    assert not fake.entered.is_set()


def test_a_script_line_comes_first_and_does_not_use_up_a_fail() -> None:
    fake = FakeDiscord()
    fake.script(OK)
    fake.fail(ERROR_DISCORD_TIMEOUT, times=1)
    fake.send(URL, "첫째")  # 줄(OK)
    with pytest.raises(WebhookError) as caught:
        fake.send(URL, "둘째")  # 줄이 없으니 `fail`
    assert caught.value.error_name == ERROR_DISCORD_TIMEOUT
    fake.send(URL, "셋째")  # 둘 다 썼다
    assert [m.error_name for m in fake.sent] == [None, ERROR_DISCORD_TIMEOUT, None]


def test_a_script_failure_does_not_reduce_the_times_of_fail() -> None:
    fake = FakeDiscord()
    fake.script(WebhookError(ERROR_DISCORD_RATE_LIMIT, 1.0))
    fake.fail(ERROR_DISCORD_TIMEOUT, times=1)
    with pytest.raises(WebhookError) as first:
        fake.send(URL, "첫째")
    assert first.value.error_name == ERROR_DISCORD_RATE_LIMIT
    with pytest.raises(WebhookError) as second:
        fake.send(URL, "둘째")
    assert second.value.error_name == ERROR_DISCORD_TIMEOUT
    fake.send(URL, "셋째")


def test_a_new_hold_clears_entered_even_without_a_release() -> None:
    fake = FakeDiscord()
    fake.hold()
    stuck = threading.Thread(target=fake.send, args=(URL, "첫째"), daemon=True)
    stuck.start()
    assert fake.entered.wait(10)
    fake.hold()  # release 없이 다시 hold: 앞서 멈춘 보내기의 표시는 지운다
    assert not fake.entered.is_set()
    fake.release()
    stuck.join(10)
    assert not stuck.is_alive()
