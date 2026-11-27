"""3단계 잇기 2(M3 명세 4절 25번): 멈춤, 오류 멈춤, 전량 매도, 상태 띠의 값.

자료 스레드, 가짜 실행기, 가짜 시세(호가, 호가 단위, 1분봉), 가짜 시계로 본다. 진짜 `claude`와
거래소는 부르지 않는다. 값은 모두 만든 값이다(코인 `KRW-AAA`, `KRW-BBB`, 현재가 100, 호가 단위 1).
"""

import json
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fakes.trade_rig import (
    AAA,
    BBB,
    HOUR,
    PARTS,
    SAFETY,
    Rig,
    holding_seed,
    raise_in_minute_check,
    rig_builder,
    rounds,
    seeds,
)

from coin_report_trader.core import (
    AiStatus,
    AiStatusView,
    ErrorPauseCause,
    Mode,
    PauseKind,
    PauseStopLoss,
    QuoteError,
    TradeStatusView,
)
from coin_report_trader.core.texts import (
    AFTER_ERROR_PAUSE,
    ERROR_BLOCKED,
    STARTING_RECONCILE,
    STARTING_STOP_CHECK,
)
from coin_report_trader.storage import trading as trade_store


@pytest.fixture
def build(tmp_path: Path) -> Iterator[Callable[..., Rig]]:
    yield from rig_builder(tmp_path)


# --- 멈춤(3.10.1) ------------------------------------------------------------------------------


def test_pause_cancels_the_open_orders_and_stops_the_rounds_without_a_dialog(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    rig.answer(direction="오를 것", buy_price="99")
    rig.round()
    assert rig.orders() == [(AAA, "buy", "limit", "judgment", "open")]
    rig.worker.pause()
    rig.settle()
    assert rig.orders() == [(AAA, "buy", "limit", "judgment", "canceled")]
    status = rig.status()
    assert (status.user_paused, status.error_paused, status.stop_loss_off) == (True, False, False)
    assert rig.pauses() == [("user", None, 0, 1)]
    requests = len(rig.runner.requests)
    rig.advance(3600)
    assert rounds(rig) == [(1, "done")]
    assert len(rig.runner.requests) == requests  # 멈춤 동안 Claude를 부르지 않는다
    assert rig.quote.candle_requests()  # 봉 받기는 계속한다
    rig.worker.pause()
    rig.settle()
    assert rig.pauses() == [("user", None, 0, 1)]  # 이미 멈췄으면 달라지지 않는다


def test_a_pause_with_stop_loss_stop_turns_the_stop_check_off(build: Callable[..., Rig]) -> None:
    rig = build(seed=holding_seed(AAA, "100", "100"), pause_stop_loss=PauseStopLoss.STOP)
    rig.start()
    rig.worker.pause()
    rig.settle()
    assert rig.status().stop_loss_off is True
    rig.quote.set_prices({AAA: Decimal(90)})  # 손절선 95 아래
    rig.advance(60)
    assert rig.orders() == []  # '손절 중지'면 손절이 없다
    rig.worker.resume_user()
    rig.settle()
    assert [o[3] for o in rig.orders()] == ["stop_loss"]  # 풀면 손절 점검이 돈다


def test_a_pause_with_stop_loss_keep_keeps_the_stop_loss(build: Callable[..., Rig]) -> None:
    rig = build(seed=holding_seed(AAA, "100", "100"), pause_stop_loss=PauseStopLoss.KEEP)
    rig.start()
    rig.worker.pause()
    rig.settle()
    assert rig.status().stop_loss_off is False
    rig.quote.set_prices({AAA: Decimal(90)})
    rig.advance(60)
    assert rig.orders() == [(AAA, "sell", "market", "stop_loss", "filled")]


def test_a_pause_discards_the_running_round_and_its_report(build: Callable[..., Rig]) -> None:
    rig = build()
    rig.start()
    hold = threading.Event()
    started = rig.runner.plan(AAA, wait=hold)
    due = rig.worker._next_due
    assert due is not None
    rig.clock.advance((due - rig.clock.now()).total_seconds())
    assert started.wait(SAFETY)
    rig.worker.pause()
    rig.settle()
    assert rounds(rig) == [(1, "discarded")]
    assert rig.query("SELECT state, no_order_reason FROM reports") == [
        ("discarded", "멈춤으로 버림")
    ]
    assert rig.runner.stop_calls >= 1
    hold.set()  # 끝난 세션의 결과는 버린다
    rig.settle()
    time.sleep(0.05)
    rig.settle()
    assert rig.query("SELECT state FROM reports") == [("discarded",)]
    assert rig.orders() == []


def test_resuming_the_user_pause_reconciles_checks_the_stop_loss_and_runs_the_next_round(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    rig.worker.pause()
    rig.settle()
    rig.sink.items.clear()
    rig.worker.resume_user()
    rig.settle()
    steps = [s.starting_step for s in rig.sink.of(TradeStatusView)]
    assert steps[:2] == [STARTING_RECONCILE, STARTING_STOP_CHECK]
    assert steps[-1] is None
    assert rig.status().user_paused is False
    assert rig.pauses() == [("user", None, 0, 0)]
    rig.round()
    assert rounds(rig)[-1] == (1, "done")


# --- 오류 멈춤(3.10.2) -------------------------------------------------------------------------


def break_orders(rig: Rig) -> None:
    rig.execute(
        "CREATE TRIGGER no_orders BEFORE INSERT ON orders BEGIN SELECT RAISE(ABORT, 'x'); END"
    )


def test_an_order_write_failure_is_an_a29_and_an_error_pause_until_it_is_released(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    break_orders(rig)
    rig.answer(direction="오를 것", buy_price="99")
    rig.round()
    assert rig.codes().count("A-29") == 1
    (a29,) = [a for a in rig.alerts() if a.code == "A-29"]
    assert a29.detail is not None
    assert a29.detail.stage == "주문 저장"
    assert a29.detail.after == (
        "쓰지 못한 주문은 내지 않았고, 쓰지 못한 체결은 반영하지 않았습니다. " + AFTER_ERROR_PAUSE
    )
    status = rig.status()
    assert (status.error_paused, status.error_cause, status.stop_loss_off) == (
        True, ErrorPauseCause.SAVE, True,
    )  # fmt: skip
    assert rig.pauses() == [("error", "save", 0, 1)]
    prices = len(rig.quote.price_requests(AAA))
    rig.advance(3600)
    assert rounds(rig) == [(1, "done")]  # 풀기 전에는 회차가 없다
    assert len(rig.quote.price_requests(AAA)) == prices  # 손절 점검도 없다
    rig.execute("DROP TRIGGER no_orders")
    rig.worker.resume_error()
    rig.settle()
    assert rig.status().error_paused is False
    assert rig.pauses() == [("error", "save", 0, 0)]
    rig.answer(direction="오를 것", buy_price="99", result_text="지난 결과", compared=PARTS)
    rig.round()
    assert [o[4] for o in rig.orders()] == ["open"]


def test_a_stop_loss_fill_write_that_keeps_failing_enters_the_error_pause_again(
    build: Callable[..., Rig],
) -> None:
    rig = build(seed=holding_seed(AAA, "100", "100"))
    rig.start()
    rig.execute(
        "CREATE TRIGGER no_fills BEFORE INSERT ON fills BEGIN SELECT RAISE(ABORT, 'x'); END"
    )
    rig.quote.set_prices({AAA: Decimal(90)})
    rig.advance(60)
    assert rig.codes().count("A-29") == 1
    assert rig.status().error_paused is True
    rig.worker.resume_error()
    rig.settle()
    # 풀 때의 손절 점검이 같은 저장에 또 실패하면 다시 오류 멈춤이고 A-29가 다시 생긴다
    assert rig.status().error_paused is True
    assert rig.codes().count("A-29") == 2
    assert len(rig.pauses()) == 2


def test_a_block_is_an_error_pause_with_one_a23_and_no_requests_until_it_is_released(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.quote.script(QuoteError(ERROR_BLOCKED, 60.0), kind="candles", market=AAA, unit=HOUR)
    rig.start()
    assert rig.codes().count("A-23") == 1
    (a23,) = [a for a in rig.alerts() if a.code == "A-23"]
    assert a23.detail is not None
    assert (a23.detail.stage, a23.detail.error_name) == ("봉 받기", "일시 차단")
    assert a23.detail.after == "사용자가 풀 때까지 회차와 손절을 하지 않습니다."
    assert (
        a23.text
        == "거래소 오류(일시 차단)로 오류 멈춤에 들어갔습니다. 지금 손절이 동작하지 않습니다."
    )
    status = rig.status()
    assert (status.error_paused, status.error_cause, status.error_detail) == (
        True, ErrorPauseCause.EXCHANGE, ERROR_BLOCKED,
    )  # fmt: skip
    assert status.blocked_until is not None
    assert rig.pauses() == [("error", "exchange", 0, 1)]
    requests = len(rig.quote.requests)
    rig.advance(600)  # 차단 시각이 지나도 요청이 하나도 없다
    assert len(rig.quote.requests) == requests
    rig.worker.resume_error()
    rig.settle()
    assert rig.status().error_paused is False
    rig.round()  # 풀면 다음 예정 시각부터 돈다
    assert rounds(rig) == [(1, "done")]


def test_the_error_pause_cannot_be_released_before_the_block_ends(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.quote.script(QuoteError(ERROR_BLOCKED, 600.0), kind="candles", market=AAA, unit=HOUR)
    rig.start()
    rig.worker.resume_error()
    rig.settle()
    assert rig.status().error_paused is True
    rig.advance(600)
    rig.worker.resume_error()
    rig.settle()
    assert rig.status().error_paused is False


def test_a_user_pause_stays_when_the_error_pause_is_released(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    rig.start()
    rig.worker.pause()
    rig.settle()
    raise_in_minute_check(rig, monkeypatch)
    status = rig.status()
    assert (status.user_paused, status.error_paused) == (True, True)
    monkeypatch.undo()
    rig.worker.resume_error()
    rig.settle()
    status = rig.status()
    assert (status.user_paused, status.error_paused) == (True, False)
    assert [row[0] for row in rig.pauses()] == ["user", "error"]
    assert rig.pauses()[0][3] == 1  # 사용자 멈춤은 풀리지 않았다


def test_an_unexpected_exception_is_an_a30_and_an_error_pause(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build()
    rig.start()

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("뜻밖")

    monkeypatch.setattr(trade_store, "realized_pnl_between", broken)
    rig.advance(60)
    assert rig.codes().count("A-30") == 1
    (a30,) = [a for a in rig.alerts() if a.code == "A-30"]
    assert a30.detail is not None
    assert a30.detail.after == AFTER_ERROR_PAUSE
    status = rig.status()
    assert (status.error_paused, status.error_cause) == (True, ErrorPauseCause.UNEXPECTED)
    assert rig.pauses() == [("error", "unexpected", 0, 1)]


def test_the_ai_is_never_turned_off_by_an_error(build: Callable[..., Rig]) -> None:
    rig = build()
    rig.runner.plan(AAA, raises=RuntimeError("x"))
    rig.start()
    rig.round()
    assert rig.status().error_paused is True
    assert AiStatus.ERROR_OFF not in [v.status for v in rig.sink.of(AiStatusView)]


def test_the_pauses_are_kept_when_the_program_is_started_again(build: Callable[..., Rig]) -> None:
    def seed(conn: Any) -> None:
        now = datetime(2026, 1, 5, 2, tzinfo=UTC)
        trade_store.insert_pause(
            conn, mode=Mode.PAPER, kind=PauseKind.USER, cause=None, detail=None,
            sell_all=False, started_at=now,
        )  # fmt: skip
        trade_store.insert_pause(
            conn, mode=Mode.PAPER, kind=PauseKind.ERROR, cause=ErrorPauseCause.SAVE, detail=None,
            sell_all=False, started_at=now,
        )  # fmt: skip

    rig = build(seed=seed, coins=(AAA,))
    rig.start()
    status = rig.status()
    assert (status.user_paused, status.error_paused, status.error_cause) == (
        True, True, ErrorPauseCause.SAVE,
    )  # fmt: skip
    assert rig.quote.price_requests(AAA) == []  # 오류 멈춤은 켤 때도 대조와 손절 점검이 없다
    assert rig.quote.candle_requests()  # 원인이 일시 차단이 아니면 봉 받기는 한다


def test_an_alert_that_could_not_be_written_last_time_enters_the_error_pause(
    build: Callable[..., Rig],
) -> None:

    rig = build()
    line = {
        "kind": "memory", "created_utc": "2026-01-05T01:00:00Z", "mode": "paper",
        "category": "error", "code": "A-29", "coin": None, "text": "저장하지 못했습니다(DB 오류).",
        "stage": "알림 저장", "error_name": "DB 오류", "content": "x", "after": "y", "id": -1,
        "seen": False, "discord_state": "failed", "source": "app.data_thread",
    }  # fmt: skip
    (rig.worker._logs_dir / "startup-errors.jsonl").write_text(
        json.dumps(line, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    rig.start()
    status = rig.status()
    assert (status.error_paused, status.error_cause) == (True, ErrorPauseCause.SAVE)


# --- 전량 매도(3.10.3) -------------------------------------------------------------------------


def test_pause_and_sell_all_sells_every_coin_by_a_market_order_and_records_it(
    build: Callable[..., Rig],
) -> None:
    rig = build(
        coins=(AAA, BBB),
        seed=seeds(holding_seed(AAA, "100", "100"), holding_seed(BBB, "60", "100")),
    )
    rig.start()
    rig.worker.pause_and_sell_all()
    rig.settle()
    assert rig.orders() == [
        (AAA, "sell", "market", "sell_all", "filled"),
        (BBB, "sell", "market", "sell_all", "filled"),
    ]
    assert rig.coin_view(AAA).sell_all == "전량 매도: 끝남"
    assert rig.coin_view(BBB).sell_all == "전량 매도: 끝남"
    assert rig.pauses() == [("user", None, 1, 1)]
    assert rig.status().user_paused is True


def test_a_coin_that_could_not_be_sold_is_retried_every_minute_even_with_stop_loss_stop(
    build: Callable[..., Rig],
) -> None:
    rig = build(
        coins=(AAA, BBB),
        pause_stop_loss=PauseStopLoss.STOP,
        seed=seeds(holding_seed(AAA, "100", "100"), holding_seed(BBB, "60", "100")),
    )
    rig.start()
    rig.quote.script(*[QuoteError("네트워크 끊김")] * 50, kind="book", market=BBB)
    rig.worker.pause_and_sell_all()
    rig.settle()
    assert rig.coin_view(AAA).sell_all == "전량 매도: 끝남"
    assert rig.coin_view(BBB).sell_all == "전량 매도: 다시 시도하는 중"
    assert rig.codes().count("A-25") == 1  # 누름마다 코인마다 한 번
    (a25,) = [a for a in rig.alerts() if a.code == "A-25"]
    assert a25.coin == BBB
    rig.advance(60)
    assert rig.coin_view(BBB).sell_all == "전량 매도: 다시 시도하는 중"
    rig.quote.clear_script()
    rig.advance(60)
    assert rig.coin_view(BBB).sell_all == "전량 매도: 끝남"
    assert rig.codes().count("A-25") == 1


def test_an_error_pause_stops_the_sell_all_and_the_button_needs_a_pause_without_error(
    build: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build(seed=holding_seed(AAA, "100", "100"))
    rig.start()
    rig.quote.script(*[QuoteError("네트워크 끊김")] * 50, kind="book", market=AAA)
    rig.worker.pause_and_sell_all()
    rig.settle()
    raise_in_minute_check(rig, monkeypatch)
    assert rig.coin_view(AAA).sell_all == (
        "전량 매도: 오류 멈춤으로 그만둠. 오류 멈춤을 푼 뒤 다시 누르세요"
    )
    rig.worker.pause_and_sell_all()  # 오류 멈춤 동안은 누를 수 없다
    rig.settle()
    assert rig.orders() == []


def test_the_a25_row_of_the_problem_table_has_no_quantity(build: Callable[..., Rig]) -> None:
    from coin_report_trader.ai.csv_files import read_csv

    rig = build(seed=holding_seed(AAA, "100", "100"))
    rig.start()
    rig.quote.script(*[QuoteError("네트워크 끊김")] * 50, kind="book", market=AAA)
    rig.worker.pause_and_sell_all()
    rig.settle()
    path = rig.worker._problems.path_for(rig.clock.now())
    header, rows = read_csv(path.read_text(encoding="utf-8").lstrip(chr(0xFEFF)))
    found = [
        dict(zip(header, row, strict=True)) for row in rows if row[header.index("code")] == "A-25"
    ]
    assert [row["message"] for row in found] == ["AAA: 팔지 못한 수량이 있습니다."]
    assert found[0]["stage"] == "긴급 매도"
    assert found[0]["error_name"] == "네트워크 끊김"
