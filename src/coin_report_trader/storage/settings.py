"""표 `settings`의 읽고 쓰기(M1 명세 3.7). 키는 이 파일 안에서만 안다.

키: `coins`(마켓 코드의 JSON 배열), `retention_days`(`"7"`, `"30"`, `"90"`),
`candle_unit`(`CandleUnit`의 값), `candle_count`(`"100"`, `"200"`, `"500"`),
`first_setup_level`(1단계 보완 전에 마치면 `"1"`, 1단계 보완에서 마치면 `"2"`),
`fetched_at:{마켓 코드}`(시각 글), `ai_model:{칸}`과 `ai_effort:{칸}`(`AiSlot` 값마다. 모델은
`CLAUDE_MODELS`의 하나, 노력은 `Effort` 값. M2 명세 3.10), `autostart`(`"on"`이나 `"off"`.
없으면 꺼짐. M1 명세 3.16.3).
"""

import json
import sqlite3
from datetime import datetime
from typing import Final

from coin_report_trader.core import (
    CANDLE_COUNTS,
    CLAUDE_MODELS,
    AiChoice,
    AiSlot,
    CandleUnit,
    Effort,
    SettingsView,
    StorageError,
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
    )
    with translate_errors("설정 읽기"):
        rows = dict(
            conn.execute(
                f"SELECT key, value FROM settings WHERE key IN ({', '.join('?' * len(keys))})",
                keys,
            ).fetchall()
        )
    unit = _decode_unit(rows.get(KEY_CANDLE_UNIT))
    count = _decode_count(rows.get(KEY_CANDLE_COUNT))
    level_present = _decode_level(rows.get(KEY_FIRST_SETUP_LEVEL))
    return SettingsView(
        coins=_decode_coins(rows.get(KEY_COINS)),
        retention_days=_decode_retention(rows.get(KEY_RETENTION_DAYS)),
        first_setup_done=level_present and unit is not None and count is not None,
        candle_unit=unit,
        candle_count=count,
        ai_choices=_decode_ai_choices(rows),
        autostart=_decode_autostart(rows.get(KEY_AUTOSTART)),
    )


def write_settings(
    conn: sqlite3.Connection,
    settings: SettingsView,
    *,
    drop_other_unit_candles: bool = False,
    clear_fetched_at: bool = False,
    autostart: bool | None = None,
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
        if settings.first_setup_done:
            both = unit is not None and settings.candle_count is not None
            level = FIRST_SETUP_LEVEL_STAGE1_FIX if both else FIRST_SETUP_LEVEL_M1
            _put(conn, KEY_FIRST_SETUP_LEVEL, level)
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
