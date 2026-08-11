from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QListWidget, QListWidgetItem, QPushButton, QFileDialog, QMessageBox, QFrame
)
from PyQt6.QtCore import Qt
from models.state import ProjectState

class KnowledgeBaseWidget(QWidget):
    """
    Виджет Шага 2: Менеджер исходников и файлов Базы Знаний проекта (код C#/Python, Excel, PDF).
    """
    def __init__(self, state: ProjectState, parent=None):
        super().__init__(parent)
        self.state = state
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        group_kb = QGroupBox("База Знаний и Иходные Файлы проекта")
        kb_lay = QVBoxLayout(group_kb)
        kb_lay.setSpacing(14)

        lbl_desc = QLabel("Прикрепленные исходники (код C#/Python, расчеты Excel, выписки, PDF) анализируются Gemini 3.5/3.6 при написании практических глав:")
        lbl_desc.setStyleSheet("color: #8C909F; font-size: 13px;")
        kb_lay.addWidget(lbl_desc)

        self.list_files = QListWidget()
        kb_lay.addWidget(self.list_files)

        h_btns = QHBoxLayout()
        h_btns.setSpacing(12)

        btn_add = QPushButton("➕ Добавить файлы исходников...")
        btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add.clicked.connect(self.on_add_files)
        
        btn_remove = QPushButton("❌ Удалить выбранный файл")
        btn_remove.setObjectName("secondaryButton")
        btn_remove.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_remove.clicked.connect(self.on_remove_file)
        
        h_btns.addWidget(btn_add)
        h_btns.addWidget(btn_remove)
        h_btns.addStretch()

        kb_lay.addLayout(h_btns)
        layout.addWidget(group_kb)

        self.refresh_file_list()

    def refresh_file_list(self):
        self.list_files.clear()
        for f in self.state.knowledge_base_files:
            ext = f.split(".")[-1].lower() if "." in f else ""
            icon = "📦"
            if ext in ["cs", "cpp", "py", "java", "js"]:
                icon = "💻"
            elif ext in ["xlsx", "csv"]:
                icon = "📊"
            elif ext in ["pdf", "docx", "doc"]:
                icon = "📄"

            item = QListWidgetItem(f"{icon} {f}")
            self.list_files.addItem(item)

    def on_add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Выберите файлы проекта", "", "Все поддерживает (*.cs *.cpp *.py *.xlsx *.csv *.pdf *.docx *.txt)"
        )
        for f in files:
            self.state.add_knowledge_base_file(f)
        self.refresh_file_list()

    def on_remove_file(self):
        current_item = self.list_files.currentItem()
        if current_item:
            clean_name = current_item.text().split(" ", 1)[-1]
            if clean_name in self.state.knowledge_base_files:
                self.state.knowledge_base_files.remove(clean_name)
            self.refresh_file_list()

    def save_state_data(self):
        pass
