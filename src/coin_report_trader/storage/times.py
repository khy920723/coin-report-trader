"""표의 시각 글(M1 명세 3.7): ISO 8601 UTC, 초까지 쓰고 끝에 `Z`(예: `2026-01-02T00:00:00Z`)."""

import re
from datetime import UTC, datetime

from coin_report_trader.core import StorageError

_UTC_TEXT = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z")


def format_utc(moment: datetime) -> str:
    """UTC 시각 글. UTC가 아닌 시각은 UTC로 바꾸고, 초 아래는 버린다(내림).

    시간대가 없는 `datetime`은 어느 시각인지 알 수 없으므로 `ValueError`다.
    """
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("시간대가 없는 시각은 저장하지 않습니다")
    utc = moment.astimezone(UTC)
    return (
        f"{utc.year:04d}-{utc.month:02d}-{utc.day:02d}"
        f"T{utc.hour:02d}:{utc.minute:02d}:{utc.second:02d}Z"
    )


def parse_utc(text: str) -> datetime:
    """`format_utc`의 글을 UTC가 붙은 `datetime`으로 읽는다. 꼴이 다르면 `StorageError`."""
    match = _UTC_TEXT.fullmatch(text)
    if match is None:
        raise StorageError("저장된 시각 글의 꼴이 맞지 않습니다")
    try:
        return datetime(*map(int, match.groups()), tzinfo=UTC)
    except ValueError as exc:
        raise StorageError("저장된 시각 글의 꼴이 맞지 않습니다") from exc
