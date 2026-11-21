"""요청 한도, 비밀 값 가리기, 웹후크 주소 꼴, 알림 보관 기간의 불변식(M1 명세 3.7, 3.9, 3.10).

시계는 `tests/fakes/`의 가짜 시계다(실제로 기다리지 않는다). 주소처럼 생긴 글은 소스에 통째로
적지 않고 실행 중에 이어 붙여 만든다(M1 명세 3.13).
"""

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fakes.clock import FakeClock
from hypothesis import example, given
from hypothesis import strategies as st

from coin_report_trader.core import MASK, AlertCategory, Mode, is_webhook_url, redact
from coin_report_trader.storage.alerts import delete_expired, insert_alert
from coin_report_trader.storage.database import migrate
from coin_report_trader.trading.quote.ratelimit import RequestLimiter

# --- 요청 한도 ------------------------------------------------------------------------------------

SPEC_LIMIT = 5
"""명세 3.10의 한도: 길이 1초인 어느 구간에도 5번을 넘지 않는다(앱의 상수를 쓰지 않는다)."""
SPEC_WINDOW = 1.0
MONOTONIC_START = 1024.0
"""가짜 시계의 처음 단조 시각. 시각이 [1024, 2048)의 한 지수 구간에 머물러, 1초를 더하고 빼는
셈이 정확하다(구간을 넘나들면 덧셈의 반올림 때문에 맞는 장치도 경계에서 어긋나 보일 수 있다)."""

gaps = st.lists(
    st.one_of(st.just(0.0), st.floats(min_value=0, max_value=2.5, allow_nan=False)),
    min_size=1,
    max_size=40,
)
TRICKY_GAPS = (
    [2.5] * 9 + [1.1] + [0.0] * 5,
    [2.5] * 8 + [1.1, 2.00001] + [0.0] * 4 + [1.0],
)
"""지휘 검토가 찾은, 지수 구간을 넘나들던 때 기준이 흔들린 입력."""


def _sent_times(gap_list: list[float]) -> tuple[list[float], list[float]]:
    """요청이 온 단조 시각과 낸 단조 시각."""
    clock = FakeClock(monotonic_start=MONOTONIC_START)
    limiter = RequestLimiter(clock)
    arrived, sent = [], []
    for gap in gap_list:
        clock.advance(gap)
        arrived.append(clock.monotonic())
        sent.append(limiter.acquire())
    return arrived, sent


@given(gaps)
@example(TRICKY_GAPS[0])
@example(TRICKY_GAPS[1])
def test_no_window_of_one_second_holds_more_requests_than_the_limit(gap_list):
    """어떤 요청 차례와 시각에서도, 양 끝을 포함한 1초 구간에 5번보다 많은 요청이 없다."""
    _, sent = _sent_times(gap_list)
    for start in sent:
        inside = [t for t in sent if start <= t <= start + SPEC_WINDOW]
        assert len(inside) <= SPEC_LIMIT


@given(gaps)
def test_a_request_is_never_sent_before_it_arrives(gap_list):
    """돌려준(낸) 시각은 그 요청이 온 시각보다 앞서지 않는다."""
    arrived, sent = _sent_times(gap_list)
    assert all(s >= a for s, a in zip(sent, arrived, strict=True))


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


FILLER = "가나다라마바사 ,;:!()[]{}<>\n\t=+*&^%$#@"
"""채움 글의 글자. 토큰 글자(영문, 숫자, `-_.`)가 없어 토큰의 조각이 우연히 생기지 않는다."""
SEPARATORS = (" ", ",", "\n", '"', ")")
"""토큰을 끝내는 구분자(토큰 글자가 아니다)."""


def _mixed_case(text: str, flips: list[bool]) -> str:
    return (
        "".join(c.swapcase() if flip else c for c, flip in zip(text, flips, strict=False))
        + text[len(flips) :]
    )


def _chunks(token: str, size: int = 16) -> list[str]:
    return [token[i : i + size] for i in range(len(token) - size + 1)]


@st.composite
def text_with_webhooks(draw: st.DrawFn) -> tuple[str, str, list[str]]:
    r"""주소 1~3개를 구분자로 이어 채움 글 사이에 둔 글. (이스케이프를 고른 글, 이스케이프하지
    않은 같은 글, 토큰들).

    대소문자를 섞고, 주소마다 `\/` 이스케이프를 고르고, 앞 채움 글의 길이를 0~2,100자로 골라
    2,000자 자르기를 넘나들게 한다.
    """
    shown: list[str] = []
    plain: list[str] = []
    found: list[str] = []
    for _ in range(draw(st.integers(1, 3))):
        url, _token = draw(webhook())
        url = _mixed_case(url, draw(st.lists(st.booleans(), max_size=len(url))))
        plain.append(url)
        found.append(url.rsplit("/", 1)[1])
        shown.append(url.replace("/", "\\/") if draw(st.booleans()) else url)
    separator = draw(st.sampled_from(SEPARATORS))
    lead_size = draw(st.one_of(st.integers(0, 40), st.integers(1900, 2100)))
    lead = draw(st.text(FILLER, min_size=lead_size, max_size=lead_size))
    tail = draw(st.sampled_from(["", *SEPARATORS])) + draw(st.text(FILLER, max_size=40))
    return lead + separator.join(shown) + tail, lead + separator.join(plain) + tail, found


@given(text_with_webhooks())
def test_a_webhook_token_never_survives_redaction(case):
    r"""주소가 1~3개, 대소문자가 섞이고 `\/`로 이스케이프되고 2,000자 자르기에 걸려도, 가린
    글에 어떤 토큰의 16자 조각도 남지 않는다. JSON에 담은 글(한 번 이스케이프된 꼴)도 같다."""
    text, plain, found = case
    for shown in (redact(text, env={}), redact(json.dumps({"error": plain}), env={})):
        for token in found:
            assert not any(chunk in shown for chunk in _chunks(token))


@pytest.mark.parametrize("joiner", ["", ".", "-", "_"], ids=["붙음", "점", "빼기", "밑줄"])
def test_two_webhooks_side_by_side_are_both_masked(joiner):
    """맞는 주소 둘이 바로 붙거나 토큰 글자로 이어져도 두 토큰이 모두 가려진다(앱 코드의 버그)."""
    first = "https:" + "//" + "discord.com/api/webhooks/" + "1" * 18 + "/" + "a" * 64
    second = "https:" + "//" + "discord.com/api/webhooks/" + "2" * 18 + "/" + "b" * 64
    shown = redact(first + joiner + second, env={})
    assert "a" * 16 not in shown
    assert "b" * 16 not in shown


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


def _path_piece() -> st.SearchStrategy[str]:
    """사용자 폴더 경로 조각(실행 중에 이어 붙인다)."""
    users = "Us" + "ers"
    return st.sampled_from(
        [
            "C:" + "\\" + users + "\\kim\\a.txt",
            "/" + "home" + "/kim/a.txt",
            "D:/" + users + "/lee park/b",
        ]
    )


@st.composite
def long_text(draw: st.DrawFn) -> str:
    """1,900~2,100자 언저리에 주소나 경로 조각을 섞은 글, 또는 2,001~3,000자의 글."""
    if draw(st.booleans()):
        size = draw(st.integers(2001, 3000))
        return draw(st.text(FILLER + "abc", min_size=size, max_size=size))
    head = draw(st.text(FILLER, min_size=1850, max_size=2050))
    piece = draw(st.one_of(webhook().map(lambda found: found[0]), _path_piece()))
    tail = draw(st.text(FILLER, max_size=200))
    return head + " " + piece + " " + tail


@given(st.one_of(st.text(max_size=300), long_text()))
def test_redaction_is_short_and_stable(text):
    """가린 글은 2,000자를 넘지 않고, 한 번 더 가려도 같다."""
    once = redact(text, env={})
    assert len(once) <= 2000
    assert redact(once, env={}) == once


def test_a_user_folder_with_a_space_in_its_name_is_shortened_whole():
    """`USERPROFILE`의 이름에 빈칸이 있어도 그 폴더 전체가 `~` 하나로 줄어든다(이름의 뒷부분이
    남지 않는다. `redaction.py`의 사용자 폴더 값 뒤 이어짐 검사)."""
    value = "C:" + "\\" + "Us" + "ers" + "\\" + "Kim Lee"
    env = {"USERPROFILE": value, "HOME": value}
    assert redact(value, env=env) == "~"
    assert redact(value + "\\a.txt", env=env) == "~\\a.txt"


def test_only_home_and_userprofile_shorten_their_sibling_folders():
    """`LOCALAPPDATA`와 `XDG_DATA_HOME`의 값의 형제 폴더는 줄이지 않는다(사용자 폴더의 값만
    그 부모 아래의 이름을 찾는다)."""
    local = "E:" + "\\Data\\Local"
    env = {"LOCALAPPDATA": local, "XDG_DATA_HOME": local}
    assert redact(local + "\\x", env=env) == "%LOCALAPPDATA%\\x"
    sibling = "E:" + "\\Data\\Roaming\\x"
    assert redact(sibling, env=env) == sibling


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
DAY_SECONDS = 24 * 3600


@st.composite
def days_and_ages(draw: st.DrawFn) -> tuple[int, list[int]]:
    """보관 기간을 먼저 고르고, 나이(초)는 아무 값과 경계(기간 × 24시간) ±2초를 섞는다."""
    days = draw(st.sampled_from([7, 30, 90]))
    edge = days * DAY_SECONDS
    ages = draw(
        st.lists(
            st.one_of(st.integers(0, 100 * DAY_SECONDS), st.integers(edge - 2, edge + 2)),
            min_size=1,
            max_size=30,
        )
    )
    return days, ages


def _alert_db(age_list: list[int]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    migrate(conn)
    for age in age_list:
        insert_alert(
            conn,
            created_at=NOW - timedelta(seconds=age),
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


@given(days_and_ages())
@example((7, [7 * DAY_SECONDS]))
@example((30, [30 * DAY_SECONDS - 1, 30 * DAY_SECONDS]))
def test_only_alerts_past_the_retention_are_deleted_and_a_second_pass_deletes_nothing(case):
    """보관 기간이 지난 알림(생긴 때 + 날 수 × 24시간 ≤ 지금)만 지우고, 다시 지우면 0이다."""
    days, age_list = case
    conn = _alert_db(age_list)
    deleted = delete_expired(conn, retention_days=days, now=NOW)
    left = [row[0] for row in conn.execute("SELECT created_utc FROM alerts")]
    limit = days * DAY_SECONDS
    assert deleted == sum(1 for age in age_list if age >= limit)
    assert len(left) == sum(1 for age in age_list if age < limit)
    assert delete_expired(conn, retention_days=days, now=NOW) == 0
