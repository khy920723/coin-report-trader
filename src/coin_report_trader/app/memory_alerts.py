"""메모리 알림(M1 명세 3.9): 표에 쓰지 못해 자료 스레드가 메모리에 둔 A-29.

번호는 음수다. 한 실행 안에서 -1부터 하나씩 줄여 주고 지운 번호를 다시 주지 않는다. 화면에는
표의 알림과 같이 `AlertView`로 보인다. 자료 스레드만 만진다.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from coin_report_trader.core import AlertCategory, AlertDetail, AlertView, Mode
from coin_report_trader.storage.states import DiscordState


@dataclass
class MemoryAlert:
    id: int
    created_at: datetime
    mode: Mode
    category: AlertCategory
    code: str
    coin: str | None
    text: str
    detail: AlertDetail | None
    seen: bool = False
    discord_state: DiscordState | None = None
    """보낸 결과가 아직 없으면 `None`."""

    def view(self) -> AlertView:
        return AlertView(
            self.id,
            self.created_at,
            self.mode,
            self.category,
            self.code,
            self.coin,
            self.text,
            self.detail,
            self.seen,
            self.discord_state is DiscordState.FAILED,
        )


class MemoryAlerts:
    """한 실행의 메모리 알림과 번호."""

    def __init__(self) -> None:
        self._items: dict[int, MemoryAlert] = {}
        self._next_id = -1

    def add(
        self,
        *,
        created_at: datetime,
        mode: Mode,
        category: AlertCategory,
        code: str,
        coin: str | None,
        text: str,
        detail: AlertDetail | None,
    ) -> MemoryAlert:
        alert = MemoryAlert(self._next_id, created_at, mode, category, code, coin, text, detail)
        self._next_id -= 1  # 지운 번호도 다시 주지 않는다
        self._items[alert.id] = alert
        return alert

    def get(self, alert_id: int) -> MemoryAlert | None:
        return self._items.get(alert_id)

    def remove(self, alert_id: int) -> MemoryAlert | None:
        return self._items.pop(alert_id, None)

    def all(self) -> tuple[MemoryAlert, ...]:
        return tuple(self._items.values())

    def of_category(self, category: AlertCategory) -> tuple[MemoryAlert, ...]:
        return tuple(item for item in self._items.values() if item.category is category)

    def unseen(self) -> Mapping[AlertCategory, int]:
        counts = dict.fromkeys(AlertCategory, 0)
        for item in self._items.values():
            if not item.seen:
                counts[item.category] += 1
        return counts

    def __len__(self) -> int:
        return len(self._items)
