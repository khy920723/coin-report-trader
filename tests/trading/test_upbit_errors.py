"""업비트 비공개 API의 오류 이름 표와 세 갈래(M5 명세 3.5.5)의 시험."""

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fakes.clock import FakeClock
from fakes.upbit import TEST_ACCESS_KEY, TEST_SECRET_KEY, FakeUpbitTransport, Reply

from coin_report_trader.core.texts import (
    ERROR_BAD_RESPONSE,
    ERROR_BLOCKED,
    ERROR_MAINTENANCE,
    ERROR_NETWORK,
    ERROR_RATE_LIMIT,
    ERROR_SERVER,
    ERROR_TIMEOUT,
)
from coin_report_trader.trading.upbit import (
    ERROR_INSUFFICIENT_FUNDS,
    ERROR_IP_NOT_ALLOWED,
    ERROR_KEY_EXPIRED,
    ERROR_KEY_INVALID,
    ERROR_KEY_SCOPE,
    ERROR_ORDER_REJECTED,
    ErrorBranch,
    UpbitClient,
    UpbitError,
    UpbitKeys,
)
from coin_report_trader.trading.upbit.errors import branch_of, error_for_response, error_unknown

NOW = datetime(2026, 1, 5, 3, 0, tzinfo=UTC)
KEYS = UpbitKeys(TEST_ACCESS_KEY, TEST_SECRET_KEY)


def body(code: str | None, message: str = "만든 글") -> str:
    if code is None:
        return ""
    return json.dumps({"error": {"name": code, "message": message}})


@pytest.mark.parametrize(
    ("status", "code", "name", "branch"),
    [
        (401, "no_authorization_ip", ERROR_IP_NOT_ALLOWED, ErrorBranch.BLOCKING),
        (401, "no_authorization_i_p", ERROR_IP_NOT_ALLOWED, ErrorBranch.BLOCKING),
        (401, "expired_access_key", ERROR_KEY_EXPIRED, ErrorBranch.BLOCKING),
        (401, "out_of_scope", ERROR_KEY_SCOPE, ErrorBranch.BLOCKING),
        (401, "jwt_verification", ERROR_KEY_INVALID, ErrorBranch.BLOCKING),
        (401, "invalid_query_payload", ERROR_KEY_INVALID, ErrorBranch.BLOCKING),
        (401, "no_authorization_token", ERROR_KEY_INVALID, ErrorBranch.BLOCKING),
        (418, None, ERROR_BLOCKED, ErrorBranch.BLOCKING),
        (418, "insufficient_funds_bid", ERROR_BLOCKED, ErrorBranch.BLOCKING),  # 상태가 먼저
        (429, None, ERROR_RATE_LIMIT, ErrorBranch.TRANSIENT),
        (503, None, ERROR_SERVER, ErrorBranch.TRANSIENT),  # 503은 문서에 없다
        (403, "market_offline", ERROR_MAINTENANCE, ErrorBranch.TRANSIENT),
        (400, "over_krw_funds_bid", ERROR_ORDER_REJECTED, ErrorBranch.TRANSIENT),
        (500, "market_offline", ERROR_SERVER, ErrorBranch.TRANSIENT),  # 상태가 먼저
        (500, None, ERROR_SERVER, ErrorBranch.TRANSIENT),
        (502, "insufficient_funds_bid", ERROR_SERVER, ErrorBranch.TRANSIENT),  # 상태가 먼저
        (400, "insufficient_funds_bid", ERROR_INSUFFICIENT_FUNDS, ErrorBranch.TRANSIENT),
        (400, "insufficient_funds_ask", ERROR_INSUFFICIENT_FUNDS, ErrorBranch.TRANSIENT),
        (400, "under_min_total_bid", ERROR_ORDER_REJECTED, ErrorBranch.TRANSIENT),
        (400, "under_min_total_ask", ERROR_ORDER_REJECTED, ErrorBranch.TRANSIENT),
        (
            400,
            "validation_error",
            "알 수 없는 거래소 오류(400. validation_error)",
            ErrorBranch.UNLISTED,
        ),
        (
            404,
            "order_not_found",
            "알 수 없는 거래소 오류(404. order_not_found)",
            ErrorBranch.UNLISTED,
        ),
        (401, "nonce_used", "알 수 없는 거래소 오류(401. nonce_used)", ErrorBranch.UNLISTED),
        (401, None, "알 수 없는 거래소 오류(401)", ErrorBranch.UNLISTED),
        (400, None, "알 수 없는 거래소 오류(400)", ErrorBranch.UNLISTED),
    ],
)
def test_error_name_table(status: int, code: str | None, name: str, branch: ErrorBranch) -> None:
    error = error_for_response(status, body(code), None, now=NOW)
    assert error.error_name == name
    assert error.branch is branch
    assert (error.status, error.code) == (status, code)
    assert error.args == (name,)


@pytest.mark.parametrize(
    "name",
    [ERROR_TIMEOUT, ERROR_NETWORK, ERROR_BAD_RESPONSE, ERROR_RATE_LIMIT, ERROR_SERVER],
)
def test_transient_names_without_a_response(name: str) -> None:
    assert branch_of(name) is ErrorBranch.TRANSIENT


def test_error_unknown_names() -> None:
    assert error_unknown(400, "made_up") == "알 수 없는 거래소 오류(400. made_up)"
    assert error_unknown(404, None) == "알 수 없는 거래소 오류(404)"
    assert error_unknown(404, "") == "알 수 없는 거래소 오류(404)"


@pytest.mark.parametrize(
    "text",
    ["", "not json", "[]", '{"error": "x"}', '{"error": {"name": 5}}', "{" * 5000],
)
def test_unreadable_error_bodies_do_not_raise(text: str) -> None:
    error = error_for_response(400, text, None, now=NOW)
    assert error.error_name == "알 수 없는 거래소 오류(400)"
    assert error.code is None
    assert error.detail is None


def test_response_text_is_never_the_error_name_and_detail_is_redacted() -> None:
    message = f"key {TEST_SECRET_KEY} was wrong"
    error = error_for_response(
        401, body("jwt_verification", message), None, now=NOW, secrets=(TEST_SECRET_KEY,)
    )
    assert error.error_name == ERROR_KEY_INVALID
    assert TEST_SECRET_KEY not in (error.detail or "")
    assert "가려서 보임" in (error.detail or "")
    assert "wrong" not in str(error)
    assert "wrong" not in repr(error)


@pytest.mark.parametrize(
    "code",
    ["no_authorization_ip", "expired_access_key", "out_of_scope", "jwt_verification"],
)
def test_four_key_errors_through_the_client_are_blocking(code: str) -> None:
    clock = FakeClock(NOW)
    fake = FakeUpbitTransport(clock=clock)
    message = f"access {TEST_ACCESS_KEY} secret {TEST_SECRET_KEY} refused"
    fake.script(Reply(401, code, message=message))
    client = UpbitClient(KEYS, fake, clock)
    with pytest.raises(UpbitError) as caught:
        client.accounts()
    error = caught.value
    assert error.branch is ErrorBranch.BLOCKING
    assert error.code == code
    assert len(fake.requests) == 1
    # 클라이언트가 두 키를 가린다(가리지 않으면 응답의 글에 든 키가 그대로 남는다).
    for text in (error.detail or "", str(error), repr(error), repr(error.args)):
        assert TEST_ACCESS_KEY not in text
        assert TEST_SECRET_KEY not in text
    assert (error.detail or "").count("가려서 보임") == 2
    assert "refused" in (error.detail or "")


def test_order_rejections_through_the_client() -> None:
    clock = FakeClock(NOW)
    fake = FakeUpbitTransport(clock=clock)
    fake.set_account(krw=Decimal(3000), coins={"AAA": (Decimal(1), Decimal(1000))})
    fake.set_chance("KRW-AAA", min_total=Decimal(5000))
    client = UpbitClient(KEYS, fake, clock)
    names = []
    for side, volume, price, uid in [
        ("bid", Decimal(10), Decimal(1000), "u1"),  # 원화 3,000원으로 10,000원어치
        ("ask", Decimal(10), Decimal(1000), "u2"),  # 1개뿐인데 10개
        ("bid", Decimal(2), Decimal(1000), "u3"),  # 2,000원은 최소 주문 금액 미달
    ]:
        with pytest.raises(UpbitError) as caught:
            client.create_order("KRW-AAA", side, "limit", volume, price, uid)
        names.append(caught.value.error_name)
    assert names == [ERROR_INSUFFICIENT_FUNDS, ERROR_INSUFFICIENT_FUNDS, ERROR_ORDER_REJECTED]
