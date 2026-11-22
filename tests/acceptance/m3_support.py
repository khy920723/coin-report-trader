"""처음 설정을 누르는 시험이 함께 쓰는 도구(M3 명세 4절 15번).

3단계의 처음 설정(M3 명세 3.17.5. 17번이 만든다)에는 매매 칸과 "꼭 읽을 글을 읽었습니다"가 있고,
그 칸을 다 채워야 시작 단추(`firstSetupStart`)가 눌린다. 17번 앞에는 그 칸이 없다. 이 도구는 칸이
있으면 만든 값으로 채우고 없으면 아무것도 하지 않아, 같은 시험이 17번 앞뒤 모두에서 통과한다.

객체 이름은 3.17.5의 것이다(앞머리 `firstSetup`). 일부만 있으면 17번이 덜 된 것이라 실패로 본다.
값은 모두 만든 값이다.

이 파일은 시험을 담지 않는다(`test_`로 시작하지 않아 모으지 않는다).
"""

from decimal import Decimal
from typing import Any

from PySide6.QtWidgets import QAbstractButton, QCheckBox, QLineEdit, QWidget

PREFIX = "firstSetup"
COIN_RATIO = "CoinRatio_"
"""투자 비율 칸의 이름 앞머리(`{앞머리}CoinRatio_{마켓 코드}`)."""

# (이름 뒤쪽, 만든 값): 고른 코인 하나(100%)의 예산 1,000,000원에서 첫 매수 20%(200,000원)와 추가
# 한 번 10%(100,000원)는 3.11.2의 하한(5,790원, 5,500원)을 넘고 합 30%는 100% 안이다.
LINE_VALUES = (
    ("Budget", "1,000,000"),
    ("FirstRatio", "20"),
    ("MrFirstRatio", "20"),
    ("AddRatio", "10"),
    ("Tolerance", "1"),
    ("StopRatio", "5"),
    ("DailyLoss", "3,000"),
    ("OrderInterval", "60"),
    ("MrLoss", "5"),
    ("PaperFunds", "1,000,000"),
    ("BackupDays", "7"),
)
BUTTONS = ("Method_trend", "PauseStop_keep", "MrSell_no")
"""둥근 단추(매매 방식, '멈춤' 중 손절, '팔지')의 이름 뒤쪽. 값은 3.11.1의 값이다."""
READ = "Read"
TRADE_NAMES = (*(name for name, _ in LINE_VALUES), *BUTTONS, READ)


def trade_fields_exist(parent: QWidget, *, prefix: str = PREFIX) -> bool:
    """3단계의 매매 칸이 이 화면에 있는지(17번 뒤인지). 예산 칸으로 본다."""
    return parent.findChild(QLineEdit, f"{prefix}Budget") is not None


def _ratio_boxes(parent: QWidget, prefix: str) -> list[QLineEdit]:
    return [
        box
        for box in parent.findChildren(QLineEdit)
        if box.objectName().startswith(f"{prefix}{COIN_RATIO}")
    ]


def _shares(count: int) -> list[Decimal]:
    """합이 100이 되는 소수 둘째 자리까지의 몫. 마지막 칸이 나머지를 받는다."""
    each = (Decimal(100) / count).quantize(Decimal("0.01"), rounding="ROUND_DOWN")
    return [*([each] * (count - 1)), Decimal(100) - each * (count - 1)]


def fill_trade_fields(parent: QWidget, *, prefix: str = PREFIX, read: bool = True) -> int:
    """3단계의 매매 칸을 만든 값으로 채우고 단추를 고른다. 채운 칸의 수를 돌려준다.

    칸이 하나도 없으면 0이고 아무것도 하지 않는다(17번 앞). 일부만 있으면 `AssertionError`다.
    투자 비율 칸은 고른 코인마다 하나씩 있어야 한다(코인을 먼저 고른 뒤에 부른다). `read`가
    참이면 "꼭 읽을 글을 읽었습니다"도 고른다(처음 설정에만 있다).
    """
    if not trade_fields_exist(parent, prefix=prefix):
        found = [name for name in TRADE_NAMES if parent.findChild(QWidget, f"{prefix}{name}")]
        assert not found, f"3단계의 매매 칸 가운데 일부({found})만 있다"
        return 0
    filled = 0
    for name, value in LINE_VALUES:
        box = parent.findChild(QLineEdit, f"{prefix}{name}")
        assert box is not None, f"{prefix}{name}이 없다"
        box.setText(value)
        filled += 1
    boxes = _ratio_boxes(parent, prefix)
    assert boxes, "고른 코인의 투자 비율 칸이 없다(코인을 먼저 고른다)"
    for box, share in zip(boxes, _shares(len(boxes)), strict=True):
        box.setText(f"{share:f}")
        filled += 1
    for name in BUTTONS:
        button: Any = parent.findChild(QAbstractButton, f"{prefix}{name}")
        assert button is not None, f"{prefix}{name}이 없다"
        button.click()
        filled += 1
    if read:
        check = parent.findChild(QCheckBox, f"{prefix}{READ}")
        assert check is not None, f"{prefix}{READ}가 없다"
        check.setChecked(True)
        filled += 1
    return filled
