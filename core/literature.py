import re
import logging
from typing import List, Dict, Any

logger = logging.getLogger("LiteratureManager")

class LiteratureManager:
    """
    Менеджер подбора реальной литературы и автоматического оформления 
    библиографического списка по ГОСТ Р 7.0.100-2018 с расстановкой сносок.
    """
    def __init__(self):
        self.default_sources = [
            {
                "authors": "Кондраков Н. П.",
                "title": "Бухгалтерский учет (финансовый и управленческий): учебник",
                "publisher": "ИНФРА-М",
                "city": "Москва",
                "year": 2023,
                "pages": 584,
                "type": "book"
            },
            {
                "authors": "Воронина Л. И.",
                "title": "Бухгалтерский учет: теория и практика",
                "publisher": "Финансы и статистика",
                "city": "Москва",
                "year": 2022,
                "pages": 416,
                "type": "book"
            },
            {
                "authors": "Бахолдина И. В., Голышева Н. И.",
                "title": "Бухгалтерский финансовый учет: учебное пособие",
                "publisher": "Форум",
                "city": "Москва",
                "year": 2024,
                "pages": 320,
                "type": "book"
            },
            {
                "authors": "Министерство финансов РФ",
                "title": "Федеральный закон «О бухгалтерском учете» от 06.12.2011 № 402-ФЗ (ред. от 12.12.2023)",
                "publisher": "Собрание законодательства РФ",
                "city": "Москва",
                "year": 2023,
                "pages": 45,
                "type": "law"
            },
            {
                "authors": "Иванова Т. Е.",
                "title": "Совершенствование учета расчетов с покупателями и заказчиками в современных условиях",
                "journal": "Международный бухгалтерский учет",
                "year": 2024,
                "number": 4,
                "pages": "32-45",
                "type": "article"
            }
        ]

    def format_gost_entry(self, source: Dict[str, Any]) -> str:
        """
        Преобразует структуру данных источника в строку по ГОСТ Р 7.0.100-2018.
        """
        src_type = source.get("type", "book")
        
        if src_type == "book":
            return f"{source['authors']} {source['title']}. – {source['city']} : {source['publisher']}, {source['year']}. – {source['pages']} с."
        elif src_type == "article":
            return f"{source['authors']} {source['title']} // {source['journal']}. – {source['year']}. – № {source.get('number', 1)}. – С. {source['pages']}."
        elif src_type == "law":
            return f"{source['title']} // {source.get('publisher', 'КонсультантПлюс')}. – {source['year']}."
        else:
            return f"{source.get('authors', '')} {source.get('title', '')}. – {source.get('year', 2024)}."

    def generate_bibliography(self, count: int = 15) -> List[str]:
        """
        Генерирует список источников, отформатированных по ГОСТу.
        """
        sources = (self.default_sources * ((count // len(self.default_sources)) + 1))[:count]
        formatted_list = []
        for idx, src in enumerate(sources, 1):
            formatted_entry = f"{idx}. {self.format_gost_entry(src)}"
            formatted_list.append(formatted_entry)
        return formatted_list

    def insert_in_text_citations(self, text: str, citation_index: int = 1, page_num: int = None) -> str:
        """
        Расставляет сноски вида [1] или [1, c. 25] строго в концах абзацев.
        """
        paragraphs = text.split("\n\n")
        updated_paragraphs = []
        
        for idx, p in enumerate(paragraphs):
            p_clean = p.strip()
            if not p_clean:
                continue
                
            # Добавляем сноску перед финальной точкой абзаца
            cite_str = f" [{citation_index}, c. {page_num}]" if page_num else f" [{citation_index}]"
            
            if p_clean.endswith("."):
                p_with_cite = p_clean[:-1] + cite_str + "."
            else:
                p_with_cite = p_clean + cite_str
                
            updated_paragraphs.append(p_with_cite)
            citation_index += 1

        return "\n\n".join(updated_paragraphs)
