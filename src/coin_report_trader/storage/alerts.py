"""표 `alerts`의 읽고 쓰기(M1 명세 3.7, 3.9). 언제 쓰고 지울지는 `app`이 정한다.

웹후크 주소는 어디에도 쓰지 않는다. 모든 쓰기는 하나의 트랜잭션이고, DB 오류는 `StorageError`다.
"""

import sqlite3
from collections.abc import Mapping
from datetime import datetime, timedelta
from types import MappingProxyType

from coin_report_trader.core import (
    AlertCategory,
    AlertDetail,
    AlertView,
    Mode,
    StorageError,
)
from coin_report_trader.storage.database import transaction, translate_errors
from coin_report_trader.storage.states import DiscordState
from coin_report_trader.storage.times import format_utc, parse_utc

_COLUMNS = (
    "id, created_utc, mode, category, code, coin, text, stage, error_name, content, "
    '"after", seen, discord_state, report_id'
)
_SETTLED_STATES = (DiscordState.FAILED, DiscordState.NO_ADDRESS)


def insert_alert(
    conn: sqlite3.Connection,
    *,
    created_at: datetime,
    mode: Mode,
    category: AlertCategory,
    code: str,
    coin: str | None,
    text: str,
    detail: AlertDetail | None = None,
    seen: bool = False,
    discord_state: DiscordState = DiscordState.NOT_SENT,
    report_id: int | None = None,
) -> int:
    """알림 한 줄을 모든 열을 채워 쓰고 새 `id`를 돌려준다.

    `report_id`는 A-26이 가리키는 보고서의 `reports.id`다(M2 명세 3.10). 다른 알림은 `None`이다.

    `detail`이 있으면 `stage`, `error_name`, `content`, `after`에 담고(없으면 넷 다 NULL),
    `detail.coin`은 `coin`과 같아야 한다(표에는 `coin` 열 하나뿐이다). 창을 띄우기 전의 오류를
    옮길 때는 줄에 적힌 `created_at`, `seen`, `discord_state`를 그대로 넘긴다.
    """
    if detail is not None and detail.coin != coin:
        raise ValueError("detail.coin은 coin과 같아야 합니다")
    stage, error_name, content, after = (
        (None, None, None, None)
        if detail is None
        else (detail.stage, detail.error_name, detail.content, detail.after)
    )
    row = (
        format_utc(created_at),
        Mode(mode).value,
        AlertCategory(category).value,
        code,
        coin,
        text,
        stage,
        error_name,
        content,
        after,
        1 if seen else 0,
        DiscordState(discord_state).value,
        report_id,
    )
    with translate_errors("알림 저장"), transaction(conn):
        cursor = conn.execute(
            "INSERT INTO alerts (created_utc, mode, category, code, coin, text, stage, error_name,"
            ' content, "after", seen, discord_state, report_id)'
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )
        alert_id = cursor.lastrowid
    if alert_id is None:  # INSERT 뒤에는 늘 값이 있다
        raise StorageError("알림 번호를 받지 못했습니다")
    return alert_id


def list_alerts(conn: sqlite3.Connection) -> tuple[AlertView, ...]:
    """모든 알림을 새것이 앞으로 읽는다(생긴 때가 같으면 `id`가 큰 것이 앞)."""
    with translate_errors("알림 읽기"):
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM alerts ORDER BY created_utc DESC, id DESC"
        ).fetchall()
    return tuple(_to_view(row) for row in rows)


def count_unseen(conn: sqlite3.Connection) -> Mapping[AlertCategory, int]:
    """분류마다 아직 보지 않은 알림의 수(`AlertsView.unseen`). 모든 분류가 키로 있다."""
    with translate_errors("안 본 알림 세기"):
        rows = conn.execute(
            "SELECT category, count(*) FROM alerts WHERE seen = 0 GROUP BY category"
        ).fetchall()
    counts = dict.fromkeys(AlertCategory, 0)
    for category, count in rows:
        counts[_category(category)] = count
    return MappingProxyType(counts)


def mark_seen(conn: sqlite3.Connection, alert_id: int) -> bool:
    """그 알림을 보았다고 표시한다. 그 줄이 있었으면 참이다."""
    return _change(conn, "알림 본 표시", "UPDATE alerts SET seen = 1 WHERE id = ?", (alert_id,)) > 0


def delete_alert(conn: sqlite3.Connection, alert_id: int) -> bool:
    """그 알림 한 줄을 지운다. 그 줄이 있었으면 참이다."""
    return _change(conn, "알림 지우기", "DELETE FROM alerts WHERE id = ?", (alert_id,)) > 0


def delete_category(conn: sqlite3.Connection, category: AlertCategory) -> int:
    """그 분류의 알림을 모두 지우고 지운 수를 돌려준다."""
    return _change(
        conn,
        "알림 지우기",
        "DELETE FROM alerts WHERE category = ?",
        (AlertCategory(category).value,),
    )


def delete_expired(conn: sqlite3.Connection, *, retention_days: int, now: datetime) -> int:
    """생긴 때에 `retention_days`×24시간을 더한 때가 `now` 이전인(같아도 포함) 알림을 지운다.

    지운 수를 돌려준다.
    """
    cutoff = format_utc(now - timedelta(days=retention_days))
    return _change(
        conn, "오래된 알림 지우기", "DELETE FROM alerts WHERE created_utc <= ?", (cutoff,)
    )


def set_discord_state(conn: sqlite3.Connection, alert_id: int, state: DiscordState) -> bool:
    """그 알림의 `discord_state`를 바꾼다. 그 줄이 있었으면 참이다."""
    sql = "UPDATE alerts SET discord_state = ? WHERE id = ?"
    return _change(conn, "디스코드 결과 저장", sql, (DiscordState(state).value, alert_id)) > 0


def settle_not_sent(conn: sqlite3.Connection, category: AlertCategory, state: DiscordState) -> int:
    """그 분류에 남은 `"not_sent"`를 그 분류의 주소 상태(`"failed"`나 `"no_address"`)로 바꾼다.

    바꾼 수를 돌려준다. `state`가 그 둘이 아니면 `ValueError`다.
    """
    state = DiscordState(state)
    if state not in _SETTLED_STATES:
        raise ValueError("주소 상태는 failed나 no_address여야 합니다")
    sql = "UPDATE alerts SET discord_state = ? WHERE category = ? AND discord_state = ?"
    params = (state.value, AlertCategory(category).value, DiscordState.NOT_SENT.value)
    return _change(conn, "디스코드 결과 정리", sql, params)


def _change(conn: sqlite3.Connection, action: str, sql: str, params: tuple[object, ...]) -> int:
    with translate_errors(action), transaction(conn):
        return conn.execute(sql, params).rowcount


def _category(text: str) -> AlertCategory:
    try:
        return AlertCategory(text)
    except ValueError as exc:
        raise StorageError("저장된 알림의 분류가 맞지 않습니다") from exc


def _to_view(row: tuple[object, ...]) -> AlertView:
    (alert_id, created, mode, category, code, coin, text, stage, error_name, content, after, seen,
     state, report_id) = row  # fmt: skip
    try:
        discord_state = DiscordState(state)
        parts = (stage, error_name, content, after)
        if all(part is None for part in parts):
            detail = None
        elif any(part is None for part in parts):
            raise StorageError("저장된 알림의 자세한 글이 모자랍니다")
        else:
            detail = AlertDetail(str(stage), coin, str(error_name), str(content), str(after))
        return AlertView(
            id=int(alert_id),  # type: ignore[call-overload]
            created_at=parse_utc(str(created)),
            mode=Mode(mode),
            category=_category(str(category)),
            code=str(code),
            coin=None if coin is None else str(coin),
            text=str(text),
            detail=detail,
            seen=seen == 1,
            discord_failed=discord_state is DiscordState.FAILED,
            report_id=None if report_id is None else int(report_id),  # type: ignore[call-overload]
        )
    except ValueError as exc:
        raise StorageError("저장된 알림의 값이 맞지 않습니다") from exc
