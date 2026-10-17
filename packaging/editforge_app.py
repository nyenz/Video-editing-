"""Start point of EditForge.exe (the desktop app)."""
import multiprocessing
import sys

from editforge.desktop import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
