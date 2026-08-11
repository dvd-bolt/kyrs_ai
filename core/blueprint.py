import logging
from typing import Dict, Any, List

logger = logging.getLogger("BlueprintManager")

class BlueprintManager:
    """
    Менеджер «Паспорта Проекта» (Context Blueprint).
    Отвечает за сохранение сквозного контекста, регистрацию рисунков и таблиц,
    удержание терминологического словаря и выводов предыдущих глав.
    """
    def __init__(self, topic: str = "", project_type: str = "coursework_it", target_word_count: int = 8000):
        self.topic = topic
        self.project_type = project_type
        self.target_word_count = target_word_count
        self.current_word_count = 0
        
        self.figures_registry: List[Dict[str, str]] = []  # [{"id": "Рисунок 1.1", "caption": "..."}]
        self.tables_registry: List[Dict[str, str]] = []   # [{"id": "Таблица 2.1", "caption": "..."}]
        self.citations_registry: List[int] = []           # [1, 2, 3]
        self.glossary: Dict[str, str] = {}               # {"LIFO": "Last In First Out"}
        self.conclusions_by_section: Dict[str, str] = {} # {"1.1": "Краткий вывод..."}

    def register_figure(self, caption: str, figure_type: str = "image") -> str:
        """
        Регистрирует новый рисунок и возвращает уникальную метку по ГОСТу (например, Рисунок 1.1).
        """
        fig_index = len(self.figures_registry) + 1
        fig_id = f"Рисунок 1.{fig_index}" if self.project_type.startswith("coursework") else f"Рисунок {fig_index}"
        
        fig_entry = {
            "id": fig_id,
            "caption": f"{fig_id} – {caption}",
            "type": figure_type
        }
        self.figures_registry.append(fig_entry)
        logger.info(f"Зарегистрирован рисунок: {fig_entry['caption']}")
        return fig_entry["caption"]

    def register_table(self, caption: str) -> str:
        """
        Регистрирует новую таблицу и возвращает метку (Таблица 1.1).
        """
        tbl_index = len(self.tables_registry) + 1
        tbl_id = f"Таблица 1.{tbl_index}" if self.project_type.startswith("coursework") else f"Таблица {tbl_index}"
        
        tbl_entry = {
            "id": tbl_id,
            "caption": f"{tbl_id} – {caption}"
        }
        self.tables_registry.append(tbl_entry)
        logger.info(f"Зарегистрирована таблица: {tbl_entry['caption']}")
        return tbl_entry["caption"]

    def add_term_to_glossary(self, term: str, definition: str) -> None:
        self.glossary[term] = definition

    def record_section_conclusion(self, section_id: str, conclusion_text: str) -> None:
        self.conclusions_by_section[section_id] = conclusion_text

    def get_context_payload(self) -> Dict[str, Any]:
        """
        Возвращает сжатый пэйлоад Паспорта Проекта для подачи в промпты LLM.
        """
        return {
            "topic": self.topic,
            "project_type": self.project_type,
            "glossary": self.glossary,
            "registered_figures": [f["id"] for f in self.figures_registry],
            "registered_tables": [t["id"] for t in self.tables_registry],
            "previous_conclusions": list(self.conclusions_by_section.values())[-3:] # Последние 3 вывода
        }
