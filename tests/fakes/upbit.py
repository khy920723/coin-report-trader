"""가짜 업비트 비공개 API(M5 명세 3.12). `UpbitTransport`를 갖춘다.

값은 모두 만든 숫자이고, 업비트의 실제 응답과 문서의 예제를 넣지 않는다. 응답의 필드 이름만
공식 문서의 뜻을 따른다.

- 인증 검사: 받은 `Authorization: Bearer {토큰}`을 클라이언트 코드를 쓰지 않고 따로 검사한다.
  - 머리의 `alg`가 "HS512"이고, 서명이 이 가짜의 Secret Key로 만든 HMAC-SHA512와 같아야 한다.
  - 몸통의 `access_key`가 이 가짜의 Access Key이고, `nonce`가 앞에서 쓰지 않은 값이어야 한다.
  - 쿼리나 몸통이 있으면 `query_hash`가 보낸 글(GET·DELETE는 쿼리 글, POST는 JSON 몸통의 필드를
    받은 차례대로 `key=value&…`로 이은 글)의 SHA512이고 `query_hash_alg`가 "SHA512"여야 한다.
  - 어기면 401과 공식 문서의 오류 코드(`jwt_verification`, `nonce_used`, `invalid_query_payload`,
    `no_authorization_token`)로 답한다. 다른 Access Key도 `jwt_verification`이다. 검사한 뒤 토큰은
    버린다.
  - 인증 검사는 정해 둔 결과(`script`)보다 먼저 한다. 서명이 틀린 요청은 정해 둔 응답을 받지 못한다.
  - `Origin` 머리글이 있으면 400(만든 코드 `origin_not_allowed`)으로 거절한다(3.5.1). 모르는 경로는
    404다.
- 기록(`requests`): `FakeUpbitRequest(method, path, query, header_names, query_hash, body, auth)`.
  `header_names`는 받은 머리글 이름(소문자)이고, `auth`는 인증 검사의 결과(통과면 `None`, 아니면
  오류 코드)다. 토큰은 남기지 않는다.
- 계정과 시장: `set_account(krw=…, coins={"AAA": (수량, 평균 매수가)})`, `set_chance(market, …)`,
  `set_market_price(market, price)`(시장가 매도가 체결되는 값).
- 주문:
  - 지정가는 `wait`로 걸리고, 매수는 원화(가격 × 수량 × (1 + 수수료율))를, 매도는 수량을 묶는다.
  - 시장가 매도는 `set_market_price`의 값에 바로 다 체결된다(`done`).
  - 주문 거절: 원화나 수량이 모자라면 400 `insufficient_funds_bid`/`insufficient_funds_ask`, 금액이
    최소 주문 금액보다 작으면 400 `under_min_total_bid`/`under_min_total_ask`, 쓴 식별값을 다시 쓰면
    400 `duplicated_identifier`.
  - `fill(identifier, volume, *, price=None)`: 시험이 체결을 만든다(부분 체결이면 여러 번 부른다).
    다 차면 `done`이다.
  - `add_direct_order(market, side, price, volume)`: 사용자가 직접 건 주문(식별값 없음)을 걸어 둔다.
  - 취소하면 `cancel`이 되고 묶인 것이 풀린다. 이미 끝난 주문을 취소하거나, 없는 식별값을 조회하거나
    취소하면 404 `order_not_found`다(공식 문서의 취소는 이 코드를 404로만 적는다).
- 정하는 실패(`script`): 경로·메서드마다 줄을 세운다. 결과는 아래 넷 가운데 하나이고, 맞는 줄이
  없으면 정상으로 답한다.
  - `Reply(status, code=None, headers=…, message=…)`: 그 상태, 오류 코드, 오류 글의 응답(예: 429,
    418과 `Retry-After`, 키 오류 넷).
  - `NoResponse(sent=…)`: 응답이 없다. `sent`가 참이면 요청을 처리한(주문이면 만든) 뒤 응답만
    잃고, 거짓이면 처리하지 않는다. 둘 다 `TransportError`를 낸다.
  - `CANCEL_FAILS`: 취소를 404 `order_not_found`로 거절한다(주문은 그대로 걸려 있다).
  - `OK`: 정상으로 답한다.
- `Remaining-Req`: 응답마다 `group={그룹}; min=600; sec={값}`을 붙인다(만든 값).
  `set_remaining(group, sec)`로 `sec`를 정한다(기본 13).
- 여러 스레드가 함께 써도 된다(잠금 하나).
"""

import base64
import hashlib
import hmac
import itertools
import json
import threading
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final
from urllib.parse import parse_qsl

from coin_report_trader.core import Clock
from coin_report_trader.trading.upbit import TransportError, TransportResponse

TEST_ACCESS_KEY: Final = "TESTACCESSKEY0000000000000000000000000000"
TEST_SECRET_KEY: Final = "TESTSECRETKEY0000000000000000000000000000"
"""꼴만 맞춘 만든 키(M5 명세 3.12)."""

DEFAULT_FEE: Final = Decimal("0.0005")
DEFAULT_MIN_TOTAL: Final = Decimal(5000)
FAKE_TIME: Final = datetime(2026, 1, 5, 3, 0, tzinfo=UTC)

ORDER_GROUP_PATHS: Final = {("POST", "/v1/orders")}
DEFAULT_SEC: Final = 13
"""`Remaining-Req`의 `sec` 기본값(만든 값)."""


@dataclass(frozen=True)
class Reply:
    status: int
    code: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    message: str = "만든 오류 글"
    """응답 본문의 오류 글(`error.message`)."""


@dataclass(frozen=True)
class NoResponse:
    sent: bool


class _Marker:
    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return self.name


OK: Final = _Marker("OK")
CANCEL_FAILS: Final = _Marker("CANCEL_FAILS")

type Outcome = Reply | NoResponse | _Marker


@dataclass(frozen=True)
class FakeUpbitRequest:
    method: str
    path: str
    query: str
    header_names: tuple[str, ...]
    query_hash: str | None
    body: str | None
    auth: str | None
    """인증 검사의 결과: 통과면 `None`, 아니면 그 오류 코드."""
    monotonic: float | None = None


@dataclass
class _Order:
    uuid: str
    identifier: str | None
    market: str
    side: str
    ord_type: str
    price: Decimal | None
    volume: Decimal
    executed: Decimal = Decimal(0)
    state: str = "wait"
    trades: list[dict[str, str]] = field(default_factory=list)
    locked_krw: Decimal = Decimal(0)
    locked_qty: Decimal = Decimal(0)


@dataclass
class _Script:
    method: str | None
    path: str | None
    outcomes: deque[Outcome]


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _num(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


class FakeUpbitTransport:
    """가짜 업비트 비공개 API(모듈 설명)."""

    def __init__(
        self,
        *,
        access_key: str = TEST_ACCESS_KEY,
        secret_key: str = TEST_SECRET_KEY,
        clock: Clock | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._access = access_key
        self._secret = secret_key
        self._clock = clock
        self._nonces: set[str] = set()
        self._scripts: list[_Script] = []
        self._remaining = {"order": DEFAULT_SEC, "default": DEFAULT_SEC}
        self._krw = Decimal(0)
        self._krw_locked = Decimal(0)
        self._coins: dict[str, list[Decimal]] = {}  # 통화 → [수량, 묶인 수량, 평균 매수가]
        self._chances: dict[str, dict[str, Decimal | None]] = {}
        self._market_prices: dict[str, Decimal] = {}
        self._orders: dict[str, _Order] = {}  # uuid → 주문
        self._used_identifiers: set[str] = set()
        self._uuid_counter = itertools.count(1)
        self.requests: list[FakeUpbitRequest] = []

    # --- 시험이 정하는 것 ------------------------------------------------------------------

    def set_account(
        self, *, krw: Decimal, coins: Mapping[str, tuple[Decimal, Decimal]] | None = None
    ) -> None:
        """원화와 코인(통화 → (수량, 평균 매수가))을 정한다. 묶인 것은 풀린다."""
        with self._lock:
            self._krw = Decimal(krw)
            self._krw_locked = Decimal(0)
            self._coins = {
                currency: [Decimal(qty), Decimal(0), Decimal(avg)]
                for currency, (qty, avg) in (coins or {}).items()
            }

    def set_chance(
        self,
        market: str,
        *,
        bid_fee: Decimal = DEFAULT_FEE,
        ask_fee: Decimal = DEFAULT_FEE,
        min_total: Decimal = DEFAULT_MIN_TOTAL,
        max_total: Decimal | None = Decimal(1_000_000_000),
    ) -> None:
        with self._lock:
            self._chances[market] = {
                "bid_fee": bid_fee,
                "ask_fee": ask_fee,
                "min_total": min_total,
                "max_total": max_total,
            }

    def set_market_price(self, market: str, price: Decimal) -> None:
        with self._lock:
            self._market_prices[market] = Decimal(price)

    def set_remaining(self, group: str, sec: int) -> None:
        with self._lock:
            self._remaining[group] = sec

    def script(
        self, *outcomes: Outcome, method: str | None = None, path: str | None = None
    ) -> None:
        for outcome in outcomes:
            if not isinstance(outcome, Reply | NoResponse | _Marker):
                raise TypeError("script: 결과는 Reply, NoResponse, CANCEL_FAILS, OK다")
        with self._lock:
            self._scripts.append(_Script(method, path, deque(outcomes)))

    def fill(self, identifier: str, volume: Decimal, *, price: Decimal | None = None) -> None:
        """걸린 주문 하나에 체결 하나를 만든다(부분 체결이면 여러 번 부른다)."""
        with self._lock:
            order = self._by_identifier(identifier)
            if order is None or order.state != "wait":
                raise ValueError("fill: 걸린 주문이 아니다")
            volume = Decimal(volume)
            if order.executed + volume > order.volume:
                raise ValueError("fill: 주문 수량보다 많이 체결할 수 없다")
            self._trade(order, volume, price if price is not None else order.price)

    def add_direct_order(self, market: str, side: str, price: Decimal, volume: Decimal) -> str:
        """사용자가 직접 건 주문(식별값 없음)을 걸어 둔다. 그 주문의 `uuid`를 돌려준다."""
        with self._lock:
            order = _Order(
                uuid=self._new_uuid(),
                identifier=None,
                market=market,
                side=side,
                ord_type="limit",
                price=Decimal(price),
                volume=Decimal(volume),
            )
            self._lock_for(order)
            self._orders[order.uuid] = order
            return order.uuid

    # --- 보는 도우미 -----------------------------------------------------------------------

    def order_state(self, identifier: str) -> str | None:
        with self._lock:
            order = self._by_identifier(identifier)
            return None if order is None else order.state

    def balances(self) -> dict[str, tuple[Decimal, Decimal]]:
        """통화 → (주문 가능, 묶임). 원화는 `KRW`다."""
        with self._lock:
            result = {"KRW": (self._krw, self._krw_locked)}
            result.update({c: (v[0], v[1]) for c, v in self._coins.items()})
            return result

    # --- UpbitTransport ---------------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        query: str,
        body: str | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        with self._lock:
            hashed_text, fields = self._fields(method, query, body)
            auth, claimed_hash = self._check_auth(headers, hashed_text)
            self.requests.append(
                FakeUpbitRequest(
                    method=method,
                    path=path,
                    query=query,
                    header_names=tuple(sorted(name.lower() for name in headers)),
                    query_hash=claimed_hash,
                    body=body,
                    auth=auth,
                    monotonic=None if self._clock is None else self._clock.monotonic(),
                )
            )
            group = "order" if (method, path) in ORDER_GROUP_PATHS else "default"
            if any(name.lower() == "origin" for name in headers):
                return self._reply(400, "origin_not_allowed", group)
            if auth is not None:
                return self._reply(401, auth, group)
            outcome = self._next_outcome(method, path)
            if isinstance(outcome, NoResponse):
                if outcome.sent:
                    self._handle(method, path, fields)
                raise TransportError(timeout=True, sent=outcome.sent)
            if isinstance(outcome, Reply):
                return self._reply(
                    outcome.status, outcome.code, group, outcome.headers, outcome.message
                )
            if outcome is CANCEL_FAILS:
                return self._reply(404, "order_not_found", group)
            status, data = self._handle(method, path, fields)
            if status != 200:
                return self._reply(status, str(data), group)
            return TransportResponse(
                status=200,
                headers={"Remaining-Req": self._remaining_header(group)},
                text=json.dumps(data, ensure_ascii=False),
            )

    # --- 안쪽 ------------------------------------------------------------------------------

    def _fields(
        self, method: str, query: str, body: str | None
    ) -> tuple[str, list[tuple[str, str]]]:
        if method == "POST":
            pairs = json.loads(body or "{}", object_pairs_hook=list)
            fields = [(str(k), str(v)) for k, v in pairs]
        else:
            fields = parse_qsl(query, keep_blank_values=True)
        return "&".join(f"{k}={v}" for k, v in fields), fields

    def _check_auth(
        self, headers: Mapping[str, str], hashed_text: str
    ) -> tuple[str | None, str | None]:
        """(오류 코드나 `None`, 토큰이 주장한 query_hash). 토큰은 여기서만 보고 버린다."""
        value = next((v for k, v in headers.items() if k.lower() == "authorization"), None)
        if value is None or not value.startswith("Bearer "):
            return "no_authorization_token", None
        token = value.removeprefix("Bearer ")
        try:
            head_b64, payload_b64, signature_b64 = token.split(".")
            head = json.loads(_b64decode(head_b64))
            payload = json.loads(_b64decode(payload_b64))
            signature = _b64decode(signature_b64)
        except (ValueError, UnicodeDecodeError):
            return "jwt_verification", None
        claimed = payload.get("query_hash")
        if head.get("alg") != "HS512":
            return "jwt_verification", claimed
        expected = hmac.new(
            self._secret.encode("utf-8"),
            f"{head_b64}.{payload_b64}".encode("ascii"),
            hashlib.sha512,
        ).digest()
        if not hmac.compare_digest(expected, signature):
            return "jwt_verification", claimed
        if payload.get("access_key") != self._access:
            return "jwt_verification", claimed
        nonce = payload.get("nonce")
        if not isinstance(nonce, str) or not nonce or nonce in self._nonces:
            return "nonce_used", claimed
        self._nonces.add(nonce)
        if hashed_text:
            want = hashlib.sha512(hashed_text.encode("utf-8")).hexdigest()
            if claimed != want or payload.get("query_hash_alg") != "SHA512":
                return "invalid_query_payload", claimed
        elif "query_hash" in payload:
            return "invalid_query_payload", claimed
        return None, claimed

    def _next_outcome(self, method: str, path: str) -> Outcome:
        for line in self._scripts:
            if (
                line.outcomes
                and (line.method is None or line.method == method)
                and (line.path is None or line.path == path)
            ):
                return line.outcomes.popleft()
        return OK

    def _reply(
        self,
        status: int,
        code: str | None,
        group: str,
        headers: Mapping[str, str] | None = None,
        message: str = "만든 오류 글",
    ) -> TransportResponse:
        all_headers = {"Remaining-Req": self._remaining_header(group), **(headers or {})}
        text = (
            ""
            if code is None
            else json.dumps({"error": {"name": code, "message": message}}, ensure_ascii=False)
        )
        return TransportResponse(status=status, headers=all_headers, text=text)

    def _remaining_header(self, group: str) -> str:
        return f"group={group}; min=600; sec={self._remaining.get(group, DEFAULT_SEC)}"

    def _new_uuid(self) -> str:
        return f"fake-order-{next(self._uuid_counter):04d}"

    def _by_identifier(self, identifier: str) -> _Order | None:
        return next((o for o in self._orders.values() if o.identifier == identifier), None)

    def _handle(self, method: str, path: str, fields: list[tuple[str, str]]) -> tuple[int, object]:
        params = dict(fields)
        if (method, path) == ("GET", "/v1/accounts"):
            return 200, self._accounts()
        if (method, path) == ("GET", "/v1/orders/chance"):
            return self._chance(params["market"])
        if (method, path) == ("POST", "/v1/orders"):
            return self._create(params)
        if (method, path) == ("GET", "/v1/order"):
            order = self._by_identifier(params.get("identifier", ""))
            return (404, "order_not_found") if order is None else (200, self._order_json(order))
        if (method, path) == ("GET", "/v1/orders/open"):
            market = params.get("market")
            return 200, [
                self._order_json(o, with_trades=False)
                for o in self._orders.values()
                if o.state == "wait" and o.market == market
            ]
        if (method, path) == ("DELETE", "/v1/order"):
            return self._cancel(params.get("identifier", ""))
        return 404, "not_found"

    def _accounts(self) -> list[dict[str, str]]:
        rows = [
            {
                "currency": "KRW",
                "balance": _num(self._krw),
                "locked": _num(self._krw_locked),
                "avg_buy_price": "0",
                "avg_buy_price_modified": False,
                "unit_currency": "KRW",
            }
        ]
        for currency, (qty, locked, avg) in self._coins.items():
            rows.append(
                {
                    "currency": currency,
                    "balance": _num(qty),
                    "locked": _num(locked),
                    "avg_buy_price": _num(avg),
                    "avg_buy_price_modified": False,
                    "unit_currency": "KRW",
                }
            )
        return rows

    def _chance(self, market: str) -> tuple[int, object]:
        chance = self._chances.get(market)
        if chance is None:
            return 404, "market_does_not_exist"
        min_total = _num(chance["min_total"])  # type: ignore[arg-type]
        max_total = chance["max_total"]
        return 200, {
            "bid_fee": _num(chance["bid_fee"]),  # type: ignore[arg-type]
            "ask_fee": _num(chance["ask_fee"]),  # type: ignore[arg-type]
            "market": {
                "id": market,
                "bid": {"currency": "KRW", "min_total": min_total},
                "ask": {"currency": market.removeprefix("KRW-"), "min_total": min_total},
                "max_total": None if max_total is None else _num(max_total),
            },
        }

    def _create(self, params: dict[str, str]) -> tuple[int, object]:
        market = params["market"]
        side = params["side"]
        ord_type = params["ord_type"]
        identifier = params.get("identifier")
        volume = Decimal(params["volume"])
        price = Decimal(params["price"]) if "price" in params else None
        if identifier is not None and identifier in self._used_identifiers:
            return 400, "duplicated_identifier"
        if identifier is not None:
            self._used_identifiers.add(identifier)
        chance = self._chances.get(market, {})
        min_total = chance.get("min_total") or DEFAULT_MIN_TOTAL
        fee = chance.get("bid_fee") or DEFAULT_FEE
        reference = price if price is not None else self._market_prices.get(market, Decimal(0))
        total = reference * volume
        if total < min_total:
            return 400, f"under_min_total_{side}"
        currency = market.removeprefix("KRW-")
        if side == "bid":
            if self._krw < total * (1 + fee):
                return 400, "insufficient_funds_bid"
        elif self._coins.get(currency, [Decimal(0)])[0] < volume:
            return 400, "insufficient_funds_ask"
        order = _Order(
            uuid=self._new_uuid(),
            identifier=identifier,
            market=market,
            side=side,
            ord_type=ord_type,
            price=price,
            volume=volume,
        )
        self._lock_for(order)
        self._orders[order.uuid] = order
        if ord_type == "market":
            self._trade(order, volume, self._market_prices[market])
        return 200, self._order_json(order, with_trades=False)

    def _cancel(self, identifier: str) -> tuple[int, object]:
        order = self._by_identifier(identifier)
        if order is None:
            return 404, "order_not_found"
        if order.state != "wait":
            return 404, "order_not_found"
        order.state = "cancel"
        self._release(order)
        return 200, self._order_json(order, with_trades=False)

    def _lock_for(self, order: _Order) -> None:
        currency = order.market.removeprefix("KRW-")
        if order.side == "bid":
            fee = self._chances.get(order.market, {}).get("bid_fee") or DEFAULT_FEE
            amount = (order.price or Decimal(0)) * order.volume * (1 + fee)
            self._krw -= amount
            self._krw_locked += amount
            order.locked_krw = amount
        else:
            coin = self._coins.setdefault(currency, [Decimal(0), Decimal(0), Decimal(0)])
            coin[0] -= order.volume
            coin[1] += order.volume
            order.locked_qty = order.volume

    def _release(self, order: _Order) -> None:
        currency = order.market.removeprefix("KRW-")
        if order.side == "bid":
            self._krw += order.locked_krw
            self._krw_locked -= order.locked_krw
            order.locked_krw = Decimal(0)
        else:
            coin = self._coins[currency]
            coin[0] += order.locked_qty
            coin[1] -= order.locked_qty
            order.locked_qty = Decimal(0)

    def _trade(self, order: _Order, volume: Decimal, price: Decimal | None) -> None:
        price = Decimal(price if price is not None else 0)
        funds = price * volume
        currency = order.market.removeprefix("KRW-")
        chance = self._chances.get(order.market, {})
        coin = self._coins.setdefault(currency, [Decimal(0), Decimal(0), Decimal(0)])
        if order.side == "bid":
            fee = (chance.get("bid_fee") or DEFAULT_FEE) * funds
            share = order.locked_krw * volume / (order.volume - order.executed)
            self._krw_locked -= share
            order.locked_krw -= share
            self._krw += share - funds - fee
            held = coin[0] + coin[1]
            coin[2] = (coin[2] * held + funds) / (held + volume)
            coin[0] += volume
        else:
            fee = (chance.get("ask_fee") or DEFAULT_FEE) * funds
            coin[1] -= volume
            order.locked_qty -= volume
            self._krw += funds - fee
        order.executed += volume
        order.trades.append(
            {
                "market": order.market,
                "uuid": f"{order.uuid}-trade-{len(order.trades) + 1}",
                "price": _num(price),
                "volume": _num(volume),
                "funds": _num(funds),
                "side": order.side,
                "created_at": FAKE_TIME.isoformat(),
            }
        )
        if order.executed == order.volume:
            order.state = "done"

    def _order_json(self, order: _Order, *, with_trades: bool = True) -> dict[str, object]:
        data: dict[str, object] = {
            "uuid": order.uuid,
            "side": order.side,
            "ord_type": order.ord_type,
            "price": None if order.price is None else _num(order.price),
            "state": order.state,
            "market": order.market,
            "created_at": FAKE_TIME.isoformat(),
            "volume": _num(order.volume),
            "remaining_volume": _num(order.volume - order.executed),
            "executed_volume": _num(order.executed),
            "trades_count": len(order.trades),
        }
        if order.identifier is not None:
            data["identifier"] = order.identifier
        if with_trades:
            data["trades"] = list(order.trades)
        return data
