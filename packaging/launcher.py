"""PyInstaller 진입 스크립트. `coin_report_trader.__main__`의 `main()`을 부른다(명세 3.4절).

`src/coin_report_trader/__main__.py`를 진입 스크립트로 바로 쓰지 않는 까닭: PyInstaller는 진입
스크립트가 든 폴더를 모듈 검색 경로 맨 앞에 넣는다. 패키지 폴더가 들어가면 그 안의 `core`, `config`,
`app` 같은 이름이 최상위 모듈처럼 보여 같은 이름의 다른 모듈을 가리거나 패키지가 두 번 올라온다.

`main()`은 어떤 예외도 밖으로 내보내지 않는다. 창 모드 exe에서 처리되지 않은 예외는 오류 대화상자를
띄워 멈추므로 이 파일도 import 말고는 아무것도 하지 않는다.
"""

import sys

from coin_report_trader.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
