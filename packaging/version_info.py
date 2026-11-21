"""Windows 실행 파일의 판 정보 값(M1 명세 3.15, REQ-MAINT-009·010).

`.spec`이 루트의 `__version__`(글로 읽은 값)을 넘기고, 돌려받은 값으로 PyInstaller의
`VSVersionInfo`를 만들어 `EXE(version=…)`에 넣는다. 빌드 뒤에는 같은 값으로 exe의 판 정보를 다시
읽어 견준다. 표준 라이브러리만 쓰고 계산만 한다. 다음 판 올리기는 이 파일을 고치지 않는다.

PyPI의 `packaging` 패키지와 이름이 겹치므로 `import packaging`으로 읽지 않고 파일 경로로 읽는다.
"""

import re

# MAJOR.MINOR.PATCH, 숫자 앞에 0이 없음(REQ-MAINT-009). `\d`는 다른 문자의 숫자도 받아 쓰지 않는다.
VERSION_PATTERN = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")

LANGUAGE = "0412"  # 한국어
CODEPAGE = "04B0"  # Unicode

PRODUCT_NAME = "Coin Report Trader"
INTERNAL_NAME = "coin-report-trader"
ORIGINAL_FILENAME = "coin-report-trader.exe"


def version_fields(version: str) -> dict:
    """판 정보에 넣는 값.

    - `filevers`, `prodvers`: 숫자 칸 (MAJOR, MINOR, PATCH, 0)
    - `language`, `codepage`: 글 칸(StringFileInfo)의 언어와 코드 페이지
    - `strings`: 글 칸의 이름과 글. `ProductVersion`과 `FileVersion`은 "v{판}"이다. `CompanyName`,
      `LegalCopyright`는 넣지 않는다.

    판이 MAJOR.MINOR.PATCH(숫자 앞에 0이 없음)가 아니면 `ValueError`다.
    """
    match = VERSION_PATTERN.fullmatch(version) if isinstance(version, str) else None
    if match is None:
        raise ValueError(f"판이 MAJOR.MINOR.PATCH 꼴이 아닙니다: {version!r}")
    numbers = (*(int(part) for part in match.groups()), 0)
    text = f"v{version}"
    return {
        "filevers": numbers,
        "prodvers": numbers,
        "language": LANGUAGE,
        "codepage": CODEPAGE,
        "strings": {
            "ProductName": PRODUCT_NAME,
            "FileDescription": PRODUCT_NAME,
            "InternalName": INTERNAL_NAME,
            "OriginalFilename": ORIGINAL_FILENAME,
            "ProductVersion": text,
            "FileVersion": text,
        },
    }
