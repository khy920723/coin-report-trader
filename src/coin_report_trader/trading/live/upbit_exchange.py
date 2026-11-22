"""실거래의 거래소 경계 `UpbitExchange`(M5 명세 3.4, 3.6.1~3.6.4).

`ExchangePort`를 갖춘다. 업비트 비공개 클라이언트(`trading/upbit/`)의 여섯 요청을 거래소 경계의
값으로 바꾼다. DB를 열지 않는다(3.2). 주문의 식별값을 먼저 저장하는 것, 상태와 체결을 쓰는 것은
`app`이 한다(3.6.2, 15번). 만드는 곳은 `select_exchange` 하나다(3.7.1, 3.13).

- 계정(3.6.1): `accounts()`의 원화(`KRW`)가 원화 잔고(주문 가능 `krw`, 묶임 `krw_locked`)이고,
  그 밖의 통화는 `KRW-{통화}`의 보유(`balance` 주문 가능, `locked` 묶임, 거래소의 평균 매수가)다.
- 규칙(3.6.1): `order_chance`의 매수·매도 수수료율, 최소 주문 금액(매수와 매도의 최소 가운데 큰
  것), 최대 주문 금액. 읽은 규칙은 코인마다 기억해 체결의 수수료 셈에 쓴다.
- 주문(3.6.2): 지정가는 가격과 수량, 시장가(손절, 긴급 매도)는 수량만의 매도다. 식별값은 `uid`다.
- 결과를 모름(3.6.3): 응답이 없음, 주문 생성의 2xx를 읽지 못함, 주문 생성의 5xx
  (`UpbitError.unknown`)이면 다시 보내지 않고 그 식별값으로 조회한다. 있으면 그 상태를 쓰고,
  없거나 조회도 실패하면 `PlaceResult.unknown`이 참이다(`app`이 `UNCONFIRMED`로 둔다).
- 조회(3.6.3): 404 `order_not_found`는 "없음"(`None`)이다.
- 취소(3.6.4): 404 `order_not_found`면 그 식별값으로 다시 조회한 값을 준다. 아직 걸림이면 취소가
  확인되지 않은 주문이고, 조회도 "없음"(`None`)이면 보내지 않은 주문이다(지휘가 정한 것. 그 판단은
  `app`이 한다).
- 체결(3.6.4): 체결 목록의 체결마다 `ExchangeTrade`다. 체결의 수수료는 체결 금액(`funds`) × 그 쪽
  수수료율이다(응답의 체결에는 수수료가 없다). 체결 수량은 목록의 합이다.
- 그 밖의 실패는 `ExchangeError`(오류 이름은 3.5.5)다.
"""

from collections.abc import Callable, Iterable
from decimal import Decimal
from typing import Final

from coin_report_trader.core.alerts import Mode
from coin_report_trader.core.clock import Clock
from coin_report_trader.core.errors import ExchangeError
from coin_report_trader.core.exchange import (
    ExchangeAccount,
    ExchangeHolding,
    ExchangeRules,
    ExchangeTrade,
    OrderSnapshot,
    PlaceResult,
)
from coin_report_trader.core.market import MARKET_PREFIX
from coin_report_trader.core.texts import ERROR_BAD_RESPONSE
from coin_report_trader.core.trade import (
    OrderKind,
    OrderPlan,
    OrderSide,
    OrderState,
    PaperAccount,
    PaperHolding,
)
from coin_report_trader.trading.upbit.client import (
    ORD_LIMIT,
    ORD_MARKET,
    SIDE_ASK,
    SIDE_BID,
    UpbitClient,
    UpbitOrder,
)
from coin_report_trader.trading.upbit.errors import UpbitError

KRW_CURRENCY: Final = "KRW"
CODE_ORDER_NOT_FOUND: Final = "order_not_found"
STATUS_NOT_FOUND: Final = 404

UPBIT_STATES: Final = {
    "wait": OrderState.OPEN,
    "watch": OrderState.OPEN,
    "done": OrderState.FILLED,
    "cancel": OrderState.CANCELED,
}
"""업비트 주문 상태 → 우리 상태. 부분 체결은 `wait`이고 체결 수량으로 본다(5.2절)."""


class UpbitExchange:
    """실거래의 거래소 경계(모듈 설명)."""

    mode = Mode.LIVE

    def __init__(self, client: UpbitClient, clock: Clock) -> None:
        if not isinstance(client, UpbitClient):
            raise TypeError("client는 UpbitClient다")
        self._client = client
        self._clock = clock
        self._rules: dict[str, ExchangeRules] = {}

    def __repr__(self) -> str:
        return "UpbitExchange()"

    def read_account(self) -> ExchangeAccount:
        balances = _call(self._client.accounts, self._clock)
        krw = Decimal(0)
        krw_locked = Decimal(0)
        holdings = []
        for item in balances:
            if item.currency == KRW_CURRENCY:
                krw, krw_locked = item.balance, item.locked
                continue
            holdings.append(
                ExchangeHolding(
                    market=f"{MARKET_PREFIX}{item.currency}",
                    balance=item.balance,
                    locked=item.locked,
                    avg_buy_price=item.avg_buy_price,
                )
            )
        return ExchangeAccount(
            krw=krw, krw_locked=krw_locked, holdings=tuple(holdings), read_utc=self._clock.now()
        )

    def read_rules(self, market: str) -> ExchangeRules:
        chance = _call(lambda: self._client.order_chance(market), self._clock)
        rules = ExchangeRules(
            bid_fee=chance.bid_fee,
            ask_fee=chance.ask_fee,
            min_total=max(chance.bid_min_total, chance.ask_min_total),
            max_total=chance.max_total,
        )
        self._rules[market] = rules
        return rules

    def place(self, plan: OrderPlan, uid: str) -> PlaceResult:
        side = SIDE_BID if plan.side is OrderSide.BUY else SIDE_ASK
        if plan.kind is OrderKind.LIMIT:
            if plan.price is None:
                raise ValueError("지정가 주문에는 가격이 있어야 한다")
            ord_type, price = ORD_LIMIT, plan.price
        else:
            if plan.side is not OrderSide.SELL:
                raise ValueError("시장가 주문은 매도뿐이다")
            ord_type, price = ORD_MARKET, None
        failed: UpbitError | None = None
        order: UpbitOrder | None = None
        try:
            order = self._client.create_order(plan.market, side, ord_type, plan.qty, price, uid)
        except UpbitError as exc:
            failed = exc
        if failed is None:
            assert order is not None
            return PlaceResult(snapshot=self._snapshot(order), error_name=None, unknown=False)
        if not failed.unknown:
            return PlaceResult(snapshot=None, error_name=failed.error_name, unknown=False)
        # 결과를 모름: 다시 보내지 않고 그 식별값으로 조회한다(3.6.3).
        found: OrderSnapshot | None = None
        try:
            found = self.query(uid)
        except ExchangeError:
            found = None
        if found is not None:
            return PlaceResult(snapshot=found, error_name=None, unknown=False)
        return PlaceResult(snapshot=None, error_name=failed.error_name, unknown=True)

    def query(self, uid: str) -> OrderSnapshot | None:
        order = _call_or_missing(lambda: self._client.get_order(uid), self._clock)
        return None if order is None else self._snapshot(order)

    def cancel(self, uid: str) -> OrderSnapshot | None:
        order = _call_or_missing(lambda: self._client.cancel_order(uid), self._clock)
        if order is None:
            # 이미 끝났거나 없는 주문: 다시 조회해 끝난 상태와 체결을 쓴다(지휘가 정한 것).
            return self.query(uid)
        return self._snapshot(order)

    def open_orders(self, market: str) -> tuple[OrderSnapshot, ...]:
        orders = _call(lambda: self._client.open_orders(market), self._clock)
        return tuple(self._snapshot(order) for order in orders)

    def _snapshot(self, order: UpbitOrder) -> OrderSnapshot:
        state = UPBIT_STATES.get(order.state)
        if state is None or order.side not in (SIDE_BID, SIDE_ASK):
            raise ExchangeError(ERROR_BAD_RESPONSE)
        side = OrderSide.BUY if order.side == SIDE_BID else OrderSide.SELL
        kind = OrderKind.LIMIT if order.ord_type == ORD_LIMIT else OrderKind.MARKET
        trades: tuple[ExchangeTrade, ...] = ()
        if order.trades:
            rules = self._rules.get(order.market) or self.read_rules(order.market)
            rate = rules.bid_fee if side is OrderSide.BUY else rules.ask_fee
            now = self._clock.now()
            trades = tuple(
                ExchangeTrade(
                    trade_key=trade.trade_key,
                    price=trade.price,
                    qty=trade.volume,
                    fee=trade.funds * rate,
                    filled_utc=trade.created_at or now,
                )
                for trade in order.trades
            )
        return OrderSnapshot(
            uid=order.identifier if order.identifier is not None else order.uuid,
            market=order.market,
            side=side,
            kind=kind,
            price=order.price,
            qty=order.volume if order.volume is not None else order.executed_volume,
            executed_qty=order.executed_volume,
            state=state,
            trades=trades,
            program=order.identifier is not None,
        )


def executed_qty(trades: Iterable[ExchangeTrade]) -> Decimal:
    """체결 수량: 체결 목록의 합이다(상태가 아니라 체결 수량으로 판정한다. 5.2절)."""
    return sum((trade.qty for trade in trades), Decimal(0))


def new_trades(snapshot: OrderSnapshot, written_keys: Iterable[str]) -> tuple[ExchangeTrade, ...]:
    """아직 쓰지 않은 체결(`trade_key`가 `fills`에 없는 것). 받은 차례 그대로다(3.6.4)."""
    written = set(written_keys)
    return tuple(trade for trade in snapshot.trades if trade.trade_key not in written)


def live_realized_pnl(
    trade: ExchangeTrade, avg_price_at_send: Decimal, bid_fee: Decimal
) -> Decimal:
    """매도 체결 하나의 실현 손익(3.6.4).

    수량 × 체결 가격 − 그 체결의 수수료 − 수량 × 주문을 낼 때의 평균 매수가 × (1 + bid_fee).
    """
    if avg_price_at_send <= 0:
        raise ValueError("주문을 낼 때의 평균 매수가가 있어야 한다")
    return trade.qty * trade.price - trade.fee - trade.qty * avg_price_at_send * (1 + bid_fee)


def live_account_for_limits(account: ExchangeAccount) -> PaperAccount:
    """한도(`check_buy`)에 넘길 계정의 꼴로 바꾼 실제 계정(3.6.1).

    원화는 주문 가능 원화(`krw`)이고 묶음은 0이다(가상 원화의 검사가 실제 원화 잔고를 본다).
    보유 수량은 `balance` + `locked`(사용자가 직접 산 수량과 직접 건 주문에 묶인 수량도 든다)이고,
    평균 매수가는 거래소의 값이다. 그래서 예산의 매수 원가는 (`balance` + `locked`) × 평균 매수가다.
    """
    holdings = tuple(
        PaperHolding(
            market=holding.market,
            qty=holding.balance + holding.locked,
            locked_qty=holding.locked,
            avg_price=holding.avg_buy_price,
            buy_amount_sum=_cost(holding),
            buy_qty_sum=holding.balance + holding.locked,
        )
        for holding in account.holdings
    )
    return PaperAccount(krw=account.krw, locked_krw=Decimal(0), holdings=holdings)


def live_krw_enough(plan: OrderPlan, account: ExchangeAccount, rules: ExchangeRules) -> bool:
    """실제 원화 잔고로 살 수 있는지: `krw` ≥ 가격 × 수량 × (1 + bid_fee)(3.6.1).

    매도는 늘 참이다.
    """
    if plan.side is not OrderSide.BUY:
        return True
    if plan.price is None:
        raise ValueError("매수는 지정가다")
    return account.krw >= plan.price * plan.qty * (1 + rules.bid_fee)


def _cost(holding: ExchangeHolding) -> Decimal:
    if holding.avg_buy_price is None:
        return Decimal(0)
    return (holding.balance + holding.locked) * holding.avg_buy_price


def _call_or_missing[T](request: Callable[[], T], clock: Clock) -> T | None:
    """요청 하나. 404 `order_not_found`는 "없음"(`None`)이고, 그 밖의 `UpbitError`는 같은 오류
    이름의 `ExchangeError`로 바꾼다. 예외는 except 밖에서 새로 내어 응답 값이 이어지지 않게 한다."""
    failed: UpbitError | None = None
    result: T | None = None
    try:
        result = request()
    except UpbitError as exc:
        failed = exc
    if failed is not None:
        if failed.status == STATUS_NOT_FOUND and failed.code == CODE_ORDER_NOT_FOUND:
            return None
        raise _exchange_error(failed, clock)
    return result


def _call[T](request: Callable[[], T], clock: Clock) -> T:
    """요청 하나. `UpbitError`는 같은 오류 이름의 `ExchangeError`로 바꾼다(404도 오류다)."""
    failed: UpbitError | None = None
    result: T | None = None
    try:
        result = request()
    except UpbitError as exc:
        failed = exc
    if failed is not None:
        raise _exchange_error(failed, clock)
    assert result is not None
    return result


def _exchange_error(error: UpbitError, clock: Clock) -> ExchangeError:
    """같은 오류 이름의 `ExchangeError`. 418의 차단이 풀리는 때가 있으면 남은 초를 `retry_after`에
    담는다."""
    retry_after = None
    if error.blocked_until is not None:
        retry_after = max(0.0, (error.blocked_until - clock.now()).total_seconds())
    return ExchangeError(error.error_name, retry_after)
