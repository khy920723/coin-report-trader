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
- 시간 제한(3.9: 연결 5초, 전체 10초): 시도 하나는 `send`를 부른 때부터 연결(이름 찾기 뒤의 TCP
  연결과 TLS 맺기)이 5초, 응답 머리글의 마지막 바이트까지 전체가 10초를 넘지 않는다. 넘으면 그
  자리에서 끝내고 "디스코드 시간 초과"를 낸다. `httpx`의 시간 제한은 단계마다의 상한이라(읽기는
  소켓에서 한 번 읽을 때마다 새로 잰다) 그것만으로는 지켜지지 않는다. 그래서 진짜 길(`transport`를
  주지 않은 `httpx.HTTPTransport`)의 연결 풀 아래 네트워크 백엔드를 `_DeadlineBackend`로 감싼다.
  - 이름을 찾은 주소마다 따로 연결하되 남은 연결 시간 안에서만 하고(주소가 여럿이어도 합이 5초),
    TLS 맺기도 같은 5초에 든다. 쓰기와 읽기는 매번 남은 전체 시간만큼만 기다린다.
  - 마감은 이 시도를 부른 스레드 안에서만 지킨다. 다른 스레드나 타이머를 띄우지 않는다. 시도가
    끝나면(성공, 실패, 시간 초과 모두) `httpx`가 응답을 닫고 연결도 닫는다(본문을 읽지 않으므로
    연결 풀로 돌아가지 않는다).
  - 못 끊는 것: 이름 찾기(`getaddrinfo`)는 스레드를 남기지 않고는 끊을 수 없다. 끝나면 남은
    시간을 다시 재고, 지났으면 연결하지 않고 바로 시간 초과로 끝낸다.
  - `httpx`의 비공개 속성(`_pool._network_backend`)에 기댄다. 그 속성이 없으면 단계마다의 상한으로
    물러서지 않고 만들 때 `RuntimeError`를 낸다(시험이 실패한다).
  - 단계마다의 상한(연결 5초, 읽기·쓰기·연결 풀 각 10초. `trading/quote`와 같다)도 그대로
    `httpx`에 걸어 둔다. 리다이렉트를 따라가지 않고, 쿠키를 두지 않고, 환경 변수의 프록시와
    인증서 설정을 읽지 않는다(`trust_env=False`). 연결은 다시 시도하지 않는다.
- TLS는 `core.make_tls_context(certifi.where())`다. 동기 `httpx.Client` 하나를 가지고 보내기
  스레드에서만 쓴다. 다 쓰면 `close()`를 부른다.

`RetryingSender(inner, clock)`는 아무 `WebhookSender`를 감싸 한 번 다시 보낸다: `inner`가
"디스코드 한도 초과"를 내면 그 `retry_after`(30초를 넘으면 30초)를 `clock.sleep`으로 기다린 뒤
한 번만 다시 보낸다. 기다릴 시간이 없으면 다시 보내지 않고 그 오류를 낸다. 그 밖의 실패는 다시
보내지 않는다. 그래서 보내기 하나의 요청은 많아야 둘이다.
"""

import math
import socket
import time
from collections.abc import Callable
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Any, Final

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


class _Deadlines:
    """시도 하나의 마감. `begin`을 부른 때(`send`를 부른 때)부터 연결 5초, 전체 10초를 잰다."""

    def __init__(self, monotonic: Callable[[], float]) -> None:
        self._monotonic = monotonic
        self._start: float | None = None

    def begin(self) -> None:
        self._start = self._monotonic()

    def end(self) -> None:
        self._start = None

    def connect_left(self) -> float:
        """연결(이름 찾기 뒤의 TCP 연결과 TLS 맺기)에 남은 초. 전체 마감도 함께 지킨다."""
        return min(self._left(CONNECT_TIMEOUT), self.total_left())

    def total_left(self) -> float:
        return self._left(TIMEOUT)

    def _left(self, limit: float) -> float:
        if self._start is None:  # 시도 밖에서는 마감이 없다(단계마다의 상한만 쓴다)
            return limit
        return self._start + limit - self._monotonic()


class _DeadlineStream:
    """연결 하나. 쓰기, 읽기, TLS 맺기를 부를 때마다 그 마감까지 남은 시간만큼만 기다린다."""

    def __init__(self, inner: Any, deadlines: _Deadlines) -> None:
        self._inner = inner
        self._deadlines = deadlines

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._inner.read(max_bytes, timeout=self._left(self._deadlines.total_left()))

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._inner.write(buffer, timeout=self._left(self._deadlines.total_left()))

    def close(self) -> None:
        self._inner.close()

    def start_tls(
        self, ssl_context: Any, server_hostname: str | None = None, timeout: float | None = None
    ) -> "_DeadlineStream":
        try:
            left = self._left(self._deadlines.connect_left())
        except TimeoutError:
            self._inner.close()
            raise
        secured = self._inner.start_tls(ssl_context, server_hostname, timeout=left)
        return _DeadlineStream(secured, self._deadlines)

    def get_extra_info(self, info: str) -> Any:
        return self._inner.get_extra_info(info)

    @staticmethod
    def _left(left: float) -> float:
        if left <= 0:
            raise TimeoutError
        return left


class _DeadlineBackend:
    """연결 풀의 네트워크 백엔드를 감싼다. 이름을 찾은 주소마다 따로, 남은 연결 시간 안에서만
    연결한다(주소가 여럿이어도 합이 연결 5초)."""

    def __init__(
        self,
        inner: Any,
        deadlines: _Deadlines,
        resolve: Callable[..., list[tuple[Any, ...]]],
    ) -> None:
        self._inner = inner
        self._deadlines = deadlines
        self._resolve = resolve

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> _DeadlineStream:
        # 이름 찾기는 끊을 수 없다. 끝난 뒤에 남은 시간을 다시 잰다.
        found = self._resolve(host, port, type=socket.SOCK_STREAM)
        last: BaseException | None = None
        for item in found:
            left = self._deadlines.connect_left()
            if left <= 0:
                raise TimeoutError
            try:
                stream = self._inner.connect_tcp(
                    item[4][0], port, timeout=left, local_address=local_address,
                    socket_options=socket_options,
                )  # fmt: skip
            except Exception as exc:
                last = exc
                continue
            return _DeadlineStream(stream, self._deadlines)
        if last is not None:
            raise last
        raise OSError("이름에 맞는 주소가 없습니다")

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


def _install_deadlines(
    transport: httpx.HTTPTransport,
    deadlines: _Deadlines,
    resolve: Callable[..., list[tuple[Any, ...]]],
    backend: Any,
) -> None:
    """연결 풀의 네트워크 백엔드를 `_DeadlineBackend`로 바꾼다. 못 하면 `RuntimeError`다."""
    pool = getattr(transport, "_pool", None)
    inner = backend if backend is not None else getattr(pool, "_network_backend", None)
    if pool is None or inner is None or not hasattr(pool, "_network_backend"):
        raise RuntimeError("httpx의 연결 풀에서 네트워크 백엔드를 찾지 못해 마감을 걸 수 없습니다")
    pool._network_backend = _DeadlineBackend(inner, deadlines, resolve)


class HttpWebhookSender:
    """`WebhookSender`의 진짜(모듈 설명). `transport`는 시험이 `MockTransport`를 넣는 길이다.

    `monotonic`, `resolve`, `network_backend`는 시험이 시계, 이름 찾기, 소켓을 가짜로 바꾸는 길이다
    (`transport`가 없을 때만 쓴다).
    """

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        monotonic: Callable[[], float] | None = None,
        resolve: Callable[..., list[tuple[Any, ...]]] | None = None,
        network_backend: Any = None,
    ) -> None:
        self._deadlines: _Deadlines | None = None
        if transport is None:
            real = httpx.HTTPTransport(
                verify=make_tls_context(certifi.where()), retries=0, trust_env=False
            )
            self._deadlines = _Deadlines(monotonic or time.monotonic)
            _install_deadlines(
                real, self._deadlines, resolve or socket.getaddrinfo, network_backend
            )
            transport = real
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
        if self._deadlines is not None:
            self._deadlines.begin()
        try:
            request = self._client.build_request("POST", target, json=payload)
            response = self._client.send(request, stream=True)
            try:
                status = response.status_code
                retry_after = parse_retry_after(response.headers.get("Retry-After"))
            finally:
                response.close()
        except (httpx.TimeoutException, TimeoutError):
            error_name = ERROR_DISCORD_TIMEOUT
        except Exception:
            error_name = ERROR_DISCORD_NETWORK
        finally:
            if self._deadlines is not None:
                self._deadlines.end()
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
