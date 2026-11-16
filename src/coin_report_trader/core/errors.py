"""공용 오류 타입."""


class StorageError(Exception):
    """저장소(SQLite) 설정이나 열기에 실패했다."""


class _NamedError(Exception):
    """오류 이름(화면과 알림에 보이는 말)과 응답이 준 기다릴 시간을 가진 오류."""

    def __init__(self, error_name: str, retry_after: float | None = None) -> None:
        super().__init__(error_name)
        self.error_name = error_name
        self.retry_after = retry_after

    def __reduce__(self) -> tuple[type, tuple[str, float | None]]:
        return (type(self), (self.error_name, self.retry_after))


class QuoteError(_NamedError):
    """공개 시세 요청이 실패했다. `error_name`은 3.9절의 오류 이름이고,
    `retry_after`는 응답이 준 기다릴 시간(초)이다."""


class WebhookError(_NamedError):
    """디스코드 보내기가 실패했다. `error_name`은 3.9절의 디스코드 쪽 오류 이름이다."""


class SecretStoreError(Exception):
    """비밀 값 보관소(Windows 자격 증명 보관소)를 쓰지 못했다."""

    def __init__(self, error_name: str) -> None:
        super().__init__(error_name)
        self.error_name = error_name

    def __reduce__(self) -> tuple[type, tuple[str]]:
        return (type(self), (self.error_name,))
