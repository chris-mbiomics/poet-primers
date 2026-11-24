"""Standalone desktop window: the web interface in a native window via pywebview.

The Flask app is served on localhost from a background thread and shown in a
pywebview window using the platform's web engine (WebKitGTK or Qt WebEngine on
Linux, WebView2 on Windows, WKWebView on macOS). Closing the window stops the
server.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any, Literal

from . import __version__

INSTALL_HINT = "install it with: pip install 'pyprimer[desktop]'"


def _data_dir() -> Path:
    """Per-user directory where the window keeps its localStorage (preferences)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "pyprimer" / "webview"


def _gui() -> Literal["qt"] | None:
    """Backend to request from pywebview: Qt on Linux when GTK bindings are missing.

    pywebview tries GTK first on Linux and logs a traceback if PyGObject is absent.
    """
    if sys.platform.startswith("linux") and importlib.util.find_spec("gi") is None:
        if importlib.util.find_spec("qtpy") is not None:
            return "qt"
    return None


class _Api:
    """Methods exposed to the page as ``window.pywebview.api``."""

    def __init__(self, webview: Any) -> None:
        self._webview = webview
        self._window: Any = None

    def save_file(self, filename: str, text: str) -> bool:
        """Ask where to save ``text`` (suggesting ``filename``); False if cancelled."""
        if self._window is None:
            return False
        dialogs = getattr(self._webview, "FileDialog", None)  # pywebview >= 5.4
        kind = dialogs.SAVE if dialogs is not None else self._webview.SAVE_DIALOG
        chosen = self._window.create_file_dialog(kind, save_filename=os.path.basename(filename))
        if not chosen:
            return False
        path = chosen if isinstance(chosen, str) else chosen[0]
        Path(path).write_text(text, encoding="utf-8")
        return True


def run_window(port: int) -> int:
    """Serve the app on ``127.0.0.1:port`` and show it in a desktop window until closed."""
    try:
        import webview
    except ImportError:
        print(f"Error: the desktop window needs pywebview; {INSTALL_HINT}", file=sys.stderr)
        return 1
    from werkzeug.serving import make_server

    from .web import create_app

    app = create_app()
    logging.getLogger("werkzeug").setLevel(logging.WARNING)  # no per-request log lines
    try:
        server = make_server("127.0.0.1", port, app, threaded=True)
    except OSError:
        server = make_server("127.0.0.1", 0, app, threaded=True)
        print(
            f"Port {port} is in use; using {server.port} instead "
            "(preferences saved under the usual port are not available this session)",
            file=sys.stderr,
        )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        api = _Api(webview)
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        api._window = webview.create_window(
            f"PyPrimer {__version__}",
            f"http://127.0.0.1:{server.port}/",
            js_api=api,
            width=1280,
            height=900,
            min_size=(800, 600),
        )
        storage = _data_dir()
        storage.mkdir(parents=True, exist_ok=True)
        webview.start(gui=_gui(), private_mode=False, storage_path=str(storage))
    finally:
        server.shutdown()
        thread.join(timeout=5)
    return 0
