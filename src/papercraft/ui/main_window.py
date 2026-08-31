from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import IntEnum, StrEnum
from html import escape
from pathlib import Path
from typing import Any

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from papercraft.application import (
    DocumentService,
    ProjectService,
    ProjectWorkspace,
    SectionRevisionService,
    SourceService,
)
from papercraft.application.autopilot import PipelineStage
from papercraft.config import AppSettings
from papercraft.domain import (
    ArtifactKind,
    DomainProfile,
    FactOrigin,
    GenerationRun,
    HeadingBlock,
    ParagraphBlock,
    ProjectBrief,
    RunStatus,
    SourceRole,
    StageStatus,
    WorkType,
)
from papercraft.infrastructure.gemini import CredentialSecretStore
from papercraft.worker import WorkerRequest, worker_invocation

from .icons import icon
from .labels import label_for
from .run_control import RunControlError, RunController
from .theme import ACCENT_SECONDARY, ERROR, SUCCESS, TEXT_MUTED, WARNING, dark_stylesheet
from .widgets import Card, CollapsibleSection, EmptyState, MetricCard, SectionHeader, StatusBadge


class WorkspaceStep(IntEnum):
    """Stable UI page identifiers for the four-step project workspace."""

    PROJECT = 0
    PLAN = 1
    GENERATE = 2
    RESULT = 3


PAGE_NAMES = ["Проект", "План", "Генерация", "Результат"]

_STEP_DETAILS: tuple[tuple[str, str], ...] = (
    ("Проект", "Задание и материалы"),
    ("План", "Требования и структура"),
    ("Генерация", "Автопилот и прогресс"),
    ("Результат", "Документ и проверка"),
)

_PHASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Подготовка",
        (
            PipelineStage.PREFLIGHT.value,
            PipelineStage.INGEST.value,
            PipelineStage.EXTRACT_REQUIREMENTS.value,
            PipelineStage.BUILD_EVIDENCE_INDEX.value,
        ),
    ),
    (
        "Исследование",
        (
            PipelineStage.VERIFIED_RESEARCH.value,
            PipelineStage.PLAN.value,
            PipelineStage.BUILD_FACTS_AND_DATASETS.value,
        ),
    ),
    (
        "Написание",
        (
            PipelineStage.GENERATE_SECTIONS.value,
            PipelineStage.GENERATE_VISUALS.value,
            PipelineStage.CITATION_AUDIT.value,
            PipelineStage.CONSISTENCY_QA.value,
        ),
    ),
    (
        "Сборка документа",
        (
            PipelineStage.RENDER_DOCX.value,
            PipelineStage.WORD_FINALIZE.value,
            PipelineStage.EXPORT_PDF.value,
            PipelineStage.PDF_VISUAL_QA.value,
            PipelineStage.FINAL_GEMINI_REVIEW.value,
            PipelineStage.PACKAGE.value,
        ),
    ),
)


RUN_STATUS_LABELS = {
    RunStatus.QUEUED: "В очереди",
    RunStatus.RUNNING: "Выполняется",
    RunStatus.RETRYING: "Повторная попытка",
    RunStatus.PAUSED: "На паузе",
    RunStatus.WAITING_INPUT: "Ожидает действия",
    RunStatus.SUCCEEDED: "Готово",
    RunStatus.FAILED: "Ошибка",
    RunStatus.CANCELLED: "Отменено",
}

STAGE_STATUS_LABELS = {
    StageStatus.QUEUED: "В очереди",
    StageStatus.RUNNING: "Выполняется",
    StageStatus.RETRYING: "Повторная попытка",
    StageStatus.PAUSED: "На паузе",
    StageStatus.WAITING_INPUT: "Ожидает действия",
    StageStatus.SUCCEEDED: "Готово",
    StageStatus.FAILED: "Ошибка",
    StageStatus.CANCELLED: "Отменено",
    StageStatus.SKIPPED: "Пропущено",
}

STAGE_LABELS = {
    PipelineStage.PREFLIGHT.value: "Проверка готовности",
    PipelineStage.INGEST.value: "Загрузка и разбор файлов",
    PipelineStage.EXTRACT_REQUIREMENTS.value: "Извлечение требований",
    PipelineStage.BUILD_EVIDENCE_INDEX.value: "Индексирование материалов",
    PipelineStage.VERIFIED_RESEARCH.value: "Проверка источников и исследования",
    PipelineStage.PLAN.value: "Построение плана",
    PipelineStage.BUILD_FACTS_AND_DATASETS.value: "Подготовка фактов и данных",
    PipelineStage.GENERATE_SECTIONS.value: "Генерация разделов",
    PipelineStage.GENERATE_VISUALS.value: "Создание таблиц и иллюстраций",
    PipelineStage.CITATION_AUDIT.value: "Проверка цитат",
    PipelineStage.CONSISTENCY_QA.value: "Проверка связности",
    PipelineStage.RENDER_DOCX.value: "Сборка DOCX-документа",
    PipelineStage.WORD_FINALIZE.value: "Финальная обработка LibreOffice",
    PipelineStage.EXPORT_PDF.value: "Экспорт PDF",
    PipelineStage.PDF_VISUAL_QA.value: "Визуальная проверка PDF",
    PipelineStage.FINAL_GEMINI_REVIEW.value: "Финальная AI-проверка",
    PipelineStage.PACKAGE.value: "Подготовка результатов",
}

_FINISHED_STAGE_STATUSES = {StageStatus.SUCCEEDED, StageStatus.SKIPPED}
_ACTIVE_STAGE_STATUSES = {StageStatus.RUNNING, StageStatus.RETRYING}
_QUOTA_MARKERS = ("429", "quota", "rate limit", "retry after", "лимит", "квот")


@dataclass(frozen=True, slots=True)
class RunProgressPresentation:
    """Safe, human-readable values for the desktop progress card."""

    operation: str
    progress: str
    elapsed: str
    eta: str
    quota_message: str | None = None
    overall_percent: int = 0
    status_tone: str = "info"


def _format_duration(value: timedelta | float | int | None) -> str:
    """Render a non-negative duration without exposing raw diagnostic values."""

    if value is None:
        return "—"
    seconds = value.total_seconds() if isinstance(value, timedelta) else float(value)
    seconds = max(0, round(seconds))
    if seconds < 60:
        return f"{seconds} с"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} мин {seconds} с" if seconds else f"{minutes} мин"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes} мин" if minutes else f"{hours} ч"


def _stage_label(name: str | None) -> str:
    if not name:
        return "Подготовка запуска"
    return STAGE_LABELS.get(name, name.replace("_", " ").capitalize())


def _safe_checkpoint_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    compact = " ".join(value.split())
    return compact[:180] if compact else None


def _checkpoint_mapping(stage: object) -> dict[str, object]:
    checkpoint = getattr(stage, "checkpoint", {})
    return checkpoint if isinstance(checkpoint, dict) else {}


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _stage_progress(stage: object) -> tuple[int, int] | None:
    """Read durable stage progress with support for older checkpoints."""

    checkpoint = _checkpoint_mapping(stage)
    current = _non_negative_int(getattr(stage, "progress_current", 0))
    total = _non_negative_int(getattr(stage, "progress_total", 0))
    if not total:
        total = _non_negative_int(checkpoint.get("total_items"))
    if not total:
        total = _non_negative_int(checkpoint.get("total"))
    if not current:
        current = _non_negative_int(checkpoint.get("completed"))
    if not current:
        completed_items = checkpoint.get("completed_items")
        if isinstance(completed_items, (dict, list)):
            current = len(completed_items)
    if total is None or total <= 0:
        return None
    return min(current or 0, total), total


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _checkpoint_duration(stage: object) -> timedelta | None:
    checkpoint = _checkpoint_mapping(stage)
    milliseconds = _non_negative_int(checkpoint.get("duration_ms"))
    if milliseconds is not None:
        return timedelta(milliseconds=milliseconds)
    return None


def _stage_duration(stage: object, now: datetime) -> timedelta | None:
    started_at = _as_utc(getattr(stage, "started_at", None))
    if started_at is None:
        return _checkpoint_duration(stage)
    finished_at = _as_utc(getattr(stage, "finished_at", None))
    status = getattr(stage, "status", None)
    if finished_at is None and status in _ACTIVE_STAGE_STATUSES:
        finished_at = now
    if finished_at is None:
        return _checkpoint_duration(stage)
    return max(timedelta(), finished_at - started_at)


def _current_stage(run: GenerationRun, stages: Sequence[object]) -> object | None:
    if run.current_stage:
        matching = next(
            (stage for stage in stages if getattr(stage, "name", None) == run.current_stage), None
        )
        if matching is not None:
            return matching
    return next(
        (stage for stage in stages if getattr(stage, "status", None) in _ACTIVE_STAGE_STATUSES),
        None,
    )


def _checkpoint_remaining(stage: object) -> timedelta | None:
    checkpoint = _checkpoint_mapping(stage)
    milliseconds = _non_negative_int(checkpoint.get("estimated_remaining_ms"))
    if milliseconds is not None:
        return timedelta(milliseconds=milliseconds)
    seconds = _non_negative_int(checkpoint.get("estimated_remaining_seconds"))
    return timedelta(seconds=seconds) if seconds is not None else None


def _parse_retry_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _quota_wait_message(run: GenerationRun, stages: Sequence[object], now: datetime) -> str | None:
    """Return a generic quota message; provider errors themselves stay out of the UI."""

    candidates = [_current_stage(run, stages), *stages]
    for stage in candidates:
        if stage is None:
            continue
        checkpoint = _checkpoint_mapping(stage)
        details = [
            _safe_checkpoint_text(checkpoint.get("progress_message")),
            _safe_checkpoint_text(getattr(stage, "error", None)),
        ]
        waiting = bool(checkpoint.get("waiting_for_quota")) or any(
            marker in (detail or "").lower() for detail in details for marker in _QUOTA_MARKERS
        )
        retry_wait_ms = _non_negative_int(checkpoint.get("retry_wait_ms"))
        retry_after_seconds = _non_negative_int(checkpoint.get("retry_after_seconds"))
        retry_at = _parse_retry_at(checkpoint.get("retry_at") or checkpoint.get("next_retry_at"))
        if retry_at is not None and retry_at <= now:
            return "Ожидание Gemini завершилось. Нажмите «Продолжить», чтобы повторить проверку."
        remaining = max(timedelta(), retry_at - now) if retry_at is not None else None
        if (
            waiting
            or retry_wait_ms is not None
            or retry_after_seconds is not None
            or remaining is not None
        ):
            delay = remaining
            if delay is None and retry_wait_ms is not None:
                delay = timedelta(milliseconds=retry_wait_ms)
            if delay is None and retry_after_seconds is not None:
                delay = timedelta(seconds=retry_after_seconds)
            if delay is not None:
                return f"Gemini временно ограничил запросы. Повтор примерно через {_format_duration(delay)}."
            return (
                "Gemini временно ограничил запросы. Нажмите «Продолжить», чтобы повторить проверку."
            )
    return None


def _progress_presentation(
    run: GenerationRun, stages: Sequence[object], *, now: datetime | None = None
) -> RunProgressPresentation:
    """Build robust progress text from persisted run and stage state."""

    current_time = _as_utc(now) or datetime.now(UTC)
    ordered = sorted(stages, key=lambda stage: int(getattr(stage, "order", 0)))
    active = _current_stage(run, ordered)
    operation = _stage_label(getattr(active, "name", None) or run.current_stage)
    quota_message = _quota_wait_message(run, ordered, current_time)
    message = (
        _safe_checkpoint_text(_checkpoint_mapping(active).get("progress_message"))
        if active
        else None
    )
    if message and quota_message is None:
        operation = f"{operation} — {message}"

    stage_progress = _stage_progress(active) if active else None
    finished_count = sum(
        getattr(stage, "status", None) in _FINISHED_STAGE_STATUSES for stage in ordered
    )
    if stage_progress:
        progress = f"{stage_progress[0]} из {stage_progress[1]}"
        if ordered:
            progress += f" · этапы: {finished_count} из {len(ordered)}"
    elif ordered:
        progress = f"этапы: {finished_count} из {len(ordered)}"
    else:
        progress = "—"

    run_started_at = _as_utc(run.started_at)
    run_finished_at = _as_utc(run.finished_at)
    if run_started_at is None:
        elapsed_value = _stage_duration(active, current_time) if active else None
    else:
        elapsed_value = max(timedelta(), (run_finished_at or current_time) - run_started_at)

    if run.status == RunStatus.SUCCEEDED:
        eta = "готово"
    elif run.status in {RunStatus.FAILED, RunStatus.CANCELLED}:
        eta = "—"
    elif quota_message:
        eta = "ожидание доступа к Gemini"
    elif run.status in {RunStatus.PAUSED, RunStatus.WAITING_INPUT}:
        eta = "после продолжения"
    else:
        remaining = _checkpoint_remaining(active) if active else None
        active_duration = _stage_duration(active, current_time) if active else None
        if (
            remaining is None
            and stage_progress
            and stage_progress[0] > 0
            and active_duration is not None
        ):
            remaining = (
                active_duration * (stage_progress[1] - stage_progress[0]) / stage_progress[0]
            )
        completed_durations = [
            duration
            for stage in ordered
            if getattr(stage, "status", None) in _FINISHED_STAGE_STATUSES
            if (duration := _stage_duration(stage, current_time)) is not None
        ]
        pending_count = sum(
            getattr(stage, "status", None) not in _FINISHED_STAGE_STATUSES for stage in ordered
        )
        if completed_durations and pending_count:
            average = sum(completed_durations, timedelta()) / len(completed_durations)
            remaining = (remaining or timedelta()) + average * max(
                0, pending_count - (1 if active is not None else 0)
            )
        if remaining is None and elapsed_value is not None and finished_count:
            remaining = elapsed_value * max(0, len(ordered) - finished_count) / finished_count
        eta = f"≈ {_format_duration(remaining)}" if remaining is not None else "рассчитываем…"

    stage_fraction = 0.0
    if stage_progress is not None:
        stage_fraction = stage_progress[0] / max(1, stage_progress[1])
    overall_percent = (
        min(100, round((finished_count + stage_fraction) * 100 / len(ordered))) if ordered else 0
    )
    status_tone = {
        RunStatus.SUCCEEDED: "success",
        RunStatus.FAILED: "error",
        RunStatus.CANCELLED: "error",
        RunStatus.PAUSED: "warning",
        RunStatus.WAITING_INPUT: "warning",
        RunStatus.RETRYING: "warning",
    }.get(run.status, "info")
    return RunProgressPresentation(
        operation=operation,
        progress=progress,
        elapsed=_format_duration(elapsed_value),
        eta=eta,
        quota_message=quota_message,
        overall_percent=overall_percent,
        status_tone=status_tone,
    )


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings | None = None) -> None:
        super().__init__()
        self.settings = settings or AppSettings.from_environment()
        self.projects = ProjectService(self.settings)
        self.workspace: ProjectWorkspace | None = None
        self.active_run_id: str | None = None
        self.process: QProcess | None = None
        self.worker_project_id: str | None = None
        self.process_buffer = ""
        self.process_error_buffer = ""
        self.event_cursor = 0
        self.setWindowTitle("PaperCraft AI Studio — автопилот академических работ")
        self.resize(1320, 820)
        self._build_ui()
        self._apply_style()
        self._refresh_projects()
        self.notice_timer = QTimer(self)
        self.notice_timer.setSingleShot(True)
        self.notice_timer.timeout.connect(self.notice_banner.hide)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(900)
        self.poll_timer.timeout.connect(self._poll_run)
        self.poll_timer.start()

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_sidebar())

        workspace = QWidget(root)
        workspace.setObjectName("workspace")
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)

        self.notice_banner = QLabel(workspace)
        self.notice_banner.setObjectName("noticeBanner")
        self.notice_banner.setWordWrap(True)
        self.notice_banner.setVisible(False)
        self.notice_banner.setContentsMargins(24, 10, 24, 10)
        workspace_layout.addWidget(self.notice_banner)

        self.pages = QStackedWidget(workspace)
        for page in (
            self._build_project_step(),
            self._build_plan_step(),
            self._build_generation_step(),
            self._build_result_step(),
        ):
            self.pages.addWidget(page)
        workspace_layout.addWidget(self.pages, 1)
        layout.addWidget(workspace, 1)
        self.setCentralWidget(root)
        self._update_navigation_access()
        self.navigation.setCurrentRow(int(WorkspaceStep.PROJECT))

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(264)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(18, 20, 18, 16)
        layout.setSpacing(14)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        brand_icon = QLabel(sidebar)
        brand_icon.setPixmap(icon("logo", ACCENT_SECONDARY).pixmap(28, 28))
        brand_icon.setAccessibleName("")
        brand.addWidget(brand_icon)
        brand_text = QVBoxLayout()
        title = QLabel("PaperCraft", sidebar)
        title.setObjectName("brandTitle")
        caption = QLabel("AI Studio", sidebar)
        caption.setObjectName("brandCaption")
        brand_text.addWidget(title)
        brand_text.addWidget(caption)
        brand.addLayout(brand_text, 1)
        layout.addLayout(brand)

        self.navigation = QListWidget()
        self.navigation.setObjectName("stepNavigation")
        self.navigation.setSpacing(5)
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for index, (name, detail) in enumerate(_STEP_DETAILS, start=1):
            item = QListWidgetItem(f"{index}. {name}\n{detail}")
            item.setData(Qt.ItemDataRole.UserRole, index - 1)
            item.setIcon(icon(("project", "plan", "generate", "result")[index - 1], TEXT_MUTED))
            item.setToolTip(f"Шаг {index}: {name}")
            self.navigation.addItem(item)
        self.navigation.currentRowChanged.connect(self._navigate)
        layout.addWidget(self.navigation, 1)

        self.sidebar_project = QLabel("Проект не выбран", sidebar)
        self.sidebar_project.setObjectName("sidebarProject")
        self.sidebar_project.setWordWrap(True)
        self.sidebar_project.setToolTip("Текущий проект")
        layout.addWidget(self.sidebar_project)
        self.settings_button = QPushButton("Настроить Gemini", sidebar)
        self.settings_button.setObjectName("quiet")
        self.settings_button.setIcon(icon("settings", TEXT_MUTED))
        self.settings_button.clicked.connect(self._set_api_key)
        layout.addWidget(self.settings_button)
        return sidebar

    def _workspace_page(
        self, title: str, subtitle: str, icon_name: str
    ) -> tuple[QScrollArea, QVBoxLayout]:
        """Create one scrollable master step with a consistent heading."""

        scroll = QScrollArea()
        scroll.setObjectName("workspaceScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget(scroll)
        content.setObjectName("workspacePage")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(34, 30, 34, 34)
        layout.setSpacing(18)
        layout.addWidget(SectionHeader(title, subtitle, icon=icon(icon_name, ACCENT_SECONDARY)))
        scroll.setWidget(content)
        return scroll, layout

    @staticmethod
    def _button(
        text: str,
        *,
        variant: str = "default",
        icon_name: str | None = None,
        tooltip: str | None = None,
    ) -> QPushButton:
        button = QPushButton(text)
        if variant != "default":
            button.setObjectName(variant)
        if icon_name:
            button.setIcon(icon(icon_name))
        button.setToolTip(tooltip or text)
        button.setAccessibleName(text)
        return button

    @staticmethod
    def _menu_button(text: str, menu: QMenu, *, tooltip: str | None = None) -> QToolButton:
        button = QToolButton()
        button.setObjectName("quiet")
        button.setText(text)
        button.setIcon(icon("more", TEXT_MUTED))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setMenu(menu)
        button.setToolTip(tooltip or text)
        button.setAccessibleName(text)
        return button

    def _build_project_step(self) -> QWidget:
        page, layout = self._workspace_page(
            "Проект",
            "Соберите тему, исходные материалы и данные титульного листа в одном месте.",
            "project",
        )
        library = Card(accessible_name="Библиотека проектов")
        library.content_layout.addWidget(
            SectionHeader(
                "Недавние проекты",
                "Откройте предыдущую работу или начните новую.",
                icon=icon("project", ACCENT_SECONDARY),
            )
        )
        library_content = QHBoxLayout()
        library_content.setSpacing(16)
        list_column = QVBoxLayout()
        self.project_list = QListWidget()
        self.project_list.setObjectName("projectList")
        self.project_list.setMinimumHeight(230)
        self.project_list.itemDoubleClicked.connect(lambda _item: self._open_selected_project())
        self.project_list_stack = QStackedWidget()
        self.project_list_stack.addWidget(self.project_list)
        self.projects_empty = EmptyState(
            "Пока нет проектов",
            "Создайте первую работу справа — она сразу появится в библиотеке.",
            "Создать проект",
            icon=icon("add", ACCENT_SECONDARY),
        )
        self.projects_empty.action_requested.connect(self._focus_new_project)
        self.project_list_stack.addWidget(self.projects_empty)
        list_column.addWidget(self.project_list_stack, 1)
        open_project = self._button("Открыть выбранный", variant="secondary", icon_name="project")
        open_project.clicked.connect(self._open_selected_project)
        list_column.addWidget(open_project, 0, Qt.AlignmentFlag.AlignLeft)
        library_content.addLayout(list_column, 1)

        new_project = Card(variant="nested", accessible_name="Новый проект")
        new_project.content_layout.addWidget(
            SectionHeader(
                "Новая работа",
                "Достаточно темы — остальные поля можно заполнить позже.",
                icon=icon("add"),
            )
        )
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        self.new_topic = QTextEdit()
        self.new_topic.setAccessibleName("Тема или задание новой работы")
        self.new_topic.setPlaceholderText("Например: разработка системы учёта заявок на Python")
        self.new_topic.setMinimumHeight(94)
        self.new_type = self._enum_combo(WorkType)
        self.new_domain = self._enum_combo(DomainProfile)
        form.addRow("Тема или задание", self.new_topic)
        form.addRow("Тип работы", self.new_type)
        form.addRow("Профиль", self.new_domain)
        new_project.content_layout.addLayout(form)
        create = self._button("Создать проект", variant="primary", icon_name="add")
        create.clicked.connect(self._create_project)
        new_project.content_layout.addWidget(create)
        library_content.addWidget(new_project, 1)
        library.content_layout.addLayout(library_content)
        layout.addWidget(library)

        self.project_editor_card = Card(accessible_name="Настройки текущего проекта")
        self.project_editor_card.content_layout.addWidget(
            SectionHeader(
                "Задание и материалы",
                "Сохраните изменения перед запуском автопилота.",
                icon=icon("document", ACCENT_SECONDARY),
            )
        )
        main_form = QFormLayout()
        main_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        main_form.setHorizontalSpacing(14)
        main_form.setVerticalSpacing(10)
        self.title_edit = QTextEdit()
        self.title_edit.setMaximumHeight(56)
        self.title_edit.setAccessibleName("Название работы")
        self.topic_edit = QTextEdit()
        self.topic_edit.setMaximumHeight(74)
        self.topic_edit.setAccessibleName("Тема работы")
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setMinimumHeight(118)
        self.prompt_edit.setAccessibleName("Требуемый результат")
        self.work_type_combo = self._enum_combo(WorkType)
        self.domain_combo = self._enum_combo(DomainProfile)
        self.consent_checkbox = QCheckBox(
            "Разрешаю отправку копий документов в Gemini на время запуска"
        )
        self.consent_checkbox.setToolTip(
            "Без согласия генерация с прикреплёнными документами не начнётся."
        )
        for label, widget in (
            ("Название", self.title_edit),
            ("Тема", self.topic_edit),
            ("Что нужно получить", self.prompt_edit),
            ("Тип работы", self.work_type_combo),
            ("Профиль", self.domain_combo),
            ("Обработка материалов", self.consent_checkbox),
        ):
            main_form.addRow(label, widget)
        self.project_editor_card.content_layout.addLayout(main_form)

        title_page = Card(variant="nested", accessible_name="Данные титульного листа")
        title_page.content_layout.addWidget(
            SectionHeader(
                "Титульный лист",
                "Можно заполнить сейчас или вернуться к этим полям позже.",
                icon=icon("document"),
            )
        )
        title_grid = QGridLayout()
        title_grid.setHorizontalSpacing(16)
        title_grid.setVerticalSpacing(9)
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
            editor.setAccessibleName(label)
            if key == "year":
                editor.setText(str(date.today().year))
            self.title_page_fields[key] = editor
            title_grid.addWidget(QLabel(label), row, column)
            title_grid.addWidget(editor, row, column + 1)
        title_grid.setColumnStretch(1, 1)
        title_grid.setColumnStretch(3, 1)
        title_page.content_layout.addLayout(title_grid)
        self.project_editor_card.content_layout.addWidget(title_page)

        sources = Card(variant="nested", accessible_name="Исходные материалы")
        source_header = SectionHeader(
            "Исходные материалы",
            "Файлы сохраняются в проекте и используются только в нужных этапах генерации.",
            icon=icon("upload"),
        )
        source_actions = QHBoxLayout()
        self.source_role = self._enum_combo(SourceRole)
        self._set_combo(self.source_role, SourceRole.METHODOLOGY)
        add_files = self._button("Добавить файлы", variant="secondary", icon_name="upload")
        add_folder = self._button("Добавить папку", variant="quiet", icon_name="folder")
        add_files.clicked.connect(self._add_files)
        add_folder.clicked.connect(self._add_folder)
        source_actions.addWidget(QLabel("Тип материала"))
        source_actions.addWidget(self.source_role)
        source_actions.addStretch(1)
        source_actions.addWidget(add_files)
        source_actions.addWidget(add_folder)
        source_header.actions_layout.addLayout(source_actions)
        sources.content_layout.addWidget(source_header)
        self.sources_table = self._table(["Файл", "Тип", "Размер"], stretch_column=0)
        self.sources_table.setMinimumHeight(170)
        sources.content_layout.addWidget(self.sources_table)
        sources_hint = QLabel(
            "Контрольная сумма доступна в подсказке строки и не мешает основной работе."
        )
        sources_hint.setObjectName("helperText")
        sources.content_layout.addWidget(sources_hint)
        self.project_editor_card.content_layout.addWidget(sources)

        editor_actions = QHBoxLayout()
        save = self._button("Сохранить проект", variant="primary", icon_name="save")
        save.clicked.connect(self._save_project)
        project_more_menu = QMenu(self)
        rebuild_inputs = QAction(
            icon("refresh"), "Сохранить и пересобрать с материалов", project_more_menu
        )
        rebuild_inputs.triggered.connect(self._save_and_rebuild_from_inputs)
        project_more_menu.addAction(rebuild_inputs)
        project_more = self._menu_button(
            "Ещё", project_more_menu, tooltip="Дополнительные действия проекта"
        )
        editor_actions.addStretch(1)
        editor_actions.addWidget(project_more)
        editor_actions.addWidget(save)
        self.project_editor_card.content_layout.addLayout(editor_actions)
        self.project_editor_card.setVisible(False)
        layout.addWidget(self.project_editor_card)
        layout.addStretch(1)
        return page

    def _build_plan_step(self) -> QWidget:
        page, layout = self._workspace_page(
            "План",
            "Проверьте требования и структуру перед тем, как передать работу автопилоту.",
            "plan",
        )
        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.metric_sources = MetricCard("Материалы", icon=icon("upload", ACCENT_SECONDARY))
        self.metric_requirements = MetricCard("Требования", icon=icon("check", ACCENT_SECONDARY))
        self.metric_sections = MetricCard("Разделы", icon=icon("plan", ACCENT_SECONDARY))
        self.metric_words = MetricCard("Объём", icon=icon("document", ACCENT_SECONDARY))
        for metric in (
            self.metric_sources,
            self.metric_requirements,
            self.metric_sections,
            self.metric_words,
        ):
            metrics.addWidget(metric, 1)
        layout.addLayout(metrics)

        settings = CollapsibleSection(
            "Настройки генерации",
            "Паузы и лимит стоимости нужны только для более точного контроля.",
            icon=icon("settings"),
            expanded=False,
        )
        settings_form = QHBoxLayout()
        self.check_requirements = QCheckBox("Остановиться после требований")
        self.check_outline = QCheckBox("Остановиться после плана")
        self.check_final = QCheckBox("Остановиться перед выпуском")
        self.allow_synthetic_data = QCheckBox("Разрешить только демонстрационные синтетические данные")
        self.allow_synthetic_data.setToolTip(
            "Используются только после отсутствия пользовательских и открытых данных; "
            "работа будет помечена как непубликационный демонстрационный черновик."
        )
        self.cost_enabled = QCheckBox("Ограничить стоимость")
        self.cost_limit = QDoubleSpinBox()
        self.cost_limit.setRange(0.01, 10000)
        self.cost_limit.setSuffix(" USD")
        self.cost_limit.setValue(20)
        for widget in (
            self.check_requirements,
            self.check_outline,
            self.check_final,
            self.allow_synthetic_data,
            self.cost_enabled,
            self.cost_limit,
        ):
            settings_form.addWidget(widget)
        settings_form.addStretch(1)
        settings.content_layout.addLayout(settings_form)
        layout.addWidget(settings)

        requirements = CollapsibleSection(
            "Требования",
            "Правила из методички, шаблонов и задания.",
            icon=icon("check"),
            expanded=False,
        )
        self.requirements_filter = QLineEdit()
        self.requirements_filter.setPlaceholderText("Фильтр по требованию или категории")
        self.requirements_filter.setClearButtonEnabled(True)
        self.requirements_filter.textChanged.connect(lambda _text: self._refresh_requirements())
        requirements.content_layout.addWidget(self.requirements_filter)
        self.requirements_table = self._table(
            [
                "Категория",
                "Ключ",
                "Требование",
                "Приоритет",
                "Покрытие",
                "Уверенность",
            ],
            stretch_column=2,
        )
        self.requirements_table.setMinimumHeight(240)
        requirements.content_layout.addWidget(self.requirements_table)
        layout.addWidget(requirements)

        datasets_section = CollapsibleSection(
            "Датасеты и доказательная база",
            "Реестр эмпирических данных, первоисточников и статус синтетики.",
            icon=icon("refresh"),
            expanded=False,
        )
        self.datasets_table = self._table(
            [
                "Название датасета",
                "Происхождение",
                "Строк / Колонок",
                "Репозиторий / Лицензия",
                "Публикация",
            ],
            stretch_column=0,
        )
        self.datasets_table.setMinimumHeight(200)
        datasets_section.content_layout.addWidget(self.datasets_table)
        layout.addWidget(datasets_section)

        plan = Card(accessible_name="План работы")
        plan_header = SectionHeader(
            "Структура работы",
            "Дважды щёлкните по названию или объёму, чтобы отредактировать план.",
            icon=icon("plan", ACCENT_SECONDARY),
        )
        plan.content_layout.addWidget(plan_header)
        self.plan_tree = QTreeWidget()
        self.plan_tree.setHeaderLabels(["Раздел", "Объём", "Ключевые тезисы"])
        self.plan_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.plan_tree.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.plan_tree.setMinimumHeight(300)
        plan.content_layout.addWidget(self.plan_tree)
        actions = QHBoxLayout()
        refresh = self._button("Обновить план", variant="quiet", icon_name="refresh")
        refresh.clicked.connect(self._refresh_plan)
        save_plan = self._button("Сохранить правки", variant="secondary", icon_name="save")
        save_plan.clicked.connect(self._save_plan_edits)
        self.plan_retry_stage_combo = self._enum_combo(PipelineStage)
        self.plan_retry_stage_combo.setVisible(False)
        plan_more_menu = QMenu(self)
        rebuild_menu = plan_more_menu.addMenu("Пересобрать с этапа")
        for stage in PipelineStage:
            action = rebuild_menu.addAction(label_for(stage.value))
            action.triggered.connect(
                lambda _checked=False, selected_stage=stage: self._confirm_retry_from(
                    selected_stage
                )
            )
        restore_plan = plan_more_menu.addAction("Вернуть прошлую версию плана")
        restore_plan.triggered.connect(self._restore_previous_plan_revision)
        plan_more = self._menu_button(
            "Ещё", plan_more_menu, tooltip="Расширенные действия с планом"
        )
        self.start_button = self._button("Запустить генерацию", variant="primary", icon_name="play")
        self.start_button.clicked.connect(self._start_autopilot)
        actions.addWidget(refresh)
        actions.addWidget(save_plan)
        actions.addStretch(1)
        actions.addWidget(plan_more)
        actions.addWidget(self.start_button)
        plan.content_layout.addLayout(actions)
        layout.addWidget(plan)
        layout.addStretch(1)
        return page

    def _build_generation_step(self) -> QWidget:
        page, layout = self._workspace_page(
            "Генерация",
            "PaperCraft сохраняет прогресс поэтапно: можно закрыть приложение и вернуться позже.",
            "generate",
        )
        overview = Card(accessible_name="Состояние генерации")
        overview_header = QHBoxLayout()
        overview_title = SectionHeader(
            "Состояние работы",
            "Следим за процессом без технического шума.",
            icon=icon("generate", ACCENT_SECONDARY),
        )
        self.run_status = StatusBadge(
            "Запуск не создан", tone="neutral", icon=icon("warning", WARNING)
        )
        overview_header.addWidget(overview_title, 1)
        overview_header.addWidget(self.run_status, 0, Qt.AlignmentFlag.AlignTop)
        overview.content_layout.addLayout(overview_header)
        self.overall_progress = QProgressBar()
        self.overall_progress.setRange(0, 100)
        self.overall_progress.setValue(0)
        self.overall_progress.setTextVisible(False)
        overview.content_layout.addWidget(self.overall_progress)
        progress_text = QHBoxLayout()
        self.progress_percent_label = QLabel("0% завершено")
        self.progress_percent_label.setObjectName("helperText")
        self.run_progress = QLabel("—")
        self.run_progress.setObjectName("helperText")
        progress_text.addWidget(self.progress_percent_label)
        progress_text.addStretch(1)
        progress_text.addWidget(self.run_progress)
        overview.content_layout.addLayout(progress_text)

        operation = Card(variant="nested", accessible_name="Текущая операция")
        operation.content_layout.addWidget(QLabel("Сейчас выполняется"))
        self.run_operation = QLabel("—")
        self.run_operation.setObjectName("operationTitle")
        self.run_operation.setWordWrap(True)
        operation.content_layout.addWidget(self.run_operation)
        self.run_quota_wait = QLabel()
        self.run_quota_wait.setObjectName("quotaWait")
        self.run_quota_wait.setWordWrap(True)
        self.run_quota_wait.setVisible(False)
        operation.content_layout.addWidget(self.run_quota_wait)
        overview.content_layout.addWidget(operation)
        layout.addWidget(overview)

        run_metrics = QHBoxLayout()
        self.run_stage_card = MetricCard("Текущий этап", icon=icon("plan", ACCENT_SECONDARY))
        self.run_stage = self.run_stage_card.value_label
        self.run_elapsed_card = MetricCard("Прошло", icon=icon("refresh", ACCENT_SECONDARY))
        self.run_elapsed = self.run_elapsed_card.value_label
        self.run_eta_card = MetricCard("Осталось", icon=icon("generate", ACCENT_SECONDARY))
        self.run_eta = self.run_eta_card.value_label
        self.run_cost_card = MetricCard("Стоимость", icon=icon("document", ACCENT_SECONDARY))
        self.run_cost = self.run_cost_card.value_label
        for metric in (
            self.run_stage_card,
            self.run_elapsed_card,
            self.run_eta_card,
            self.run_cost_card,
        ):
            run_metrics.addWidget(metric, 1)
        layout.addLayout(run_metrics)

        phases = Card(accessible_name="Фазы генерации")
        phases.content_layout.addWidget(
            SectionHeader(
                "Путь работы", "Четыре понятные фазы вместо длинного списка технических шагов."
            )
        )
        phase_row = QHBoxLayout()
        self.phase_badges: dict[str, StatusBadge] = {}
        for name, _stage_names in _PHASES:
            column = QVBoxLayout()
            label = QLabel(name)
            label.setObjectName("phaseTitle")
            badge = StatusBadge("Ожидает", tone="neutral")
            self.phase_badges[name] = badge
            column.addWidget(label)
            column.addWidget(badge)
            phase_row.addLayout(column, 1)
        phases.content_layout.addLayout(phase_row)
        layout.addWidget(phases)

        self.stages_section = CollapsibleSection(
            "Подробные этапы",
            "17 внутренних операций и их прогресс.",
            icon=icon("plan"),
            expanded=False,
        )
        self.stages_table = self._table(
            ["№", "Этап", "Статус", "Прогресс", "Время", "Попытки"], stretch_column=1
        )
        self.stages_table.setMinimumHeight(340)
        self.stages_section.content_layout.addWidget(self.stages_table)
        layout.addWidget(self.stages_section)

        self.events_section = CollapsibleSection(
            "Технический журнал",
            "Показывает сообщения worker и детали этапов.",
            icon=icon("document"),
            expanded=False,
        )
        self.events_view = QTextBrowser()
        self.events_view.setMinimumHeight(220)
        self.events_section.content_layout.addWidget(self.events_view)
        layout.addWidget(self.events_section)

        controls = Card(variant="actions", accessible_name="Управление генерацией")
        controls_layout = QHBoxLayout()
        self.pause_button = self._button("Пауза", variant="secondary", icon_name="pause")
        self.resume_button = self._button("Продолжить", variant="primary", icon_name="play")
        self.cancel_button = self._button("Отменить", variant="danger", icon_name="cancel")
        self.pause_button.clicked.connect(self._pause_run)
        self.resume_button.clicked.connect(self._resume_run)
        self.cancel_button.clicked.connect(self._confirm_cancel_run)
        advanced_menu = QMenu(self)
        advanced_panel = QWidget(advanced_menu)
        advanced_layout = QVBoxLayout(advanced_panel)
        advanced_layout.setContentsMargins(8, 8, 8, 8)
        advanced_layout.setSpacing(8)
        self.refresh_research_button = self._button(
            "Перепроверить источники", variant="quiet", icon_name="refresh"
        )
        self.retry_stage_combo = self._enum_combo(PipelineStage)
        self.retry_stage_button = self._button(
            "Пересобрать с этапа", variant="quiet", icon_name="refresh"
        )
        self.refresh_research_button.clicked.connect(self._refresh_research)
        self.retry_stage_button.clicked.connect(self._confirm_retry_from_selected_stage)
        advanced_layout.addWidget(self.refresh_research_button)
        advanced_layout.addWidget(self.retry_stage_combo)
        advanced_layout.addWidget(self.retry_stage_button)
        advanced_action = QWidgetAction(advanced_menu)
        advanced_action.setDefaultWidget(advanced_panel)
        advanced_menu.addAction(advanced_action)
        progress_more = self._menu_button(
            "Ещё", advanced_menu, tooltip="Дополнительные действия генерации"
        )
        for button in (
            self.pause_button,
            self.resume_button,
            self.cancel_button,
            self.refresh_research_button,
            self.retry_stage_button,
        ):
            button.setEnabled(False)
        controls_layout.addWidget(self.pause_button)
        controls_layout.addWidget(self.resume_button)
        controls_layout.addWidget(self.cancel_button)
        controls_layout.addStretch(1)
        controls_layout.addWidget(progress_more)
        controls.content_layout.addLayout(controls_layout)
        layout.addWidget(controls)
        # Keep the three main run controls above optional diagnostics so they
        # remain reachable without scrolling on compact laptop windows.
        layout.removeWidget(controls)
        layout.insertWidget(4, controls)
        layout.addStretch(1)
        return page

    def _build_result_step(self) -> QWidget:
        page, layout = self._workspace_page(
            "Результат",
            "Откройте готовый документ, просмотрите PDF-страницы и проверьте замечания.",
            "result",
        )
        artifacts = QHBoxLayout()
        self.docx_card = MetricCard(
            "DOCX-документ",
            "Пока не готов",
            "Появится после сборки документа.",
            icon=icon("document", ACCENT_SECONDARY),
        )
        self.pdf_card = MetricCard(
            "PDF-документ",
            "Пока не готов",
            "Появится после визуальной проверки.",
            icon=icon("result", ACCENT_SECONDARY),
        )
        artifacts.addWidget(self.docx_card, 1)
        artifacts.addWidget(self.pdf_card, 1)
        layout.addLayout(artifacts)

        document_actions = Card(accessible_name="Действия с документом")
        document_action_layout = QHBoxLayout()
        self.open_docx_button = self._button(
            "Открыть документ", variant="primary", icon_name="document"
        )
        self.export_docx_button = self._button(
            "Экспорт DOCX", variant="secondary", icon_name="export"
        )
        self.export_pdf_button = self._button(
            "Экспорт PDF", variant="secondary", icon_name="export"
        )
        self.open_docx_button.clicked.connect(self._open_word)
        self.export_docx_button.clicked.connect(lambda: self._export(ArtifactKind.DOCX))
        self.export_pdf_button.clicked.connect(lambda: self._export(ArtifactKind.PDF))
        for button in (self.open_docx_button, self.export_docx_button, self.export_pdf_button):
            button.setEnabled(False)
            document_action_layout.addWidget(button)
        document_action_layout.addStretch(1)
        document_actions.content_layout.addLayout(document_action_layout)
        layout.addWidget(document_actions)

        preview = Card(accessible_name="Предпросмотр страниц")
        preview_header = SectionHeader(
            "Предпросмотр страниц",
            "Показываются страницы, которые прошли визуальную проверку PDF.",
            icon=icon("result", ACCENT_SECONDARY),
        )
        preview.content_layout.addWidget(preview_header)
        self.preview_image = QLabel("Предпросмотр появится после проверки PDF")
        self.preview_image.setObjectName("previewImage")
        self.preview_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_image.setMinimumHeight(420)
        self.preview_image.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.preview_image.setWordWrap(True)
        preview.content_layout.addWidget(self.preview_image)
        preview_actions = QHBoxLayout()
        self.preview_previous_button = self._button("Назад", variant="quiet", icon_name="chevron")
        self.preview_next_button = self._button("Вперёд", variant="quiet", icon_name="chevron")
        self.preview_page_label = QLabel("Страницы пока нет")
        self.preview_page_label.setObjectName("helperText")
        self.preview_previous_button.clicked.connect(lambda: self._move_preview(-1))
        self.preview_next_button.clicked.connect(lambda: self._move_preview(1))
        preview_actions.addWidget(self.preview_previous_button)
        preview_actions.addWidget(self.preview_next_button)
        preview_actions.addWidget(self.preview_page_label)
        preview_actions.addStretch(1)
        preview.content_layout.addLayout(preview_actions)
        self.preview_pages: list[Path] = []
        self.preview_index = 0
        self.preview_previous_button.setEnabled(False)
        self.preview_next_button.setEnabled(False)
        layout.addWidget(preview)

        quality = QHBoxLayout()
        qa_card = Card(accessible_name="Проверка качества")
        qa_header = QHBoxLayout()
        qa_header.addWidget(
            SectionHeader(
                "Проверка качества",
                "Замечания можно исправить точечно через меню «Ещё».",
                icon=icon("check", ACCENT_SECONDARY),
            ),
            1,
        )
        self.qa_badge = StatusBadge("Пока нет проверки", tone="neutral")
        qa_header.addWidget(self.qa_badge, 0, Qt.AlignmentFlag.AlignTop)
        qa_card.content_layout.addLayout(qa_header)
        self.qa_view = QTextBrowser()
        self.qa_view.setMinimumHeight(200)
        qa_card.content_layout.addWidget(self.qa_view)
        quality.addWidget(qa_card, 2)

        artifacts_card = Card(accessible_name="Другие артефакты")
        artifacts_card.content_layout.addWidget(
            SectionHeader("Файлы проекта", "Дополнительные материалы и отчёты.")
        )
        self.artifact_list = QListWidget()
        self.artifact_list.setMinimumHeight(200)
        self.artifact_list.itemDoubleClicked.connect(self._open_artifact)
        artifacts_card.content_layout.addWidget(self.artifact_list)
        quality.addWidget(artifacts_card, 1)
        layout.addLayout(quality)

        advanced_menu = QMenu(self)
        advanced_panel = QWidget(advanced_menu)
        advanced_layout = QVBoxLayout(advanced_panel)
        advanced_layout.setContentsMargins(8, 8, 8, 8)
        advanced_layout.setSpacing(8)
        self.result_section_combo = QComboBox()
        edit_section = self._button("Править раздел", variant="secondary", icon_name="document")
        restore_section = self._button("Вернуть прошлую версию", variant="quiet", icon_name="refresh")
        rebuild_section = self._button(
            "Перегенерировать раздел", variant="quiet", icon_name="refresh"
        )
        rebuild_from = self._button("Пересобрать документ", variant="quiet", icon_name="refresh")
        open_folder = self._button("Открыть папку проекта", variant="quiet", icon_name="folder")
        refresh_results = self._button("Обновить данные", variant="quiet", icon_name="refresh")
        edit_section.clicked.connect(self._edit_selected_section)
        restore_section.clicked.connect(self._restore_selected_section_revision)
        rebuild_section.clicked.connect(self._confirm_rebuild_section)
        rebuild_from.clicked.connect(self._confirm_rebuild_document)
        open_folder.clicked.connect(self._open_project_folder)
        refresh_results.clicked.connect(self._refresh_results)
        advanced_layout.addWidget(QLabel("Раздел для перегенерации"))
        advanced_layout.addWidget(self.result_section_combo)
        advanced_layout.addWidget(edit_section)
        advanced_layout.addWidget(restore_section)
        advanced_layout.addWidget(rebuild_section)
        advanced_layout.addWidget(rebuild_from)
        advanced_layout.addWidget(open_folder)
        advanced_layout.addWidget(refresh_results)
        advanced_action = QWidgetAction(advanced_menu)
        advanced_action.setDefaultWidget(advanced_panel)
        advanced_menu.addAction(advanced_action)
        result_controls = Card(variant="actions", accessible_name="Расширенные действия результата")
        result_controls_layout = QHBoxLayout()
        result_controls_layout.addStretch(1)
        result_controls_layout.addWidget(
            self._menu_button("Ещё", advanced_menu, tooltip="Дополнительные действия с результатом")
        )
        result_controls.content_layout.addLayout(result_controls_layout)
        layout.addWidget(result_controls)
        layout.addStretch(1)
        return page

    def _focus_new_project(self) -> None:
        self.new_topic.setFocus(Qt.FocusReason.OtherFocusReason)

    @staticmethod
    def _enum_combo(enum_type: type[StrEnum]) -> QComboBox:
        combo = QComboBox()
        for item in enum_type:
            combo.addItem(label_for(item), item.value)
        return combo

    @staticmethod
    def _table(headers: list[str], *, stretch_column: int) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(
            stretch_column, QHeaderView.ResizeMode.Stretch
        )
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        return table

    def _update_navigation_access(self) -> None:
        """Keep the master linear until a project is available to work on."""

        has_workspace = self.workspace is not None
        for index in range(self.navigation.count()):
            item = self.navigation.item(index)
            if item is None:
                continue
            flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
            if index > int(WorkspaceStep.PROJECT) and not has_workspace:
                flags &= ~Qt.ItemFlag.ItemIsEnabled
            item.setFlags(flags)
            if index > int(WorkspaceStep.PROJECT) and not has_workspace:
                item.setToolTip("Сначала создайте или откройте проект")
            else:
                item.setToolTip(f"Шаг {index + 1}: {PAGE_NAMES[index]}")
        if hasattr(self, "project_editor_card"):
            self.project_editor_card.setVisible(has_workspace)
        if hasattr(self, "sidebar_project"):
            title = ""
            if self.workspace is not None:
                title = self.workspace.project.brief.title or self.workspace.project.brief.topic
            self.sidebar_project.setText(title or "Проект не выбран")
        self._refresh_step_indicators()

    def _refresh_step_indicators(self) -> None:
        """Show the meaningful state of each master step directly in the sidebar."""

        base_icons = ("project", "plan", "generate", "result")
        for index, icon_name in enumerate(base_icons):
            item = self.navigation.item(index)
            if item is not None:
                item.setIcon(icon(icon_name, TEXT_MUTED))
        if self.workspace is None:
            return

        project = self.workspace.project
        project_item = self.navigation.item(int(WorkspaceStep.PROJECT))
        if project_item is not None:
            project_item.setIcon(icon("check", SUCCESS))
            project_item.setToolTip("Шаг 1: Проект — готов к настройке и запуску")

        blueprint = self.workspace.repository.get_latest_blueprint(project.id)
        plan_item = self.navigation.item(int(WorkspaceStep.PLAN))
        if plan_item is not None and blueprint is not None:
            plan_item.setIcon(icon("check", SUCCESS))
            plan_item.setToolTip("Шаг 2: План — структура создана")

        current_run = self._active_run()
        if current_run is None:
            runs = self.workspace.repository.list_runs(project.id)
            current_run = runs[0] if runs else None
        generation_item = self.navigation.item(int(WorkspaceStep.GENERATE))
        if generation_item is not None and current_run is not None:
            if current_run.status == RunStatus.SUCCEEDED:
                generation_item.setIcon(icon("check", SUCCESS))
                generation_item.setToolTip("Шаг 3: Генерация — завершена")
            elif current_run.status in {RunStatus.FAILED, RunStatus.CANCELLED}:
                generation_item.setIcon(icon("error", ERROR))
                generation_item.setToolTip("Шаг 3: Генерация — требует внимания")
            elif current_run.status in {RunStatus.RUNNING, RunStatus.RETRYING, RunStatus.QUEUED}:
                generation_item.setIcon(icon("generate", ACCENT_SECONDARY))
                generation_item.setToolTip("Шаг 3: Генерация — выполняется")

        result_item = self.navigation.item(int(WorkspaceStep.RESULT))
        if result_item is not None and current_run is not None:
            artifact_kinds = {
                artifact.kind
                for artifact in self.workspace.repository.list_artifacts(
                    project.id, run_id=current_run.id
                )
            }
            if ArtifactKind.DOCX in artifact_kinds or ArtifactKind.PDF in artifact_kinds:
                result_item.setIcon(icon("check", SUCCESS))
                result_item.setToolTip("Шаг 4: Результат — документы доступны")

    def _navigate(self, index: int) -> None:
        if index > int(WorkspaceStep.PROJECT) and self.workspace is None:
            self.navigation.blockSignals(True)
            self.navigation.setCurrentRow(int(WorkspaceStep.PROJECT))
            self.navigation.blockSignals(False)
            self._show_banner("Сначала создайте или откройте проект.", "warning")
            return
        self.pages.setCurrentIndex(max(0, index))
        if index == int(WorkspaceStep.PLAN):
            self._refresh_requirements()
            self._refresh_plan()
        elif index == int(WorkspaceStep.GENERATE):
            self._poll_run()
        elif index == int(WorkspaceStep.RESULT):
            self._refresh_results()

    def _show_banner(self, message: str, tone: str = "info", *, timeout_ms: int = 6000) -> None:
        """Show a short, non-blocking status message inside the workspace."""

        self.notice_banner.setText(message)
        self.notice_banner.setProperty("tone", tone)
        style = self.notice_banner.style()
        style.unpolish(self.notice_banner)
        style.polish(self.notice_banner)
        self.notice_banner.setVisible(True)
        self.statusBar().showMessage(message, timeout_ms)
        if timeout_ms > 0:
            self.notice_timer.start(timeout_ms)

    def _refresh_projects(self) -> None:
        self.project_list.clear()
        for project in self.projects.list():
            title = project.brief.title or project.brief.topic or "Без названия"
            item = QListWidgetItem(
                f"{title}\n{label_for(project.brief.work_type)} · {project.updated_at:%d.%m.%Y %H:%M}"
            )
            item.setData(Qt.ItemDataRole.UserRole, project.id)
            item.setToolTip(f"Открыть проект «{title}»")
            self.project_list.addItem(item)
        self.project_list_stack.setCurrentIndex(0 if self.project_list.count() else 1)

    def _create_project(self) -> None:
        if self._worker_is_running():
            self._show_banner(
                "Нельзя создать другой проект, пока идёт генерация. Дождитесь завершения или отмените запуск.",
                "warning",
            )
            return
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
        self._update_navigation_access()
        self.navigation.setCurrentRow(int(WorkspaceStep.PROJECT))
        self._show_banner("Проект создан. Добавьте материалы или переходите к плану.", "success")

    def _open_selected_project(self) -> None:
        if self._worker_is_running():
            self._show_banner(
                "Нельзя сменить проект, пока идёт генерация. Дождитесь завершения или отмените запуск.",
                "warning",
            )
            return
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
        self._update_navigation_access()
        self.navigation.setCurrentRow(int(WorkspaceStep.PROJECT))
        self._show_banner("Проект открыт.", "success")

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
        self.allow_synthetic_data.setChecked(options.allow_synthetic_data)
        self.cost_enabled.setChecked(options.maximum_cost is not None)
        if options.maximum_cost is not None:
            self.cost_limit.setValue(float(options.maximum_cost))
        self._refresh_sources()
        self._refresh_requirements()
        self._refresh_datasets()
        self._refresh_plan()
        self._refresh_results()
        self._update_navigation_access()

    @staticmethod
    def _set_combo(combo: QComboBox, value: Any) -> None:
        normalized = value.value if isinstance(value, StrEnum) else value
        index = combo.findData(normalized)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _save_project(self) -> bool:
        if self.workspace is None:
            return False
        topic, prompt = (
            self.topic_edit.toPlainText().strip(),
            self.prompt_edit.toPlainText().strip(),
        )
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
                "allow_synthetic_data": self.allow_synthetic_data.isChecked(),
                "maximum_cost": Decimal(str(self.cost_limit.value()))
                if self.cost_enabled.isChecked()
                else None,
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
        self.workspace = self.projects.update(
            self.workspace.project.id, brief=brief, options=options
        )
        self.statusBar().showMessage("Проект сохранён", 3000)
        self._refresh_projects()
        self._update_navigation_access()
        self._show_banner("Изменения проекта сохранены.", "success")
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
            result = SourceService(self.workspace).import_files(
                paths, self.source_role.currentData()
            )
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
            self.metric_sources.set_value("—")
            return
        sources = [
            source
            for source in self.workspace.repository.list_sources(self.workspace.project.id)
            if not source.metadata.get("generated")
        ]
        for source in sources:
            row = self.sources_table.rowCount()
            self._append_row(
                self.sources_table,
                [source.original_name, label_for(source.role), _human_size(source.size_bytes)],
            )
            filename_item = self.sources_table.item(row, 0)
            if filename_item is not None:
                filename_item.setToolTip(f"SHA-256: {source.sha256}")
        self.metric_sources.set_value(str(len(sources)))
        self.metric_sources.set_detail("добавлено в проект")

    def _refresh_datasets(self) -> None:
        self.datasets_table.setRowCount(0)
        if self.workspace is None:
            return
        datasets = self.workspace.repository.list_datasets(self.workspace.project.id)
        for ds in datasets:
            origin_label = {
                FactOrigin.USER: "Пользовательский",
                FactOrigin.VERIFIED_SOURCE: "Открытый (Zenodo/DataCite)",
                FactOrigin.SYNTHETIC: "Синтетический (Демо)",
                FactOrigin.CALCULATED: "Расчётный",
            }.get(ds.origin, str(ds.origin.value))

            repo_license = " — "
            if ds.repository or ds.license:
                repo_license = f"{ds.repository or ''} · {ds.license or ''}".strip(" ·")

            pub_status = "Публикуемый" if ds.publishability == "publishable" else "Демонстрационный черновик"

            self._append_row(
                self.datasets_table,
                [
                    ds.name,
                    origin_label,
                    f"{len(ds.rows)} / {len(ds.columns)}",
                    repo_license,
                    pub_status,
                ],
            )

    def _refresh_requirements(self) -> None:
        self.requirements_table.setRowCount(0)
        if self.workspace is None:
            self.metric_requirements.set_value("—")
            return
        requirements = self.workspace.repository.get_latest_requirement_set(
            self.workspace.project.id
        )
        coverage_by_rule: dict[str, Any] = {}
        if self.active_run_id:
            report = self.workspace.repository.get_latest_qa_report(self.active_run_id)
            if report is not None and report.requirement_coverage is not None:
                coverage_by_rule = {
                    entry.requirement_rule_id: entry
                    for entry in report.requirement_coverage.entries
                }
        if requirements:
            filter_text = self.requirements_filter.text().casefold().strip()
            for rule in requirements.rules:
                haystack = " ".join((rule.category.value, rule.key, rule.statement)).casefold()
                if filter_text and filter_text not in haystack:
                    continue
                coverage = coverage_by_rule.get(rule.id)
                coverage_text = {
                    "covered": "Покрыто",
                    "partial": "Частично",
                    "missing": "Не закрыто",
                }.get(str(getattr(coverage, "status", "")), "Не проверено")
                priority = label_for(getattr(coverage, "priority", None))
                if coverage is not None and getattr(coverage, "criticality", "") == "critical":
                    priority = f"Критично · {priority}"
                self._append_row(
                    self.requirements_table,
                    [
                        label_for(rule.category),
                        rule.key,
                        rule.statement,
                        priority,
                        coverage_text,
                        f"{rule.confidence:.0%}",
                    ],
                )
                coverage_item = self.requirements_table.item(
                    self.requirements_table.rowCount() - 1, 4
                )
                if coverage_item is not None and coverage is not None:
                    details = [
                        f"Статус: {coverage_text}",
                        f"Блоки: {', '.join(coverage.block_ids) or 'не определены'}",
                    ]
                    page_mappings = getattr(coverage, "pdf_page_mappings", [])
                    if page_mappings:
                        pages = "; ".join(
                            f"{item.block_id}: {', '.join(map(str, item.pages))}"
                            for item in page_mappings
                        )
                        details.append(f"Страницы PDF: {pages}")
                    if coverage.evidence_gaps:
                        details.append("Пробелы evidence: " + "; ".join(coverage.evidence_gaps))
                    if coverage.reason:
                        details.append(coverage.reason)
                    coverage_item.setToolTip("\n".join(details))
            self.metric_requirements.set_value(str(len(requirements.rules)))
            if coverage_by_rule:
                closed = sum(
                    1
                    for entry in coverage_by_rule.values()
                    if getattr(entry, "status", "") == "covered"
                )
                critical = sum(
                    1
                    for entry in coverage_by_rule.values()
                    if getattr(entry, "criticality", "") == "critical"
                )
                self.metric_requirements.set_detail(
                    f"{closed} из {len(coverage_by_rule)} покрыто; критичных: {critical}"
                )
            else:
                self.metric_requirements.set_detail("правил найдено; покрытие появится после QA")
        else:
            self.metric_requirements.set_value("0")
            self.metric_requirements.set_detail("появятся после анализа")

    def _refresh_plan(self) -> None:
        self.plan_tree.clear()
        if self.workspace is None:
            self.metric_sections.set_value("—")
            self.metric_words.set_value("—")
            return
        blueprint = self.workspace.repository.get_latest_blueprint(self.workspace.project.id)
        if blueprint:
            sections = sorted(blueprint.outline.sections, key=lambda item: item.order)
            for section in sections:
                details = "; ".join(section.theses[:3])
                item = QTreeWidgetItem([section.title, f"{section.target_words} слов", details])
                item.setData(0, Qt.ItemDataRole.UserRole, section.id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                self.plan_tree.addTopLevelItem(item)
            self.metric_sections.set_value(str(len(sections)))
            self.metric_sections.set_detail("в структуре")
            self.metric_words.set_value(
                f"{sum(section.target_words for section in sections):,}".replace(",", " ")
            )
            self.metric_words.set_detail("слов планируется")
        else:
            self.metric_sections.set_value("0")
            self.metric_sections.set_detail("появятся после планирования")
            self.metric_words.set_value("—")
            self.metric_words.set_detail("")
        self._refresh_step_indicators()

    def _save_plan_edits(self) -> None:
        if self.workspace is None:
            return
        run = self._active_run()
        if run is not None and run.status in {
            RunStatus.RUNNING,
            RunStatus.RETRYING,
            RunStatus.QUEUED,
        }:
            self._error("Сначала дождитесь завершения текущей генерации")
            return
        blueprint = self.workspace.repository.get_latest_blueprint(self.workspace.project.id)
        if blueprint is None:
            self._error("План ещё не создан")
            return
        edits: dict[str, tuple[str, int]] = {}
        for index in range(self.plan_tree.topLevelItemCount()):
            item = self.plan_tree.topLevelItem(index)
            if item is None:
                self._error("Раздел плана не найден")
                return
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
        if updated == blueprint:
            self.statusBar().showMessage("В плане нет новых правок", 3000)
            return
        try:
            revisions = SectionRevisionService(self.workspace.project.id, self.workspace.repository)
            result = revisions.revise_plan(updated)
            if run is not None:
                revisions.prepare_plan_rebuild(run.id, result)
        except Exception as exc:
            self._error(f"Не удалось сохранить правку плана: {exc}")
            return
        self._refresh_plan()
        self._refresh_result_sections()
        if run is None:
            self.statusBar().showMessage(
                f"Создана ревизия плана {result.record.revision}; запустите генерацию.",
                6000,
            )
            return
        self.statusBar().showMessage(
            f"Создана ревизия плана {result.record.revision}; запускается пересборка зависимых разделов.",
            6000,
        )
        self._launch_worker(
            WorkerRequest(
                project_id=self.workspace.project.id,
                projects_root=self.settings.projects_root,
                run_id=run.id,
                retry_from=result.invalidation.start_stage.value,
            )
        )
        self.navigation.setCurrentRow(int(WorkspaceStep.GENERATE))

    def _restore_previous_plan_revision(self) -> None:
        if self.workspace is None:
            return
        run = self._active_run()
        if run is not None and run.status in {
            RunStatus.RUNNING,
            RunStatus.RETRYING,
            RunStatus.QUEUED,
        }:
            self._error("Сначала дождитесь завершения текущей генерации")
            return
        revisions = SectionRevisionService(self.workspace.project.id, self.workspace.repository)
        try:
            history = revisions.list_plan_revisions()
        except Exception as exc:
            self._error(f"Не удалось прочитать историю плана: {exc}")
            return
        if len(history) < 2:
            self._error("Для плана пока нет предыдущей версии")
            return
        answer = QMessageBox.question(
            self,
            "Вернуть прошлую версию плана?",
            "Будет создана новая ревизия на основе предыдущего плана, затем "
            "пересоберутся только затронутые разделы и итоговые проверки.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = revisions.restore_previous_plan_revision()
            if run is not None:
                revisions.prepare_plan_rebuild(run.id, result)
        except Exception as exc:
            self._error(f"Не удалось восстановить план: {exc}")
            return
        self._refresh_plan()
        self._refresh_result_sections()
        if run is None:
            self.statusBar().showMessage(
                f"Восстановлена ревизия плана {result.record.revision}; запустите генерацию.",
                6000,
            )
            return
        self.statusBar().showMessage(
            f"Восстановлена ревизия плана {result.record.revision}; запускается пересборка затронутых разделов.",
            6000,
        )
        self._launch_worker(
            WorkerRequest(
                project_id=self.workspace.project.id,
                projects_root=self.settings.projects_root,
                run_id=run.id,
                retry_from=result.invalidation.start_stage.value,
            )
        )
        self.navigation.setCurrentRow(int(WorkspaceStep.GENERATE))

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
                    unfinished is not None and self._waiting_at_checkpoint(unfinished)
                ),
            )
        )
        self.navigation.setCurrentRow(int(WorkspaceStep.GENERATE))

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
        if self._worker_is_running():
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
        self.worker_project_id = request.project_id
        self.process = process
        process.start(program, arguments)

    def _worker_is_running(self) -> bool:
        return self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning

    def _worker_process_error(self, error: QProcess.ProcessError) -> None:
        if (
            self.process is None
            or self.workspace is None
            or self.worker_project_id != self.workspace.project.id
        ):
            return
        self.events_view.append(
            f"<span style='color:#b91c1c'>Worker: {escape(self.process.errorString())} "
            f"({escape(error.name)})</span>"
        )

    def _read_worker_output(self) -> None:
        assert self.process is not None
        self.process_buffer += bytes(self.process.readAllStandardOutput().data()).decode(
            "utf-8", errors="replace"
        )
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
        if (
            self.workspace is None
            or self.worker_project_id is None
            or self.worker_project_id != self.workspace.project.id
        ):
            return
        if payload.get("run_id"):
            run_id = str(payload["run_id"])
            if self.active_run_id != run_id:
                self.active_run_id = run_id
                self.event_cursor = 0
        event = str(payload.get("event") or "worker")
        if payload.get("error"):
            self.events_view.append(
                f"<span style='color:#b91c1c'>{escape(str(payload['error']))}</span>"
            )
        elif event == "run_ready":
            self.statusBar().showMessage(f"Worker готов: {payload.get('action', 'execute')}", 3000)

    def _read_worker_error(self) -> None:
        assert self.process is not None
        self.process_error_buffer += bytes(self.process.readAllStandardError().data()).decode(
            "utf-8", errors="replace"
        )
        while "\n" in self.process_error_buffer:
            line, self.process_error_buffer = self.process_error_buffer.split("\n", 1)
            self._handle_worker_line(line)

    def _worker_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
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
        self.worker_project_id = None
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
        self.run_stage.setText(_stage_label(run.current_stage) if run.current_stage else "—")
        self.run_cost.setText(f"{run.cost:.4f} {run.currency}")
        stages = self.workspace.repository.list_stages(run.id)
        rendered_at = datetime.now(UTC)
        progress = _progress_presentation(run, stages, now=rendered_at)
        self.run_status.set_status(
            RUN_STATUS_LABELS.get(run.status, run.status.value),
            tone=progress.status_tone,
            icon=icon(
                {
                    "success": "check",
                    "warning": "warning",
                    "error": "error",
                    "info": "generate",
                }.get(progress.status_tone, "generate"),
                {
                    "success": SUCCESS,
                    "warning": WARNING,
                    "error": ERROR,
                    "info": ACCENT_SECONDARY,
                }.get(progress.status_tone, ACCENT_SECONDARY),
            ),
        )
        self.run_operation.setText(progress.operation)
        self.run_progress.setText(progress.progress)
        self.run_elapsed.setText(progress.elapsed)
        self.run_eta.setText(progress.eta)
        self.overall_progress.setValue(progress.overall_percent)
        self.overall_progress.setObjectName(
            {
                "success": "successProgress",
                "warning": "warningProgress",
                "error": "errorProgress",
            }.get(progress.status_tone, "progress")
        )
        self.progress_percent_label.setText(f"{progress.overall_percent}% завершено")
        self.run_quota_wait.setText(progress.quota_message or "")
        self.run_quota_wait.setVisible(progress.quota_message is not None)
        self._refresh_phase_badges(stages)
        self.stages_table.setRowCount(0)
        for stage in stages:
            stage_progress = _stage_progress(stage)
            self._append_row(
                self.stages_table,
                [
                    stage.order + 1,
                    _stage_label(stage.name),
                    STAGE_STATUS_LABELS.get(stage.status, stage.status.value),
                    f"{stage_progress[0]} из {stage_progress[1]}" if stage_progress else "—",
                    _format_duration(_stage_duration(stage, rendered_at)),
                    stage.attempts,
                ],
            )
        for sequence, event in self.workspace.repository.list_events(
            run.id, after_sequence=self.event_cursor
        ):
            self.event_cursor = max(self.event_cursor, sequence)
            self.events_view.append(
                f"[{event.created_at:%H:%M:%S}] {event.message or event.event_type}"
            )
        if run.status == RunStatus.SUCCEEDED:
            self._refresh_results()
        self._update_run_actions(run)
        self._refresh_step_indicators()

    def _update_run_actions(self, run: GenerationRun) -> None:
        process_running = (
            self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning
        )
        self.pause_button.setEnabled(run.status in {RunStatus.RUNNING, RunStatus.RETRYING})
        self.resume_button.setEnabled(
            not process_running
            and run.status in {RunStatus.PAUSED, RunStatus.WAITING_INPUT, RunStatus.FAILED}
        )
        self.cancel_button.setEnabled(run.status not in {RunStatus.SUCCEEDED, RunStatus.CANCELLED})
        self.retry_stage_button.setEnabled(
            not process_running
            and run.status
            in {RunStatus.PAUSED, RunStatus.WAITING_INPUT, RunStatus.FAILED, RunStatus.SUCCEEDED}
        )
        self.refresh_research_button.setEnabled(
            not process_running
            and run.status
            in {RunStatus.PAUSED, RunStatus.WAITING_INPUT, RunStatus.FAILED, RunStatus.SUCCEEDED}
        )

    def _refresh_phase_badges(self, stages: Sequence[object]) -> None:
        """Collapse technical stage state into the four student-facing phases."""

        stage_by_name = {str(getattr(stage, "name", "")): stage for stage in stages}
        for phase_name, stage_names in _PHASES:
            badge = self.phase_badges[phase_name]
            phase_stages = [stage_by_name[name] for name in stage_names if name in stage_by_name]
            statuses = {getattr(stage, "status", None) for stage in phase_stages}
            if phase_stages and statuses <= _FINISHED_STAGE_STATUSES:
                badge.set_status("Готово", tone="success", icon=icon("check", SUCCESS))
            elif statuses & _ACTIVE_STAGE_STATUSES:
                badge.set_status(
                    "Выполняется", tone="running", icon=icon("generate", ACCENT_SECONDARY)
                )
            elif StageStatus.FAILED in statuses or StageStatus.CANCELLED in statuses:
                badge.set_status("Нужна проверка", tone="error", icon=icon("error", ERROR))
            elif statuses & {StageStatus.PAUSED, StageStatus.WAITING_INPUT, StageStatus.RETRYING}:
                badge.set_status("Ожидает действия", tone="warning", icon=icon("warning", WARNING))
            else:
                badge.set_status("Ожидает", tone="neutral", icon=None)

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
        if (
            run is None
            or self.workspace is None
            or run.status not in {RunStatus.PAUSED, RunStatus.WAITING_INPUT, RunStatus.FAILED}
        ):
            self._error("Этот запуск сейчас нельзя продолжить")
            return
        self._launch_worker(
            WorkerRequest(
                project_id=self.workspace.project.id,
                projects_root=self.settings.projects_root,
                run_id=run.id,
                acknowledge_checkpoint=self._waiting_at_checkpoint(run),
            )
        )

    def _waiting_at_checkpoint(self, run: GenerationRun) -> bool:
        """Distinguish approval checkpoints from credential/configuration failures."""

        if (
            self.workspace is None
            or run.status != RunStatus.WAITING_INPUT
            or run.current_stage is None
        ):
            return False
        return any(
            stage.name == run.current_stage
            and stage.status in {StageStatus.SUCCEEDED, StageStatus.SKIPPED}
            for stage in self.workspace.repository.list_stages(run.id)
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

    def _confirm_cancel_run(self) -> None:
        answer = QMessageBox.question(
            self,
            "Отменить генерацию?",
            "Текущий этап будет безопасно остановлен. Уже проверенные результаты останутся в проекте, "
            "но документ не будет собран до следующего запуска.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._cancel_run()

    def _active_run(self) -> GenerationRun | None:
        if self.workspace is None or self.active_run_id is None:
            return None
        return self.workspace.repository.get_run(self.active_run_id)

    def _refresh_results(self) -> None:
        selected_preview = (
            self.preview_pages[self.preview_index]
            if self.preview_pages and 0 <= self.preview_index < len(self.preview_pages)
            else None
        )
        self.artifact_list.clear()
        self.qa_view.clear()
        self.preview_pages = []
        self.docx_card.set_value("Пока не готов")
        self.docx_card.set_detail("Появится после сборки документа.")
        self.docx_card.set_state("default")
        self.pdf_card.set_value("Пока не готов")
        self.pdf_card.set_detail("Появится после визуальной проверки.")
        self.pdf_card.set_state("default")
        self.open_docx_button.setEnabled(False)
        self.export_docx_button.setEnabled(False)
        self.export_pdf_button.setEnabled(False)
        self.qa_badge.set_status("Пока нет проверки", tone="neutral", icon=None)
        if self.workspace is None:
            self.preview_index = 0
            self._render_preview()
            return
        artifacts = self.workspace.repository.list_artifacts(
            self.workspace.project.id, run_id=self.active_run_id
        )
        for artifact in artifacts:
            item = QListWidgetItem(f"{label_for(artifact.kind)} · {Path(artifact.path).name}")
            item.setData(Qt.ItemDataRole.UserRole, artifact.path)
            item.setToolTip(str(artifact.path))
            self.artifact_list.addItem(item)
        document_service = DocumentService(self.workspace.project.id, self.workspace.repository)
        try:
            docx = document_service.latest(ArtifactKind.DOCX, self.active_run_id)
        except (FileNotFoundError, OSError, ValueError):
            docx = None
        if docx is not None:
            self.docx_card.set_value("Готов")
            self.docx_card.set_detail(docx.name)
            self.docx_card.set_state("success")
            self.open_docx_button.setEnabled(True)
            docx_export_reason = document_service.export_block_reason(
                ArtifactKind.DOCX, self.active_run_id
            )
            self.export_docx_button.setEnabled(docx_export_reason is None)
            self.export_docx_button.setToolTip(
                docx_export_reason or "Сохранить проверенный DOCX в выбранное место"
            )
        try:
            pdf = document_service.latest(ArtifactKind.PDF, self.active_run_id)
        except (FileNotFoundError, OSError, ValueError):
            pdf = None
        if pdf is not None:
            self.pdf_card.set_value("Готов")
            self.pdf_card.set_detail(pdf.name)
            self.pdf_card.set_state("success")
            pdf_export_reason = document_service.export_block_reason(
                ArtifactKind.PDF, self.active_run_id
            )
            self.export_pdf_button.setEnabled(pdf_export_reason is None)
            self.export_pdf_button.setToolTip(
                pdf_export_reason or "Сохранить проверенный PDF в выбранное место"
            )
        if self.active_run_id:
            try:
                self.preview_pages = document_service.preview(self.active_run_id)
            except (FileNotFoundError, OSError, ValueError):
                self.preview_pages = []
        if selected_preview in self.preview_pages:
            self.preview_index = self.preview_pages.index(selected_preview)
        else:
            self.preview_index = 0
        self._render_preview()
        if self.active_run_id:
            report = self.workspace.repository.get_latest_qa_report(self.active_run_id)
            if report:
                qa_presentation = {
                    "pass": ("Проверка пройдена", "success", "check", SUCCESS),
                    "warning": ("Есть замечания", "warning", "warning", WARNING),
                    "fail": ("Требуется исправление", "error", "error", ERROR),
                }.get(report.status.value, ("Есть замечания", "warning", "warning", WARNING))
                self.qa_badge.set_status(
                    qa_presentation[0],
                    tone=qa_presentation[1],
                    icon=icon(qa_presentation[2], qa_presentation[3]),
                )
                rows = "".join(
                    f"<li><b>{escape(label_for(issue.severity))}</b> — {escape(issue.message)}</li>"
                    for issue in report.issues
                    if not issue.resolved
                )
                self.qa_view.setHtml(
                    f"<p>{escape(report.summary or 'Проверка завершена.')}</p>"
                    f"<ul>{rows or '<li>Активных замечаний нет.</li>'}</ul>"
                )
        self._refresh_result_sections()
        self._refresh_requirements()
        self._refresh_step_indicators()

    def _render_preview(self) -> None:
        """Render the selected verified PDF preview page inside the result step."""

        if not self.preview_pages:
            self.preview_image.setPixmap(QPixmap())
            self.preview_image.setText("Предпросмотр появится после проверки PDF")
            self.preview_page_label.setText("Страницы пока нет")
            self.preview_previous_button.setEnabled(False)
            self.preview_next_button.setEnabled(False)
            return
        self.preview_index = min(max(self.preview_index, 0), len(self.preview_pages) - 1)
        page = self.preview_pages[self.preview_index]
        pixmap = QPixmap(str(page))
        if pixmap.isNull():
            self.preview_image.setPixmap(QPixmap())
            self.preview_image.setText("Не удалось показать эту страницу")
        else:
            self.preview_image.setText("")
            viewport = self.preview_image.size()
            self.preview_image.setPixmap(
                pixmap.scaled(
                    viewport,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        self.preview_page_label.setText(
            f"Страница {self.preview_index + 1} из {len(self.preview_pages)}"
        )
        self.preview_previous_button.setEnabled(self.preview_index > 0)
        self.preview_next_button.setEnabled(self.preview_index < len(self.preview_pages) - 1)

    def _move_preview(self, offset: int) -> None:
        if not self.preview_pages:
            return
        self.preview_index = min(max(self.preview_index + offset, 0), len(self.preview_pages) - 1)
        self._render_preview()

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

    def _confirm_retry_from_selected_stage(self) -> None:
        raw_stage = self.retry_stage_combo.currentData()
        if raw_stage:
            self._confirm_retry_from(PipelineStage(str(raw_stage)))

    def _confirm_retry_from(self, stage: PipelineStage) -> None:
        answer = QMessageBox.question(
            self,
            "Пересобрать этап?",
            f"PaperCraft повторит этап «{_stage_label(stage.value)}» и зависимые результаты. "
            "Проверенные материалы до этого этапа сохранятся.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._retry_from_stage(stage)

    def _confirm_rebuild_section(self) -> None:
        answer = QMessageBox.question(
            self,
            "Перегенерировать раздел?",
            "Выбранный раздел и зависящие от него части будут написаны заново. "
            "Готовый документ после этого потребуется собрать повторно.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._rebuild_selected_section()

    def _edit_selected_section(self) -> None:
        run = self._active_run()
        section_id = self.result_section_combo.currentData()
        if run is None or self.workspace is None or not section_id:
            self._error("Сначала выберите готовый запуск и раздел")
            return
        if run.status in {RunStatus.RUNNING, RunStatus.RETRYING, RunStatus.QUEUED}:
            self._error("Сначала дождитесь завершения текущей генерации")
            return
        try:
            original = self._section_text_for_editor(str(section_id))
        except (KeyError, RuntimeError) as exc:
            self._error(str(exc))
            return
        edited, accepted = QInputDialog.getMultiLineText(
            self,
            "Правка раздела",
            "Измените текст. После сохранения PaperCraft повторно проверит цитаты, "
            "связность и соберёт DOCX/PDF; незатронутые разделы не будут генерироваться заново. "
            "Фактические утверждения без проверенной evidence остановят QA и экспорт.",
            original,
        )
        if not accepted:
            return
        value = edited.strip()
        if not value:
            self._error("Текст раздела не может быть пустым")
            return
        try:
            result = SectionRevisionService(
                self.workspace.project.id, self.workspace.repository
            ).revise_section(str(section_id), value)
        except Exception as exc:
            self._error(f"Не удалось сохранить правку: {exc}")
            return
        self.statusBar().showMessage(
            f"Создана ревизия {result.record.revision}; запускается повторная проверка.",
            6000,
        )
        self._refresh_results()
        self._launch_worker(
            WorkerRequest(
                project_id=self.workspace.project.id,
                projects_root=self.settings.projects_root,
                run_id=run.id,
                retry_from=result.invalidation.start_stage.value,
            )
        )
        self.navigation.setCurrentRow(int(WorkspaceStep.GENERATE))

    def _restore_selected_section_revision(self) -> None:
        run = self._active_run()
        section_id = self.result_section_combo.currentData()
        if run is None or self.workspace is None or not section_id:
            self._error("Сначала выберите готовый запуск и раздел")
            return
        if run.status in {RunStatus.RUNNING, RunStatus.RETRYING, RunStatus.QUEUED}:
            self._error("Сначала дождитесь завершения текущей генерации")
            return
        revisions = SectionRevisionService(self.workspace.project.id, self.workspace.repository)
        try:
            history = revisions.list_revisions(str(section_id))
        except Exception as exc:
            self._error(f"Не удалось прочитать историю правок: {exc}")
            return
        if len(history) < 2:
            self._error("Для этого раздела пока нет предыдущей версии")
            return
        answer = QMessageBox.question(
            self,
            "Вернуть прошлую версию?",
            "Будет создана новая ревизия на основе предыдущего текста, затем запустится QA и сборка документа.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = revisions.restore_previous_revision(str(section_id))
        except Exception as exc:
            self._error(f"Не удалось восстановить версию: {exc}")
            return
        self.statusBar().showMessage(
            f"Восстановлена ревизия {result.record.revision}; запускается повторная проверка.",
            6000,
        )
        self._refresh_results()
        self._launch_worker(
            WorkerRequest(
                project_id=self.workspace.project.id,
                projects_root=self.settings.projects_root,
                run_id=run.id,
                retry_from=result.invalidation.start_stage.value,
            )
        )
        self.navigation.setCurrentRow(int(WorkspaceStep.GENERATE))

    def _section_text_for_editor(self, section_id: str) -> str:
        if self.workspace is None:
            raise RuntimeError("Проект не открыт")
        manuscript = self.workspace.repository.get_latest_manuscript(self.workspace.project.id)
        if manuscript is None:
            raise RuntimeError("Сначала создайте черновик документа")
        active = False
        paragraphs: list[str] = []
        unsupported_blocks: set[str] = set()
        for block in manuscript.blocks:
            if isinstance(block, HeadingBlock) and block.section_id is not None:
                if active:
                    break
                active = block.section_id == section_id
                continue
            if not active:
                continue
            if isinstance(block, ParagraphBlock):
                paragraphs.append(block.text)
            else:
                # ``_edit_selected_section`` saves a string as a complete
                # replacement body.  Silently omitting a table, formula or
                # figure from the editor text would therefore delete it on
                # save.  A rich mixed-block editor is intentionally outside
                # this beta; refuse the lossy operation until one exists.
                unsupported_blocks.add(block.type)
        if not active:
            raise KeyError("Выбранный раздел не найден в текущем черновике")
        if unsupported_blocks:
            block_labels = ", ".join(sorted(unsupported_blocks))
            raise RuntimeError(
                "Этот раздел содержит нетекстовые блоки "
                f"({block_labels}). Текстовый редактор beta не может безопасно сохранить "
                "такую правку без удаления таблиц, формул или иллюстраций. "
                "Используйте пересборку раздела или дождитесь редактора смешанного содержимого."
            )
        return "\n\n".join(paragraphs)

    def _confirm_rebuild_document(self) -> None:
        answer = QMessageBox.question(
            self,
            "Пересобрать документ?",
            "Готовые разделы будут использованы повторно, а итоговый DOCX, PDF и проверка будут выполнены заново.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._retry_from_stage(PipelineStage.GENERATE_SECTIONS)

    def _refresh_research(self) -> None:
        run = self._active_run()
        if run is None or self.workspace is None:
            self._error("Сначала запустите автопилот")
            return
        if run.status in {RunStatus.RUNNING, RunStatus.RETRYING, RunStatus.QUEUED}:
            self._error("Дождитесь паузы или завершения текущей генерации")
            return
        self._launch_worker(
            WorkerRequest(
                project_id=self.workspace.project.id,
                projects_root=self.settings.projects_root,
                run_id=run.id,
                refresh_research=True,
            )
        )
        self.navigation.setCurrentRow(int(WorkspaceStep.GENERATE))

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
        self.navigation.setCurrentRow(int(WorkspaceStep.GENERATE))

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
        self.navigation.setCurrentRow(int(WorkspaceStep.GENERATE))

    def _open_preview(self) -> None:
        if self.workspace is None or self.active_run_id is None:
            self._error("Предпросмотр пока не создан")
            return
        self._refresh_results()
        if not self.preview_pages:
            self._error("В этом запуске нет постраничного предпросмотра")
            return
        self.navigation.setCurrentRow(int(WorkspaceStep.RESULT))

    def _open_artifact(self, item: QListWidgetItem) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(item.data(Qt.ItemDataRole.UserRole))))

    def _open_word(self) -> None:
        if self.workspace:
            try:
                DocumentService(self.workspace.project.id, self.workspace.repository).open_in_word(
                    self.active_run_id
                )
            except Exception as exc:
                self._error(str(exc))

    def _export(self, kind: ArtifactKind) -> None:
        if self.workspace is None:
            return
        suffix = ".docx" if kind == ArtifactKind.DOCX else ".pdf"
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт", filter=f"{suffix.upper()} (*{suffix})"
        )
        if path:
            try:
                result = DocumentService(
                    self.workspace.project.id, self.workspace.repository
                ).export(kind, path, self.active_run_id)
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
        self.setStyleSheet(dark_stylesheet())

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "preview_image"):
            QTimer.singleShot(0, self._render_preview)

    def _error(self, message: str) -> None:
        self._show_banner(message, "error", timeout_ms=8000)


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


__all__ = ["PAGE_NAMES", "MainWindow", "WorkspaceStep"]
