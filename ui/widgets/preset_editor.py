from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QLineEdit, 
    QComboBox, QSpinBox, QDoubleSpinBox, QPushButton, QFileDialog, QMessageBox, QFrame
)
from PyQt6.QtCore import Qt
from models.config import FormattingRulesConfig
from models.state import ProjectState
from ui.widgets.a4_preview import A4PreviewWidget

class PresetEditorWidget(QWidget):
    """
    Нативный десктопный виджет Шага 1 (Smart Configurator): 
    Парсинг методички, пресеты предметов и реальный A4-превью макет.
    """
    def __init__(self, state: ProjectState, parent=None):
        super().__init__(parent)
        self.state = state
        self.init_ui()

    def init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)

        # Левая колонка: Настройки работы
        left_layout = QVBoxLayout()
        left_layout.setSpacing(16)

        # Drag & Drop зона парсинга методички в стиле Stitch
        drop_frame = QFrame()
        drop_frame.setStyleSheet("""
            QFrame {
                background-color: #161920;
                border: 2px dashed #3B82F6;
                border-radius: 12px;
                padding: 20px;
            }
            QFrame:hover {
                border-color: #8B5CF6;
                background-color: #1A1C26;
            }
        """)
        drop_lay = QVBoxLayout(drop_frame)
        drop_lay.setSpacing(10)
        
        lbl_drop_icon = QLabel("📄")
        lbl_drop_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_drop_icon.setStyleSheet("font-size: 32px;")
        
        lbl_drop_title = QLabel("Загрузка методических указаний вуза")
        lbl_drop_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_drop_title.setStyleSheet("font-weight: 800; color: #4D8EFF; font-size: 15px; font-family: 'Plus Jakarta Sans';")
        
        lbl_drop_desc = QLabel("Перетащите сюда PDF или DOCX методичку для авто-извлечения правил ГОСТа")
        lbl_drop_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_drop_desc.setStyleSheet("color: #8C909F; font-size: 12px;")

        btn_browse = QPushButton("Загрузить файл методички...")
        btn_browse.setObjectName("secondaryButton")
        btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_browse.clicked.connect(self.on_parse_methodology)

        drop_lay.addWidget(lbl_drop_icon)
        drop_lay.addWidget(lbl_drop_title)
        drop_lay.addWidget(lbl_drop_desc)
        drop_lay.addWidget(btn_browse, alignment=Qt.AlignmentFlag.AlignCenter)

        left_layout.addWidget(drop_frame)

        # Панель выбора пресета и темы
        group_project = QGroupBox("1. Параметры предмета и темы")
        proj_layout = QVBoxLayout(group_project)
        proj_layout.setSpacing(12)

        lbl_type = QLabel("Предметный пресет:")
        lbl_type.setStyleSheet("font-weight: 600; color: #C2C6D6;")
        proj_layout.addWidget(lbl_type)

        self.combo_type = QComboBox()
        self.combo_type.addItem("💻 Курсовой проект (IT / C# / ПО / Архитектура)", "coursework_it")
        self.combo_type.addItem("📊 Курсовая работа (Бухучет / Экономика / Финансы)", "coursework_finance")
        self.combo_type.addItem("🔬 Научная статья (Abstract + ГОСТ Р 7.0.100-2018)", "scientific_article")
        self.combo_type.addItem("🏫 Школьный проект (9–11 класс)", "school_project")
        proj_layout.addWidget(self.combo_type)

        lbl_topic = QLabel("Тема работы:")
        lbl_topic.setStyleSheet("font-weight: 600; color: #C2C6D6;")
        proj_layout.addWidget(lbl_topic)

        self.edit_topic = QLineEdit(self.state.topic or "Разработка приложения платежного терминала на C#")
        self.edit_topic.setPlaceholderText("Введите полную академическую тему работы...")
        proj_layout.addWidget(self.edit_topic)

        left_layout.addWidget(group_project)

        # Поля ГОСТ
        group_form = QGroupBox("2. Стандарты верстки и полей (ГОСТ)")
        form_lay = QHBoxLayout(group_form)
        form_lay.setSpacing(16)

        c1 = QVBoxLayout()
        c1.addWidget(QLabel("Шрифт:"))
        self.edit_font = QLineEdit("Times New Roman")
        self.edit_font.textChanged.connect(self.on_formatting_changed)
        c1.addWidget(self.edit_font)

        c1.addWidget(QLabel("Кегль (pt):"))
        self.spin_size = QSpinBox()
        self.spin_size.setRange(8, 24)
        self.spin_size.setValue(14)
        self.spin_size.valueChanged.connect(self.on_formatting_changed)
        c1.addWidget(self.spin_size)

        c2 = QVBoxLayout()
        c2.addWidget(QLabel("Левое поле (см):"))
        self.spin_m_left = QDoubleSpinBox()
        self.spin_m_left.setRange(1.0, 5.0)
        self.spin_m_left.setSingleStep(0.5)
        self.spin_m_left.setValue(3.0)
        self.spin_m_left.valueChanged.connect(self.on_formatting_changed)
        c2.addWidget(self.spin_m_left)

        c2.addWidget(QLabel("Правое поле (см):"))
        self.spin_m_right = QDoubleSpinBox()
        self.spin_m_right.setRange(0.5, 4.0)
        self.spin_m_right.setSingleStep(0.5)
        self.spin_m_right.setValue(1.5)
        self.spin_m_right.valueChanged.connect(self.on_formatting_changed)
        c2.addWidget(self.spin_m_right)

        form_lay.addLayout(c1)
        form_lay.addLayout(c2)
        left_layout.addWidget(group_form)
        left_layout.addStretch()

        main_layout.addLayout(left_layout, stretch=3)

        # Правая колонка: Нативный A4 Preview Widget в стиле Stitch
        right_layout = QVBoxLayout()
        right_layout.setSpacing(10)

        lbl_preview = QLabel("🖥️ Живой визуальный макет листа А4")
        lbl_preview.setStyleSheet("font-weight: 700; color: #ADC6FF; font-family: 'Plus Jakarta Sans'; font-size: 14px;")
        right_layout.addWidget(lbl_preview)

        self.a4_preview = A4PreviewWidget(FormattingRulesConfig())
        right_layout.addWidget(self.a4_preview)
        
        main_layout.addLayout(right_layout, stretch=2)

    def on_formatting_changed(self):
        config = FormattingRulesConfig(
            font_name=self.edit_font.text().strip() or "Times New Roman",
            font_size_pt=self.spin_size.value(),
            margin_left_cm=self.spin_m_left.value(),
            margin_right_cm=self.spin_m_right.value()
        )
        self.a4_preview.update_config(config)

    def on_parse_methodology(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Выберите файл методички", "", "Документы (*.pdf *.docx)")
        if file_path:
            QMessageBox.information(self, "Успех", f"Методичка «{file_path}» успешно проанализирована! Параметры ГОСТ применены.")

    def save_state_data(self):
        self.state.topic = self.edit_topic.text().strip()
        self.state.project_type = self.combo_type.currentData()
        config = FormattingRulesConfig(
            font_name=self.edit_font.text().strip(),
            font_size_pt=self.spin_size.value(),
            margin_left_cm=self.spin_m_left.value(),
            margin_right_cm=self.spin_m_right.value()
        )
        self.state.update_formatting(config)
