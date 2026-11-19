"""`logs/startup-errors.jsonl`: 창을 띄우기 전에 난 오류(M1 명세 3.9).

한 줄이 UTF-8 JSON 객체 하나다. 키는 `kind`(`"startup"`, `"memory"`), `created_utc`, `mode`,
`category`, `code`, `coin`, `text`, `stage`, `error_name`, `content`, `after`다. `"memory"` 줄은
`id`(음수), `seen`(참·거짓), `discord_state`(3.7의 값, 아직 결과가 없으면 `null`)도 담는다. 글은
모두 `redact`를 거친 것이다.

`"startup"` 줄(19번)은 다음에 켤 때 알림(`discord_state` `"local_only"`)으로 옮긴다. `"memory"`
줄(27번)은 표에 쓰지 못해 메모리에 둔 알림이다. 그 알림의 보았다는 표시와 디스코드 결과를 줄에
고쳐 쓰고, 지우면 줄을 뺀다. 다음에 켤 때 줄에 적힌 표시대로 알림으로 옮기고 다시 보내지 않는다.
"""

import json
import logging
import os
import sqlite3
from collections.abc import Callable
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

from .memory_alerts import MemoryAlert

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


def memory_line(alert: MemoryAlert) -> dict[str, object]:
    """표에 쓰지 못해 메모리에 둔 알림의 줄. 열한 키에 `id`, `seen`, `discord_state`를 더한다."""
    detail = alert.detail
    return {
        "kind": KIND_MEMORY,
        "created_utc": format_utc(alert.created_at),
        "mode": alert.mode.value,
        "category": alert.category.value,
        "code": alert.code,
        "coin": alert.coin,
        "text": redact(alert.text),
        "stage": None if detail is None else redact(detail.stage),
        "error_name": None if detail is None else redact(detail.error_name),
        "content": None if detail is None else redact(detail.content),
        "after": None if detail is None else redact(detail.after),
        "id": alert.id,
        "seen": alert.seen,
        "discord_state": None if alert.discord_state is None else alert.discord_state.value,
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
    seen: bool = False
    discord_state: DiscordState | None = None


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
    kind = _text(data["kind"], "kind")
    seen, state = False, None
    if kind == KIND_MEMORY:
        seen, state = _memory_fields(data)
    return _Parsed(
        raw,
        kind,
        parse_utc(_text(data["created_utc"], "created_utc")),
        Mode(_text(data["mode"], "mode")),
        AlertCategory(_text(data["category"], "category")),
        _text(data["code"], "code"),
        coin,
        _text(data["text"], "text"),
        detail,
        seen,
        state,
    )


def _memory_fields(data: dict[str, object]) -> tuple[bool, DiscordState | None]:
    """`"memory"` 줄의 `id`(음수), `seen`, `discord_state`. 맞지 않으면 `ValueError`."""
    alert_id, seen, state = data.get("id"), data.get("seen"), data.get("discord_state")
    if isinstance(alert_id, bool) or not isinstance(alert_id, int) or alert_id >= 0:
        raise ValueError("id")
    if not isinstance(seen, bool):
        raise ValueError("seen")
    if state is None:
        return seen, None
    return seen, DiscordState(_text(state, "discord_state"))


def move_to_alerts(
    conn: sqlite3.Connection,
    logs_dir: Path,
    settle: Callable[[AlertCategory], DiscordState] | None = None,
) -> int:
    """줄을 알림으로 옮기고 파일에서 뺀다. 줄에 적힌 생긴 때를 그대로 쓴다.

    - `"startup"` 줄: `discord_state`는 `"local_only"`다(디스코드로 보내지 않는다).
    - `"memory"` 줄(`settle`을 준 때만): 적힌 `seen`과 `discord_state`대로 둔다(다시 보내지
      않는다). 결과가 적혀 있지 않으면(`null`이나 `"not_sent"`) `settle(분류)`가 정한 그 분류의
      주소 상태(`"failed"`나 `"no_address"`)로 쓴다. `settle`이 없으면 `"memory"` 줄은 파일에
      그대로 둔다.

    옮긴 알림의 수를 돌려준다. 읽지 못하는 줄은 건너뛰고 로그에 남긴다. 표에 쓰지 못한 줄은
    파일에 남긴다. 파일이 없거나 읽지 못하면 아무것도 하지 않는다.
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
        if parsed.kind == KIND_STARTUP:
            seen, state = False, DiscordState.LOCAL_ONLY
        elif parsed.kind == KIND_MEMORY and settle is not None:
            seen = parsed.seen
            recorded = parsed.discord_state
            if recorded is None or recorded is DiscordState.NOT_SENT:
                state = settle(parsed.category)
            else:
                state = recorded
        else:
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
                seen=seen,
                discord_state=state,
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


def _same_memory_line(raw: str, alert: MemoryAlert) -> bool:
    """이 실행의 메모리 알림의 줄인지. 앞 실행에서 남은 줄과 번호가 겹쳐도 가른다."""
    try:
        data = json.loads(raw)
    except ValueError:
        return False
    return (
        isinstance(data, dict)
        and data.get("kind") == KIND_MEMORY
        and data.get("id") == alert.id
        and data.get("created_utc") == format_utc(alert.created_at)
        and data.get("code") == alert.code
    )


def _rewrite(logs_dir: Path, alert: MemoryAlert, replacement: dict[str, object] | None) -> bool:
    """그 메모리 알림의 줄을 `replacement`로 바꾸고, `None`이면 뺀다. 다른 줄은 그대로 둔다."""
    path = logs_dir / FILE_NAME
    try:
        content = path.read_text(encoding="utf-8")
        lines = content.splitlines()
        out: list[str] = []
        for raw in lines:
            if not _same_memory_line(raw, alert):
                out.append(raw)
            elif replacement is not None:
                out.append(json.dumps(replacement, ensure_ascii=False))
        temp = path.with_name(FILE_NAME + ".tmp")
        temp.write_text("".join(f"{raw}\n" for raw in out), encoding="utf-8", newline="\n")
        os.replace(temp, path)
    except (OSError, UnicodeError) as exc:
        log.warning("startup-errors.jsonl을 고치지 못했습니다: %s", type(exc).__name__)
        return False
    return True


def update_memory_line(logs_dir: Path, alert: MemoryAlert) -> bool:
    """메모리 알림의 `seen`과 `discord_state`를 그 줄에 고쳐 쓴다. 쓰지 못하면 거짓."""
    return _rewrite(logs_dir, alert, memory_line(alert))


def remove_memory_line(logs_dir: Path, alert: MemoryAlert) -> bool:
    """지운 메모리 알림의 줄을 뺀다. 쓰지 못하면 거짓."""
    return _rewrite(logs_dir, alert, None)
