from __future__ import annotations

import json
import os
import re
from datetime import date
from decimal import Decimal
from enum import StrEnum
from html import escape
from pathlib import Path
from typing import Any

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from papercraft.application import DocumentService, ProjectService, ProjectWorkspace, SourceService
from papercraft.application.autopilot import PipelineStage
from papercraft.config import AppSettings
from papercraft.domain import (
    ArtifactKind,
    DomainProfile,
    GenerationRun,
    ProjectBrief,
    RunStatus,
    SourceRole,
    WorkType,
)
from papercraft.infrastructure.gemini import CredentialSecretStore
from papercraft.worker import WorkerRequest, worker_invocation

from .run_control import RunControlError, RunController

PAGE_NAMES = [
    "1. Проекты",
    "2. Задание и файлы",
    "3. Требования",
    "4. План и автопилот",
    "5. Прогресс",
    "6. Результат и QA",
]


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings | None = None) -> None:
        super().__init__()
        self.settings = settings or AppSettings.from_environment()
        self.projects = ProjectService(self.settings)
        self.workspace: ProjectWorkspace | None = None
        self.active_run_id: str | None = None
        self.process: QProcess | None = None
        self.process_buffer = ""
        self.process_error_buffer = ""
        self.event_cursor = 0
        self.setWindowTitle("PaperCraft AI Studio — автопилот академических работ")
        self.resize(1320, 820)
        self._build_ui()
        self._apply_style()
        self._refresh_projects()
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(900)
        self.poll_timer.timeout.connect(self._poll_run)
        self.poll_timer.start()

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        self.navigation = QListWidget()
        self.navigation.setFixedWidth(245)
        self.navigation.addItems(PAGE_NAMES)
        self.navigation.currentRowChanged.connect(self._navigate)
        self.pages = QStackedWidget()
        for page in (
            self._projects_page(),
            self._brief_page(),
            self._requirements_page(),
            self._plan_page(),
            self._progress_page(),
            self._result_page(),
        ):
            self.pages.addWidget(page)
        layout.addWidget(self.navigation)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)
        self.navigation.setCurrentRow(0)

    def _projects_page(self) -> QWidget:
        page, layout = self._page("Проекты", "Создайте проект или продолжите генерацию после перезапуска.")
        splitter = QSplitter()
        self.project_list = QListWidget()
        self.project_list.itemDoubleClicked.connect(lambda _item: self._open_selected_project())
        splitter.addWidget(self.project_list)
        editor = QWidget()
        form = QFormLayout(editor)
        self.new_topic = QTextEdit()
        self.new_topic.setPlaceholderText("Например: разработка системы учёта заявок на Python")
        self.new_topic.setMaximumHeight(130)
        self.new_type = self._enum_combo(WorkType)
        self.new_domain = self._enum_combo(DomainProfile)
        create = QPushButton("Создать проект")
        create.clicked.connect(self._create_project)
        form.addRow("Тема или задание", self.new_topic)
        form.addRow("Тип работы", self.new_type)
        form.addRow("Профиль", self.new_domain)
        form.addRow(create)
        splitter.addWidget(editor)
        layout.addWidget(splitter, 1)
        bar = QHBoxLayout()
        for label, callback in (
            ("Открыть выбранный", self._open_selected_project),
            ("Настроить ключ Gemini", self._set_api_key),
            ("Обновить", self._refresh_projects),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            bar.addWidget(button)
        bar.addStretch(1)
        layout.addLayout(bar)
        return page

    def _brief_page(self) -> QWidget:
        page, layout = self._page(
            "Задание и исходники",
            "Опишите результат и добавьте методичку, пример, данные, код и изображения.",
        )
        form = QFormLayout()
        self.title_edit = QTextEdit()
        self.title_edit.setMaximumHeight(55)
        self.topic_edit = QTextEdit()
        self.topic_edit.setMaximumHeight(75)
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setMinimumHeight(110)
        self.work_type_combo = self._enum_combo(WorkType)
        self.domain_combo = self._enum_combo(DomainProfile)
        self.consent_checkbox = QCheckBox(
            "Разрешаю отправку копий документов в Gemini на время запуска"
        )
        for label, widget in (
            ("Название", self.title_edit),
            ("Тема", self.topic_edit),
            ("Что нужно получить", self.prompt_edit),
            ("Тип", self.work_type_combo),
            ("Профиль", self.domain_combo),
            ("Обработка", self.consent_checkbox),
        ):
            form.addRow(label, widget)
        layout.addLayout(form)
        title_box = QGroupBox("Данные титульного листа")
        title_layout = QGridLayout(title_box)
        title_fields = (
            ("university", "Вуз"),
            ("faculty", "Факультет"),
            ("department", "Кафедра"),
            ("subject", "Дисциплина"),
            ("student", "Студент"),
            ("supervisor", "Руководитель"),
            ("city", "Город"),
            ("year", "Год"),
        )
        self.title_page_fields: dict[str, QLineEdit] = {}
        for index, (key, label) in enumerate(title_fields):
            row, pair = divmod(index, 2)
            column = pair * 2
            editor = QLineEdit()
            if key == "year":
                editor.setText(str(date.today().year))
            self.title_page_fields[key] = editor
            title_layout.addWidget(QLabel(label), row, column)
            title_layout.addWidget(editor, row, column + 1)
        title_layout.setColumnStretch(1, 1)
        title_layout.setColumnStretch(3, 1)
        layout.addWidget(title_box)
        source_bar = QHBoxLayout()
        self.source_role = self._enum_combo(SourceRole)
        self._set_combo(self.source_role, SourceRole.METHODOLOGY)
        add_files = QPushButton("Добавить файлы")
        add_files.clicked.connect(self._add_files)
        add_folder = QPushButton("Добавить папку/проект")
        add_folder.clicked.connect(self._add_folder)
        source_bar.addWidget(QLabel("Роль:"))
        source_bar.addWidget(self.source_role)
        source_bar.addWidget(add_files)
        source_bar.addWidget(add_folder)
        source_bar.addStretch(1)
        layout.addLayout(source_bar)
        self.sources_table = self._table(["Файл", "Роль", "Размер", "SHA-256"], stretch_column=0)
        layout.addWidget(self.sources_table, 1)
        brief_actions = QHBoxLayout()
        save = QPushButton("Сохранить задание")
        save.clicked.connect(self._save_project)
        rebuild = QPushButton("Сохранить и пересобрать отсюда")
        rebuild.clicked.connect(self._save_and_rebuild_from_inputs)
        brief_actions.addStretch(1)
        brief_actions.addWidget(save)
        brief_actions.addWidget(rebuild)
        layout.addLayout(brief_actions)
        return page

    def _requirements_page(self) -> QWidget:
        page, layout = self._page(
            "Извлечённые требования", "Каждое правило хранит происхождение и приоритет."
        )
        self.requirements_table = self._table(
            ["Категория", "Ключ", "Требование", "Обязательно", "Уверенность"],
            stretch_column=2,
        )
        layout.addWidget(self.requirements_table)
        requirements_actions = QHBoxLayout()
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(self._refresh_requirements)
        rebuild = QPushButton("Пересобрать требования отсюда")
        rebuild.clicked.connect(
            lambda: self._retry_from_stage(PipelineStage.EXTRACT_REQUIREMENTS)
        )
        requirements_actions.addStretch(1)
        requirements_actions.addWidget(refresh)
        requirements_actions.addWidget(rebuild)
        layout.addLayout(requirements_actions)
        return page

    def _plan_page(self) -> QWidget:
        page, layout = self._page(
            "План и настройки автопилота",
            "По умолчанию весь конвейер проходит без остановок.",
        )
        options = QHBoxLayout()
        self.check_requirements = QCheckBox("Пауза после требований")
        self.check_outline = QCheckBox("Пауза после плана")
        self.check_final = QCheckBox("Пауза перед выпуском")
        self.cost_enabled = QCheckBox("Лимит, USD")
        self.cost_limit = QDoubleSpinBox()
        self.cost_limit.setRange(0.01, 10000)
        self.cost_limit.setValue(20)
        for widget in (
            self.check_requirements,
            self.check_outline,
            self.check_final,
            self.cost_enabled,
            self.cost_limit,
        ):
            options.addWidget(widget)
        options.addStretch(1)
        layout.addLayout(options)
        self.plan_tree = QTreeWidget()
        self.plan_tree.setHeaderLabels(["Раздел", "Объём", "Тезисы и зависимости"])
        self.plan_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.plan_tree.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        layout.addWidget(self.plan_tree, 1)
        bar = QHBoxLayout()
        refresh = QPushButton("Обновить план")
        refresh.clicked.connect(self._refresh_plan)
        save_plan = QPushButton("Сохранить правки плана")
        save_plan.clicked.connect(self._save_plan_edits)
        self.plan_retry_stage_combo = self._enum_combo(PipelineStage)
        start = QPushButton("Запустить автопилот")
        start.setObjectName("primary")
        start.clicked.connect(self._start_autopilot)
        rebuild = QPushButton("Пересобрать отсюда")
        rebuild.setToolTip("Повторить конвейер от выбранного этапа")
        rebuild.clicked.connect(
            lambda: self._retry_from_combo(self.plan_retry_stage_combo)
        )
        bar.addWidget(refresh)
        bar.addWidget(save_plan)
        bar.addWidget(self.plan_retry_stage_combo)
        bar.addWidget(rebuild)
        bar.addStretch(1)
        bar.addWidget(start)
        layout.addLayout(bar)
        return page

    def _progress_page(self) -> QWidget:
        page, layout = self._page(
            "Живой прогресс", "Этапы сохраняют checkpoint; закрытие приложения не теряет результат."
        )
        summary = QHBoxLayout()
        self.run_status = QLabel("Запуск не создан")
        self.run_stage = QLabel("—")
        self.run_cost = QLabel("0.0000 USD")
        for widget in (
            self.run_status,
            QLabel("Этап:"),
            self.run_stage,
            QLabel("Стоимость:"),
            self.run_cost,
        ):
            summary.addWidget(widget)
        summary.addStretch(1)
        layout.addLayout(summary)
        self.stages_table = self._table(["№", "Этап", "Статус", "Попытки"], stretch_column=1)
        layout.addWidget(self.stages_table, 2)
        self.events_view = QTextBrowser()
        layout.addWidget(self.events_view, 1)
        bar = QHBoxLayout()
        self.pause_button = QPushButton("Пауза")
        self.resume_button = QPushButton("Продолжить / подтвердить")
        self.cancel_button = QPushButton("Отменить")
        self.retry_stage_combo = self._enum_combo(PipelineStage)
        self.retry_stage_button = QPushButton("Пересобрать отсюда")
        self.pause_button.clicked.connect(self._pause_run)
        self.resume_button.clicked.connect(self._resume_run)
        self.cancel_button.clicked.connect(self._cancel_run)
        self.retry_stage_button.clicked.connect(self._retry_from_selected_stage)
        for button in (
            self.pause_button,
            self.resume_button,
            self.cancel_button,
            self.retry_stage_button,
        ):
            button.setEnabled(False)
        for button in (self.pause_button, self.resume_button, self.cancel_button):
            bar.addWidget(button)
        bar.addStretch(1)
        bar.addWidget(QLabel("Этап:"))
        bar.addWidget(self.retry_stage_combo)
        bar.addWidget(self.retry_stage_button)
        layout.addLayout(bar)
        return page

    def _result_page(self) -> QWidget:
        page, layout = self._page(
            "Предпросмотр, QA и экспорт",
            "Доступны только реально созданные и проверенные артефакты.",
        )
        splitter = QSplitter()
        self.artifact_list = QListWidget()
        self.artifact_list.itemDoubleClicked.connect(self._open_artifact)
        self.qa_view = QTextBrowser()
        splitter.addWidget(self.artifact_list)
        splitter.addWidget(self.qa_view)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        rebuild_box = QGroupBox("Точечная перегенерация")
        rebuild_layout = QHBoxLayout(rebuild_box)
        self.result_section_combo = QComboBox()
        rebuild_section = QPushButton("Перегенерировать раздел")
        rebuild_section.clicked.connect(self._rebuild_selected_section)
        rebuild_from = QPushButton("Пересобрать документ отсюда")
        rebuild_from.clicked.connect(
            lambda: self._retry_from_stage(PipelineStage.GENERATE_SECTIONS)
        )
        rebuild_layout.addWidget(QLabel("Раздел:"))
        rebuild_layout.addWidget(self.result_section_combo, 1)
        rebuild_layout.addWidget(rebuild_section)
        rebuild_layout.addWidget(rebuild_from)
        layout.addWidget(rebuild_box)
        bar = QHBoxLayout()
        actions = (
            ("Предпросмотр страниц", self._open_preview),
            ("Открыть DOCX в Word", self._open_word),
            ("Экспорт DOCX", lambda: self._export(ArtifactKind.DOCX)),
            ("Экспорт PDF", lambda: self._export(ArtifactKind.PDF)),
            ("Папка проекта", self._open_project_folder),
            ("Обновить", self._refresh_results),
        )
        for label, callback in actions:
            button = QPushButton(label)
            button.clicked.connect(callback)
            bar.addWidget(button)
        bar.addStretch(1)
        layout.addLayout(bar)
        return page

    @staticmethod
    def _page(title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        description = QLabel(subtitle)
        description.setObjectName("subtitle")
        description.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(description)
        return page, layout

    @staticmethod
    def _enum_combo(enum_type: type[StrEnum]) -> QComboBox:
        combo = QComboBox()
        for item in enum_type:
            combo.addItem(item.value.replace("_", " ").title(), item.value)
        return combo

    @staticmethod
    def _table(headers: list[str], *, stretch_column: int) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(stretch_column, QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        return table

    def _navigate(self, index: int) -> None:
        if index > 0 and self.workspace is None:
            self.navigation.blockSignals(True)
            self.navigation.setCurrentRow(0)
            self.navigation.blockSignals(False)
            return
        self.pages.setCurrentIndex(max(0, index))
        if index == 2:
            self._refresh_requirements()
        elif index == 3:
            self._refresh_plan()
        elif index == 5:
            self._refresh_results()

    def _refresh_projects(self) -> None:
        self.project_list.clear()
        for project in self.projects.list():
            item = QListWidgetItem(
                f"{project.brief.title or project.brief.topic or 'Без названия'}\n"
                f"{project.updated_at:%d.%m.%Y %H:%M}"
            )
            item.setData(Qt.ItemDataRole.UserRole, project.id)
            self.project_list.addItem(item)

    def _create_project(self) -> None:
        topic = self.new_topic.toPlainText().strip()
        if not topic:
            self._error("Укажите тему или задание")
            return
        self.workspace = self.projects.create(
            ProjectBrief(
                title=topic[:160],
                topic=topic,
                prompt=topic,
                work_type=self.new_type.currentData(),
                domain_profile=self.new_domain.currentData(),
            )
        )
        self.active_run_id = None
        self.event_cursor = 0
        self.events_view.clear()
        self._load_workspace()
        self._refresh_projects()
        self.navigation.setCurrentRow(1)

    def _open_selected_project(self) -> None:
        item = self.project_list.currentItem()
        if item is None:
            self._error("Выберите проект")
            return
        try:
            self.workspace = self.projects.open(str(item.data(Qt.ItemDataRole.UserRole)))
        except Exception as exc:
            self._error(str(exc))
            return
        self._load_workspace()
        runs = self.workspace.repository.list_runs(self.workspace.project.id)
        self.active_run_id = runs[0].id if runs else None
        self.event_cursor = 0
        self.events_view.clear()
        self.navigation.setCurrentRow(1)

    def _load_workspace(self) -> None:
        assert self.workspace is not None
        brief, options = self.workspace.project.brief, self.workspace.project.options
        self.title_edit.setPlainText(brief.title)
        self.topic_edit.setPlainText(brief.topic)
        self.prompt_edit.setPlainText(brief.prompt)
        self._set_combo(self.work_type_combo, brief.work_type)
        self._set_combo(self.domain_combo, brief.domain_profile)
        for key, editor in self.title_page_fields.items():
            fallback = date.today().year if key == "year" else ""
            editor.setText(str(brief.title_page.get(key, fallback)))
        self.consent_checkbox.setChecked(options.consent_to_remote_processing)
        self.check_requirements.setChecked(options.checkpoint_requirements)
        self.check_outline.setChecked(options.checkpoint_outline)
        self.check_final.setChecked(options.checkpoint_final_review)
        self.cost_enabled.setChecked(options.maximum_cost is not None)
        if options.maximum_cost is not None:
            self.cost_limit.setValue(float(options.maximum_cost))
        self._refresh_sources()
        self._refresh_requirements()
        self._refresh_plan()
        self._refresh_results()

    @staticmethod
    def _set_combo(combo: QComboBox, value: Any) -> None:
        normalized = value.value if isinstance(value, StrEnum) else value
        index = combo.findData(normalized)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _save_project(self) -> bool:
        if self.workspace is None:
            return False
        topic, prompt = self.topic_edit.toPlainText().strip(), self.prompt_edit.toPlainText().strip()
        if not topic and not prompt:
            self._error("Нужна тема или формулировка задания")
            return False
        brief = self.workspace.project.brief.model_copy(
            update={
                "title": self.title_edit.toPlainText().strip() or topic,
                "topic": topic,
                "prompt": prompt,
                "work_type": WorkType(str(self.work_type_combo.currentData())),
                "domain_profile": DomainProfile(str(self.domain_combo.currentData())),
            }
        )
        options = self.workspace.project.options.model_copy(
            update={
                "consent_to_remote_processing": self.consent_checkbox.isChecked(),
                "checkpoint_requirements": self.check_requirements.isChecked(),
                "checkpoint_outline": self.check_outline.isChecked(),
                "checkpoint_final_review": self.check_final.isChecked(),
                "maximum_cost": Decimal(str(self.cost_limit.value())) if self.cost_enabled.isChecked() else None,
            }
        )
        title_page = dict(self.workspace.project.brief.title_page)
        for key, editor in self.title_page_fields.items():
            title_page[key] = editor.text().strip()
        raw_year = str(title_page.get("year") or "").strip()
        if raw_year:
            if not raw_year.isdigit() or not 1900 <= int(raw_year) <= 2200:
                self._error("Год на титульном листе должен быть от 1900 до 2200")
                return False
            title_page["year"] = int(raw_year)
        title_page["topic"] = topic
        work_type = WorkType(str(self.work_type_combo.currentData()))
        title_page["work_type"] = work_type.value.replace("_", " ").upper()
        brief.title_page = title_page
        self.workspace = self.projects.update(self.workspace.project.id, brief=brief, options=options)
        self.statusBar().showMessage("Проект сохранён", 3000)
        return True

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Выберите исходники")
        if paths:
            self._import_sources(paths)

    def _add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Выберите папку с материалами")
        if path:
            self._import_sources([path])

    def _import_sources(self, paths: list[str]) -> None:
        if self.workspace is None:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = SourceService(self.workspace).import_files(paths, self.source_role.currentData())
            self.statusBar().showMessage(
                f"Импортировано: {len(result.sources)}; отклонено: {len(result.rejected)}", 6000
            )
        except Exception as exc:
            self._error(str(exc))
        finally:
            QApplication.restoreOverrideCursor()
        self._refresh_sources()

    def _refresh_sources(self) -> None:
        self.sources_table.setRowCount(0)
        if self.workspace is None:
            return
        for source in self.workspace.repository.list_sources(self.workspace.project.id):
            if source.metadata.get("generated"):
                continue
            self._append_row(
                self.sources_table,
                [source.original_name, source.role.value, _human_size(source.size_bytes), source.sha256[:16] + "…"],
            )

    def _refresh_requirements(self) -> None:
        self.requirements_table.setRowCount(0)
        if self.workspace is None:
            return
        requirements = self.workspace.repository.get_latest_requirement_set(self.workspace.project.id)
        if requirements:
            for rule in requirements.rules:
                self._append_row(
                    self.requirements_table,
                    [rule.category.value, rule.key, rule.statement, "да" if rule.mandatory else "нет", f"{rule.confidence:.0%}"],
                )

    def _refresh_plan(self) -> None:
        self.plan_tree.clear()
        if self.workspace is None:
            return
        blueprint = self.workspace.repository.get_latest_blueprint(self.workspace.project.id)
        if blueprint:
            for section in sorted(blueprint.outline.sections, key=lambda item: item.order):
                details = "; ".join(section.theses[:3])
                item = QTreeWidgetItem([section.title, f"{section.target_words} слов", details])
                item.setData(0, Qt.ItemDataRole.UserRole, section.id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                self.plan_tree.addTopLevelItem(item)

    def _save_plan_edits(self) -> None:
        if self.workspace is None:
            return
        blueprint = self.workspace.repository.get_latest_blueprint(self.workspace.project.id)
        if blueprint is None:
            self._error("План ещё не создан")
            return
        edits: dict[str, tuple[str, int]] = {}
        for index in range(self.plan_tree.topLevelItemCount()):
            item = self.plan_tree.topLevelItem(index)
            section_id = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
            title = item.text(0).strip()
            match = re.search(r"\d+", item.text(1))
            if not section_id or not title or match is None:
                self._error("У каждого раздела должны быть название и объём")
                return
            edits[section_id] = (title, int(match.group()))
        sections = [
            section.model_copy(
                update={
                    "title": edits.get(section.id, (section.title, section.target_words))[0],
                    "target_words": edits.get(section.id, (section.title, section.target_words))[1],
                }
            )
            for section in blueprint.outline.sections
        ]
        updated = blueprint.model_copy(
            update={"outline": blueprint.outline.model_copy(update={"sections": sections})}
        )
        self.workspace.repository.save_blueprint(updated)
        self.statusBar().showMessage("План сохранён; выберите этап для пересборки", 5000)
        self._refresh_plan()

    @staticmethod
    def _append_row(table: QTableWidget, values: list[Any]) -> None:
        row = table.rowCount()
        table.insertRow(row)
        for column, value in enumerate(values):
            table.setItem(row, column, QTableWidgetItem(str(value)))

    def _start_autopilot(self) -> None:
        if self.workspace is None:
            return
        if not self._save_project():
            return
        if not self.workspace.project.options.consent_to_remote_processing:
            self._error("Подтвердите обработку документов Gemini на экране задания")
            return
        unfinished = next(
            (
                run
                for run in self.workspace.repository.list_runs(self.workspace.project.id)
                if run.status
                in {
                    RunStatus.QUEUED,
                    RunStatus.RUNNING,
                    RunStatus.RETRYING,
                    RunStatus.PAUSED,
                    RunStatus.WAITING_INPUT,
                    RunStatus.FAILED,
                }
            ),
            None,
        )
        self.active_run_id = unfinished.id if unfinished else None
        self.event_cursor = 0
        self.events_view.clear()
        self._launch_worker(
            WorkerRequest(
                project_id=self.workspace.project.id,
                projects_root=self.settings.projects_root,
                run_id=self.active_run_id,
                acknowledge_checkpoint=(
                    unfinished is not None and unfinished.status == RunStatus.WAITING_INPUT
                ),
            )
        )
        self.navigation.setCurrentRow(4)

    def _save_and_rebuild_from_inputs(self) -> None:
        if not self._save_project() or self.workspace is None:
            return
        runs = self.workspace.repository.list_runs(self.workspace.project.id)
        self.active_run_id = runs[0].id if runs else None
        if self.active_run_id is None:
            self._start_autopilot()
            return
        self._retry_from_stage(PipelineStage.INGEST)

    def _launch_worker(self, request: WorkerRequest) -> None:
        if self.workspace is None:
            return
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            self._error("Worker уже выполняется")
            return
        process = QProcess(self)
        process.setWorkingDirectory(str(self.workspace.paths.root))
        environment = QProcessEnvironment.systemEnvironment()
        source_root = str(Path(__file__).resolve().parents[2])
        existing = environment.value("PYTHONPATH")
        environment.insert("PYTHONPATH", source_root + (os.pathsep + existing if existing else ""))
        environment.insert("PYTHONUTF8", "1")
        process.setProcessEnvironment(environment)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.readyReadStandardOutput.connect(self._read_worker_output)
        process.readyReadStandardError.connect(self._read_worker_error)
        process.finished.connect(self._worker_finished)
        process.errorOccurred.connect(self._worker_process_error)
        try:
            program, arguments = worker_invocation(request)
        except OSError as exc:
            process.deleteLater()
            self._error(str(exc))
            return
        self.process_buffer = ""
        self.process_error_buffer = ""
        self.process = process
        process.start(program, arguments)

    def _worker_process_error(self, error: QProcess.ProcessError) -> None:
        if self.process is None:
            return
        self.events_view.append(
            f"<span style='color:#b91c1c'>Worker: {escape(self.process.errorString())} "
            f"({escape(error.name)})</span>"
        )

    def _read_worker_output(self) -> None:
        assert self.process is not None
        self.process_buffer += bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        while "\n" in self.process_buffer:
            line, self.process_buffer = self.process_buffer.split("\n", 1)
            self._handle_worker_line(line)

    def _handle_worker_line(self, line: str) -> None:
        if not line.strip():
            return
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            self.events_view.append(escape(line))
            return
        if not isinstance(payload, dict):
            return
        if payload.get("run_id"):
            run_id = str(payload["run_id"])
            if self.active_run_id != run_id:
                self.active_run_id = run_id
                self.event_cursor = 0
        event = str(payload.get("event") or "worker")
        if payload.get("error"):
            self.events_view.append(f"<span style='color:#b91c1c'>{escape(str(payload['error']))}</span>")
        elif event == "run_ready":
            self.statusBar().showMessage(f"Worker готов: {payload.get('action', 'execute')}", 3000)

    def _read_worker_error(self) -> None:
        assert self.process is not None
        self.process_error_buffer += bytes(self.process.readAllStandardError()).decode(
            "utf-8", errors="replace"
        )
        while "\n" in self.process_error_buffer:
            line, self.process_error_buffer = self.process_error_buffer.split("\n", 1)
            self._handle_worker_line(line)

    def _worker_finished(self, exit_code: int, _status) -> None:
        process = self.process
        if process is not None:
            self._read_worker_output()
            self._read_worker_error()
        if self.process_buffer.strip():
            self._handle_worker_line(self.process_buffer)
            self.process_buffer = ""
        if self.process_error_buffer.strip():
            self._handle_worker_line(self.process_error_buffer)
            self.process_error_buffer = ""
        if exit_code:
            self.statusBar().showMessage(f"Worker завершился с кодом {exit_code}", 8000)
        self.process = None
        if process is not None:
            process.deleteLater()
        self._poll_run()

    def _poll_run(self) -> None:
        if self.workspace is None:
            return
        if self.active_run_id is None:
            runs = self.workspace.repository.list_runs(self.workspace.project.id)
            self.active_run_id = runs[0].id if runs else None
        if self.active_run_id is None:
            return
        run = self.workspace.repository.get_run(self.active_run_id)
        if run is None:
            return
        self.run_status.setText(run.status.value.upper())
        self.run_stage.setText(run.current_stage or "—")
        self.run_cost.setText(f"{run.cost:.4f} {run.currency}")
        stages = self.workspace.repository.list_stages(run.id)
        self.stages_table.setRowCount(0)
        for stage in stages:
            self._append_row(self.stages_table, [stage.order + 1, stage.name, stage.status.value, stage.attempts])
        for sequence, event in self.workspace.repository.list_events(run.id, after_sequence=self.event_cursor):
            self.event_cursor = max(self.event_cursor, sequence)
            self.events_view.append(f"[{event.created_at:%H:%M:%S}] {event.message or event.event_type}")
        if run.status == RunStatus.SUCCEEDED:
            self._refresh_results()
        self._update_run_actions(run)

    def _update_run_actions(self, run: GenerationRun) -> None:
        process_running = (
            self.process is not None
            and self.process.state() != QProcess.ProcessState.NotRunning
        )
        self.pause_button.setEnabled(run.status in {RunStatus.RUNNING, RunStatus.RETRYING})
        self.resume_button.setEnabled(
            not process_running
            and run.status in {RunStatus.PAUSED, RunStatus.WAITING_INPUT, RunStatus.FAILED}
        )
        self.cancel_button.setEnabled(
            run.status not in {RunStatus.SUCCEEDED, RunStatus.CANCELLED}
        )
        self.retry_stage_button.setEnabled(
            not process_running
            and run.status
            in {RunStatus.PAUSED, RunStatus.WAITING_INPUT, RunStatus.FAILED, RunStatus.SUCCEEDED}
        )

    def _pause_run(self) -> None:
        if self.workspace is None or self.active_run_id is None:
            self._error("Активный запуск не выбран")
            return
        try:
            RunController(self.workspace.repository).pause(self.active_run_id)
        except RunControlError as exc:
            self._error(str(exc))
        self._poll_run()

    def _resume_run(self) -> None:
        run = self._active_run()
        if run is None or self.workspace is None or run.status not in {RunStatus.PAUSED, RunStatus.WAITING_INPUT, RunStatus.FAILED}:
            self._error("Этот запуск сейчас нельзя продолжить")
            return
        self._launch_worker(
            WorkerRequest(
                project_id=self.workspace.project.id,
                projects_root=self.settings.projects_root,
                run_id=run.id,
                acknowledge_checkpoint=run.status == RunStatus.WAITING_INPUT,
            )
        )

    def _cancel_run(self) -> None:
        if self.workspace is None or self.active_run_id is None:
            self._error("Активный запуск не выбран")
            return
        try:
            RunController(self.workspace.repository).cancel(self.active_run_id)
        except RunControlError as exc:
            self._error(str(exc))
            return
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            self.statusBar().showMessage(
                "Отмена сохранена; worker завершит текущую операцию и безопасно очистит удалённые файлы",
                8000,
            )
        else:
            self._launch_worker(
                WorkerRequest(
                    project_id=self.workspace.project.id,
                    projects_root=self.settings.projects_root,
                    run_id=self.active_run_id,
                    cancel=True,
                )
            )
        self._poll_run()

    def _active_run(self) -> GenerationRun | None:
        if self.workspace is None or self.active_run_id is None:
            return None
        return self.workspace.repository.get_run(self.active_run_id)

    def _refresh_results(self) -> None:
        self.artifact_list.clear()
        self.qa_view.clear()
        if self.workspace is None:
            return
        artifacts = self.workspace.repository.list_artifacts(
            self.workspace.project.id, run_id=self.active_run_id
        )
        for artifact in artifacts:
            item = QListWidgetItem(f"{artifact.kind.value}: {Path(artifact.path).name}")
            item.setData(Qt.ItemDataRole.UserRole, artifact.path)
            self.artifact_list.addItem(item)
        if self.active_run_id:
            report = self.workspace.repository.get_latest_qa_report(self.active_run_id)
            if report:
                rows = "".join(
                    f"<li><b>{escape(issue.severity.value)}</b> — {escape(issue.message)}</li>"
                    for issue in report.issues
                )
                self.qa_view.setHtml(
                    f"<h2>QA: {escape(report.status.value.upper())}</h2>"
                    f"<p>{escape(report.summary)}</p><ul>{rows}</ul>"
                )
        self._refresh_result_sections()

    def _refresh_result_sections(self) -> None:
        previous = self.result_section_combo.currentData()
        self.result_section_combo.clear()
        if self.workspace is None:
            return
        blueprint = self.workspace.repository.get_latest_blueprint(self.workspace.project.id)
        if blueprint is None:
            return
        for section in sorted(blueprint.outline.sections, key=lambda item: item.order):
            self.result_section_combo.addItem(section.title, section.id)
        index = self.result_section_combo.findData(previous)
        if index >= 0:
            self.result_section_combo.setCurrentIndex(index)

    def _retry_from_selected_stage(self) -> None:
        self._retry_from_combo(self.retry_stage_combo)

    def _retry_from_combo(self, combo: QComboBox) -> None:
        raw_stage = combo.currentData()
        if raw_stage:
            self._retry_from_stage(PipelineStage(str(raw_stage)))

    def _retry_from_stage(self, stage: PipelineStage) -> None:
        run = self._active_run()
        if run is None or self.workspace is None:
            self._error("Сначала запустите автопилот")
            return
        if run.status in {RunStatus.RUNNING, RunStatus.RETRYING, RunStatus.QUEUED}:
            self._error("Перед пересборкой дождитесь паузы или завершения текущего этапа")
            return
        self._launch_worker(
            WorkerRequest(
                project_id=self.workspace.project.id,
                projects_root=self.settings.projects_root,
                run_id=run.id,
                retry_from=stage.value,
            )
        )
        self.navigation.setCurrentRow(4)

    def _rebuild_selected_section(self) -> None:
        run = self._active_run()
        section_id = self.result_section_combo.currentData()
        if run is None or self.workspace is None or not section_id:
            self._error("Выберите готовый запуск и раздел")
            return
        if run.status in {RunStatus.RUNNING, RunStatus.RETRYING, RunStatus.QUEUED}:
            self._error("Сначала остановите текущую генерацию")
            return
        self._launch_worker(
            WorkerRequest(
                project_id=self.workspace.project.id,
                projects_root=self.settings.projects_root,
                run_id=run.id,
                rebuild_section_id=str(section_id),
            )
        )
        self.navigation.setCurrentRow(4)

    def _open_preview(self) -> None:
        if self.workspace is None or self.active_run_id is None:
            self._error("Предпросмотр пока не создан")
            return
        pages = DocumentService(
            self.workspace.project.id, self.workspace.repository
        ).preview(self.active_run_id)
        if not pages:
            self._error("В этом запуске нет постраничного предпросмотра")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(pages[0])))

    def _open_artifact(self, item: QListWidgetItem) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(item.data(Qt.ItemDataRole.UserRole))))

    def _open_word(self) -> None:
        if self.workspace:
            try:
                DocumentService(self.workspace.project.id, self.workspace.repository).open_in_word(self.active_run_id)
            except Exception as exc:
                self._error(str(exc))

    def _export(self, kind: ArtifactKind) -> None:
        if self.workspace is None:
            return
        suffix = ".docx" if kind == ArtifactKind.DOCX else ".pdf"
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт", filter=f"{suffix.upper()} (*{suffix})")
        if path:
            try:
                result = DocumentService(self.workspace.project.id, self.workspace.repository).export(kind, path, self.active_run_id)
                self.statusBar().showMessage(f"Сохранено: {result}", 6000)
            except Exception as exc:
                self._error(str(exc))

    def _open_project_folder(self) -> None:
        if self.workspace:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.workspace.paths.root)))

    def _set_api_key(self) -> None:
        key, accepted = QInputDialog.getText(
            self, "Ключ Gemini", "API key:", QLineEdit.EchoMode.Password
        )
        if accepted:
            try:
                CredentialSecretStore().set_api_key(key)
                self.statusBar().showMessage("Ключ сохранён в Windows Credential Manager", 5000)
            except Exception as exc:
                self._error(str(exc))

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background:#f7f8fb; color:#172033; font-size:13px; }
            QListWidget { background:#111827; color:#dbeafe; border:none; padding:10px; }
            QListWidget::item { padding:12px 10px; border-radius:7px; }
            QListWidget::item:selected { background:#2563eb; color:white; }
            QTableWidget, QTreeWidget, QTextEdit, QTextBrowser, QComboBox, QDoubleSpinBox {
                background:white; border:1px solid #d7dce5; border-radius:6px; padding:5px;
            }
            QPushButton { background:white; border:1px solid #cbd5e1; border-radius:7px; padding:8px 14px; }
            QPushButton:hover { border-color:#2563eb; }
            QPushButton#primary { background:#2563eb; color:white; border:none; font-weight:600; }
            QLabel#pageTitle { font-size:25px; font-weight:700; color:#0f172a; }
            QLabel#subtitle { color:#64748b; margin-bottom:10px; }
            """
        )

    def _error(self, message: str) -> None:
        QMessageBox.warning(self, "PaperCraft", message)


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


__all__ = ["MainWindow"]
