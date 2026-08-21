from __future__ import annotations

import logging
import sys
import traceback
from types import TracebackType


def main() -> int:
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "PySide6 is not installed. Install PaperCraft with the 'desktop' dependency group."
        ) from exc

    from .main_window import MainWindow

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    application = QApplication(sys.argv)
    application.setApplicationName("PaperCraft AI Studio")
    application.setOrganizationName("PaperCraftAI")

    def exception_hook(
        exception_type: type[BaseException], value: BaseException, tb: TracebackType | None
    ) -> None:
        details = "".join(traceback.format_exception(exception_type, value, tb))
        logging.getLogger("papercraft.ui").error(details)
        dialog = QMessageBox(QMessageBox.Icon.Critical, "Ошибка PaperCraft", str(value))
        dialog.setDetailedText(details)
        dialog.exec()

    sys.excepthook = exception_hook
    window = MainWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
