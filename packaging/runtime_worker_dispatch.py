"""Dispatch the background worker inside the frozen application executable.

The desktop intentionally starts its worker through the current interpreter.
In a PyInstaller build that interpreter is the application executable, so the
runtime hook handles the worker arguments before the GUI entry point runs.
"""

import sys

if sys.argv[1:2] == ["--papercraft-worker"]:
    from papercraft.worker.cli import main

    raise SystemExit(main(sys.argv[2:]))
