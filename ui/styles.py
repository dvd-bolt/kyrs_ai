def get_app_stylesheet(dark_mode: bool = True) -> str:
    """
    Возвращает премиальную таблицу стилей QSS на базе точной дизайн-системы Stitch 2.0.
    Палитра:
    - Root surface: #0C0E12 / #111317
    - Card surface: #161920 / #1A1C20
    - Hover surface: #282A2E / #333539
    - Borders: #262B36 / #424754
    - Primary Blue: #4D8EFF (Hover: #6DA0FF)
    - Emerald Success: #10B981 (Hover: #34D399)
    - AI Gradient: #8B5CF6 -> #EC4899
    - Text Primary: #E2E2E8, Muted: #8C909F, Code: #ADC6FF
    """
    return """
    QMainWindow, QDialog {
        background-color: #0C0E12;
        color: #E2E2E8;
        font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
        font-size: 13px;
    }

    QWidget {
        background-color: transparent;
        color: #E2E2E8;
        font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
    }

    /* Карточки и Группы */
    QGroupBox {
        background-color: #161920;
        border: 1px solid #262B36;
        border-radius: 10px;
        margin-top: 20px;
        padding: 16px 14px 14px 14px;
        font-family: 'Plus Jakarta Sans', sans-serif;
        font-weight: 700;
        font-size: 13px;
        color: #4D8EFF;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 14px;
        padding: 2px 8px;
        background-color: #161920;
        border: 1px solid #262B36;
        border-radius: 4px;
        color: #ADC6FF;
    }

    /* Метки */
    QLabel {
        color: #E2E2E8;
        font-size: 13px;
    }
    QLabel#mutedLabel {
        color: #8C909F;
        font-size: 12px;
    }
    QLabel#headerTitle {
        font-family: 'Plus Jakarta Sans', sans-serif;
        font-weight: 800;
        font-size: 18px;
        color: #FFFFFF;
    }
    QLabel#badgeLabel {
        background-color: #1E2024;
        border: 1px solid #262B36;
        border-radius: 6px;
        padding: 4px 8px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 11px;
    }

    /* Поля ввода */
    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
        background-color: #1A1C20;
        border: 1px solid #262B36;
        border-radius: 8px;
        padding: 8px 12px;
        color: #F0F0F4;
        font-family: 'Inter', sans-serif;
        font-size: 13px;
        selection-background-color: #4D8EFF;
        selection-color: #001A42;
    }
    QLineEdit:hover, QTextEdit:hover, QComboBox:hover {
        border-color: #424754;
    }
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
        border: 1px solid #4D8EFF;
        background-color: #1E2024;
    }
    QLineEdit[readOnly="true"], QTextEdit[readOnly="true"] {
        background-color: #14161B;
        color: #8C909F;
        border-style: dashed;
    }

    QComboBox::drop-down {
        border: none;
        width: 24px;
        padding-right: 8px;
    }
    QComboBox QAbstractItemView {
        background-color: #1A1C20;
        border: 1px solid #262B36;
        border-radius: 8px;
        selection-background-color: #4D8EFF;
        selection-color: #001A42;
        padding: 4px;
    }

    /* Кнопки */
    QPushButton {
        background-color: #4D8EFF;
        color: #001A42;
        border: none;
        border-radius: 8px;
        padding: 10px 18px;
        font-family: 'Plus Jakarta Sans', sans-serif;
        font-weight: 700;
        font-size: 13px;
    }
    QPushButton:hover {
        background-color: #6DA0FF;
    }
    QPushButton:pressed {
        background-color: #3B72D9;
    }
    QPushButton:disabled {
        background-color: #262B36;
        color: #5C606E;
    }

    /* ИИ Градиентная кнопка */
    QPushButton#aiButton {
        background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0, stop:0 #8B5CF6, stop:1 #EC4899);
        color: #FFFFFF;
        font-family: 'Plus Jakarta Sans', sans-serif;
        font-weight: 800;
        border-radius: 8px;
    }
    QPushButton#aiButton:hover {
        background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0, stop:0 #9D6EFA, stop:1 #F45CA7);
    }
    QPushButton#aiButton:pressed {
        background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0, stop:0 #7C3AED, stop:1 #DB2777);
    }

    /* Изумрудная кнопка успешной сборки */
    QPushButton#accentButton {
        background-color: #10B981;
        color: #002113;
        font-family: 'Plus Jakarta Sans', sans-serif;
        font-weight: 800;
        border-radius: 8px;
    }
    QPushButton#accentButton:hover {
        background-color: #34D399;
    }
    QPushButton#accentButton:pressed {
        background-color: #059669;
    }

    /* Второстепенная контурная кнопка */
    QPushButton#secondaryButton {
        background-color: #1E2024;
        color: #C2C6D6;
        border: 1px solid #262B36;
        border-radius: 8px;
    }
    QPushButton#secondaryButton:hover {
        background-color: #282A2E;
        color: #FFFFFF;
        border-color: #424754;
    }

    /* Таблицы, списки и деревья */
    QTreeWidget, QListWidget, QTableWidget {
        background-color: #161920;
        border: 1px solid #262B36;
        border-radius: 8px;
        gridline-color: #262B36;
        color: #E2E2E8;
        padding: 4px;
        outline: none;
    }
    QTreeWidget::item, QListWidget::item {
        padding: 8px 10px;
        border-radius: 6px;
        margin-bottom: 2px;
    }
    QTreeWidget::item:hover, QListWidget::item:hover {
        background-color: #20232A;
        color: #FFFFFF;
    }
    QTreeWidget::item:selected, QListWidget::item:selected {
        background-color: #1E2B45;
        color: #6DA0FF;
        border: 1px solid #3B82F6;
        font-weight: 600;
    }
    QHeaderView::section {
        background-color: #14161B;
        color: #ADC6FF;
        padding: 8px 12px;
        border: none;
        border-bottom: 1px solid #262B36;
        font-family: 'JetBrains Mono', monospace;
        font-size: 11px;
        font-weight: 700;
        text-transform: uppercase;
    }

    /* Сплиттер разделитель */
    QSplitter::handle {
        background-color: #262B36;
        width: 3px;
        height: 3px;
    }
    QSplitter::handle:hover {
        background-color: #4D8EFF;
    }

    /* Скроллбары */
    QScrollBar:vertical {
        border: none;
        background: #111317;
        width: 8px;
        margin: 0px;
        border-radius: 4px;
    }
    QScrollBar::handle:vertical {
        background: #262B36;
        min-height: 20px;
        border-radius: 4px;
    }
    QScrollBar::handle:vertical:hover {
        background: #4D8EFF;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0px;
    }

    QScrollBar:horizontal {
        border: none;
        background: #111317;
        height: 8px;
        margin: 0px;
        border-radius: 4px;
    }
    QScrollBar::handle:horizontal {
        background: #262B36;
        min-width: 20px;
        border-radius: 4px;
    }
    QScrollBar::handle:horizontal:hover {
        background: #4D8EFF;
    }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
        width: 0px;
    }

    /* Чекбоксы и радиокнопки */
    QCheckBox, QRadioButton {
        color: #E2E2E8;
        spacing: 8px;
        font-weight: 500;
    }
    QCheckBox::indicator, QRadioButton::indicator {
        width: 18px;
        height: 18px;
        border: 1px solid #262B36;
        border-radius: 4px;
        background-color: #1A1C20;
    }
    QCheckBox::indicator:hover, QRadioButton::indicator:hover {
        border-color: #4D8EFF;
    }
    QCheckBox::indicator:checked {
        background-color: #4D8EFF;
        border-color: #4D8EFF;
    }

    /* Всплывающие подсказки */
    QToolTip {
        background-color: #1E2024;
        color: #E2E2E8;
        border: 1px solid #262B36;
        border-radius: 6px;
        padding: 6px 10px;
        font-size: 12px;
    }
    """
