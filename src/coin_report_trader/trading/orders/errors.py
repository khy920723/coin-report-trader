"""거래소 오류의 세 갈래(M3 명세 3.8.4, 제품 규칙 11절. 3단계의 오류 이름 표).

3단계에 만나는 거래소 오류는 공개 시세의 것뿐이고, 오류 이름은 M1 명세 3.9의 것이다.

- 거래를 계속 막는 오류: "일시 차단". 오류 멈춤(3.10.2)과 A-23.
- 잠깐의 문제: "요청 한도 초과", "거래소 점검", "서버 오류", "시간 초과", "네트워크 끊김",
  "응답 형식 오류". 그 일만 실패로 두고 다음 회차나 다음 1분 점검에서 다시 한다.
- 목록에 없는 오류: "알 수 없는 거래소 오류({HTTP 상태})" 등. 그 일만 실패로 두고 알린다.

갈래의 값은 5단계의 업비트 클라이언트와 같은 `ErrorBranch`(`trading/upbit/`)다. 거래 API의
오류 이름은 5·6단계 명세가 이 표에 더한다.
"""

from typing import Final

from coin_report_trader.core.texts import (
    ERROR_BAD_RESPONSE,
    ERROR_BLOCKED,
    ERROR_MAINTENANCE,
    ERROR_NETWORK,
    ERROR_RATE_LIMIT,
    ERROR_SERVER,
    ERROR_TIMEOUT,
)
from coin_report_trader.trading.upbit.errors import ErrorBranch

QUOTE_BLOCKING_NAMES: Final = frozenset({ERROR_BLOCKED})
QUOTE_TRANSIENT_NAMES: Final = frozenset(
    {
        ERROR_RATE_LIMIT,
        ERROR_MAINTENANCE,
        ERROR_SERVER,
        ERROR_TIMEOUT,
        ERROR_NETWORK,
        ERROR_BAD_RESPONSE,
    }
)


def error_branch(error_name: str) -> ErrorBranch:
    """공개 시세 오류 이름의 갈래(3.8.4의 표). 표에 없는 이름은 목록에 없는 오류다."""
    if error_name in QUOTE_BLOCKING_NAMES:
        return ErrorBranch.BLOCKING
    if error_name in QUOTE_TRANSIENT_NAMES:
        return ErrorBranch.TRANSIENT
    return ErrorBranch.UNLISTED
