"""바깥과 잇는 경계(Protocol). 가짜와 진짜가 같은 꼴을 쓴다."""

from datetime import datetime
from typing import Protocol, runtime_checkable

from coin_report_trader.core.alerts import AlertCategory
from coin_report_trader.core.market import Candle, CandleUnit, Market


@runtime_checkable
class QuoteSource(Protocol):
    """공개 시세. 한 번 부르는 것이 거래소 요청 한 번이다. 실패하면 `QuoteError`를 낸다."""

    def list_markets(self) -> tuple[Market, ...]:
        """원화 마켓만 준다."""
        ...

    def get_candles(
        self, market: str, unit: CandleUnit, *, to: datetime | None, count: int
    ) -> tuple[Candle, ...]:
        """`to` 앞에 시작한 봉을 새것부터 `count`(200 이하)개 준다.

        `to`가 `None`이면 가장 최근 것부터 준다.
        """
        ...


@runtime_checkable
class WebhookSender(Protocol):
    """디스코드 보내기. 실패하면 `WebhookError`를 낸다."""

    def send(self, url: str, text: str) -> None: ...


@runtime_checkable
class SecretStore(Protocol):
    """비밀 값 보관소. 실패하면 `SecretStoreError`를 낸다."""

    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


@runtime_checkable
class UiActions(Protocol):
    """화면이 부르는 동작. 모두 Qt의 주 스레드에서 부르고, 일을 큐에 넣고 바로 돌아온다.
    결과는 `MainWindow.apply`로 온다."""

    def refresh_markets(self) -> None: ...

    def save_settings(
        self,
        coins: tuple[str, ...],
        retention_days: int,
        *,
        first_setup: bool,
        candle_unit: CandleUnit | None = None,
        candle_count: int | None = None,
    ) -> None: ...

    def retry_candles(self) -> None: ...

    def mark_seen(self, alert_ids: tuple[int, ...]) -> None: ...

    def delete_alert(self, alert_id: int) -> None: ...

    def clear_alerts(self, category: AlertCategory) -> None: ...

    def save_webhook(self, category: AlertCategory, url: str) -> None: ...

    def delete_webhook(self, category: AlertCategory) -> None: ...

    def send_test(self, category: AlertCategory) -> None: ...
