"""공개 시세(M1 명세 3.2, 3.4): 진짜 `QuoteSource`와 감싸는 부품 `QuoteClient`.

- `HttpQuoteSource`: 거래소의 공개 시세를 부르는 진짜. 한 번 부르는 것이 요청 한 번이다.
- `QuoteClient`: 나눠 받기(3.5.3)와 요청 한도(3.10)를 맡는다. `app`은 이것만 쓴다.
- `RecentCandles`: `QuoteClient.fetch_recent`의 결과(3.4).
"""

from coin_report_trader.trading.quote.client import MAX_CANDLES, QuoteClient, RecentCandles
from coin_report_trader.trading.quote.http_source import HttpQuoteSource
from coin_report_trader.trading.quote.ratelimit import RequestLimiter

__all__ = ["MAX_CANDLES", "HttpQuoteSource", "QuoteClient", "RecentCandles", "RequestLimiter"]
