"""거래소 연결 고르기(M5 명세 3.7.1, `CLAUDE.md`의 구조 원칙).

`UpbitExchange`를 만드는 곳은 `select_exchange` 하나다. `app`의 조립은 이 함수를 부르기만 한다.
`scripts/checks/live_switch.py`(3.13)가 이 함수의 몸통이 `LIVE_TRADING_ALLOWED`를 읽는지 본다.

- 모의매매(`Mode.PAPER`): `PaperExchange`(3단계 엔진을 `PaperStore`로 감싼 것). 키가 필요 없다.
- 실거래(`Mode.LIVE`): `UpbitExchange`. `allowed`와 실거래 허용 값이 둘 다 참이고 키 한 벌의
  `UpbitClient`가 있어야 한다. 아니면 `LiveNotAllowed`다.
"""

from decimal import Decimal
from typing import TYPE_CHECKING

from coin_report_trader.core.alerts import Mode
from coin_report_trader.core.clock import Clock
from coin_report_trader.core.ports import ExchangePort, PaperStore
from coin_report_trader.trading.live.switch import LIVE_TRADING_ALLOWED
from coin_report_trader.trading.live.upbit_exchange import UpbitExchange
from coin_report_trader.trading.paper.exchange import PaperExchange
from coin_report_trader.trading.upbit.client import UpbitClient

if TYPE_CHECKING:
    from coin_report_trader.trading.quote import QuoteClient


class LiveNotAllowed(Exception):
    """실거래 연결을 만들 수 없다: 실거래 허용 값이 거짓이거나 키가 없다."""


def select_exchange(
    mode: Mode,
    *,
    paper_store: PaperStore | None,
    client: UpbitClient | None,
    allowed: bool,
    quotes: "QuoteClient | None" = None,
    clock: Clock,
    min_order: Decimal | None = None,
) -> ExchangePort:
    """모드에 맞는 거래소 경계를 만든다.

    - `client`: 키 한 벌의 `UpbitClient`(운반, 키, 요청 한도 장치). `app`이 키마다 하나를 만들어
      이 함수와 계정 화면의 읽기에 함께 넘긴다.
    - `quotes`, `clock`, `min_order`: 모의매매의 체결 판정에 쓰는 공개 시세(최우선 호가와 1분봉),
      시계, 시험이 넣는 최소 주문 금액(`Services.min_order`)이다. 실거래는 `clock`만 쓴다.
    """
    if mode is Mode.PAPER:
        if paper_store is None or quotes is None:
            raise ValueError("모의매매에는 paper_store와 quotes가 있어야 한다")
        return PaperExchange(paper_store, quotes, clock, min_order=min_order)
    if mode is not Mode.LIVE:
        raise ValueError(f"모르는 모드다: {mode!r}")
    if not (allowed and LIVE_TRADING_ALLOWED):
        raise LiveNotAllowed("이 판에서는 실거래를 켤 수 없다")
    if client is None:
        raise LiveNotAllowed("업비트 키가 없다")
    return UpbitExchange(client, clock)
