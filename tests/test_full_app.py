import os
import pytest
from PyQt6.QtWidgets import QApplication
from ui.main_window import MainWindow

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app

def test_main_window_creation(qapp):
    window = MainWindow()
    assert window is not None
    assert window.windowTitle() is not None
    assert window.stacked_widget.count() == 6
    
    # Проверка навигации
    window.on_next_step()
    assert window.stacked_widget.currentIndex() == 1
    
    window.on_prev_step()
    assert window.stacked_widget.currentIndex() == 0

def test_full_document_assembly(qapp, tmp_path):
    window = MainWindow()
    window.state.topic = "Тестовая генерация документа"
    window.state.plan_structure = [
        {"id": "0", "title": "ВВЕДЕНИЕ", "target_words": 500, "is_section_header": True},
        {"id": "1.1", "title": "1.1 Описание предметной области", "target_words": 800, "is_section_header": False}
    ]
    window.state.sections_content = {
        "1.1": "В данном разделе рассмотрены основные аспекты автоматизации..."
    }
    
    output_docx = str(tmp_path / "final_test.docx")
    
    # Запуск верстки
    from models.config import FormattingRulesConfig, TitlePageData
    from core.renderer import DocxRenderer
    
    config = FormattingRulesConfig()
    renderer = DocxRenderer(config)
    doc = renderer.create_document()
    
    renderer.render_title_page(doc, TitlePageData(topic="Тестовая работа"))
    renderer.add_heading_1(doc, "ВВЕДЕНИЕ")
    renderer.add_paragraph(doc, "Текст введения...")
    
    doc.save(output_docx)
    assert os.path.exists(output_docx)
