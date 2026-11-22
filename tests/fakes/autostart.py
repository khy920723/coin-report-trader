"""가짜 시작 앱 등록(M1 명세 3.16.4). `core.AutostartStore`(3.16.3)의 약속을 진짜와 같게 지킨다.

- `FakeAutostart(supported=True, registered=None, write_error=None, delete_error=None)`:
  - `supported()`는 `supported`를 돌려준다. 시험이 `supported`를 바꿀 수 있다.
  - `read()`는 등록된 글(`registered`)이다. 없으면 `None`.
  - `write(command)`는 `write_error`가 있으면 그 `OSError`를 내고(등록된 글은 그대로),
    없으면 `registered`를 바꾸고 `writes`에 남긴다.
  - `delete()`는 `delete_error`가 있으면 그 `OSError`를 내고(등록된 글은 그대로), 없으면 등록된
    글을 비우고 `deletes`를 하나 늘린다. 등록된 글이 없어도 성공이다.
- 정한 오류는 시험이 `write_error`와 `delete_error`에 넣고 빼 쓴다(쓰기와 지우기 따로).
- 기록: `writes`는 쓴 명령의 글(실패한 것은 남기지 않는다), `deletes`는 지운 수다.
- 여러 스레드가 함께 써도 된다(잠금 하나. 부르는 것은 자료 스레드다).
- 이 파일은 시험만 import한다. `src/`는 이 폴더를 import하지 않는다.
"""

import threading


class FakeAutostart:
    def __init__(
        self,
        *,
        supported: bool = True,
        registered: str | None = None,
        write_error: OSError | None = None,
        delete_error: OSError | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self.is_supported = supported
        self.registered = registered
        self.write_error = write_error
        self.delete_error = delete_error
        self.writes: list[str] = []
        self.deletes = 0

    def supported(self) -> bool:
        with self._lock:
            return self.is_supported

    def read(self) -> str | None:
        with self._lock:
            return self.registered

    def write(self, command: str) -> None:
        with self._lock:
            if self.write_error is not None:
                raise self.write_error
            self.registered = command
            self.writes.append(command)

    def delete(self) -> None:
        with self._lock:
            if self.delete_error is not None:
                raise self.delete_error
            self.registered = None
            self.deletes += 1

    def __repr__(self) -> str:
        return "FakeAutostart()"
