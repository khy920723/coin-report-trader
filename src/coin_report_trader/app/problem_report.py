"""문제 보고 표(M2 명세 3.11.1, 제품 규칙 12절, 14절, REQ-DATA-011).

오류 알림과 기타 알림을 열이 정해진 CSV로 자료 폴더 `problems\\problems-YYYY-MM.csv`에 쌓는다
(`YYYY-MM`은 그 줄의 `occurred_kst`의 한국 시간 달). 꼴은 M2 명세 3.8.1의 공통 꼴(UTF-8 BOM,
쉼표, CRLF, 첫 줄은 머리줄)이다. 쓰는 것은 자료 스레드다. 파일 일의 실패는 `OSError`이고, 부르는
쪽이 로그에만 남긴다. `--self-test`는 이 폴더와 파일을 만들거나 쓰지 않는다.
"""

import hashlib
import logging
import os
import re
import traceback
from collections.abc import Iterable, Mapping
from dataclasses import astuple, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

from coin_report_trader.ai.csv_files import CsvError, read_csv, write_csv
from coin_report_trader.core import KST, redact

log = logging.getLogger(__name__)

PROBLEMS_FOLDER: Final = "problems"
BOM: Final = chr(0xFEFF)
MAX_MESSAGE_CHARS: Final = 500
MASK_IP: Final = "(가려서 보임)"
MASK_PATH: Final = "(경로)"
KEEP_MONTHS: Final = 3
PROBLEM_COLUMNS: Final = (
    "alert_id",
    "occurred_kst",
    "app_version",
    "mode",
    "category",
    "code",
    "stage",
    "error_name",
    "source",
    "coin",
    "round_number",
    "repeat_24h",
    "fingerprint",
    "message",
)
_PACKAGE: Final = "coin_report_trader"
_FILE_NAME: Final = re.compile(r"problems-(\d{4})-(\d{2})\.csv(\.bad)?")
_IPV4: Final = re.compile(
    r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
    r"(?![\d.]*\d)"
)
_IPV6: Final = re.compile(
    r"(?<![0-9A-Fa-f:])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,}(?![0-9A-Fa-f:])"
)
_PATH: Final = re.compile(
    r"(?<!\w)[A-Za-z]:[\\/]\S*|\\\\\S*|(?<![\w:/])/[^\s/]*/\S*"
)  # 드라이브 글자, `\\`, 낱말이나 `:`·`/` 바로 뒤가 아닌 `/`로 시작해 `/`가 하나 더 든 경로
_UUID: Final = re.compile(
    r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
)
_NUMBER: Final = re.compile(
    r"(?<![\d.,])(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{5,}(?:\.\d+)?|\d+\.\d{3,})(?![\d])"
)
"""쉼표로 세 자리씩 나눈 수, 정수 부분이 다섯 자리 이상인 수, 소수점 뒤가 세 자리 이상인 수
(금액, 잔고, 수량. M3 명세 3.14.3)."""
MASK_UUID: Final = "(식별값)"
MASK_NUMBER: Final = "(수)"
_SPACES: Final = re.compile(r"\r\n|\n|\r|\t")


@dataclass(frozen=True)
class ProblemRow:
    """`PROBLEM_COLUMNS`와 같은 이름, 같은 차례의 필드. 빈 칸은 `None`(정수)이나 빈 글이다."""

    alert_id: int | None
    occurred_kst: str
    app_version: str
    mode: str
    category: str
    code: str
    stage: str
    error_name: str
    source: str
    coin: str
    round_number: int | None
    repeat_24h: int
    fingerprint: str
    message: str

    def cells(self) -> list[str]:
        return ["" if value is None else str(value) for value in astuple(self)]


def _package_dir() -> Path:
    """앱 패키지 폴더(이 파일이 든 `app`의 위)."""
    return Path(__file__).resolve().parent.parent


def problem_source(exc: BaseException | None, module: str) -> str:
    """일어난 자리(`source`): 예외의 traceback에서 앱 패키지 폴더 안의 마지막 자리를 폴더에 대한
    상대 경로(`/`로 잇는다)와 줄 번호로(예: `ai/claude_cli.py:142`). 예외가 없거나 앱 밖에서만
    났으면 알림을 만든 모듈의 이름에서 앞의 `coin_report_trader.`를 뗀 것이다(예:
    `app.data_thread`). 절대 경로와 사용자 폴더는 어느 쪽에도 쓰지 않는다."""
    root = _package_dir()
    place = ""
    if exc is not None:
        for frame in traceback.extract_tb(exc.__traceback__):
            try:
                relative = Path(frame.filename).resolve().relative_to(root)
            except (ValueError, OSError):
                continue
            place = f"{relative.as_posix()}:{frame.lineno}"
    if place:
        return place
    prefix = _PACKAGE + "."
    return module[len(prefix) :] if module.startswith(prefix) else module


def problem_fingerprint(code: str, stage: str, error_name: str, source: str) -> str:
    """같은 문제의 표시: 네 값을 `|`로 이은 글의 sha256의 앞 12자(16진수 소문자)."""
    joined = "|".join((code, stage, error_name, source))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


def _mask_ipv6(match: re.Match[str]) -> str:
    text = match.group(0)
    if "::" in text or any(char in "abcdefABCDEF" for char in text):
        return MASK_IP
    return text


def problem_message(
    text: str,
    content: str | None,
    *,
    secrets: Iterable[str] = (),
    env: Mapping[str, str] | None = None,
) -> str:
    """가린 내용: 알림의 글에 내용이 있으면 ` · ` 뒤에 이은 한 줄. 차례대로 `redact`, IP 주소,
    식별값(uuid 꼴), 금액·수량 꼴의 수(M3 명세 3.14.3), 남은 절대 경로, 줄바꿈과 탭, 500자 자르기를
    거친다."""
    joined = text if not content else f"{text} · {content}"
    masked = redact(joined, secrets, env)
    masked = _IPV4.sub(MASK_IP, masked)
    masked = _IPV6.sub(_mask_ipv6, masked)
    masked = _UUID.sub(MASK_UUID, masked)
    masked = _NUMBER.sub(MASK_NUMBER, masked)
    masked = _PATH.sub(MASK_PATH, masked)
    masked = _SPACES.sub(" ", masked)
    return masked[:MAX_MESSAGE_CHARS]


def kst_text(moment: datetime) -> str:
    """`occurred_kst`의 글: 한국 시간, 초까지, `2026-11-22T03:15:20+09:00` 꼴."""
    return moment.astimezone(KST).isoformat(timespec="seconds")


def _month_key(moment: datetime) -> tuple[int, int]:
    local = moment.astimezone(KST)
    return local.year, local.month


def _month_name(year: int, month: int) -> str:
    return f"problems-{year:04d}-{month:02d}.csv"


def _shift(key: tuple[int, int], months: int) -> tuple[int, int]:
    index = key[0] * 12 + (key[1] - 1) + months
    return index // 12, index % 12 + 1


class ProblemReport:
    """`root`(자료 폴더) 아래 `problems` 폴더의 달마다의 파일."""

    def __init__(self, root: Path) -> None:
        self._folder = Path(root) / PROBLEMS_FOLDER
        self._header = write_csv(PROBLEM_COLUMNS, [])

    def path_for(self, moment: datetime) -> Path:
        """그 시각이 속한 한국 시간 달의 파일."""
        return self._folder / _month_name(*_month_key(moment))

    def prune(self, now: datetime) -> None:
        """남기는 파일: 지금의 한국 시간 달과 그 앞 두 달이 아닌 달의 파일을 지운다. 그 밖의
        이름은 건드리지 않는다. 실패(`OSError`)는 로그에만 남기고 다음 파일로 간다."""
        keep = {_shift(_month_key(now), -offset) for offset in range(KEEP_MONTHS)}
        try:
            names = os.listdir(self._folder)
        except FileNotFoundError:
            return
        except OSError as exc:
            log.warning("문제 보고 폴더를 읽지 못했습니다: %s", type(exc).__name__)
            return
        for name in names:
            found = _FILE_NAME.fullmatch(name)
            if found is None or (int(found.group(1)), int(found.group(2))) in keep:
                continue
            try:
                (self._folder / name).unlink()
            except OSError as exc:
                log.warning("오래된 문제 보고 파일을 지우지 못했습니다: %s", type(exc).__name__)

    def append(self, row: ProblemRow) -> None:
        """한 줄을 덧붙인다. 파일이 없으면 BOM과 머리줄부터 쓴다. 덧붙일 파일의 첫 줄이 머리줄과
        다르면 그 파일의 이름 뒤에 `.bad`를 붙여 두고(같은 이름의 `.bad`는 덮는다) 새 파일을
        머리줄부터 쓴다. 실패는 `OSError`다."""
        occurred = datetime.fromisoformat(row.occurred_kst)
        self._folder.mkdir(parents=True, exist_ok=True)
        path = self.path_for(occurred)
        if path.exists() and not self._has_header(path):
            os.replace(path, path.with_name(path.name + ".bad"))
        line = write_csv(PROBLEM_COLUMNS, [row.cells()])[len(self._header) :]
        fresh = not path.exists()
        with path.open("a", encoding="utf-8", newline="") as handle:
            if fresh:
                handle.write(BOM + self._header)
            handle.write(line)

    def count_recent(self, fingerprint: str, occurred: datetime) -> int:
        """그 달과 앞 달의 파일에서 `occurred_kst`가 `occurred`의 24시간 전보다 뒤이고 `occurred`
        이하이며 `fingerprint`가 같은 줄의 수(새로 쓸 줄은 세지 않는다)."""
        count = 0
        start = occurred - timedelta(hours=24)
        for offset in (-1, 0):
            name = _month_name(*_shift(_month_key(occurred), offset))
            for cells in self._rows(self._folder / name):
                try:
                    when = datetime.fromisoformat(cells["occurred_kst"])
                except (KeyError, ValueError):
                    continue
                if cells.get("fingerprint") == fingerprint and start < when <= occurred:
                    count += 1
        return count

    def _has_header(self, path: Path) -> bool:
        with path.open("rb") as handle:
            first = handle.readline()
        text = first.decode("utf-8", errors="replace").lstrip(BOM)
        return text == self._header

    def _rows(self, path: Path) -> list[dict[str, str]]:
        try:
            text = path.read_text(encoding="utf-8")
            header, rows = read_csv(text)
        except FileNotFoundError:
            return []
        except (OSError, UnicodeError, CsvError) as exc:
            log.warning("문제 보고 파일을 읽지 못했습니다: %s", type(exc).__name__)
            return []
        if header != PROBLEM_COLUMNS:
            return []
        return [dict(zip(header, row, strict=False)) for row in rows]
