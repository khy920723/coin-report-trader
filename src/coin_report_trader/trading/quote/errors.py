"""거래소 오류 나눔(M1 명세 3.2, 3.9).

공개 시세의 응답과 예외를 `QuoteError`의 오류 이름으로 바꾼다.

| 받은 것 | 오류 이름 | `retry_after` |
|---|---|---|
| `httpx.TimeoutException`(연결·읽기·쓰기·연결 풀의 시간 초과) | 시간 초과 | 없음 |
| `httpx.DecodingError`(본문의 압축을 풀지 못함) | 응답 형식 오류 | 없음 |
| 그 밖의 `httpx.RequestError`(연결 실패, TLS 실패, 끊김, 프로토콜 오류) | 네트워크 끊김 | 없음 |
| HTTP 418 | 일시 차단 | `Retry-After` |
| HTTP 429 | 요청 한도 초과 | `Retry-After` |
| HTTP 503 | 거래소 점검 | `Retry-After` |
| 그 밖의 HTTP 5xx | 서버 오류 | `Retry-After` |
| 그 밖의 2xx가 아닌 상태(1xx, 3xx, 4xx) | 알 수 없는 거래소 오류({HTTP 상태}) | `Retry-After` |
| 2xx인데 본문을 읽지 못함(JSON이 아님, 꼴이 다름, 값이 숫자가 아님 등) | 응답 형식 오류 | 없음 |

- 상태 코드만 본다. 응답 본문의 글(오류 이름이나 문구)은 오류 이름으로 옮기지 않는다(3.9).
- `retry_after`: 응답의 `Retry-After` 헤더가 양의 정수 초(앞뒤 빈칸은 지운다, 9자리까지)일 때만
  그 초다. 헤더가 없거나, 0이거나, 날짜 꼴이거나, 소수·음수·읽지 못하는 값이면 `None`이다. 날짜 꼴은
  PC 시계에 기대야 해서 읽지 않는다. 주지 않았을 때 늘리는 차례는 `app`이 든다(M1 명세 2의 3).
- 리다이렉트는 따라가지 않으므로 3xx도 실패다.
"""

import httpx

from coin_report_trader.core.errors import QuoteError
from coin_report_trader.core.texts import (
    ERROR_BAD_RESPONSE,
    ERROR_BLOCKED,
    ERROR_MAINTENANCE,
    ERROR_NETWORK,
    ERROR_RATE_LIMIT,
    ERROR_SERVER,
    ERROR_TIMEOUT,
    error_unknown_exchange,
)

STATUS_BLOCKED = 418
STATUS_RATE_LIMIT = 429
STATUS_MAINTENANCE = 503

_MAX_RETRY_AFTER_DIGITS = 9


def parse_retry_after(value: str | None) -> float | None:
    """`Retry-After`의 양의 정수 초. 그 밖의 값은 `None`이다(모듈 설명)."""
    if value is None:
        return None
    text = value.strip()
    if not (text.isascii() and text.isdigit()):
        return None
    digits = text.lstrip("0")
    if not digits or len(digits) > _MAX_RETRY_AFTER_DIGITS:
        return None
    return float(int(digits))


def error_for_status(status: int, headers: httpx.Headers) -> QuoteError:
    """2xx가 아닌 응답 하나의 `QuoteError`. 본문은 받지 않는다(읽지 않는다)."""
    retry_after = parse_retry_after(headers.get("Retry-After"))
    if status == STATUS_BLOCKED:
        name = ERROR_BLOCKED
    elif status == STATUS_RATE_LIMIT:
        name = ERROR_RATE_LIMIT
    elif status == STATUS_MAINTENANCE:
        name = ERROR_MAINTENANCE
    elif 500 <= status < 600:
        name = ERROR_SERVER
    else:
        name = error_unknown_exchange(status)
    return QuoteError(name, retry_after)


def error_for_exception(exc: httpx.RequestError) -> QuoteError:
    """요청을 보내거나 응답을 받다가 난 `httpx` 예외의 `QuoteError`."""
    if isinstance(exc, httpx.TimeoutException):
        return QuoteError(ERROR_TIMEOUT)
    if isinstance(exc, httpx.DecodingError):
        return QuoteError(ERROR_BAD_RESPONSE)
    return QuoteError(ERROR_NETWORK)


def bad_response() -> QuoteError:
    """성공 응답의 본문을 읽지 못했다."""
    return QuoteError(ERROR_BAD_RESPONSE)
