from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QListWidget, QListWidgetItem,
    QTextEdit, QPushButton, QMessageBox, QFrame, QSplitter
)
from PyQt6.QtCore import Qt
from models.state import ProjectState
from core.gemini_engine import ContentGenerator
from core.blueprint import BlueprintManager

class ContentEditorWidget(QWidget):
    """
    Нативный десктопный виджет Шага 5 (Студия Написания): 
    3-панельная компоновка Stitch Design System со сквозным Паспортом Проекта.
    """
    def __init__(self, state: ProjectState, parent=None):
        super().__init__(parent)
        self.state = state
        self.generator = ContentGenerator()
        self.blueprint = BlueprintManager(topic=self.state.topic, project_type=self.state.project_type)
        self.init_ui()

    def init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 1. Левая панель: Навигатор по разделам оглавления
        left_panel = QFrame()
        left_panel.setStyleSheet("background-color: #161920; border-radius: 10px; border: 1px solid #262B36;")
        left_lay = QVBoxLayout(left_panel)
        left_lay.setContentsMargins(12, 12, 12, 12)
        left_lay.setSpacing(10)
        
        lbl_nav = QLabel("📋 Разделы работы")
        lbl_nav.setStyleSheet("font-weight: 800; color: #4D8EFF; font-family: 'Plus Jakarta Sans'; font-size: 14px;")
        left_lay.addWidget(lbl_nav)

        self.list_sections = QListWidget()
        self.list_sections.itemClicked.connect(self.on_section_selected)
        left_lay.addWidget(self.list_sections)

        splitter.addWidget(left_panel)

        # 2. Центральная панель: Редактор контента подглав
        center_panel = QFrame()
        center_panel.setStyleSheet("background-color: #161920; border-radius: 10px; border: 1px solid #262B36;")
        center_lay = QVBoxLayout(center_panel)
        center_lay.setContentsMargins(14, 14, 14, 14)
        center_lay.setSpacing(12)

        h_top = QHBoxLayout()
        self.lbl_curr_sec = QLabel("Выберите подглаву слева...")
        self.lbl_curr_sec.setStyleSheet("font-size: 15px; font-weight: 800; color: #FFFFFF; font-family: 'Plus Jakarta Sans';")
        h_top.addWidget(self.lbl_curr_sec)

        h_top.addStretch()

        btn_gen = QPushButton("⚡ ИИ-Генерация текста (Gemini 3.5 Lite)")
        btn_gen.setObjectName("aiButton")
        btn_gen.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_gen.clicked.connect(self.on_generate_section)
        h_top.addWidget(btn_gen)

        center_lay.addLayout(h_top)

        self.text_editor = QTextEdit()
        self.text_editor.setPlaceholderText("Здесь будет сгенерирован или отредактирован академический текст подглавы по ГОСТу...")
        self.text_editor.textChanged.connect(self.on_text_edited)
        center_lay.addWidget(self.text_editor)

        # Нижняя панель с кнопкой Антиплагиата
        h_actions = QHBoxLayout()
        btn_anti_plag = QPushButton("✨ Повысить уникальность выделенного фрагмента (Gemini 3.6 Flash)")
        btn_anti_plag.setObjectName("secondaryButton")
        btn_anti_plag.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_anti_plag.clicked.connect(self.on_anti_plagiarism_click)
        h_actions.addWidget(btn_anti_plag)
        h_actions.addStretch()

        center_lay.addLayout(h_actions)
        splitter.addWidget(center_panel)

        # 3. Правая панель: Инспектор Паспорта Проекта (Context Blueprint)
        right_panel = QFrame()
        right_panel.setStyleSheet("background-color: #161920; border-radius: 10px; border: 1px solid #262B36;")
        right_lay = QVBoxLayout(right_panel)
        right_lay.setContentsMargins(12, 12, 12, 12)
        right_lay.setSpacing(10)

        lbl_bp = QLabel("🧠 Паспорт Проекта (Blueprint)")
        lbl_bp.setStyleSheet("font-weight: 800; color: #8B5CF6; font-family: 'Plus Jakarta Sans'; font-size: 14px;")
        right_lay.addWidget(lbl_bp)

        self.lbl_stats = QLabel("Слов в документе: 0 / 8 000\nЗарегистрировано рисунков: 0\nЗарегистрировано таблиц: 0\nГлоссарий: 0 терминов")
        self.lbl_stats.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px; color: #ADC6FF; background-color: #1A1C20; padding: 10px; border-radius: 6px; border: 1px solid #262B36;")
        right_lay.addWidget(self.lbl_stats)

        right_lay.addWidget(QLabel("Зарегистрированные рисунки и схемы:"))
        self.list_figures = QListWidget()
        right_lay.addWidget(self.list_figures)

        splitter.addWidget(right_panel)

        # Пропорции сплиттера: 220px | 600px | 240px
        splitter.setSizes([220, 600, 240])
        main_layout.addWidget(splitter)

        self.refresh_sections_list()

    def refresh_sections_list(self):
        self.list_sections.clear()
        for item in self.state.plan_structure:
            sec_id = item.get("id")
            title = item.get("title")
            has_content = bool(self.state.sections_content.get(sec_id, "").strip())
            status = "🟢" if has_content else "⚪"
            
            list_item = QListWidgetItem(f"{status} [{sec_id}] {title}")
            list_item.setData(Qt.ItemDataRole.UserRole, sec_id)
            self.list_sections.addItem(list_item)

    def on_section_selected(self, item: QListWidgetItem):
        sec_id = item.data(Qt.ItemDataRole.UserRole)
        self.lbl_curr_sec.setText(f"Подглава [{sec_id}]")
        content = self.state.sections_content.get(sec_id, "")
        self.text_editor.blockSignals(True)
        self.text_editor.setText(content)
        self.text_editor.blockSignals(False)
        self.update_blueprint_stats()

    def on_text_edited(self):
        curr_item = self.list_sections.currentItem()
        if curr_item:
            sec_id = curr_item.data(Qt.ItemDataRole.UserRole)
            self.state.add_section_content(sec_id, self.text_editor.toPlainText())
            self.update_blueprint_stats()

    def update_blueprint_stats(self):
        total_words = sum(len(text.split()) for text in self.state.sections_content.values())
        fig_count = len(self.blueprint.figures_registry)
        tbl_count = len(self.blueprint.tables_registry)
        self.lbl_stats.setText(f"Слов в документе: {total_words} / 8 000\nЗарегистрировано рисунков: {fig_count}\nЗарегистрировано таблиц: {tbl_count}")

    def on_generate_section(self):
        curr_item = self.list_sections.currentItem()
        if not curr_item:
            QMessageBox.warning(self, "Внимание", "Выберите подглаву слева в списке!")
            return
            
        sec_id = curr_item.data(Qt.ItemDataRole.UserRole)
        generated_text = self.generator.generate_paragraph_draft(curr_item.text(), self.blueprint)
        self.text_editor.setText(generated_text)
        self.state.add_section_content(sec_id, generated_text)
        self.refresh_sections_list()
        QMessageBox.information(self, "Успех", "Текст подглавы сгенерирован с соблюдением правил Burstiness & Perplexity!")

    def on_anti_plagiarism_click(self):
        cursor = self.text_editor.textCursor()
        selected = cursor.selectedText()
        if not selected:
            QMessageBox.warning(self, "Внимание", "Выделите мышкой фрагмент текста для рерайта!")
            return
            
        rewritten = self.generator.rewrite_selected_text(selected)
        cursor.insertText(rewritten)
        QMessageBox.information(self, "Антиплагиат", "Фрагмент переписан для 100% уникальности в Антиплагиат.ВУЗ!")

    def save_state_data(self):
        pass
