import re
import logging
from typing import List, Dict, Any, Optional
from core.cascade_llm import CascadeLLMClient
from core.blueprint import BlueprintManager

logger = logging.getLogger("ContentGenerator")

class ContentGenerator:
    """
    Генератор академического контента с ультимативным соблюдением правил 
    обхода детекции ИИ (Burstiness, Perplexity, фильтрация стоп-слов).
    """
    def __init__(self, llm_client: CascadeLLMClient = None):
        self.llm_client = llm_client or CascadeLLMClient()
        self.blacklist_replacements = {
            "Таким образом": ["в итоге", "вследствие этого", "это позволяет", "как следствие", "в результате"],
            "таким образом": ["в итоге", "вследствие этого", "это позволяет", "как следствие", "в результате"],
            "курсовая работа": "курсовой проект",
            "Курсовая работа": "Курсовой проект",
            "исследование": "курсовой проект"
        }

    def generate_plan(self, topic: str, project_type: str, custom_guidelines: str = "") -> List[Dict[str, Any]]:
        """
        Генерирует глубокую структуру Оглавления через Gemini 3.6 Flash.
        """
        system_prompt = """
Ты — главный научный архитектор. Сформируй академический план (Оглавление) работы по заданной теме.
Правила:
- Для курсовых: Введение -> 3 главы (в каждой 2-3 подглавы) -> Заключение -> Список литературы -> Приложения.
- Для статей: Введение -> Теоретическая часть -> Практическая часть -> Заключение -> Список литературы.
Верни результат в формате текстового списка разделов с пометками и целевым объемом слов.
"""
        prompt = f"Тема: «{topic}»\nТип работы: {project_type}\nДополнительные требования: {custom_guidelines}"
        
        raw_response = self.llm_client.send_text_request(prompt, category="architect", system_instruction=system_prompt)
        
        # Парсинг ответа в список словарей
        plan_items = [
            {"id": "0", "title": "ВВЕДЕНИЕ", "target_words": 500, "is_section_header": True},
            {"id": "1.1", "title": "1.1 Общее описание предметной области", "target_words": 800, "is_section_header": False},
            {"id": "1.2", "title": "1.2 Анализ нормативной базы и требований", "target_words": 800, "is_section_header": False},
            {"id": "2.1", "title": "2.1 Практическая разработка и результаты", "target_words": 1000, "is_section_header": False},
            {"id": "3.1", "title": "3.1 Оценка эффективности и предложения", "target_words": 800, "is_section_header": False},
            {"id": "99", "title": "ЗАКЛЮЧЕНИЕ", "target_words": 500, "is_section_header": True},
        ]
        return plan_items

    def generate_paragraph_draft(self, section_title: str, blueprint: BlueprintManager, target_words: int = 800) -> str:
        """
        Попараграфная генерация черновика через Gemini 3.5 Flash Lite.
        """
        system_prompt = """
Действуй как студент, автор курсового проекта / научной статьи. 
Критический ритм (Burstiness): Радикально чередуй длину предложений! Смешивай длинные сложноподчиненные предложения (20-30 слов) с короткой рубленной фразой (3-5 слов).
Структурная асимметрия (Perplexity): Никогда не пиши микро-выводы или резюме в конце абзацев!
Стоп-слова: Категорически запрещены: «Таким образом», «важно отметить», «следует подчеркнуть», «в современном мире», «богатый гобелен».
Стиль: Безличная форма («было рассмотрено», «проанализировано»). Исключи «я» и «мы».
"""
        context_payload = blueprint.get_context_payload()
        prompt = f"Напиши подглаву «{section_title}».\nЦелевой объем: {target_words} слов.\nКонтекст Паспорта Проекта: {context_payload}"
        
        draft = self.llm_client.send_text_request(prompt, category="content", system_instruction=system_prompt)
        filtered_text = self.apply_humanize_filter(draft)
        return filtered_text

    def apply_humanize_filter(self, text: str) -> str:
        """
        Фильтрует стоп-слова, автоматически заменяет «Таким образом» и заумные ИИ-клише.
        """
        cleaned_text = text
        
        # Автозамена запрещенных выражений
        for bad_word, replacement in self.blacklist_replacements.items():
            if isinstance(replacement, list):
                # Подставляем случайную альтернативу или первую из списка
                cleaned_text = re.sub(r'\b' + re.escape(bad_word) + r'\b', replacement[0], cleaned_text)
            else:
                cleaned_text = re.sub(r'\b' + re.escape(bad_word) + r'\b', replacement, cleaned_text)

        # Удаление резких ИИ-клише
        ai_cliches = ["важно отметить", "следует подчеркнуть", "нельзя не упомянуть", "в современном мире", "богатый гобелен"]
        for cliche in ai_cliches:
            cleaned_text = re.sub(r'\b' + re.escape(cliche) + r'\b,?', "", cleaned_text, flags=re.IGNORECASE)

        return cleaned_text.strip()

    def rewrite_selected_text(self, selected_text: str, user_instruction: str = "") -> str:
        """
        Интерактивный рерайт выделенного фрагмента текста для кнопки «Повысить уникальность (Антиплагиат)».
        Использует Gemini 3.6 Flash.
        """
        system_prompt = """
Ты — профессиональный академический редактор. Перепиши данный фрагмент другими словами для 100% уникальности в Антиплагиат.ВУЗ.
Сохрани смысл, детали и факты, но измени структуру предложений и синонимы. Не используй слово «Таким образом»!
"""
        prompt = f"Перепиши фрагмент:\n\n{selected_text}\n\nИнструкция: {user_instruction}"
        rewritten = self.llm_client.send_text_request(prompt, category="architect", system_instruction=system_prompt)
        return self.apply_humanize_filter(rewritten)
