"""업비트 비공개 API의 오류 이름과 세 갈래(M5 명세 3.5.5, 제품 규칙 11절).

| 갈래 | 오류 이름 | 받은 것(업비트의 오류 코드는 공식 문서의 오류 코드 목록에서 맞췄다) |
|---|---|---|
| 거래를 계속 막는 오류 | IP 미등록 | `no_authorization_ip`(다른 표기 `no_authorization_i_p`도) |
| | 키 만료 | `expired_access_key` |
| | 키 권한 부족 | `out_of_scope` |
| | 키가 맞지 않음 | `jwt_verification`, `invalid_access_key`, `invalid_query_payload`, |
| | | `no_authorization_token` |
| | 일시 차단 | HTTP 418(코드와 상관없이) |
| 잠깐의 문제이거나 주문 한 건의 문제 | 요청 한도 초과 | HTTP 429 |
| | 거래소 점검 | HTTP 503(공개 시세와 같은 가정. M1 9번) |
| | 서버 오류 | 그 밖의 HTTP 5xx |
| | 시간 초과 | 응답 기다리기의 시간 초과 |
| | 네트워크 끊김 | 연결 실패, 연결이 끊김 |
| | 응답 형식 오류 | 2xx인데 읽을 수 없는 응답(필드가 없거나 수가 아님) |
| | 잔고 부족 | `insufficient_funds_bid`, `insufficient_funds_ask` |
| | 금액·호가 단위 거절 | `under_min_total_bid`, `under_min_total_ask` |
| 목록에 없는 오류 | 알 수 없는 거래소 오류({HTTP 상태}. {오류 코드}) | 그 밖의 모든 오류 |

오류 코드가 없는 목록에 없는 오류는 `core`의 "알 수 없는 거래소 오류({HTTP 상태})"다.

- 상태 코드 418, 429, 503, 5xx를 먼저 보고, 그다음 오류 코드를 본다(5xx를 코드로 주문 거절로 읽으면
  "주문이 만들어지지 않았다"고 잘못 볼 수 있다).
- "키가 맞지 않음"은 모든 요청이 실패해 거래를 계속 막으므로 첫 갈래다(3.5.5. 11절의 첫 갈래의
  목록에 없던 이름이다).
- 응답의 오류 글(`message`)은 오류 이름으로 옮기지 않는다. 비밀 값과 경로를 `core.redact`로 지운
  글만 `UpbitError.detail`에 담는다. 응답 원문은 담지 않는다.
- `retry_after`/`blocked_until`: 418의 `Retry-After` 머리글이 양의 정수 초이면 차단이 풀리는 때
  (`clock.now()` + 그 초)를 `blocked_until`에 담는다. 없으면 `None`이다.
"""

import json
import os
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from coin_report_trader.core.redaction import redact
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
from coin_report_trader.trading.quote.errors import parse_retry_after

ERROR_IP_NOT_ALLOWED: Final = "IP 미등록"
ERROR_KEY_EXPIRED: Final = "키 만료"
ERROR_KEY_SCOPE: Final = "키 권한 부족"
ERROR_KEY_INVALID: Final = "키가 맞지 않음"
ERROR_INSUFFICIENT_FUNDS: Final = "잔고 부족"
ERROR_ORDER_REJECTED: Final = "금액·호가 단위 거절"


class ErrorBranch(StrEnum):
    """11절의 세 갈래."""

    BLOCKING = "거래를 계속 막는 오류"
    TRANSIENT = "잠깐의 문제이거나 주문 한 건의 문제"
    UNLISTED = "목록에 없는 오류"


BLOCKING_NAMES: Final = frozenset(
    {ERROR_IP_NOT_ALLOWED, ERROR_KEY_EXPIRED, ERROR_KEY_SCOPE, ERROR_KEY_INVALID, ERROR_BLOCKED}
)
TRANSIENT_NAMES: Final = frozenset(
    {
        ERROR_RATE_LIMIT,
        ERROR_MAINTENANCE,
        ERROR_SERVER,
        ERROR_TIMEOUT,
        ERROR_NETWORK,
        ERROR_BAD_RESPONSE,
        ERROR_INSUFFICIENT_FUNDS,
        ERROR_ORDER_REJECTED,
    }
)

CODE_NAMES: Final = {
    "no_authorization_ip": ERROR_IP_NOT_ALLOWED,
    "no_authorization_i_p": ERROR_IP_NOT_ALLOWED,
    "expired_access_key": ERROR_KEY_EXPIRED,
    "out_of_scope": ERROR_KEY_SCOPE,
    "jwt_verification": ERROR_KEY_INVALID,
    "invalid_access_key": ERROR_KEY_INVALID,
    "invalid_query_payload": ERROR_KEY_INVALID,
    "no_authorization_token": ERROR_KEY_INVALID,
    "insufficient_funds_bid": ERROR_INSUFFICIENT_FUNDS,
    "insufficient_funds_ask": ERROR_INSUFFICIENT_FUNDS,
    "under_min_total_bid": ERROR_ORDER_REJECTED,
    "under_min_total_ask": ERROR_ORDER_REJECTED,
}
"""업비트의 오류 코드 → 오류 이름. 여기 없는 코드는 목록에 없는 오류다."""

STATUS_BLOCKED = 418
STATUS_RATE_LIMIT = 429
STATUS_MAINTENANCE = 503


def error_unknown(status: int, code: str | None) -> str:
    """목록에 없는 오류의 이름."""
    if code:
        return f"알 수 없는 거래소 오류({status}. {code})"
    return error_unknown_exchange(status)


def branch_of(error_name: str) -> ErrorBranch:
    if error_name in BLOCKING_NAMES:
        return ErrorBranch.BLOCKING
    if error_name in TRANSIENT_NAMES:
        return ErrorBranch.TRANSIENT
    return ErrorBranch.UNLISTED


class UpbitError(Exception):
    """비공개 API 요청이 실패했다.

    - `error_name`: 위 표의 이름. `branch`: 그 갈래.
    - `status`: HTTP 상태(응답이 없었으면 `None`). `code`: 업비트의 오류 코드(없으면 `None`).
    - `unknown`: 요청이 거래소에 닿았는지 모른다(시간 초과, 보낸 뒤 끊김, 주문의 429). 주문이면
      식별값으로 조회해야 한다(3.6.3).
    - `blocked_until`: 일시 차단이 풀리는 때(응답이 주었을 때만).
    - `detail`: 응답의 오류 글을 가린 것(없으면 `None`). 예외의 글(`args`)에는 오류 이름만 든다.
    """

    def __init__(
        self,
        error_name: str,
        *,
        status: int | None = None,
        code: str | None = None,
        unknown: bool = False,
        blocked_until: datetime | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(error_name)
        self.error_name = error_name
        self.branch = branch_of(error_name)
        self.status = status
        self.code = code
        self.unknown = unknown
        self.blocked_until = blocked_until
        self.detail = detail

    def __repr__(self) -> str:
        return f"UpbitError({self.error_name!r}, status={self.status!r}, code={self.code!r})"


def _code_and_message(text: str) -> tuple[str | None, str | None]:
    """`{"error": {"name": …, "message": …}}`의 코드와 글. 꼴이 다르면 `None`."""
    try:
        parsed = json.loads(text)
    except (ValueError, RecursionError):
        return None, None
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if not isinstance(error, dict):
        return None, None
    name = error.get("name")
    message = error.get("message")
    return (
        name if isinstance(name, str) and name else None,
        message if isinstance(message, str) and message else None,
    )


def error_for_response(
    status: int,
    text: str,
    retry_after: str | None,
    *,
    now: datetime,
    secrets: tuple[str, ...] = (),
    unknown: bool = False,
) -> UpbitError:
    """2xx가 아닌 응답 하나의 `UpbitError`. 어떤 입력에도 예외를 내지 않는다(모듈 설명)."""
    code, message = _code_and_message(text)
    detail = None if message is None else redact(message, secrets, os.environ)
    blocked_until = None
    if status == STATUS_BLOCKED:
        name = ERROR_BLOCKED
        seconds = parse_retry_after(retry_after)
        if seconds is not None:
            blocked_until = now + timedelta(seconds=seconds)
    elif status == STATUS_RATE_LIMIT:
        name = ERROR_RATE_LIMIT
    elif status == STATUS_MAINTENANCE:
        name = ERROR_MAINTENANCE
    elif 500 <= status < 600:
        name = ERROR_SERVER
    elif code is not None and code in CODE_NAMES:
        name = CODE_NAMES[code]
    else:
        name = error_unknown(status, code)
    return UpbitError(
        name,
        status=status,
        code=code,
        unknown=unknown,
        blocked_until=blocked_until,
        detail=detail,
    )
