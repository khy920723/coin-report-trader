"""모의매매의 거래소 경계 `PaperExchange`(M5 명세 3.4. 3단계 엔진을 `PaperStore`로 감쌈).

`ExchangePort`를 갖춘다. 3단계의 동작(M3 명세 3.7)을 바꾸지 않는다. DB를 열지 않고, 가상 계정과
주문과 체결은 `app`이 넘긴 `PaperStore`로 읽고 쓴다(3.2). 공개 시세(최우선 호가와 1분봉)는
`QuoteClient`로 읽는다(요청 한도는 봉 받기와 같은 장치. M3 명세 3.6).

- 계정: 가상 계정의 원화에서 묶인 원화를 뺀 것이 주문 가능 원화(`krw`)이고, 보유마다 팔 수 있는
  수량(`balance`)과 묶인 수량(`locked`), 평균 매수가다. 수량이 0인 줄은 넣지 않는다.
- 규칙: 수수료율은 매수·매도 모두 `PAPER_FEE_RATE`, 최소 주문 금액은 `min_order`(없으면
  `MIN_ORDER_FALLBACK` 5,000원)다.
- 주문(`place`): `app`이 식별값과 주문 값을 먼저 `orders`에 쓴 뒤(3.8.3) 부른다. 최우선 호가를 읽어
  넣을 때의 바로 체결이면 체결 한 줄과 계정을 한 트랜잭션으로 쓰고(`PaperStore.apply_fill`), 아니면
  묶음을 더해 계정을 쓴다. 최우선 호가를 읽지 못하면 내지 않고 그 오류 이름을 준다(A-2).
- 조회(`query`): 걸린 지정가 주문을 1분봉으로 판정한다(M3 명세 3.7.2). 마지막으로 본 1분봉의 끝은
  이 객체가 기억하고, 모르면(다시 켬) 건 때를 올림한 분부터 본다(3.7.4의 소급 판정과 같다).
- 취소(`cancel`): 취소하기 전에 한 번 더 판정한다. 체결이면 취소하지 않고 체결로 쓴다. 1분봉을 받지
  못하면 취소하지 않고 걸린 그대로 준다(취소가 확인되지 않은 주문. M3 명세 3.7.2).
- 모의매매에는 부분 체결, 취소 실패, 주문 거절, 응답 없음이 없다(9절).
"""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from coin_report_trader.core.alerts import Mode
from coin_report_trader.core.clock import Clock
from coin_report_trader.core.errors import ExchangeError, QuoteError
from coin_report_trader.core.exchange import (
    ExchangeAccount,
    ExchangeHolding,
    ExchangeRules,
    ExchangeTrade,
    OrderSnapshot,
    PlaceResult,
)
from coin_report_trader.core.ports import MinuteCandle, PaperStore
from coin_report_trader.core.trade import (
    MIN_ORDER_FALLBACK,
    PAPER_FEE_RATE,
    FillRecord,
    OrderPlan,
    OrderRecord,
    OrderState,
    PaperAccount,
)
from coin_report_trader.trading.paper.account import apply_fill, lock_order, unlock_order
from coin_report_trader.trading.paper.fills import (
    Fill,
    PendingOrder,
    fill_on_place,
    first_judged_until,
    judge_fills,
    judge_window,
)

if TYPE_CHECKING:
    from coin_report_trader.trading.quote import QuoteClient

PAPER_TRADE_KEY = "paper"
"""모의매매 체결의 `trade_key`(M5 명세 3.4, 3.9)."""


class PaperExchange:
    """모의매매의 거래소 경계(모듈 설명)."""

    mode = Mode.PAPER

    def __init__(
        self,
        store: PaperStore,
        quotes: "QuoteClient",
        clock: Clock,
        *,
        min_order: Decimal | None = None,
    ) -> None:
        self._store = store
        self._quotes = quotes
        self._clock = clock
        self._min_order = MIN_ORDER_FALLBACK if min_order is None else min_order
        self._judged: dict[str, datetime] = {}

    def __repr__(self) -> str:
        return "PaperExchange()"

    def read_account(self) -> ExchangeAccount:
        account = self._account()
        holdings = tuple(
            ExchangeHolding(
                market=holding.market,
                balance=holding.qty - holding.locked_qty,
                locked=holding.locked_qty,
                avg_buy_price=holding.avg_price,
            )
            for holding in account.holdings
            if holding.qty > 0
        )
        return ExchangeAccount(
            krw=account.krw - account.locked_krw,
            krw_locked=account.locked_krw,
            holdings=holdings,
            read_utc=self._clock.now(),
        )

    def read_rules(self, market: str) -> ExchangeRules:
        del market
        return ExchangeRules(
            bid_fee=PAPER_FEE_RATE,
            ask_fee=PAPER_FEE_RATE,
            min_total=self._min_order,
            max_total=None,
        )

    def place(self, plan: OrderPlan, uid: str) -> PlaceResult:
        order = self._store.find_order(uid)
        if order is None or order.state is not OrderState.OPEN:
            raise ValueError("식별값과 주문 값을 먼저 걸림으로 써야 한다(3.8.3)")
        if (order.market, order.side, order.kind, order.price, order.qty) != (
            plan.market,
            plan.side,
            plan.kind,
            plan.price,
            plan.qty,
        ):
            raise ValueError("저장한 주문과 낼 주문이 다르다")
        failed: QuoteError | None = None
        best = (Decimal(0), Decimal(0))
        try:
            best = self._quotes.read_best_prices(plan.market)
        except QuoteError as exc:
            failed = exc
        if failed is not None:
            return PlaceResult(snapshot=None, error_name=failed.error_name, unknown=False)
        ask, bid = best
        fill = fill_on_place(order, ask, bid, self._clock.now())
        if fill is not None:
            return PlaceResult(snapshot=self._settle(order, fill), error_name=None, unknown=False)
        self._store.write_account(Mode.PAPER, lock_order(self._account(), order))
        self._judged[uid] = first_judged_until(order.created_utc)
        return PlaceResult(snapshot=_snapshot(order, ()), error_name=None, unknown=False)

    def query(self, uid: str) -> OrderSnapshot | None:
        order = self._store.find_order(uid)
        if order is None:
            return None
        if order.state is not OrderState.OPEN:
            return self._closed_snapshot(order)
        minutes, until = self._minutes(order)
        return self._judge(order, minutes, until)

    def cancel(self, uid: str) -> OrderSnapshot | None:
        order = self._store.find_order(uid)
        if order is None:
            return None
        if order.state is not OrderState.OPEN:
            return self._closed_snapshot(order)
        failed = False
        minutes: tuple[MinuteCandle, ...] = ()
        until: datetime | None = None
        try:
            minutes, until = self._minutes(order)
        except ExchangeError:
            failed = True
        if failed:
            return _snapshot(order, ())  # 판정하지 못하면 취소하지 않는다(걸린 그대로)
        judged = self._judge(order, minutes, until)
        if judged.state is not OrderState.OPEN:
            return judged
        now = self._clock.now()
        self._store.write_account(Mode.PAPER, unlock_order(self._account(), order))
        self._store.update_order(order.order_id, state=OrderState.CANCELED, closed_utc=now)
        self._judged.pop(uid, None)
        return _snapshot(replace(order, state=OrderState.CANCELED, closed_utc=now), ())

    def open_orders(self, market: str) -> tuple[OrderSnapshot, ...]:
        return tuple(_snapshot(order, ()) for order in self._store.open_orders(Mode.PAPER, market))

    # --- 안 -------------------------------------------------------------------------------

    def _account(self) -> PaperAccount:
        account = self._store.read_account(Mode.PAPER)
        if account is None:
            raise ValueError("가상 계정이 아직 없다(처음 설정 전)")
        return account

    def _minutes(self, order: OrderRecord) -> tuple[tuple[MinuteCandle, ...], datetime | None]:
        """판정할 1분봉과 그 구간의 끝. 볼 봉이 없으면 `((), None)`. 받지 못하면 `ExchangeError`."""
        judged = self._judged.get(order.uid, first_judged_until(order.created_utc))
        window = judge_window(judged, self._clock.now())
        if window is None:
            return (), None
        since, until = window
        failed: QuoteError | None = None
        minutes: tuple[MinuteCandle, ...] = ()
        try:
            minutes = self._quotes.fetch_minutes(order.market, since=since, until=until)
        except QuoteError as exc:
            failed = exc
        if failed is not None:
            raise ExchangeError(failed.error_name, failed.retry_after)
        return minutes, until

    def _judge(
        self, order: OrderRecord, minutes: tuple[MinuteCandle, ...], until: datetime | None
    ) -> OrderSnapshot:
        if until is None:
            return _snapshot(order, ())
        judged = self._judged.get(order.uid, first_judged_until(order.created_utc))
        fills = judge_fills([PendingOrder(order, judged)], minutes)
        if fills:
            return self._settle(order, fills[0])
        self._judged[order.uid] = until
        return _snapshot(order, ())

    def _settle(self, order: OrderRecord, fill: Fill) -> OrderSnapshot:
        """체결 하나를 계정에 넣고 체결 줄과 함께 한 트랜잭션으로 쓴다(M3 명세 3.7.2)."""
        settlement = apply_fill(self._account(), order, fill)
        record = FillRecord(
            fill_id=0,
            order_id=order.order_id,
            mode=Mode.PAPER,
            market=order.market,
            side=order.side,
            price=fill.price,
            qty=fill.qty,
            fee=settlement.fee,
            realized_pnl=settlement.realized_pnl,
            filled_utc=fill.filled_utc,
        )
        written = self._store.apply_fill(record, settlement.account)
        self._judged.pop(order.uid, None)
        filled = replace(order, state=OrderState.FILLED, closed_utc=fill.filled_utc)
        return _snapshot(filled, (written,))

    def _closed_snapshot(self, order: OrderRecord) -> OrderSnapshot:
        return _snapshot(order, self._store.fills_of(order.order_id))


def _snapshot(order: OrderRecord, fills: tuple[FillRecord, ...]) -> OrderSnapshot:
    trades = tuple(
        ExchangeTrade(
            trade_key=PAPER_TRADE_KEY,
            price=fill.price,
            qty=fill.qty,
            fee=fill.fee,
            filled_utc=fill.filled_utc,
        )
        for fill in fills
    )
    return OrderSnapshot(
        uid=order.uid,
        market=order.market,
        side=order.side,
        kind=order.kind,
        price=order.price,
        qty=order.qty,
        executed_qty=sum((t.qty for t in trades), Decimal(0)),
        state=order.state,
        trades=trades,
        program=True,
    )
