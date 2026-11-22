"""실거래 허용 값(M5 명세 3.7.1, 제품 규칙 9절).

빌드 때 정하는 값이다. 설정 파일이나 화면으로는 바꿀 수 없다. 5단계 초기 판부터 참이다(사용자
결정(2026-11-22)). 거짓이면 실거래 켜기가 막히고(`LiveBlock.NOT_ALLOWED`), 저장된 모드가 `live`여도
켤 때 `paper`로 돌린다(`app`, 15번).

`scripts/checks/live_switch.py`(3.13)가 이 값을 `ast`로 읽어 승인된 값과 견준다. 그래서 글자
그대로의 `True`/`False`로 쓴다.
"""

from typing import Final

LIVE_TRADING_ALLOWED: Final[bool] = True
