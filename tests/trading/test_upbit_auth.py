"""업비트 인증(M5 명세 3.5.1)의 시험: JWT, `query_hash`, nonce, 키 가리기.

서명과 `query_hash`가 맞는지는 클라이언트 코드를 쓰지 않는 가짜(`tests/fakes/upbit.py`)가 따로
검사한다. 키는 꼴만 맞춘 만든 값이다.
"""

import base64
import hashlib
import json
import pickle
from decimal import Decimal

import pytest
from fakes.clock import FakeClock
from fakes.upbit import TEST_ACCESS_KEY, TEST_SECRET_KEY, FakeUpbitTransport, Reply

from coin_report_trader.trading.upbit import (
    UpbitClient,
    UpbitError,
    UpbitKeys,
    make_token,
    query_hash,
    query_text,
)

KEYS = UpbitKeys(TEST_ACCESS_KEY, TEST_SECRET_KEY)
M = "KRW-AAA"


def decode(part: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def setup() -> tuple[UpbitClient, FakeUpbitTransport]:
    clock = FakeClock()
    fake = FakeUpbitTransport(clock=clock)
    fake.set_account(krw=Decimal(100_000), coins={"AAA": (Decimal(10), Decimal(1000))})
    fake.set_chance(M)
    fake.set_market_price(M, Decimal(1000))
    return UpbitClient(KEYS, fake, clock), fake


# --- 쿼리 글과 해시 -------------------------------------------------------------------------


def test_query_text_is_unencoded_and_keeps_order() -> None:
    assert query_text([("market", M), ("identifier", "abc-1_2.3~")]) == (
        "market=KRW-AAA&identifier=abc-1_2.3~"
    )
    assert query_text([("states[]", "wait"), ("states[]", "watch")]) == (
        "states[]=wait&states[]=watch"
    )
    assert query_text([]) == ""


@pytest.mark.parametrize("value", ["a b", "a&b", "a=b", "가", "", "a%20"])
def test_query_values_that_would_need_encoding_are_refused(value: str) -> None:
    with pytest.raises(ValueError, match="인코딩"):
        query_text([("market", value)])


@pytest.mark.parametrize("key", ["", "a b", "1a", "a[", "a&"])
def test_bad_query_keys_are_refused(key: str) -> None:
    with pytest.raises(ValueError, match="키"):
        query_text([(key, "x")])


def test_query_hash_is_sha512_hex() -> None:
    text = "market=KRW-AAA"
    assert query_hash(text) == hashlib.sha512(text.encode()).hexdigest()
    assert len(query_hash(text)) == 128


# --- 토큰 ---------------------------------------------------------------------------------


def test_token_has_hs512_header_access_key_nonce_and_hash() -> None:
    token = make_token(KEYS, "market=KRW-AAA", nonce="n-1")
    head, payload, signature = token.split(".")
    assert decode(head) == {"alg": "HS512", "typ": "JWT"}
    assert decode(payload) == {
        "access_key": TEST_ACCESS_KEY,
        "nonce": "n-1",
        "query_hash": hashlib.sha512(b"market=KRW-AAA").hexdigest(),
        "query_hash_alg": "SHA512",
    }
    assert "=" not in token  # base64url, 채움 없음
    assert len(base64.urlsafe_b64decode(signature + "==")) == 64  # SHA512의 길이


def test_token_without_params_has_no_hash() -> None:
    payload = decode(make_token(KEYS, "").split(".")[1])
    assert set(payload) == {"access_key", "nonce"}


def test_nonce_is_a_new_uuid4_every_time() -> None:
    nonces = {decode(make_token(KEYS, "").split(".")[1])["nonce"] for _ in range(50)}
    assert len(nonces) == 50
    assert all(len(n) == 36 and n[14] == "4" for n in nonces)


def test_every_request_signs_with_a_new_nonce_and_passes_the_fake() -> None:
    client, fake = setup()
    client.accounts()
    client.order_chance(M)
    client.create_order(M, "bid", "limit", Decimal(10), Decimal(1000), "id-1")
    client.get_order("id-1")
    client.open_orders(M)
    client.cancel_order("id-1")
    client.accounts()
    assert len(fake.requests) == 7
    assert [r.auth for r in fake.requests] == [None] * 7
    # 쿼리나 몸통이 있는 요청에는 query_hash가 있고, 계정 읽기에는 없다.
    assert [r.query_hash is not None for r in fake.requests] == [
        False,
        True,
        True,
        True,
        True,
        True,
        False,
    ]


def test_post_body_and_hash_use_the_same_fields_in_the_same_order() -> None:
    client, fake = setup()
    client.create_order(M, "ask", "market", Decimal("5.5"), None, "id-sell")
    request = fake.requests[0]
    body = json.loads(request.body, object_pairs_hook=list)
    text = "&".join(f"{k}={v}" for k, v in body)
    assert text == (
        "market=KRW-AAA&side=ask&volume=5.5&ord_type=market&identifier=id-sell"
        "&smp_type=cancel_maker"
    )
    assert request.query == ""
    assert request.query_hash == hashlib.sha512(text.encode()).hexdigest()


def test_the_fake_rejects_a_wrong_secret() -> None:
    clock = FakeClock()
    fake = FakeUpbitTransport(clock=clock)
    wrong = UpbitClient(UpbitKeys(TEST_ACCESS_KEY, "OTHERSECRET000"), fake, clock)
    with pytest.raises(UpbitError) as caught:
        wrong.accounts()
    assert caught.value.error_name == "키가 맞지 않음"
    assert fake.requests[0].auth == "jwt_verification"


def test_the_fake_rejects_another_access_key() -> None:
    clock = FakeClock()
    fake = FakeUpbitTransport(clock=clock)
    other = UpbitClient(UpbitKeys("OTHERACCESS000", TEST_SECRET_KEY), fake, clock)
    with pytest.raises(UpbitError) as caught:
        other.accounts()
    assert caught.value.error_name == "키가 맞지 않음"
    assert fake.requests[0].auth == "jwt_verification"


def test_the_fake_rejects_a_hash_that_does_not_match_the_query() -> None:
    fake = FakeUpbitTransport()
    token = make_token(KEYS, "market=KRW-BBB")  # 다른 글로 만든 해시
    response = fake.request(
        "GET",
        "/v1/orders/chance",
        query="market=KRW-AAA",
        body=None,
        headers={"Authorization": "Bearer " + token},
    )
    assert response.status == 401
    assert fake.requests[0].auth == "invalid_query_payload"


def test_the_fake_rejects_a_reused_nonce() -> None:
    fake = FakeUpbitTransport()
    token = make_token(KEYS, "", nonce="same")
    for _ in range(2):
        fake.request(
            "GET", "/v1/accounts", query="", body=None, headers={"Authorization": "Bearer " + token}
        )
    assert [r.auth for r in fake.requests] == [None, "nonce_used"]


# --- 키 가리기 ------------------------------------------------------------------------------


def test_keys_are_not_in_repr_or_str() -> None:
    for text in (repr(KEYS), str(KEYS), f"{KEYS}", repr([KEYS])):
        assert TEST_ACCESS_KEY not in text
        assert TEST_SECRET_KEY not in text
    assert "가려서 보임" in repr(KEYS)


def test_keys_are_not_pickled() -> None:
    with pytest.raises(TypeError):
        pickle.dumps(KEYS)


@pytest.mark.parametrize(("access", "secret"), [("", "s"), ("a", ""), (1, "s")])
def test_empty_or_non_text_keys_are_refused(access: object, secret: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        UpbitKeys(access, secret)  # type: ignore[arg-type]


def test_client_repr_and_errors_do_not_hold_keys_or_tokens() -> None:
    client, fake = setup()
    fake.script(
        Reply(401, "jwt_verification", message=f"{TEST_ACCESS_KEY}:{TEST_SECRET_KEY} is wrong")
    )
    with pytest.raises(UpbitError) as caught:
        client.accounts()
    error = caught.value
    texts = [repr(client), repr(error), str(error), repr(error.args), str(error.detail)]
    for text in texts:
        assert TEST_ACCESS_KEY not in text
        assert TEST_SECRET_KEY not in text
        assert "Bearer" not in text
    assert "가려서 보임" in str(error.detail)
    assert error.__context__ is None
    assert error.__cause__ is None
