from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    source_root = Path(__file__).resolve().parent / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    from papercraft.ui.app import main as desktop_main

    return desktop_main()


if __name__ == "__main__":
    raise SystemExit(main())
