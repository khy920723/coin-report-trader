"""M2 명세 6절의 회차 시나리오: C1, C2, C3, C20, C21, C22, C25, C26(3.5, 3.8, 3.9).

자료 스레드, 보내기 스레드, 가짜 실행기, 가짜 시세, 가짜 시계를 함께 돌린다
(`acceptance.m2_support`). 거래소와 진짜 `claude`는 부르지 않는다. 값은 모두 만든 값이다. C1의
스레드 확인(AI 스레드는 DB를 열지 않음, `apply`는 주 스레드)은 창을 띄우는
`test_m2_c_window_run.py`에 있다.
"""

import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from acceptance.m2_support import (
    AAA,
    BBB,
    FIRST_CLOSE,
    HOUR,
    THIS_HOUR,
    Rig,
    fake_quote,
    make_rig,
    token_of,
    webhook_url,
)
from fakes.clock import FakeClock
from fakes.quote import make_candles

from coin_report_trader.ai import inputs as ai_inputs
from coin_report_trader.ai.csv_files import read_csv
from coin_report_trader.core import (
    BAR_STATS_COLUMNS,
    AlertCategory,
    CoinView,
    LoadState,
    Mode,
    QuoteError,
    ReportListView,
    ReportState,
)
from coin_report_trader.core.texts import ERROR_NETWORK
from coin_report_trader.storage import reports as report_store
from coin_report_trader.ui.report_screen import round_end_text

# 3.8의 머리줄(명세의 글을 그대로 옮긴다. 코드의 상수와 따로 적어 서로 견준다).
ROWS = ("market", "kind", "seq", "part", "ref_seq", "text", "url", "verdict", "belief",
        "compared", "hits")  # fmt: skip
NOW_COLUMNS = ("market", "current_price", "price_read_utc", "unit", "round_close_utc",
               "next_round_utc")  # fmt: skip
LAST_JUDGMENT_COLUMNS = ("round_number", "round_close_utc", "base_price", "direction",
                         "predicted_price", "predicted_reason", "result_text", "target_close_utc",
                         "actual_price", "diff", "hit")  # fmt: skip
HEADERS = {
    "search_input.csv": ("market", "symbol", "unit", "round_close_utc", "next_round_utc"),
    "candle_stats.csv": tuple(BAR_STATS_COLUMNS),
    "final_now.csv": NOW_COLUMNS,
    "final_last_judgment.csv": LAST_JUDGMENT_COLUMNS,
    "final_last_report.csv": ("answer_part", *ROWS),
    "final_trades.csv": ("market", "kind", "price", "utc"),
}
SAFETY = 10.0


@pytest.fixture
def build(tmp_path: Path) -> Iterator[Callable[..., Rig]]:
    made: list[Rig] = []

    def factory(**kwargs: Any) -> Rig:
        rig = make_rig(tmp_path / f"r{len(made)}", **kwargs)
        made.append(rig)
        return rig

    yield factory
    for rig in made:
        rig.stop()


def table(text: str) -> tuple[tuple[str, ...], list[list[str]]]:
    header, rows = read_csv(text.removeprefix(chr(0xFEFF)))
    return tuple(header), [list(row) for row in rows]


def order_rows(rig: Rig) -> int:
    """주문에 관한 표(이름에 order나 fill이 든 표)의 줄 수. 2단계에는 표도 줄도 없다."""
    names = [n for (n,) in rig.rows("SELECT name FROM sqlite_master WHERE type = 'table'")]
    found = [n for n in names if "order" in n or "fill" in n]
    return sum(rig.rows(f'SELECT COUNT(*) FROM "{name}"')[0][0] for name in found)


def two_coin_round(build: Callable[..., Rig], **kwargs: Any) -> Rig:
    """C1: 코인 둘, 저장된 완성된 봉이 고른 개수 이상, 두 코인에 정상 답. 회차 1을 돌린다."""
    rig = build(coins=(AAA, BBB), **kwargs)
    rig.start()
    hold = threading.Event()
    first = rig.runner.plan(AAA, wait=hold)
    second = rig.runner.plan(BBB)
    rig.to_next_due(10)  # 다음 봉이 끝나는 때 + 10초
    assert first.wait(SAFETY)
    # 둘째 세션은 첫째(붙잡힌 채)의 끝을 기다리지 않고 시작한다.
    assert second.wait(SAFETY)
    hold.set()
    rig.drain()
    return rig


# --- C1 ---------------------------------------------------------------------------------------


@pytest.mark.scenario("C1")
def test_c1_a_round_of_two_coins_runs_two_sessions_together_and_ends_done(
    build: Callable[..., Rig],
) -> None:
    rig = two_coin_round(build)
    assert sorted(r.market for r in rig.runner.requests) == [AAA, BBB]
    assert rig.rows('SELECT number, state, "trigger", close_utc FROM rounds') == [
        (1, "done", "bar_close", "2026-01-05T04:00:00Z")
    ]
    for market in (AAA, BBB):
        (item,) = rig.reports(market)
        assert (item.round_number, item.state) == (1, ReportState.OK)
        assert item.close_utc == FIRST_CLOSE  # T는 그 봉의 마감 시각이다
    done = [a for a in rig.alerts() if a.code == "A-26"]
    assert sorted(a.coin for a in done) == [AAA, BBB]  # 코인마다 하나씩 둘
    assert rig.codes() == ["A-26", "A-26"]
    assert rig.quote.book_requests() == []  # 주문에 관한 요청이 없다
    assert order_rows(rig) == 0


# --- C2 ---------------------------------------------------------------------------------------


@pytest.mark.scenario("C2")
def test_c2_the_session_gets_the_six_files_with_the_spec_headers_and_no_raw_candles(
    build: Callable[..., Rig],
) -> None:
    rig = two_coin_round(build)
    for request in rig.runner.requests:
        assert set(request.files) == set(HEADERS)
        for name, text in request.files.items():
            header, rows = table(text)
            assert header == HEADERS[name], name
            # 원본 봉(시가, 고가, 저가, 종가의 줄 묶음)이 없다: 봉 하나하나의 줄을 담는 파일이 없다.
            assert not {"open", "opening_price", "trade_price"} & set(header), name
        stats_only = set(BAR_STATS_COLUMNS) - {"market", "unit"}
        for name, text in request.files.items():
            if name != "candle_stats.csv":
                assert not stats_only & set(table(text)[0]), name  # 봉 통계의 열은 한 파일에만
        header, rows = table(request.files["candle_stats.csv"])
        assert len(rows) == 1  # 봉 통계 한 줄(봉의 줄 묶음이 아니다)
        shown = [
            v for v in rig.sink.of(CoinView)
            if v.market == request.market and v.state is LoadState.READY
        ][-1]  # fmt: skip
        assert request.files["candle_stats.csv"] == ai_inputs.candle_stats(shown.stats)
        assert dict(zip(header, rows[0], strict=True))["market"] == request.market


# --- C3 ---------------------------------------------------------------------------------------


@pytest.mark.scenario("C3")
def test_c3_the_inputs_have_no_webhook_vault_value_or_data_path_and_the_first_round_has_no_past(
    build: Callable[..., Rig],
) -> None:
    saved = {
        category: webhook_url(tag) for category, tag in zip(AlertCategory, "pqrs", strict=False)
    }
    rig = two_coin_round(build, saved=saved)
    secrets = [token_of(url) for url in saved.values()] + list(rig.vault.values.values())
    paths = [str(rig.root), str(rig.root.resolve()), rig.root.as_posix()]
    for request in rig.runner.requests:
        joined = "\n".join(request.files.values())
        for secret in secrets:
            assert secret not in joined
        for path in paths:
            assert path not in joined
        for name in ("final_last_judgment.csv", "final_last_report.csv", "final_trades.csv"):
            header, rows = table(request.files[name])
            assert (header, rows) == (HEADERS[name], []), name  # 머리줄뿐


# --- C20 --------------------------------------------------------------------------------------


@pytest.mark.scenario("C20")
def test_c20_a_failed_price_read_skips_only_that_coin_with_one_a2(
    build: Callable[..., Rig],
) -> None:
    rig = build(coins=(AAA, BBB))
    rig.start()
    rig.quote.script(*[QuoteError(ERROR_NETWORK)] * 5, kind="price", market=AAA)
    rig.round()
    (aaa,) = rig.reports(AAA)
    assert (aaa.state, aaa.failure) == (ReportState.SKIPPED, "현재가를 읽지 못함")
    (bbb,) = rig.reports(BBB)
    assert bbb.state is ReportState.OK
    assert [r.market for r in rig.runner.requests] == [BBB]  # AAA의 세션이 뜨지 않는다
    price_failed = [a for a in rig.alerts() if a.code == "A-2"]
    assert [(a.coin, a.detail.error_name) for a in price_failed] == [(AAA, ERROR_NETWORK)]


# --- C21 --------------------------------------------------------------------------------------


@pytest.mark.scenario("C21")
def test_c21_a_failed_fetch_skips_the_coin_without_a_price_request_and_only_the_a1(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    prices_before = len(rig.quote.price_requests(AAA))
    rig.quote.script(QuoteError(ERROR_NETWORK), kind="candles", market=AAA)
    rig.round()
    (item,) = rig.reports(AAA)
    assert (item.state, item.failure) == (ReportState.SKIPPED, "봉 통계를 만들지 못함")
    assert len(rig.quote.price_requests(AAA)) == prices_before  # 현재가 요청이 없다
    assert rig.runner.requests == []  # 세션이 없다
    assert rig.codes() == ["A-1"]  # 알림은 그 받기의 A-1뿐이다


@pytest.mark.scenario("C21")
def test_c21_too_few_complete_candles_skip_the_coin_with_only_the_a1(
    build: Callable[..., Rig], tmp_path: Path
) -> None:
    clock = FakeClock()
    quote = fake_quote(clock, (AAA,))
    quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 50, last_start=THIS_HOUR))  # 100보다 적다
    rig = build(clock=clock, quote=quote)
    rig.start()
    rig.data.wait_idle(SAFETY)
    codes_before = rig.codes()
    prices_before = len(rig.quote.price_requests(AAA))
    rig.round()
    (item,) = rig.reports(AAA)
    assert (item.state, item.failure) == (ReportState.SKIPPED, "봉 통계를 만들지 못함")
    assert len(rig.quote.price_requests(AAA)) == prices_before
    assert rig.runner.requests == []
    new = rig.codes()[len(codes_before) :]
    assert set(new) <= {"A-1"}  # 새 알림이 있다면 그 받기의 A-1뿐이다
    assert set(rig.codes()) == {"A-1"}


# --- C22 --------------------------------------------------------------------------------------


@pytest.mark.scenario("C22")
def test_c22_a_held_session_makes_no_new_round_but_the_fetch_goes_on(
    build: Callable[..., Rig],
) -> None:
    rig = build()
    rig.start()
    hold = threading.Event()
    started = rig.runner.plan(AAA, wait=hold)
    rig.to_next_due()
    assert started.wait(SAFETY)
    fetches = len(rig.quote.candle_requests(market=AAA))
    rig.to_next_due()  # 세션을 붙잡은 동안 다음 봉의 때
    assert len(rig.quote.candle_requests(market=AAA)) > fetches  # 봉 받기는 그대로 나간다
    assert rig.rows("SELECT number FROM rounds") == [(1,)]  # 새 회차가 생기지 않는다
    assert len(rig.runner.requests) == 1
    hold.set()
    rig.drain()
    assert rig.rows("SELECT number, state FROM rounds") == [(1, "done")]


# --- C25 --------------------------------------------------------------------------------------


@pytest.mark.scenario("C25")
def test_c25_a_running_round_left_in_the_db_is_interrupted_at_start_and_not_run_again(
    build: Callable[..., Rig],
) -> None:
    def seed(conn: Any) -> None:
        round_id = report_store.insert_round(
            conn, mode=Mode.PAPER, number=1, trigger=report_store.RoundTrigger.BAR_CLOSE,
            unit=HOUR, close_at=THIS_HOUR, next_at=FIRST_CLOSE, started_at=THIS_HOUR,
        )  # fmt: skip
        report_store.insert_report(
            conn, round_id=round_id, mode=Mode.PAPER, market=AAA, created_at=THIS_HOUR
        )

    rig = build(seed=seed)
    leftover = rig.sessions / "r1-KRW-AAA"
    leftover.mkdir(parents=True)
    (leftover / "search_input.csv").write_text("남은 것", encoding="utf-8")
    rig.start()
    assert rig.rows("SELECT state FROM rounds") == [("interrupted",)]
    assert rig.rows("SELECT state FROM reports") == [("interrupted",)]
    assert list(rig.sessions.iterdir()) == []  # 세션 폴더가 비었다
    (item,) = rig.sink.of(ReportListView)[0].items
    assert item.state is ReportState.INTERRUPTED
    assert round_end_text(item) == "끊김"  # 화면의 목록 줄의 끝
    assert rig.runner.requests == []  # 다시 돌지 않는다
    rig.round()
    assert rig.rows("SELECT number, state FROM rounds ORDER BY number") == [
        (1, "interrupted"),
        (2, "done"),
    ]
    assert len(rig.runner.requests) == 1


# --- C26 --------------------------------------------------------------------------------------


@pytest.mark.scenario("C26")
def test_c26_missed_rounds_are_not_run_at_start_and_the_next_close_runs_round_two(
    build: Callable[..., Rig],
) -> None:
    ten = datetime(2026, 1, 5, 10, tzinfo=UTC)

    def seed(conn: Any) -> None:
        round_id = report_store.insert_round(
            conn, mode=Mode.PAPER, number=1, trigger=report_store.RoundTrigger.BAR_CLOSE,
            unit=HOUR, close_at=ten, next_at=ten + timedelta(hours=1), started_at=ten,
        )  # fmt: skip
        report_store.finish_round(
            conn, round_id, state=report_store.ROUND_DONE, ended_at=ten + timedelta(seconds=30)
        )

    clock = FakeClock(datetime(2026, 1, 5, 13, 20, tzinfo=UTC))  # 그 뒤로 봉 마감 셋이 지났다
    quote = fake_quote(clock, (AAA,))
    last = datetime(2026, 1, 5, 13, tzinfo=UTC)
    quote.set_candles(AAA, HOUR, make_candles(AAA, HOUR, 200, last_start=last))
    rig = build(seed=seed, clock=clock, quote=quote)
    rig.start()
    assert len(rig.quote.candle_requests(market=AAA)) >= 1  # (가) 받기만 나간다
    assert rig.runner.requests == []  # 켤 때 회차가 돌지 않는다
    assert rig.rows("SELECT number FROM rounds") == [(1,)]
    assert rig.data._next_due == datetime(2026, 1, 5, 14, 0, 10, tzinfo=UTC)
    rig.round()  # 14:00:10
    assert rig.rows('SELECT number, "trigger", close_utc FROM rounds ORDER BY number') == [
        (1, "bar_close", "2026-01-05T10:00:00Z"),
        (2, "bar_close", "2026-01-05T14:00:00Z"),
    ]
    assert len(rig.runner.requests) == 1
