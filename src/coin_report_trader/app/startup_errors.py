"""`logs/startup-errors.jsonl`: 창을 띄우기 전에 난 오류(M1 명세 3.9).

한 줄이 UTF-8 JSON 객체 하나다. 키는 `kind`(`"startup"`, `"memory"`), `created_utc`, `mode`,
`category`, `code`, `coin`, `text`, `stage`, `error_name`, `content`, `after`다(`"memory"` 줄의
`id`, `seen`, `discord_state`는 27번이 더한다). 글은 모두 `redact`를 거친 것이다.

19번은 `"startup"` 줄을 쓰고, 다음에 켤 때 알림(`discord_state` `"local_only"`)으로 옮긴다.
`"memory"` 줄은 쓰지도 옮기지도 않고 파일에 그대로 둔다(27번의 몫).
"""

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from coin_report_trader.core import AlertCategory, AlertDetail, Mode, StorageError, redact
from coin_report_trader.core.texts import (
    AFTER_SAVE_FAILED,
    AFTER_UNEXPECTED,
    CODE_SAVE_FAILED,
    CODE_UNEXPECTED,
    ERROR_STORAGE,
    STAGE_STARTUP,
    text_save_failed,
    text_unexpected,
)
from coin_report_trader.storage.alerts import insert_alert
from coin_report_trader.storage.states import DiscordState
from coin_report_trader.storage.times import format_utc, parse_utc

log = logging.getLogger(__name__)

FILE_NAME = "startup-errors.jsonl"
KIND_STARTUP = "startup"
KIND_MEMORY = "memory"
_KEYS = (
    "kind", "created_utc", "mode", "category", "code", "coin", "text", "stage", "error_name",
    "content", "after",
)  # fmt: skip


def startup_line(exc: BaseException, now: datetime) -> dict[str, object]:
    """켜는 순서 1, 2에서 난 오류의 줄. `StorageError`는 A-29, 그 밖의 예외는 A-30이다."""
    if isinstance(exc, StorageError):
        code, category, error_name = CODE_SAVE_FAILED, AlertCategory.ERROR, ERROR_STORAGE
        text, after = text_save_failed(STAGE_STARTUP, error_name), AFTER_SAVE_FAILED[STAGE_STARTUP]
    else:
        code, category, error_name = CODE_UNEXPECTED, AlertCategory.OTHER, type(exc).__name__
        text, after = text_unexpected(error_name), AFTER_UNEXPECTED
    return {
        "kind": KIND_STARTUP,
        "created_utc": format_utc(now),
        "mode": Mode.PAPER.value,
        "category": category.value,
        "code": code,
        "coin": None,
        "text": redact(text),
        "stage": STAGE_STARTUP,
        "error_name": redact(error_name),
        "content": redact(str(exc)),
        "after": redact(after),
    }


def append_line(logs_dir: Path, line: dict[str, object]) -> bool:
    """한 줄을 덧붙인다. 쓰지 못하면 거짓(로그에만 남는다)."""
    try:
        with (logs_dir / FILE_NAME).open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("startup-errors.jsonl에 쓰지 못했습니다: %s", type(exc).__name__)
        return False
    return True


@dataclass(frozen=True)
class _Parsed:
    raw: str
    kind: str
    created_at: datetime
    mode: Mode
    category: AlertCategory
    code: str
    coin: str | None
    text: str
    detail: AlertDetail | None


def _text(value: object, key: str) -> str:
    if not isinstance(value, str):
        raise ValueError(key)
    return value


def _optional(value: object, key: str) -> str | None:
    return None if value is None else _text(value, key)


def _parse(raw: str) -> _Parsed:
    data = json.loads(raw)
    if not isinstance(data, dict) or any(key not in data for key in _KEYS):
        raise ValueError("keys")
    coin = _optional(data["coin"], "coin")
    parts = [_optional(data[key], key) for key in ("stage", "error_name", "content", "after")]
    stage, error_name, content, after = parts
    detail = None
    if any(part is not None for part in parts):
        if stage is None or error_name is None or content is None or after is None:
            raise ValueError("detail")
        detail = AlertDetail(stage, coin, error_name, content, after)
    return _Parsed(
        raw,
        _text(data["kind"], "kind"),
        parse_utc(_text(data["created_utc"], "created_utc")),
        Mode(_text(data["mode"], "mode")),
        AlertCategory(_text(data["category"], "category")),
        _text(data["code"], "code"),
        coin,
        _text(data["text"], "text"),
        detail,
    )


def move_to_alerts(conn: sqlite3.Connection, logs_dir: Path) -> int:
    """`"startup"` 줄을 알림(`"local_only"`, 줄에 적힌 생긴 때)으로 옮기고 파일에서 뺀다.

    옮긴 알림의 수를 돌려준다. 읽지 못하는 줄은 건너뛰고 로그에 남긴다. 표에 쓰지 못한 줄과
    `"memory"` 줄은 파일에 남긴다. 파일이 없거나 읽지 못하면 아무것도 하지 않는다.
    """
    path = logs_dir / FILE_NAME
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return 0
    except (OSError, UnicodeError) as exc:
        log.warning("startup-errors.jsonl을 읽지 못했습니다: %s", type(exc).__name__)
        return 0
    kept: list[str] = []
    moved = 0
    for number, raw in enumerate(content.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            parsed = _parse(raw)
        except (ValueError, TypeError, StorageError):  # JSONDecodeError도 ValueError다
            log.warning("startup-errors.jsonl의 %s번째 줄을 읽지 못해 건너뜁니다", number)
            continue
        if parsed.kind != KIND_STARTUP:
            kept.append(raw)
            continue
        try:
            insert_alert(
                conn,
                created_at=parsed.created_at,
                mode=parsed.mode,
                category=parsed.category,
                code=parsed.code,
                coin=parsed.coin,
                text=parsed.text,
                detail=parsed.detail,
                seen=False,
                discord_state=DiscordState.LOCAL_ONLY,
            )
        except (StorageError, ValueError) as exc:
            log.warning("startup-errors.jsonl의 %s번째 줄을 옮기지 못했습니다: %s", number, exc)
            kept.append(raw)
            continue
        moved += 1
    try:
        path.write_text("".join(f"{raw}\n" for raw in kept), encoding="utf-8", newline="\n")
    except OSError as exc:
        log.warning("startup-errors.jsonl을 비우지 못했습니다: %s", type(exc).__name__)
    return moved
