import os
import sys
import subprocess
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget, 
    QPushButton, QLabel, QMessageBox, QFrame, QGraphicsDropShadowEffect
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from models.state import ProjectState
from models.config import FormattingRulesConfig, TitlePageData
from core.renderer import DocxRenderer
from ui.styles import get_app_stylesheet

from ui.widgets.preset_editor import PresetEditorWidget
from ui.widgets.knowledge_base import KnowledgeBaseWidget
from ui.widgets.title_page_editor import TitlePageEditorWidget
from ui.widgets.plan_editor import PlanEditorWidget
from ui.widgets.content_editor import ContentEditorWidget
from ui.widgets.literature_widget import LiteratureWidget

class MainWindow(QMainWindow):
    """
    Нативное десктопное приложение PyQt6 с 100% точной адаптацией Stitch Design System 2.0.
    """
    def __init__(self):
        super().__init__()
        self.state = ProjectState()
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("PaperCraft AI Studio — Native Desktop Academic Paper Builder")
        self.resize(1380, 880)
        self.setMinimumSize(1100, 700)
        self.setStyleSheet(get_app_stylesheet(dark_mode=True))

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 1. Верхняя панель (Header Bar): Логотип + Лимиты Gemini RPD
        header = QFrame()
        header.setFixedHeight(54)
        header.setStyleSheet("background-color: #0C0E12; border-bottom: 1px solid #262B36;")
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(20, 0, 20, 0)

        lbl_logo = QLabel("🎓 PaperCraft AI Studio")
        lbl_logo.setObjectName("headerTitle")
        lbl_logo.setStyleSheet("font-family: 'Plus Jakarta Sans'; font-weight: 800; font-size: 16px; color: #ADC6FF;")
        h_lay.addWidget(lbl_logo)

        lbl_subtitle = QLabel("•  Stitch Native Desktop")
        lbl_subtitle.setStyleSheet("color: #8C909F; font-size: 12px; font-weight: 500;")
        h_lay.addWidget(lbl_subtitle)

        h_lay.addStretch()

        # RPD счетчики лимитов
        lim1 = QLabel("⚡ 3.6 Flash: 16/20 RPD")
        lim1.setObjectName("badgeLabel")
        lim1.setStyleSheet("background-color: #161920; border: 1px solid #262B36; border-radius: 6px; padding: 4px 10px; font-family: 'JetBrains Mono'; font-size: 11px; color: #4EDEA3;")
        
        lim2 = QLabel("🧠 3.5 Lite: 132/500 RPD")
        lim2.setObjectName("badgeLabel")
        lim2.setStyleSheet("background-color: #161920; border: 1px solid #262B36; border-radius: 6px; padding: 4px 10px; font-family: 'JetBrains Mono'; font-size: 11px; color: #ADC6FF;")

        lim3 = QLabel("🎨 Imagen 4: 15/25 RPD")
        lim3.setObjectName("badgeLabel")
        lim3.setStyleSheet("background-color: #161920; border: 1px solid #262B36; border-radius: 6px; padding: 4px 10px; font-family: 'JetBrains Mono'; font-size: 11px; color: #D0BCFF;")

        h_lay.addWidget(lim1)
        h_lay.addWidget(lim2)
        h_lay.addWidget(lim3)

        root_layout.addWidget(header)

        # 2. Основная рабочая область: Левый Сайдбар + Центральный QStackedWidget
        body_widget = QWidget()
        body_lay = QHBoxLayout(body_widget)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        # Сайдбар навигации
        sidebar = QFrame()
        sidebar.setFixedWidth(240)
        sidebar.setStyleSheet("background-color: #14161B; border-right: 1px solid #262B36;")
        side_lay = QVBoxLayout(sidebar)
        side_lay.setContentsMargins(14, 20, 14, 20)
        side_lay.setSpacing(10)

        lbl_step_header = QLabel("НАВИГАЦИЯ ПО ШАГАМ")
        lbl_step_header.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 10px; font-weight: 700; color: #8C909F; letter-spacing: 1px;")
        side_lay.addWidget(lbl_step_header)

        self.nav_buttons = []
        steps_info = [
            ("1. Rules & ГОСТ", 0),
            ("2. Sources & Файлы", 1),
            ("3. Title Page", 2),
            ("4. Plan & Оглавление", 3),
            ("5. Studio Написание", 4),
            ("6. Build & Выгрузка", 5)
        ]

        for title, idx in steps_info:
            btn = QPushButton(title)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    color: #C2C6D6;
                    text-align: left;
                    padding: 11px 14px;
                    border-radius: 8px;
                    font-size: 13px;
                    font-family: 'Inter', sans-serif;
                    font-weight: 500;
                    border: 1px solid transparent;
                }
                QPushButton:hover {
                    background-color: #1E2024;
                    color: #FFFFFF;
                    border-color: #262B36;
                }
                QPushButton:checked {
                    background-color: #1E2B45;
                    color: #6DA0FF;
                    font-weight: 700;
                    border: 1px solid #3B82F6;
                }
            """)
            btn.clicked.connect(lambda _, i=idx: self.switch_step(i))
            side_lay.addWidget(btn)
            self.nav_buttons.append(btn)

        side_lay.addStretch()

        # Кнопка финальной сборки в сайдбаре
        self.btn_launch_word = QPushButton("🚀 Сформировать .docx")
        self.btn_launch_word.setObjectName("accentButton")
        self.btn_launch_word.setFixedHeight(44)
        
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setColor(QColor(16, 185, 129, 130))
        shadow.setOffset(0, 3)
        self.btn_launch_word.setGraphicsEffect(shadow)
        
        self.btn_launch_word.clicked.connect(self.on_build_and_launch_word)
        side_lay.addWidget(self.btn_launch_word)

        body_lay.addWidget(sidebar)

        # QStackedWidget с экранами мастеров
        self.stacked_widget = QStackedWidget()
        self.stacked_widget.setStyleSheet("background-color: #0C0E12;")
        
        self.step1_widget = PresetEditorWidget(self.state)
        self.step2_widget = KnowledgeBaseWidget(self.state)
        self.step3_widget = TitlePageEditorWidget(self.state)
        self.step4_widget = PlanEditorWidget(self.state)
        self.step5_widget = ContentEditorWidget(self.state)
        self.step6_widget = LiteratureWidget(self.state)

        self.stacked_widget.addWidget(self.step1_widget)
        self.stacked_widget.addWidget(self.step2_widget)
        self.stacked_widget.addWidget(self.step3_widget)
        self.stacked_widget.addWidget(self.step4_widget)
        self.stacked_widget.addWidget(self.step5_widget)
        self.stacked_widget.addWidget(self.step6_widget)

        body_lay.addWidget(self.stacked_widget)
        root_layout.addWidget(body_widget)

        # 3. Нижний Статус-бар (Status Bar)
        status_bar = QFrame()
        status_bar.setFixedHeight(30)
        status_bar.setStyleSheet("background-color: #0C0E12; border-top: 1px solid #262B36;")
        stat_lay = QHBoxLayout(status_bar)
        stat_lay.setContentsMargins(16, 0, 16, 0)
        
        self.lbl_status = QLabel("🟢 Проект автосохранен | ГОСТ Р 7.0.5-2008 | Times New Roman 14pt | Поля: 3.0 / 1.5 / 2.0 / 2.0 см")
        self.lbl_status.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px; color: #8C909F;")
        stat_lay.addWidget(self.lbl_status)
        
        root_layout.addWidget(status_bar)

        self.switch_step(0)

    def switch_step(self, idx: int):
        self.stacked_widget.setCurrentIndex(idx)
        for i, btn in enumerate(self.nav_buttons):
            btn.setChecked(i == idx)

    def on_next_step(self):
        curr_idx = self.stacked_widget.currentIndex()
        if curr_idx < self.stacked_widget.count() - 1:
            self.switch_step(curr_idx + 1)

    def on_prev_step(self):
        curr_idx = self.stacked_widget.currentIndex()
        if curr_idx > 0:
            self.switch_step(curr_idx - 1)

    def on_build_and_launch_word(self):
        try:
            config = FormattingRulesConfig(**self.state.formatting_rules)
            renderer = DocxRenderer(config)
            doc = renderer.create_document()

            # 1. Титульный лист
            title_data = TitlePageData(**self.state.title_page_data)
            title_data.topic = self.state.topic
            renderer.render_title_page(doc, title_data)

            # 2. Оглавление
            if self.state.plan_structure:
                renderer.add_table_of_contents_placeholder(doc, self.state.plan_structure)

            # 3. Наполнение разделами
            for item in self.state.plan_structure:
                sec_id = item.get("id")
                sec_title = item.get("title")
                
                if item.get("is_section_header", False):
                    renderer.add_heading_1(doc, sec_title)
                else:
                    renderer.add_heading_2(doc, sec_title)
                    
                content = self.state.sections_content.get(sec_id, "")
                if content:
                    for p in content.split("\n\n"):
                        if p.strip():
                            renderer.add_paragraph(doc, p.strip())

            output_filename = f"Готовая_работа_{self.state.project_id[:6]}.docx"
            output_path = os.path.abspath(output_filename)
            doc.save(output_path)
            
            QMessageBox.information(self, "Успех", f"Документ успешно отформатирован по ГОСТу!\nПуть: {output_path}")

            if os.name == 'nt':
                os.startfile(output_path)
            elif sys.platform == 'darwin':
                subprocess.call(['open', output_path])

        except Exception as e:
            QMessageBox.critical(self, "Ошибка сборки", f"Не удалось сформировать документ: {e}")
