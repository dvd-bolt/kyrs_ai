import json
import os
import uuid
from typing import Dict, Any, List, Optional
from models.config import FormattingRulesConfig, TitlePageData

class ProjectState:
    """
    Класс управления полным состоянием текущего проекта (курсовая, статья, отчет).
    Поддерживает атомарное автосохранение и восстановление после сбоев (.courseproject / JSON).
    """
    def __init__(self, topic: str = "", project_type: str = "coursework_it"):
        self.project_id: str = str(uuid.uuid4())
        self.topic: str = topic
        self.project_type: str = project_type  # coursework_it, coursework_finance, scientific_article, school_project
        self.current_step: int = 1
        
        # Конфигурация и данные титульника
        self.formatting_rules: Dict[str, Any] = FormattingRulesConfig().model_dump()
        self.title_page_data: Dict[str, Any] = TitlePageData(topic=topic).model_dump()
        
        # Структура работы и сгенерированный контент
        self.plan_structure: List[Dict[str, Any]] = [] # [{"id": "1.1", "title": "...", "target_words": 800}]
        self.sections_content: Dict[str, str] = {}    # {"1.1": "Сгенерированный текст..."}
        
        # Паспорт проекта (Context Blueprint)
        self.blueprint_data: Dict[str, Any] = {
            "figures_registry": [],
            "tables_registry": [],
            "citations_registry": [],
            "glossary": {},
            "conclusions_by_section": {}
        }
        
        # Исходные файлы в Базе Знаний проекта
        self.knowledge_base_files: List[str] = []
        
        # Путь к файлу проекта для автосохранения
        self.project_filepath: Optional[str] = None

    def update_formatting(self, rules_config: FormattingRulesConfig) -> None:
        self.formatting_rules = rules_config.model_dump()

    def update_title_page(self, title_data: TitlePageData) -> None:
        self.title_page_data = title_data.model_dump()

    def add_section_content(self, section_id: str, content: str) -> None:
        self.sections_content[section_id] = content

    def add_knowledge_base_file(self, file_path: str) -> None:
        if file_path not in self.knowledge_base_files and os.path.exists(file_path):
            self.knowledge_base_files.append(file_path)

    def save_to_file(self, filepath: str = None) -> str:
        """
        Сохраняет текущий проект в файл формата .courseproject (JSON).
        """
        target_path = filepath or self.project_filepath
        if not target_path:
            target_path = f"project_{self.project_id[:8]}.courseproject"
        
        self.project_filepath = target_path
        data = {
            "project_id": self.project_id,
            "topic": self.topic,
            "project_type": self.project_type,
            "current_step": self.current_step,
            "formatting_rules": self.formatting_rules,
            "title_page_data": self.title_page_data,
            "plan_structure": self.plan_structure,
            "sections_content": self.sections_content,
            "blueprint_data": self.blueprint_data,
            "knowledge_base_files": self.knowledge_base_files,
            "project_filepath": self.project_filepath
        }
        
        # Временная запись и атомарное переименование для предотвращения повреждения при сбое
        temp_path = target_path + ".tmp"
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        if os.path.exists(target_path):
            os.remove(target_path)
        os.rename(temp_path, target_path)
        
        return target_path

    @classmethod
    def load_from_file(cls, filepath: str) -> 'ProjectState':
        """
        Загружает состояние проекта из файла .courseproject.
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Файл проекта не найден: {filepath}")
            
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        state = cls(topic=data.get("topic", ""), project_type=data.get("project_type", "coursework_it"))
        state.project_id = data.get("project_id", state.project_id)
        state.current_step = data.get("current_step", 1)
        state.formatting_rules = data.get("formatting_rules", state.formatting_rules)
        state.title_page_data = data.get("title_page_data", state.title_page_data)
        state.plan_structure = data.get("plan_structure", [])
        state.sections_content = data.get("sections_content", {})
        state.blueprint_data = data.get("blueprint_data", {})
        state.knowledge_base_files = data.get("knowledge_base_files", [])
        state.project_filepath = filepath
        
        return state
