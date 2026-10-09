"""Build the official C++ SDK's ``demo_loadBalancing`` for interop tests.

The demo's own sources are compiled as shipped, except for two changes made on
a copy in the build directory (the SDK tree is never modified):

* ``NetworkLogic.cpp`` reads its app id from ``PHOTON_APP_ID`` instead of the
  hardcoded placeholder.
* ``StdIO_main.cpp`` is replaced by ``loadbalancing_driver.cpp``, which takes
  the demo's menu keys from piped stdin instead of the keyboard.

Needs Windows, MSVC (found via ``vswhere``) and the Photon Windows C++ SDK,
looked up in ``$PHOTON_CPP_SDK`` or ``sdks/photon-windows-sdk_*``. Run directly
to build without running the tests::

    python tests/e2e/sdk_demos/build.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BUILD_DIR = REPO / "build" / "sdk_demos"
EXE = BUILD_DIR / "demo_loadBalancing.exe"
DRIVER = HERE / "loadbalancing_driver.cpp"

_APP_ID_LINE = re.compile(r"^static const EG_CHAR\* appID = .*$", re.MULTILINE)
_APP_ID_FROM_ENV = 'static const EG_CHAR* appID = _wgetenv(L"PHOTON_APP_ID");'
_VSWHERE = (
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    / "Microsoft Visual Studio"
    / "Installer"
    / "vswhere.exe"
)


class DemoUnavailableError(RuntimeError):
    """The demo can't be built here; tests skip with this message."""


def find_sdk() -> Path:
    """Locate the Photon Windows C++ SDK root.

    Raises:
        DemoUnavailableError: No SDK found.
    """
    if env := os.environ.get("PHOTON_CPP_SDK"):
        candidates = [Path(env)]
    else:
        candidates = sorted((REPO / "sdks").glob("photon-windows-sdk_*"), reverse=True)
    for sdk in candidates:
        if (sdk / "Demos" / "demo_loadBalancing").is_dir():
            return sdk
    msg = "Photon Windows C++ SDK not found (set PHOTON_CPP_SDK)"
    raise DemoUnavailableError(msg)


def _find_vcvars() -> Path:
    if sys.platform != "win32":
        msg = "the C++ SDK demos only build on Windows"
        raise DemoUnavailableError(msg)
    if not _VSWHERE.is_file():
        msg = "Visual Studio / Build Tools not found"
        raise DemoUnavailableError(msg)
    install = subprocess.run(  # noqa: S603 -- fixed path from Program Files
        [
            str(_VSWHERE),
            "-latest",
            "-products",
            "*",
            "-requires",
            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            "-property",
            "installationPath",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    vcvars = Path(install) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    if not install or not vcvars.is_file():
        msg = "MSVC x64 tools not installed"
        raise DemoUnavailableError(msg)
    return vcvars


def _sources(sdk: Path) -> list[Path]:
    demos = sdk / "Demos"
    return [
        demos / "demo_loadBalancing" / "src" / "NetworkLogic.cpp",
        demos / "shared" / "src" / "Console.cpp",
        DRIVER,
    ]


def build_demo() -> Path:
    """Build the demo if missing or older than its sources.

    Returns:
        Path to the executable.

    Raises:
        DemoUnavailableError: Missing SDK/toolchain, or the build failed.
    """
    sdk = find_sdk()
    sources = _sources(sdk)
    if EXE.is_file() and all(
        EXE.stat().st_mtime > src.stat().st_mtime for src in [*sources, Path(__file__)]
    ):
        return EXE
    vcvars = _find_vcvars()
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    network_logic = sources[0].read_text(encoding="utf-8")
    patched, count = _APP_ID_LINE.subn(_APP_ID_FROM_ENV, network_logic)
    if count != 1:
        msg = f"couldn't find the appID line in {sources[0]}"
        raise DemoUnavailableError(msg)
    patched_path = BUILD_DIR / "NetworkLogic.cpp"
    patched_path.write_text(patched, encoding="utf-8")

    demos = sdk / "Demos"
    libs = [
        sdk / sub / "lib" / f"{sub}_vc17_release_windows_mt_x64.lib"
        for sub in ("Common-cpp", "Photon-cpp", "LoadBalancing-cpp")
    ]
    cl = [
        "cl",
        "/nologo",
        "/EHsc",
        "/O2",
        "/MT",
        "/W0",
        "/D_EG_WINDOWS_PLATFORM",
        "/DUNICODE",
        "/D_UNICODE",
        f'/I"{demos / "demo_loadBalancing" / "inc"}"',
        f'/I"{demos / "shared" / "inc"}"',
        f'/I"{sdk}"',
        f'/Fo"{BUILD_DIR}\\\\"',
        f'/Fe"{EXE}"',
        f'"{patched_path}"',
        *(f'"{src}"' for src in sources[1:]),
        "/link",
        *(f'"{lib}"' for lib in libs),
        "ws2_32.lib",
        "advapi32.lib",
        "crypt32.lib",
        "bcrypt.lib",
        "iphlpapi.lib",
        "winhttp.lib",
    ]
    result = subprocess.run(  # noqa: S602 -- vcvars needs cmd; all args are ours
        f'call "{vcvars}" >nul && {" ".join(cl)}',
        shell=True,
        capture_output=True,
        text=True,
        cwd=BUILD_DIR,
        check=False,
    )
    if result.returncode != 0 or not EXE.is_file():
        msg = f"demo build failed:\n{result.stdout}\n{result.stderr}"
        raise DemoUnavailableError(msg)
    return EXE


if __name__ == "__main__":
    sys.stdout.write(f"{build_demo()}\n")
