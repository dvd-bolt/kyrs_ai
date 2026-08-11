import os
import sys
import subprocess
import logging
import webbrowser
from fastapi import FastAPI, HTTPException, Body
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Dict, Any, List

from models.config import FormattingRulesConfig, TitlePageData
from models.state import ProjectState
from core.renderer import DocxRenderer
from core.gemini_engine import ContentGenerator
from core.blueprint import BlueprintManager
from core.literature import LiteratureManager
from core.finance_engine import FinanceEngine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("WebApp")

app = FastAPI(title="Stitch Academic Paper Builder Web UI")

global_state = ProjectState(topic="Разработка приложения платежного терминала на C#", project_type="coursework_it")
generator = ContentGenerator()
blueprint = BlueprintManager(topic=global_state.topic, project_type=global_state.project_type)
lit_manager = LiteratureManager()
finance_engine = FinanceEngine()

STITCH_DIR = os.path.join(os.path.dirname(__file__), "stitch_design")

# Монтируем статические файлы для JS и ресурсов
app.mount("/static", StaticFiles(directory=STITCH_DIR), name="static")

SCRIPT_INJECTION = """
<script>
document.addEventListener('DOMContentLoaded', () => {
    const stepPaths = {
        '1. Rules': '/step/1',
        '2. Sources': '/step/2',
        '3. Title': '/step/3',
        '4. Plan': '/step/4',
        '5. Studio': '/step/5',
        '6. Build': '/step/6'
    };

    const navItems = document.querySelectorAll('nav a');
    navItems.forEach((item) => {
        const text = item.textContent.trim();
        for (const [key, path] of Object.entries(stepPaths)) {
            if (text.includes(key)) {
                item.style.cursor = 'pointer';
                item.addEventListener('click', (e) => {
                    e.preventDefault();
                    window.location.href = path;
                });
            }
        }
    });

    // Оживляем кнопки действий
    document.querySelectorAll('button').forEach(btn => {
        const t = btn.innerText.toLowerCase();
        if (t.includes('plan') || t.includes('сгенерировать')) {
            btn.addEventListener('click', async () => {
                btn.innerText = '⚡ ИИ генерирует план (Gemini 3.6)...';
                try {
                    const res = await fetch('/api/generate-plan', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({topic: 'Разработка приложения платежного терминала на C#'})
                    });
                    const data = await res.json();
                    alert('План сформирован! Переходим к Студии Написания...');
                    window.location.href = '/step/5';
                } catch(e) { alert('Ошибка: ' + e); }
            });
        } else if (t.includes('build') || t.includes('сформировать') || t.includes('word')) {
            btn.addEventListener('click', async () => {
                btn.innerText = '🚀 Формирование .docx и запуск в Word...';
                try {
                    const res = await fetch('/api/build-docx', {method: 'POST'});
                    const data = await res.json();
                    alert('Документ сформирован по ГОСТу и открыт в MS Word!');
                } catch(e) { alert('Ошибка: ' + e); }
            });
        }
    });
});
</script>
"""

@app.get("/", response_class=HTMLResponse)
def get_home():
    step1_path = os.path.join(STITCH_DIR, "step1.html")
    if os.path.exists(step1_path):
        with open(step1_path, "r", encoding="utf-8") as f:
            content = f.read().replace("</body>", f"{SCRIPT_INJECTION}</body>")
            return HTMLResponse(content=content)
    return HTMLResponse("<h1>Stitch HTML not found</h1>")

@app.get("/step/{step_num}", response_class=HTMLResponse)
def get_step(step_num: int):
    step_file = os.path.join(STITCH_DIR, f"step{step_num}.html")
    if os.path.exists(step_file):
        with open(step_file, "r", encoding="utf-8") as f:
            content = f.read().replace("</body>", f"{SCRIPT_INJECTION}</body>")
            return HTMLResponse(content=content)
    raise HTTPException(status_code=404, detail="Step design screen not found")

@app.get("/api/state")
def get_state():
    return {
        "topic": global_state.topic,
        "project_type": global_state.project_type,
        "current_step": global_state.current_step,
        "formatting_rules": global_state.formatting_rules,
        "title_page_data": global_state.title_page_data,
        "plan_structure": global_state.plan_structure,
        "sections_content": global_state.sections_content,
        "blueprint": blueprint.get_context_payload()
    }

@app.post("/api/generate-plan")
def api_generate_plan(data: Dict[str, Any] = Body(...)):
    topic = data.get("topic", global_state.topic)
    project_type = data.get("project_type", global_state.project_type)
    global_state.topic = topic
    global_state.project_type = project_type
    
    plan = generator.generate_plan(topic, project_type)
    global_state.plan_structure = plan
    return {"status": "ok", "plan": plan}

@app.post("/api/generate-section")
def api_generate_section(data: Dict[str, Any] = Body(...)):
    sec_id = data.get("section_id", "1.1")
    sec_title = data.get("section_title", "Описание предметной области")
    
    text = generator.generate_paragraph_draft(sec_title, blueprint)
    global_state.add_section_content(sec_id, text)
    return {"status": "ok", "section_id": sec_id, "text": text}

@app.post("/api/anti-plagiarism")
def api_anti_plagiarism(data: Dict[str, Any] = Body(...)):
    selected_text = data.get("text", "")
    if not selected_text:
        raise HTTPException(status_code=400, detail="Text is required")
        
    rewritten = generator.rewrite_selected_text(selected_text)
    return {"status": "ok", "original": selected_text, "rewritten": rewritten}

@app.post("/api/build-docx")
def api_build_docx():
    try:
        config = FormattingRulesConfig(**global_state.formatting_rules)
        renderer = DocxRenderer(config)
        doc = renderer.create_document()

        # 1. Титульный лист
        title_data = TitlePageData(**global_state.title_page_data)
        title_data.topic = global_state.topic
        renderer.render_title_page(doc, title_data)

        # 2. Оглавление
        if global_state.plan_structure:
            renderer.add_table_of_contents_placeholder(doc, global_state.plan_structure)

        # 3. Разделы
        for item in global_state.plan_structure:
            sec_id = item.get("id")
            sec_title = item.get("title")
            
            if item.get("is_section_header", False):
                renderer.add_heading_1(doc, sec_title)
            else:
                renderer.add_heading_2(doc, sec_title)
                
            content = global_state.sections_content.get(sec_id, "")
            if content:
                for p in content.split("\n\n"):
                    if p.strip():
                        renderer.add_paragraph(doc, p.strip())

        output_filename = f"Готовая_работа_{global_state.project_id[:6]}.docx"
        output_path = os.path.abspath(output_filename)
        doc.save(output_path)
        
        if os.name == 'nt':
            os.startfile(output_path)
        elif sys.platform == 'darwin':
            subprocess.call(['open', output_path])
            
        return {"status": "ok", "filename": output_filename, "filepath": output_path}
    except Exception as e:
        logger.error(f"Build error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    print("🚀 Запуск Stitch Web UI на http://127.0.0.1:8000...")
    uvicorn.run(app, host="127.0.0.1", port=8000)
