"""Hide the console window of the frozen Windows executable when it was double-clicked.

The bundled ``PyPrimer.exe`` is a console program so that the command line keeps
working (stdout/stderr, the shell waiting for it). When it is started from Explorer,
Windows creates a new console that only this process is attached to; in that case
the console is hidden and only the PyPrimer window remains. Started from cmd or
PowerShell, the console is shared with the shell and left alone.
"""

from __future__ import annotations

import sys


def hide_if_owned() -> bool:
    """Hide the console if this process is its only user; True if it was hidden."""
    if sys.platform != "win32":
        return False
    import ctypes

    kernel32 = ctypes.windll.kernel32
    processes = (ctypes.c_uint32 * 2)()
    if kernel32.GetConsoleProcessList(processes, 2) != 1:
        return False
    console = kernel32.GetConsoleWindow()
    if not console:
        return False
    ctypes.windll.user32.ShowWindow(console, 0)  # SW_HIDE
    return True
