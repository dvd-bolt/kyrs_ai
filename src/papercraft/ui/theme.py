"""Reusable Dark Studio visual language for the PaperCraft desktop UI.

The module deliberately contains no widget construction.  Keeping colours and
QSS here makes the application theme easy to reuse in the main window, dialogs
and small custom controls without coupling it to a particular screen layout.
"""

from __future__ import annotations

from typing import Final

# Core palette.  These names are intentionally short and stable: screens may
# also use them for rich text or generated SVGs where QSS is not available.
BACKGROUND: Final = "#090B10"
PANEL: Final = "#131824"
PANEL_ELEVATED: Final = "#192031"
BORDER: Final = "#263044"
TEXT: Final = "#F5F7FB"
TEXT_MUTED: Final = "#9AA7BD"
ACCENT: Final = "#7C5CFC"
ACCENT_HOVER: Final = "#9077FF"
ACCENT_PRESSED: Final = "#6246D8"
ACCENT_SECONDARY: Final = "#35C2FF"
SUCCESS: Final = "#43D19E"
WARNING: Final = "#F5B946"
ERROR: Final = "#F26D7D"
INFO: Final = "#35C2FF"
FOCUS_RING: Final = "#B5A6FF"

FONT_FAMILY: Final = "Segoe UI Variable"
BASE_FONT_SIZE: Final = 14
CARD_RADIUS: Final = 14
BUTTON_MIN_HEIGHT: Final = 40


def dark_stylesheet() -> str:
    """Return the complete PaperCraft Dark Studio Qt stylesheet.

    Widgets opt into semantic variants through object names (for example,
    ``primary`` or ``statusSuccess``) and can use the matching ``status``
    dynamic property for badges.  The base selectors intentionally remain
    conservative so native dialogs and Windows window chrome are preserved.
    """

    return f"""
    * {{
        font-family: "{FONT_FAMILY}", "Segoe UI", sans-serif;
        font-size: {BASE_FONT_SIZE}px;
        color: {TEXT};
    }}

    QMainWindow {{
        background: {BACKGROUND};
        color: {TEXT};
    }}
    QWidget {{ color: {TEXT}; }}
    QLabel {{ background: transparent; }}
    QDialog, QMessageBox {{
        background: {PANEL};
    }}
    QLabel#pageTitle {{
        color: {TEXT};
        font-size: 28px;
        font-weight: 700;
        letter-spacing: 0.1px;
    }}
    QLabel#sectionTitle {{
        color: {TEXT};
        font-size: 18px;
        font-weight: 650;
    }}
    QLabel#subtitle, QLabel#muted, QLabel#helperText {{
        color: {TEXT_MUTED};
    }}
    QLabel#eyebrow {{
        color: {ACCENT_SECONDARY};
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.9px;
    }}

    QFrame#card, QWidget#card, QGroupBox#card {{
        background: {PANEL};
        border: 1px solid {BORDER};
        border-radius: {CARD_RADIUS}px;
    }}
    QFrame#card:hover, QWidget#card:hover {{
        border-color: #394762;
        background: {PANEL_ELEVATED};
    }}
    QGroupBox#card {{
        margin-top: 12px;
        padding: 18px 16px 16px 16px;
        font-size: 15px;
        font-weight: 650;
    }}
    QGroupBox#card::title {{
        subcontrol-origin: margin;
        left: 14px;
        padding: 0 6px;
        color: {TEXT};
    }}
    QWidget#sidebar {{
        background: #0E121B;
        border-right: 1px solid {BORDER};
    }}
    QScrollArea#workspaceScroll {{
        border: none;
        background: {BACKGROUND};
    }}
    QWidget#workspacePage {{
        background: {BACKGROUND};
    }}
    QLabel#brandTitle {{
        color: {TEXT};
        font-size: 20px;
        font-weight: 700;
    }}
    QLabel#brandCaption {{
        color: {ACCENT_SECONDARY};
        font-size: 12px;
        font-weight: 650;
        letter-spacing: 0.8px;
    }}
    QLabel#sidebarProject {{
        padding: 10px 11px;
        color: {TEXT_MUTED};
        background: #111723;
        border: 1px solid #202A3B;
        border-radius: 10px;
    }}
    QListWidget#stepNavigation {{
        padding: 4px;
        background: transparent;
        border: none;
        border-radius: 0;
    }}
    QListWidget#stepNavigation::item {{
        min-height: 45px;
        padding: 10px 9px;
        margin: 2px 0;
        color: {TEXT_MUTED};
        border: 1px solid transparent;
        border-radius: 10px;
    }}
    QListWidget#stepNavigation::item:hover {{
        color: {TEXT};
        background: #151D2B;
    }}
    QListWidget#stepNavigation::item:selected {{
        color: #F7F5FF;
        background: rgba(124, 92, 252, 42);
        border-color: rgba(124, 92, 252, 130);
    }}
    QListWidget#stepNavigation::item:disabled {{
        color: #596477;
        background: transparent;
    }}
    QLabel#noticeBanner {{
        margin: 12px 24px 0 24px;
        padding: 10px 12px;
        border-radius: 10px;
        font-weight: 600;
        background: rgba(53, 194, 255, 24);
        color: #AEE9FF;
        border: 1px solid rgba(53, 194, 255, 100);
    }}
    QLabel#noticeBanner[tone="success"] {{
        background: rgba(67, 209, 158, 25);
        color: #8DF0C2;
        border-color: rgba(67, 209, 158, 130);
    }}
    QLabel#noticeBanner[tone="warning"] {{
        background: rgba(245, 185, 70, 24);
        color: #FFD887;
        border-color: rgba(245, 185, 70, 130);
    }}
    QLabel#noticeBanner[tone="error"] {{
        background: rgba(242, 109, 125, 24);
        color: #FFB7C0;
        border-color: rgba(242, 109, 125, 130);
    }}
    QWidget#sectionHeader {{ background: transparent; border: none; }}
    QLabel#sectionHeaderTitle {{
        color: {TEXT};
        font-size: 19px;
        font-weight: 700;
    }}
    QLabel#sectionHeaderSubtitle {{
        color: {TEXT_MUTED};
        font-size: 13px;
    }}
    QLabel#metricLabel {{
        color: {TEXT_MUTED};
        font-size: 12px;
        font-weight: 650;
    }}
    QLabel#metricValue {{
        color: {TEXT};
        font-size: 21px;
        font-weight: 700;
    }}
    QLabel#metricDetail {{
        color: {TEXT_MUTED};
        font-size: 12px;
    }}
    QFrame#card[variant="metric"] {{
        min-height: 96px;
        background: #121925;
    }}
    QFrame#card[state="success"] {{ border-color: rgba(67, 209, 158, 150); }}
    QFrame#card[state="warning"] {{ border-color: rgba(245, 185, 70, 150); }}
    QFrame#card[state="error"] {{ border-color: rgba(242, 109, 125, 150); }}
    QFrame#statusBadge {{
        background: #151C29;
        border: 1px solid {BORDER};
        border-radius: 9px;
    }}
    QFrame#statusBadge QLabel {{ background: transparent; }}
    QFrame#statusBadge QLabel#statusBadgeText {{
        color: {TEXT_MUTED};
        font-size: 12px;
        font-weight: 700;
    }}
    QFrame#statusBadge[tone="info"], QFrame#statusBadge[tone="running"] {{
        background: rgba(53, 194, 255, 24);
        border-color: rgba(53, 194, 255, 130);
    }}
    QFrame#statusBadge[tone="info"] QLabel#statusBadgeText,
    QFrame#statusBadge[tone="running"] QLabel#statusBadgeText {{ color: #AEE9FF; }}
    QFrame#statusBadge[tone="success"] {{
        background: rgba(67, 209, 158, 25);
        border-color: rgba(67, 209, 158, 130);
    }}
    QFrame#statusBadge[tone="success"] QLabel#statusBadgeText {{ color: #8DF0C2; }}
    QFrame#statusBadge[tone="warning"] {{
        background: rgba(245, 185, 70, 24);
        border-color: rgba(245, 185, 70, 130);
    }}
    QFrame#statusBadge[tone="warning"] QLabel#statusBadgeText {{ color: #FFD887; }}
    QFrame#statusBadge[tone="error"] {{
        background: rgba(242, 109, 125, 24);
        border-color: rgba(242, 109, 125, 130);
    }}
    QFrame#statusBadge[tone="error"] QLabel#statusBadgeText {{ color: #FFB7C0; }}
    QFrame#collapsibleSection {{
        background: #111824;
        border: 1px solid {BORDER};
        border-radius: 12px;
    }}
    QWidget#collapsibleHeader {{ background: transparent; border: none; }}
    QToolButton#collapsibleToggle {{
        min-height: 28px;
        padding: 2px 0;
        color: {TEXT};
        background: transparent;
        border: none;
        font-weight: 700;
        text-align: left;
    }}
    QToolButton#collapsibleToggle:hover {{ color: #D7D0FF; }}
    QLabel#collapsibleSubtitle {{ color: {TEXT_MUTED}; font-size: 12px; }}
    QWidget#collapsibleBody {{ background: transparent; border: none; }}
    QLabel#emptyStateTitle {{ color: {TEXT}; font-size: 17px; font-weight: 700; }}
    QLabel#emptyStateDescription {{ color: {TEXT_MUTED}; }}
    QPushButton#emptyStateAction {{ background: {ACCENT}; color: white; border-color: {ACCENT}; }}
    QLabel#operationTitle {{ color: {TEXT}; font-size: 18px; font-weight: 700; }}
    QLabel#phaseTitle {{ color: {TEXT_MUTED}; font-size: 12px; font-weight: 650; }}
    QLabel#previewImage {{
        color: {TEXT_MUTED};
        background: #0B1018;
        border: 1px dashed #38455D;
        border-radius: 11px;
    }}

    QPushButton {{
        min-height: {BUTTON_MIN_HEIGHT}px;
        padding: 0 16px;
        background: {PANEL_ELEVATED};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 10px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background: #222B3E;
        border-color: #43516C;
    }}
    QPushButton:pressed {{
        background: #161D2B;
        border-color: #546480;
    }}
    QPushButton:disabled {{
        color: #68748A;
        background: #111722;
        border-color: #20293A;
    }}
    QPushButton#primary {{
        background: {ACCENT};
        color: white;
        border-color: {ACCENT};
    }}
    QPushButton#primary:hover {{
        background: {ACCENT_HOVER};
        border-color: {ACCENT_HOVER};
    }}
    QPushButton#primary:pressed {{
        background: {ACCENT_PRESSED};
        border-color: {ACCENT_PRESSED};
    }}
    QPushButton#primary:disabled {{
        color: #827BA2;
        background: #25213B;
        border-color: #332D50;
    }}
    QPushButton#secondary {{
        background: rgba(53, 194, 255, 24);
        color: #AEE9FF;
        border-color: rgba(53, 194, 255, 110);
    }}
    QPushButton#secondary:hover {{
        background: rgba(53, 194, 255, 45);
        border-color: {ACCENT_SECONDARY};
    }}
    QPushButton#secondary:disabled {{
        color: #657286;
        background: #111722;
        border-color: #20293A;
    }}
    QPushButton#danger {{
        background: rgba(242, 109, 125, 22);
        color: #FFB7C0;
        border-color: rgba(242, 109, 125, 105);
    }}
    QPushButton#danger:hover {{
        background: rgba(242, 109, 125, 43);
        border-color: {ERROR};
    }}
    QPushButton#danger:disabled {{
        color: #80616A;
        background: #17141B;
        border-color: #2B222B;
    }}
    QPushButton#quiet {{
        background: transparent;
        color: {TEXT_MUTED};
        border-color: transparent;
    }}
    QPushButton#quiet:hover {{
        background: #1A2232;
        color: {TEXT};
        border-color: transparent;
    }}
    QToolButton#quiet {{
        min-height: {BUTTON_MIN_HEIGHT}px;
        padding: 0 10px;
        color: {TEXT_MUTED};
        background: transparent;
        border: 1px solid transparent;
        border-radius: 10px;
        font-weight: 600;
    }}
    QToolButton#quiet:hover {{
        color: {TEXT};
        background: #1A2232;
    }}
    QPushButton#iconButton {{
        min-width: {BUTTON_MIN_HEIGHT}px;
        max-width: {BUTTON_MIN_HEIGHT}px;
        padding: 0;
        border-radius: 10px;
    }}

    QLineEdit, QTextEdit, QTextBrowser, QPlainTextEdit, QComboBox,
    QDoubleSpinBox, QSpinBox, QDateEdit {{
        min-height: 28px;
        padding: 6px 10px;
        background: #101621;
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 9px;
        selection-background-color: {ACCENT};
        selection-color: white;
    }}
    QTextEdit, QTextBrowser, QPlainTextEdit {{
        padding: 9px 10px;
    }}
    QLineEdit:hover, QTextEdit:hover, QTextBrowser:hover, QPlainTextEdit:hover,
    QComboBox:hover, QDoubleSpinBox:hover, QSpinBox:hover, QDateEdit:hover {{
        border-color: #43516C;
    }}
    QLineEdit:focus, QTextEdit:focus, QTextBrowser:focus, QPlainTextEdit:focus,
    QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus, QDateEdit:focus,
    QPushButton:focus, QAbstractItemView:focus {{
        border-color: {FOCUS_RING};
    }}
    QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled,
    QDoubleSpinBox:disabled, QSpinBox:disabled, QDateEdit:disabled {{
        background: #0D121B;
        color: #68748A;
        border-color: #20293A;
    }}
    QLineEdit#error, QTextEdit#error, QComboBox#error {{
        border-color: {ERROR};
        background: #21141C;
    }}
    QLineEdit#warning, QTextEdit#warning, QComboBox#warning {{
        border-color: {WARNING};
        background: #221C10;
    }}
    QLineEdit#success, QTextEdit#success, QComboBox#success {{
        border-color: {SUCCESS};
        background: #11221E;
    }}
    QComboBox::drop-down {{
        width: 28px;
        border: none;
    }}
    QComboBox QAbstractItemView {{
        padding: 5px;
        background: {PANEL_ELEVATED};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 9px;
        selection-background-color: {ACCENT};
    }}

    QTableWidget, QTreeWidget, QListWidget {{
        background: #101621;
        alternate-background-color: #131A27;
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 10px;
        outline: none;
    }}
    QTableWidget::item, QTreeWidget::item, QListWidget::item {{
        padding: 8px;
        border-radius: 7px;
    }}
    QTableWidget::item:hover, QTreeWidget::item:hover, QListWidget::item:hover {{
        background: #1A2232;
    }}
    QTableWidget::item:selected, QTreeWidget::item:selected, QListWidget::item:selected {{
        background: {ACCENT};
        color: white;
    }}
    QHeaderView::section {{
        min-height: 32px;
        padding: 0 9px;
        background: #182031;
        color: {TEXT_MUTED};
        border: none;
        border-bottom: 1px solid {BORDER};
        font-weight: 650;
    }}

    QLabel[status="success"], QLabel#statusSuccess {{
        background: rgba(67, 209, 158, 25);
        color: #8DF0C2;
        border: 1px solid rgba(67, 209, 158, 130);
        border-radius: 9px;
        padding: 5px 9px;
        font-weight: 650;
    }}
    QLabel[status="warning"], QLabel#statusWarning, QLabel#quotaWait {{
        background: rgba(245, 185, 70, 24);
        color: #FFD887;
        border: 1px solid rgba(245, 185, 70, 130);
        border-radius: 9px;
        padding: 5px 9px;
        font-weight: 650;
    }}
    QLabel[status="error"], QLabel#statusError {{
        background: rgba(242, 109, 125, 24);
        color: #FFB7C0;
        border: 1px solid rgba(242, 109, 125, 130);
        border-radius: 9px;
        padding: 5px 9px;
        font-weight: 650;
    }}
    QLabel[status="info"], QLabel#statusInfo {{
        background: rgba(53, 194, 255, 24);
        color: #AEE9FF;
        border: 1px solid rgba(53, 194, 255, 130);
        border-radius: 9px;
        padding: 5px 9px;
        font-weight: 650;
    }}

    QProgressBar {{
        min-height: 12px;
        max-height: 12px;
        background: #0C111A;
        color: transparent;
        border: 1px solid {BORDER};
        border-radius: 6px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        background: {ACCENT};
        border-radius: 5px;
    }}
    QProgressBar#successProgress::chunk {{ background: {SUCCESS}; }}
    QProgressBar#warningProgress::chunk {{ background: {WARNING}; }}
    QProgressBar#errorProgress::chunk {{ background: {ERROR}; }}

    QMenu {{
        padding: 6px;
        background: {PANEL_ELEVATED};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 10px;
    }}
    QMenu::item {{
        min-height: 28px;
        padding: 5px 28px 5px 10px;
        border-radius: 7px;
    }}
    QMenu::item:selected {{
        background: #26334B;
    }}
    QMenu::item:disabled {{ color: #68748A; }}
    QMenu::separator {{
        height: 1px;
        margin: 5px 8px;
        background: {BORDER};
    }}

    QScrollBar:vertical {{
        width: 12px;
        margin: 4px 2px;
        background: transparent;
    }}
    QScrollBar::handle:vertical {{
        min-height: 28px;
        background: #3A475F;
        border-radius: 5px;
    }}
    QScrollBar::handle:vertical:hover {{ background: #52627E; }}
    QScrollBar:horizontal {{
        height: 12px;
        margin: 2px 4px;
        background: transparent;
    }}
    QScrollBar::handle:horizontal {{
        min-width: 28px;
        background: #3A475F;
        border-radius: 5px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: #52627E; }}
    QScrollBar::add-line, QScrollBar::sub-line {{
        width: 0;
        height: 0;
        background: none;
        border: none;
    }}

    QToolTip {{
        padding: 6px 8px;
        background: #202A3D;
        color: {TEXT};
        border: 1px solid #475670;
        border-radius: 6px;
    }}
    QStatusBar {{
        background: #0E121B;
        color: {TEXT_MUTED};
        border-top: 1px solid {BORDER};
    }}
    QCheckBox, QRadioButton {{
        spacing: 8px;
        color: {TEXT};
    }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 17px;
        height: 17px;
        background: #101621;
        border: 1px solid {BORDER};
        border-radius: 5px;
    }}
    QRadioButton::indicator {{ border-radius: 9px; }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        background: {ACCENT};
        border-color: {ACCENT};
    }}
    """.strip()


__all__ = [
    "ACCENT",
    "ACCENT_HOVER",
    "ACCENT_PRESSED",
    "ACCENT_SECONDARY",
    "BACKGROUND",
    "BASE_FONT_SIZE",
    "BORDER",
    "BUTTON_MIN_HEIGHT",
    "CARD_RADIUS",
    "ERROR",
    "FOCUS_RING",
    "FONT_FAMILY",
    "INFO",
    "PANEL",
    "PANEL_ELEVATED",
    "SUCCESS",
    "TEXT",
    "TEXT_MUTED",
    "WARNING",
    "dark_stylesheet",
]
