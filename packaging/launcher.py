"""Entry script frozen by PyInstaller: the regular ``poet-primers`` command line.

Started without arguments (double-click), it opens the desktop window.
"""

import multiprocessing

from poet_primers.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
