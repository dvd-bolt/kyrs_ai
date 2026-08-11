import sys
import logging
import traceback
from PyQt6.QtWidgets import QApplication, QMessageBox
from ui.main_window import MainWindow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("CourseworkApp")

def setup_exception_hook():
    """
    Перехватывает исключения Python и отображает диалог ошибки.
    """
    def custom_excepthook(exctype, value, tb):
        error_msg = "".join(traceback.format_exception(exctype, value, tb))
        logger.error(f"Критическое исключение приложения:\n{error_msg}")
        
        msg_box = QMessageBox()
        msg_box.setIcon(QMessageBox.Icon.Critical)
        msg_box.setWindowTitle("Критическая ошибка приложения")
        msg_box.setText("Произошел сбой при работе нативного интерфейса.")
        msg_box.setDetailedText(error_msg)
        msg_box.exec()
        
    sys.excepthook = custom_excepthook

def main():
    logger.info("Запуск ЧИСТОГО НАТИВНОГО PyQt6 ДЕСКТОРНОГО ПРИЛОЖЕНИЯ (Stitch Design System)...")
    setup_exception_hook()
    
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
