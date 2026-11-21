"""프로젝트 검사(M0 명세 3.7절, M1 명세 4절 7번, M2 명세 4절 3번). 표준 라이브러리만 쓴다.

사용: `uv run --no-project python scripts/checks/run_all.py` (저장소 어디서 불러도 된다).

항목은 같은 폴더의 모듈 하나씩이다. 모듈마다 `TITLE`(항목 이름)과 `check(root) -> list[str]`
(어긴 곳 목록, 비면 통과)를 둔다. 항목마다 `[통과]` 또는 `[실패]` 줄을 하나씩 쓰고, 실패한 항목
밑에는 어긴 파일과 줄을 들여 쓴다. 하나라도 실패하면 1, 아니면 0으로 끝난다.
검사하는 저장소는 이 파일이 든 저장소다(`scripts/checks/`의 두 단계 위).
"""

import importlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

# M0 명세 3.7절의 순서에 M1 명세 4절 7번의 웹후크 주소 검사, M2 명세 3.13의 Claude 자격 정보
# 검사를 더했다.
ITEM_MODULES = (
    "required_files",
    "legacy_excluded",
    "import_rules",
    "personal_info",
    "trademark",
    "webhook_urls",
    "claude_credentials",
)


def run_item(module_name: str, root: Path) -> tuple[str, list[str]]:
    """(항목 이름, 어긴 곳 목록). 항목이 예외를 내도 실패로 적고 끝낸다."""
    title = module_name
    try:
        module = importlib.import_module(module_name)
        title = module.TITLE
        return title, list(module.check(root))
    except Exception as exc:
        return title, [f"검사를 끝까지 하지 못했다({type(exc).__name__})"]


def main() -> int:
    _utf8_output()
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    failed = 0
    for module_name in ITEM_MODULES:
        title, problems = run_item(module_name, ROOT)
        if problems:
            failed += 1
            print(f"[실패] {title}: {len(problems)}건")
            for problem in problems:
                print(f"    - {problem}")
        else:
            print(f"[통과] {title}")
    total = len(ITEM_MODULES)
    verdict = "실패" if failed else "통과"
    print(f"결과: {verdict} ({total - failed}/{total})")
    return 1 if failed else 0


def _utf8_output() -> None:
    # Windows에서 파이프로 받으면 기본 인코딩이 cp1252 같은 것이라 한글을 쓰지 못한다.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    sys.exit(main())
