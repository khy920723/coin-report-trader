"""요청 한도 장치(M1 명세 3.10): 공개 시세 요청은 길이 1초인 어느 구간에도 5번을 넘지 않는다.

- 구간은 양 끝을 포함해 센다. 그래서 어느 요청이든, 그보다 다섯 번 앞의 요청을 낸 단조 시각에서
  1초보다 뒤에 낸다(꼭 1초 뒤는 안 된다).
- 최근 다섯 요청을 낸 단조 시각을 기억한다. 다섯이 찼으면 가장 앞의 것에서 1초가 넘게 지날 때까지
  `clock.sleep`으로 기다린다. 기다린 뒤에도 단조 시각을 다시 읽어 확인한다(실제 시계가 조금 일찍
  깨어나도 지킨다). 1초에 `MARGIN`을 더한 만큼 기다려 "꼭 1초 뒤"가 되지 않게 한다.
- 시간은 `Clock`의 `monotonic()`과 `sleep()`만 쓴다(PC 시계를 믿지 않는다. 가짜 시계로 시험이
  기다리지 않고 돈다).
- 실패한 요청도 낸 요청이므로 센다. 여러 스레드가 함께 불러도 된다(잠금 하나).
"""

import threading
from collections import deque

from coin_report_trader.core.clock import Clock

LIMIT = 5
"""길이 `WINDOW`인 구간에 낼 수 있는 요청 수(제품 규칙 7절의 초당 10회의 절반)."""
WINDOW = 1.0
"""구간의 길이(초)."""
MARGIN = 0.01
"""기다릴 때 더하는 초. 꼭 1초 뒤가 되지 않게 한다."""


class RequestLimiter:
    """`acquire()`를 부른 뒤에 요청 하나를 낸다."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._sent: deque[float] = deque(maxlen=LIMIT)
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """요청 하나를 내도 되는 때까지 기다리고, 그 단조 시각을 낸 요청으로 적어 돌려준다."""
        with self._lock:
            now = self._clock.monotonic()
            while len(self._sent) == LIMIT and now - self._sent[0] <= WINDOW:
                self._clock.sleep(self._sent[0] + WINDOW - now + MARGIN)
                now = self._clock.monotonic()
            self._sent.append(now)
            return now
