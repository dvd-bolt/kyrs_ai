"""PyInstaller entry point that preserves the ``papercraft`` package context."""

from papercraft.ui.app import main

if __name__ == "__main__":
    raise SystemExit(main())
