"""AI 스레드(M2 명세 3.5.2): 코인 세션마다 하나씩 띄우는 daemon 스레드.

`AiRunner.run`을 부르고 그 결과(또는 실행기가 낸 예외)를 `deliver`로 넘기고 끝난다. DB를 열지
않고 Qt 객체를 만지지 않는다. `deliver`는 자료 스레드의 큐에 넣는 일이다.
"""

import logging
import threading
from collections.abc import Callable

from coin_report_trader.core import AiRunner, SessionRequest, SessionResult

log = logging.getLogger(__name__)

AI_THREAD_PREFIX = "crt-ai-"
"""AI 스레드의 이름 앞머리. 뒤에 세션의 `key`가 붙는다."""


def start_session(
    runner: AiRunner,
    request: SessionRequest,
    deliver: Callable[[SessionResult | Exception], None],
) -> threading.Thread:
    """세션 하나를 새 daemon 스레드에서 돌린다. 결과는 `deliver`로 한 번만 간다."""

    def work() -> None:
        outcome: SessionResult | Exception
        try:
            outcome = runner.run(request)
        except Exception as exc:  # 실행기 안의 예상하지 못한 예외만 온다(3.4)
            outcome = exc
        try:
            deliver(outcome)
        except Exception as exc:
            log.warning("AI 스레드의 결과를 넘기지 못했습니다: %s", type(exc).__name__)

    thread = threading.Thread(target=work, name=AI_THREAD_PREFIX + request.key, daemon=True)
    thread.start()
    return thread
