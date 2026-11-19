"""보내기 스레드(M1 명세 3.8, 3.9): 디스코드 보내기와 보관소 읽고 쓰기를 맡는다.

- 이 스레드만 `WebhookSender`와 `SecretStore`를 쓴다. 보내는 객체는 스레드 안에서 만들고(진짜는
  `HttpWebhookSender`와 `vault.create_secret_store()`) 스레드 안에서만 쓰고 끝에 닫는다.
- 주소는 보내기 직전에 보관소에서 읽어 지역 변수에만 둔다. 자료 스레드, DB, 로그, 알림, 화면의
  글, 예외 글, `repr`에 두지 않는다. 주소를 담은 일(`_SaveJob`)은 `repr`에서 주소를 뺀다. 로그에는
  오류 이름과 예외 종류만 쓴다.
- 큐: 알림 보내기(`SendJob`, 자료 스레드가 `submit`), 주소 저장·지우기, 확인용 글 보내기(화면의
  동작이 이 스레드의 큐에 바로 들어온다). 한 번에 하나씩 차례로 처리한다.
- 기다림: 큐가 비면 `event` 하나만 기다린다(시간을 재지 않는다. 큐의 시간 제한이나 `sleep`을
  되풀이하지 않는다). 다시 보내기 전의 기다림(`RetryingSender`의 `sleep`)은 `Clock.wait(멈춤,
  초)`로 한다. 그래서 시험의 가짜 시계를 옮겨 깨우고, 가짜 시계의 시각이 이 스레드 때문에
  앞서가지 않으며, 끝낼 때 바로 풀린다.
- 결과: 알림은 `DeliveryResult`(보냄, 보내지 못함, 주소 없음), 주소 저장·지우기는
  `WebhookResult`를 자료 스레드(`DataPort`)에 돌려준다. 칸 아래 글은 분류마다 `save_message`와
  `test_message`를 따로 들고 있다가 새 `WebhookView`마다 둘 다 담아 화면으로 보낸다.
- 끝낼 때(`stop`)는 하던 보내기가 끝나기를 기다리지 않는다. 스레드는 daemon이고, 멈춤 뒤에는 결과를
  자료 스레드에도 화면에도 보내지 않고 다음 일도 하지 않는다.
"""

import logging
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from coin_report_trader.core import (
    AlertCategory,
    Clock,
    SecretStore,
    SecretStoreError,
    UiUpdate,
    WebhookError,
    WebhookSender,
    WebhookView,
    is_webhook_url,
)
from coin_report_trader.core.texts import (
    STAGE_WEBHOOK_DELETE,
    STAGE_WEBHOOK_SAVE,
    TEST_FAILED,
    TEST_SENDING,
    TEST_SENT,
    WEBHOOK_BAD_FORMAT,
    WEBHOOK_DELETE_FAILED,
    WEBHOOK_SAVE_FAILED,
)
from coin_report_trader.notify import (
    HttpWebhookSender,
    RetryingSender,
    send_confirmation_message,
)
from coin_report_trader.storage.states import DiscordState
from coin_report_trader.vault import create_secret_store, webhook_secret_name

from .send_types import (
    AddressState,
    DataPort,
    DeliveryResult,
    SendJob,
    WebhookResult,
)

log = logging.getLogger(__name__)

SEND_THREAD_NAME: Final = "crt-send"
"""보내기 스레드의 이름. 시험이 보관소와 디스코드를 쓰는 스레드를 가린다."""


class _Stopped(Exception):
    """기다리는 동안 멈춤이 왔다."""


class _StopClock:
    """보내기 스레드가 쓰는 시계. 넘겨받은 시계를 감싸고 `sleep`만 멈춤에 풀리게 바꾼다.

    `RetryingSender`가 부르는 `sleep(초)`을 `clock.wait(멈춤, 초)`로 한다. 가짜 시계에서는 시각을
    옮기지 않으므로(옮기면 자료 스레드의 시각도 함께 앞선다) 시험이 `advance`로 깨운다.
    """

    def __init__(self, clock: Clock, stop: threading.Event) -> None:
        self._clock = clock
        self._stop = stop

    def now(self) -> datetime:
        return self._clock.now()

    def monotonic(self) -> float:
        return self._clock.monotonic()

    def sleep(self, seconds: float) -> None:
        if self._clock.wait(self._stop, seconds) or self._stop.is_set():
            raise _Stopped

    def wait(self, event: threading.Event, seconds: float) -> bool:
        return self._clock.wait(event, seconds)


@dataclass(frozen=True)
class _SaveJob:
    category: AlertCategory
    url: str = field(repr=False)


@dataclass(frozen=True)
class _DeleteJob:
    category: AlertCategory


@dataclass(frozen=True)
class _TestJob:
    category: AlertCategory


type _Job = SendJob | _SaveJob | _DeleteJob | _TestJob


class SendWorker:
    """보내기 스레드와 그 큐. 화면의 주소 동작(`save_webhook`, `delete_webhook`, `send_test`)을
    주 스레드에서 받고, 자료 스레드의 알림(`submit`)을 받는다."""

    def __init__(
        self,
        *,
        webhook: WebhookSender | None,
        vault: SecretStore | None,
        clock: Clock,
        sink: Callable[[UiUpdate], None],
    ) -> None:
        self._webhook = webhook
        self._vault = vault
        self._clock = clock
        self._sink = sink
        self._data: DataPort | None = None
        self._lock = threading.Lock()
        self._queue: deque[_Job] = deque()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._waiting = threading.Event()
        self._processing = False
        self._thread: threading.Thread | None = None
        # 아래는 보내기 스레드만 만진다.
        self._sender: WebhookSender | None = None
        self._owned_sender: HttpWebhookSender | None = None
        self._store: SecretStore | None = None
        self._saved: dict[AlertCategory, bool] = {}
        self._save_message: dict[AlertCategory, str | None] = {}
        self._test_message: dict[AlertCategory, str | None] = {}

    # --- 주 스레드와 자료 스레드: 시작, 끝, 일 넣기 ----------

    def attach(self, data: DataPort) -> None:
        """결과를 돌려줄 자료 스레드. 시작하기 전에 정한다."""
        self._data = data

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=SEND_THREAD_NAME, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """멈춤을 알린다. 하던 보내기가 끝나기를 기다리지 않는다."""
        self._stop.set()
        self._wake.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def thread(self) -> threading.Thread | None:
        return self._thread

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """큐가 비고 하는 일이 없어 `event`를 기다릴 때까지 기다린다(시험의 길)."""
        done = threading.Event()
        for _ in range(max(1, int(timeout / 0.002))):
            with self._lock:
                idle = not self._queue and not self._processing
            if idle and self._waiting.is_set():
                return True
            done.wait(0.002)
        return False

    def submit(self, job: SendJob) -> None:
        self._put(job)

    def save_webhook(self, category: AlertCategory, url: str) -> None:
        self._put(_SaveJob(AlertCategory(category), url))

    def delete_webhook(self, category: AlertCategory) -> None:
        self._put(_DeleteJob(AlertCategory(category)))

    def send_test(self, category: AlertCategory) -> None:
        self._put(_TestJob(AlertCategory(category)))

    def _put(self, job: _Job) -> None:
        with self._lock:
            self._queue.append(job)
        self._wake.set()

    # --- 보내기 스레드 ----------

    def _run(self) -> None:
        try:
            self._open()
            self._publish_startup()
            self._loop()
        except Exception as exc:
            log.error("보내기 스레드가 멈췄습니다: %s", type(exc).__name__)
        finally:
            self._close()

    def _open(self) -> None:
        sender = self._webhook
        if sender is None:
            self._owned_sender = HttpWebhookSender()
            sender = self._owned_sender
        self._sender = sender
        store = self._vault
        if store is None:
            store = create_secret_store()
        self._store = store

    def _close(self) -> None:
        if self._owned_sender is not None:
            try:
                self._owned_sender.close()
            except Exception as exc:
                log.warning("디스코드 연결을 닫지 못했습니다: %s", type(exc).__name__)

    def _post(self, update: UiUpdate) -> None:
        if self._stop.is_set():
            return
        try:
            self._sink(update)
        except Exception as exc:
            log.warning("화면에 보내지 못했습니다: %s", type(exc).__name__)

    def _publish_startup(self) -> None:
        """분류마다 보관소에서 저장되어 있는지만 보고 `WebhookView`를 보낸다. 상태만 자료
        스레드에 넘긴다(주소는 넘기지 않는다)."""
        states: dict[AlertCategory, AddressState] = {}
        try:
            for category in AlertCategory:
                states[category] = self._address_state(category)
                self._saved[category] = states[category] is AddressState.SAVED
                self._save_message[category] = None
                self._test_message[category] = None
                self._publish(category)
        finally:
            for category in AlertCategory:
                states.setdefault(category, AddressState.UNREADABLE)
            if self._data is not None and not self._stop.is_set():
                self._data.address_states(states)

    def _address_state(self, category: AlertCategory) -> AddressState:
        assert self._store is not None
        try:
            value = self._store.get(webhook_secret_name(category))
        except SecretStoreError as exc:
            log.warning("주소를 읽지 못함(%s): %s", category.value, exc.error_name)
            return AddressState.UNREADABLE
        except Exception as exc:
            log.warning("주소를 읽지 못함(%s): %s", category.value, type(exc).__name__)
            return AddressState.UNREADABLE
        return AddressState.NONE if value is None else AddressState.SAVED

    def _publish(self, category: AlertCategory) -> None:
        self._post(
            WebhookView(
                category,
                self._saved.get(category, False),
                self._save_message.get(category),
                self._test_message.get(category),
            )
        )

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            with self._lock:
                job = self._queue.popleft() if self._queue else None
                self._processing = job is not None
            if job is None:
                self._waiting.set()
                self._wake.wait()
                self._waiting.clear()
                continue
            try:
                self._handle(job)
            except Exception as exc:
                log.error("보내기 스레드의 일이 실패했습니다: %s", type(exc).__name__)
            finally:
                with self._lock:
                    self._processing = False

    def _handle(self, job: _Job) -> None:
        if isinstance(job, SendJob):
            self._deliver(job)
        elif isinstance(job, _SaveJob):
            self._save(job)
        elif isinstance(job, _DeleteJob):
            self._delete(job)
        else:
            self._test(job)

    # --- 알림 보내기 ----------

    def _deliver(self, job: SendJob) -> None:
        state = self._send_alert(job)
        if state is None or self._stop.is_set() or self._data is None:
            return
        self._data.delivery_result(DeliveryResult(job.alert_id, state))

    def _send_alert(self, job: SendJob) -> DiscordState | None:
        """그 분류의 주소를 읽어 한 번 보낸다(한도 초과일 때만 한 번 더). 멈춤이면 `None`."""
        assert self._store is not None
        assert self._sender is not None
        try:
            url = self._store.get(webhook_secret_name(job.category))
        except SecretStoreError as exc:
            log.warning("주소를 읽지 못함(%s): %s", job.category.value, exc.error_name)
            return DiscordState.FAILED
        except Exception as exc:
            log.warning("주소를 읽지 못함(%s): %s", job.category.value, type(exc).__name__)
            return DiscordState.FAILED
        if url is None:
            return DiscordState.NO_ADDRESS
        try:
            RetryingSender(self._sender, _StopClock(self._clock, self._stop)).send(url, job.message)
        except _Stopped:
            return None
        except WebhookError as exc:
            log.warning("알림 %s을 보내지 못했습니다: %s", job.alert_id, exc.error_name)
            return DiscordState.FAILED
        except Exception as exc:
            log.warning("알림 %s을 보내지 못했습니다: %s", job.alert_id, type(exc).__name__)
            return DiscordState.FAILED
        return DiscordState.SENT

    # --- 주소 저장, 지우기, 확인용 글 ----------

    def _save(self, job: _SaveJob) -> None:
        category = job.category
        target = job.url.strip()
        if not is_webhook_url(target):  # 화면이 먼저 거르므로 여기까지 오지 않는다
            self._save_message[category] = WEBHOOK_BAD_FORMAT
            self._publish(category)
            return
        assert self._store is not None
        error_name: str | None = None
        try:
            self._store.set(webhook_secret_name(category), target)
        except SecretStoreError as exc:
            error_name = exc.error_name
        except Exception as exc:
            error_name = type(exc).__name__
        if error_name is None:
            self._saved[category] = True
            self._save_message[category] = None
            self._test_message[category] = None  # 앞 주소로 본 결과는 새 주소의 것이 아니다
        else:
            log.warning("주소를 저장하지 못했습니다(%s): %s", category.value, error_name)
            self._save_message[category] = WEBHOOK_SAVE_FAILED
        self._finish_webhook(category, STAGE_WEBHOOK_SAVE, error_name)

    def _delete(self, job: _DeleteJob) -> None:
        category = job.category
        assert self._store is not None
        error_name: str | None = None
        try:
            self._store.delete(webhook_secret_name(category))
        except SecretStoreError as exc:
            error_name = exc.error_name
        except Exception as exc:
            error_name = type(exc).__name__
        if error_name is None:
            self._saved[category] = False
            self._save_message[category] = None
            self._test_message[category] = None
        else:
            log.warning("주소를 지우지 못했습니다(%s): %s", category.value, error_name)
            self._save_message[category] = WEBHOOK_DELETE_FAILED  # 저장된 주소는 그대로다
        self._finish_webhook(category, STAGE_WEBHOOK_DELETE, error_name)

    def _finish_webhook(self, category: AlertCategory, stage: str, error_name: str | None) -> None:
        """결과마다 새 `WebhookView`를 보내고(화면이 칸의 잠금을 푼다) 자료 스레드에 알린다."""
        self._publish(category)
        if self._data is not None and not self._stop.is_set():
            result = WebhookResult(category, stage, error_name is None, error_name)
            self._data.webhook_result(result)

    def _test(self, job: _TestJob) -> None:
        category = job.category
        self._test_message[category] = TEST_SENDING
        self._publish(category)
        ok = self._send_confirmation(category)
        if ok is None:
            return
        self._test_message[category] = TEST_SENT if ok else TEST_FAILED
        self._publish(category)

    def _send_confirmation(self, category: AlertCategory) -> bool | None:
        """저장된 주소로 확인용 글을 보낸다. 멈춤이면 `None`. `alerts` 표에 쓰지 않고 알림도
        만들지 않는다."""
        assert self._store is not None
        assert self._sender is not None
        try:
            url = self._store.get(webhook_secret_name(category))
        except SecretStoreError as exc:
            log.warning("주소를 읽지 못함(%s): %s", category.value, exc.error_name)
            return False
        except Exception as exc:
            log.warning("주소를 읽지 못함(%s): %s", category.value, type(exc).__name__)
            return False
        if url is None:
            return False
        try:
            send_confirmation_message(
                self._sender, url, category, clock=_StopClock(self._clock, self._stop)
            )
        except _Stopped:
            return None
        except WebhookError as exc:
            log.warning("확인용 글을 보내지 못했습니다(%s): %s", category.value, exc.error_name)
            return False
        except Exception as exc:
            name = type(exc).__name__
            log.warning("확인용 글을 보내지 못했습니다(%s): %s", category.value, name)
            return False
        return True
