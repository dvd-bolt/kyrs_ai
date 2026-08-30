"""Small, dependency-free PaperCraft icon set.

Icons are stored as inline SVG paths, rendered by Qt at call time and therefore
work in packaged builds without a resource file or an icon-font dependency.
"""

from __future__ import annotations

import re
from typing import Final

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .theme import TEXT

_ICON_SIZE: Final = 24
_VALID_COLOR: Final = re.compile(r"^#[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{3})?$")

# All paths use a 24x24 viewport.  Stroke-based icons make the set visually
# coherent at both navigation and button sizes.
_PATHS: Final[dict[str, str]] = {
    "logo": (
        '<path d="M5.1 4.7 12 2l6.9 2.7v5.2c0 4.6-2.9 8.6-6.9 10.1-4-1.5-6.9-5.5-6.9-10.1V4.7Z" '
        'fill="currentColor" stroke="none"/>'
        '<path d="M8.3 8.2h7.4M8.3 11.2h5.1M8.3 14.2h4" stroke="#090B10" stroke-width="1.5" '
        'stroke-linecap="round"/>'
    ),
    "project": '<path d="M3.5 6.5h6l1.8 2h9.2v9.7a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2V6.5Z"/>',
    "plan": '<path d="M6 3.5h9l3 3v14H6a2 2 0 0 1-2-2v-13a2 2 0 0 1 2-2Z"/><path d="M9 10h6M9 14h6M9 18h3"/>',
    "generate": '<path d="m12 3 1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3Z"/><path d="m18.5 15 .9 2.6L22 18.5l-2.6.9-.9 2.6-.9-2.6-2.6-.9 2.6-.9.9-2.6Z"/>',
    "result": '<path d="M5 3.5h10l4 4v13H5a2 2 0 0 1-2-2v-13a2 2 0 0 1 2-2Z"/><path d="M15 3.5v4h4M8 14l2.3 2.3L16 10.6"/>',
    "add": '<path d="M12 5v14M5 12h14"/>',
    "save": '<path d="M5 3.5h12.7L21 6.8v13.7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-15a2 2 0 0 1 2-2Z"/><path d="M7 3.5v6h9v-6M7.5 21.5v-7h9v7"/>',
    "upload": '<path d="M12 16V4M7.5 8.5 12 4l4.5 4.5M5 15.5v4a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4"/>',
    "more": '<path d="M5 12h.01M12 12h.01M19 12h.01" stroke-width="3"/>',
    "settings": '<path d="M12 8.1a3.9 3.9 0 1 0 0 7.8 3.9 3.9 0 0 0 0-7.8Z"/><path d="m19.4 13.5 1.3 1-1.8 3.1-1.6-.7a7.4 7.4 0 0 1-2.1 1.2L15 20h-3.6l-.2-1.9a7.4 7.4 0 0 1-2.1-1.2l-1.6.7-1.8-3.1 1.3-1a7.8 7.8 0 0 1 0-3l-1.3-1 1.8-3.1 1.6.7a7.4 7.4 0 0 1 2.1-1.2l.2-1.9H15l.2 1.9a7.4 7.4 0 0 1 2.1 1.2l1.6-.7 1.8 3.1-1.3 1a7.8 7.8 0 0 1 0 3Z"/>',
    "pause": '<path d="M8 5v14M16 5v14" stroke-width="3"/>',
    "play": '<path d="m8 5 11 7-11 7V5Z" fill="currentColor" stroke="none"/>',
    "cancel": '<path d="m6 6 12 12M18 6 6 18" stroke-width="2.3"/>',
    "export": '<path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 15.5v4a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4"/>',
    "folder": '<path d="M3.5 6.5h6l1.8 2h9.2v9.7a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2V6.5Z"/><path d="M3.5 9.5h17"/>',
    "document": '<path d="M6 3.5h8.5L19 8v12.5H6a2 2 0 0 1-2-2v-13a2 2 0 0 1 2-2Z"/><path d="M14.5 3.5V8H19M8 12h7M8 16h7"/>',
    "check": '<path d="m5 12.5 4.3 4.3L19.5 6.7" stroke-width="2.4"/>',
    "warning": '<path d="M10.3 4.1 2.9 17a2.3 2.3 0 0 0 2 3.4h14.2a2.3 2.3 0 0 0 2-3.4L13.7 4.1a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4.5M12 17.2v.1" stroke-width="2"/>',
    "error": '<circle cx="12" cy="12" r="8.5"/><path d="m9 9 6 6m0-6-6 6" stroke-width="2.1"/>',
    "refresh": '<path d="M19.5 9A7.8 7.8 0 0 0 5.7 6.2L4 8M4 4v4h4M4.5 15A7.8 7.8 0 0 0 18.3 17.8L20 16M20 20v-4h-4"/>',
    "chevron": '<path d="m9 5 7 7-7 7" stroke-width="2.2"/>',
}


def _icon_color(color: str | None) -> str:
    """Return a safe hex colour for embedding into inline SVG."""

    if color and _VALID_COLOR.fullmatch(color):
        return color
    return TEXT


def icon_svg(name: str, color: str | None = None) -> str:
    """Return an inline SVG document for one named PaperCraft icon.

    Unknown names intentionally fall back to the document icon.  A missing
    ornament must never prevent a user from accessing an application action.
    """

    path = _PATHS.get(name, _PATHS["document"])
    resolved_color = _icon_color(color)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{resolved_color}" stroke-width="1.8" stroke-linecap="round" '
        f'stroke-linejoin="round" color="{resolved_color}">{path}</svg>'
    )


def icon(name: str, color: str | None = None) -> QIcon:
    """Create a :class:`QIcon` from the self-contained PaperCraft SVG set."""

    renderer = QSvgRenderer(QByteArray(icon_svg(name, color).encode("utf-8")))
    pixmap = QPixmap(QSize(_ICON_SIZE, _ICON_SIZE))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    return QIcon(pixmap)


__all__ = ["icon", "icon_svg"]
