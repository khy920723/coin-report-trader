"""M3 명세 6절 D20~D26, D35: 멈춤, 멈추고 전량 매도, 오류 멈춤, 알림만 내는 저장 실패.

자료 스레드, 보내기 스레드, 가짜 실행기, 가짜 시세, 가짜 시계, 가짜 보관소와 진짜 창(확인 창과
상태 띠)으로 본다. 거래소와 진짜 `claude`를 부르지 않는다. 값은 모두 만든 값이다.
"""

import sqlite3
import threading
from collections.abc import Callable, Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from acceptance.m2_support import webhook_url
from acceptance.m3_d_support import (
    AAA,
    BBB,
    HOUR,
    PARTS,
    Rig,
    advance,
    all_codes,
    answer,
    apply_all,
    close_window,
    coin_view,
    d_rig,
    d_trade,
    fills,
    holding_seed,
    open_window,
    orders,
    pauses,
    seeds,
    status,
)

from coin_report_trader.core import (
    AlertCategory,
    ErrorPauseCause,
    HistoryTab,
    HistoryView,
    PauseStopLoss,
    QuoteError,
    TradeStatusView,
)
from coin_report_trader.core.texts import ERROR_BLOCKED, STARTING_RECONCILE
from coin_report_trader.storage import trading as trade_store
from coin_report_trader.ui.texts import BAND_PAUSE_USER, BAND_STOP_OFF

D = Decimal
BOOK = "book"
CANDLES = "candles"


@pytest.fixture
def build(tmp_path: Path) -> Iterator[Callable[..., Rig]]:
    rigs: list[Rig] = []

    def factory(**kwargs: Any) -> Rig:
        rig = d_rig(tmp_path / f"r{len(rigs)}", **kwargs)
        rigs.append(rig)
        return rig

    yield factory
    for rig in rigs:
        rig.stop()


def reports(rig: Rig) -> list[tuple[Any, ...]]:
    return rig.rows("SELECT state, order_id, no_order_reason FROM reports ORDER BY id")


def rounds(rig: Rig) -> list[tuple[Any, ...]]:
    return rig.rows("SELECT number, state FROM rounds ORDER BY id")


def execute(rig: Rig, sql: str) -> None:
    """돌고 있는 앱 밖에서 DB에 바로 쓴다(저장 실패를 일으키는 장치를 건다)."""
    conn = sqlite3.connect(rig.db)
    try:
        conn.execute(sql)
        conn.commit()
    finally:
        conn.close()


def break_table(rig: Rig, table: str) -> None:
    sql = f"CREATE TRIGGER no_{table} BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT, 'x'); END"
    execute(rig, sql)


def labels(window: Any, name: str) -> str | None:
    from PySide6.QtWidgets import QLabel

    label = window.findChild(QLabel, name)
    assert label is not None, name
    return None if label.isHidden() else label.text()


# --- D20: 멈춤 ------------------------------------------------------------------------------------


@pytest.mark.scenario("D20")
def test_d20_a_pause_cancels_the_open_order_without_a_dialog_and_stops_the_rounds(
    build: Callable[..., Rig], qapp: Any
) -> None:
    rig = build()
    rig.start()
    answer(rig, direction="오를 것", buy_price="990")
    rig.round()
    assert [row[4] for row in orders(rig)] == ["open"]
    window = open_window(rig)
    try:
        asked = _Confirm(False)
        window.set_confirm_handler(asked)
        window.findChild(_button(), "pauseButton").click()
        rig.settle()
        assert asked.asked == []  # 확인 창이 뜨지 않는다
        apply_all(window, rig)
        assert [row[4] for row in orders(rig)] == ["canceled"]
        assert (status(rig).user_paused, status(rig).stop_loss_off) == (True, False)
        assert labels(window, "tradeBandPause") == BAND_PAUSE_USER
        requests = len(rig.runner.requests)
        rig.to_next_due()
        assert len(rig.runner.requests) == requests  # 다음 봉의 때에 회차와 세션이 없다
        assert rounds(rig) == [(1, "done")]
    finally:
        close_window(window)


@pytest.mark.scenario("D20")
def test_d20_with_stop_loss_stop_a_pause_leaves_no_stop_loss(
    build: Callable[..., Rig], qapp: Any
) -> None:
    trade = d_trade((AAA,), pause_stop_loss=PauseStopLoss.STOP)
    rig = build(trade=trade, seed=holding_seed(AAA, "10", "980"))
    rig.start()
    rig.data.pause()
    rig.settle()
    window = open_window(rig)
    try:
        apply_all(window, rig)
        assert status(rig).stop_loss_off is True
        assert labels(window, "tradeBandStopOff") == BAND_STOP_OFF  # 지금 손절이 동작하지 않음
    finally:
        close_window(window)
    rig.quote.set_prices({AAA: D(930)})
    advance(rig, 120)
    assert orders(rig) == []  # 손절이 없다


@pytest.mark.scenario("D20")
def test_d20_with_stop_loss_keep_a_pause_keeps_the_stop_loss(build: Callable[..., Rig]) -> None:
    trade = d_trade((AAA,), pause_stop_loss=PauseStopLoss.KEEP)
    rig = build(trade=trade, seed=holding_seed(AAA, "10", "980"))
    rig.start()
    rig.data.pause()
    rig.settle()
    assert status(rig).stop_loss_off is False
    rig.quote.set_prices({AAA: D(930)})
    advance(rig, 60)
    assert [row[3:5] for row in orders(rig)] == [("stop_loss", "filled")]  # 손절이 돈다


# --- D21: 도는 회차의 멈춤 ------------------------------------------------------------------------


@pytest.mark.scenario("D21")
def test_d21_a_pause_discards_the_running_round_and_the_history_shows_it(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    hold = threading.Event()
    started = rig.runner.plan(AAA, wait=hold)
    due = rig.data._next_due
    assert due is not None
    rig.clock.advance((due - rig.clock.now()).total_seconds())
    assert started.wait(10.0)
    rig.data.pause()
    rig.settle()
    assert rounds(rig) == [(1, "discarded")]
    assert reports(rig) == [("discarded", None, "멈춤으로 버림")]
    assert rig.runner.stop_calls >= 1  # 세션은 끝난다
    hold.set()
    rig.settle()
    assert reports(rig)[0][0] == "discarded"  # 끝난 세션의 결과는 버린다
    rig.data.load_history(HistoryTab.ROUNDS, None, date(2026, 1, 5))
    rig.settle()
    view = rig.sink.of(HistoryView)[-1]
    assert [row.cells[3] for row in view.rows] == ["멈춤으로 버림"]


# --- D22: 멈추고 전량 매도 ------------------------------------------------------------------------


def _two_coin_holdings() -> Callable[[Any], None]:
    return seeds(holding_seed(AAA, "10", "900"), holding_seed(BBB, "20", "900"))


@pytest.mark.scenario("D22")
def test_d22_cancel_does_nothing_and_confirm_pauses_and_sells_both_coins_at_market(
    build: Callable[..., Rig], qapp: Any
) -> None:
    rig = build(coins=(AAA, BBB), seed=_two_coin_holdings())
    rig.start()
    window = open_window(rig)
    try:
        sell = window.findChild(_button(), "sellAllButton")
        cancel = _Confirm(False)
        window.set_confirm_handler(cancel)
        sell.click()
        rig.settle()
        assert len(cancel.asked) == 1
        assert orders(rig) == []
        assert pauses(rig) == []
        window.set_confirm_handler(_Confirm(True))
        sell.click()
        rig.settle()
        assert [(row[0], row[1], row[2], row[3], row[4]) for row in orders(rig)] == [
            (AAA, "sell", "market", "sell_all", "filled"),
            (BBB, "sell", "market", "sell_all", "filled"),
        ]  # 멈춤과 두 코인의 시장가 매도 한 건씩
        assert pauses(rig) == [("user", None, 1, 1)]  # `pauses.sell_all` 1
        assert coin_view(rig, AAA).sell_all == "전량 매도: 끝남"
        assert coin_view(rig, BBB).sell_all == "전량 매도: 끝남"
        assert status(rig).user_paused is True
        assert len(fills(rig)) == 2
    finally:
        close_window(window)


# --- D23: 전량 매도의 다시 시도 -------------------------------------------------------------------


@pytest.mark.scenario("D23")
@pytest.mark.parametrize("stop_loss", [PauseStopLoss.KEEP, PauseStopLoss.STOP])
def test_d23_a_coin_that_cannot_be_sold_is_retried_every_minute_even_with_stop_loss_stop(
    build: Callable[..., Rig], stop_loss: PauseStopLoss
) -> None:
    trade = d_trade((AAA, BBB), pause_stop_loss=stop_loss)
    rig = build(coins=(AAA, BBB), trade=trade, seed=_two_coin_holdings())
    rig.start()
    unreadable = QuoteError("네트워크 끊김")
    rig.quote.script(unreadable, unreadable, kind=BOOK, market=BBB)
    rig.data.pause_and_sell_all()
    rig.settle()
    assert coin_view(rig, AAA).sell_all == "전량 매도: 끝남"
    assert coin_view(rig, BBB).sell_all == "전량 매도: 다시 시도하는 중"
    advance(rig, 60)  # 한 번 더 못 읽는다
    assert coin_view(rig, BBB).sell_all == "전량 매도: 다시 시도하는 중"
    advance(rig, 60)  # 읽게 되면 끝난다
    assert coin_view(rig, BBB).sell_all == "전량 매도: 끝남"
    assert [row[0] for row in orders(rig)] == [AAA, BBB]
    assert all_codes(rig).count("A-25") == 1  # 누름마다 코인마다 한 번


# --- D24: 저장 실패의 오류 멈춤 -------------------------------------------------------------------


@pytest.mark.scenario("D24")
def test_d24_an_order_write_failure_is_an_a29_and_an_error_pause_until_it_is_released(
    build: Callable[..., Rig], qapp: Any
) -> None:
    rig = build()
    rig.start()
    break_table(rig, "orders")
    answer(rig, direction="오를 것", buy_price="990")
    rig.round()
    assert rig.codes().count("A-29") == 1
    assert (status(rig).error_paused, status(rig).stop_loss_off) == (True, True)
    window = open_window(rig)
    try:
        apply_all(window, rig)
        assert "오류 멈춤" in (labels(window, "tradeBandPause") or "")
        assert labels(window, "tradeBandStopOff") == BAND_STOP_OFF
    finally:
        close_window(window)
    prices = len(rig.quote.price_requests(AAA))
    advance(rig, 3600)  # 풀기 전에는 회차와 손절 점검이 없다
    assert rounds(rig) == [(1, "done")]
    assert len(rig.quote.price_requests(AAA)) == prices
    execute(rig, "DROP TRIGGER no_orders")
    rig.data.resume_error()
    rig.settle()
    assert status(rig).error_paused is False  # 대조가 끝나면 돈다
    answer(rig, direction="오를 것", buy_price="990", result_text="지난 결과", compared=PARTS)
    rig.round()
    assert [row[4] for row in orders(rig)] == ["open"]


@pytest.mark.scenario("D24")
def test_d24_a_stop_loss_fill_write_that_keeps_failing_enters_the_error_pause_again(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=holding_seed(AAA, "10", "980"))
    rig.start()
    break_table(rig, "fills")
    rig.quote.set_prices({AAA: D(930)})
    advance(rig, 60)
    assert rig.codes().count("A-29") == 1
    assert status(rig).error_paused is True
    rig.data.resume_error()
    rig.settle()
    # 풀 때의 손절 점검이 같은 저장에 또 실패한다: 같은 단계의 A-29가 아직 성공하지 않았어도
    assert status(rig).error_paused is True
    assert rig.codes().count("A-29") == 2
    assert len(pauses(rig)) == 2


# --- D25: 일시 차단 -------------------------------------------------------------------------------


@pytest.mark.scenario("D25")
def test_d25_a_block_is_an_error_pause_with_a23_and_no_requests_until_it_is_released(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.quote.script(QuoteError(ERROR_BLOCKED, 60.0), kind=CANDLES, market=AAA, unit=HOUR)
    rig.start()
    assert all_codes(rig).count("A-23") == 1
    current = status(rig)
    assert (current.error_paused, current.error_cause) == (True, ErrorPauseCause.EXCHANGE)
    assert current.blocked_until is not None
    requests = len(rig.quote.requests)
    advance(rig, 600)  # 차단 시각이 지나도 회차와 봉 받기가 없다
    assert len(rig.quote.requests) == requests
    assert rounds(rig) == []
    rig.data.resume_error()
    rig.settle()
    assert status(rig).error_paused is False
    rig.round()  # 풀면 대조 뒤 돈다
    assert rounds(rig) == [(1, "done")]


@pytest.mark.scenario("D25")
def test_d25_the_error_pause_of_a_block_cannot_be_released_before_the_block_ends(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.quote.script(QuoteError(ERROR_BLOCKED, 600.0), kind=CANDLES, market=AAA, unit=HOUR)
    rig.start()
    rig.data.resume_error()
    rig.settle()
    assert status(rig).error_paused is True
    advance(rig, 600)
    rig.data.resume_error()
    rig.settle()
    assert status(rig).error_paused is False
    assert STARTING_RECONCILE in [v.starting_step for v in rig.sink.of(TradeStatusView)]


# --- D26: 두 멈춤이 함께 --------------------------------------------------------------------------


@pytest.mark.scenario("D26")
def test_d26_releasing_the_error_pause_leaves_the_user_pause(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    rig.start()
    rig.data.pause()
    rig.settle()

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("뜻밖")

    # 이 시험의 바꿈만 되돌린다(`monkeypatch.undo()`는 `conftest.py`가 건 자료 폴더 값까지 되돌린다)
    with monkeypatch.context() as patch:
        patch.setattr(trade_store, "realized_pnl_between", broken)
        advance(rig, 60)
        assert (status(rig).user_paused, status(rig).error_paused) == (True, True)
    rig.data.resume_error()
    rig.settle()
    assert (status(rig).user_paused, status(rig).error_paused) == (True, False)
    assert [(row[0], row[3]) for row in pauses(rig)] == [("user", 1), ("error", 0)]


# --- D35: 오류 멈춤이 아닌 저장 실패 --------------------------------------------------------------


@pytest.mark.scenario("D35")
def test_d35_a_vault_save_failure_is_only_an_a29_and_the_rounds_and_stop_loss_go_on(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=holding_seed(AAA, "10", "980"))
    rig.start()
    rig.vault.fail_set()
    rig.send.save_webhook(AlertCategory.ERROR, webhook_url("v"))
    rig.settle()
    (a29,) = [a for a in rig.alerts() if a.code == "A-29"]
    assert a29.detail is not None
    assert a29.detail.stage == "주소 저장"
    assert status(rig).error_paused is False
    assert pauses(rig) == []
    _rounds_and_stop_loss_still_run(rig)


@pytest.mark.scenario("D35")
def test_d35_a_backup_failure_is_only_an_a29_and_the_rounds_and_stop_loss_go_on(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=holding_seed(AAA, "10", "980"))
    (rig.root / "backups").write_text("파일", encoding="utf-8")  # 폴더를 만들 수 없다
    rig.start()
    (a29,) = [a for a in rig.alerts() if a.code == "A-29"]
    assert a29.detail is not None
    assert a29.detail.stage == "DB 백업"
    assert status(rig).error_paused is False
    assert pauses(rig) == []
    # 하루가 바뀌는 때: 다시 해도 성공하기 전에는 같은 A-29를 또 만들지 않는다
    advance(rig, 24 * 3600)
    assert all_codes(rig).count("A-29") == 1
    _rounds_and_stop_loss_still_run(rig)


@pytest.mark.scenario("D35")
def test_d35_a_fill_write_failure_is_an_a29_and_an_error_pause(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=holding_seed(AAA, "10", "980"))
    rig.start()
    break_table(rig, "fills")
    rig.quote.set_prices({AAA: D(930)})
    advance(rig, 60)
    (a29,) = [a for a in rig.alerts() if a.code == "A-29"]
    assert a29.detail is not None
    assert a29.detail.stage == "주문 저장"
    assert status(rig).error_paused is True
    assert pauses(rig)[0][:2] == ("error", "save")


def _rounds_and_stop_loss_still_run(rig: Rig) -> None:
    """회차가 돌고 손절이 나간다(오류 멈춤이 아니다)."""
    answer(rig, direction="오를 것", buy_price="990")
    rig.round()
    assert rounds(rig)[-1][1] == "done"
    rig.quote.set_prices({AAA: D(930)})
    advance(rig, 60)
    assert "stop_loss" in [row[3] for row in orders(rig)]


# --- 도우미 ---------------------------------------------------------------------------------------


def _button() -> Any:
    from PySide6.QtWidgets import QPushButton

    return QPushButton


class _Confirm:
    """확인 창을 대신하는 것: 정한 답을 주고 물은 것을 적는다."""

    def __init__(self, answer_: bool) -> None:
        self.answer = answer_
        self.asked: list[tuple[str, str]] = []

    def __call__(self, title: str, body: str) -> bool:
        self.asked.append((title, body))
        return self.answer

