"""진짜 `WebhookSender`와 한 번 다시 보내기(M1 명세 3.9, 3.10).

`HttpWebhookSender.send(url, text)`는 한 번 보낸다(요청 하나). 실패는 모두 `WebhookError`다.
상태와 예외마다의 오류 이름:

- 2xx: 성공
- 429: "디스코드 한도 초과". `retry_after`는 `Retry-After` 머리글의 초(소수 가능)이고, 없거나
  읽지 못하거나 0 이하면 `None`이다.
- 2xx가 아닌 그 밖의 상태(3xx, 4xx, 5xx): "디스코드 거절({상태})"
- `httpx.TimeoutException`(연결, 쓰기, 읽기, 연결 풀): "디스코드 시간 초과"
- 그 밖의 `Exception`(연결 실패 등), 웹후크 주소의 꼴이 아님(보내지 않는다): "디스코드 네트워크
  끊김"

- 주소는 인자로만 받는다. 보내기 전에 앞뒤 빈칸을 지우고 `is_webhook_url`로 본다. 오류는
  `except` 밖에서 오류 이름만으로 새로 내므로(원래 오류를 잇지 않는다) 예외의 글, `args`, `repr`에
  주소가 없다. 응답 본문은 읽지 않는다(스트림으로 받아 머리글만 보고 닫는다).
- 보내는 글은 JSON `{"content": 글, "allowed_mentions": {"parse": []}}`다. `allowed_mentions`의
  빈 `parse`로 사람, 역할, `@everyone`의 멘션이 울리지 않는다.
- 시간 제한: 연결 5초, 읽기, 쓰기, 연결 풀 각 10초(`trading/quote`와 같다). `httpx`의 시간 제한은
  단계마다의 상한이어서 요청 전체의 상한이 아니다. 리다이렉트를 따라가지 않고, 쿠키를 두지 않고,
  환경 변수의 프록시와 인증서 설정을 읽지 않는다(`trust_env=False`). 연결은 다시 시도하지 않는다.
- TLS는 `core.make_tls_context(certifi.where())`다. 동기 `httpx.Client` 하나를 가지고 보내기
  스레드에서만 쓴다. 다 쓰면 `close()`를 부른다.

`RetryingSender(inner, clock)`는 아무 `WebhookSender`를 감싸 한 번 다시 보낸다: `inner`가
"디스코드 한도 초과"를 내면 그 `retry_after`(30초를 넘으면 30초)를 `clock.sleep`으로 기다린 뒤
한 번만 다시 보낸다. 기다릴 시간이 없으면 다시 보내지 않고 그 오류를 낸다. 그 밖의 실패는 다시
보내지 않는다. 그래서 보내기 하나의 요청은 많아야 둘이다.
"""

import math
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Final

import certifi
import httpx

from coin_report_trader import __version__
from coin_report_trader.core import (
    AlertCategory,
    AlertDetail,
    Clock,
    Mode,
    WebhookError,
    WebhookSender,
    is_webhook_url,
    make_tls_context,
)
from coin_report_trader.core.texts import (
    ERROR_DISCORD_NETWORK,
    ERROR_DISCORD_RATE_LIMIT,
    ERROR_DISCORD_TIMEOUT,
    error_discord_rejected,
)
from coin_report_trader.notify.message import alert_message, confirmation_message

CONNECT_TIMEOUT: Final = 5.0
TIMEOUT: Final = 10.0
MAX_RETRY_WAIT: Final = 30.0
"""다시 보내기 전에 기다리는 가장 긴 시간(초)."""
USER_AGENT: Final = f"coin-report-trader/{__version__}"
_STATUS_RATE_LIMIT: Final = 429


def parse_retry_after(value: str | None) -> float | None:
    """`Retry-After`의 양의 유한한 초(소수 가능). 그 밖의 값은 `None`이다."""
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds > 0 else None


def _no_cookies() -> CookieJar:
    return CookieJar(policy=DefaultCookiePolicy(allowed_domains=()))


class HttpWebhookSender:
    """`WebhookSender`의 진짜(모듈 설명). `transport`는 시험이 `MockTransport`를 넣는 길이다."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        if transport is None:
            transport = httpx.HTTPTransport(
                verify=make_tls_context(certifi.where()), retries=0, trust_env=False
            )
        self._client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(TIMEOUT, connect=CONNECT_TIMEOUT),
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
            cookies=_no_cookies(),
            trust_env=False,
        )

    def __repr__(self) -> str:
        return "HttpWebhookSender()"

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpWebhookSender":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def send(self, url: str, text: str) -> None:
        """한 번 보낸다. 실패하면 `WebhookError`(모듈 설명의 표)."""
        error_name, retry_after = self._post(url, text)
        if error_name is not None:
            raise WebhookError(error_name, retry_after)

    def _post(self, url: str, text: str) -> tuple[str | None, float | None]:
        """(오류 이름, 기다릴 시간). 예외는 이 함수 안에서 끝내 주소가 실려 나가지 않게 한다."""
        target = url.strip() if isinstance(url, str) else ""
        if not is_webhook_url(target):
            return ERROR_DISCORD_NETWORK, None  # 보내지 않는다
        payload = {"content": text, "allowed_mentions": {"parse": []}}
        status = 0
        retry_after: float | None = None
        error_name: str | None = None
        try:
            request = self._client.build_request("POST", target, json=payload)
            response = self._client.send(request, stream=True)
            try:
                status = response.status_code
                retry_after = parse_retry_after(response.headers.get("Retry-After"))
            finally:
                response.close()
        except httpx.TimeoutException:
            error_name = ERROR_DISCORD_TIMEOUT
        except Exception:
            error_name = ERROR_DISCORD_NETWORK
        if error_name is not None:
            return error_name, None
        if 200 <= status < 300:
            return None, None
        if status == _STATUS_RATE_LIMIT:
            return ERROR_DISCORD_RATE_LIMIT, retry_after
        return error_discord_rejected(status), None


class RetryingSender:
    """아무 `WebhookSender`를 감싸 "디스코드 한도 초과"에서 한 번만 다시 보낸다(모듈 설명)."""

    def __init__(self, inner: WebhookSender, clock: Clock) -> None:
        self._inner = inner
        self._clock = clock

    def __repr__(self) -> str:
        return "RetryingSender()"

    def send(self, url: str, text: str) -> None:
        wait = self._first_wait(url, text)
        if wait is None:
            return
        self._clock.sleep(wait)
        self._inner.send(url, text)

    def _first_wait(self, url: str, text: str) -> float | None:
        """첫 보내기. 성공이면 `None`, 한도 초과이고 기다릴 시간이 있으면 기다릴 초를 돌려준다."""
        retry_after: float | None = None
        failure: WebhookError | None = None
        try:
            self._inner.send(url, text)
        except WebhookError as exc:
            failure = exc
            retry_after = exc.retry_after
        if failure is None:
            return None
        if (
            failure.error_name != ERROR_DISCORD_RATE_LIMIT
            or retry_after is None
            or not math.isfinite(retry_after)
            or retry_after <= 0
        ):
            raise WebhookError(failure.error_name, failure.retry_after)
        return min(float(retry_after), MAX_RETRY_WAIT)


def send_alert(
    sender: WebhookSender,
    url: str,
    *,
    mode: Mode,
    category: AlertCategory,
    coin: str | None,
    text: str,
    detail: AlertDetail | None = None,
    clock: Clock | None = None,
) -> None:
    """알림 하나를 `sender`로 보낸다. `clock`을 주면 한 번 다시 보내기를 거친다."""
    message = alert_message(mode=mode, category=category, coin=coin, text=text, detail=detail)
    _through(sender, clock).send(url, message)


def send_confirmation_message(
    sender: WebhookSender, url: str, category: AlertCategory, *, clock: Clock | None = None
) -> None:
    """그 분류의 확인용 글을 `sender`로 보낸다. `clock`을 주면 한 번 다시 보내기를 거친다."""
    _through(sender, clock).send(url, confirmation_message(category))


def _through(sender: WebhookSender, clock: Clock | None) -> WebhookSender:
    return sender if clock is None else RetryingSender(sender, clock)
