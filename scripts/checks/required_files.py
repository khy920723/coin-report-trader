"""항목 1. 필수 파일: 프로젝트 틀의 파일이 있고, `packaging/` 아래 `.spec`가 둘 이상이 아니다."""

from pathlib import Path

TITLE = "필수 파일"

REQUIRED = ("pyproject.toml", "uv.lock", ".python-version", ".gitignore")


def check(root: Path) -> list[str]:
    problems = [f"{name}: 없다" for name in REQUIRED if not (root / name).is_file()]
    packaging = root / "packaging"
    if packaging.is_dir():
        specs = sorted(p.relative_to(root).as_posix() for p in packaging.rglob("*.spec"))
        if len(specs) > 1:
            problems.append(
                f"packaging/: .spec가 {len(specs)}개다(하나만 둔다): {', '.join(specs)}"
            )
    return problems
