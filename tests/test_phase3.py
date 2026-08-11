import pytest
from core.finance_engine import FinanceEngine
from core.literature import LiteratureManager

def test_finance_engine():
    engine = FinanceEngine(company_name="ООО «Партнер»")
    entries = engine.generate_journal_entries(2024)
    assert len(entries) >= 10

    # Проверка баланса двойной записи
    is_balanced = engine.verify_double_entry_balance(entries)
    assert is_balanced is True

    # Расчет сальдо по счетам
    balances = engine.calculate_account_balances()
    assert "51" in balances
    assert "10" in balances
    assert balances["51"]["final_balance"] is not None

    # Динамика за 3 года
    analytics = engine.generate_3year_analytics(2024)
    assert "resources" in analytics
    assert "results" in analytics
    assert len(analytics["resources"]["rows"]) >= 4

def test_literature_manager():
    manager = LiteratureManager()
    bib_list = manager.generate_bibliography(10)
    assert len(bib_list) == 10
    assert bib_list[0].startswith("1. Кондраков Н. П.")

    sample_text = "Бухгалтерский учет является ключевым элементом информационной системы организации.\n\nОн обеспечивает формирование полной и достоверной информации."
    cited_text = manager.insert_in_text_citations(sample_text, citation_index=1, page_num=45)
    assert "[1, c. 45]." in cited_text
    assert "[2, c. 45]." in cited_text
