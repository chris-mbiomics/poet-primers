# PyInstaller spec for the standalone POET binaries. Run via packaging/build.py.
#
#   Linux:   onedir build, turned into an AppImage by build.py (Qt WebEngine bundled)
#   Windows: single-file poet-primers.exe (system WebView2 via pythonnet)
#   macOS:   POET.app bundle (system WKWebView), wrapped in a .dmg by build.py
#
# All three are console programs, so the full command line keeps working; started
# without arguments they open the desktop window (see poet_primers.cli.main).
# ruff: noqa
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

from poet_primers import __version__

HERE = Path(SPECPATH)  # noqa: F821 (provided by PyInstaller)
ICONS = HERE / "icons"
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

datas = collect_data_files("poet_primers")  # templates, static assets, REBASE data
# pywebview's injected JS is needed everywhere; its .NET DLLs only on Windows.
datas += collect_data_files("webview", excludes=[] if IS_WIN else ["lib"])

hiddenimports = ["waitress"]
excludes = ["tkinter", "gi", "webview.platforms.gtk", "webview.platforms.cef",
            "webview.platforms.android", "pytest", "mypy", "ruff", "PyInstaller"]
if IS_WIN:
    hiddenimports += ["webview.platforms.winforms", "webview.platforms.edgechromium", "clr"]
    excludes += ["webview.platforms.qt", "webview.platforms.cocoa", "qtpy", "PyQt6", "PySide6"]
elif IS_MAC:
    hiddenimports += ["webview.platforms.cocoa"]
    excludes += ["webview.platforms.qt", "webview.platforms.winforms", "qtpy", "PyQt6",
                 "PySide6", "clr"]
else:
    hiddenimports += [
        "webview.platforms.qt",
        "qtpy",
        "PyQt6.QtWebEngineWidgets",
        "PyQt6.QtWebEngineCore",
        "PyQt6.QtWebChannel",
        "PyQt6.QtNetwork",
    ]
    excludes += [
        "webview.platforms.winforms", "webview.platforms.cocoa", "clr", "PySide6", "PyQt5",
        # Qt modules pywebview never touches; keeps the AppImage smaller.
        "PyQt6.Qt3DCore", "PyQt6.Qt3DRender", "PyQt6.Qt3DInput", "PyQt6.Qt3DLogic",
        "PyQt6.Qt3DAnimation", "PyQt6.Qt3DExtras", "PyQt6.QtBluetooth", "PyQt6.QtCharts",
        "PyQt6.QtDataVisualization", "PyQt6.QtDesigner", "PyQt6.QtHelp",
        "PyQt6.QtMultimedia", "PyQt6.QtMultimediaWidgets", "PyQt6.QtNfc",
        "PyQt6.QtPositioning", "PyQt6.QtQuick3D", "PyQt6.QtRemoteObjects",
        "PyQt6.QtSensors", "PyQt6.QtSerialPort", "PyQt6.QtSpatialAudio", "PyQt6.QtSql",
        "PyQt6.QtTest", "PyQt6.QtTextToSpeech", "PyQt6.QtWebSockets", "PyQt6.QtBluetooth",
        "PyQt6.QtPdf", "PyQt6.QtPdfWidgets", "PyQt6.QtSvgWidgets",
    ]

a = Analysis(  # noqa: F821
    [str(HERE / "launcher.py")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
if not (IS_WIN or IS_MAC):
    import re

    # Qt hooks collect far more than a QWebEngineView needs (QML modules, Quick3D,
    # Multimedia, ...). WebEngineCore itself links Quick/Qml/Positioning, so those stay.
    # The GTK3 platform theme is dropped too: bundling GTK clashes with host themes.
    _UNUSED_QT = re.compile(
        r"libQt6(Quick3D\w*|QuickControls2\w*|QuickDialogs2\w*|QuickEffects|QuickLayouts"
        r"|QuickParticles|QuickShapes|QuickTemplates2|QuickTest|QuickTimeline\w*"
        r"|QuickVectorImage\w*|Multimedia\w*|Sensors\w*|SpatialAudio|TextToSpeech"
        r"|RemoteObjects\w*|Test|ShaderTools|StateMachine\w*|WebEngineQuick\w*"
        r"|WebChannelQuick|WebSockets|PositioningQuick)\.so"
    )

    def _keep(entry):
        dest = entry[0].replace("\\", "/")
        name = dest.rsplit("/", 1)[-1]
        if "PyQt6/Qt6/qml/" in dest or dest.endswith("platformthemes/libqgtk3.so"):
            return False
        if name.startswith(("libgtk-3.so", "libgdk-3.so")) or _UNUSED_QT.match(name):
            return False
        if "PyQt6/Qt6/translations/" in dest:  # UI is English; keep WebEngine's en-US pack
            return dest.endswith("qtwebengine_locales/en-US.pak")
        return True

    a.binaries = [e for e in a.binaries if _keep(e)]
    a.datas = [e for e in a.datas if _keep(e)]

pyz = PYZ(a.pure)  # noqa: F821
# UTF-8 mode: reports print Unicode (°, Δ) regardless of the console/locale code page.
options = [("X utf8_mode=1", None, "OPTION")]

icon = str(ICONS / ("poet-primers.ico" if IS_WIN else "poet-primers.icns" if IS_MAC else "poet-primers.png"))

if IS_WIN:
    exe = EXE(  # noqa: F821
        pyz,
        a.scripts,
        options,
        a.binaries,
        a.datas,
        name="poet-primers",
        icon=icon,
        console=True,
        upx=False,
    )
else:
    exe = EXE(  # noqa: F821
        pyz,
        a.scripts,
        options,
        exclude_binaries=True,
        name="poet-primers",
        icon=icon,
        console=True,
        upx=False,
    )
    coll = COLLECT(exe, a.binaries, a.datas, name="poet-primers", upx=False)  # noqa: F821
    if IS_MAC:
        app = BUNDLE(  # noqa: F821
            coll,
            name="POET.app",
            icon=icon,
            bundle_identifier="org.poet-primers.poet",
            version=__version__,
            info_plist={
                "CFBundleName": "POET",
                "CFBundleDisplayName": "POET",
                "CFBundleShortVersionString": __version__,
                "NSHighResolutionCapable": True,
            },
        )
