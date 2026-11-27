"""표 `settings`의 읽고 쓰기(M1 명세 3.7). 키는 이 파일 안에서만 안다.

키: `coins`(마켓 코드의 JSON 배열), `retention_days`(`"7"`, `"30"`, `"90"`),
`candle_unit`(`CandleUnit`의 값), `candle_count`(`"100"`, `"200"`, `"500"`),
`first_setup_level`(1단계 보완 전에 마치면 `"1"`, 1단계 보완에서 마치면 `"2"`),
`fetched_at:{마켓 코드}`(시각 글), `ai_model:{칸}`과 `ai_effort:{칸}`(`AiSlot` 값마다. 모델은
`CLAUDE_MODELS`의 하나, 노력은 `Effort` 값. M2 명세 3.10), `autostart`(`"on"`이나 `"off"`.
없으면 꺼짐. M1 명세 3.16.3), 3단계의 매매 칸(M3 명세 3.11.1: `budget`, `coin_ratio:{마켓 코드}`,
`first_ratio`, `mr_first_ratio`, `add_ratio`, `tolerance`, `stop_ratio`, `daily_loss_limit`,
`order_interval_s`, `trade_method`, `pause_stop_loss`, `mr_sell_unknown_fee`(`"yes"`/`"no"`),
`mr_tolerable_loss`, `paper_funds`, `backup_days`). `first_setup_level`은 3단계에서 처음 설정을
마치면 `"3"`이고 `"3"`은 내려가지 않는다. 5단계(M5 명세 3.9): `trade_mode`(`paper`/`live`. 없으면
`paper`), `upbit_key_created`(`YYYY-MM-DD`), `live_rounds_n`(10, 30, 100, 200. 없으면 30),
실거래 설정 셋(`live_budget`, `live_coin_ratio:{마켓 코드}`, `live_daily_loss_limit`. 없으면 비어
있음).
"""

import json
import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Final

from coin_report_trader.core import (
    CANDLE_COUNTS,
    CLAUDE_MODELS,
    AiChoice,
    AiSlot,
    CandleUnit,
    Effort,
    LiveSettings,
    Mode,
    PauseStopLoss,
    SettingsView,
    StorageError,
    TradeMethod,
    TradeSettings,
)
from coin_report_trader.storage.database import transaction, translate_errors
from coin_report_trader.storage.times import format_utc, parse_utc

KEY_COINS: Final = "coins"
KEY_RETENTION_DAYS: Final = "retention_days"
KEY_CANDLE_UNIT: Final = "candle_unit"
KEY_CANDLE_COUNT: Final = "candle_count"
KEY_FIRST_SETUP_LEVEL: Final = "first_setup_level"
KEY_FETCHED_AT_PREFIX: Final = "fetched_at:"
KEY_AI_MODEL_PREFIX: Final = "ai_model:"
KEY_AI_EFFORT_PREFIX: Final = "ai_effort:"
KEY_AUTOSTART: Final = "autostart"
KEY_BUDGET: Final = "budget"
KEY_COIN_RATIO_PREFIX: Final = "coin_ratio:"
KEY_FIRST_RATIO: Final = "first_ratio"
KEY_MR_FIRST_RATIO: Final = "mr_first_ratio"
KEY_ADD_RATIO: Final = "add_ratio"
KEY_TOLERANCE: Final = "tolerance"
KEY_STOP_RATIO: Final = "stop_ratio"
KEY_DAILY_LOSS_LIMIT: Final = "daily_loss_limit"
KEY_ORDER_INTERVAL_S: Final = "order_interval_s"
KEY_TRADE_METHOD: Final = "trade_method"
KEY_PAUSE_STOP_LOSS: Final = "pause_stop_loss"
KEY_MR_SELL_UNKNOWN_FEE: Final = "mr_sell_unknown_fee"
KEY_MR_TOLERABLE_LOSS: Final = "mr_tolerable_loss"
KEY_PAPER_FUNDS: Final = "paper_funds"
KEY_BACKUP_DAYS: Final = "backup_days"
KEY_TRADE_MODE: Final = "trade_mode"
KEY_UPBIT_KEY_CREATED: Final = "upbit_key_created"
KEY_LIVE_ROUNDS_N: Final = "live_rounds_n"
KEY_LIVE_BUDGET: Final = "live_budget"
KEY_LIVE_COIN_RATIO_PREFIX: Final = "live_coin_ratio:"
KEY_LIVE_DAILY_LOSS_LIMIT: Final = "live_daily_loss_limit"
LIVE_ROUNDS_CHOICES: Final = (10, 30, 100, 200)
LIVE_ROUNDS_DEFAULT: Final = 30
YES: Final = "yes"
NO: Final = "no"
_TRADE_KEYS: Final = (
    KEY_BUDGET,
    KEY_FIRST_RATIO,
    KEY_MR_FIRST_RATIO,
    KEY_ADD_RATIO,
    KEY_TOLERANCE,
    KEY_STOP_RATIO,
    KEY_DAILY_LOSS_LIMIT,
    KEY_ORDER_INTERVAL_S,
    KEY_TRADE_METHOD,
    KEY_PAUSE_STOP_LOSS,
    KEY_MR_SELL_UNKNOWN_FEE,
    KEY_MR_TOLERABLE_LOSS,
    KEY_PAPER_FUNDS,
    KEY_BACKUP_DAYS,
)
AUTOSTART_ON: Final = "on"
AUTOSTART_OFF: Final = "off"
_AI_KEYS: Final = tuple(
    key
    for slot in AiSlot
    for key in (KEY_AI_MODEL_PREFIX + slot.value, KEY_AI_EFFORT_PREFIX + slot.value)
)

RETENTION_CHOICES: Final = (7, 30, 90)
FIRST_SETUP_LEVEL_M1: Final = "1"
FIRST_SETUP_LEVEL_STAGE1_FIX: Final = "2"
FIRST_SETUP_LEVEL_STAGE3: Final = "3"

_UPSERT = (
    "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = ?"
)


def _put(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(_UPSERT, (key, value, value))


def read_settings(conn: sqlite3.Connection) -> SettingsView:
    """저장된 `coins`, `retention_days`, `candle_unit`, `candle_count`, `first_setup_level`을
    `SettingsView`로 읽는다.

    값이 없는 키는 기본값(코인 없음, 보관 기간·봉 단위·봉 개수 `None`, 처음 설정 안 마침)이다.
    `first_setup_done`은 `first_setup_level`이 있고 `candle_unit`과 `candle_count`가 모두 저장되어
    있을 때만 참이다(3.7. 1단계 보완 전에 마친 기록은 `"1"`만 있어 거짓이다).
    저장된 글이 맞지 않으면 `StorageError`다. 다만 AI의 칸(`ai_model:{칸}`, `ai_effort:{칸}`)은
    틀린 값(목록에 없는 모델이나 노력 값)이 든 칸만 저장되지 않은 것으로 보고 버린다. 모델과
    노력이 둘 다 맞는 칸만 `AiSlot`의 차례로 `ai_choices`에 담고, AI 키 때문에 `StorageError`를
    내지 않는다.
    """
    keys = (
        KEY_COINS,
        KEY_RETENTION_DAYS,
        KEY_CANDLE_UNIT,
        KEY_CANDLE_COUNT,
        KEY_FIRST_SETUP_LEVEL,
        KEY_AUTOSTART,
        *_AI_KEYS,
        *_TRADE_KEYS,
        KEY_LIVE_BUDGET,
        KEY_LIVE_DAILY_LOSS_LIMIT,
    )
    with translate_errors("설정 읽기"):
        rows = dict(
            conn.execute(
                f"SELECT key, value FROM settings WHERE key IN ({', '.join('?' * len(keys))})",
                keys,
            ).fetchall()
        )
        ratios = _read_ratios(conn)
        live_ratios = _read_ratios(conn, KEY_LIVE_COIN_RATIO_PREFIX)
    unit = _decode_unit(rows.get(KEY_CANDLE_UNIT))
    count = _decode_count(rows.get(KEY_CANDLE_COUNT))
    level_present = _decode_level(rows.get(KEY_FIRST_SETUP_LEVEL))
    coins = _decode_coins(rows.get(KEY_COINS))
    return SettingsView(
        coins=coins,
        retention_days=_decode_retention(rows.get(KEY_RETENTION_DAYS)),
        first_setup_done=level_present and unit is not None and count is not None,
        candle_unit=unit,
        candle_count=count,
        ai_choices=_decode_ai_choices(rows),
        autostart=_decode_autostart(rows.get(KEY_AUTOSTART)),
        trade=_decode_trade(rows, ratios, coins),
        live=_decode_live(rows, live_ratios),
    )


def write_settings(
    conn: sqlite3.Connection,
    settings: SettingsView,
    *,
    drop_other_unit_candles: bool = False,
    clear_fetched_at: bool = False,
    autostart: bool | None = None,
    created_at: datetime | None = None,
) -> None:
    """`SettingsView`의 다섯 값을 한 트랜잭션으로 쓴다.

    `retention_days`, `candle_unit`, `candle_count`가 `None`이면 그 키를 지운다.
    `first_setup_done`이 거짓이면 `first_setup_level`을 지우고, 참이면 봉 단위와 봉 개수가 모두
    있을 때(1단계 보완에서 마침) `"2"`, 아니면(1단계 보완 전에 마침) `"1"`을 쓴다. 보관 기간이
    7, 30, 90이 아니거나 봉 개수가 100, 200, 500이 아니면 `ValueError`다.

    `drop_other_unit_candles`가 참이면 같은 트랜잭션에서 `settings.candle_unit`이 아닌 단위의 봉을
    지운다(봉 단위가 바뀔 때. 3.5.4). `clear_fetched_at`이 참이면 모든 코인의 `fetched_at`도
    같은 트랜잭션에서 지운다. 둘 다 봉 단위가 있을 때만 뜻이 있고, 단위가 `None`이면 봉을 지우지
    않는다.

    `ai_choices`가 빈 튜플이면 ai 키를 쓰지도 지우지도 않는다(M2 명세 3.10). 네 칸(`AiSlot` 넷이
    하나씩)이 아닌 묶음은 버리고 다른 값만 저장한다(`ValueError`를 내지 않는다). 네 칸이면 여덟
    키를 같은 트랜잭션에서 쓴다.

    `autostart`가 `None`이 아니면 키 `autostart`를 같은 트랜잭션에서 쓴다(`"on"`이나 `"off"`).
    `None`이면 저장된 값을 그대로 둔다(`settings.autostart`는 쓰지 않는다. M1 명세 3.16.3).

    3단계(M3 명세 3.11.1): `settings.trade`가 있으면 매매 칸의 키를 같은 트랜잭션에서 쓴다(첫 매수
    비율 둘은 `None`이면 지운다). `None`이면 매매 칸의 키를 건드리지 않는다. 고른 코인 목록에 없는
    코인의 `coin_ratio:{마켓 코드}`는 저장할 때마다 지운다. 처음 설정을 마친 기록은 `trade`가 있거나
    3단계 키가 모두 저장되어 있거나 이미 `"3"`이면 `first_setup_level`을 `"3"`으로 쓴다(`"3"`은
    내려가지 않는다). 처음 `"3"`이 될 때 같은 트랜잭션에서 가상 계정(모드 `paper`, 가상 자금 =
    가상 원화)을 만든다(3.11.3. 때는 `created_at`, 없으면 지금). `"3"`이 된 뒤에는 `paper_funds`를
    고쳐 쓰지 않는다(3.7.1).

    5단계(M5 명세 3.7.5, 3.9): `settings.live`가 있으면 실거래 설정의 키를 같은 트랜잭션에서
    쓴다(예산과 하루 손실 한도는 `None`이면 지운다). `None`이면 건드리지 않는다. 고른 코인 목록에
    없는 코인의 `live_coin_ratio:{마켓 코드}`는 저장할 때마다 지운다.
    """
    if settings.retention_days is not None and settings.retention_days not in RETENTION_CHOICES:
        raise ValueError(f"보관 기간은 {RETENTION_CHOICES} 가운데 하나여야 합니다")
    if settings.candle_count is not None and settings.candle_count not in CANDLE_COUNTS:
        raise ValueError(f"봉 개수는 {CANDLE_COUNTS} 가운데 하나여야 합니다")
    unit = None if settings.candle_unit is None else CandleUnit(settings.candle_unit)
    coins = json.dumps(list(settings.coins), ensure_ascii=False)
    with translate_errors("설정 저장"), transaction(conn):
        _put(conn, KEY_COINS, coins)
        if settings.retention_days is None:
            conn.execute("DELETE FROM settings WHERE key = ?", (KEY_RETENTION_DAYS,))
        else:
            _put(conn, KEY_RETENTION_DAYS, str(settings.retention_days))
        if unit is None:
            conn.execute("DELETE FROM settings WHERE key = ?", (KEY_CANDLE_UNIT,))
        else:
            _put(conn, KEY_CANDLE_UNIT, unit.value)
        if settings.candle_count is None:
            conn.execute("DELETE FROM settings WHERE key = ?", (KEY_CANDLE_COUNT,))
        else:
            _put(conn, KEY_CANDLE_COUNT, str(settings.candle_count))
        stored = _stored(conn, KEY_FIRST_SETUP_LEVEL)
        started = stored == FIRST_SETUP_LEVEL_STAGE3
        trade = settings.trade
        if trade is not None:
            _write_trade(conn, trade, keep_funds=started)
        _drop_other_ratios(conn, settings.coins)
        if settings.live is not None:
            _write_live(conn, settings.live)
        _drop_other_ratios(conn, settings.coins, KEY_LIVE_COIN_RATIO_PREFIX)
        if settings.first_setup_done:
            both = unit is not None and settings.candle_count is not None
            level = FIRST_SETUP_LEVEL_STAGE1_FIX if both else FIRST_SETUP_LEVEL_M1
            if started or trade is not None or _stage3_keys_stored(conn, settings.coins):
                level = FIRST_SETUP_LEVEL_STAGE3
            _put(conn, KEY_FIRST_SETUP_LEVEL, level)
            if level == FIRST_SETUP_LEVEL_STAGE3 and not started:
                _create_paper_account(conn, created_at or datetime.now(UTC))
        else:
            conn.execute("DELETE FROM settings WHERE key = ?", (KEY_FIRST_SETUP_LEVEL,))
        for key, value in _encode_ai_choices(settings.ai_choices):
            _put(conn, key, value)
        if autostart is not None:
            _put(conn, KEY_AUTOSTART, AUTOSTART_ON if autostart else AUTOSTART_OFF)
        if drop_other_unit_candles and unit is not None:
            conn.execute("DELETE FROM candles WHERE unit <> ?", (unit.value,))
        if clear_fetched_at:
            conn.execute(
                "DELETE FROM settings WHERE substr(key, 1, ?) = ?",
                (len(KEY_FETCHED_AT_PREFIX), KEY_FETCHED_AT_PREFIX),
            )


def read_first_setup_level(conn: sqlite3.Connection) -> str | None:
    """저장된 `first_setup_level`의 글(`"1"`, `"2"`, `"3"`). 없으면 `None`(M3 명세 3.11.3)."""
    with translate_errors("설정 읽기"):
        return _stored(conn, KEY_FIRST_SETUP_LEVEL)


def read_trade_mode(conn: sqlite3.Connection) -> Mode:
    """저장된 모드. 없으면 `Mode.PAPER`다(M5 명세 3.9)."""
    with translate_errors("설정 읽기"):
        text = _stored(conn, KEY_TRADE_MODE)
    if text is None:
        return Mode.PAPER
    try:
        return Mode(text)
    except ValueError as exc:
        raise StorageError("저장된 모드가 맞지 않습니다") from exc


def write_trade_mode(conn: sqlite3.Connection, mode: Mode) -> None:
    """모드(`paper`/`live`)를 쓴다. 매매 DB 쓰기다(M5 명세 3.9)."""
    with translate_errors("설정 저장"), transaction(conn):
        _put(conn, KEY_TRADE_MODE, Mode(mode).value)


def read_key_created(conn: sqlite3.Connection) -> date | None:
    """업비트 키를 만든 날(`upbit_key_created`). 없으면 `None`. 글이 맞지 않으면 `StorageError`."""
    with translate_errors("설정 읽기"):
        text = _stored(conn, KEY_UPBIT_KEY_CREATED)
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise StorageError("저장된 키를 만든 날이 맞지 않습니다") from exc


def write_key_created(conn: sqlite3.Connection, created: date) -> None:
    """키를 만든 날을 `YYYY-MM-DD`로 쓴다."""
    with translate_errors("설정 저장"), transaction(conn):
        _put(conn, KEY_UPBIT_KEY_CREATED, created.isoformat())


def delete_key_created(conn: sqlite3.Connection) -> None:
    """키를 지울 때 `upbit_key_created`도 지운다(M5 명세 3.8)."""
    with translate_errors("설정 저장"), transaction(conn):
        conn.execute("DELETE FROM settings WHERE key = ?", (KEY_UPBIT_KEY_CREATED,))


def read_live_rounds_n(conn: sqlite3.Connection) -> int:
    """실거래 전 모의 회차 수 N(10, 30, 100, 200). 없으면 30이다(M5 명세 3.9)."""
    with translate_errors("설정 읽기"):
        text = _stored(conn, KEY_LIVE_ROUNDS_N)
    if text is None:
        return LIVE_ROUNDS_DEFAULT
    if text not in {str(n) for n in LIVE_ROUNDS_CHOICES}:
        raise StorageError("저장된 모의 회차 수가 맞지 않습니다")
    return int(text)


def write_live_rounds_n(conn: sqlite3.Connection, n: int) -> None:
    """N을 쓴다. 10, 30, 100, 200이 아니면 `ValueError`다."""
    if n not in LIVE_ROUNDS_CHOICES:
        raise ValueError(f"N은 {LIVE_ROUNDS_CHOICES} 가운데 하나여야 합니다")
    with translate_errors("설정 저장"), transaction(conn):
        _put(conn, KEY_LIVE_ROUNDS_N, str(n))


def read_fetched_at(conn: sqlite3.Connection, market: str) -> datetime | None:
    """그 코인의 마지막으로 성공한 받기가 끝난 때. 없으면 `None`."""
    with translate_errors("받은 때 읽기"):
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (KEY_FETCHED_AT_PREFIX + market,)
        ).fetchone()
    return None if row is None else parse_utc(row[0])


def write_fetched_at(conn: sqlite3.Connection, market: str, moment: datetime) -> None:
    """그 코인의 `fetched_at`을 쓴다(같은 코인은 덮어쓴다)."""
    text = format_utc(moment)
    with translate_errors("받은 때 저장"), transaction(conn):
        _put(conn, KEY_FETCHED_AT_PREFIX + market, text)


def delete_all_fetched_at(conn: sqlite3.Connection) -> int:
    """모든 코인의 `fetched_at`을 지우고 지운 수를 돌려준다(3.5.4)."""
    with translate_errors("받은 때 지우기"), transaction(conn):
        return conn.execute(
            "DELETE FROM settings WHERE substr(key, 1, ?) = ?",
            (len(KEY_FETCHED_AT_PREFIX), KEY_FETCHED_AT_PREFIX),
        ).rowcount


def _encode_ai_choices(choices: tuple[AiChoice, ...]) -> tuple[tuple[str, str], ...]:
    """네 칸이 하나씩 있는 묶음만 (키, 값) 여덟 쌍으로 바꾼다. 아니면 쌍이 없다."""
    if len(choices) != len(AiSlot) or {choice.slot for choice in choices} != set(AiSlot):
        return ()
    pairs: list[tuple[str, str]] = []
    for choice in choices:
        slot = AiSlot(choice.slot)
        pairs.append((KEY_AI_MODEL_PREFIX + slot.value, str(choice.model)))
        pairs.append((KEY_AI_EFFORT_PREFIX + slot.value, Effort(choice.effort).value))
    return tuple(pairs)


def _decode_ai_choices(rows: dict[str, str]) -> tuple[AiChoice, ...]:
    """모델과 노력이 둘 다 맞는 칸만 `AiSlot`의 차례로 담는다. 틀린 칸은 버린다."""
    choices: list[AiChoice] = []
    for slot in AiSlot:
        model = rows.get(KEY_AI_MODEL_PREFIX + slot.value)
        effort_text = rows.get(KEY_AI_EFFORT_PREFIX + slot.value)
        if model not in CLAUDE_MODELS or effort_text is None:
            continue
        try:
            effort = Effort(effort_text)
        except ValueError:
            continue
        choices.append(AiChoice(slot, model, effort))
    return tuple(choices)


def _decode_autostart(text: str | None) -> bool:
    """`"on"`만 켜짐이다. 없거나 다른 글이면 꺼짐(처음 값)이다."""
    return text == AUTOSTART_ON


def _stored(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row[0])


def _read_ratios(conn: sqlite3.Connection, prefix: str = KEY_COIN_RATIO_PREFIX) -> dict[str, str]:
    """`coin_ratio:{마켓 코드}`(`prefix`로 `live_coin_ratio:`)의 글(마켓 코드에서 글)."""
    size = len(prefix)
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE substr(key, 1, ?) = ?",
        (size, prefix),
    ).fetchall()
    return {str(key)[size:]: str(value) for key, value in rows}


def _number(text: str | None) -> Decimal | None:
    if text is None:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _integer(text: str | None) -> int | None:
    if text is None or not text.isascii() or not text.isdigit():
        return None
    return int(text)


def _decode_trade(
    rows: dict[str, str], ratios: dict[str, str], coins: tuple[str, ...]
) -> TradeSettings | None:
    """저장된 매매 칸이 모두 있고 글이 맞을 때만 `TradeSettings`다. 아니면 `None`(3단계 전의
    기록, 처음 설정 전, 글이 틀린 칸이 있음). 매매 칸 때문에 `StorageError`를 내지 않는다(켤 때
    설정 전체를 읽지 못한 것이 되지 않게. AI 칸과 같은 꼴)."""
    try:
        method = TradeMethod(rows.get(KEY_TRADE_METHOD, ""))
        pause = PauseStopLoss(rows.get(KEY_PAUSE_STOP_LOSS, ""))
    except ValueError:
        return None
    fee = rows.get(KEY_MR_SELL_UNKNOWN_FEE)
    if fee not in (YES, NO) or not coins:
        return None
    coin_ratios: dict[str, Decimal] = {}
    for coin in coins:
        ratio = _number(ratios.get(coin))
        if ratio is None:
            return None
        coin_ratios[coin] = ratio
    numbers = {
        key: _number(rows.get(key))
        for key in (
            KEY_BUDGET, KEY_ADD_RATIO, KEY_TOLERANCE, KEY_STOP_RATIO, KEY_DAILY_LOSS_LIMIT,
            KEY_MR_TOLERABLE_LOSS, KEY_PAPER_FUNDS,
        )
    }  # fmt: skip
    interval = _integer(rows.get(KEY_ORDER_INTERVAL_S))
    backup_days = _integer(rows.get(KEY_BACKUP_DAYS))
    first_ratio = _number(rows.get(KEY_FIRST_RATIO))
    mr_first_ratio = _number(rows.get(KEY_MR_FIRST_RATIO))
    chosen_first = first_ratio if method is TradeMethod.TREND else mr_first_ratio
    if any(value is None for value in numbers.values()) or None in (
        interval,
        backup_days,
        chosen_first,
    ):
        return None
    return TradeSettings(
        budget=numbers[KEY_BUDGET],  # type: ignore[arg-type]
        coin_ratios=coin_ratios,
        first_ratio=first_ratio,
        mr_first_ratio=mr_first_ratio,
        add_ratio=numbers[KEY_ADD_RATIO],  # type: ignore[arg-type]
        tolerance=numbers[KEY_TOLERANCE],  # type: ignore[arg-type]
        stop_ratio=numbers[KEY_STOP_RATIO],  # type: ignore[arg-type]
        daily_loss_limit=numbers[KEY_DAILY_LOSS_LIMIT],  # type: ignore[arg-type]
        order_interval_s=interval,  # type: ignore[arg-type]
        method=method,
        pause_stop_loss=pause,
        mr_sell_unknown_fee=fee == YES,
        mr_tolerable_loss=numbers[KEY_MR_TOLERABLE_LOSS],  # type: ignore[arg-type]
        paper_funds=numbers[KEY_PAPER_FUNDS],  # type: ignore[arg-type]
        backup_days=backup_days,  # type: ignore[arg-type]
    )


def _decode_live(rows: dict[str, str], ratios: dict[str, str]) -> LiveSettings | None:
    """저장된 실거래 설정. 셋이 모두 없으면 `None`(3단계 전의 기록과 아직 넣지 않은 기록). 글이
    맞지 않은 값은 없는 값으로 본다(설정 전체를 읽지 못한 것이 되지 않게)."""
    budget = _number(rows.get(KEY_LIVE_BUDGET))
    limit = _number(rows.get(KEY_LIVE_DAILY_LOSS_LIMIT))
    coin_ratios = {
        market: value for market, text in ratios.items() if (value := _number(text)) is not None
    }
    if budget is None and limit is None and not coin_ratios:
        return None
    return LiveSettings(budget=budget, coin_ratios=coin_ratios, daily_loss_limit=limit)


def _write_live(conn: sqlite3.Connection, live: LiveSettings) -> None:
    """실거래 설정의 키를 쓴다(예산과 하루 손실 한도는 `None`이면 지운다). 저장된 비율 가운데
    `live.coin_ratios`에 없는 코인의 것은 지운다."""
    for key, value in (
        (KEY_LIVE_BUDGET, live.budget),
        (KEY_LIVE_DAILY_LOSS_LIMIT, live.daily_loss_limit),
    ):
        if value is None:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        else:
            _put(conn, key, str(value))
    for market in _read_ratios(conn, KEY_LIVE_COIN_RATIO_PREFIX):
        if market not in live.coin_ratios:
            conn.execute(
                "DELETE FROM settings WHERE key = ?", (KEY_LIVE_COIN_RATIO_PREFIX + market,)
            )
    for market, ratio in live.coin_ratios.items():
        _put(conn, KEY_LIVE_COIN_RATIO_PREFIX + market, str(ratio))


def _stage3_keys_stored(conn: sqlite3.Connection, coins: tuple[str, ...]) -> bool:
    """3단계의 꼭 넣는 키가 모두 저장되어 있는지(3.11.1)."""
    keys = _TRADE_KEYS
    rows = dict(
        conn.execute(
            f"SELECT key, value FROM settings WHERE key IN ({', '.join('?' * len(keys))})", keys
        ).fetchall()
    )
    return _decode_trade(rows, _read_ratios(conn), coins) is not None


def _write_trade(conn: sqlite3.Connection, trade: TradeSettings, *, keep_funds: bool) -> None:
    """매매 칸의 키를 쓴다. 수는 `Decimal`의 글, 고르기는 값, 예/아니오는 `"yes"`/`"no"`다."""
    values: dict[str, str | None] = {
        KEY_BUDGET: str(trade.budget),
        KEY_FIRST_RATIO: None if trade.first_ratio is None else str(trade.first_ratio),
        KEY_MR_FIRST_RATIO: None if trade.mr_first_ratio is None else str(trade.mr_first_ratio),
        KEY_ADD_RATIO: str(trade.add_ratio),
        KEY_TOLERANCE: str(trade.tolerance),
        KEY_STOP_RATIO: str(trade.stop_ratio),
        KEY_DAILY_LOSS_LIMIT: str(trade.daily_loss_limit),
        KEY_ORDER_INTERVAL_S: str(int(trade.order_interval_s)),
        KEY_TRADE_METHOD: TradeMethod(trade.method).value,
        KEY_PAUSE_STOP_LOSS: PauseStopLoss(trade.pause_stop_loss).value,
        KEY_MR_SELL_UNKNOWN_FEE: YES if trade.mr_sell_unknown_fee else NO,
        KEY_MR_TOLERABLE_LOSS: str(trade.mr_tolerable_loss),
        KEY_BACKUP_DAYS: str(int(trade.backup_days)),
    }
    if not keep_funds:
        values[KEY_PAPER_FUNDS] = str(trade.paper_funds)
    for key, value in values.items():
        if value is None:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        else:
            _put(conn, key, value)
    for market, ratio in trade.coin_ratios.items():
        _put(conn, KEY_COIN_RATIO_PREFIX + market, str(ratio))


def _drop_other_ratios(
    conn: sqlite3.Connection, coins: tuple[str, ...], prefix: str = KEY_COIN_RATIO_PREFIX
) -> None:
    """고른 코인 목록에 없는 코인의 `coin_ratio:{마켓 코드}`(`prefix`로 `live_coin_ratio:`)를
    지운다(M3 명세 3.11.1, M5 명세 3.9)."""
    for market in _read_ratios(conn, prefix):
        if market not in coins:
            conn.execute("DELETE FROM settings WHERE key = ?", (prefix + market,))


def _create_paper_account(conn: sqlite3.Connection, moment: datetime) -> None:
    """처음 설정을 마칠 때 한 번 만드는 가상 계정(M3 명세 3.7.1). 이미 있으면 그대로 둔다."""
    funds = _stored(conn, KEY_PAPER_FUNDS)
    if funds is None:
        raise ValueError("가상 자금이 없어 가상 계정을 만들 수 없습니다")
    conn.execute(
        "INSERT INTO paper_accounts (mode, funds, krw, locked_krw, created_utc)"
        " VALUES (?, ?, ?, ?, ?) ON CONFLICT (mode) DO NOTHING",
        (Mode.PAPER.value, funds, funds, "0", format_utc(moment)),
    )


def _decode_coins(text: str | None) -> tuple[str, ...]:
    if text is None:
        return ()
    try:
        loaded = json.loads(text)
    except ValueError as exc:
        raise StorageError("저장된 코인 목록을 읽지 못했습니다") from exc
    if not isinstance(loaded, list) or not all(isinstance(item, str) for item in loaded):
        raise StorageError("저장된 코인 목록의 꼴이 맞지 않습니다")
    return tuple(loaded)


def _decode_retention(text: str | None) -> int | None:
    if text is None:
        return None
    if text not in {str(days) for days in RETENTION_CHOICES}:
        raise StorageError("저장된 보관 기간이 맞지 않습니다")
    return int(text)


def _decode_unit(text: str | None) -> CandleUnit | None:
    if text is None:
        return None
    try:
        return CandleUnit(text)
    except ValueError as exc:
        raise StorageError("저장된 봉 단위가 맞지 않습니다") from exc


def _decode_count(text: str | None) -> int | None:
    if text is None:
        return None
    if text not in {str(count) for count in CANDLE_COUNTS}:
        raise StorageError("저장된 봉 개수가 맞지 않습니다")
    return int(text)


def _decode_level(text: str | None) -> bool:
    if text is None:
        return False
    if not text.isascii() or not text.isdigit():
        raise StorageError("저장된 처음 설정 단계가 맞지 않습니다")
    return int(text) >= 1
