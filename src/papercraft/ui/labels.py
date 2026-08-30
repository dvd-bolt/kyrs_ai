"""Human-friendly Russian labels for values stored as stable English identifiers."""

from __future__ import annotations

from enum import StrEnum

LABELS: dict[str, str] = {
    # Project setup.
    "coursework": "Курсовая работа",
    "scientific_article": "Научная статья",
    "practice_report": "Отчёт по практике",
    "lab_report": "Лабораторная работа",
    "industrial_report": "Производственный отчёт",
    "school_project": "Школьный проект",
    "universal": "Универсальный профиль",
    "it": "IT",
    "programming": "Программирование",
    "finance": "Финансы",
    "accounting": "Бухгалтерский учёт",
    "general": "Общий",
    "science": "Наука",
    "school": "Школа",
    "methodology": "Методичка",
    "example": "Пример работы",
    "template": "Шаблон",
    "source_data": "Исходные данные",
    "codebase": "Код проекта",
    "image": "Изображение",
    "reference": "Справочный материал",
    "unknown": "Не определено",
    # Pipeline.
    "preflight": "Проверка готовности",
    "ingest": "Загрузка и разбор файлов",
    "extract_requirements": "Извлечение требований",
    "build_evidence_index": "Индексирование материалов",
    "verified_research": "Проверка источников",
    "plan": "Построение плана",
    "build_facts_and_datasets": "Подготовка фактов и данных",
    "generate_sections": "Генерация разделов",
    "generate_visuals": "Создание таблиц и иллюстраций",
    "citation_audit": "Проверка цитат",
    "consistency_qa": "Проверка связности",
    "render_docx": "Сборка DOCX-документа",
    "word_finalize": "Финальная обработка LibreOffice",
    "export_pdf": "Экспорт PDF",
    "pdf_visual_qa": "Визуальная проверка PDF",
    "final_gemini_review": "Финальная AI-проверка",
    "package": "Подготовка результатов",
    # Artifacts.
    "source_copy": "Копия исходника",
    "extracted_text": "Извлечённый текст",
    "requirements": "Требования",
    "blueprint": "Проектный план",
    "outline": "Структура работы",
    "dataset": "Набор данных",
    "chart": "Диаграмма",
    "diagram": "Схема",
    "manuscript": "Черновик",
    "docx": "DOCX-документ",
    "pdf": "PDF-документ",
    "page_preview": "Страница предпросмотра",
    "qa_json": "Отчёт проверки",
    "qa_html": "HTML-отчёт",
    "other": "Другой файл",
    # Quality checks.
    "pass": "Проверка пройдена",
    "warning": "Предупреждение",
    "fail": "Требуется исправление",
    "info": "Информация",
    "error": "Ошибка",
    "critical": "Критическое замечание",
    "blocker": "Блокирующее замечание",
    # Requirements.
    "structure": "Структура",
    "volume": "Объём",
    "title_page": "Титульный лист",
    "page_layout": "Макет страницы",
    "typography": "Типографика",
    "headings": "Заголовки",
    "tables": "Таблицы",
    "figures": "Рисунки",
    "formulas": "Формулы",
    "code_listings": "Листинги кода",
    "pagination": "Нумерация страниц",
    "citations": "Цитаты",
    "bibliography": "Список источников",
    "appendices": "Приложения",
    "custom": "Особое требование",
}


def label_for(value: StrEnum | str | None) -> str:
    """Translate a stable enum value while retaining a readable fallback."""

    if value is None:
        return "—"
    raw = value.value if isinstance(value, StrEnum) else str(value)
    return LABELS.get(raw, raw.replace("_", " ").capitalize())


__all__ = ["LABELS", "label_for"]
