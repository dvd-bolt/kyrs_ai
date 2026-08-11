import pytest
from core.blueprint import BlueprintManager
from core.gemini_engine import ContentGenerator

def test_blueprint_manager():
    blueprint = BlueprintManager(topic="Разработка приложения", project_type="coursework_it")
    
    fig_caption = blueprint.register_figure("Схема архитектуры", figure_type="mermaid")
    assert "Рисунок 1.1" in fig_caption

    tbl_caption = blueprint.register_table("Сводка результатов")
    assert "Таблица 1.1" in tbl_caption

    blueprint.add_term_to_glossary("API", "Application Programming Interface")
    blueprint.record_section_conclusion("1.1", "Сформулированы ключевые архитектурные требования.")

    payload = blueprint.get_context_payload()
    assert payload["topic"] == "Разработка приложения"
    assert "API" in payload["glossary"]
    assert len(payload["registered_figures"]) == 1

def test_content_generator_filters():
    generator = ContentGenerator()
    
    bad_text = "Таким образом, важно отметить, что исследование показало высокий результат."
    cleaned = generator.apply_humanize_filter(bad_text)
    
    assert "Таким образом" not in cleaned
    assert "важно отметить" not in cleaned
    assert "в итоге" in cleaned or "вследствие этого" in cleaned or "это позволяет" in cleaned or "курсовой проект" in cleaned

def test_content_generator_rewrite():
    generator = ContentGenerator()
    result = generator.rewrite_selected_text("Бухгалтерский учет является базой финансовой устойчивости компании.")
    assert result is not None
    assert len(result) > 0
