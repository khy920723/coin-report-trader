"""hypothesis 설정 묶음(검증 과제): 입력을 늘 같게 만들고 흔들리지 않게 한다.

`tests/property/conftest.py`와 `tests/differential/conftest.py`가 이 모듈만 불러온다. hypothesis를
쓰는 시험은 그 두 폴더에 둔다. 설정 묶음은 한 번 불러오면 시험 프로세스 전체에 걸린다(폴더마다 다른
설정을 둘 수 없다).

- `derandomize=True`: 돌릴 때마다 같은 입력을 만든다(같은 코드에서 결과가 바뀌지 않는다).
- `max_examples`: 시험마다 만드는 입력 수를 정한다.
- `deadline=None`: 한 입력의 시간 제한을 끈다(Windows 검사가 느리다).
- `database=None`: 예제 저장소를 쓰지 않는다.
- hypothesis는 예제 저장소를 쓰지 않아도 캐시(상수, 유니코드 표)를 지금 폴더의 `.hypothesis/`에
  둔다(시험을 모을 때부터). 그 자리를 이 모듈을 처음 불러올 때 만든 임시 폴더로 돌리고 프로세스가
  끝날 때 지워, 저장소에 `.hypothesis/`가 생기지 않게 한다.
"""

import atexit
import shutil
import tempfile

from hypothesis import settings
from hypothesis.configuration import set_hypothesis_home_dir

_HOME = tempfile.mkdtemp(prefix="crt-hypothesis-")
set_hypothesis_home_dir(_HOME)
atexit.register(shutil.rmtree, _HOME, ignore_errors=True)

settings.register_profile(
    "verify",
    derandomize=True,
    max_examples=100,
    deadline=None,
    database=None,
    print_blob=False,
)
settings.load_profile("verify")
