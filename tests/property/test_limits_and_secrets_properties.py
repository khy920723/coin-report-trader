"""요청 한도, 비밀 값 가리기, 웹후크 주소 꼴, 알림 보관 기간의 불변식(M1 명세 3.7, 3.9, 3.10).

시계는 `tests/fakes/`의 가짜 시계다(실제로 기다리지 않는다). 주소처럼 생긴 글은 소스에 통째로
적지 않고 실행 중에 이어 붙여 만든다(M1 명세 3.13).
"""

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from fakes.clock import FakeClock
from hypothesis import given
from hypothesis import strategies as st

from coin_report_trader.core import MASK, AlertCategory, Mode, is_webhook_url, redact
from coin_report_trader.storage.alerts import delete_expired, insert_alert
from coin_report_trader.storage.database import migrate
from coin_report_trader.trading.quote.ratelimit import LIMIT, WINDOW, RequestLimiter

# --- 요청 한도 ------------------------------------------------------------------------------------

gaps = st.lists(
    st.one_of(st.just(0.0), st.floats(min_value=0, max_value=2.5, allow_nan=False)),
    min_size=1,
    max_size=40,
)


def _sent_times(gap_list: list[float]) -> tuple[list[float], list[float]]:
    """요청이 온 단조 시각과 낸 단조 시각."""
    clock = FakeClock()
    limiter = RequestLimiter(clock)
    arrived, sent = [], []
    for gap in gap_list:
        clock.advance(gap)
        arrived.append(clock.monotonic())
        sent.append(limiter.acquire())
    return arrived, sent


@given(gaps)
def test_no_window_of_one_second_holds_more_requests_than_the_limit(gap_list):
    """어떤 요청 차례와 시각에서도, 양 끝을 포함한 1초 구간에 한도보다 많은 요청이 없다."""
    _, sent = _sent_times(gap_list)
    for start in sent:
        inside = [t for t in sent if start <= t <= start + WINDOW]
        assert len(inside) <= LIMIT


@given(gaps)
def test_a_request_is_never_sent_before_it_arrives_and_the_order_is_kept(gap_list):
    """요청은 온 때보다 앞서 나가지 않고, 낸 차례는 온 차례와 같다."""
    arrived, sent = _sent_times(gap_list)
    assert all(s >= a for s, a in zip(sent, arrived, strict=True))
    assert sent == sorted(sent)


# --- 비밀 값 가리기 -------------------------------------------------------------------------------

TOKEN_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_."
hosts = st.sampled_from(["discord.com", "discordapp.com"]).flatmap(
    lambda host: st.sampled_from(["", "canary.", "ptb."]).map(lambda lead: lead + host)
)
versions = st.one_of(st.just(""), st.integers(1, 99).map(lambda n: f"v{n}/"))
ids = st.text("0123456789", min_size=17, max_size=20)
tokens = st.text(TOKEN_CHARS, min_size=60, max_size=90)


@st.composite
def webhook(draw: st.DrawFn) -> tuple[str, str]:
    """(주소, 토큰). 주소는 실행 중에 이어 붙인다."""
    token = draw(tokens)
    url = "https:" + "//" + draw(hosts) + "/api/" + draw(versions) + "webhooks/" + draw(ids)
    return url + "/" + token, token


around = st.text(max_size=40).filter(lambda s: "https:" not in s.lower())


@given(webhook(), around, around, st.booleans())
def test_a_webhook_token_never_survives_redaction(found, before, after, escaped):
    """주소가 글 가운데 어디에 있든(JSON의 `\\/` 꼴 포함) 가린 글에 토큰이 남지 않는다."""
    url, token = found
    shown = url.replace("/", "\\/") if escaped else url
    text = redact(before + shown + after, env={})
    assert token not in text
    # JSON에 담은 글(한 번만 이스케이프된 꼴)도 같다.
    assert token not in redact(json.dumps({"error": before + url + after}), env={})


secrets = st.text("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", min_size=8, max_size=24)


@given(st.lists(secrets, min_size=1, max_size=3), st.text(max_size=60), st.data())
def test_given_secrets_never_survive_redaction(values, filler, data):
    """넘겨받은 비밀 값은 글 어디에 몇 번 있든 가린 글에 남지 않는다."""
    pieces = data.draw(st.lists(st.sampled_from([*values, filler]), min_size=1, max_size=8))
    text = redact("".join(pieces), values, env={})
    assert not any(value in text for value in values)


@given(secrets, secrets, secrets)
def test_a_secret_that_holds_another_secret_is_masked_whole(head, inner, tail):
    """다른 비밀 값을 품은 비밀 값은 반쪽이 남지 않고 통째로 가려진다(긴 값부터 바꾼다)."""
    outer = head + inner + tail
    assert redact(outer, [inner, outer], env={}) == MASK
    assert redact(outer, [outer, inner], env={}) == MASK


def test_a_secret_that_holds_a_lexically_later_secret_is_masked_whole():
    """글자 차례로는 품은 값이 뒤에 오는 경우(예: "Z…"를 품은 "AZ…")도 통째로 가려진다."""
    assert redact("A" + "Z" * 9, ["Z" * 9, "A" + "Z" * 9], env={}) == MASK
    assert redact("AB" + "Y" * 8 + "C", ["Y" * 8 + "C", "AB" + "Y" * 8 + "C"], env={}) == MASK


@given(st.text(max_size=3000))
def test_redaction_is_short_and_stable(text):
    """가린 글은 2,000자를 넘지 않고, 한 번 더 가려도 같다(웹후크 꼴과 비밀이 없는 글)."""
    once = redact(text, env={})
    assert len(once) <= 2000
    assert redact(once, env={}) == once


# --- 웹후크 주소 꼴 -------------------------------------------------------------------------------


@given(webhook(), st.sampled_from(["", " ", "\t", "\n", "  "]), st.sampled_from(["", " ", "\n"]))
def test_every_well_formed_address_is_accepted_with_surrounding_blanks(found, lead, tail):
    """꼴을 지킨 주소는 앞뒤 빈칸이 있어도 받는다."""
    url, _ = found
    assert is_webhook_url(lead + url + tail)


@given(webhook(), st.sampled_from(["/", "?", "#", "/x", "?a=1", "#x", " x", "%"]))
def test_anything_after_the_token_is_refused(found, extra):
    """토큰 뒤에 무엇이 있으면(끝의 `/`, `?`, `#` 포함) 받지 않는다."""
    url, _ = found
    assert not is_webhook_url(url + extra)


@given(webhook(), st.integers(0, 59))
def test_a_short_token_or_a_wrong_number_is_refused(found, keep):
    """토큰이 60자보다 짧거나, 번호가 17~20자의 숫자가 아니면 받지 않는다."""
    url, token = found
    assert not is_webhook_url(url[: len(url) - len(token)] + token[:keep])
    head, number, tail = url.rsplit("/", 2)[0], url.rsplit("/", 2)[1], token
    assert not is_webhook_url(head + "/" + number[:16] + "/" + tail)
    assert not is_webhook_url(head + "/" + number + "1" * (21 - len(number)) + "/" + tail)
    assert not is_webhook_url(head + "/" + number[:-1] + "a" + "/" + tail)


@given(webhook())
def test_http_or_another_host_is_refused(found):
    """`https://`가 아니거나 호스트가 디스코드의 것이 아니면 받지 않는다."""
    url, _ = found
    assert not is_webhook_url("http:" + url[len("https:") :])
    assert not is_webhook_url(url.replace(".com/", ".org/", 1))
    assert not is_webhook_url(url.replace("https:" + "//", "https:" + "//x", 1))


# --- 알림 보관 기간 -------------------------------------------------------------------------------

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
ages = st.lists(
    st.integers(0, 100 * 24 * 3600).map(lambda s: timedelta(seconds=s)), min_size=1, max_size=30
)


def _alert_db(age_list: list[timedelta]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    migrate(conn)
    for age in age_list:
        insert_alert(
            conn,
            created_at=NOW - age,
            mode=Mode.PAPER,
            category=AlertCategory.OTHER,
            code="A-30",
            coin=None,
            text="만든 알림",
            detail=None,
            seen=False,
            discord_state="no_address",
        )
    return conn


@given(ages, st.sampled_from([7, 30, 90]))
def test_only_alerts_past_the_retention_are_deleted_and_a_second_pass_deletes_nothing(
    age_list, days
):
    """보관 기간이 지난 알림(생긴 때 + 날 수 × 24시간 ≤ 지금)만 지우고, 다시 지우면 0이다."""
    conn = _alert_db(age_list)
    deleted = delete_expired(conn, retention_days=days, now=NOW)
    left = [row[0] for row in conn.execute("SELECT created_utc FROM alerts")]
    limit = timedelta(days=days)
    assert deleted == sum(1 for age in age_list if age >= limit)
    assert len(left) == sum(1 for age in age_list if age < limit)
    assert delete_expired(conn, retention_days=days, now=NOW) == 0
