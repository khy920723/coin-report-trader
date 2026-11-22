"""업비트 비공개 클라이언트(M5 명세 3.5): 인증, 요청, 요청 한도, 응답 읽기, 오류 이름.

거래소의 키로 서명하는 요청은 여기서만 낸다. `app`은 이 폴더를 부르지 않고 `trading/live/`의 경계
(5단계 7번)를 거친다.
"""

from coin_report_trader.trading.upbit.auth import UpbitKeys, make_token, query_hash, query_text
from coin_report_trader.trading.upbit.client import (
    UpbitBalance,
    UpbitChance,
    UpbitClient,
    UpbitOrder,
    UpbitTrade,
)
from coin_report_trader.trading.upbit.errors import (
    ERROR_INSUFFICIENT_FUNDS,
    ERROR_IP_NOT_ALLOWED,
    ERROR_KEY_EXPIRED,
    ERROR_KEY_INVALID,
    ERROR_KEY_SCOPE,
    ERROR_ORDER_REJECTED,
    ErrorBranch,
    UpbitError,
)
from coin_report_trader.trading.upbit.limits import UpbitLimiter, parse_remaining_req
from coin_report_trader.trading.upbit.transport import (
    HttpxUpbitTransport,
    TransportError,
    TransportResponse,
    UpbitTransport,
)

__all__ = [
    "ERROR_INSUFFICIENT_FUNDS",
    "ERROR_IP_NOT_ALLOWED",
    "ERROR_KEY_EXPIRED",
    "ERROR_KEY_INVALID",
    "ERROR_KEY_SCOPE",
    "ERROR_ORDER_REJECTED",
    "ErrorBranch",
    "HttpxUpbitTransport",
    "TransportError",
    "TransportResponse",
    "UpbitBalance",
    "UpbitChance",
    "UpbitClient",
    "UpbitError",
    "UpbitKeys",
    "UpbitLimiter",
    "UpbitOrder",
    "UpbitTrade",
    "UpbitTransport",
    "make_token",
    "parse_remaining_req",
    "query_hash",
    "query_text",
]
