"""가짜 `claude` 실행 파일(M2 명세 3.15). 진짜 실행기의 시험이 `[sys.executable, 이 파일]`로 부른다.

앞의 두 인자 `--fake-config 파일`이 정한 모습(JSON)을 따른다. 그 뒤는 실행기가 준 인자다. 모습:

- `version`(글), `version_exit`, `version_sleep`: `--version`의 출력, 끝 코드, 잠자기(초).
- `auth_exit`, `auth_sleep`: `auth status`의 끝 코드와 잠자기(초). 출력에는 계정의 글
  (`account_text`)을 쓴다(실행기가 버려야 한다).
- `lines`(JSON 객체의 목록): stream-json의 줄. `exit_code`, `stderr`(글), `sleep`(초. 잠자기),
  `child`(참이면 자식 프로세스를 띄우고, 자식이 처음 쓴 것을 본 뒤에 이어 간다),
  `heartbeat`(파일 자리. 자신과 자식이 이 자리에 `.self`, `.child`를 붙인 파일에 조금씩 써서
  살아 있는지 보이게 한다).
- `record`(파일 자리): 부를 때마다 JSON 한 줄을 붙여 쓴다: 받은 인자, 환경 변수의 이름, 작업
  폴더와 그 안의 파일 이름과 글, 표준 입력에서 읽은 글.

주소나 비밀 값은 쓰지 않는다. 이 파일은 시험의 도구이고 값은 모두 만든 값이다.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

CHILD_CODE = (
    "import sys, time\n"
    "path = sys.argv[1]\n"
    "while True:\n"
    "    with open(path, 'ab') as handle:\n"
    "        handle.write(b'.')\n"
    "    time.sleep(0.05)\n"
)


def _record(config: dict, mode: str, args: list[str]) -> None:
    path = config.get("record")
    if not path:
        return
    folder = Path.cwd()
    files = {}
    for item in sorted(folder.iterdir()):
        if item.is_file():
            files[item.name] = item.read_bytes().decode("utf-8", errors="replace")
    entry = {
        "mode": mode,
        "args": args,
        "env_names": sorted(os.environ),
        "background_tasks": os.environ.get("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"),
        "cwd": str(folder),
        "files": files,
        "stdin": sys.stdin.read(),
    }
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _sleep(config: dict) -> None:
    seconds = float(config.get("sleep", 0))
    heartbeat = config.get("heartbeat")
    if config.get("child") and heartbeat:
        child_file = Path(heartbeat + ".child")
        subprocess.Popen([sys.executable, "-c", CHILD_CODE, str(child_file)])
        end_wait = time.monotonic() + 15
        while time.monotonic() < end_wait:  # 자식이 한 번 쓸 때까지 기다린 뒤에 이어 간다
            if child_file.exists() and child_file.stat().st_size > 0:
                break
            time.sleep(0.02)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if heartbeat:
            with open(heartbeat + ".self", "ab") as handle:
                handle.write(b".")
        time.sleep(0.05)


def main(argv: list[str]) -> int:
    config: dict = {}
    args = argv
    if argv[:1] == ["--fake-config"]:
        config = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        args = argv[2:]
    if args == ["--version"]:
        _record(config, "version", args)
        time.sleep(float(config.get("version_sleep", 0)))
        print(config.get("version", "2.1.314 (Claude Code)"))
        return int(config.get("version_exit", 0))
    if args[:2] == ["auth", "status"]:
        _record(config, "auth", args)
        time.sleep(float(config.get("auth_sleep", 0)))
        account = config.get("account_text", "")
        print(account)
        print(account, file=sys.stderr)
        return int(config.get("auth_exit", 0))
    _record(config, "run", args)
    _sleep(config)
    for obj in config.get("lines", []):
        print(json.dumps(obj, ensure_ascii=False), flush=True)
    if config.get("stderr"):
        print(config["stderr"], file=sys.stderr, flush=True)
    return int(config.get("exit_code", 0))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
