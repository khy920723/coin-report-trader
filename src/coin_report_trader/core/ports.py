"""바깥과 잇는 경계(Protocol). 가짜와 진짜가 같은 꼴을 쓴다."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from coin_report_trader.core.ai import (
    AiChoice,
    AiSlot,
    ClaudeState,
    ModelTokens,
    SessionOutcome,
    TokenUsage,
)
from coin_report_trader.core.alerts import AlertCategory
from coin_report_trader.core.market import Candle, CandleUnit, Market
from coin_report_trader.core.trade import TradeSettings
from coin_report_trader.core.views import HistoryTab


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
class PriceSource(Protocol):
    """현재가(M2 명세 3.4, 3.6). `QuoteSource`에 더하지 않고 따로 둔다."""

    def get_price(self, market: str) -> Decimal:
        """거래소 요청 한 번이다. 실패하면 `QuoteError`를 낸다."""
        ...


@dataclass(frozen=True)
class MinuteCandle:
    """1분봉(M3 명세 3.4). 봉 통계에 쓰지 않으므로 `Candle`과 `CandleUnit`에 더하지 않는다."""

    market: str
    start: datetime
    high: Decimal
    low: Decimal
    close: Decimal


@runtime_checkable
class BookSource(Protocol):
    """공개 시세의 더한 값(M3 명세 3.4, 3.6). 한 번 부르는 것이 요청 한 번이고 실패하면
    `QuoteError`를 낸다. `QuoteSource`와 `PriceSource`에 더하지 않고 따로 둔다."""

    def get_best_prices(self, market: str) -> tuple[Decimal, Decimal]:
        """(최우선 매도 호가, 최우선 매수 호가)."""
        ...

    def get_tick_size(self, market: str) -> Decimal: ...

    def get_minute_candles(
        self, market: str, *, to: datetime | None, count: int
    ) -> tuple[MinuteCandle, ...]:
        """`to` 앞에 시작한 1분봉을 새것부터 `count`(200 이하)개 준다."""
        ...


@dataclass(frozen=True)
class ClaudeCheck:
    """`AiRunner.check()`의 결과(3.7.1)."""

    state: ClaudeState
    version: str | None


@dataclass(frozen=True)
class SessionRequest:
    """Claude Code 세션 하나의 부탁(3.7.2)."""

    key: str
    """회차 id와 마켓 코드로 만든 이름. 폴더 이름에 쓴다."""
    market: str
    files: Mapping[str, str]
    """입력 파일 이름에서 BOM 없는 CSV 글로(3.8)."""
    choices: tuple[AiChoice, ...]
    """네 칸."""
    timeout_seconds: float


@dataclass(frozen=True)
class SessionResult:
    """세션 하나의 결과(3.7.5)."""

    outcome: SessionOutcome
    answers: Mapping[str, str]
    """답 필드 이름에서 CSV 글로. `ANSWERED`일 때만."""
    tokens: TokenUsage | None
    model_tokens: tuple[ModelTokens, ...]
    detail: str | None
    """가린 짧은 글."""
    tools: Mapping[AiSlot, tuple[str, ...]]
    """칸마다 쓴 도구의 이름."""


@runtime_checkable
class AiRunner(Protocol):
    """Claude Code 실행기(3.4, 3.7). 진짜는 `ai/claude_cli.py`, 가짜는 `tests/fakes/ai.py`."""

    def check(self) -> ClaudeCheck:
        """실행 파일, 버전, 로그인을 본다(3.7.1)."""
        ...

    def run(self, request: SessionRequest) -> SessionResult:
        """세션 하나를 끝까지 돌린다. 시간 제한을 넘기면 스스로 끝내고 돌아온다(3.7.4)."""
        ...

    def stop_all(self) -> None:
        """도는 세션의 프로세스를 모두 끝낸다."""
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
class AutostartStore(Protocol):
    """PC를 켤 때 자동 시작하는 앱의 등록(M1 명세 3.16.3). 실패하면 `OSError`를 낸다."""

    def supported(self) -> bool: ...

    def read(self) -> str | None:
        """등록된 명령의 글. 없으면 `None`."""
        ...

    def write(self, command: str) -> None: ...

    def delete(self) -> None:
        """등록을 지운다. 없으면 그대로 성공이다."""
        ...


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
        ai_choices: tuple[AiChoice, ...] | None = None,
        autostart: bool | None = None,
        trade: TradeSettings | None = None,
        accept_below_stop: tuple[str, ...] = (),
    ) -> None: ...

    def load_reports(self, market: str) -> None: ...

    def load_report(self, report_id: int) -> None: ...

    def retry_candles(self) -> None: ...

    def mark_seen(self, alert_ids: tuple[int, ...]) -> None: ...

    def delete_alert(self, alert_id: int) -> None: ...

    def clear_alerts(self, category: AlertCategory) -> None: ...

    def save_webhook(self, category: AlertCategory, url: str) -> None: ...

    def delete_webhook(self, category: AlertCategory) -> None: ...

    def send_test(self, category: AlertCategory) -> None: ...

    def pause(self) -> None: ...

    def pause_and_sell_all(self) -> None: ...

    def resume_user(self) -> None: ...

    def resume_error(self) -> None: ...

    def load_history(self, tab: HistoryTab, market: str | None, day: date) -> None: ...

    def export_history(self, tab: HistoryTab, market: str | None, path: str) -> None: ...
