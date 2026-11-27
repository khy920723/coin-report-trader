"""3단계로 처음 설정을 마친 DB를 심는 도구(M3 명세 3.11.1, 3.11.3).

`write_stage3`가 쓴 DB는 `first_setup_level` "3", 3단계 키(아래 만든 값), 가상 계정
(`paper_accounts`의 한 줄)을 갖는다. 23번 뒤에는 이것이 있어야 처음 설정을 마친 기록이다(예전
기록 "1", "2"는 처음 설정을 다시 보인다). 값은 모두 만든 값이다.

방식의 처음 값은 평균회귀다. 가짜 실행기의 기본 답('오를 것')과 보유 없음은 판단표에서 "아무것도
하지 않음"이라(3.5.2), 주문을 보지 않는 앞 단계의 시험에 주문과 매매 알림이 더해지지 않는다.
"""

import sqlite3
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from coin_report_trader.core import PauseStopLoss, SettingsView, TradeMethod, TradeSettings
from coin_report_trader.storage.settings import write_settings

SEEDED_AT = datetime(2026, 1, 1, tzinfo=UTC)
"""가상 계정을 만든 때로 적는 만든 시각."""


def stage3_trade(
    coins: Iterable[str], *, method: TradeMethod = TradeMethod.MEAN_REVERSION
) -> TradeSettings:
    """고른 코인의 투자 비율을 나눈 3단계 값(예산 1,000,000원, 첫 매수 20%, 추가 10%)."""
    markets = tuple(coins)
    ratios: dict[str, Decimal] = {}
    if markets:
        each = (Decimal(100) / len(markets)).quantize(Decimal("0.01"), rounding="ROUND_DOWN")
        ratios = dict.fromkeys(markets, each)
        ratios[markets[-1]] = Decimal(100) - each * (len(markets) - 1)
    return TradeSettings(
        budget=Decimal(1_000_000),
        coin_ratios=ratios,
        first_ratio=Decimal(20),
        mr_first_ratio=Decimal(20),
        add_ratio=Decimal(10),
        tolerance=Decimal(0),
        stop_ratio=Decimal(5),
        daily_loss_limit=Decimal(3000),
        order_interval_s=60,
        method=method,
        pause_stop_loss=PauseStopLoss.KEEP,
        mr_sell_unknown_fee=False,
        mr_tolerable_loss=Decimal(5),
        paper_funds=Decimal(1_000_000),
        backup_days=7,
    )


def write_stage3(conn: sqlite3.Connection, view: SettingsView, **kwargs: object) -> None:
    """`view`(처음 설정을 마친 값)에 3단계 값을 더해 쓴다. `kwargs`는 `write_settings`에 넘긴다."""
    trade = view.trade if view.trade is not None else stage3_trade(view.coins)
    write_settings(conn, replace(view, trade=trade), created_at=SEEDED_AT, **kwargs)  # type: ignore[arg-type]
