"""진입점: `python -m coin_report_trader [--self-test | --version]`.

exe는 `packaging/launcher.py`가 이 `main()`을 부른다. 표준 라이브러리와 루트의 `__version__`만
import하고, 무거운 모듈(`app`)은 옵션을 읽은 뒤 함수 안에서 import한다.

`main`은 종료 코드를 돌려주고 어떤 예외도(`SystemExit` 포함) 밖으로 내보내지 않는다. 창 모드
exe에서는 `sys.stdout`, `sys.stderr`가 `None`이고, 처리되지 않은 예외는 오류 대화상자를 띄워 멈춘다.
"""

import argparse
import contextlib
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from coin_report_trader import __version__

PROG = "coin-report-trader"
# app/self_test.py의 REPORT_FILE_NAME과 같아야 한다(시험이 확인한다).
# 그 모듈을 import하지 못한 경우에도 보고서를 남기려고 따로 둔다.
FALLBACK_REPORT_NAME = "self-test-report.txt"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, add_help=False, allow_abbrev=False)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="창 없이 실행 환경을 점검하고 현재 폴더에 self-test-report.txt를 쓴 뒤 끝낸다",
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    return parser


def main(argv: Sequence[str] | None = None, *, services: object | None = None) -> int:
    """`services`는 시험이 가짜를 넣는 자리다. 타입을 `object`로 두고 받은 값을 그대로 넘긴다."""
    try:
        return _main(argv, services)
    except SystemExit as exc:  # argparse가 내는 종료(`--version`은 0, 모르는 옵션은 2)
        return _exit_code(exc)
    except BaseException:
        return 1


def _main(argv: Sequence[str] | None, services: object | None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        return _self_test(services)
    return _gui([] if argv is None else list(argv), services)


def _gui(argv: list[str], services: object | None = None) -> int:
    from coin_report_trader.app import run_gui

    try:
        return run_gui(argv, services=services)  # type: ignore[arg-type]
    except Exception as exc:
        # 원인은 run_gui가 로그에 남긴다. 로그를 열기 전의 오류는 종류만 알린다.
        _warn(f"{PROG}: 시작하지 못했습니다 ({type(exc).__name__})")
        return 1


def _self_test(services: object | None = None) -> int:
    try:
        from coin_report_trader.app.self_test import run_self_test

        return run_self_test(services=services)  # type: ignore[arg-type]
    except BaseException as exc:
        _write_fallback_report(exc)
        return 1


def _write_fallback_report(exc: BaseException) -> None:
    # 경로가 들어간 트레이스백은 쓰지 않고 오류의 종류와 모듈 이름만 쓴다.
    cause = type(exc).__name__
    if isinstance(exc, ImportError) and exc.name:
        cause += f" ({exc.name})"
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = (
        "Coin Report Trader 자체 점검 (--self-test)\n"
        f"버전: {__version__}\n"
        f"실행 형태: {'exe' if getattr(sys, 'frozen', False) else '소스'}\n"
        f"점검 시각(UTC): {now}\n"
        "\n"
        "자체 점검을 끝까지 실행하지 못했습니다.\n"
        f"원인: {cause}\n"
        "\n"
        "결과: 실패\n"
    )
    with contextlib.suppress(Exception):
        (Path.cwd() / FALLBACK_REPORT_NAME).write_text(text, encoding="utf-8-sig")


def _exit_code(exc: SystemExit) -> int:
    code = exc.code
    if code is None:
        return 0
    return code if isinstance(code, int) else 1


def _warn(message: str) -> None:
    stream = sys.stderr
    if stream is not None:  # 창 모드 exe에서는 None이다.
        with contextlib.suppress(Exception):
            print(message, file=stream)


if __name__ == "__main__":
    sys.exit(main())
