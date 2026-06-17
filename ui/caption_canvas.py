"""
ui/caption_canvas.py
--------------------
QGraphicsObject that renders draggable captions inside a QGraphicsScene.
Receives pre-computed display lines from main_window (no wrapping here).
"""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt, QRectF, QSizeF, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QFontDatabase, QFontMetrics,
    QPainter, QPen, QBrush,
)
from PyQt6.QtWidgets import QGraphicsObject, QStyleOptionGraphicsItem, QWidget

from core.caption_model import CaptionStyle, WordToken


class CaptionCanvas(QGraphicsObject):
    positionChanged = pyqtSignal(float, float)

    def __init__(self) -> None:
        super().__init__()
        self.setFlag(self.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(self.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)

        self._style:         CaptionStyle           = CaptionStyle()
        self._scene_size:    QSizeF                 = QSizeF(1280, 720)
        self._lines:         List[str]              = []
        self._tokens:        List[List[WordToken]]  = []
        self._current_time:  float                  = 0.0

        self._apply_style_pos()

    # ── Public API ────────────────────────────────────────────────────────

    def set_scene_size(self, size: QSizeF) -> None:
        self._scene_size = size
        self._apply_style_pos()

    def set_style(self, style: CaptionStyle) -> None:
        self._style = style
        self._apply_style_pos()
        self.prepareGeometryChange()
        self.update()

    def set_display(
        self,
        lines: List[str],
        tokens_per_line: List[List[WordToken]],
        time_s: float,
    ) -> None:
        """Set the exact lines to show (already split into rows by the caller)."""
        self._lines        = lines
        self._tokens       = tokens_per_line
        self._current_time = time_s
        self.prepareGeometryChange()
        self.update()

    # kept for compatibility with _load_video "Caption preview"
    def set_preview_text(self, text: str) -> None:
        self._lines  = [text] if text else []
        self._tokens = [[]]
        self.prepareGeometryChange()
        self.update()

    # ── Helpers ───────────────────────────────────────────────────────────

    def _apply_style_pos(self) -> None:
        nx, ny = self._style.position
        self.setPos(nx * self._scene_size.width(), ny * self._scene_size.height())

    def _build_font(self) -> QFont:
        if self._style.font_path:
            fid = QFontDatabase.addApplicationFont(self._style.font_path)
            families = QFontDatabase.applicationFontFamilies(fid)
            if families:
                f = QFont(families[0], self._style.font_size)
                f.setBold(True)
                return f
        f = QFont("Arial", self._style.font_size)
        f.setBold(True)
        return f

    # ── Qt interface ──────────────────────────────────────────────────────

    def boundingRect(self) -> QRectF:
        font  = self._build_font()
        fm    = QFontMetrics(font)
        lines = self._lines or ["Wg"]
        max_w   = max(fm.horizontalAdvance(l) for l in lines)
        total_h = fm.height() * len(lines)
        pad     = self._style.outline_width + 6
        return QRectF(-max_w / 2 - pad, -total_h / 2 - pad,
                      max_w + pad * 2, total_h + pad * 2)

    def paint(self, painter: QPainter,
              option: QStyleOptionGraphicsItem,
              widget: Optional[QWidget] = None) -> None:
        if not self._lines:
            return

        font = self._build_font()
        painter.setFont(font)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        fm      = painter.fontMetrics()
        th      = fm.height()
        total_h = th * len(self._lines)
        fg      = QColor(self._style.color)
        ol      = QColor(self._style.outline_color)
        hl      = QColor(self._style.highlight_color)
        olw     = self._style.outline_width
        karaoke = self._style.karaoke

        for line_idx, line in enumerate(self._lines):
            y = -total_h / 2 + line_idx * th + th - fm.descent()

            tokens = (self._tokens[line_idx]
                      if karaoke and line_idx < len(self._tokens) and self._tokens[line_idx]
                      else None)

            if tokens:
                # Karaoke: render word by word
                line_text = " ".join(t.word.strip() for t in tokens)
                tw        = fm.horizontalAdvance(line_text)
                x         = -tw / 2
                for t_idx, token in enumerate(tokens):
                    word   = token.word.strip()
                    word_w = fm.horizontalAdvance(word)
                    gap    = fm.horizontalAdvance(" ") if t_idx < len(tokens) - 1 else 0
                    color  = hl if token.start <= self._current_time < token.end else fg
                    self._draw_word(painter, word, int(x), int(y), color, ol, olw)
                    x += word_w + gap
            else:
                tw = fm.horizontalAdvance(line)
                x  = -tw / 2
                self._draw_word(painter, line, int(x), int(y), fg, ol, olw)

        # Drag handle dot
        painter.setBrush(QBrush(QColor(80, 160, 255, 180)))
        painter.setPen(QPen(QColor(255, 255, 255, 200), 1.0))
        painter.drawEllipse(QRectF(-5, -total_h / 2 - 14, 10, 10))

    def _draw_word(self, painter, text, x, y, fg, ol, olw):
        if olw:
            painter.setPen(QPen(ol))
            for dx in range(-olw, olw + 1):
                for dy in range(-olw, olw + 1):
                    if dx or dy:
                        painter.drawText(x + dx, y + dy, text)
        painter.setPen(QPen(fg))
        painter.drawText(x, y, text)

    # ── Drag ─────────────────────────────────────────────────────────────

    def itemChange(self, change, value):
        if change == self.GraphicsItemChange.ItemPositionHasChanged:
            sw, sh = self._scene_size.width(), self._scene_size.height()
            if sw and sh:
                nx = max(0.0, min(1.0, self.x() / sw))
                ny = max(0.0, min(1.0, self.y() / sh))
                self._style.position = (nx, ny)
                self.positionChanged.emit(nx, ny)
        return super().itemChange(change, value)

    def hoverEnterEvent(self, event) -> None:
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def hoverLeaveEvent(self, event) -> None:
        self.setCursor(Qt.CursorShape.ArrowCursor)
