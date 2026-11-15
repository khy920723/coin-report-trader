# PyInstaller 설정(M0 명세 3.9절).
#   - 폴더형(onedir), 창 모드, UPX 끔
#   - 결과: dist/coin-report-trader/coin-report-trader.exe (Linux에서는 확장자 없음)
# 실행(저장소 루트에서): uv run pyinstaller packaging/coin-report-trader.spec --noconfirm --clean
#
# 라이선스 고지: exe 옆에 NOTICE.txt(LGPL-3.0 4(a)의 눈에 띄는 고지)를 두고, 원문과 목록은
# dist/coin-report-trader/_internal/licenses/ 아래에 넣는다. 대상은 번들에 든 제3자 것이다:
#   PySide6·Qt(LGPL-3.0과 GPL-3.0 원문), Python 런타임(그 안의 외부 소프트웨어 고지 포함),
#   PyInstaller 부트로더와 런타임 훅. 이 저장소의 코드는 라이선스를 두지 않으므로 앱의 LICENSE는 없다.
# 런타임 패키지의 라이선스 파일은 *.dist-info 폴더째 들어간다.
#
# 빌드를 멈추는 경우: GPL로만 제공되는 Qt 모듈이 번들에 들어감, 개발·빌드 전용 패키지가 번들에
# 들어감, (Windows) Python·패키지 밖에서 가져온 바이너리가 섞임, 고지가 가리키는 파일이 없음.

import importlib.metadata
import re
import shutil
import sys
import sysconfig
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

APP_NAME = "coin-report-trader"
PACKAGE = "coin_report_trader"
ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src"
PACKAGING = ROOT / "packaging"
LICENSES = "licenses"
VERSION_TOKEN = "@PYSIDE6_VERSION@"

# 오픈소스로는 GPL-3.0(또는 상용)으로만 제공되는 Qt 모듈의 바이너리 이름. 하나라도 번들에 들어가면
# 배포 전체가 GPL 조건을 따라야 하므로 빌드를 멈춘다. Qt 6.12의 라이선스 목록에 모듈 페이지 기준
# GPL 전용인 Charts, Data Visualization을 더했다. Designer는 GPL-3.0 + Qt GPL 예외(도구 출력만
# 예외)라 링크하면 GPL이다. Quick 3D는 이름으로 고른다: Qt6Quick3DSpatialAudio(Qt Multimedia, LGPL)와
# Qt 3D의 quick3dcoreplugin 등(LGPL)은 GPL 전용이 아니다.
GPL_ONLY_QT_BINARY = re.compile(
    r"canvaspainter|coap|qt6graphs|graphswidgets|grpc|httpserver|lottie|mqtt|networkauth"
    r"|qmlcompiler|qt6quick3d(?!spatialaudio)|qtquick3d|qquick3d|quick3dxr|quick3dphysics"
    r"|quick3dprofiler|assimp|quicktimeline|virtualkeyboard|qtvkb|waylandcompositor"
    r"|qt6charts|chartsqml|datavisualization|designer",
    re.IGNORECASE,
)


def runtime_distributions():
    """pyproject.toml의 런타임 의존성 이름(버전 조건을 뗀 것)."""
    with (ROOT / "pyproject.toml").open("rb") as file:
        requirements = tomllib.load(file)["project"]["dependencies"]
    return [re.split(r"[\s;\[<>=!~]", requirement, maxsplit=1)[0] for requirement in requirements]


def canonical(name):
    """배포판 이름 정규화(PEP 503)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def runtime_closure():
    """런타임 의존성과, 그 의존성 가운데 이 플랫폼에 설치되는 것의 정규화 이름."""
    from packaging.requirements import Requirement  # 빌드 환경에만 있으면 된다(PyInstaller 의존성)

    todo = list(runtime_distributions())
    seen = set()
    while todo:
        name = canonical(todo.pop())
        if name in seen:
            continue
        seen.add(name)
        for text in importlib.metadata.distribution(name).requires or []:
            requirement = Requirement(text)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                todo.append(requirement.name)
    return seen


def foreign_modules(module_names, owners, allowed):
    """번들 모듈 가운데 allowed 밖의 배포판(개발·빌드 전용)이 설치한 최상위 모듈.

    표준 라이브러리 모듈은 owners(packages_distributions)에 없으므로 걸리지 않는다.
    """
    found = set()
    for module_name in module_names:
        top = module_name.split(".")[0]
        distributions = {canonical(d) for d in owners.get(top, [])}
        if distributions and not distributions & allowed:
            found.add(top)
    return sorted(found)


def without_software_opengl(binaries):
    """opengl32sw.dll(Mesa llvmpipe 소프트웨어 OpenGL)을 뺀다.

    위젯 앱은 래스터로 그려 쓰지 않고, 넣으면 Mesa(MIT, BSL-1.0) 고지가 더 필요하다.
    """
    return [
        entry
        for entry in binaries
        if entry[0].replace("\\", "/").rsplit("/", 1)[-1].lower() != "opengl32sw.dll"
    ]


def binaries_outside(binaries, roots):
    """roots(Python 설치, 가상 환경, 저장소) 밖에서 모은 바이너리의 번들 경로.

    PATH에 있던 다른 프로그램의 DLL(예: 러너의 OpenSSL)이 섞이는 것을 막는다. Microsoft Universal
    CRT(ucrtbase.dll, api-ms-win-*.dll)는 PyInstaller가 Windows에서 일부러 모으는 시스템 런타임이라
    허용한다.
    """
    prefixes = tuple(root.replace("\\", "/").lower().rstrip("/") + "/" for root in roots)

    def is_ucrt(dest):
        name = dest.replace("\\", "/").rsplit("/", 1)[-1].lower()
        return name == "ucrtbase.dll" or (name.startswith("api-ms-win-") and name.endswith(".dll"))

    return sorted(
        dest
        for dest, source, _kind in binaries
        if not is_ucrt(dest) and not str(source).replace("\\", "/").lower().startswith(prefixes)
    )


def license_files(distribution):
    dist = importlib.metadata.distribution(distribution)
    names = ("LICENSE", "LICENCE", "COPYING", "NOTICE")
    return [Path(dist.locate_file(f)) for f in dist.files or [] if f.name.upper().startswith(names)]


def python_license_files():
    stdlib = Path(sysconfig.get_paths()["stdlib"])
    for candidate in (Path(sys.base_prefix) / "LICENSE.txt", stdlib / "LICENSE.txt"):
        if candidate.is_file():
            return [candidate]
    raise SystemExit("빌드에 쓴 Python의 LICENSE.txt를 찾지 못했습니다(licenses/python).")


def license_name(distribution):
    metadata = importlib.metadata.metadata(distribution)
    return metadata.get("License-Expression") or metadata.get("License") or "dist-info 참고"


def components_text(pyside_version):
    """_internal/licenses/components.txt: 번들에 든 제3자 구성 요소의 목록."""
    python = ".".join(str(part) for part in sys.version_info[:3])
    tools = [(name, importlib.metadata.version(name)) for name in ("pyinstaller", "pyinstaller-hooks-contrib")]
    lines = [
        "Coin Report Trader: 번들에 든 제3자 구성 요소 / Bundled third-party components",
        "",
        f"- Python {python} (PSF-2.0, 안의 외부 소프트웨어 고지 포함): licenses/python/",
        f"- SQLite (Python의 sqlite3, public domain)",
    ]
    for name in runtime_closure():
        version = importlib.metadata.version(name)
        lines.append(f"- {name} {version} ({license_name(name)}): licenses/LGPL-3.0.txt, licenses/GPL-3.0.txt, *.dist-info")
    lines.append(f"- Qt {pyside_version} (PySide6-Essentials에 들어 있다, LGPL-3.0으로 사용)")
    for name, version in tools:
        lines.append(f"- {name} {version} (부트로더·런타임 훅, {license_name(name)}): licenses/{name}/")
    return "\n".join(lines) + "\n"


datas = [
    (str(PACKAGING / "licenses" / "LGPL-3.0.txt"), LICENSES),
    (str(PACKAGING / "licenses" / "GPL-3.0.txt"), LICENSES),
]
for name in runtime_distributions():
    datas += copy_metadata(name, recursive=True)
# exe에 PyInstaller 부트로더(GPL + 예외)와 런타임 훅이 들어간다.
for tool in ("pyinstaller", "pyinstaller-hooks-contrib"):
    datas += [(str(path), f"{LICENSES}/{tool}") for path in license_files(tool)]
# Python 런타임: 빌드에 쓴 Python의 LICENSE.txt와, 거기에 빠진 외부 소프트웨어(expat, libmpdec,
# mimalloc, HACL* 등)의 고지.
datas += [(str(path), f"{LICENSES}/python") for path in python_license_files()]
for name in ("cpython-3.13.18-license.rst", "hacl-star-MIT.txt"):
    datas.append((str(PACKAGING / "licenses" / name), f"{LICENSES}/python"))

a = Analysis(
    [str(PACKAGING / "launcher.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    # --self-test가 앱 모듈을 모두 import해 보므로, 동적으로만 쓰이는 모듈도 빠짐없이 넣는다.
    hiddenimports=collect_submodules(PACKAGE),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 앱이 쓰지 않는 개발·빌드 전용 패키지가 PyInstaller의 처리에 끌려 들어오지 않게 한다.
    # QtNetwork는 앱이 쓰지 않고, 넣으면 OpenSSL DLL을 PATH에서 찾아 넣을 수 있다.
    excludes=[
        "setuptools",
        "_distutils_hack",
        "pkg_resources",
        "packaging",
        "PySide6.QtNetwork",
        "_pytest",
        "pytest",
        "pytestqt",
        "pygments",
    ],
    # LGPL-3.0 4(d)(1): PySide6·shiboken6의 Python 부분도 exe(PYZ) 밖 .py로 두어 바꿀 수 있게 한다.
    module_collection_mode={"PySide6": "py", "shiboken6": "py"},
    noarchive=False,
    optimize=0,
)

a.binaries = without_software_opengl(a.binaries)

if sys.platform == "win32":
    roots = [sys.base_prefix, sys.prefix, *sysconfig.get_paths().values(), str(ROOT)]
    foreign = binaries_outside(a.binaries, roots)
    if foreign:
        raise SystemExit("Python·패키지 밖에서 가져온 바이너리가 있습니다: " + ", ".join(foreign))

gpl_only = sorted(
    dest
    for dest, _source, _kind in (*a.binaries, *a.datas)
    if dest.replace("\\", "/").startswith("PySide6/") and GPL_ONLY_QT_BINARY.search(Path(dest).name)
)
if gpl_only:
    raise SystemExit("GPL 전용 Qt 바이너리가 번들에 들어갔습니다:\n  " + "\n  ".join(gpl_only))

dev_only = foreign_modules(
    (module_name for module_name, _source, _kind in a.pure),
    importlib.metadata.packages_distributions(),
    runtime_closure() | {APP_NAME},
)
if dev_only:
    raise SystemExit("개발·빌드 전용 패키지가 번들에 들어갔습니다: " + ", ".join(dev_only))

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="coin-report-trader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="coin-report-trader",
)

# COLLECT가 폴더를 만든 뒤에 고지와 목록을 쓴다.
dist_dir = Path(coll.name)
contents = dist_dir / (exe.contents_directory or ".")
pyside_version = importlib.metadata.version("PySide6-Essentials")

# LGPL-3.0 4(a): 사본마다 Qt를 LGPL로 쓴다는 고지를 exe 옆에 눈에 띄게 둔다.
notice = (PACKAGING / "NOTICE.txt").read_text(encoding="utf-8").replace(VERSION_TOKEN, pyside_version)
if "@" in notice:
    raise SystemExit("NOTICE.txt에 채우지 못한 자리표시가 남았습니다.")
# Windows 메모장에서도 한글이 깨지지 않게 BOM을 붙이고 줄바꿈은 CRLF로 쓴다.
(dist_dir / "NOTICE.txt").write_text(notice, encoding="utf-8-sig", newline="\r\n")
(contents / LICENSES / "components.txt").write_text(
    components_text(pyside_version), encoding="utf-8-sig", newline="\r\n"
)

# NOTICE.txt가 가리키는 파일이 실제로 들어갔는지 확인한다.
missing = [
    relative
    for relative in (
        f"{LICENSES}/LGPL-3.0.txt",
        f"{LICENSES}/GPL-3.0.txt",
        f"{LICENSES}/components.txt",
        f"{LICENSES}/python",
        f"{LICENSES}/pyinstaller",
        "PySide6",
        "shiboken6",
    )
    if not (contents / relative).exists()
]
if missing:
    raise SystemExit("NOTICE.txt가 가리키는 파일이 번들에 없습니다: " + ", ".join(missing))
