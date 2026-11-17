"""창이 두 화면(처음 설정, 설정)에 넘기는 그림 그릴 값. 화면은 이 값만 보고 그린다."""

from dataclasses import dataclass
from enum import Enum, StrEnum


class Phase(Enum):
    WAITING = "waiting"
    """`actions`가 있는데 첫 `SettingsView`가 아직 오지 않았다."""
    UNREADABLE = "unreadable"
    """켤 때 설정을 읽지 못했다(`SettingsView(state=FAILED)`)."""
    READY = "ready"


class PickerKind(Enum):
    IDLE = "idle"
    """보일 목록도 글도 없다(받으라고 한 적이 없거나, 코인 고르기를 쓰지 않는 때)."""
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"
    """받지 못함이나 일시 차단 중. 글과 "다시 시도"만 보인다."""


@dataclass(frozen=True)
class PickerMode:
    kind: PickerKind
    text: str = ""
    retry: bool = False


class Tone(StrEnum):
    NORMAL = "normal"
    WARNING = "warning"


@dataclass(frozen=True)
class Message:
    text: str
    tone: Tone = Tone.NORMAL
