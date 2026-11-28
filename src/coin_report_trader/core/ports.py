"""바깥과 잇는 경계(Protocol). 가짜와 진짜가 같은 꼴을 쓴다."""

from collections.abc import Mapping
from dataclasses import dataclass, field
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
from coin_report_trader.core.alerts import AlertCategory, Mode
from coin_report_trader.core.exchange import (
    ExchangeAccount,
    ExchangeRules,
    LiveSettings,
    OrderSnapshot,
    PlaceResult,
)
from coin_report_trader.core.market import Candle, CandleUnit, Market
from coin_report_trader.core.trade import (
    FillRecord,
    OrderPlan,
    OrderRecord,
    OrderState,
    PaperAccount,
    TradeSettings,
)
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


@runtime_checkable
class MultiPriceSource(Protocol):
    """계정 화면의 현재가(M5 명세 3.4). `PriceSource`를 바꾸지 않고 새로 둔다."""

    def get_prices(self, markets: tuple[str, ...]) -> Mapping[str, Decimal]:
        """여러 마켓의 현재가를 요청 한 번으로 읽는다. 실패는 `QuoteError`다."""
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
class ExchangePort(Protocol):
    """거래소 경계(M5 명세 3.4). 모의매매는 `PaperExchange`, 실거래는 `UpbitExchange`다.
    실패는 `ExchangeError`(`error_name`은 3.5.5의 이름)다."""

    mode: Mode

    def read_account(self) -> ExchangeAccount: ...

    def read_rules(self, market: str) -> ExchangeRules: ...

    def place(self, plan: OrderPlan, uid: str) -> PlaceResult: ...

    def query(self, uid: str) -> OrderSnapshot | None:
        """`None`은 거래소에 없음."""
        ...

    def cancel(self, uid: str) -> OrderSnapshot | None: ...

    def open_orders(self, market: str) -> tuple[OrderSnapshot, ...]: ...


@runtime_checkable
class PaperStore(Protocol):
    """`PaperExchange`가 쓰는 가상 계정, 걸린 주문, 체결의 읽고 쓰기(M5 명세 3.4). `app`이
    `storage`로 갖춘다(`SecretStore`와 같은 꼴). 메서드의 뜻은 M3 명세 3.12의 표와 3.7.2다.
    이름은 3번이 정했다(판정한 때의 둘은 15번이 더했다). 쓰기의 실패는 `StorageError`다."""

    def read_account(self, mode: Mode) -> PaperAccount | None:
        """그 모드의 가상 계정. 아직 만들지 않았으면 `None`."""
        ...

    def write_account(self, mode: Mode, account: PaperAccount) -> None:
        """가상 원화, 묶인 원화, 보유(수량, 묶인 수량, 매수 합)를 한 트랜잭션으로 쓴다."""
        ...

    def insert_order(
        self, plan: OrderPlan, *, mode: Mode, uid: str, round_id: int | None, created_utc: datetime
    ) -> OrderRecord:
        """주문을 `OPEN`, `sent` 0으로 쓰고 `order_id`가 든 줄을 돌려준다."""
        ...

    def update_order(
        self,
        order_id: int,
        *,
        state: OrderState,
        closed_utc: datetime | None = None,
        error_name: str | None = None,
        sent: bool | None = None,
    ) -> None: ...

    def find_order(self, uid: str) -> OrderRecord | None:
        """식별값으로 찾는다. 없으면 `None`."""
        ...

    def open_orders(self, mode: Mode, market: str | None = None) -> tuple[OrderRecord, ...]:
        """걸린(`OPEN`) 주문. `market`이 없으면 모든 코인."""
        ...

    def apply_fill(self, fill: FillRecord, account: PaperAccount) -> FillRecord:
        """체결 한 줄, 그 주문의 `FILLED`와 닫은 때, 가상 계정을 한 트랜잭션으로 쓴다(M3 명세
        3.7.2). 새 `fill_id`가 든 줄을 돌려준다."""
        ...

    def fills_of(self, order_id: int) -> tuple[FillRecord, ...]: ...

    def read_judged_until(self, order_id: int) -> datetime | None:
        """모의 체결 판정에서 그 주문의 마지막으로 본 1분봉의 끝(`orders.judged_until_utc`. M3 명세
        3.12, 3.7.2). 그 주문이 없으면 `None`."""
        ...

    def set_judged_until(self, order_id: int, until: datetime) -> None:
        """판정한 뒤 체결되지 않은 주문의 `judged_until_utc`를 `until`로 쓴다(M3 명세 3.7.2)."""
        ...


@dataclass(frozen=True)
class TransportResponse:
    """받은 응답 하나(M5 명세 3.4)."""

    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    """머리글. 이름의 대소문자를 가리지 않고 찾으려면 `header()`를 쓴다."""
    text: str = ""

    def header(self, name: str) -> str | None:
        wanted = name.lower()
        for key, value in self.headers.items():
            if key.lower() == wanted:
                return value
        return None


class TransportError(Exception):
    """응답을 받지 못했다.

    - `timeout`: 시간이 다 되었다(참)인지, 연결이 안 되거나 끊겼다(거짓)인지.
    - `sent`: 요청이 거래소에 닿았을 수 있는지. 연결을 맺지 못했으면 거짓이다. 거짓일 때만 요청이
      나가지 않은 것이 확실하다.
    """

    def __init__(self, *, timeout: bool, sent: bool) -> None:
        super().__init__("timeout" if timeout else "network")
        self.timeout = timeout
        self.sent = sent


@runtime_checkable
class UpbitTransport(Protocol):
    """업비트 비공개 API의 운반(M5 명세 3.4). 진짜는 `httpx`, 가짜는 `tests/fakes/upbit.py`다.
    경로와 쿼리 글은 클라이언트가 만든 그대로 보낸다."""

    def request(
        self,
        method: str,
        path: str,
        *,
        query: str,
        body: str | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        """요청 하나. `query`는 `?` 없이 보낼 쿼리 글이고 비어 있으면 붙이지 않는다. 응답을 받지
        못하면 `TransportError`를 낸다. 상태 코드가 무엇이든 응답을 받았으면 돌려준다."""
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
        live: LiveSettings | None = None,
    ) -> None: ...

    def load_reports(self, market: str, mode: Mode | None = None) -> None: ...

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

    def load_history(
        self, tab: HistoryTab, market: str | None, day: date, mode: Mode | None = None
    ) -> None: ...

    def export_history(self, tab: HistoryTab, market: str | None, path: str) -> None: ...

    def save_upbit_key(self, access: str, secret: str, created: date) -> None: ...

    def delete_upbit_key(self) -> None: ...

    def prepare_live_on(self) -> None: ...

    def turn_live_on(self) -> None: ...

    def turn_live_off(self) -> None: ...

    def account_visible(self, visible: bool) -> None: ...

    def refresh_account(self) -> None: ...
