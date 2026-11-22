"""업비트 비공개 클라이언트 `UpbitClient`(M5 명세 3.5.2). 여섯 요청을 낸다.

| 메서드 | 요청 | 한도 그룹 |
|---|---|---|
| `accounts()` | `GET /v1/accounts` | `default` |
| `order_chance(market)` | `GET /v1/orders/chance?market=…` | `default` |
| `create_order(…)` | `POST /v1/orders`(JSON 몸통) | `order` |
| `get_order(identifier)` | `GET /v1/order?identifier=…` | `default` |
| `open_orders(market)` | `GET /v1/orders/open?market=…` | `default` |
| `cancel_order(identifier)` | `DELETE /v1/order?identifier=…` | `default` |

- 인증은 `auth.py`(요청마다 새 JWT. 머리글은 `Authorization`, `Accept`, `User-Agent`, 몸통이 있을 때
  `Content-Type`뿐이다. `Origin`은 보내지 않는다).
- 주문의 종류: 판단의 주문은 지정가(`limit`, 가격과 수량), 손절과 긴급 매도는 시장가 매도(`market`,
  수량)다. 시장가 매수(`price`)와 최유리(`best`)는 받지 않는다. `smp_type`은 늘 `cancel_maker`다
  (제품 규칙 5.7절). 숫자는 `Decimal`을 지수 없이 쓴 글이다.
- 응답은 필드 이름으로 읽는다. 없거나 꼴이 다른 필드는 "응답 형식 오류"다.
- 한도와 다시 보내기(3.5.3):
  - 요청마다 `UpbitLimiter.acquire`를 거친다. 응답마다 `Remaining-Req`를 반영한다.
  - 429이면 그 그룹을 다음 초까지 멈춘 뒤 한 번 다시 보낸다(새 토큰). 두 번째도 429이면
    "요청 한도 초과"다. 주문(`create_order`)은 다시 보내지 않는다. 보냈는지 모르므로
    `unknown`이 참인 "요청 한도 초과"를 낸다(3.6.3의 조회로 간다).
  - 시간 초과와 연결 실패는 다시 보내지 않는다. 요청이 닿았을 수 있으면 `unknown`이 참이다.
- 실패는 모두 `UpbitError`다(`errors.py`의 표).
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

from coin_report_trader import __version__
from coin_report_trader.core.clock import Clock
from coin_report_trader.core.texts import (
    ERROR_BAD_RESPONSE,
    ERROR_NETWORK,
    ERROR_RATE_LIMIT,
    ERROR_TIMEOUT,
)
from coin_report_trader.trading.quote import convert
from coin_report_trader.trading.upbit.auth import UpbitKeys, make_token, query_text
from coin_report_trader.trading.upbit.errors import (
    STATUS_RATE_LIMIT,
    UpbitError,
    error_for_response,
)
from coin_report_trader.trading.upbit.limits import GROUP_DEFAULT, GROUP_ORDER, UpbitLimiter
from coin_report_trader.trading.upbit.transport import TransportError, UpbitTransport

USER_AGENT: Final = f"coin-report-trader/{__version__}"
SMP_TYPE: Final = "cancel_maker"
SIDE_BID: Final = "bid"
SIDE_ASK: Final = "ask"
ORD_LIMIT: Final = "limit"
ORD_MARKET: Final = "market"
MAX_IDENTIFIER_LENGTH: Final = 64
"""주문 식별값의 길이 상한(공식 문서)."""

ACCOUNTS_PATH: Final = "/v1/accounts"
CHANCE_PATH: Final = "/v1/orders/chance"
ORDERS_PATH: Final = "/v1/orders"
ORDER_PATH: Final = "/v1/order"
OPEN_ORDERS_PATH: Final = "/v1/orders/open"


@dataclass(frozen=True)
class UpbitBalance:
    """계정 자산의 통화 하나. `avg_buy_price`는 없거나 0이면 `None`이다(3.5.4)."""

    currency: str
    balance: Decimal
    """주문 가능 수량."""
    locked: Decimal
    """주문에 묶인 수량."""
    avg_buy_price: Decimal | None


@dataclass(frozen=True)
class UpbitChance:
    """주문 가능 정보. 수수료는 율(예: 0.0005)이고 금액은 원이다."""

    market: str
    bid_fee: Decimal
    ask_fee: Decimal
    bid_min_total: Decimal
    ask_min_total: Decimal
    max_total: Decimal | None


@dataclass(frozen=True)
class UpbitTrade:
    """주문의 체결 하나. `trade_key`는 거래소의 체결 식별값(`uuid`)이다."""

    trade_key: str
    price: Decimal
    volume: Decimal
    funds: Decimal
    created_at: datetime | None


@dataclass(frozen=True)
class UpbitOrder:
    """주문 하나(만들기, 조회, 목록, 취소의 응답). 체결 목록은 개별 조회에만 든다."""

    uuid: str
    identifier: str | None
    """우리 식별값. 사용자가 직접 건 주문에는 없을 수 있다."""
    market: str
    side: str
    ord_type: str
    price: Decimal | None
    volume: Decimal | None
    executed_volume: Decimal
    state: str
    created_at: datetime | None
    trades: tuple[UpbitTrade, ...] = ()


def number_text(value: Decimal) -> str:
    """`Decimal` → 지수 없는 글. 유한하고 0보다 커야 한다."""
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError("주문의 숫자는 0보다 큰 유한한 Decimal이다")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _check_market(market: str) -> str:
    if not isinstance(market, str) or convert.MARKET_CODE.fullmatch(market) is None:
        raise ValueError(f"마켓 코드의 꼴이 아니다: {market!r}")
    return market


def _check_identifier(identifier: str) -> str:
    if not isinstance(identifier, str) or not identifier or len(identifier) > MAX_IDENTIFIER_LENGTH:
        raise ValueError("주문 식별값은 1자에서 64자의 글이다")
    query_text([("identifier", identifier)])  # 인코딩이 필요한 글자를 막는다
    return identifier


class UpbitClient:
    """업비트 비공개 API(모듈 설명). `keys`는 보관소에서 읽어 넘긴다(3.8)."""

    def __init__(self, keys: UpbitKeys, transport: UpbitTransport, clock: Clock) -> None:
        if not isinstance(keys, UpbitKeys):
            raise TypeError("keys는 UpbitKeys다")
        self._keys = keys
        self._transport = transport
        self._clock = clock
        self._limiter = UpbitLimiter(clock)

    def __repr__(self) -> str:
        return "UpbitClient()"

    @property
    def limiter(self) -> UpbitLimiter:
        return self._limiter

    # --- 여섯 요청 -------------------------------------------------------------------------

    def accounts(self) -> tuple[UpbitBalance, ...]:
        data = self._send("GET", ACCOUNTS_PATH, (), GROUP_DEFAULT)
        return _parse(lambda d: tuple(_balance(item) for item in convert.items(d)), data)

    def order_chance(self, market: str) -> UpbitChance:
        market = _check_market(market)
        data = self._send("GET", CHANCE_PATH, (("market", market),), GROUP_DEFAULT)
        return _parse(lambda d: _chance(d, market), data)

    def create_order(
        self,
        market: str,
        side: str,
        ord_type: str,
        volume: Decimal,
        price: Decimal | None,
        identifier: str,
        smp_type: str = SMP_TYPE,
    ) -> UpbitOrder:
        """주문 하나. 지정가(`limit`)는 가격과 수량, 시장가 매도(`market`, `ask`)는 수량만이다."""
        market = _check_market(market)
        identifier = _check_identifier(identifier)
        if smp_type != SMP_TYPE:
            raise ValueError("smp_type은 늘 cancel_maker다")
        if side not in (SIDE_BID, SIDE_ASK):
            raise ValueError(f"주문 방향은 bid나 ask다: {side!r}")
        fields: list[tuple[str, str]] = [
            ("market", market),
            ("side", side),
            ("volume", number_text(volume)),
        ]
        if ord_type == ORD_LIMIT:
            if price is None:
                raise ValueError("지정가 주문에는 가격이 있어야 한다")
            fields.append(("price", number_text(price)))
        elif ord_type == ORD_MARKET:
            if side != SIDE_ASK or price is not None:
                raise ValueError("시장가 주문은 가격 없는 매도만 낸다")
        else:
            raise ValueError(f"주문 종류는 limit이나 market이다: {ord_type!r}")
        fields += [("ord_type", ord_type), ("identifier", identifier), ("smp_type", SMP_TYPE)]
        data = self._send("POST", ORDERS_PATH, tuple(fields), GROUP_ORDER, is_order=True)
        return _parse(_order, data)

    def get_order(self, identifier: str) -> UpbitOrder:
        identifier = _check_identifier(identifier)
        data = self._send("GET", ORDER_PATH, (("identifier", identifier),), GROUP_DEFAULT)
        return _parse(_order, data)

    def open_orders(self, market: str) -> tuple[UpbitOrder, ...]:
        market = _check_market(market)
        data = self._send("GET", OPEN_ORDERS_PATH, (("market", market),), GROUP_DEFAULT)
        return _parse(lambda d: tuple(_order(item) for item in convert.items(d)), data)

    def cancel_order(self, identifier: str) -> UpbitOrder:
        identifier = _check_identifier(identifier)
        data = self._send("DELETE", ORDER_PATH, (("identifier", identifier),), GROUP_DEFAULT)
        return _parse(_order, data)

    # --- 보내기 ----------------------------------------------------------------------------

    def _send(
        self,
        method: str,
        path: str,
        fields: tuple[tuple[str, str], ...],
        group: str,
        *,
        is_order: bool = False,
    ) -> Any:
        text = query_text(fields)
        if method == "POST":
            query = ""
            body: str | None = json.dumps(dict(fields), ensure_ascii=False, separators=(",", ":"))
        else:
            query = text
            body = None
        attempts = 1 if is_order else 2
        for attempt in range(attempts):
            self._limiter.acquire(group)
            headers = self._headers(text, body is not None)
            try:
                response = self._transport.request(
                    method, path, query=query, body=body, headers=headers
                )
            except TransportError as exc:
                name = ERROR_TIMEOUT if exc.timeout else ERROR_NETWORK
                raise UpbitError(name, unknown=exc.sent) from None
            finally:
                del headers  # 토큰이 든 머리글을 남기지 않는다
            self._limiter.observe(group, response.header("Remaining-Req"))
            if 200 <= response.status < 300:
                return _parse(convert.parse_json, response.text.encode("utf-8"))
            if response.status == STATUS_RATE_LIMIT:
                self._limiter.pause_until_next_second(group)
                if is_order:
                    raise UpbitError(ERROR_RATE_LIMIT, status=STATUS_RATE_LIMIT, unknown=True)
                if attempt + 1 < attempts:
                    continue
            raise error_for_response(
                response.status,
                response.text,
                response.header("Retry-After"),
                now=self._clock.now(),
                secrets=(self._keys.access, self._keys.secret),
            )
        raise AssertionError("닿지 않는 곳")  # pragma: no cover

    def _headers(self, hashed: str, has_body: bool) -> dict[str, str]:
        headers = {
            "Authorization": "Bearer " + make_token(self._keys, hashed),
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if has_body:
            headers["Content-Type"] = "application/json; charset=utf-8"
        return headers


# --- 응답 읽기 -------------------------------------------------------------------------------

_SHAPE_ERRORS = (convert.ShapeError, KeyError, TypeError, ValueError, ArithmeticError)


def _parse[T](parse: Callable[[Any], T], data: Any) -> T:
    """`parse(data)`. 꼴이 다르면 "응답 형식 오류"다. 응답 값이 든 예외를 잇지 않는다."""
    failed = False
    result: Any = None
    try:
        result = parse(data)
    except _SHAPE_ERRORS:
        failed = True
    if failed:
        raise UpbitError(ERROR_BAD_RESPONSE)
    return result


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise convert.ShapeError("객체가 아니다")
    return value


def _text(value: Any) -> str:
    return convert.text_value(value)


def _optional_decimal(value: Any) -> Decimal | None:
    return None if value is None else convert.decimal_value(value)


def _time(value: Any) -> datetime | None:
    if value is None:
        return None
    moment = datetime.fromisoformat(_text(value))
    if moment.tzinfo is None:
        raise convert.ShapeError("시각에 시간대가 없다")
    return moment.astimezone(UTC)


def _balance(value: Any) -> UpbitBalance:
    item = _mapping(value)
    avg = convert.decimal_value(item["avg_buy_price"])
    return UpbitBalance(
        currency=_text(item["currency"]),
        balance=convert.decimal_value(item["balance"]),
        locked=convert.decimal_value(item["locked"]),
        avg_buy_price=avg if avg > 0 else None,
    )


def _chance(value: Any, market: str) -> UpbitChance:
    item = _mapping(value)
    info = _mapping(item["market"])
    if _text(info["id"]) != market:
        raise convert.ShapeError("다른 마켓의 주문 가능 정보다")
    return UpbitChance(
        market=market,
        bid_fee=convert.decimal_value(item["bid_fee"]),
        ask_fee=convert.decimal_value(item["ask_fee"]),
        bid_min_total=convert.decimal_value(_mapping(info["bid"])["min_total"]),
        ask_min_total=convert.decimal_value(_mapping(info["ask"])["min_total"]),
        max_total=_optional_decimal(info.get("max_total")),
    )


def _trade(value: Any) -> UpbitTrade:
    item = _mapping(value)
    return UpbitTrade(
        trade_key=_text(item["uuid"]),
        price=convert.decimal_value(item["price"]),
        volume=convert.decimal_value(item["volume"]),
        funds=convert.decimal_value(item["funds"]),
        created_at=_time(item.get("created_at")),
    )


def _order(value: Any) -> UpbitOrder:
    item = _mapping(value)
    identifier = item.get("identifier")
    trades = item.get("trades")
    return UpbitOrder(
        uuid=_text(item["uuid"]),
        identifier=None if identifier is None else _text(identifier),
        market=convert.market_code(item["market"]),
        side=_text(item["side"]),
        ord_type=_text(item["ord_type"]),
        price=_optional_decimal(item.get("price")),
        volume=_optional_decimal(item.get("volume")),
        executed_volume=convert.decimal_value(item["executed_volume"]),
        state=_text(item["state"]),
        created_at=_time(item.get("created_at")),
        trades=() if trades is None else tuple(_trade(t) for t in convert.items(trades)),
    )
