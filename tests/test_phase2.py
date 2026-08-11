import os
import pytest
from core.cascade_llm import CascadeLLMClient
from core.executor import ScriptExecutor
from core.mermaid_render import render_mermaid_to_png
from core.image_gen import generate_ai_illustration

def test_cascade_llm_client():
    client = CascadeLLMClient()
    response = client.send_text_request("Расскажи о принципах программирования", category="content")
    assert response is not None
    assert len(response) > 0

def test_script_executor(tmp_path):
    executor = ScriptExecutor()
    script_code = """
import matplotlib.pyplot as plt
plt.figure(figsize=(6, 4))
plt.plot([1, 2, 3, 4], [10, 20, 25, 30], label='Динамика')
plt.title('Тестовый график')
"""
    output_png = str(tmp_path / "chart_test.png")
    success = executor.execute_chart_script(script_code, output_png)
    assert success is True
    assert os.path.exists(output_png)
    assert os.path.getsize(output_png) > 0

def test_mermaid_render(tmp_path):
    mermaid_code = """
graph TD
    A[Пользователь] --> B(Авторизация)
    B --> C{Успешно?}
    C -->|Да| D[Главный экран]
    C -->|Нет| E[Ошибка]
"""
    output_png = str(tmp_path / "mermaid_test.png")
    result_path = render_mermaid_to_png(mermaid_code, output_png)
    assert os.path.exists(result_path)
    assert os.path.getsize(result_path) > 0

def test_image_gen(tmp_path):
    output_png = str(tmp_path / "ai_test.png")
    result_path = generate_ai_illustration("Макет интерфейса платежного терминала", output_png)
    assert os.path.exists(result_path)
    assert os.path.getsize(result_path) > 0
