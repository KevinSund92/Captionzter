"""
ui/caption_canvas.py
--------------------
QGraphicsObject that renders draggable captions inside a QGraphicsScene.

Alignment anchor-point model
-----------------------------
The item's scene position (x, y) is the ANCHOR POINT.
text_align controls which edge of the text sits at the anchor:
  left   → text starts at anchor, grows right
  center → text is centred on the anchor
  right  → text ends at anchor, grows left

This makes the drag handle the single reference point regardless of alignment,
and a thin guide line through the handle makes the anchor visually clear.
"""

from __future__ import annotations

import math
from typing import List, Optional

from PyQt6.QtCore import Qt, QRectF, QSizeF, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QFontDatabase, QFontMetrics,
    QPainter, QPen, QBrush,
)
from PyQt6.QtWidgets import QGraphicsObject, QStyleOptionGraphicsItem, QWidget

from core.caption_model import CaptionStyle, WordToken

# Animation durations in seconds
_ANIM_POP_TOTAL   = 0.30   # 0–0.15 s scale up, 0.15–0.30 s settle
_ANIM_SLIDE_DUR   = 0.30
_ANIM_SHAKE_DUR   = 0.50


class CaptionCanvas(QGraphicsObject):
    positionChanged = pyqtSignal(float, float)

    def __init__(self) -> None:
        super().__init__()
        self.setFlag(self.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(self.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)

        self._style:          CaptionStyle           = CaptionStyle()
        self._scene_size:     QSizeF                 = QSizeF(1280, 720)
        self._lines:          List[str]              = []
        self._tokens:         List[List[WordToken]]  = []
        self._current_time:   float                  = 0.0
        self._align_override: Optional[str]          = None
        self._prog_move:      bool                   = False
        self._anim_type:      str                    = "none"
        self._seg_start:      float                  = 0.0

        # Timer for smooth animation repaints when player is paused/slow
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(16)   # ~60 fps
        self._anim_timer.timeout.connect(self.update)

        self._apply_style_pos()

    # ── Public API ────────────────────────────────────────────────────────

    def set_scene_size(self, size: QSizeF) -> None:
        self._scene_size = size
        self._apply_style_pos()

    def set_style(self, style: CaptionStyle) -> None:
        self._style = style
        # Do NOT call _apply_style_pos here — per-segment positions are managed
        # externally by main_window. Only set_scene_size repositions the canvas.
        self.prepareGeometryChange()
        self.update()

    def set_align_override(self, align: Optional[str]) -> None:
        self._align_override = align
        self.prepareGeometryChange()
        self.update()

    def set_animation(self, anim_type: str, seg_start: float) -> None:
        """Set the animation type and the segment start time for elapsed calculation."""
        self._anim_type  = anim_type or "none"
        self._seg_start  = seg_start
        # Start fast-repaint timer so animation plays smoothly even when paused
        anim_dur = {"pop": _ANIM_POP_TOTAL, "slide_in": _ANIM_SLIDE_DUR,
                    "shake": _ANIM_SHAKE_DUR}.get(self._anim_type, 0)
        if anim_dur > 0:
            self._anim_timer.start()
        else:
            self._anim_timer.stop()

    def set_display(
        self,
        lines: List[str],
        tokens_per_line: List[List[WordToken]],
        time_s: float,
    ) -> None:
        self._lines        = lines
        self._tokens       = tokens_per_line
        self._current_time = time_s
        # Stop animation timer once the animation window has passed
        if self._anim_timer.isActive():
            anim_dur = {"pop": _ANIM_POP_TOTAL, "slide_in": _ANIM_SLIDE_DUR,
                        "shake": _ANIM_SHAKE_DUR}.get(self._anim_type, 0)
            if time_s - self._seg_start > anim_dur:
                self._anim_timer.stop()
        self.prepareGeometryChange()
        self.update()

    def set_position_override(self, nx: float, ny: float) -> None:
        """Move to a specific (nx, ny) — does not emit positionChanged."""
        self._prog_move = True
        self.setPos(nx * self._scene_size.width(), ny * self._scene_size.height())
        self._prog_move = False

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
        bold = getattr(self._style, "bold", True)
        if self._style.font_path:
            fid = QFontDatabase.addApplicationFont(self._style.font_path)
            families = QFontDatabase.applicationFontFamilies(fid)
            if families:
                f = QFont(families[0], self._style.font_size)
                f.setBold(bold)
                return f
        f = QFont("Arial", self._style.font_size)
        f.setBold(bold)
        return f

    def _effective_align(self) -> str:
        return self._align_override or self._style.text_align or "center"

    def _x_start(self, tw: float, align: str) -> float:
        """Return the x offset of the LEFT edge of a line of width tw."""
        if align == "left":
            return 0.0          # line starts at anchor
        if align == "right":
            return -tw          # line ends at anchor
        return -tw / 2          # line centred on anchor

    # ── Qt interface ──────────────────────────────────────────────────────

    def boundingRect(self) -> QRectF:
        font    = self._build_font()
        fm      = QFontMetrics(font)
        lines   = self._lines or ["Wg"]
        align   = self._effective_align()
        pad     = self._style.outline_width + 8
        total_h = fm.height() * len(lines)

        lsp = getattr(self._style, "letter_spacing", 0)
        wsp = getattr(self._style, "word_spacing", 0)
        widths = [self._measure_line(fm, l, lsp, wsp) for l in lines]
        max_w  = max(widths)

        if align == "left":
            left = -pad
            rect_w = max_w + pad * 2
        elif align == "right":
            left = -max_w - pad
            rect_w = max_w + pad * 2
        else:
            left = -max_w / 2 - pad
            rect_w = max_w + pad * 2

        return QRectF(left, -total_h / 2 - pad - 18, rect_w, total_h + pad * 2 + 18)

    def _apply_anim(self, painter: QPainter, total_h: float) -> None:
        """Push an animation transform onto the painter for the current elapsed time."""
        elapsed = max(0.0, self._current_time - self._seg_start)
        anim    = self._anim_type

        if anim == "pop":
            if elapsed >= _ANIM_POP_TOTAL:
                return
            half = _ANIM_POP_TOTAL / 2
            if elapsed < half:
                s = elapsed / half * 1.1          # 0 → 1.1
            else:
                s = 1.1 - (elapsed - half) / half * 0.1  # 1.1 → 1.0
            painter.translate(0, 0)
            painter.scale(s, s)

        elif anim == "slide_in":
            if elapsed >= _ANIM_SLIDE_DUR:
                return
            progress = elapsed / _ANIM_SLIDE_DUR
            # ease-out: fast start, slows down
            ease     = 1.0 - (1.0 - progress) ** 2
            offset_y = (1.0 - ease) * (total_h + 20)
            painter.translate(0, offset_y)

        elif anim == "shake":
            if elapsed >= _ANIM_SHAKE_DUR:
                return
            # decaying sine wave
            decay  = math.exp(-elapsed * 9)
            offset_x = math.sin(elapsed * 55) * 12 * decay
            painter.translate(offset_x, 0)

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
        align   = self._effective_align()
        lsp     = getattr(self._style, "letter_spacing", 0)
        wsp     = getattr(self._style, "word_spacing", 0)

        # ── Alignment guide line (drawn before anim transform) ───────────
        guide_pen = QPen(QColor(100, 180, 255, 120), 1.5, Qt.PenStyle.DashLine)
        guide_pen.setDashPattern([4, 4])
        painter.setPen(guide_pen)
        painter.drawLine(0, int(-total_h / 2) - 4, 0, int(total_h / 2) + 4)

        # ── Apply animation transform ─────────────────────────────────────
        painter.save()
        self._apply_anim(painter, total_h)

        # ── Text lines ────────────────────────────────────────────────────
        for line_idx, line in enumerate(self._lines):
            y = -total_h / 2 + line_idx * th + th - fm.descent()

            tokens = (self._tokens[line_idx]
                      if karaoke and line_idx < len(self._tokens) and self._tokens[line_idx]
                      else None)

            line_text = " ".join(t.word.strip() for t in tokens) if tokens else line
            # Measure width respecting letter + word spacing
            tw = self._measure_line(fm, line_text, lsp, wsp)
            x_start = self._x_start(tw, align)

            if tokens:
                x = x_start
                for t_idx, token in enumerate(tokens):
                    word   = token.word.strip()
                    word_w = self._measure_word(fm, word, lsp)
                    gap    = fm.horizontalAdvance(" ") + wsp if t_idx < len(tokens) - 1 else 0
                    color  = hl if token.start <= self._current_time < token.end else fg
                    self._draw_word_spaced(painter, word, int(x), int(y), color, ol, olw, lsp)
                    x += word_w + gap
            else:
                # Always draw word-by-word so lsp is never added across spaces
                x = x_start
                words = line.split(" ")
                for w_idx, word in enumerate(words):
                    self._draw_word_spaced(painter, word, int(x), int(y), fg, ol, olw, lsp)
                    word_w = self._measure_word(fm, word, lsp)
                    gap    = fm.horizontalAdvance(" ") + wsp if w_idx < len(words) - 1 else 0
                    x += word_w + gap

        painter.restore()   # end animation transform

        # ── Drag handle ───────────────────────────────────────────────────
        handle_y = -total_h / 2 - 14
        painter.setBrush(QBrush(QColor(80, 160, 255, 200)))
        painter.setPen(QPen(QColor(255, 255, 255, 220), 1.5))
        painter.drawEllipse(QRectF(-6, handle_y - 6, 12, 12))
        # Small arrow showing alignment direction
        painter.setPen(QPen(QColor(255, 255, 255, 180), 1.5))
        if align == "left":
            painter.drawLine(0, int(handle_y), 14, int(handle_y))
            painter.drawLine(10, int(handle_y) - 4, 14, int(handle_y))
            painter.drawLine(10, int(handle_y) + 4, 14, int(handle_y))
        elif align == "right":
            painter.drawLine(0, int(handle_y), -14, int(handle_y))
            painter.drawLine(-10, int(handle_y) - 4, -14, int(handle_y))
            painter.drawLine(-10, int(handle_y) + 4, -14, int(handle_y))
        else:
            painter.drawLine(-10, int(handle_y), 10, int(handle_y))

    def _measure_word(self, fm, word: str, lsp: int) -> int:
        """Width of word with per-character letter spacing applied."""
        if lsp == 0:
            return fm.horizontalAdvance(word)
        total = 0
        for ch in word:
            total += fm.horizontalAdvance(ch) + lsp
        return max(0, total - lsp)   # no trailing gap on last char

    def _measure_line(self, fm, line: str, lsp: int, wsp: int) -> int:
        words = line.split(" ")
        w = sum(self._measure_word(fm, w, lsp) for w in words)
        gaps = (fm.horizontalAdvance(" ") + wsp) * max(0, len(words) - 1)
        return w + gaps

    def _draw_word_spaced(self, painter, text: str, x: int, y: int,
                          fg, ol, olw: int, lsp: int) -> None:
        """Draw text character-by-character when lsp != 0, else in one call."""
        if lsp == 0:
            self._draw_word(painter, text, x, y, fg, ol, olw)
            return
        fm = painter.fontMetrics()
        cx = x
        for ch in text:
            self._draw_word(painter, ch, cx, y, fg, ol, olw)
            cx += fm.horizontalAdvance(ch) + lsp

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
            if not self._prog_move:
                sw, sh = self._scene_size.width(), self._scene_size.height()
                if sw and sh:
                    nx = max(0.0, min(1.0, self.x() / sw))
                    ny = max(0.0, min(1.0, self.y() / sh))
                    self.positionChanged.emit(nx, ny)
        return super().itemChange(change, value)

    def hoverEnterEvent(self, event) -> None:
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def hoverLeaveEvent(self, event) -> None:
        self.setCursor(Qt.CursorShape.ArrowCursor)
