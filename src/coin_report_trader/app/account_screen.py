"""5단계 잇기 3: 계정 화면의 읽는 때와 실행 자리(M5 명세 3.15.4, 3.15.5. 15번).

자료 스레드(`DataWorker`)가 상속한다. `account_visible`과 `refresh_account`는 주 스레드에서 큐에
넣고, 나머지는 자료 스레드에서만 부른다. 읽기와 셈은 `trading/live/account_view.py`의 부품을
부르기만 하고(`account_price_markets`, `build_account_view`), 매매 엔진과 따로다: 외부 변동,
A-6·A-22, `live_snapshots`·`external_changes`를 쓰지 않고 DB의 어느 표에도 쓰지 않는다.
주문하지 않는다.

- 때: 보이게 될 때 한 번, "새로 고침", 보이는 동안 60초마다(자료 스레드의 '다음 할 때'). 보이지
  않으면 읽지 않는다.
- 매매가 먼저: 같은 때(1초 안)에 회차나 1분 점검이 있으면 그 일을 먼저 하고 이번 읽기는 건너뛴다
  (다음 60초의 때에 읽는다. 그 때를 1초 늦춰 같은 때가 되풀이되지 않게 한다). 때가 다르면 매매의
  일 뒤에 읽는다. 60초마다의 때는 앞의 때에서 센다(늦게 읽어도 다음 때를 뒤로 밀지 않는다).
- 한 번에 하나: 읽는 중에는 `reading`이 참이고 "새로 고침"을 받지 않는다.
- 키는 매매 엔진과 같은 `UpbitClient`다(3.5.3). 키를 저장하거나 지우면 다시 읽는다.
- 요청하지 않는 때: 처음 설정 전(`BEFORE_SETUP`), 키 없음(`NO_KEY`), 오류 멈춤의 원인이 일시
  차단(`BLOCKED`).
- 오류(3.15.5): 어떤 까닭으로 실패해도 이 화면의 '읽기 실패'(`FAILED`)일 뿐이고 알림, 오류 멈춤,
  디스코드, 문제 보고 표의 줄을 만들지 않는다. 429와 418은 요청 한도 장치에 남고, 실거래 중의 418은
  매매의 다음 요청에 일시 차단으로 걸린다(`_live_block_pending`).
- 로그에는 경로(쿼리 글 없이), HTTP 상태, 오류 이름, 예외의 종류만 쓴다.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Final

from coin_report_trader.core import (
    ErrorPauseCause,
    Mode,
    QuoteError,
)
from coin_report_trader.core.texts import ERROR_BLOCKED
from coin_report_trader.core.views import AccountState, AccountView
from coin_report_trader.trading.live import account_price_markets, build_account_view
from coin_report_trader.trading.upbit import UpbitClient, UpbitError

log = logging.getLogger(__name__)

ACCOUNT_INTERVAL: Final = 60.0
"""보이는 동안 계정 화면을 읽는 간격(초. 3.15.4)."""
SAME_TIME: Final = 1.0
"""매매의 일과 '같은 때'로 보는 단조 시각의 차(초)."""
_ACCOUNTS_PATH: Final = "/v1/accounts"
_PRICES_PATH: Final = "/v1/ticker"


@dataclass(frozen=True)
class AccountTask:
    """`account_visible`(보임과 숨음)과 `refresh_account`(`visible`이 `None`)."""

    visible: bool | None


def _empty(state: AccountState) -> AccountView:
    return AccountView(
        state=state,
        krw_available=None,
        krw_locked=None,
        holdings_value=None,
        total_assets=None,
        total_buy=None,
        total_pnl=None,
        total_pnl_rate=None,
        rows=(),
        excluded_value=0,
        excluded_pnl=0,
        read_utc=None,
        error_name=None,
        reading=False,
    )


class AccountMixin:
    """계정 화면의 읽기. `DataWorker`의 일부다."""

    _clock: Any
    _settings: Any
    _client: Any
    _pause: Any
    _mode: Mode
    _markets_listed: Any
    _post: Callable[..., None]
    _put: Callable[..., None]
    _key_client: Callable[[], UpbitClient | None]
    _is_blocked: Callable[[], bool]
    _live_block_pending: Any

    def _init_account(self) -> None:
        self._acct_visible = False
        self._acct_next_mono: float | None = None
        self._acct_busy = threading.Event()
        """읽는 중(주 스레드의 `refresh_account`가 본다)."""
        self._acct_last: AccountView | None = None
        """마지막으로 읽은 `OK`의 값(읽기 실패와 일시 차단에서 흐리게 둔다)."""

    # --- 주 스레드: 동작 -----------------------------------------------------------------------

    def account_visible(self, visible: bool) -> None:
        self._put(AccountTask(bool(visible)))

    def refresh_account(self) -> None:
        """ "새로 고침". 읽는 중이면 아무것도 하지 않는다(3.15.4)."""
        if self._acct_busy.is_set():
            return
        self._put(AccountTask(None))

    # --- 자료 스레드 ---------------------------------------------------------------------------

    def _run_account_task(self, task: AccountTask) -> None:
        if task.visible is None:
            if self._acct_visible:
                self._account_read()
            return
        if task.visible == self._acct_visible:
            return  # 보임이 바뀌지 않았다
        self._acct_visible = task.visible
        if task.visible:
            self._account_read()
        else:
            self._acct_next_mono = None

    def _account_due(self, now_mono: float) -> bool:
        due = self._acct_next_mono
        return self._acct_visible and due is not None and now_mono >= due

    def _account_skip_for(self, trade_due: float | None) -> None:
        """매매의 일이 같은 때에 있으면 이번 읽기를 건너뛴다(다음 60초의 때. 3.15.4)."""
        due = self._acct_next_mono
        if due is None or trade_due is None or not self._acct_visible:
            return
        if abs(due - trade_due) < SAME_TIME:
            self._acct_next_mono = due + ACCOUNT_INTERVAL + SAME_TIME

    def _on_key_changed(self) -> None:
        """키를 저장하거나 지운 뒤: 보이면 다시 읽는다(들고 있던 값은 이미 버렸다)."""
        self._acct_last = None
        if self._acct_visible:
            self._account_read()

    def _account_blocked(self) -> bool:
        """오류 멈춤의 원인이 일시 차단이면 읽지 않는다(3.15.4)."""
        state = self._pause
        exchange = state.error and state.error_cause is ErrorPauseCause.EXCHANGE
        return bool(exchange and state.error_detail == ERROR_BLOCKED)

    def _account_tick_read(self) -> None:
        """60초마다의 때의 읽기. 다음 때는 이번 때에서 센다."""
        due = self._acct_next_mono
        self._account_read()
        if due is None or self._acct_next_mono is None:
            return
        now = self._clock.monotonic()
        following = due + ACCOUNT_INTERVAL
        self._acct_next_mono = following if following > now else now + ACCOUNT_INTERVAL

    def _account_read(self) -> None:
        self._acct_next_mono = None
        settings = self._settings
        if settings is None or not settings.first_setup_done:
            self._post(replace(_empty(AccountState.BEFORE_SETUP), as_of=self._clock.now()))
            return
        if self._account_blocked():
            self._acct_next_mono = self._clock.monotonic() + ACCOUNT_INTERVAL
            self._post(self._account_dimmed(AccountState.BLOCKED, None))
            return
        client = self._key_client()
        if client is None:
            self._acct_last = None
            self._post(replace(_empty(AccountState.NO_KEY), as_of=self._clock.now()))
            return
        self._acct_busy.set()
        try:
            if self._acct_last is None:
                self._post(replace(_empty(AccountState.LOADING), reading=True, as_of=self._now()))
            else:
                self._post(replace(self._acct_last, reading=True, as_of=self._now()))
            view = self._account_view(client)
        finally:
            self._acct_busy.clear()
            self._acct_next_mono = self._clock.monotonic() + ACCOUNT_INTERVAL
        self._post(view)

    def _now(self) -> Any:
        return self._clock.now()

    def _account_view(self, client: UpbitClient) -> AccountView:
        """읽고 셈한다. 어떤 실패든 '읽기 실패'의 값이다(알림 없음. 3.15.5)."""
        path = _ACCOUNTS_PATH
        try:
            balances = client.accounts()
            path = "markets"
            listed = self._markets_listed
            if listed is None:
                markets = self._client.list_markets()
                listed = frozenset(market.market for market in markets)
                self._markets_listed = listed
            wanted = account_price_markets(balances, listed)
            path = _PRICES_PATH
            prices = None
            if not self._is_blocked():
                try:
                    prices = self._client.read_prices(wanted)
                except QuoteError as exc:
                    self._log_failed(path, None, exc.error_name, exc)
            now = self._clock.now()
            coins = () if self._settings is None else tuple(self._settings.coins)
            view = build_account_view(balances, prices, listed, coins, now)
        except UpbitError as exc:
            self._log_failed(path, exc.status, exc.error_name, exc)
            if exc.error_name == ERROR_BLOCKED and self._mode is Mode.LIVE:
                seconds = None
                if exc.blocked_until is not None:
                    seconds = max(0.0, (exc.blocked_until - self._clock.now()).total_seconds())
                self._live_block_pending = (seconds,)
            return self._account_dimmed(AccountState.FAILED, exc.error_name)
        except QuoteError as exc:
            self._log_failed(path, None, exc.error_name, exc)
            return self._account_dimmed(AccountState.FAILED, exc.error_name)
        except Exception as exc:
            name = type(exc).__name__
            self._log_failed(path, None, name, exc)
            return self._account_dimmed(AccountState.FAILED, name)
        view = replace(view, as_of=self._clock.now())
        self._acct_last = view
        return view

    def _account_dimmed(self, state: AccountState, error_name: str | None) -> AccountView:
        """마지막으로 읽은 값을 흐리게 둔 값(읽은 적이 없으면 금액 칸이 비어 있다)."""
        last = self._acct_last
        base = _empty(state) if last is None else last
        return replace(base, state=state, error_name=error_name, reading=False, as_of=self._now())

    def _log_failed(
        self, path: str, status: int | None, error_name: str, exc: BaseException
    ) -> None:
        log.warning(
            "계정 화면의 읽기가 실패했습니다: 경로 %s, HTTP 상태 %s, 오류 이름 %s, 예외 %s",
            path,
            "-" if status is None else status,
            error_name,
            type(exc).__name__,
        )
