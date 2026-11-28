"""5단계 잇기: 실거래의 거래소 경계 부르기(M5 명세 3.6, 3.5.5, 4절 15번, 6절의 F5~F15, F24, F34).

가짜 업비트(`FakeUpbitTransport`), 가짜 시세, 가짜 시계, 가짜 보관소로만 돈다. 값은 모두 만든
값이다. 실거래의 체결은 `SqlLiveFills`(진짜 저장 함수를 부름)로 쓴다. `fills=False`이면 프로그램의
`StoredLiveFills`다.
"""

import itertools
import json
from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fakes.ai import answered, good_answers
from fakes.live_rig import AAA, BBB, PRICE, LiveRig, SqlLiveFills, live_rig_builder
from fakes.trade_rig import PARTS, SAFETY
from fakes.upbit import CANCEL_FAILS, NoResponse, Reply

from coin_report_trader.app.data_thread import DataWorker
from coin_report_trader.core import ErrorPauseCause, LiveSettings, Mode, TodayView
from coin_report_trader.core.ports import TransportError, TransportResponse

ORDERS = ("POST", "/v1/orders")
ORDER = "/v1/order"
ACCOUNTS = ("GET", "/v1/accounts")


@pytest.fixture
def build(tmp_path: Path) -> Iterator[Callable[..., LiveRig]]:
    yield from live_rig_builder(tmp_path)


def running(build: Callable[..., LiveRig], **kwargs: Any) -> LiveRig:
    """실거래로 켜고 '멈춤 풀기'까지 한 틀."""
    rig = build(mode=Mode.LIVE, **kwargs)
    rig.start()
    rig.worker.resume_user()
    rig.settle()
    return rig


def plan(rig: LiveRig, market: str, **kwargs: Any) -> None:
    """그 코인의 가짜 실행기 답. 예측 가격은 현재가(1,000)에서 방향 쪽이다."""
    up = kwargs.get("direction", "오를 것") == "오를 것"
    kwargs.setdefault("predicted", "1010" if up else "990")
    rig.runner.plan(market, lambda req: answered(good_answers(market, **kwargs)))


def order_bodies(rig: LiveRig) -> list[dict[str, str]]:
    return [json.loads(r.body or "{}") for r in rig.upbit_calls(*ORDERS)]


def stored_uid(rig: LiveRig) -> str:
    (row,) = rig.query("SELECT uid FROM orders WHERE mode = 'live' ORDER BY id DESC LIMIT 1")
    return str(row[0])


def restart(rig: LiveRig) -> LiveRig:
    """앱을 끄고 켠다: 같은 DB, 가짜 업비트, 보관소, 시계로 새 자료 스레드를 띄운다."""
    rig.worker.stop()
    rig.worker.join(SAFETY)
    rig.sink.items.clear()
    worker = DataWorker(
        database_file=rig.db,
        logs_dir=rig.db.parent / "logs",
        clock=rig.clock,
        quote=rig.quote,
        sink=rig.sink,
        ai=rig.runner,
        sessions_root=rig.db.parent / "ai-sessions",
        vault=rig.vault,
        exchange_transport=rig.upbit,
    )
    worker._live_fill_store = SqlLiveFills()
    again = LiveRig(
        worker, rig.sink, rig.clock, rig.quote, rig.runner, rig.db, upbit=rig.upbit, vault=rig.vault
    )
    again.start()
    return again


# --- 주문 내기(3.6.2), F5 ---------------------------------------------------------------------


# F5
def test_a_live_round_sends_one_limit_buy_with_our_identifier(
    build: Callable[..., LiveRig],
) -> None:
    rig = running(build)
    plan(rig, AAA, direction="오를 것", buy_price="990")
    rig.round()
    (body,) = order_bodies(rig)
    uid = stored_uid(rig)
    assert body == {
        "market": AAA,
        "side": "bid",
        "volume": "20.2020202",
        "price": "990",
        "ord_type": "limit",
        "identifier": uid,
        "smp_type": "cancel_maker",
    }
    (request,) = rig.upbit_calls(*ORDERS)
    assert "origin" not in request.header_names
    assert request.auth is None  # 서명과 query_hash가 가짜의 검사를 통과했다
    assert rig.query("SELECT mode, state, sent, price, qty FROM orders WHERE uid = ?", uid) == [
        ("live", "open", 1, "990", "20.20202020")
    ]
    assert "A-27" in rig.codes()
    (report,) = rig.query("SELECT order_id FROM reports WHERE mode = 'live'")
    assert report[0] is not None
    assert rig.query("SELECT mode FROM rounds ORDER BY id DESC LIMIT 1") == [("live",)]


# F6
def test_a_lost_reply_is_queried_not_sent_again(build: Callable[..., LiveRig]) -> None:
    rig = running(build)
    rig.upbit.script(NoResponse(sent=True), method="POST", path="/v1/orders")
    plan(rig, AAA, direction="오를 것", buy_price="990")
    rig.round()
    assert len(rig.upbit_calls(*ORDERS)) == 1  # 다시 보내지 않았다
    assert rig.upbit_calls("GET", ORDER)  # 그 식별값으로 조회했다
    assert rig.live_orders() == [(AAA, "buy", "limit", "open", 1)]


class _Garbled:
    """주문 생성의 응답만 바꿔 주는 운반: 가짜가 주문을 받아 둔 뒤 (1) 읽을 수 없는 2xx, (2) 500."""

    def __init__(self, inner: Any, status: int) -> None:
        self.inner = inner
        self.status = status
        self.done = False

    def request(self, method: str, path: str, **kwargs: Any) -> TransportResponse:
        response = self.inner.request(method, path, **kwargs)
        if (method, path) == ORDERS and not self.done:
            self.done = True
            return TransportResponse(status=self.status, headers=response.headers, text="{")
        return response  # type: ignore[no-any-return]


# F34
@pytest.mark.parametrize("status", [200, 500])
def test_an_unreadable_or_server_error_reply_is_queried_and_open(
    build: Callable[..., LiveRig], status: int
) -> None:
    rig = build(mode=Mode.LIVE)
    garbled = _Garbled(rig.upbit, status)
    rig.worker._transport = garbled  # type: ignore[assignment]
    rig.start()
    rig.worker.resume_user()
    rig.settle()
    plan(rig, AAA, direction="오를 것", buy_price="990")
    rig.round()
    assert garbled.done
    assert len(rig.upbit_calls(*ORDERS)) == 1
    assert rig.live_orders() == [(AAA, "buy", "limit", "open", 1)]  # FAILED가 아니다


class _Lost:
    """주문 생성의 요청을 가짜에 넘기지 않고 응답도 없이 끝내는 운반(보냈는지 모름. 받지 않음)."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.lost = 0

    def request(self, method: str, path: str, **kwargs: Any) -> TransportResponse:
        if (method, path) == ORDERS and not self.lost:
            self.lost += 1
            raise TransportError(timeout=True, sent=True)
        return self.inner.request(method, path, **kwargs)  # type: ignore[no-any-return]


# F7
def test_an_order_never_received_is_unconfirmed_then_not_sent_after_600_seconds(
    build: Callable[..., LiveRig],
) -> None:
    rig = build(mode=Mode.LIVE)
    lost = _Lost(rig.upbit)
    rig.worker._transport = lost  # type: ignore[assignment]
    rig.start()
    rig.worker.resume_user()
    rig.settle()
    plan(rig, AAA, direction="오를 것", buy_price="990")
    rig.round()
    assert rig.live_orders() == [(AAA, "buy", "limit", "unconfirmed", 1)]
    for _ in range(9):
        rig.advance(60)
        assert rig.live_orders()[0][3] == "unconfirmed"
    rig.advance(60)
    rig.advance(60)
    assert rig.live_orders() == [(AAA, "buy", "limit", "not_sent", 1)]
    assert lost.lost == 1
    assert rig.upbit_calls(*ORDERS) == []  # 다시 보내지 않았다


# F10
def test_a_rejected_order_is_failed_with_its_name_and_no_error_pause(
    build: Callable[..., LiveRig],
) -> None:
    rig = running(build)
    rig.upbit.script(Reply(400, "insufficient_funds_bid"), method="POST", path="/v1/orders")
    plan(rig, AAA, direction="오를 것", buy_price="990")
    rig.round()
    assert rig.query("SELECT state, error_name FROM orders WHERE mode = 'live'") == [
        ("failed", "잔고 부족")
    ]
    assert rig.status().error_paused is False
    assert "A-23" not in rig.codes()
    (report,) = rig.query("SELECT order_id FROM reports WHERE mode = 'live'")
    assert report[0] is not None


# F13
def test_a_429_on_an_order_goes_to_a_query_without_sending_again(
    build: Callable[..., LiveRig],
) -> None:
    rig = running(build)
    rig.upbit.script(Reply(429), method="POST", path="/v1/orders")
    plan(rig, AAA, direction="오를 것", buy_price="990")
    rig.round()
    assert len(rig.upbit_calls(*ORDERS)) == 1
    assert rig.upbit_calls("GET", ORDER)
    assert rig.live_orders()[0][3] == "unconfirmed"  # 가짜는 429에 주문을 만들지 않았다
    assert rig.worker._upbit is not None
    assert rig.worker._upbit.limiter.paused_until("order") > 0


# F13
def test_sec_zero_holds_the_group_until_the_next_second(build: Callable[..., LiveRig]) -> None:
    rig = running(build)
    plan(rig, AAA, direction="오를 것", buy_price="990")
    rig.round()
    rig.upbit.set_remaining("default", 0)
    before = len(rig.upbit.requests)
    rig.advance(60)  # 1분 점검: 걸린 주문의 조회 → 계정 읽기
    times = [r.monotonic for r in rig.upbit.requests[before:]]
    assert len(times) >= 2
    assert all(b - a >= 1.0 for a, b in itertools.pairwise(times))


# --- 부분 체결과 체결 쓰기(3.6.4), F8 ----------------------------------------------------------


# F8
def test_partial_fills_are_written_once_each_and_the_rest_stays_a_holding(
    build: Callable[..., LiveRig],
) -> None:
    rig = running(build, holdings={"AAA": (Decimal(10), Decimal(1000))})
    plan(rig, AAA, direction="내릴 것", sell_price="1010")
    rig.round()
    uid = stored_uid(rig)
    assert rig.live_orders() == [(AAA, "sell", "limit", "open", 1)]
    assert rig.query("SELECT avg_price_at_send FROM orders WHERE uid = ?", uid) == [("1000",)]
    rig.upbit.fill(uid, Decimal(2), price=Decimal(1010))
    rig.advance(60)
    rig.upbit.fill(uid, Decimal(2), price=Decimal(1010))
    with rig.upbit._lock:  # 거래소에서 남은 수량이 취소됨
        rig.upbit._cancel(uid)
    rig.advance(60)
    rig.advance(60)  # 다시 조회해도 같은 체결을 두 번 쓰지 않는다
    fills = rig.query(
        "SELECT qty, fee, realized_pnl, trade_key FROM fills WHERE mode = 'live' ORDER BY id"
    )
    # 2 × 1,010 − 1.01 − 2 × 1,000 × 1.0005 = 17.99(3.6.4의 식)
    assert [(Decimal(q), Decimal(f), Decimal(p)) for q, f, p, _ in fills] == [
        (Decimal(2), Decimal("1.01"), Decimal("17.99")),
        (Decimal(2), Decimal("1.01"), Decimal("17.99")),
    ]
    assert len({key for *_, key in fills}) == 2
    assert rig.query("SELECT state, executed_qty FROM orders WHERE uid = ?", uid) == [
        ("canceled", "4")
    ]
    assert rig.upbit.balances()["AAA"] == (Decimal(6), Decimal(0))  # 남은 6개가 보유다
    assert rig.codes().count("A-28") == 2


def test_the_program_s_own_fill_store_writes_a_live_partial_fill(
    build: Callable[..., LiveRig],
) -> None:
    """실제 프로그램의 `StoredLiveFills`(시험의 것을 넣지 않음)로 부분 체결을 쓴다: A-29도 오류
    멈춤도 없고, 체결 한 줄과 체결 수량만 쓰고 주문은 걸린 그대로다(3.6.4)."""
    rig = running(build, holdings={"AAA": (Decimal(10), Decimal(1000))}, fills=False)
    assert type(rig.worker._live_fill_store).__name__ == "StoredLiveFills"
    plan(rig, AAA, direction="내릴 것", sell_price="1010")
    rig.round()
    uid = stored_uid(rig)
    rig.upbit.fill(uid, Decimal(2), price=Decimal(1010))
    rig.advance(60)
    assert rig.status().error_paused is False
    assert "A-29" not in rig.codes()
    rows = rig.query("SELECT mode, qty, price, trade_key FROM fills WHERE mode = 'live'")
    assert [(mode, qty, price) for mode, qty, price, _ in rows] == [("live", "2", "1010")]
    assert rows[0][3] not in ("", "paper")
    assert rig.query("SELECT state, executed_qty FROM orders WHERE uid = ?", uid) == [("open", "2")]
    assert rig.codes().count("A-28") == 1
    rig.advance(60)  # 다음 조회에서 같은 체결을 다시 쓰지 않는다
    assert len(rig.query("SELECT id FROM fills WHERE mode = 'live'")) == 1
    assert "A-29" not in rig.codes()


def test_a_live_fill_that_cannot_be_written_is_a29_and_an_error_pause(
    build: Callable[..., LiveRig],
) -> None:
    rig = running(build, holdings={"AAA": (Decimal(10), Decimal(1000))}, fills=False)
    plan(rig, AAA, direction="내릴 것", sell_price="1010")
    rig.round()
    uid = stored_uid(rig)
    rig.execute(
        "CREATE TRIGGER no_live_fills BEFORE INSERT ON fills WHEN NEW.mode = 'live'"
        " BEGIN SELECT RAISE(ABORT, 'x'); END"
    )
    rig.upbit.fill(uid, Decimal(2), price=Decimal(1010))
    rig.advance(60)
    assert rig.status().error_paused is True
    assert rig.status().error_cause is ErrorPauseCause.SAVE
    (a29,) = [a for a in rig.alerts() if a.code == "A-29"]
    assert a29.detail is not None
    assert a29.detail.stage == "주문 저장"
    assert rig.query("SELECT count(*) FROM fills") == [(0,)]
    assert rig.query("SELECT state FROM orders WHERE uid = ?", uid) == [("open",)]


# --- 정리의 취소 실패(3.6.4), F9 ----------------------------------------------------------------


# F9
def test_a_cancel_refused_in_the_cleanup_keeps_the_coin_from_buying(
    build: Callable[..., LiveRig],
) -> None:
    rig = running(build)
    plan(rig, AAA, direction="오를 것", buy_price="990")
    rig.round()
    first = stored_uid(rig)
    rig.upbit.script(CANCEL_FAILS, CANCEL_FAILS, method="DELETE", path=ORDER)
    plan(rig, AAA, direction="오를 것", buy_price="990", result_text="지난 결과", compared=PARTS)
    rig.round()
    assert len(rig.upbit_calls(*ORDERS)) == 1  # 새 매수를 내지 않았다
    assert rig.upbit.order_state(first) == "wait"
    (row,) = rig.query("SELECT order_id, no_order_reason FROM reports ORDER BY id DESC LIMIT 1")
    assert row[1] == "확인 안 된 주문"


# --- 오류 멈춤(3.5.5), F11, F12 ---------------------------------------------------------------


# F11
def test_a_418_in_the_minute_check_is_an_error_pause_with_the_block_time(
    build: Callable[..., LiveRig],
) -> None:
    rig = running(build)
    rig.upbit.script(Reply(418, headers={"Retry-After": "120"}), method="GET", path="/v1/accounts")
    rig.advance(60)
    status = rig.status()
    assert (status.error_paused, status.error_cause, status.error_detail) == (
        True,
        ErrorPauseCause.EXCHANGE,
        "일시 차단",
    )
    assert status.stop_loss_off is True
    assert status.blocked_until is not None
    assert (status.blocked_until - rig.clock.now()).total_seconds() == pytest.approx(120, abs=1)
    (a23,) = [a for a in rig.alerts() if a.code == "A-23"]
    assert a23.detail.error_name == "일시 차단"
    rig.worker.resume_error()  # 차단 시각 전에는 풀리지 않는다
    rig.settle()
    assert rig.status().error_paused is True


# F12
def test_an_expired_key_is_an_error_pause_and_no_round_or_stop_loss_runs(
    build: Callable[..., LiveRig],
) -> None:
    rig = running(build)
    rig.upbit.script(Reply(401, "expired_access_key"), method="GET", path="/v1/accounts")
    plan(rig, AAA, direction="오를 것", buy_price="990")
    rig.round()
    status = rig.status()
    assert (status.error_paused, status.error_detail) == (True, "키 만료")
    (a23,) = [a for a in rig.alerts() if a.code == "A-23"]
    assert (
        a23.text
        == "거래소 오류(키 만료)로 오류 멈춤에 들어갔습니다. 지금 손절이 동작하지 않습니다."
    )
    count = len(rig.upbit.requests)
    rig.advance(60)
    rig.advance(3600)
    assert len(rig.upbit.requests) == count  # 풀기 전에는 회차와 손절이 없다
    assert rig.upbit_calls(*ORDERS) == []


# --- 켤 때의 대조(3.6.5), F14 -------------------------------------------------------------------


# F14
def test_a_restart_reconciles_keeps_the_direct_order_and_raises_a6_once(
    build: Callable[..., LiveRig],
) -> None:
    live = LiveSettings(
        budget=Decimal(200_000),
        coin_ratios={AAA: Decimal(50), BBB: Decimal(50)},
        daily_loss_limit=Decimal(3000),
    )
    rig = running(build, coins=(AAA, BBB), live=live, holdings={"AAA": (Decimal(5), Decimal(900))})
    plan(rig, AAA, direction="오를 것", buy_price="990")
    plan(rig, BBB, direction="오를 것", buy_price="990")
    rig.round()
    assert [row[3] for row in rig.live_orders()] == ["open", "open"]
    direct = rig.upbit.add_direct_order(AAA, "ask", Decimal(1100), Decimal(2))
    before = len(rig.upbit.requests)
    again = restart(rig)
    calls = [(r.method, r.path) for r in again.upbit.requests[before:]]
    first_list = calls.index(("GET", "/v1/orders/open"))
    assert calls.index(("GET", ORDER)) > first_list  # 목록 → 주문마다 조회
    assert calls.count(("DELETE", ORDER)) == 0  # 직접 건 주문을 취소하지 않는다
    assert [row[3] for row in again.live_orders()] == ["open", "open"]
    assert again.codes().count("A-6") == 1
    (a6,) = [a for a in again.alerts() if a.code == "A-6"]
    assert (a6.coin, a6.text) == (AAA, "직접 걸어 둔 주문에 묶인 2.0은 팔 수 없습니다.")
    assert direct.startswith("fake-order-")
    again.advance(60)
    assert again.codes().count("A-6") == 1  # 상태가 바뀔 때 한 번


# --- 외부 변동(3.6.6), F15 ---------------------------------------------------------------------


# F15
def test_an_outside_change_is_one_a22_and_a_row_after_the_program_share(
    build: Callable[..., LiveRig],
) -> None:
    rig = running(build, holdings={"AAA": (Decimal(3), Decimal(900))})
    plan(rig, AAA, direction="오를 것", buy_price="990")
    rig.round()
    uid = stored_uid(rig)
    rig.upbit.fill(uid, Decimal(1), price=Decimal(990))  # 프로그램 체결 1개
    with rig.upbit._lock:
        rig.upbit._coins["AAA"][0] += Decimal(2)  # 밖에서 2개
    rig.advance(60)
    assert rig.codes().count("A-22") == 1
    assert rig.query("SELECT mode, market, qty_delta FROM external_changes") == [
        ("live", AAA, "2.00000000")
    ]
    (a22,) = [a for a in rig.alerts() if a.code == "A-22"]
    assert (a22.coin, a22.text) == (AAA, "프로그램이 내지 않은 잔고 변화가 있었습니다(외부 변동).")
    with rig.upbit._lock:
        rig.upbit._krw += Decimal(5000)  # 원화가 바뀜
    rig.advance(60)
    krw = [a for a in rig.alerts() if a.code == "A-22" and a.coin is None]
    assert [a.text for a in krw] == [
        "원화: 프로그램이 내지 않은 잔고 변화가 있었습니다(외부 변동)."
    ]
    assert rig.query("SELECT market, qty_delta FROM external_changes ORDER BY id")[-1] == (
        "KRW",
        "5000",
    )


# --- 원화 잔고와 직접 산 보유(3.6.1), F24 -------------------------------------------------------


# F24
def test_too_little_krw_is_a11_and_no_smaller_order(build: Callable[..., LiveRig]) -> None:
    rig = running(build, krw=Decimal(15_000))
    plan(rig, AAA, direction="오를 것", buy_price="990")
    rig.round()
    assert rig.upbit_calls(*ORDERS) == []
    (a11,) = [a for a in rig.alerts() if a.code == "A-11"]
    assert a11.text == "원화 잔고가 모자라 이번 회차에 사지 않았습니다."
    assert rig.query("SELECT no_order_reason FROM reports WHERE mode = 'live'") == [
        ("원화 잔고가 모자람",)
    ]


# F24
def test_enough_krw_buys_the_first_buy_amount(build: Callable[..., LiveRig]) -> None:
    rig = running(build, krw=Decimal(30_000))
    plan(rig, AAA, direction="오를 것", buy_price="990")
    rig.round()
    (body,) = order_bodies(rig)
    assert Decimal(body["price"]) * Decimal(body["volume"]) <= Decimal(20_000)


# F24
def test_a_directly_bought_holding_below_the_stop_line_is_sold_at_market(
    build: Callable[..., LiveRig],
) -> None:
    rig = build(mode=Mode.LIVE, holdings={"AAA": (Decimal(10), Decimal(900))})
    rig.quote.set_prices({AAA: Decimal(850), BBB: PRICE})
    rig.upbit.set_market_price(AAA, Decimal(850))
    rig.start()
    rig.worker.resume_user()
    rig.settle()
    (body,) = order_bodies(rig)
    assert (body["side"], body["ord_type"], body["volume"]) == ("ask", "market", "10")
    assert "price" not in body
    assert rig.query("SELECT reason, state FROM orders WHERE mode = 'live'") == [
        ("stop_loss", "filled")
    ]
    # 10 × 850 − 4.25 − 10 × 900 × 1.0005(주문을 낼 때의 평균 매수가)
    ((pnl,),) = rig.query("SELECT realized_pnl FROM fills WHERE mode = 'live'")
    assert Decimal(pnl) == Decimal("-508.75")
    today = rig.sink.of(TodayView)[-1]
    assert today.realized_pnl == Decimal("-508.75")
    assert today.budget == Decimal(100_000)  # 실거래 설정의 값
    assert today.daily_loss_limit == Decimal(3000)


def test_the_live_today_view_uses_the_real_krw_and_live_settings(
    build: Callable[..., LiveRig],
) -> None:
    rig = running(build, krw=Decimal(123_000))
    today = rig.sink.of(TodayView)[-1]
    assert (today.paper_krw, today.budget, today.daily_loss_limit) == (
        Decimal(123_000),
        Decimal(100_000),
        Decimal(3000),
    )


def test_a_paper_round_never_calls_the_exchange(build: Callable[..., LiveRig]) -> None:
    rig = build(paused=False)
    rig.start()
    plan(rig, AAA, direction="오를 것", buy_price="990", result_text="지난 결과")
    rig.round()
    rig.advance(60)
    assert rig.upbit.requests == []  # 모의매매에서 매매 엔진은 실제 계정을 읽지 않는다
    assert rig.query("SELECT mode FROM orders") == [("paper",)]


# --- A-7, 켤 때 `sent` 0인 줄, 매매 DB 쓰기의 실패, A-15 ---------------------------------------


def test_a7_a_direct_order_gone_after_an_opposite_program_fill(
    build: Callable[..., LiveRig],
) -> None:
    rig = running(build, holdings={"AAA": (Decimal(5), Decimal(900))})
    rig.upbit.add_direct_order(AAA, "ask", Decimal(1100), Decimal(2))
    plan(rig, AAA, direction="오를 것", buy_price="990")
    rig.round()  # 정리의 목록에서 직접 건 주문을 본다
    rig.upbit.fill(stored_uid(rig), Decimal(1), price=Decimal(990))  # 프로그램 매수 체결
    with rig.upbit._lock:  # 직접 건 매도 주문이 거래소에서 취소됨
        rig.upbit._cancel(None)  # type: ignore[arg-type]
    rig.advance(60)
    assert "A-7" not in rig.codes()
    plan(rig, AAA, direction="오를 것", buy_price="990", result_text="지난 결과", compared=PARTS)
    rig.round()  # 다음 목록에서 사라졌다
    (a7,) = [a for a in rig.alerts() if a.code == "A-7"]
    assert (a7.coin, a7.text) == (
        AAA,
        "프로그램 주문과 만나 직접 걸어 둔 주문이 거래소에서 취소되었습니다.",
    )


def test_an_unsent_live_row_is_queried_on_start_not_closed(build: Callable[..., LiveRig]) -> None:
    from coin_report_trader.core import OrderKind, OrderPlan, OrderReason, OrderSide
    from coin_report_trader.storage import trading as trade_store

    def seed(conn: Any) -> None:
        order = OrderPlan(
            AAA, OrderSide.BUY, OrderKind.LIMIT, OrderReason.JUDGMENT, Decimal(990), Decimal(1),
            Decimal(990),
        )  # fmt: skip
        trade_store.insert_order(
            conn,
            order,
            mode=Mode.LIVE,
            uid="seed-unsent",
            round_id=None,
            created_at=rig_time(),
            judged_until=rig_time(),
        )

    rig = build(mode=Mode.LIVE, seed=seed)
    rig.start()
    assert [r.query for r in rig.upbit_calls("GET", ORDER)][:1] == ["identifier=seed-unsent"]
    assert rig.live_orders() == [(AAA, "buy", "limit", "unconfirmed", 1)]


def rig_time() -> Any:
    from fakes.clock import DEFAULT_START

    return DEFAULT_START


def test_a_snapshot_that_cannot_be_written_is_a29_and_an_error_pause(
    build: Callable[..., LiveRig], monkeypatch: pytest.MonkeyPatch
) -> None:
    from coin_report_trader.core import StorageError
    from coin_report_trader.storage import trading as trade_store

    rig = running(build)

    def broken(*args: Any, **kwargs: Any) -> None:
        raise StorageError("만든 쓰기 실패")

    monkeypatch.setattr(trade_store, "replace_live_snapshots", broken)
    rig.advance(60)
    status = rig.status()
    assert (status.error_paused, status.error_cause) == (True, ErrorPauseCause.SAVE)
    (a29,) = [a for a in rig.alerts() if a.code == "A-29"]
    assert a29.detail.stage == "주문 저장"


def test_a_live_minimum_that_fails_the_saved_settings_is_a15_and_no_buy(
    build: Callable[..., LiveRig],
) -> None:
    rig = running(build)
    rig.upbit.set_chance(AAA, min_total=Decimal(10_000))  # 추가 한 번 10,000원 < 11,000원
    plan(rig, AAA, direction="오를 것", buy_price="990")
    rig.round()
    assert "A-15" in rig.codes()
    assert rig.upbit_calls(*ORDERS) == []
    assert rig.query("SELECT no_order_reason FROM reports WHERE mode = 'live'") == [
        ("설정이 검사에 걸림",)
    ]


def test_the_live_daily_loss_counts_only_live_fills(build: Callable[..., LiveRig]) -> None:
    rig = build(mode=Mode.LIVE, holdings={"AAA": (Decimal(100), Decimal(1000))})
    rig.quote.set_prices({AAA: Decimal(940), BBB: PRICE})  # 손절선 950 아래
    rig.upbit.set_market_price(AAA, Decimal(940))
    rig.start()
    rig.worker.resume_user()
    rig.settle()
    # 100 × 940 − 47 − 100 × 1,000 × 1.0005 = −6,097원: 하루 손실 한도 3,000원에 닿았다
    assert "A-12" in rig.codes()
    assert rig.sink.of(TodayView)[-1].limit_reached is True
