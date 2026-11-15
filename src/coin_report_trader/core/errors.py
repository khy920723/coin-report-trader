"""공용 오류 타입."""


class StorageError(Exception):
    """저장소(SQLite) 설정이나 열기에 실패했다."""
