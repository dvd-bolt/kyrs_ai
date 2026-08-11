from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QBrush
from models.config import FormattingRulesConfig

class A4PreviewWidget(QWidget):
    """
    Нативный виджет пропорционального эскиза страницы А4 с динамической 
    отрисовкой полей, абзацного отступа и образца текста по ГОСТу.
    """
    def __init__(self, config: FormattingRulesConfig = None, parent=None):
        super().__init__(parent)
        self.config = config or FormattingRulesConfig()
        self.setMinimumSize(220, 310)

    def update_config(self, config: FormattingRulesConfig):
        self.config = config
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        
        # 1. Тень под листом А4
        shadow_rect = QRectF(15, 15, w - 30, h - 30)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 100))
        painter.drawRoundedRect(shadow_rect.translated(4, 4), 6, 6)

        # 2. Белый лист бумажного документа
        paper_rect = QRectF(15, 15, w - 30, h - 30)
        painter.setBrush(QColor(248, 249, 250))
        painter.setPen(QPen(QColor(38, 43, 54), 1))
        painter.drawRoundedRect(paper_rect, 4, 4)

        # 3. Расчет динамических полей (в пропорции от размера виджета)
        px_per_cm = (w - 30) / 21.0  # 21 см ширина А4
        
        m_left = 15 + (self.config.margin_left_cm * px_per_cm)
        m_right = (w - 15) - (self.config.margin_right_cm * px_per_cm)
        m_top = 15 + (self.config.margin_top_cm * px_per_cm)
        m_bottom = (h - 15) - (self.config.margin_bottom_cm * px_per_cm)

        margin_rect = QRectF(m_left, m_top, m_right - m_left, m_bottom - m_top)

        # 4. Пунктирная голубая рамка полей по ГОСТу
        pen_margin = QPen(QColor(59, 130, 246, 180), 1, Qt.PenStyle.DashLine)
        painter.setPen(pen_margin)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(margin_rect)

        # 5. Отрисовка имитации текста по ГОСТу
        painter.setPen(QPen(QColor(40, 40, 40), 1))
        font = QFont(self.config.font_name, 8)
        painter.setFont(font)

        line_y = m_top + 15
        indent_px = self.config.paragraph_indent_cm * px_per_cm

        # Рисуем имитационные строки текста
        for i in range(7):
            if line_y + 12 > m_bottom:
                break
            start_x = m_left + indent_px if i in (0, 4) else m_left
            end_x = m_right - (20 if i in (3, 6) else 0)
            
            painter.drawLine(int(start_x), int(line_y), int(end_x), int(line_y))
            line_y += 14 * (self.config.line_spacing / 1.5)

        # 6. Бейдж размера шрифта в углу
        badge_rect = QRectF(w - 75, h - 35, 60, 20)
        painter.setBrush(QColor(30, 32, 36))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(badge_rect, 4, 4)

        painter.setPen(QColor(173, 198, 255))
        badge_font = QFont("JetBrains Mono", 8, QFont.Weight.Bold)
        painter.setFont(badge_font)
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, f"{self.config.font_size_pt}pt {self.config.font_name[:5]}")
