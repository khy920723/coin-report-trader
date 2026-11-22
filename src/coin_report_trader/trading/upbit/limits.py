"""업비트 비공개 API의 요청 한도(M5 명세 3.5.3). 공개 시세의 한도 장치(M1 명세 3.10)와 따로 센다.

- 그룹: 주문(`order`. 주문 내기)과 그 밖의 거래 그룹(`default`. 계정, 주문 가능 정보, 조회, 목록,
  취소). 응답을 받기 전의 처음 한도는 그룹마다 길이 1초인 어느 구간에도 5회다(공식 문서의 한도보다
  낮다: 주문 그룹 초당 12회, 그 밖 초당 30회). 구간은 양 끝을 포함해 센다(공개 시세와 같은 셈).
- `Remaining-Req` 머리글(`group=…; min=…; sec=…`)의 `group`과 `sec`를 읽는다(`min`은 쓰지 않는다).
  `sec`가 0이면 그 그룹은 다음 초가 시작될 때까지 내지 않는다(지휘가 정한 것). 머리글의 그룹이
  위 둘이 아니면 그 요청의 그룹으로 본다.
- 429를 받은 그룹도 다음 초가 시작될 때까지 내지 않는다(다시 보내는지는 `UpbitClient`가 정한다).
- "다음 초"는 PC 시계를 믿지 않고(`CLAUDE.md`, 공개 시세의 장치와 같다) 단조 시각으로 잰다: 멈춘
  때부터 1초에 `MARGIN`을 더한 뒤다. PC 시계가 거래소와 1초 안에서 어긋나도 거래소의 같은 초에
  다시 내지 않는다. 기다림은 `clock.monotonic()`과 `clock.sleep()`만으로 한다(시험이 가짜 시계로
  돈다).
"""

import math
import threading
from collections import deque
from typing import Final

from coin_report_trader.core.clock import Clock

GROUP_ORDER: Final = "order"
GROUP_DEFAULT: Final = "default"
GROUPS: Final = (GROUP_ORDER, GROUP_DEFAULT)
LIMIT_PER_SECOND: Final = 5
WINDOW: Final = 1.0
MARGIN: Final = 0.01
_MAX_SEC_DIGITS = 6


def parse_remaining_req(value: str | None) -> tuple[str | None, int | None]:
    """`Remaining-Req`의 (그룹, `sec`). 읽을 수 없는 값은 `None`이다."""
    if not value:
        return None, None
    fields: dict[str, str] = {}
    for part in value.split(";"):
        key, separator, item = part.partition("=")
        if separator:
            fields.setdefault(key.strip().lower(), item.strip())
    group = fields.get("group") or None
    sec_text = fields.get("sec", "")
    readable = sec_text.isascii() and sec_text.isdigit() and len(sec_text) <= _MAX_SEC_DIGITS
    return group, int(sec_text) if readable else None


class _Bucket:
    def __init__(self) -> None:
        self.sent: deque[float] = deque(maxlen=LIMIT_PER_SECOND)
        self.paused_until = -math.inf


class UpbitLimiter:
    """그룹마다의 한도(모듈 설명). 여러 스레드가 함께 불러도 된다(잠금 하나)."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._buckets = {group: _Bucket() for group in GROUPS}
        self._lock = threading.Lock()

    def acquire(self, group: str) -> float:
        """그 그룹에 요청 하나를 내도 되는 때까지 기다리고, 그 단조 시각을 적어 돌려준다."""
        bucket = self._bucket(group)
        with self._lock:
            while True:
                now = self._clock.monotonic()
                if now < bucket.paused_until:
                    self._clock.sleep(bucket.paused_until - now)
                    continue
                if len(bucket.sent) == LIMIT_PER_SECOND and now - bucket.sent[0] <= WINDOW:
                    self._clock.sleep(bucket.sent[0] + WINDOW - now + MARGIN)
                    continue
                bucket.sent.append(now)
                return now

    def observe(self, group: str, remaining_req: str | None) -> None:
        """응답의 `Remaining-Req`를 반영한다. `sec`가 0이면 그 그룹을 다음 초까지 멈춘다."""
        header_group, sec = parse_remaining_req(remaining_req)
        if sec != 0:
            return
        self.pause_until_next_second(header_group if header_group in GROUPS else group)

    def pause_until_next_second(self, group: str) -> None:
        """그 그룹을 다음 초까지(단조 시각으로 1초 + `MARGIN` 뒤까지) 멈춘다(429, `sec` 0)."""
        bucket = self._bucket(group)
        with self._lock:
            resume = self._clock.monotonic() + WINDOW + MARGIN
            bucket.paused_until = max(bucket.paused_until, resume)

    def paused_until(self, group: str) -> float:
        """시험이 보는 값: 그 그룹이 멈춘 단조 시각의 끝(멈추지 않았으면 `-inf`)."""
        with self._lock:
            return self._bucket(group).paused_until

    def _bucket(self, group: str) -> _Bucket:
        try:
            return self._buckets[group]
        except KeyError:
            raise ValueError(f"모르는 한도 그룹이다: {group}") from None
