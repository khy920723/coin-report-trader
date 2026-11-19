"""진짜 `notify/`(M1 명세 3.9, 3.10)의 시험: 글, `HttpWebhookSender`, 한 번 다시 보내기, 확인용 글.

실제 디스코드를 부르지 않는다. 보내기는 `httpx.MockTransport`로, 기다림은 가짜 시계로 본다.
웹후크 주소 꼴의 글은 소스에 통째로 적지 않고 실행 중에 이어 붙여 만든다.
"""

import copy
import json
import logging
import pickle
import subprocess
import sys
import textwrap
from collections.abc import Callable

import httpx
import pytest
from fakes.clock import FakeClock
from fakes.discord import OK, FakeDiscord

from coin_report_trader import notify
from coin_report_trader.core import (
    ALERT_CATEGORY_TITLES,
    AlertCategory,
    AlertDetail,
    Mode,
    WebhookError,
    WebhookSender,
    texts,
)
from coin_report_trader.core.texts import (
    ERROR_DISCORD_NETWORK,
    ERROR_DISCORD_RATE_LIMIT,
    ERROR_DISCORD_TIMEOUT,
    TEST_MESSAGE_PREFIX,
)
from coin_report_trader.notify import (
    HttpWebhookSender,
    RetryingSender,
    alert_message,
    confirmation_message,
    send_alert,
    send_confirmation_message,
)

TOKEN = "Ab9-_." * 11
SECRET_PART = TOKEN[:20]


def hook(number: str = "1" * 18, token: str = TOKEN) -> str:
    return "https://" + "discord.com/api/webhooks/" + f"{number}/{token}"


URL = hook()
DETAIL = AlertDetail(
    "봉 받기",
    "KRW-AAA",
    "시간 초과",
    "내용은 보내지 않는다",
    "다음 받는 때(13:00)에 다시 받습니다.",
)


# --- 글 --------------------------------------------------------------------------------------


def test_an_error_alert_has_three_lines_with_the_coin() -> None:
    shown = alert_message(
        mode=Mode.PAPER,
        category=AlertCategory.ERROR,
        coin="KRW-AAA",
        text="봉 통계를 만들지 못했습니다(시간 초과).",
        detail=DETAIL,
    )
    assert shown.split("\n") == [
        "[모의매매] 오류 알림",
        "AAA: 봉 통계를 만들지 못했습니다(시간 초과).",
        "단계: 봉 받기 · 오류: 시간 초과 · 그 뒤에 한 일: 다음 받는 때(13:00)에 다시 받습니다.",
    ]
    assert DETAIL.content not in shown  # 내용 전체는 넣지 않는다


def test_without_a_coin_the_second_line_is_just_the_text() -> None:
    shown = alert_message(
        mode=Mode.PAPER,
        category=AlertCategory.OTHER,
        coin=None,
        text="예상하지 못한 오류가 났습니다(RuntimeError).",
        detail=AlertDetail(
            "켜기", None, "RuntimeError", "내용", "그 일을 그만두고 다음 일을 계속합니다."
        ),
    )
    assert shown.split("\n") == [
        "[모의매매] 기타 알림",
        "예상하지 못한 오류가 났습니다(RuntimeError).",
        "단계: 켜기 · 오류: RuntimeError · 그 뒤에 한 일: 그 일을 그만두고 다음 일을 계속합니다.",
    ]


def test_other_categories_have_two_lines_even_with_a_detail() -> None:
    for category in (
        AlertCategory.TRADE,
        AlertCategory.REPORT,
        AlertCategory.LIMIT,
        AlertCategory.HOLDING,
    ):
        shown = alert_message(
            mode=Mode.PAPER, category=category, coin="KRW-BBB", text="글", detail=DETAIL
        )
        assert shown.split("\n") == [f"[모의매매] {ALERT_CATEGORY_TITLES[category]}", "BBB: 글"]


def test_an_error_alert_without_a_detail_has_two_lines_and_the_mode_title_follows_the_mode() -> (
    None
):
    shown = alert_message(mode=Mode.LIVE, category=AlertCategory.ERROR, coin=None, text="글")
    assert shown == "[실거래] 오류 알림\n글"


def test_the_first_line_of_the_acceptance_case() -> None:
    shown = alert_message(mode=Mode.PAPER, category=AlertCategory.ERROR, coin="KRW-AAA", text="글")
    assert shown.split("\n")[0] == "[모의매매] 오류 알림"


def test_a_long_text_is_cut_at_1500_characters() -> None:
    shown = alert_message(
        mode=Mode.PAPER, category=AlertCategory.ERROR, coin=None, text="가" * 3000, detail=DETAIL
    )
    assert len(shown) == notify.MAX_MESSAGE_LENGTH == 1500
    assert shown.startswith("[모의매매] 오류 알림\n가가")
    exact = alert_message(
        mode=Mode.PAPER, category=AlertCategory.TRADE, coin=None, text="나" * 1470
    )
    assert len(exact) == len("[모의매매] 매매 알림\n") + 1470 < 1500


def test_the_test_message_per_category() -> None:
    for category in AlertCategory:
        shown = confirmation_message(category)
        assert shown == texts.test_message_text(ALERT_CATEGORY_TITLES[category])
        assert shown.startswith(TEST_MESSAGE_PREFIX)
        title = ALERT_CATEGORY_TITLES[category]
        assert shown == f"[Coin Report Trader] 확인용 글입니다. 이 채널로 {title}이 옵니다."


# --- HttpWebhookSender ---------------------------------------------------------------------


class Recorder:
    def __init__(self, respond: Callable[[httpx.Request], httpx.Response]) -> None:
        self.respond = respond
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)


def make_sender(
    respond: Callable[[httpx.Request], httpx.Response] | None = None,
) -> tuple[HttpWebhookSender, Recorder]:
    recorder = Recorder(respond or (lambda request: httpx.Response(204)))
    return HttpWebhookSender(transport=httpx.MockTransport(recorder)), recorder


def status(
    code: int, headers: dict[str, str] | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    """응답 본문에 요청 주소가 든다(본문을 읽지 않는지, 예외 글에 싣지 않는지 본다)."""
    return lambda request: httpx.Response(
        code, headers=headers, content=("본문에 URL이 든다 " + str(request.url)).encode()
    )


def test_it_is_a_webhook_sender_and_has_no_address_in_its_repr() -> None:
    sender, _ = make_sender()
    assert isinstance(sender, WebhookSender)
    assert isinstance(RetryingSender(sender, FakeClock()), WebhookSender)
    assert repr(sender) == "HttpWebhookSender()"
    sender.close()


def test_it_posts_the_text_to_the_given_address_without_mentions() -> None:
    sender, recorder = make_sender()
    sender.send(URL, "[모의매매] 오류 알림\nAAA: 글")
    (request,) = recorder.requests
    assert request.method == "POST"
    assert str(request.url) == URL
    assert json.loads(request.content) == {
        "content": "[모의매매] 오류 알림\nAAA: 글",
        "allowed_mentions": {"parse": []},  # 사람, 역할, @everyone의 멘션이 울리지 않는다
    }
    assert request.headers["content-type"] == "application/json"
    assert request.headers["user-agent"].startswith("coin-report-trader/")
    assert "cookie" not in request.headers
    assert "authorization" not in request.headers


def test_the_address_is_trimmed_and_the_text_is_sent_as_it_is() -> None:
    sender, recorder = make_sender()
    sender.send(f"  {URL}\n", "@everyone @here <@123> 글")
    assert str(recorder.requests[0].url) == URL
    assert json.loads(recorder.requests[0].content)["content"] == "@everyone @here <@123> 글"


def test_the_timeouts_are_five_seconds_to_connect_and_ten_for_the_rest() -> None:
    sender, recorder = make_sender()
    sender.send(URL, "글")
    assert recorder.requests[0].extensions["timeout"] == {
        "connect": 5.0,
        "read": 10.0,
        "write": 10.0,
        "pool": 10.0,
    }


@pytest.mark.parametrize("code", [200, 201, 204, 299])
def test_any_2xx_is_a_success_with_one_request(code: int) -> None:
    sender, recorder = make_sender(status(code))
    sender.send(URL, "글")
    assert len(recorder.requests) == 1


@pytest.mark.parametrize("code", [301, 302, 307, 400, 401, 403, 404, 500, 502, 503])
def test_other_statuses_are_a_rejection_with_one_request_and_no_redirect(code: int) -> None:
    sender, recorder = make_sender(status(code, {"Location": URL + "/next"}))
    with pytest.raises(WebhookError) as caught:
        sender.send(URL, "글")
    assert caught.value.error_name == f"디스코드 거절({code})"
    assert caught.value.retry_after is None
    assert len(recorder.requests) == 1  # 따라가지 않고 다시 보내지 않는다


def test_the_rate_limit_carries_the_time_given() -> None:
    cases = {"2": 2.0, "2.5": 2.5, " 7 ": 7.0, "0.75": 0.75, "120": 120.0}
    for header, expected in cases.items():
        sender, _ = make_sender(status(429, {"Retry-After": header}))
        with pytest.raises(WebhookError) as caught:
            sender.send(URL, "글")
        assert caught.value.error_name == ERROR_DISCORD_RATE_LIMIT == "디스코드 한도 초과"
        assert caught.value.retry_after == expected


@pytest.mark.parametrize("header", [None, "", "0", "-3", "abc", "inf", "nan", "1e999"])
def test_a_rate_limit_without_a_usable_time_has_no_retry_after(header: str | None) -> None:
    headers = None if header is None else {"Retry-After": header}
    sender, _ = make_sender(status(429, headers))
    with pytest.raises(WebhookError) as caught:
        sender.send(URL, "글")
    assert (caught.value.error_name, caught.value.retry_after) == (ERROR_DISCORD_RATE_LIMIT, None)


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout],
)
def test_timeouts_are_one_request_and_a_timeout_error(error: type[httpx.TimeoutException]) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        raise error(f"too slow {request.url}", request=request)

    sender, recorder = make_sender(respond)
    with pytest.raises(WebhookError) as caught:
        sender.send(URL, "글")
    assert caught.value.error_name == ERROR_DISCORD_TIMEOUT == "디스코드 시간 초과"
    assert len(recorder.requests) == 1


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError])
def test_connection_failures_are_a_network_error(error: type[httpx.RequestError]) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        raise error(f"failed {request.url}", request=request)

    sender, recorder = make_sender(respond)
    with pytest.raises(WebhookError) as caught:
        sender.send(URL, "글")
    assert caught.value.error_name == ERROR_DISCORD_NETWORK == "디스코드 네트워크 끊김"
    assert len(recorder.requests) == 1


def test_any_other_exception_is_a_network_error_too() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        raise RuntimeError(f"odd {request.url}")

    sender, _ = make_sender(respond)
    with pytest.raises(WebhookError) as caught:
        sender.send(URL, "글")
    assert caught.value.error_name == ERROR_DISCORD_NETWORK


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "http://" + "discord.com/api/webhooks/" + "1" * 18 + "/" + TOKEN,
        "https://" + "example.com/api/webhooks/" + "1" * 18 + "/" + TOKEN,
        hook(number="12"),
        hook(token="short"),
        URL + "?wait=true",
        URL + "/",
        None,
        3,
    ],
)
def test_an_address_that_is_not_a_webhook_address_is_not_sent(bad: object) -> None:
    sender, recorder = make_sender()
    with pytest.raises(WebhookError) as caught:
        sender.send(bad, "글")  # type: ignore[arg-type]
    assert caught.value.error_name == ERROR_DISCORD_NETWORK
    assert recorder.requests == []
    assert TOKEN not in repr(caught.value) + str(caught.value.args)


def test_the_address_and_the_body_are_not_in_any_error() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.headers.get("x-case") == "never":
            raise AssertionError
        raise httpx.ConnectError(f"failed {request.url}", request=request)

    cases = [
        respond,
        status(500),
        status(429, {"Retry-After": "3"}),
        lambda request: (_ for _ in ()).throw(
            httpx.ReadTimeout(f"slow {request.url}", request=request)
        ),
    ]
    for case in cases:
        sender, _ = make_sender(case)
        with pytest.raises(WebhookError) as caught:
            sender.send(URL, "글")
        error = caught.value
        shown = " ".join([str(error), repr(error), repr(error.args), repr(sender)])
        assert SECRET_PART not in shown
        assert "본문에 URL" not in shown
        assert error.error_name in shown
        assert error.__cause__ is None
        assert error.__context__ is None
        assert pickle.loads(pickle.dumps(error)).error_name == error.error_name
        assert copy.copy(error).error_name == error.error_name


def test_sending_does_not_touch_the_logging_setup_and_the_app_loggers_stay_clean(
    caplog: pytest.LogCaptureFixture,
) -> None:
    httpx_logger = logging.getLogger("httpx")
    before = (httpx_logger.level, list(httpx_logger.handlers), list(logging.getLogger().handlers))
    caplog.set_level(logging.DEBUG, logger="coin_report_trader")
    sender, _ = make_sender(status(500))
    with pytest.raises(WebhookError):
        sender.send(URL, "글")
    sender.send(URL, "글") if False else None
    after = (httpx_logger.level, list(httpx_logger.handlers), list(logging.getLogger().handlers))
    assert before[0] == after[0]
    assert before[1] == after[1]
    assert [h for h in after[2] if h not in before[2]] == []
    ours = [r for r in caplog.records if r.name.startswith("coin_report_trader")]
    assert SECRET_PART not in " ".join(r.getMessage() for r in ours)


def test_a_real_sender_can_be_made_and_closed_without_connecting() -> None:
    sender = (
        HttpWebhookSender()
    )  # 앱의 TLS 설정으로 `httpx.HTTPTransport`를 만든다(연결하지 않는다)
    assert isinstance(sender, WebhookSender)
    sender.close()


def test_the_sender_works_as_a_context_manager() -> None:
    recorder = Recorder(lambda request: httpx.Response(204))
    with HttpWebhookSender(transport=httpx.MockTransport(recorder)) as sender:
        sender.send(URL, "글")
    assert len(recorder.requests) == 1


# --- 한 번 다시 보내기 ------------------------------------------------------------------------


def rate_limited(seconds: float | None) -> WebhookError:
    return WebhookError(ERROR_DISCORD_RATE_LIMIT, seconds)


def test_a_rate_limit_then_a_success_waits_the_time_given_and_sends_twice() -> None:
    fake, clock = FakeDiscord(), FakeClock()
    fake.script(rate_limited(2.5), OK)
    RetryingSender(fake, clock).send(URL, "글")
    assert clock.sleeps == [2.5]
    assert len(fake.sent) == 2
    assert [m.error_name for m in fake.sent] == [ERROR_DISCORD_RATE_LIMIT, None]


def test_the_wait_is_at_most_thirty_seconds() -> None:
    fake, clock = FakeDiscord(), FakeClock()
    fake.script(rate_limited(120.0), OK)
    RetryingSender(fake, clock).send(URL, "글")
    assert clock.sleeps == [30.0]
    fake2, clock2 = FakeDiscord(), FakeClock()
    fake2.script(rate_limited(30.0), OK)
    RetryingSender(fake2, clock2).send(URL, "글")
    assert clock2.sleeps == [30.0]


def test_two_rate_limits_are_two_requests_and_then_the_rate_limit_error() -> None:
    fake, clock = FakeDiscord(), FakeClock()
    fake.script(rate_limited(1.0), rate_limited(4.0))
    with pytest.raises(WebhookError) as caught:
        RetryingSender(fake, clock).send(URL, "글")
    assert caught.value.error_name == ERROR_DISCORD_RATE_LIMIT
    assert caught.value.retry_after == 4.0  # 마지막으로 낸 오류의 값이다
    assert len(fake.sent) == 2
    assert clock.sleeps == [1.0]


@pytest.mark.parametrize("seconds", [None, 0.0, -1.0, float("inf"), float("nan")])
def test_a_rate_limit_without_a_usable_time_is_not_retried(seconds: float | None) -> None:
    fake, clock = FakeDiscord(), FakeClock()
    fake.script(rate_limited(seconds), OK)
    with pytest.raises(WebhookError) as caught:
        RetryingSender(fake, clock).send(URL, "글")
    assert caught.value.error_name == ERROR_DISCORD_RATE_LIMIT
    assert len(fake.sent) == 1
    assert clock.sleeps == []


@pytest.mark.parametrize(
    "name",
    [ERROR_DISCORD_TIMEOUT, ERROR_DISCORD_NETWORK, "디스코드 거절(404)", "디스코드 거절(500)"],
)
def test_other_failures_are_tried_once_only(name: str) -> None:
    fake, clock = FakeDiscord(), FakeClock()
    fake.script(WebhookError(name), OK)
    with pytest.raises(WebhookError) as caught:
        RetryingSender(fake, clock).send(URL, "글")
    assert caught.value.error_name == name
    assert len(fake.sent) == 1
    assert clock.sleeps == []


def test_a_success_is_one_request_and_no_wait() -> None:
    fake, clock = FakeDiscord(), FakeClock()
    RetryingSender(fake, clock).send(URL, "글")
    assert len(fake.sent) == 1
    assert clock.sleeps == []


def test_a_different_failure_after_the_wait_is_raised_as_it_is() -> None:
    fake, clock = FakeDiscord(), FakeClock()
    fake.script(rate_limited(1.0), WebhookError(ERROR_DISCORD_TIMEOUT))
    with pytest.raises(WebhookError) as caught:
        RetryingSender(fake, clock).send(URL, "글")
    assert caught.value.error_name == ERROR_DISCORD_TIMEOUT
    assert len(fake.sent) == 2


def test_the_real_sender_with_the_retry_end_to_end() -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "1.5"})
        return httpx.Response(204)

    sender, recorder = make_sender(respond)
    clock = FakeClock()
    RetryingSender(sender, clock).send(URL, "글")
    assert len(recorder.requests) == 2
    assert clock.sleeps == [1.5]
    for request in recorder.requests:  # 다시 보낸 요청도 같은 시간 제한이다
        assert request.extensions["timeout"]["connect"] == 5.0


# --- 알림과 확인용 글을 보내는 함수 ---------------------------------------------------------------


def test_send_alert_builds_the_message_and_sends_it() -> None:
    fake = FakeDiscord()
    send_alert(
        fake, URL, mode=Mode.PAPER, category=AlertCategory.ERROR, coin="KRW-AAA", text="글",
        detail=DETAIL,
    )  # fmt: skip
    (sent,) = fake.alerts
    assert sent.url == URL
    assert sent.text.split("\n")[0] == "[모의매매] 오류 알림"
    assert fake.tests == []


def test_send_confirmation_message_sends_the_category_text_as_a_test_message() -> None:
    for category in AlertCategory:
        fake = FakeDiscord()
        send_confirmation_message(fake, URL, category)
        (sent,) = fake.tests
        assert sent.text == texts.test_message_text(ALERT_CATEGORY_TITLES[category])
        assert fake.alerts == []


def test_the_retry_applies_to_both_when_a_clock_is_given() -> None:
    for sending in (
        lambda fake, clock: send_confirmation_message(fake, URL, AlertCategory.ERROR, clock=clock),
        lambda fake, clock: send_alert(
            fake,
            URL,
            mode=Mode.PAPER,
            category=AlertCategory.ERROR,
            coin=None,
            text="글",
            clock=clock,
        ),
    ):
        fake, clock = FakeDiscord(), FakeClock()
        fake.script(rate_limited(3.0), OK)
        sending(fake, clock)
        assert len(fake.sent) == 2
        assert clock.sleeps == [3.0]


def test_without_a_clock_there_is_no_retry() -> None:
    fake = FakeDiscord()
    fake.script(rate_limited(3.0), OK)
    with pytest.raises(WebhookError):
        send_confirmation_message(fake, URL, AlertCategory.ERROR)
    assert len(fake.sent) == 1


def test_a_test_message_through_the_real_sender_has_the_same_limits() -> None:
    sender, recorder = make_sender(status(500))
    with pytest.raises(WebhookError) as caught:
        send_confirmation_message(sender, URL, AlertCategory.TRADE, clock=FakeClock())
    assert caught.value.error_name == "디스코드 거절(500)"
    assert len(recorder.requests) == 1
    body = json.loads(recorder.requests[0].content)
    assert body["content"].startswith(TEST_MESSAGE_PREFIX)
    assert body["allowed_mentions"] == {"parse": []}


# --- 가져오기 ---------------------------------------------------------------------------------


def test_importing_the_notify_modules_makes_no_client_and_no_connection() -> None:
    """새 프로세스에서 클라이언트와 전송 객체를 만들면 끝나게 해 놓고 `notify/`를 import한다."""
    code = textwrap.dedent(
        """
        import importlib
        import os
        import pkgutil

        import httpx

        def stop(*args, **kwargs):
            os._exit(3)

        httpx.Client = stop
        httpx.HTTPTransport = stop

        import coin_report_trader.notify as package

        names = [package.__name__]
        names += [m.name for m in pkgutil.walk_packages(package.__path__, package.__name__ + ".")]
        for name in names:
            importlib.import_module(name)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code], timeout=120, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")


def test_the_package_exports_its_names() -> None:
    assert sorted(notify.__all__) == sorted(
        [
            "MAX_MESSAGE_LENGTH",
            "MAX_RETRY_WAIT",
            "HttpWebhookSender",
            "RetryingSender",
            "alert_message",
            "send_alert",
            "send_confirmation_message",
            "confirmation_message",
        ]
    )
    assert notify.MAX_RETRY_WAIT == 30.0


def test_only_a_rate_limit_is_retried_even_if_another_error_carries_a_time() -> None:
    fake, clock = FakeDiscord(), FakeClock()
    fake.script(WebhookError(ERROR_DISCORD_TIMEOUT, 2.0), OK)
    with pytest.raises(WebhookError) as caught:
        RetryingSender(fake, clock).send(URL, "글")
    assert caught.value.error_name == ERROR_DISCORD_TIMEOUT
    assert len(fake.sent) == 1
    assert clock.sleeps == []


def test_the_default_transport_uses_the_app_tls_context_from_the_certifi_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import certifi

    from coin_report_trader.notify import sender as sender_module

    asked: list[str] = []
    made: list[object] = []
    verified: list[object] = []
    real_context = sender_module.make_tls_context
    real_transport = httpx.HTTPTransport

    def spy_context(cafile: str) -> object:
        asked.append(cafile)
        context = real_context(cafile)
        made.append(context)
        return context

    def spy_transport(*args: object, **kwargs: object) -> httpx.HTTPTransport:
        verified.append(kwargs.get("verify"))
        return real_transport(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(sender_module, "make_tls_context", spy_context)
    monkeypatch.setattr(httpx, "HTTPTransport", spy_transport)
    with HttpWebhookSender() as sender:  # 만들고 닫는다. 보내지 않는다.
        assert repr(sender) == "HttpWebhookSender()"
    assert asked == [certifi.where()]
    assert len(made) == len(verified) == 1
    assert verified[0] is made[0]  # 전송 객체가 받은 `verify`가 앱의 TLS 설정 그대로다
