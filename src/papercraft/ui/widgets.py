"""Small, themeable building blocks for the PaperCraft desktop workspace.

The widgets in this module deliberately own no application state.  They expose
stable child widgets and dynamic properties so the main window can compose
them freely while the QSS theme controls their visual treatment.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

_TONES = frozenset({"neutral", "info", "success", "warning", "error", "running"})
_CARD_STATES = frozenset({"default", "success", "warning", "error"})


def _normalise_tone(value: str) -> str:
    """Keep dynamic QSS properties predictable when data comes from a run."""

    normalised = value.strip().lower()
    return normalised if normalised in _TONES else "neutral"


def _normalise_card_state(value: str) -> str:
    normalised = value.strip().lower()
    return normalised if normalised in _CARD_STATES else "default"


def _refresh_style(widget: QWidget) -> None:
    """Ask Qt to re-evaluate QSS selectors after a dynamic property changes."""

    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def _set_icon(label: QLabel, icon: QIcon | None, size: int) -> None:
    """Render an optional icon in a decorative label without changing layout."""

    visible = icon is not None and not icon.isNull()
    label.setVisible(visible)
    if visible and icon is not None:
        label.setPixmap(icon.pixmap(QSize(size, size)))
    else:
        label.clear()


class Card(QFrame):
    """A padded surface with semantic state properties for the application QSS.

    ``content_layout`` is intentionally public: callers add normal Qt widgets
    and layouts without needing a specialised container API.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        variant: str = "default",
        state: str = "default",
        accessible_name: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("component", "card")
        self.setProperty("variant", variant)
        self.setProperty("state", _normalise_card_state(state))
        self.setAccessibleName(accessible_name or "Карточка")
        self.content_layout = QVBoxLayout(self)
        self.content_layout.setContentsMargins(20, 18, 20, 18)
        self.content_layout.setSpacing(12)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

    def set_variant(self, variant: str) -> None:
        self.setProperty("variant", variant)
        _refresh_style(self)

    def set_state(self, state: str) -> None:
        self.setProperty("state", _normalise_card_state(state))
        _refresh_style(self)


class SectionHeader(QWidget):
    """A page or card heading with optional icon, subtitle and trailing actions."""

    def __init__(
        self,
        title: str,
        subtitle: str | None = None,
        *,
        icon: QIcon | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("sectionHeader")
        self.setProperty("component", "sectionHeader")
        self.setAccessibleName(title)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.icon_label = QLabel(self)
        self.icon_label.setObjectName("sectionHeaderIcon")
        self.icon_label.setAccessibleName("")
        _set_icon(self.icon_label, icon, 24)
        layout.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignTop)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(3)
        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("sectionHeaderTitle")
        self.title_label.setWordWrap(True)
        self.subtitle_label = QLabel(self)
        self.subtitle_label.setObjectName("sectionHeaderSubtitle")
        self.subtitle_label.setWordWrap(True)
        self.set_subtitle(subtitle)
        text_column.addWidget(self.title_label)
        text_column.addWidget(self.subtitle_label)
        layout.addLayout(text_column, 1)

        self.actions_layout = QHBoxLayout()
        self.actions_layout.setContentsMargins(8, 0, 0, 0)
        self.actions_layout.setSpacing(8)
        layout.addLayout(self.actions_layout)

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)
        self.setAccessibleName(title)

    def set_subtitle(self, subtitle: str | None) -> None:
        text = subtitle or ""
        self.subtitle_label.setText(text)
        self.subtitle_label.setVisible(bool(text))

    def set_icon(self, icon: QIcon | None) -> None:
        _set_icon(self.icon_label, icon, 24)

    def add_action(self, action: QWidget) -> None:
        """Place a compact secondary action at the trailing edge of the header."""

        self.actions_layout.addWidget(action, 0, Qt.AlignmentFlag.AlignTop)


class MetricCard(Card):
    """A compact label/value card for project, run and QA summaries."""

    def __init__(
        self,
        label: str,
        value: str = "—",
        detail: str | None = None,
        *,
        icon: QIcon | None = None,
        tone: str = "neutral",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, variant="metric", accessible_name=label)
        self.setProperty("metric", True)
        self.set_tone(tone)
        self.content_layout.setSpacing(6)

        label_row = QHBoxLayout()
        label_row.setContentsMargins(0, 0, 0, 0)
        label_row.setSpacing(7)
        self.icon_label = QLabel(self)
        self.icon_label.setObjectName("metricIcon")
        self.icon_label.setAccessibleName("")
        _set_icon(self.icon_label, icon, 18)
        label_row.addWidget(self.icon_label)
        self.label = QLabel(label, self)
        self.label.setObjectName("metricLabel")
        self.label.setWordWrap(True)
        label_row.addWidget(self.label, 1)
        self.content_layout.addLayout(label_row)

        self.value_label = QLabel(value, self)
        self.value_label.setObjectName("metricValue")
        self.value_label.setWordWrap(True)
        self.content_layout.addWidget(self.value_label)

        self.detail_label = QLabel(self)
        self.detail_label.setObjectName("metricDetail")
        self.detail_label.setWordWrap(True)
        self.set_detail(detail)
        self.content_layout.addWidget(self.detail_label)

    def set_label(self, label: str) -> None:
        self.label.setText(label)
        self.setAccessibleName(label)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)

    def set_detail(self, detail: str | None) -> None:
        text = detail or ""
        self.detail_label.setText(text)
        self.detail_label.setVisible(bool(text))

    def set_icon(self, icon: QIcon | None) -> None:
        _set_icon(self.icon_label, icon, 18)

    def set_tone(self, tone: str) -> None:
        self.setProperty("tone", _normalise_tone(tone))
        _refresh_style(self)


class StatusBadge(QFrame):
    """An accessible text status chip whose QSS colour is selected by ``tone``."""

    def __init__(
        self,
        text: str = "",
        *,
        tone: str = "neutral",
        icon: QIcon | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("statusBadge")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("component", "statusBadge")
        self.setProperty("tone", _normalise_tone(tone))
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 4, 9, 4)
        layout.setSpacing(5)
        self.icon_label = QLabel(self)
        self.icon_label.setObjectName("statusBadgeIcon")
        self.icon_label.setAccessibleName("")
        _set_icon(self.icon_label, icon, 14)
        layout.addWidget(self.icon_label)
        self.label = QLabel(text, self)
        self.label.setObjectName("statusBadgeText")
        layout.addWidget(self.label)
        self.set_text(text)

    def text(self) -> str:
        return self.label.text()

    def set_text(self, text: str) -> None:
        self.label.setText(text)
        description = f"Статус: {text}" if text else "Статус"
        self.setAccessibleName(description)
        self.setToolTip(description)

    def set_tone(self, tone: str) -> None:
        self.setProperty("tone", _normalise_tone(tone))
        _refresh_style(self)

    def set_icon(self, icon: QIcon | None) -> None:
        _set_icon(self.icon_label, icon, 14)

    def set_status(
        self,
        text: str,
        *,
        tone: str | None = None,
        icon: QIcon | None = None,
    ) -> None:
        self.set_text(text)
        if tone is not None:
            self.set_tone(tone)
        self.set_icon(icon)


class EmptyState(Card):
    """A friendly empty-state card with an optional, signal-based primary action."""

    action_requested = Signal()

    def __init__(
        self,
        title: str,
        description: str,
        action_text: str | None = None,
        *,
        icon: QIcon | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, variant="empty", accessible_name=title)
        self.setProperty("empty", True)
        self.content_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon_label = QLabel(self)
        self.icon_label.setObjectName("emptyStateIcon")
        self.icon_label.setAccessibleName("")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _set_icon(self.icon_label, icon, 36)
        self.content_layout.addWidget(self.icon_label)

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("emptyStateTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setWordWrap(True)
        self.content_layout.addWidget(self.title_label)

        self.description_label = QLabel(description, self)
        self.description_label.setObjectName("emptyStateDescription")
        self.description_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.description_label.setWordWrap(True)
        self.content_layout.addWidget(self.description_label)

        self.action_button = QPushButton(self)
        self.action_button.setObjectName("emptyStateAction")
        self.action_button.setAccessibleName("Основное действие")
        self.action_button.clicked.connect(self.action_requested.emit)
        self.set_action(action_text)
        self.content_layout.addWidget(self.action_button, 0, Qt.AlignmentFlag.AlignCenter)

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)
        self.setAccessibleName(title)

    def set_description(self, description: str) -> None:
        self.description_label.setText(description)

    def set_icon(self, icon: QIcon | None) -> None:
        _set_icon(self.icon_label, icon, 36)

    def set_action(
        self,
        text: str | None,
        *,
        icon: QIcon | None = None,
        tooltip: str | None = None,
    ) -> None:
        visible = bool(text)
        self.action_button.setVisible(visible)
        self.action_button.setText(text or "")
        self.action_button.setIcon(icon or QIcon())
        self.action_button.setToolTip(tooltip or (text or ""))
        self.action_button.setAccessibleName(text or "Основное действие")


class CollapsibleSection(QFrame):
    """A titled, keyboard-accessible disclosure panel.

    Add content through ``content_layout`` or use :meth:`set_content` for a
    single body widget.  The body stays parented while collapsed, so state in
    controls and views is preserved.
    """

    expanded_changed = Signal(bool)

    def __init__(
        self,
        title: str,
        subtitle: str | None = None,
        *,
        icon: QIcon | None = None,
        expanded: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("collapsibleSection")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("component", "collapsibleSection")
        self.setAccessibleName(title)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        header = QWidget(self)
        header.setObjectName("collapsibleHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 10, 14, 10)
        header_layout.setSpacing(8)

        self.icon_label = QLabel(header)
        self.icon_label.setObjectName("collapsibleIcon")
        self.icon_label.setAccessibleName("")
        _set_icon(self.icon_label, icon, 18)
        header_layout.addWidget(self.icon_label)

        self.toggle_button = QToolButton(header)
        self.toggle_button.setObjectName("collapsibleToggle")
        self.toggle_button.setText(title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.toggle_button.setAccessibleName(f"Раздел: {title}")
        self.toggle_button.toggled.connect(self._on_toggled)
        header_layout.addWidget(self.toggle_button, 1)

        self.subtitle_label = QLabel(header)
        self.subtitle_label.setObjectName("collapsibleSubtitle")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.set_subtitle(subtitle)
        header_layout.addWidget(self.subtitle_label)
        root_layout.addWidget(header)

        self.body = QWidget(self)
        self.body.setObjectName("collapsibleBody")
        self.content_layout = QVBoxLayout(self.body)
        self.content_layout.setContentsMargins(14, 0, 14, 14)
        self.content_layout.setSpacing(10)
        root_layout.addWidget(self.body)
        self._content_widget: QWidget | None = None

        self.toggle_button.setChecked(expanded)
        self._apply_expanded(expanded)

    @property
    def expanded(self) -> bool:
        return self.toggle_button.isChecked()

    @property
    def content_widget(self) -> QWidget | None:
        return self._content_widget

    def set_title(self, title: str) -> None:
        self.toggle_button.setText(title)
        self.setAccessibleName(title)
        self.toggle_button.setAccessibleName(f"Раздел: {title}")
        self._update_toggle_tooltip()

    def set_subtitle(self, subtitle: str | None) -> None:
        text = subtitle or ""
        self.subtitle_label.setText(text)
        self.subtitle_label.setVisible(bool(text))

    def set_icon(self, icon: QIcon | None) -> None:
        _set_icon(self.icon_label, icon, 18)

    def set_content(self, content: QWidget | None) -> None:
        """Replace the optional single content widget without deleting it."""

        if self._content_widget is content:
            return
        if self._content_widget is not None:
            self.content_layout.removeWidget(self._content_widget)
            self._content_widget.setParent(None)
        self._content_widget = content
        if content is not None:
            self.content_layout.addWidget(content)

    def set_expanded(self, expanded: bool) -> None:
        if self.toggle_button.isChecked() != expanded:
            self.toggle_button.setChecked(expanded)
            return
        self._apply_expanded(expanded)

    def toggle(self) -> None:
        self.set_expanded(not self.expanded)

    def _on_toggled(self, expanded: bool) -> None:
        self._apply_expanded(expanded)
        self.expanded_changed.emit(expanded)

    def _apply_expanded(self, expanded: bool) -> None:
        self.body.setVisible(expanded)
        self.toggle_button.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.setProperty("expanded", expanded)
        self._update_toggle_tooltip()
        _refresh_style(self)

    def _update_toggle_tooltip(self) -> None:
        action = "Свернуть" if self.expanded else "Развернуть"
        self.toggle_button.setToolTip(f"{action} раздел: {self.toggle_button.text()}")
__all__ = [
    "Card",
    "CollapsibleSection",
    "EmptyState",
    "MetricCard",
    "SectionHeader",
    "StatusBadge",
]
