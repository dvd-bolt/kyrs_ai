from pydantic import BaseModel, Field
from typing import List, Optional, Dict

class FormattingRulesConfig(BaseModel):
    """
    Конфигурация правил форматирования документа по ГОСТу и индивидуальной методичке.
    """
    font_name: str = Field(default="Times New Roman", description="Основной шрифт текста")
    font_size_pt: int = Field(default=14, description="Размер основного шрифта в пунктах")
    heading_1_size_pt: int = Field(default=16, description="Размер шрифта заголовков 1-го уровня")
    heading_2_size_pt: int = Field(default=14, description="Размер шрифта заголовков 2-го уровня")
    line_spacing: float = Field(default=1.5, description="Межстрочный интервал")
    margin_left_cm: float = Field(default=3.0, description="Левое поле страницы в см")
    margin_right_cm: float = Field(default=1.5, description="Правое поле страницы в см")
    margin_top_cm: float = Field(default=2.0, description="Верхнее поле страницы в см")
    margin_bottom_cm: float = Field(default=2.0, description="Нижнее поле страницы в см")
    paragraph_indent_cm: float = Field(default=1.25, description="Отступ первой строки (красная строка) в см")
    header_distance_cm: float = Field(default=1.25, description="Расстояние до верхушки колонтитула в см")
    page_number_position: str = Field(default="top_right", description="Позиция номера страницы (top_right, top_center, bottom_center)")
    table_font_size_pt: int = Field(default=12, description="Размер шрифта внутри таблиц")
    table_line_spacing: float = Field(default=1.0, description="Межстрочный интервал внутри таблиц")

class TitlePageData(BaseModel):
    """
    Данные для генерации или прикрепления титульного листа.
    """
    university: str = Field(default="МИНИСТЕРСТВО НАУКИ И ВЫСШЕГО ОБРАЗОВАНИЯ РОССИЙСКОЙ ФЕДЕРАЦИИ", description="Наименование ведомства и вуза")
    faculty: str = Field(default="Факультет прикладной информатики", description="Факультет")
    department: str = Field(default="Кафедра компьютерных технологий и систем", description="Кафедра")
    work_type: str = Field(default="КУРСОВОЙ ПРОЕКТ", description="Тип работы: КУРСОВОЙ ПРОЕКТ / НАУЧНАЯ СТАТЬЯ / ОТЧЕТ")
    subject: str = Field(default="по дисциплине «Информационные технологии»", description="Предмет / Дисциплина")
    topic: str = Field(default="", description="Тема работы")
    student_info: str = Field(default="Выполнил: студент группы ЭК-2101\nИванов И.И.", description="Данные соискателя")
    teacher_info: str = Field(default="Проверил: к.т.н., доцент\nПетров П.П.", description="Данные проверяющего")
    city: str = Field(default="Краснодар", description="Город сдачи")
    year: int = Field(default=2026, description="Год сдачи")
    use_custom_file: bool = Field(default=False, description="Использовать готовый .docx файл титульного листа")
    custom_docx_path: Optional[str] = Field(default=None, description="Путь к пользовательскому .docx файлу титульника")

class ProjectPreset(BaseModel):
    """
    Пресет предметной области с уникальными промптами и правилами.
    """
    preset_id: str = Field(description="Уникальный идентификатор пресета: it, finance, science, school")
    preset_name: str = Field(description="Человекопонятное название пресета")
    formatting: FormattingRulesConfig = Field(default_factory=FormattingRulesConfig)
    custom_system_prompt: str = Field(default="")
    stop_words_blacklist: List[str] = Field(default_factory=lambda: [
        "Таким образом", "важно отметить", "следует подчеркнуть", "нельзя не упомянуть", 
        "в современном мире", "безусловно", "в заключение", "богатый гобелен", "глубокое погружение"
    ])
