"""M1·M2의 수용 시험이 심는 '처음 설정을 마친 DB'가 3단계의 꼴이다(M3 명세 4절 31번).

3단계로 마친 DB는 `first_setup_level` "3", 3단계의 키(3.11.1), 가상 계정(`paper_accounts`의 한 줄)이
있다. 23번이 `first_setup_done`을 그렇게 바꾼 뒤에도 같은 시험이 통과하려면 이 꼴이어야 한다. 1단계
보완 전의 기록("1". 봉 단위나 개수가 없음)은 옛 기록 그대로 둔다. 값은 모두 만든 값이다.
"""

import sqlite3
from decimal import Decimal
from pathlib import Path

from acceptance.m1_support import AAA, BBB, make_rig
from acceptance.m2_support import make_rig as make_m2_rig
from acceptance.m2_support import seed_db
from acceptance.m3_support import PLANTED_PRICE, planted_trade, release_error_pause
from fakes.quote import FakeQuoteSource

from coin_report_trader.core import TradeMethod

KEYS = {
    "budget",
    "first_ratio",
    "mr_first_ratio",
    "add_ratio",
    "tolerance",
    "stop_ratio",
    "daily_loss_limit",
    "order_interval_s",
    "trade_method",
    "pause_stop_loss",
    "mr_sell_unknown_fee",
    "mr_tolerable_loss",
    "paper_funds",
    "backup_days",
}


def stored(db: Path) -> tuple[dict[str, str], list[tuple[object, ...]]]:
    conn = sqlite3.connect(db)
    try:
        settings = dict(conn.execute("SELECT key, value FROM settings").fetchall())
        accounts = conn.execute(
            "SELECT mode, funds, krw, locked_krw FROM paper_accounts"
        ).fetchall()
        holdings = conn.execute("SELECT COUNT(*) FROM paper_holdings").fetchone()
    finally:
        conn.close()
    assert holdings == (0,)
    return settings, accounts


def test_the_planted_trade_values_are_the_made_ones() -> None:
    trade = planted_trade((AAA, BBB))
    assert set(trade.coin_ratios) == {AAA, BBB}
    assert sum(trade.coin_ratios.values()) == Decimal(100)
    assert (trade.budget, trade.method) == (Decimal(1_000_000), TradeMethod.TREND)
    assert (trade.stop_ratio, trade.daily_loss_limit, trade.order_interval_s) == (5, 3000, 60)


def test_a_finished_m2_db_is_stage_three_with_the_keys_and_a_paper_account(tmp_path: Path) -> None:
    db = seed_db(tmp_path, coins=(AAA, BBB))
    settings, accounts = stored(db)
    assert settings["first_setup_level"] == "3"
    assert set(settings) >= KEYS
    assert {f"coin_ratio:{AAA}", f"coin_ratio:{BBB}"} <= set(settings)
    assert accounts == [("paper", "1000000", "1000000", "0")]


def test_the_four_ai_slots_are_the_only_thing_missing_when_the_choices_are_empty(
    tmp_path: Path,
) -> None:
    db = seed_db(tmp_path, coins=(AAA,), choices=())  # C27
    settings, accounts = stored(db)
    assert settings["first_setup_level"] == "3"
    assert set(settings) >= KEYS
    assert not [key for key in settings if key.startswith(("ai_model:", "ai_effort:"))]
    assert len(accounts) == 1


def test_a_finished_m1_db_is_stage_three_and_gets_a_price_for_every_chosen_coin(
    tmp_path: Path,
) -> None:
    rig = make_rig(tmp_path, coins=(AAA, BBB))
    settings, accounts = stored(rig.db)
    assert settings["first_setup_level"] == "3"
    assert set(settings) >= KEYS
    assert accounts == [("paper", "1000000", "1000000", "0")]
    assert rig.quote.get_price(AAA) == PLANTED_PRICE == rig.quote.get_price(BBB)


def test_an_older_m1_record_without_a_unit_stays_the_older_record(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, unit=None, count=None)  # 1단계 보완 전에 마친 기록("1")
    settings, accounts = stored(rig.db)
    assert settings["first_setup_level"] == "1"
    assert not KEYS & set(settings)
    assert accounts == []


def test_an_unfinished_m1_db_has_no_stage_three_values(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, done=False)
    settings, accounts = stored(rig.db)
    assert "first_setup_level" not in settings
    assert accounts == []


def test_release_error_pause_does_nothing_before_the_data_thread_reports_a_pause(
    tmp_path: Path,
) -> None:
    rig = make_m2_rig(tmp_path / "m2")
    rig.start()
    assert release_error_pause(rig) is False  # 오류 멈춤이 없다(25번 앞은 멈춤의 값도 없다)
    assert isinstance(rig.quote, FakeQuoteSource)
