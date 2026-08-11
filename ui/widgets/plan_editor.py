from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QTreeWidget, QTreeWidgetItem, QPushButton, QInputDialog, QMessageBox
)
from PyQt6.QtCore import Qt
from models.state import ProjectState
from core.gemini_engine import ContentGenerator

class PlanEditorWidget(QWidget):
    """
    Нативный десктопный виджет Шага 4: Интерактивный редактор Оглавления и структуры (QTreeWidget).
    """
    def __init__(self, state: ProjectState, parent=None):
        super().__init__(parent)
        self.state = state
        self.generator = ContentGenerator()
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        group_plan = QGroupBox("Структура и план работы (Оглавление)")
        plan_lay = QVBoxLayout(group_plan)
        plan_lay.setSpacing(14)

        self.tree_plan = QTreeWidget()
        self.tree_plan.setHeaderLabels(["ID", "Название подглавы / раздела", "Целевой объем (слов)", "Тип элемента"])
        self.tree_plan.setColumnWidth(0, 70)
        self.tree_plan.setColumnWidth(1, 480)
        self.tree_plan.setColumnWidth(2, 160)
        plan_lay.addWidget(self.tree_plan)

        h_btns = QHBoxLayout()
        h_btns.setSpacing(12)

        btn_gen_plan = QPushButton("⚡ Сгенерировать план ИИ (Gemini 3.6 Flash)")
        btn_gen_plan.setObjectName("aiButton")
        btn_gen_plan.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_gen_plan.clicked.connect(self.on_generate_plan)
        
        btn_add = QPushButton("➕ Добавить пункт")
        btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add.clicked.connect(self.on_add_item)
        
        btn_remove = QPushButton("❌ Удалить пункт")
        btn_remove.setObjectName("secondaryButton")
        btn_remove.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_remove.clicked.connect(self.on_remove_item)
        
        h_btns.addWidget(btn_gen_plan)
        h_btns.addWidget(btn_add)
        h_btns.addWidget(btn_remove)
        h_btns.addStretch()

        plan_lay.addLayout(h_btns)
        layout.addWidget(group_plan)

        self.refresh_tree()

    def refresh_tree(self):
        self.tree_plan.clear()
        for item in self.state.plan_structure:
            is_header = item.get("is_section_header", False)
            item_type = "📌 Заголовок" if is_header else "📄 Подглава"
            
            node = QTreeWidgetItem([
                item.get("id", ""),
                item.get("title", ""),
                f"{item.get('target_words', 800)} слов",
                item_type
            ])
            self.tree_plan.addTopLevelItem(node)

    def on_generate_plan(self):
        if not self.state.topic:
            QMessageBox.warning(self, "Ошибка", "Сначала укажите тему работы на Шаге 1!")
            return
            
        plan_items = self.generator.generate_plan(self.state.topic, self.state.project_type)
        self.state.plan_structure = plan_items
        self.refresh_tree()
        QMessageBox.information(self, "Успешно", "ИИ сформировал глубокую академическую структуру оглавления!")

    def on_add_item(self):
        title, ok = QInputDialog.getText(self, "Добавить пункт", "Введите название подглавы:")
        if ok and title:
            item_id = f"1.{len(self.state.plan_structure) + 1}"
            self.state.plan_structure.append({
                "id": item_id,
                "title": title,
                "target_words": 800,
                "is_section_header": False
            })
            self.refresh_tree()

    def on_remove_item(self):
        selected = self.tree_plan.currentItem()
        if selected:
            idx = self.tree_plan.indexOfTopLevelItem(selected)
            if 0 <= idx < len(self.state.plan_structure):
                self.state.plan_structure.pop(idx)
                self.refresh_tree()

    def save_state_data(self):
        pass
