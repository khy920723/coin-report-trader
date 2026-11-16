"""표 안에서만 쓰는 값."""

from enum import StrEnum


class DiscordState(StrEnum):
    """`alerts.discord_state`(M1 명세 3.7)."""

    NOT_SENT = "not_sent"
    """만들 때."""
    SENT = "sent"
    FAILED = "failed"
    NO_ADDRESS = "no_address"
    LOCAL_ONLY = "local_only"
    """창을 띄우기 전에 난 오류. 디스코드로 보내지 않고 표시도 붙이지 않는다."""
