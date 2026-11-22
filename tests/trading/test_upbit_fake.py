"""가짜 업비트 비공개 API(`tests/fakes/upbit.py`)의 시험(M5 명세 3.12).

뒤 번호(7번의 `UpbitExchange`, 9번의 대조, 17번의 수용 시험)가 이 가짜를 고치지 않고 쓰므로, 정할
수 있는 것(부분 체결, 취소 실패, 주문 거절, 응답 없음, 429, 418, 키 오류, `sec`)을 여기서 본다.
"""

from decimal import Decimal

import pytest
from fakes.clock import FakeClock
from fakes.upbit import (
    CANCEL_FAILS,
    OK,
    TEST_ACCESS_KEY,
    TEST_SECRET_KEY,
    FakeUpbitTransport,
    NoResponse,
    Reply,
)

from coin_report_trader.trading.upbit import (
    TransportError,
    UpbitClient,
    UpbitError,
    UpbitKeys,
    UpbitTransport,
)

KEYS = UpbitKeys(TEST_ACCESS_KEY, TEST_SECRET_KEY)
M = "KRW-AAA"


def setup(krw: Decimal = Decimal(100_000)) -> tuple[UpbitClient, FakeUpbitTransport]:
    clock = FakeClock()
    fake = FakeUpbitTransport(clock=clock)
    fake.set_account(krw=krw, coins={"AAA": (Decimal(20), Decimal(990))})
    fake.set_chance(M, bid_fee=Decimal("0.001"), ask_fee=Decimal("0.001"))
    fake.set_market_price(M, Decimal(1000))
    return UpbitClient(KEYS, fake, clock), fake


def test_is_an_upbit_transport() -> None:
    assert isinstance(FakeUpbitTransport(), UpbitTransport)


def test_buy_locks_krw_and_partial_fills_then_done() -> None:
    client, fake = setup()
    client.create_order(M, "bid", "limit", Decimal(10), Decimal(1000), "uid-1")
    # 10,000원 × (1 + 0.001) = 10,010원이 묶인다.
    assert fake.balances()["KRW"] == (Decimal(89_990), Decimal(10_010))
    fake.fill("uid-1", Decimal(4))
    order = client.get_order("uid-1")
    assert (order.state, order.executed_volume, len(order.trades)) == ("wait", Decimal(4), 1)
    fake.fill("uid-1", Decimal(6), price=Decimal(999))
    order = client.get_order("uid-1")
    assert (order.state, order.executed_volume) == ("done", Decimal(10))
    assert [t.volume for t in order.trades] == [Decimal(4), Decimal(6)]
    assert len({t.trade_key for t in order.trades}) == 2
    assert fake.balances()["AAA"][0] == Decimal(30)


def test_fill_checks_its_arguments() -> None:
    client, fake = setup()
    client.create_order(M, "bid", "limit", Decimal(10), Decimal(1000), "uid-2")
    with pytest.raises(ValueError, match="많이"):
        fake.fill("uid-2", Decimal(11))
    with pytest.raises(ValueError, match="걸린 주문"):
        fake.fill("nope", Decimal(1))


def test_sell_locks_quantity_and_cancel_releases_it() -> None:
    client, fake = setup()
    client.create_order(M, "ask", "limit", Decimal(5), Decimal(1100), "uid-3")
    assert fake.balances()["AAA"] == (Decimal(15), Decimal(5))
    client.cancel_order("uid-3")
    assert fake.order_state("uid-3") == "cancel"
    assert fake.balances()["AAA"] == (Decimal(20), Decimal(0))


def test_market_sell_is_done_at_the_market_price() -> None:
    client, fake = setup()
    order = client.create_order(M, "ask", "market", Decimal(5), None, "uid-4")
    assert order.ord_type == "market"
    filled = client.get_order("uid-4")
    assert filled.state == "done"
    assert filled.trades[0].price == Decimal(1000)
    assert fake.balances()["AAA"] == (Decimal(15), Decimal(0))
    assert fake.balances()["KRW"][0] == Decimal(100_000) + Decimal(5000) - Decimal(5)


def test_cancel_fails_when_scripted_and_the_order_stays() -> None:
    client, fake = setup()
    client.create_order(M, "bid", "limit", Decimal(10), Decimal(1000), "uid-5")
    fake.script(CANCEL_FAILS, method="DELETE")
    with pytest.raises(UpbitError):
        client.cancel_order("uid-5")
    assert fake.order_state("uid-5") == "wait"
    client.cancel_order("uid-5")  # 다 쓴 뒤에는 취소된다
    assert fake.order_state("uid-5") == "cancel"


def test_cancel_of_a_finished_or_unknown_order_is_refused() -> None:
    client, _ = setup()
    client.create_order(M, "ask", "market", Decimal(5), None, "uid-6")
    with pytest.raises(UpbitError) as finished:
        client.cancel_order("uid-6")
    assert finished.value.status == 400
    with pytest.raises(UpbitError) as unknown:
        client.cancel_order("uid-none")
    assert unknown.value.status == 404
    with pytest.raises(UpbitError) as lookup:
        client.get_order("uid-none")
    assert lookup.value.status == 404


def test_identifier_cannot_be_used_twice() -> None:
    client, _ = setup()
    client.create_order(M, "bid", "limit", Decimal(10), Decimal(1000), "uid-7")
    with pytest.raises(UpbitError) as caught:
        client.create_order(M, "bid", "limit", Decimal(10), Decimal(1000), "uid-7")
    assert caught.value.code == "duplicated_identifier"


def test_rejected_orders_are_not_kept() -> None:
    client, fake = setup(krw=Decimal(1000))
    with pytest.raises(UpbitError):
        client.create_order(M, "bid", "limit", Decimal(10), Decimal(1000), "uid-8")
    assert fake.order_state("uid-8") is None
    assert fake.balances()["KRW"] == (Decimal(1000), Decimal(0))


@pytest.mark.parametrize("sent", [True, False])
def test_no_response_after_or_before_handling(sent: bool) -> None:
    _, fake = setup()
    client = UpbitClient(KEYS, fake, FakeClock())
    fake.script(NoResponse(sent=sent))
    with pytest.raises(UpbitError) as caught:
        client.create_order(M, "bid", "limit", Decimal(10), Decimal(1000), "uid-9")
    assert caught.value.unknown is sent
    assert (fake.order_state("uid-9") is not None) is sent


def test_no_response_raises_a_transport_error_at_the_transport() -> None:
    fake = FakeUpbitTransport()
    fake.script(NoResponse(sent=False))
    with pytest.raises(TransportError) as caught:
        fake.request("GET", "/v1/accounts", query="", body=None, headers={})
    assert caught.value.sent is False


def test_script_lines_pick_method_and_path_and_ok_passes() -> None:
    client, fake = setup()
    fake.script(OK, Reply(500), path="/v1/accounts")
    fake.script(Reply(503), method="GET", path="/v1/orders/chance")
    assert client.accounts()
    with pytest.raises(UpbitError) as server:
        client.accounts()
    assert server.value.status == 500
    with pytest.raises(UpbitError) as maintenance:
        client.order_chance(M)
    assert maintenance.value.status == 503
    assert client.order_chance(M).market == M


def test_script_checks_outcomes() -> None:
    with pytest.raises(TypeError):
        FakeUpbitTransport().script("boom")  # type: ignore[arg-type]


def test_remaining_req_header_carries_the_set_sec() -> None:
    fake = FakeUpbitTransport()
    fake.set_remaining("default", 7)
    client = UpbitClient(KEYS, fake, FakeClock())
    fake.set_account(krw=Decimal(1))
    client.accounts()
    response = fake.request("GET", "/v1/accounts", query="", body=None, headers={})
    assert response.header("Remaining-Req") == "group=default; min=1800; sec=7"


def test_requests_keep_no_token() -> None:
    client, fake = setup()
    client.accounts()
    client.create_order(M, "bid", "limit", Decimal(10), Decimal(1000), "uid-10")
    for request in fake.requests:
        text = repr(request)
        assert "Bearer" not in text
        assert TEST_SECRET_KEY not in text
        assert TEST_ACCESS_KEY not in text
        assert "authorization" in request.header_names


def test_missing_token_is_refused() -> None:
    fake = FakeUpbitTransport()
    response = fake.request("GET", "/v1/accounts", query="", body=None, headers={})
    assert response.status == 401
    assert fake.requests[0].auth == "no_authorization_token"


def test_open_orders_are_only_waiting_orders_of_that_market() -> None:
    client, fake = setup()
    client.create_order(M, "bid", "limit", Decimal(10), Decimal(990), "uid-11")
    client.create_order(M, "ask", "market", Decimal(5), None, "uid-12")  # 바로 끝난다
    fake.add_direct_order("KRW-BBB", "bid", Decimal(100), Decimal(100))
    assert [o.identifier for o in client.open_orders(M)] == ["uid-11"]
