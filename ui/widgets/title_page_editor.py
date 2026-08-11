from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QLineEdit, 
    QCheckBox, QPushButton, QFileDialog
)
from PyQt6.QtCore import Qt
from models.config import TitlePageData
from models.state import ProjectState

class TitlePageEditorWidget(QWidget):
    """
    Виджет Шага 3: Редактор реквизитов титульного листа или выбор готового .docx от вуза.
    """
    def __init__(self, state: ProjectState, parent=None):
        super().__init__(parent)
        self.state = state
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        # Выбор режима
        self.check_custom_file = QCheckBox("Подшить готовый .docx файл титульного листа от моего вуза")
        self.check_custom_file.setStyleSheet("font-weight: 700; color: #ADC6FF;")
        self.check_custom_file.toggled.connect(self.on_toggle_custom_file)
        layout.addWidget(self.check_custom_file)

        # Панель выгрузки файла
        self.group_file = QGroupBox("Пользовательский файл титульного листа (.docx)")
        file_lay = QHBoxLayout(self.group_file)
        file_lay.setSpacing(12)
        
        self.edit_file_path = QLineEdit()
        self.edit_file_path.setReadOnly(True)
        self.edit_file_path.setPlaceholderText("Выберите путь к файлу титульника .docx...")
        
        btn_browse = QPushButton("Обзор...")
        btn_browse.setObjectName("secondaryButton")
        btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_browse.clicked.connect(self.on_browse_file)
        
        file_lay.addWidget(self.edit_file_path)
        file_lay.addWidget(btn_browse)
        layout.addWidget(self.group_file)
        self.group_file.hide()

        # Форма ввода полей
        self.group_fields = QGroupBox("Реквизиты и авторы титульного листа")
        fields_lay = QVBoxLayout(self.group_fields)
        fields_lay.setSpacing(12)

        fields_lay.addWidget(QLabel("Министерство / Ведомство и Наименование вуза:"))
        self.edit_vuz = QLineEdit("МИНИСТЕРСТВО НАУКИ И ВЫСШЕГО ОБРАЗОВАНИЯ РФ\nКубанский государственный аграрный университет")
        fields_lay.addWidget(self.edit_vuz)

        fields_lay.addWidget(QLabel("Факультет и Кафедра:"))
        self.edit_fac = QLineEdit("Факультет прикладной информатики\nКафедра компьютерных технологий и систем")
        fields_lay.addWidget(self.edit_fac)

        fields_lay.addWidget(QLabel("Данные соискателя (Выполнил):"))
        self.edit_student = QLineEdit("Выполнил: студент гр. ЭК-2101\nИванов И.И.")
        fields_lay.addWidget(self.edit_student)

        fields_lay.addWidget(QLabel("Данные проверяющего (Проверил):"))
        self.edit_teacher = QLineEdit("Проверил: к.т.н., доцент\nПетров П.П.")
        fields_lay.addWidget(self.edit_teacher)

        h_bottom = QHBoxLayout()
        h_bottom.setSpacing(16)
        
        h_bottom.addWidget(QLabel("Город сдачи:"))
        self.edit_city = QLineEdit("Краснодар")
        h_bottom.addWidget(self.edit_city)
        
        h_bottom.addWidget(QLabel("Год:"))
        self.edit_year = QLineEdit("2026")
        h_bottom.addWidget(self.edit_year)
        
        fields_lay.addLayout(h_bottom)
        layout.addWidget(self.group_fields)

        layout.addStretch()

    def on_toggle_custom_file(self, checked: bool):
        self.group_file.setVisible(checked)
        self.group_fields.setVisible(not checked)

    def on_browse_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Выберите файл титульного листа", "", "Word Документы (*.docx)")
        if file_path:
            self.edit_file_path.setText(file_path)

    def save_state_data(self):
        title_data = TitlePageData(
            university=self.edit_vuz.text().strip(),
            faculty=self.edit_fac.text().strip(),
            topic=self.state.topic,
            student_info=self.edit_student.text().strip(),
            teacher_info=self.edit_teacher.text().strip(),
            city=self.edit_city.text().strip(),
            year=int(self.edit_year.text().strip() or "2026"),
            use_custom_file=self.check_custom_file.isChecked(),
            custom_docx_path=self.edit_file_path.text().strip() if self.check_custom_file.isChecked() else None
        )
        self.state.update_title_page(title_data)
