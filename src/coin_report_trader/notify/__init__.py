"""디스코드 보내기(M1 명세 3.9): 알림의 글, 진짜 `WebhookSender`, 한 번 다시 보내기, 확인용 글.

`core`, 표준 라이브러리, `httpx`, `certifi`만 import한다. 보내기 스레드, 보관소에서 주소 읽기, 어느
분류의 주소로 보낼지, `discord_state` 쓰기, 칸 아래 글, 로그는 이 폴더의 일이 아니다(27번의
`app`이 한다). import할 때 클라이언트를 만들거나 연결하거나 파일을 읽지 않는다(`--self-test`가
이 폴더의 모듈을 import한다).
"""

from coin_report_trader.notify.message import (
    MAX_MESSAGE_LENGTH,
    alert_message,
    confirmation_message,
)
from coin_report_trader.notify.sender import (
    MAX_RETRY_WAIT,
    HttpWebhookSender,
    RetryingSender,
    send_alert,
    send_confirmation_message,
)

__all__ = [
    "MAX_MESSAGE_LENGTH",
    "MAX_RETRY_WAIT",
    "HttpWebhookSender",
    "RetryingSender",
    "alert_message",
    "confirmation_message",
    "send_alert",
    "send_confirmation_message",
]
