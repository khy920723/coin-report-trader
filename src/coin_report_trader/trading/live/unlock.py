"""실거래 켜기의 조건(M5 명세 3.7.2, 제품 규칙 9절).

계산만 한다. 저장된 기록(보고서, 설정 바뀜, 멈춤 기록, 키, 계정)은 `app`이 모아 넘긴다(15번).

- `live_blocks`: 조건 1~7의 막힌 까닭(`LiveBlock`). 비면 켤 수 있다(조건 8은 계정을 읽은 뒤
  `live_budget_fits`로 본다).
- `count_paper_rounds`: 조건 5의 모의 회차를 세는 셈. 세는 회차는 그 코인의 모의매매 보고서 가운데
  `OK`이고 지금 매매 방식이며, 그 코인의 마지막 '0부터 다시 셈'보다 뒤인 것이다. '0부터 다시 셈'은
  코인을 새로 고름(`coin_added`), 매매 방식을 바꿈(`method_changed`), 봉 개수를 바꿈
  (`candle_count_changed`), N을 바꿈(`n_changed`)이다. 코인이 없는 바뀜(`market`이 `None`)은 고른
  코인 모두에 든다.
- `live_settings_ok`: 조건 7. 실거래 설정이 다 있고(`LiveSettings.complete`) 그 설정으로 바꾼
  `TradeSettings`가 받지 않는 값(`check_trade_settings`)에 걸리지 않는다(3.7.5).
- `live_budget_fits`: 조건 8의 자산 검사. 실거래 예산이 원화 잔고(주문 가능)와 고른 코인 보유의
  평가 금액((`balance` + `locked`) × 현재가)의 합보다 크지 않으면 된다(같으면 된다).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final

from coin_report_trader.core.alerts import Mode
from coin_report_trader.core.exchange import ExchangeAccount, LiveBlock, LiveSettings
from coin_report_trader.core.trade import TradeMethod, TradeSettings
from coin_report_trader.trading.orders.settings_check import check_trade_settings
from coin_report_trader.trading.risk.pause import PauseState

RESET_KINDS: Final = frozenset(
    {"coin_added", "method_changed", "candle_count_changed", "n_changed"}
)
"""모의 회차를 0부터 다시 세게 하는 설정 바뀜(`setting_events.kind`)."""


@dataclass(frozen=True)
class RoundReport:
    """세는 데 쓰는 보고서 한 줄(`reports`): 코인, 모드, `OK`인지, 그 회차의 매매 방식, 때."""

    market: str
    mode: Mode
    ok: bool
    method: TradeMethod | None
    """3단계 전(2단계)의 보고서는 `None`이라 세지 않는다(M3 명세 3.16.2)."""
    utc: datetime


@dataclass(frozen=True)
class SettingEvent:
    """설정 바뀜 한 줄(`setting_events`): 종류, 코인(없으면 모든 코인), 때."""

    kind: str
    market: str | None
    utc: datetime


def count_paper_rounds(
    market: str,
    method: TradeMethod,
    reports: Iterable[RoundReport],
    events: Iterable[SettingEvent],
) -> int:
    """그 코인의 세는 모의 회차의 수(조건 5)."""
    resets = [
        event.utc
        for event in events
        if event.kind in RESET_KINDS and event.market in (None, market)
    ]
    since = max(resets) if resets else None
    return sum(
        1
        for report in reports
        if report.market == market
        and report.mode is Mode.PAPER
        and report.ok
        and report.method is method
        and (since is None or report.utc > since)
    )


def live_settings_ok(
    live: LiveSettings | None,
    trade: TradeSettings,
    coins: tuple[str, ...],
    min_order: Decimal,
) -> bool:
    """조건 7: 실거래 설정이 다 있고 받지 않는 값에 걸리지 않는다."""
    if live is None or not live.complete(coins):
        return False
    return not check_trade_settings(live.apply(trade), coins, min_order)


def live_blocks(
    *,
    allowed: bool,
    key_present: bool,
    pause: PauseState,
    coins: tuple[str, ...],
    counts: Mapping[str, int],
    rounds_n: int,
    sell_all_done: bool,
    settings_ok: bool,
) -> tuple[LiveBlock, ...]:
    """조건 1~7의 막힌 까닭을 조건의 차례로 준다. 비면 켤 수 있다.

    - `allowed`: 실거래 허용 값(조건 1). `key_present`: 보관소에 두 값이 다 있음(조건 2).
    - `pause`: 사용자 멈춤이고 오류 멈춤이 아니어야 한다(조건 3).
    - `coins`: 고른 코인(조건 4). `counts`: 코인마다 센 모의 회차(`count_paper_rounds`),
      `rounds_n`: N(조건 5. 모자란 코인이 여럿이어도 까닭은 하나다. 화면의 글은 `counts`로 채운다).
    - `sell_all_done`: 모의매매의 '멈추고 전량 매도' 기록이 있음(조건 6).
    - `settings_ok`: `live_settings_ok`의 결과(조건 7).
    """
    if rounds_n <= 0:
        raise ValueError("N은 0보다 커야 한다")
    blocks: list[LiveBlock] = []
    if not allowed:
        blocks.append(LiveBlock.NOT_ALLOWED)
    if not key_present:
        blocks.append(LiveBlock.NO_KEY)
    if not pause.user or pause.error:
        blocks.append(LiveBlock.NOT_PAUSED)
    if not coins:
        blocks.append(LiveBlock.NO_COINS)
    elif any(counts.get(coin, 0) < rounds_n for coin in coins):
        blocks.append(LiveBlock.NOT_ENOUGH_ROUNDS)
    if not sell_all_done:
        blocks.append(LiveBlock.NO_SELL_ALL)
    if not settings_ok:
        blocks.append(LiveBlock.NO_SETTINGS)
    return tuple(blocks)


def live_budget_fits(
    budget: Decimal,
    account: ExchangeAccount,
    prices: Mapping[str, Decimal | None],
) -> tuple[bool, Decimal | None]:
    """조건 8: (예산 ≤ 합, 합). 합은 원화 잔고(주문 가능) + 고른 코인 보유의 평가 금액이다.

    `prices`의 열쇠가 고른 코인이다(값은 현재가. 읽지 못했으면 `None`). 고른 코인에 보유가 있는데
    현재가를 읽지 못했으면 셈하지 않고 `(False, None)`이다(화면의 "현재가를 읽지 못해 확인하지
    못했습니다.").
    고르지 않은 코인의 보유는 합에 넣지 않는다.
    """
    total = account.krw
    for holding in account.holdings:
        if holding.market not in prices:
            continue
        qty = holding.balance + holding.locked
        if qty <= 0:
            continue
        price = prices[holding.market]
        if price is None:
            return False, None
        total += qty * price
    return budget <= total, total
