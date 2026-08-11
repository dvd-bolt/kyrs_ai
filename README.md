# 🎓 PaperCraft AI Studio — Automated Academic Paper Generator

> **PaperCraft AI Studio** — это нативное десктопное приложение (`PyQt6`) и веб-студия (`FastAPI`), предназначенные для автоматизированной генерации и профессиональной верстки академических работ (курсовые работы, научные статьи, отчеты по практике и школьные проекты) по ГОСТу с использованием ИИ-моделей **Google Gemini 3.6 / 3.5** и **Imagen 4**.

---

## 🌟 Основные Возможности

- 📐 **Движок верстки ГОСТ (`python-docx`):**
  - Автоматическая настройка стилей, полей (3.0 / 1.5 / 2.0 / 2.0 см) и красной строки (1.25 см).
  - Скрытие колонтитулов на титульном листе, динамическая нумерация страниц (`{ PAGE }`).
  - Умное пропорциональное сжатие изображений (*Smart Image Fitter*) до 16.5 см.
  - Листинги кода в моноширинной рамке `#F4F4F4` и таблицы с серыми шапками по ГОСТу.
  - Возможность подшивки пользовательского `.docx` титульного листа от вуза.

- ⚡ **Каскадный LLM-Роутер (Gemini & Imagen 4):**
  - Горячее резервирование при исчерпании дневных лимитов (RPD):
    - `Gemini 3.6 Flash` (Архитектор / Вычитка / Рерайт) $\rightarrow$ `Gemini 3.5 Flash`.
    - `Gemini 3.5 Flash Lite` (Главный генератор текста) $\rightarrow$ `Gemini 3.1 Flash Lite`.
    - `Imagen 4 Ultra Generate` (ИИ-иллюстрации) $\rightarrow$ `Generate` $\rightarrow$ `Fast Generate`.
  - Трекинг лимитов RPD в интерфейсе приложения.

- 🤖 **Обход детекции ИИ (Burstiness & Perplexity Engine):**
  - **Burstiness Filter:** Ритмическое чередование сложноподчиненных предложений (20–30 слов) и рубленных фраз (3–5 слов).
  - **Perplexity Engine:** Запрет микро-выводов в конце абзацев и шаблона «тезис-аргумент-вывод».
  - **Strict Stop-Words Filter:** Автозамена запрещенных штампов («Таким образом» $\rightarrow$ «в итоге», «вследствие этого»; удаление «богатый гобелен», «важно отметить» и др.).
  - **Интерактивный рерайт:** Кнопка «Повысить уникальность» для подготовки фрагментов к проверке в «Антиплагиат.ВУЗ».

- 📊 **Встроенные генераторы графики и расчетов:**
  - **ScriptExecutor:** Безопасный запуск `matplotlib` / `pandas` / `seaborn` для создания графиков.
  - **MermaidRenderer:** Генерация UML/DFD/ERD диаграмм из кода Mermaid в PNG.
  - **FinanceEngine:** Движок бухгалтерского учета с двойной записью, балансовым тождеством и расчетными таблицами за 3 года (2022–2024 гг.).
  - **LiteratureManager:** Генерация библиографии по ГОСТ Р 7.0.100-2018 с расстановкой сносок вида `[1, c. 12]`.

---

## 🛠️ Стек Технологий

- **GUI & Web:** PyQt6 (Wizard-интерфейс из 6 шагов), FastAPI, HTML5/CSS3 (Stitch Design System).
- **ИИ SDK:** `google-genai` (Gemini API & Imagen 4).
- **Документы:** `python-docx`, `pypdf`, `Pillow`.
- **Вычисления и Анализ:** `pandas`, `matplotlib`, `seaborn`.
- **Тестирование:** `pytest`, `httpx`.

---

## 🚀 Быстрый Запуск

### 1. Клонирование репозитория и установка зависимостей

```bash
git clone https://github.com/username/kyrs_ai.git
cd kyrs_ai

# Создание и активация виртуального окружения
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Установка зависимостей
pip install -r requirements.txt
```

### 2. Установка API Ключа Gemini (опционально)

При отсутствии ключа приложения работают в режиме **Mock LLM** для локального тестирования.

```bash
# Windows PowerShell:
$env:GEMINI_API_KEY="your_api_key_here"

# Linux / macOS:
export GEMINI_API_KEY="your_api_key_here"
```

### 3. Запуск приложений

* **Нативное Desktop приложение (PyQt6):**
  ```bash
  python main.py
  ```

* **Веб-версия Studio (FastAPI):**
  ```bash
  python web_app.py
  ```
  После запуска откройте в браузере: `http://127.0.0.1:8000`

---

## 🧪 Запуск Тестов

В проекте настроена автоматическая сюита интеграционных и модульных тестов:

```bash
pytest -v
```

---

## 📂 Структура Проекта

```text
kyrs_ai/
├── core/                   # Движки верстки, Gemini API, финансов и иллюстраций
│   ├── blueprint.py        # Паспорт Проекта (Context Blueprint)
│   ├── cascade_llm.py      # Каскадный LLM-роутер и трекер RPD
│   ├── executor.py         # Безопасный исполняемый модуль графиков Python
│   ├── finance_engine.py   # Бухгалтерский движок двойной записи
│   ├── gemini_engine.py    # Генератор контента с фильтрами анти-ИИ
│   ├── image_gen.py        # Генерация иллюстраций Imagen 4
│   ├── literature.py       # Менеджер литературы по ГОСТ Р 7.0.100-2018
│   ├── mermaid_render.py   # Рендерер диаграмм Mermaid -> PNG
│   └── renderer.py         # Движок верстки python-docx
├── models/                 # Pydantic модели и управление состоянием
│   ├── config.py           # Конфигурация ГОСТ и пресеты
│   └── state.py            # Проектная сессия (.courseproject)
├── ui/                     # PyQt6 GUI навигация и 6 шагов мастера
│   ├── main_window.py      # Главное окно Wizard
│   ├── styles.py           # QSS стилистика Stitch Dark Mode
│   └── widgets/            # Виджеты экранов и живого А4 превью
├── stitch_design/          # Статические файлы для FastAPI Web UI
├── tests/                  # Тестовая сюита pytest
├── main.py                 # Точка входа Desktop GUI
├── web_app.py              # Точка входа Web API Studio
├── ROADMAP.md              # Мастер-спецификация и дорожная карта
└── requirements.txt        # Зависимости Python
```

---

## 📜 Лицензия

MIT License
