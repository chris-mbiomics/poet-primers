"""Build the standalone PyPrimer binary for the current OS.

    pip install '.[server,desktop,bundle]'
    python packaging/build.py

Linux   -> dist/PyPrimer-<version>-x86_64.AppImage
Windows -> dist/PyPrimer-<version>-windows-x64.exe
macOS   -> dist/PyPrimer-<version>-macos-<arch>.dmg

Prints the path of the finished file on the last line.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"
BUILD = ROOT / "build"
DIST = ROOT / "dist"

APPIMAGETOOL_VERSION = "1.9.1"
APPIMAGETOOL_URL = (
    "https://github.com/AppImage/appimagetool/releases/download/"
    f"{APPIMAGETOOL_VERSION}/appimagetool-x86_64.AppImage"
)
APPIMAGETOOL_SHA256 = "ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"


def run(*command: str | Path, **kwargs: object) -> None:
    print("+", " ".join(str(c) for c in command), flush=True)
    subprocess.run([str(c) for c in command], check=True, **kwargs)  # type: ignore[call-overload]


def version() -> str:
    from pyprimer import __version__

    return __version__


def pyinstaller() -> None:
    run(
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        DIST / "pyinstaller",
        "--workpath",
        BUILD / "pyinstaller",
        PACKAGING / "pyprimer.spec",
    )


def appimagetool() -> Path:
    found = shutil.which("appimagetool")
    if found:
        return Path(found)
    tool = BUILD / f"appimagetool-{APPIMAGETOOL_VERSION}-x86_64.AppImage"
    if not tool.exists():
        BUILD.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {APPIMAGETOOL_URL}", flush=True)
        with urllib.request.urlopen(APPIMAGETOOL_URL, timeout=120) as response:
            data = response.read()
        digest = hashlib.sha256(data).hexdigest()
        if digest != APPIMAGETOOL_SHA256:
            raise SystemExit(f"appimagetool checksum mismatch: {digest}")
        tool.write_bytes(data)
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    return tool


def build_linux() -> Path:
    appdir = BUILD / "PyPrimer.AppDir"
    shutil.rmtree(appdir, ignore_errors=True)
    shutil.copytree(DIST / "pyinstaller" / "pyprimer", appdir / "usr" / "bin")
    shutil.copy2(PACKAGING / "linux" / "AppRun", appdir / "AppRun")
    (appdir / "AppRun").chmod(0o755)
    shutil.copy2(PACKAGING / "linux" / "pyprimer.desktop", appdir / "pyprimer.desktop")
    shutil.copy2(PACKAGING / "icons" / "pyprimer.png", appdir / "pyprimer.png")
    icons = appdir / "usr" / "share" / "icons" / "hicolor" / "512x512" / "apps"
    icons.mkdir(parents=True)
    shutil.copy2(PACKAGING / "icons" / "pyprimer.png", icons / "pyprimer.png")
    (appdir / ".DirIcon").symlink_to("pyprimer.png")

    machine = platform.machine()
    target = DIST / f"PyPrimer-{version()}-{machine}.AppImage"
    target.unlink(missing_ok=True)
    # Extract-and-run lets appimagetool work without FUSE (e.g. in CI containers).
    env = {**os.environ, "ARCH": machine, "APPIMAGE_EXTRACT_AND_RUN": "1"}
    run(appimagetool(), "--no-appstream", appdir, target, env=env)
    return target


def build_windows() -> Path:
    target = DIST / f"PyPrimer-{version()}-windows-x64.exe"
    target.unlink(missing_ok=True)
    shutil.move(DIST / "pyinstaller" / "PyPrimer.exe", target)
    return target


def build_macos() -> Path:
    arch = platform.machine()
    target = DIST / f"PyPrimer-{version()}-macos-{arch}.dmg"
    target.unlink(missing_ok=True)
    run(
        "hdiutil",
        "create",
        "-volname",
        "PyPrimer",
        "-srcfolder",
        DIST / "pyinstaller" / "PyPrimer.app",
        "-format",
        "UDZO",
        "-ov",
        target,
    )
    return target


def main() -> None:
    DIST.mkdir(exist_ok=True)
    pyinstaller()
    if sys.platform == "win32":
        target = build_windows()
    elif sys.platform == "darwin":
        target = build_macos()
    else:
        target = build_linux()
    print(f"Built {target} ({target.stat().st_size / 1e6:.0f} MB)")
    print(target)


if __name__ == "__main__":
    main()
