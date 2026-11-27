"""비밀 값 보관소(M1 명세 3.10): Windows 자격 증명 보관소에
웹후크 주소와 업비트 키(M5 명세 3.8)를 둔다.

`core`, 표준 라이브러리, `keyring`만 import한다. 알림, 칸 아래 글, 로그, 스레드는 이 폴더의 일이
아니다(27번의 `app`이 한다). import할 때 `keyring.get_keyring()`을 부르거나 백엔드를 만들거나
값을 읽지 않는다(`--self-test`가 이 폴더의 모듈을 import한다).
"""

from coin_report_trader.vault.store import (
    SERVICE_NAME,
    UPBIT_ACCESS_KEY_NAME,
    UPBIT_SECRET_KEY_NAME,
    WEBHOOK_NAME_PREFIX,
    KeyringSecretStore,
    UnavailableSecretStore,
    create_secret_store,
    select_windows_backend,
    webhook_secret_name,
)

__all__ = [
    "SERVICE_NAME",
    "UPBIT_ACCESS_KEY_NAME",
    "UPBIT_SECRET_KEY_NAME",
    "WEBHOOK_NAME_PREFIX",
    "KeyringSecretStore",
    "UnavailableSecretStore",
    "create_secret_store",
    "select_windows_backend",
    "webhook_secret_name",
]
