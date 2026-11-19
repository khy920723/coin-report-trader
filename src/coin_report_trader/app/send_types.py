"""자료 스레드와 보내기 스레드가 주고받는 값(M1 명세 3.8의 "보내기 스레드").

두 스레드는 이 값들만 주고받는다. 주소는 어디에도 없다: 자료 스레드는 알림의 번호, 분류, 이미
가린 글만 넘기고, 보내기 스레드는 분류와 결과(성공, 실패와 오류 이름)와 주소 상태만 돌려준다.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from coin_report_trader.core import AlertCategory
from coin_report_trader.storage.states import DiscordState


class AddressState(StrEnum):
    """켤 때 분류마다의 주소 상태. 상태만 넘기고 주소는 넘기지 않는다."""

    SAVED = "saved"
    NONE = "none"
    UNREADABLE = "unreadable"
    """보관소를 읽지 못함."""


@dataclass(frozen=True)
class SendJob:
    """자료 스레드가 보내기 스레드에 넘기는 알림 하나."""

    alert_id: int
    """표의 줄 번호, 또는 메모리 알림의 음수 번호."""
    category: AlertCategory
    message: str
    """`notify.alert_message`로 만든, 이미 가린 글."""


@dataclass(frozen=True)
class DeliveryResult:
    """보내기 스레드가 돌려주는 알림 하나의 결과."""

    alert_id: int
    state: DiscordState
    """`SENT`, `FAILED`, `NO_ADDRESS` 가운데 하나."""


@dataclass(frozen=True)
class WebhookResult:
    """주소 저장과 지우기의 결과. 자료 스레드가 이것으로 A-29를 만들거나 되풀이를 푼다."""

    category: AlertCategory
    stage: str
    """`STAGE_WEBHOOK_SAVE`나 `STAGE_WEBHOOK_DELETE`."""
    ok: bool
    error_name: str | None = None
    """실패했을 때의 오류 이름("보관소를 쓸 수 없음", "보관소 오류" 또는 예외 종류)."""


class DataPort(Protocol):
    """보내기 스레드가 자료 스레드로 돌려주는 길. 어느 스레드에서나 부르고 바로 돌아온다."""

    def address_states(self, states: dict[AlertCategory, AddressState]) -> None: ...

    def delivery_result(self, result: DeliveryResult) -> None: ...

    def webhook_result(self, result: WebhookResult) -> None: ...


class SendPort(Protocol):
    """자료 스레드가 보내기 스레드로 넘기는 길. 어느 스레드에서나 부르고 바로 돌아온다."""

    def submit(self, job: SendJob) -> None: ...
