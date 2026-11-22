"""바깥과 잇는 것을 한곳에 모은다(M1 명세 3.10의 "가짜를 넣는 길"). 시험이 가짜를 넣는 자리다.

필드를 `None`으로 두면 진짜를 쓴다: `quote`는 `HttpQuoteSource`, `clock`은 `SystemClock`이다.
`webhook`과 `vault`는 27번이 쓰기 시작한다(19번은 쓰지 않는다). exe를 가짜로 돌리는 명령줄 옵션이나
환경 변수는 두지 않는다.
"""

from dataclasses import dataclass
from decimal import Decimal

from coin_report_trader.core import (
    AiRunner,
    AutostartStore,
    BookSource,
    Clock,
    QuoteSource,
    SecretStore,
    SystemClock,
    UpbitTransport,
    WebhookSender,
)


@dataclass(frozen=True)
class Services:
    quote: QuoteSource | None = None
    webhook: WebhookSender | None = None
    vault: SecretStore | None = None
    clock: Clock | None = None
    ai: AiRunner | None = None
    """2단계: 없으면 진짜 `ClaudeCodeRunner`다. 15번까지 쓰지 않는다(M2 명세 3.4)."""
    autostart: AutostartStore | None = None
    """없으면 진짜 `WindowsAutostart`다(M1 명세 3.16.3)."""
    book: BookSource | None = None
    """3단계: `None`이면 `services.quote`가 `BookSource`도 갖출 때(진짜 `HttpQuoteSource`) 그것을
    쓴다(M3 명세 3.4)."""
    min_order: Decimal | None = None
    """3단계: 시험만 넣는 최소 주문 금액. `None`이면 `MIN_ORDER_FALLBACK`(5,000원)이다."""
    exchange_transport: UpbitTransport | None = None
    """5단계: 시험이 가짜를 넣는다. `None`이면 진짜 `httpx` 운반이다(M5 명세 3.4)."""


def resolve_clock(services: Services | None) -> Clock:
    """넘긴 시계, 없으면 진짜 시계."""
    if services is not None and services.clock is not None:
        return services.clock
    return SystemClock()
