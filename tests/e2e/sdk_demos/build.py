"""Build the official Photon Windows C++ SDK demos for interop tests.

Each demo is copied into ``build/sdk_demos/sdk/Demos`` next to junctions to
the SDK's library folders, so its own vc17 project builds unchanged with
MSBuild (just on the installed toolset). Only the copy is edited:

* every ``L"<no-app-id>"`` placeholder reads ``PHOTON_APP_ID`` instead
  (``PHOTON_CHAT_APP_ID`` for the chat demo);
* ``demo_basics`` / ``demo_typeSupport`` get an unbuffered stdout, as their
  ``wprintf`` output would otherwise sit in a pipe buffer until exit;
* ``demo_loadBalancing``'s keyboard main is swapped for
  ``loadbalancing_driver.cpp``, which takes its menu keys from piped stdin.

Needs Windows, MSVC (found via ``vswhere``) and the SDK, looked up in
``$PHOTON_CPP_SDK`` or ``sdks/photon-windows-sdk_*``. Build them all without
running tests with::

    python tests/e2e/sdk_demos/build.py [demo ...]
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BUILD_DIR = REPO / "build" / "sdk_demos"
SDK_COPY = BUILD_DIR / "sdk"

CONFIGURATION = "release_windows_mt"
PLATFORM = "x64"
_LIB_DIRS = ("Common-cpp", "Photon-cpp", "LoadBalancing-cpp", "Chat-cpp")
_PLACEHOLDER = 'L"<no-app-id>"'
_UNBUFFER = (
    "#include <stdio.h>\n"
    "static const int egUnbufferedStdout = setvbuf(stdout, NULL, _IONBF, 0);\n"
)
_VSWHERE = (
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    / "Microsoft Visual Studio"
    / "Installer"
    / "vswhere.exe"
)


@dataclass(frozen=True, slots=True)
class Demo:
    """One SDK demo: where its project is and how to adapt the copy."""

    name: str
    project: str
    """vc17 project, relative to ``Demos``."""
    app_id_env: dict[str, str] = field(default_factory=dict)
    """Source file (relative to ``Demos``) -> env var replacing its placeholder."""
    replace: dict[str, Path] = field(default_factory=dict)
    """Source file (relative to ``Demos``) -> file to put in its place."""
    unbuffer: str | None = None
    """Source file (relative to ``Demos``) to make stdout unbuffered from."""


DEMOS = {
    demo.name: demo
    for demo in (
        Demo(
            "demo_basics",
            "demo_basics/windows/demo_windows_basics_vc17.vcxproj",
            {"demo_basics/src/Photon_lib.cpp": "PHOTON_APP_ID"},
            unbuffer="demo_basics/src/StdIO_main.cpp",
        ),
        Demo(
            "demo_typeSupport",
            "demo_typeSupport/windows/demo_windows_typeSupport_vc17.vcxproj",
            {"demo_typeSupport/src/Photon_lib.cpp": "PHOTON_APP_ID"},
            unbuffer="demo_typeSupport/src/StdIO_main.cpp",
        ),
        Demo(
            "demo_loadBalancing",
            "demo_loadBalancing/windows/demo_windows_loadBalancing_vc17.vcxproj",
            {"demo_loadBalancing/src/NetworkLogic.cpp": "PHOTON_APP_ID"},
            {
                "demo_loadBalancing/src/StdIO_main.cpp": HERE
                / "loadbalancing_driver.cpp"
            },
        ),
        Demo(
            "demo_particle",
            "demo_particle/console/windows/demo_windows_particle_console_vc17.vcxproj",
            {"demo_particle/src/DemoConstants.h": "PHOTON_APP_ID"},
        ),
        Demo(
            "demo_chat",
            "demo_chat/console/windows/demo_windows_chat_console_vc17.vcxproj",
            {"demo_chat/src/DemoConstants.h": "PHOTON_CHAT_APP_ID"},
        ),
    )
}
"""Every SDK demo with a Windows console build, minus audio: ``demo_voice_basics``
is left out for now, and ``demo_memory`` only ships a cocos2d-x frontend."""


class DemoUnavailableError(RuntimeError):
    """A demo can't be built here; tests skip with this message."""


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
        if (sdk / "Demos").is_dir():
            return sdk
    msg = "Photon Windows C++ SDK not found (set PHOTON_CPP_SDK)"
    raise DemoUnavailableError(msg)


def _find_msbuild() -> tuple[Path, str]:
    if sys.platform != "win32":
        msg = "the C++ SDK demos only build on Windows"
        raise DemoUnavailableError(msg)
    if not _VSWHERE.is_file():
        msg = "Visual Studio / Build Tools not found"
        raise DemoUnavailableError(msg)
    found = subprocess.run(  # noqa: S603 -- fixed path from Program Files
        [
            str(_VSWHERE),
            "-latest",
            "-products",
            "*",
            "-requires",
            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            "-find",
            r"MSBuild\**\Bin\MSBuild.exe",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    if not found:
        msg = "MSVC x64 tools not installed"
        raise DemoUnavailableError(msg)
    msbuild = Path(found[0])
    # The projects pin v143; build with whatever toolset is installed (the
    # SDK's vc17 libs are ABI-compatible with every v14x toolset).
    toolsets = sorted(
        (msbuild.parents[2] / "Microsoft" / "VC").glob(
            "v1*/Platforms/x64/PlatformToolsets/v14*"
        )
    )
    if not toolsets:
        msg = "no v14x platform toolset found"
        raise DemoUnavailableError(msg)
    return msbuild, toolsets[-1].name


def _junction(link: Path, target: Path) -> None:
    if link.exists():
        return
    subprocess.run(  # noqa: S603 -- fixed command, our own paths
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],  # noqa: S607
        capture_output=True,
        check=True,
    )


def _stage(sdk: Path, demo: Demo) -> None:
    """Refresh the demo's copy (and the shared sources) under ``SDK_COPY``."""
    SDK_COPY.mkdir(parents=True, exist_ok=True)
    for lib in _LIB_DIRS:
        if (sdk / lib).is_dir():
            _junction(SDK_COPY / lib, sdk / lib)
    for sub in ("shared", demo.name):
        shutil.copytree(
            sdk / "Demos" / sub, SDK_COPY / "Demos" / sub, dirs_exist_ok=True
        )
    for rel, env_var in demo.app_id_env.items():
        path = SDK_COPY / "Demos" / rel
        source = (sdk / "Demos" / rel).read_text(encoding="utf-8-sig")
        if _PLACEHOLDER not in source:
            msg = f"no app id placeholder in {rel}"
            raise DemoUnavailableError(msg)
        source = source.replace(_PLACEHOLDER, f'_wgetenv(L"{env_var}")')
        if "<stdlib.h>" not in source:
            source = "#include <stdlib.h>\n" + source
        path.write_text(source, encoding="utf-8")
    for rel, replacement in demo.replace.items():
        shutil.copyfile(replacement, SDK_COPY / "Demos" / rel)
    if demo.unbuffer:
        path = SDK_COPY / "Demos" / demo.unbuffer
        source = (sdk / "Demos" / demo.unbuffer).read_text(encoding="utf-8-sig")
        path.write_text(_UNBUFFER + source, encoding="utf-8")


def exe_path(demo: Demo) -> Path:
    """Where MSBuild puts the demo's executable.

    Returns:
        The path (which may not exist yet).
    """
    project_dir = (SDK_COPY / "Demos" / demo.project).parent
    return (
        project_dir
        / f"vc17_{CONFIGURATION}_{PLATFORM}"
        / f"{Path(demo.project).stem}.exe"
    )


def _inputs(sdk: Path, demo: Demo) -> list[Path]:
    roots = [sdk / "Demos" / demo.name, sdk / "Demos" / "shared"]
    files = [
        p for root in roots for p in root.rglob("*") if p.suffix in {".cpp", ".h", ".c"}
    ]
    return [*files, *demo.replace.values(), Path(__file__)]


def build_demo(name: str) -> Path:
    """Build one demo if missing or older than its sources.

    Returns:
        Path to the executable.

    Raises:
        DemoUnavailableError: Missing SDK/toolchain, or the build failed.
    """
    demo = DEMOS[name]
    sdk = find_sdk()
    exe = exe_path(demo)
    if exe.is_file():
        built = exe.stat().st_mtime
        if all(src.stat().st_mtime < built for src in _inputs(sdk, demo)):
            return exe
    msbuild, toolset = _find_msbuild()
    _stage(sdk, demo)
    for project in (demo.project,):
        result = subprocess.run(  # noqa: S603 -- MSBuild from vswhere, our project
            [
                str(msbuild),
                str(SDK_COPY / "Demos" / project),
                "/nologo",
                "/m",
                "/v:minimal",
                f"/p:Configuration={CONFIGURATION}",
                f"/p:Platform={PLATFORM}",
                f"/p:PlatformToolset={toolset}",
                # The projects pin Windows SDK 10.0.10240; "10.0" means latest.
                "/p:WindowsTargetPlatformVersion=10.0",
                "/p:TreatWarningAsError=false",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            msg = (
                f"{name}: building {project} failed:\n{result.stdout}\n{result.stderr}"
            )
            raise DemoUnavailableError(msg)
    if not exe.is_file():
        msg = f"{name}: build succeeded but {exe} is missing"
        raise DemoUnavailableError(msg)
    return exe


if __name__ == "__main__":
    failed = False
    for demo_name in sys.argv[1:] or DEMOS:
        try:
            sys.stdout.write(f"{demo_name}: {build_demo(demo_name)}\n")
        except DemoUnavailableError as exc:
            failed = True
            sys.stdout.write(f"{exc}\n")
    sys.exit(failed)
