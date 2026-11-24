"""Entry script frozen by PyInstaller: the regular ``pyprimer`` command line.

Started without arguments (double-click), it opens the desktop window.
"""

import multiprocessing

from pyprimer.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
