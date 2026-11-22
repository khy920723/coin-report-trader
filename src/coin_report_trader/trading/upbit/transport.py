"""업비트 비공개 API의 운반(M5 명세 3.4, 3.5).

- `UpbitTransport`(`Protocol`): 요청 하나를 보내고 응답(상태, 머리글, 본문 글)을 돌려준다.
  - 경로와 쿼리 글은 클라이언트가 만든 그대로 보낸다(3.5.1). 운반은 글을 바꾸지 않는다.
  - 응답을 받지 못하면 `TransportError`를 낸다. 상태 코드가 무엇이든 응답을 받았으면 돌려준다.
  - 이 번호(1번)에서는 여기 두고, 5단계 3번이 `core/ports.py`로 옮긴 뒤 이 모듈은 그것을
    다시 내보낸다.
- `HttpxUpbitTransport`: 진짜 운반이다. 공개 시세 클라이언트와 같은 TLS 설정(`core.make_tls_context`
  와 `certifi`의 묶음), 시간 제한(연결 5초, 읽기 10초), 다시 보내지 않음, 리다이렉트를 따라가지
  않음, 쿠키를 두지 않음, 환경 변수의 프록시·인증서 설정을 읽지 않음(`trust_env=False`)이다.
  시험은 이것 대신 `tests/fakes/upbit.py`의 가짜를 넣는다.
"""

import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Protocol, runtime_checkable

import certifi
import httpx

from coin_report_trader.core.tls import make_tls_context

DEFAULT_BASE_URL = "https://api.upbit.com"
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 10.0


@dataclass(frozen=True)
class TransportResponse:
    """받은 응답 하나."""

    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    """머리글. 이름의 대소문자를 가리지 않고 찾으려면 `header()`를 쓴다."""
    text: str = ""

    def header(self, name: str) -> str | None:
        wanted = name.lower()
        for key, value in self.headers.items():
            if key.lower() == wanted:
                return value
        return None


class TransportError(Exception):
    """응답을 받지 못했다.

    - `timeout`: 시간이 다 되었다(참)인지, 연결이 안 되거나 끊겼다(거짓)인지.
    - `sent`: 요청이 거래소에 닿았을 수 있는지. 연결을 맺지 못했으면 거짓이다. 거짓일 때만 요청이
      나가지 않은 것이 확실하다.
    """

    def __init__(self, *, timeout: bool, sent: bool) -> None:
        super().__init__("timeout" if timeout else "network")
        self.timeout = timeout
        self.sent = sent


@runtime_checkable
class UpbitTransport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        query: str,
        body: str | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        """요청 하나. `query`는 `?` 없이 보낼 쿼리 글이고 비어 있으면 붙이지 않는다."""
        ...


def default_tls_context() -> ssl.SSLContext:
    return make_tls_context(certifi.where())


class HttpxUpbitTransport:
    """진짜 운반(`httpx`). 한 스레드에서만 쓴다. 다 쓰면 `close()`를 부른다.

    `transport`는 시험이 `httpx.MockTransport`를 넣는 길이다(소켓을 열지 않는다).
    """

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        if base_url.endswith("/"):
            raise ValueError("base_url은 끝의 /없이 준다")
        if transport is None:
            transport = httpx.HTTPTransport(
                verify=default_tls_context(), retries=0, trust_env=False
            )
        self._base_url = base_url
        self._client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
            follow_redirects=False,
            cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=())),
            trust_env=False,
        )

    def __repr__(self) -> str:
        return "HttpxUpbitTransport()"

    def close(self) -> None:
        self._client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        query: str,
        body: str | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        url = f"{self._base_url}{path}?{query}" if query else f"{self._base_url}{path}"
        # 실패의 종류만 담고 except 밖에서 낸다. httpx의 예외는 요청(머리글의 토큰 포함)을 들고
        # 있으므로 `TransportError`에 잇지 않는다(`__context__`가 비어 있다).
        failure: tuple[bool, bool] | None = None
        response: httpx.Response | None = None
        try:
            response = self._client.request(
                method,
                url,
                content=None if body is None else body.encode("utf-8"),
                headers=dict(headers),
            )
        except (httpx.ConnectTimeout, httpx.PoolTimeout):
            failure = (True, False)
        except httpx.TimeoutException:
            failure = (True, True)
        except httpx.ConnectError:
            failure = (False, False)
        except httpx.RequestError:
            failure = (False, True)
        if failure is not None or response is None:
            timeout, sent = failure if failure is not None else (False, True)
            raise TransportError(timeout=timeout, sent=sent)
        return TransportResponse(
            status=response.status_code,
            headers=dict(response.headers.items()),
            text=response.text,
        )
