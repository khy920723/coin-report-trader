"""차등 시험: 같은 입력(hypothesis가 만든 입력)으로 앱 코드와 시험 안의 기준 구현을 견준다.

기준 구현은 앱 코드를 옮겨 쓰지 않고 명세와 제품 규칙의 정의에서 바로 쓴, 느리지만 뻔한 것이다.

- 봉 합치기(M1 명세 3.7): (마켓, 단위, 시작 시각)을 열쇠로 한 사전에 나중 값으로 덮어 쓰고 시작
  시각으로 정렬한다.
- 요청 한도(M1 명세 3.10): 양 끝을 포함한 길이 1초의 창에 든 앞 요청을 센다.
- 웹후크 주소 꼴(M1 명세 3.9의 "웹후크 주소의 꼴"): 정규식 없이 글을 나눠 조건을 하나씩 본다.
- 오래된 알림 지우기(M1 명세 3.9): 생긴 때 + 날 수 × 24시간 ≤ 지금인 알림을 고른다.

봉 통계는 1단계 보완에서 기간과 지표의 항목이 바뀌므로 그 값을 굳히는 차등 시험을 두지 않는다
(값이 아닌 성질은 `tests/property/test_stats_properties.py`가 본다).
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fakes.clock import FakeClock
from hypothesis import given
from hypothesis import strategies as st

from coin_report_trader.core import AlertCategory, Candle, CandleUnit, Mode, is_webhook_url
from coin_report_trader.storage.alerts import delete_expired, insert_alert
from coin_report_trader.storage.candles import read_candles, write_candles
from coin_report_trader.storage.database import migrate
from coin_report_trader.trading.quote.ratelimit import RequestLimiter

# --- 봉 합치기 ------------------------------------------------------------------------------------

BASE = datetime(2026, 1, 1, tzinfo=UTC)
STEP = {CandleUnit.DAY: timedelta(days=1), CandleUnit.MINUTE_60: timedelta(hours=1)}
numbers = st.decimals(min_value=Decimal("0.0001"), max_value=Decimal("99999999"), places=4)


@st.composite
def any_candle(draw: st.DrawFn) -> Candle:
    """값이 서로 맞지 않아도 되는 봉(합치기는 값을 보지 않는다).

    같은 열쇠가 자주 겹치게 시작 시각의 칸을 좁힌다.
    """
    unit = draw(st.sampled_from(list(CandleUnit)))
    return Candle(
        market=draw(st.sampled_from(["KRW-AAA", "KRW-BBB"])),
        unit=unit,
        start=BASE + STEP[unit] * draw(st.integers(0, 12)),
        open=draw(numbers),
        high=draw(numbers),
        low=draw(numbers),
        close=draw(numbers),
        volume=draw(numbers),
        value=draw(numbers),
    )


def reference_merge(batches: list[list[Candle]]) -> dict[tuple[str, CandleUnit], list[Candle]]:
    kept: dict[tuple[str, CandleUnit, datetime], Candle] = {}
    for batch in batches:
        for candle in batch:
            kept[(candle.market, candle.unit, candle.start)] = candle  # 나중 값이 이긴다
    out: dict[tuple[str, CandleUnit], list[Candle]] = {}
    for (market, unit, _start), candle in sorted(kept.items(), key=lambda item: item[0][2]):
        out.setdefault((market, unit), []).append(candle)
    return out


@given(st.lists(st.lists(any_candle(), max_size=20), min_size=1, max_size=6))
def test_candle_merge_matches_a_last_value_wins_dictionary(batches):
    """여러 번 받은 봉을 저장한 결과가 '열쇠마다 나중 값, 시작 시각 순서'의 기준과 같다.

    다르게 둔 곳: 한 묶음 안에서 같은 열쇠가 두 번 오면 앱(`INSERT OR REPLACE`)도 기준도
    그 묶음의 뒤 것을 쓴다. 명세의 "같은 (마켓, 단위, 시작 시각)은 덮어쓴다"를 묶음 안에도
    넓혀 읽은 것이다.
    """
    conn = sqlite3.connect(":memory:")
    migrate(conn)
    for batch in batches:
        write_candles(conn, batch)
    expected = reference_merge(batches)
    for market in ("KRW-AAA", "KRW-BBB"):
        for unit in CandleUnit:
            assert list(read_candles(conn, market, unit)) == expected.get((market, unit), [])


# --- 요청 한도 ------------------------------------------------------------------------------------

WINDOW = 1.0
LIMIT = 5


def reference_bound(sent: list[float], arrived: float) -> tuple[float, bool]:
    """기준: 온 때에 바로 낼 수 있으면 (온 때, 참). 아니면 (낼 수 있는 때의 하한, 거짓).

    정의: 때 t에 내도 되는 것은, 양 끝을 포함한 창 [t − 1초, t]에 든 앞 요청이 다섯보다 적을 때다.
    창에 든 수는 t가 커질수록 줄기만 하므로, 막혔으면 낼 수 있는 때는 어떤 앞 요청의 낸 때 + 1초
    "바로 뒤"부터다(그 순간은 아직 그 요청이 창의 끝에 걸려 있다). 후보 c(앞 요청 + 1초)를 차례로
    늘려 가며, c 바로 뒤의 창 (c − 1초, c]에 든 수를 세어 처음으로 다섯보다 적어지는 c가 하한이다.
    """
    if len([s for s in sent if arrived - WINDOW <= s <= arrived]) < LIMIT:
        return arrived, True
    for c in sorted({s + WINDOW for s in sent if s + WINDOW >= arrived}):
        if len([s for s in sent if c - WINDOW < s <= c]) < LIMIT:
            return c, False
    raise AssertionError("기준이 하한을 찾지 못했다")


@given(
    st.lists(
        st.one_of(st.just(0.0), st.floats(min_value=0, max_value=2.5, allow_nan=False)),
        min_size=1,
        max_size=40,
    )
)
def test_the_request_limiter_matches_a_one_second_window_count(gap_list):
    """요청 한도 장치가 요청을 낸 때가 '1초 창의 개수 세기' 기준과 맞는다.

    다르게 둔 곳: 막힌 요청을 낼 수 있는 때는 하한의 "바로 뒤"라서 가장 이른 때가 없다. 앱은 하한에
    `MARGIN`(0.01초)을 더한 때에 낸다. 그래서 막힌 요청은 하한보다 뒤이고 하한 + 0.01초 이하이며,
    막히지 않은 요청은 온 때에 그대로 낸다.
    """
    clock = FakeClock()
    limiter = RequestLimiter(clock)
    sent: list[float] = []
    for gap in gap_list:
        clock.advance(gap)
        arrived = clock.monotonic()
        actual = limiter.acquire()
        bound, free = reference_bound(sent, arrived)
        if free:
            assert actual == arrived
        else:
            assert bound < actual <= bound + 0.01 + 1e-9
        sent.append(actual)


# --- 웹후크 주소 꼴 -------------------------------------------------------------------------------

DIGITS = "0123456789"
TOKEN_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz" + DIGITS + "-_.")
HOSTS = {
    lead + host for host in ("discord.com", "discordapp.com") for lead in ("", "canary.", "ptb.")
}


def reference_is_webhook_url(text: str) -> bool:
    """명세 3.9의 조건을 글자 단위로 하나씩 본다(정규식을 쓰지 않는다).

    다르게 둔 곳: 앞뒤 빈칸은 앱과 같이 `str.strip()`으로 지운다(유니코드 빈칸 포함). 숫자는 ASCII
    `0`~`9`만 숫자로 본다(`str.isdigit()`은 다른 숫자도 받으므로 쓰지 않는다).
    """
    value = text.strip()
    scheme = "https:" + "//"
    if not value.startswith(scheme):
        return False
    host, _, path = value[len(scheme) :].partition("/")
    if host not in HOSTS:
        return False
    parts = path.split("/")
    if len(parts) == 5:
        api, version, hooks, number, token = parts
        if not (len(version) > 1 and version[0] == "v" and all(c in DIGITS for c in version[1:])):
            return False
    elif len(parts) == 4:
        api, hooks, number, token = parts
    else:
        return False
    return (
        api == "api"
        and hooks == "webhooks"
        and 17 <= len(number) <= 20
        and all(c in DIGITS for c in number)
        and len(token) >= 60
        and all(c in TOKEN_CHARS for c in token)
    )


@st.composite
def address_like(draw: st.DrawFn) -> str:
    """맞는 주소와 한 군데씩 어긋난 주소, 아무 글을 섞어 만든다(실행 중에 이어 붙인다)."""
    scheme = draw(
        st.sampled_from(["https:" + "//", "http:" + "//", "https:/", "HTTPS:" + "//", ""])
    )
    host = draw(st.sampled_from([*sorted(HOSTS), "discord.org", "xdiscord.com", "Discord.com"]))
    version = draw(st.sampled_from(["", "v10/", "v/", "va/", "v1x/", "v٣/"]))
    hooks = draw(st.sampled_from(["webhooks", "webhook", "WEBHOOKS"]))
    number = draw(st.text(DIGITS + "a٣", min_size=15, max_size=22))
    token = draw(st.text("Ab9-_.~ /?é", min_size=55, max_size=70))
    lead = draw(st.sampled_from(["", " ", "\t", "　", "x"]))
    tail = draw(st.sampled_from(["", " ", "\n", "/", "?", "#"]))
    built = lead + scheme + host + "/api/" + version + hooks + "/" + number + "/" + token + tail
    return draw(st.one_of(st.just(built), st.text(max_size=120)))


@given(address_like())
def test_the_webhook_url_check_matches_a_step_by_step_reading_of_the_rules(text):
    """`is_webhook_url`의 답이 명세의 조건을 하나씩 본 기준과 같다."""
    assert is_webhook_url(text) == reference_is_webhook_url(text)


# --- 오래된 알림 지우기 ---------------------------------------------------------------------------

NOW = datetime(2026, 3, 1, 12, 0, 30, 500_000, tzinfo=UTC)


@given(
    st.lists(st.integers(-3600, 100 * 24 * 3600), min_size=1, max_size=30),
    st.sampled_from([7, 30, 90]),
    st.integers(0, 999_999),
)
def test_old_alert_deletion_matches_the_retention_definition(age_seconds, days, micro):
    """지운 알림이 '생긴 때 + 날 수 × 24시간 ≤ 지금'의 기준이 고른 것과 같다.

    다르게 둔 곳: 표의 생긴 때는 초까지(초 아래는 버림) 적히므로(M1 명세 3.7), 기준도 적힌 초 단위의
    생긴 때로 잰다.
    """
    conn = sqlite3.connect(":memory:")
    migrate(conn)
    created: dict[int, datetime] = {}
    for age in age_seconds:
        when = NOW - timedelta(seconds=age, microseconds=micro)
        alert_id = insert_alert(
            conn,
            created_at=when,
            mode=Mode.PAPER,
            category=AlertCategory.ERROR,
            code="A-1",
            coin=None,
            text="만든 알림",
            detail=None,
            seen=False,
            discord_state="no_address",
        )
        created[alert_id] = when.replace(microsecond=0)
    delete_expired(conn, retention_days=days, now=NOW)
    left = {row[0] for row in conn.execute("SELECT id FROM alerts")}
    expected = {i for i, when in created.items() if when + timedelta(days=days) > NOW}
    assert left == expected
