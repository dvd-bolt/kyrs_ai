from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QListWidget, QPushButton, QMessageBox
)
from PyQt6.QtCore import Qt
from models.state import ProjectState
from core.literature import LiteratureManager

class LiteratureWidget(QWidget):
    """
    Виджет Шага 6: Список использованных источников по ГОСТ Р 7.0.100-2018 и сборка работы.
    """
    def __init__(self, state: ProjectState, parent=None):
        super().__init__(parent)
        self.state = state
        self.lit_manager = LiteratureManager()
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        group_lit = QGroupBox("Список использованной литературы (ГОСТ Р 7.0.100-2018)")
        lit_lay = QVBoxLayout(group_lit)
        lit_lay.setSpacing(14)

        lbl_desc = QLabel("Автоматически сформированный список источников с расстановкой в тексте сносок вида [1, c. 12]:")
        lbl_desc.setStyleSheet("color: #8C909F; font-size: 13px;")
        lit_lay.addWidget(lbl_desc)

        self.list_sources = QListWidget()
        lit_lay.addWidget(self.list_sources)

        h_btns = QHBoxLayout()
        h_btns.setSpacing(12)

        btn_auto_bib = QPushButton("📚 Сформировать список литературы (ГОСТ Р 7.0.100-2018)")
        btn_auto_bib.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_auto_bib.clicked.connect(self.on_generate_bibliography)
        
        h_btns.addWidget(btn_auto_bib)
        h_btns.addStretch()

        lit_lay.addLayout(h_btns)
        layout.addWidget(group_lit)
        self.refresh_list()

    def refresh_list(self):
        self.list_sources.clear()
        bib_items = self.lit_manager.generate_bibliography(15)
        for b in bib_items:
            self.list_sources.addItem(b)

    def on_generate_bibliography(self):
        self.refresh_list()
        QMessageBox.information(self, "ГОСТ Литература", "Список источников обновлен по ГОСТ Р 7.0.100-2018!")

    def save_state_data(self):
        pass
