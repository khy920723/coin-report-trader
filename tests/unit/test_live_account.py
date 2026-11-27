"""5단계 잇기: 계정 화면의 읽는 때와 실행 자리(M5 명세 3.15.4, 3.15.5, 4절 15번, 6절의 F26~F33).

가짜 업비트, 가짜 시세, 가짜 보관소, 가짜 시계로만 돈다. 값은 모두 만든 값이다.
"""

import logging
from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from fakes.live_rig import AAA, BBB, LiveRig, live_rig_builder
from fakes.quote import PRICES
from fakes.trade_rig import make_rig
from fakes.upbit import Reply

from coin_report_trader.core import AccountState, AccountView, Mode, QuoteError

ACCOUNTS = ("GET", "/v1/accounts")


@pytest.fixture
def build(tmp_path: Path) -> Iterator[Callable[..., LiveRig]]:
    yield from live_rig_builder(tmp_path)


def views(rig: LiveRig) -> list[AccountView]:
    return rig.sink.of(AccountView)


def show(rig: LiveRig, visible: bool = True) -> None:
    rig.worker.account_visible(visible)
    rig.settle()


def reads(rig: LiveRig) -> int:
    return len(rig.upbit_calls(*ACCOUNTS))


def dashboard(build: Callable[..., LiveRig], **kwargs: object) -> LiveRig:
    """3.15.2의 예의 계정(F27): 원화 100,000원·묶임 20,000원, AAA 10개(평균 1,000), BBB 2개
    (평균 없음), CCC 5개(원화 마켓 없음). 현재가 1,100·500."""
    rig = build(
        holdings={
            "AAA": (Decimal(10), Decimal(1000)),
            "BBB": (Decimal(2), Decimal(0)),
            "CCC": (Decimal(5), Decimal(100)),
        },
        krw=Decimal(100_000),
        paused=False,
        **kwargs,
    )
    with rig.upbit._lock:
        rig.upbit._krw_locked = Decimal(20_000)
    rig.quote.set_prices({AAA: Decimal(1100), BBB: Decimal(500)})
    rig.start()
    return rig


# --- 요청하지 않는 때(F26) -------------------------------------------------------------------


def test_f26_no_key_shows_no_key_and_requests_nothing(build: Callable[..., LiveRig]) -> None:
    rig = build(key=False)
    rig.start()
    quotes = len(rig.quote.requests)
    show(rig)
    assert views(rig)[-1].state is AccountState.NO_KEY
    assert rig.upbit.requests == []
    assert rig.quote.prices_requests() == []
    assert len(rig.quote.requests) == quotes


def test_f26_before_the_first_setup_requests_nothing(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, done=False)
    rig.start()
    try:
        rig.worker.account_visible(True)
        rig.settle()
        (view,) = rig.sink.of(AccountView)
        assert view.state is AccountState.BEFORE_SETUP
        assert view.total_assets is None
    finally:
        rig.worker.stop()
        rig.worker.join(10)


# --- 읽기와 셈(F27, F28) ---------------------------------------------------------------------


def test_f27_the_example_account_is_read_and_summed(build: Callable[..., LiveRig]) -> None:
    rig = dashboard(build)
    show(rig)
    loading, view = views(rig)[-2:]
    assert (loading.state, loading.reading) == (AccountState.LOADING, True)
    assert (view.state, view.reading) == (AccountState.OK, False)
    assert (view.krw_available, view.krw_locked) == (Decimal(100_000), Decimal(20_000))
    assert view.holdings_value == Decimal(12_000)
    assert view.total_assets == Decimal(132_000)
    assert (view.total_buy, view.total_pnl, view.total_pnl_rate) == (
        Decimal(10_000),
        Decimal(1000),
        Decimal("0.1"),
    )
    assert (view.excluded_value, view.excluded_pnl) == (1, 2)
    assert [row.currency for row in view.rows] == ["AAA", "BBB", "CCC"]
    assert view.rows[0].picked is True
    (prices,) = rig.quote.prices_requests()  # 묶은 요청 한 번
    assert set((prices.market or "").split(",")) == {AAA, BBB}
    assert [(r.method, r.path) for r in rig.upbit.requests] == [ACCOUNTS]  # 주문, 취소 없음


def test_f28_a_failed_price_request_leaves_the_coins_unvalued(
    build: Callable[..., LiveRig],
) -> None:
    rig = dashboard(build)
    rig.quote.script(QuoteError("서버 오류"), kind=PRICES)
    show(rig)
    view = views(rig)[-1]
    assert view.state is AccountState.OK
    assert [row.note for row in view.rows[:2]] == ["현재가를 읽지 못함", "현재가를 읽지 못함"]
    assert view.total_assets == Decimal(120_000)


# --- 읽는 때(F29) ------------------------------------------------------------------------------


def test_f29_reads_while_visible_and_stops_while_hidden(build: Callable[..., LiveRig]) -> None:
    rig = build(paused=False)
    rig.start()
    show(rig)
    assert reads(rig) == 1
    rig.advance(30)  # 1분 점검과 다른 때가 되게 옮긴다
    show(rig, False)
    show(rig)
    assert reads(rig) == 2  # 다시 보이면 한 번
    for expected in (3, 4, 5):
        rig.advance(61)  # 1분 점검과 같은 때가 되지 않는 걸음
        assert reads(rig) == expected
    show(rig, False)
    rig.advance(61)
    rig.advance(61)
    assert reads(rig) == 5  # 숨은 동안 요청이 없다
    show(rig)
    assert reads(rig) == 6


def test_f29_the_minute_check_at_the_same_time_goes_first_and_this_read_is_skipped(
    build: Callable[..., LiveRig],
) -> None:
    rig = build(paused=False)
    rig.start()
    show(rig)  # 1분 점검과 같은 단조 시각에서 센다
    assert reads(rig) == 1
    prices = len(rig.quote.price_requests(AAA))
    rig.advance(60)
    assert len(rig.quote.price_requests(AAA)) > prices  # 1분 점검은 했다
    assert reads(rig) == 1  # 같은 때의 읽기는 건너뛰었다
    rig.advance(60)
    rig.advance(60)
    assert reads(rig) == 2  # 다음 60초의 때에 읽는다
    rig.advance(60)
    assert reads(rig) == 3


def test_f29_reading_is_flagged_and_a_refresh_reads_again(build: Callable[..., LiveRig]) -> None:
    rig = build()
    rig.start()
    show(rig)
    rig.worker.refresh_account()
    rig.settle()
    assert reads(rig) == 2
    assert [v.reading for v in views(rig)] == [True, False, True, False]


# --- 오류의 모습(F30, F32)과 개인정보(F33) ---------------------------------------------------


@pytest.mark.parametrize("failure", ["expired", "exception"])
def test_f30_a_failed_read_dims_the_last_values_without_any_alert(
    build: Callable[..., LiveRig], failure: str
) -> None:
    rig = dashboard(build)
    show(rig)
    codes = rig.codes()
    if failure == "expired":
        rig.upbit.script(Reply(401, "expired_access_key"), method="GET", path="/v1/accounts")
    else:
        rig.upbit.script(Reply(200), method="GET", path="/v1/accounts")  # 읽을 수 없는 2xx
    rig.worker.refresh_account()
    rig.settle()
    view = views(rig)[-1]
    assert view.state is AccountState.FAILED
    assert view.total_assets == Decimal(132_000)  # 마지막 값을 흐리게 둔다
    assert view.error_name == ("키 만료" if failure == "expired" else "응답 형식 오류")
    assert rig.codes() == codes  # 알림이 없다
    assert rig.status().error_paused is False
    problems = rig.db.parent / "problems"
    assert not problems.exists() or not any(problems.iterdir())


def test_f32_a_418_on_the_account_screen_in_paper_is_only_a_failed_read(
    build: Callable[..., LiveRig],
) -> None:
    rig = build()
    rig.start()
    rig.upbit.script(Reply(418, headers={"Retry-After": "120"}), method="GET", path="/v1/accounts")
    show(rig)
    assert views(rig)[-1].state is AccountState.FAILED
    rig.advance(60)
    assert rig.status().error_paused is False
    assert "A-23" not in rig.codes()


def test_f32_a_418_on_the_account_screen_in_live_stops_the_next_engine_request(
    build: Callable[..., LiveRig],
) -> None:
    rig = build(mode=Mode.LIVE)
    rig.start()
    rig.worker.resume_user()
    rig.settle()
    rig.upbit.script(Reply(418, headers={"Retry-After": "120"}), method="GET", path="/v1/accounts")
    show(rig)
    assert views(rig)[-1].state is AccountState.FAILED
    count = len(rig.upbit.requests)
    rig.advance(60)  # 1분 점검의 엔진 요청이 장치에 걸린다
    status = rig.status()
    assert (status.error_paused, status.error_detail) == (True, "일시 차단")
    assert "A-23" in rig.codes()
    assert len(rig.upbit.requests) == count  # 차단 동안 내지 않았다
    rig.advance(60)
    assert views(rig)[-1].state is AccountState.BLOCKED
    assert len(rig.upbit.requests) == count


def test_f31_the_account_screen_never_writes_or_raises_engine_alerts(
    build: Callable[..., LiveRig],
) -> None:
    rig = build(mode=Mode.LIVE, holdings={"AAA": (Decimal(3), Decimal(900))})
    rig.start()
    snapshots = rig.query("SELECT * FROM live_snapshots ORDER BY market")
    with rig.upbit._lock:
        rig.upbit._coins["AAA"][0] += Decimal(2)
    show(rig)
    rig.advance(1)
    rig.worker.refresh_account()
    rig.settle()
    rig.worker.refresh_account()
    rig.settle()
    assert rig.query("SELECT * FROM live_snapshots ORDER BY market") == snapshots
    assert rig.query("SELECT count(*) FROM external_changes") == [(0,)]
    assert "A-22" not in rig.codes()
    assert "A-6" not in rig.codes()
    screen = {(r.method, r.path) for r in rig.upbit.requests[-3:]}
    assert screen == {ACCOUNTS}
    rig.advance(59)  # 다음 1분 점검의 엔진 읽기에서만 A-22가 생긴다
    assert "A-22" in rig.codes()


def test_f33_no_amounts_coins_or_markets_go_to_logs_alerts_or_the_db(
    build: Callable[..., LiveRig], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    rig = dashboard(build)
    rig.upbit.script(Reply(500), method="GET", path="/v1/accounts")
    show(rig)
    tables = rig.query("SELECT name FROM sqlite_master WHERE type = 'table'")
    before = {t: rig.query(f"SELECT count(*) FROM {t}") for (t,) in tables}
    rig.worker.refresh_account()
    rig.settle()
    after = {t: rig.query(f"SELECT count(*) FROM {t}") for (t,) in tables}
    assert before == after
    for text in ("100000", "132000", "AAA", "CCC", "KRW-BBB", "1100"):
        assert text not in caplog.text
    assert all("AAA" not in (a.text or "") for a in rig.alerts())
