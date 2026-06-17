"""
ui/timeline_widget.py
---------------------
Horizontal scrollable timeline showing ONE chip per caption segment.
Block boundaries are drawn as subtle tick marks inside each chip.
"""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics,
    QPainter, QPen, QPolygon,
)
from PyQt6.QtWidgets import QScrollBar, QSizePolicy, QWidget

from core.caption_model import CaptionSegment, CaptionStyle, get_caption_blocks


# ── Geometry ──────────────────────────────────────────────────────────────────

_RULER_H  = 22   # time ruler
_TRACK_H  = 64   # chip row
_TOTAL_H  = _RULER_H + _TRACK_H + 16   # +16 scrollbar


# ── Colours ───────────────────────────────────────────────────────────────────

_PALETTE = [
    QColor( 52, 110, 180),
    QColor( 42, 140,  75),
    QColor(140,  70, 160),
    QColor(180, 120,  30),
    QColor(160,  55,  55),
    QColor( 28, 140, 150),
]
_RULER_BG     = QColor(25,  25,  25)
_TRACK_BG     = QColor(18,  18,  18)
_RULER_TICK   = QColor(80,  80,  80)
_RULER_TEXT   = QColor(140, 140, 140)
_GRID_LINE    = QColor(38,  38,  38)
_CHIP_TEXT    = QColor(230, 230, 230)
_SEL_BORDER   = QColor(255, 205,  50)
_PLAYHEAD_COL = QColor(255,  60,  60)


def _nice_step(approx: float) -> float:
    for s in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600):
        if s >= approx:
            return s
    return 600.0


def _fmt(t: float) -> str:
    s = int(t); m = s // 60; s %= 60
    return f"{m}:{s:02d}"


# ── Widget ────────────────────────────────────────────────────────────────────

class TimelineWidget(QWidget):

    seekRequested        = pyqtSignal(float)
    segmentDoubleClicked = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._segments:   List[CaptionSegment] = []
        self._style:      CaptionStyle         = CaptionStyle()
        self._duration:   float                = 0.0
        self._position:   float                = 0.0
        self._selected:   Optional[int]        = None
        self._px_per_sec: float                = 80.0
        self._scroll_x:   int                  = 0
        self._dragging:   bool                 = False

        self.setFixedHeight(_TOTAL_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)

        self._scrollbar = QScrollBar(Qt.Orientation.Horizontal, self)
        self._scrollbar.setFixedHeight(16)
        self._scrollbar.valueChanged.connect(self._on_scroll)

    # ── Geometry ──────────────────────────────────────────────────────────

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._scrollbar.setGeometry(0, self.height() - 16, self.width(), 16)
        self._recalc_scale()
        self._update_scrollbar()

    def _content_width(self) -> int:
        return max(self.width(), int(self._duration * self._px_per_sec) + 60)

    def _recalc_scale(self) -> None:
        if self._duration > 0 and self.width() > 0:
            self._px_per_sec = max(20.0, (self.width() * 0.85) / self._duration)

    def _update_scrollbar(self) -> None:
        cw, vw = self._content_width(), self.width()
        if cw <= vw:
            self._scrollbar.setRange(0, 0)
        else:
            self._scrollbar.setRange(0, cw - vw)
            self._scrollbar.setPageStep(vw)
        self._scrollbar.setSingleStep(40)

    def _on_scroll(self, val: int) -> None:
        self._scroll_x = val
        self.update()

    # ── Public API ────────────────────────────────────────────────────────

    def set_duration(self, s: float) -> None:
        self._duration = max(0.0, s)
        self._recalc_scale()
        self._update_scrollbar()
        self.update()

    def set_position(self, s: float) -> None:
        self._position = s
        self._auto_scroll()
        self.update()

    def set_segments(self, segs: List[CaptionSegment]) -> None:
        self._segments = list(segs)
        self.update()

    def set_style(self, style: CaptionStyle) -> None:
        self._style = style
        self.update()

    def set_selected(self, idx: Optional[int]) -> None:
        self._selected = idx
        self.update()

    # ── Auto-scroll ───────────────────────────────────────────────────────

    def _auto_scroll(self) -> None:
        px  = int(self._position * self._px_per_sec)
        vw  = self.width()
        off = self._scroll_x
        if px - off > vw - 80:
            self._scrollbar.setValue(max(0, px - vw // 2))
        elif px - off < 60 and off > 0:
            self._scrollbar.setValue(max(0, px - 60))

    # ── Paint ─────────────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height() - 16

        painter.fillRect(0, 0, w, _RULER_H, _RULER_BG)
        painter.fillRect(0, _RULER_H, w, h - _RULER_H, _TRACK_BG)

        self._paint_ruler(painter, w, h)
        self._paint_chips(painter, h)
        self._paint_playhead(painter, w, h)
        painter.end()

    def _paint_ruler(self, p: QPainter, w: int, h: int) -> None:
        if self._duration <= 0:
            return
        step = _nice_step(80.0 / self._px_per_sec)
        font = QFont("Arial", 8)
        p.setFont(font)
        fm = QFontMetrics(font)
        t  = 0.0
        while t <= self._duration + step * 0.5:
            x = int(t * self._px_per_sec) - self._scroll_x
            if 0 <= x <= w:
                p.setPen(QPen(_GRID_LINE, 1))
                p.drawLine(x, _RULER_H, x, h)
                p.setPen(QPen(_RULER_TICK, 1))
                p.drawLine(x, _RULER_H - 6, x, _RULER_H)
                label = _fmt(t)
                lw = fm.horizontalAdvance(label)
                p.setPen(QPen(_RULER_TEXT))
                p.drawText(x - lw // 2, _RULER_H - 8, label)
            t = round(t + step, 6)

    def _paint_chips(self, p: QPainter, h: int) -> None:
        if not self._segments:
            return

        font = QFont("Arial", 9)
        font.setBold(True)
        p.setFont(font)
        fm = QFontMetrics(font)

        chip_top = _RULER_H + 4
        chip_h   = _TRACK_H - 8

        for seg_idx, seg in enumerate(self._segments):
            color = _PALETTE[seg_idx % len(_PALETTE)]

            seg_x  = int(seg.start * self._px_per_sec) - self._scroll_x
            seg_w  = max(6, int((seg.end - seg.start) * self._px_per_sec) - 2)

            if seg_x + seg_w < 0 or seg_x > self.width():
                continue

            rect = QRect(seg_x, chip_top, seg_w, chip_h)

            # Fill
            p.setBrush(QBrush(color))
            border_pen = QPen(_SEL_BORDER, 2) if seg_idx == self._selected \
                         else QPen(QColor(0, 0, 0, 60), 1)
            p.setPen(border_pen)
            p.drawRoundedRect(rect, 4, 4)

            # Thin highlight bar at top
            p.fillRect(QRect(seg_x + 2, chip_top + 2, seg_w - 4, 3),
                       color.lighter(160))

            # Block-boundary tick marks inside the chip
            blocks = get_caption_blocks(seg, self._style)
            if len(blocks) > 1:
                tick_pen = QPen(QColor(255, 255, 255, 50), 1)
                p.setPen(tick_pen)
                for b_start, b_end, _, _ in blocks[1:]:   # skip first — that's the left edge
                    tx = int(b_start * self._px_per_sec) - self._scroll_x
                    if seg_x < tx < seg_x + seg_w:
                        p.drawLine(tx, chip_top + 4, tx, chip_top + chip_h - 4)

            # Text: full sentence, clipped to chip
            p.setPen(QPen(_CHIP_TEXT))
            p.setClipRect(rect.adjusted(6, 0, -6, 0))
            label = seg.text.strip()
            p.drawText(seg_x + 6, chip_top + chip_h // 2 + fm.ascent() // 2 - 1, label)
            p.setClipping(False)

    def _paint_playhead(self, p: QPainter, w: int, h: int) -> None:
        px = int(self._position * self._px_per_sec) - self._scroll_x
        if not (0 <= px <= w):
            return
        p.setPen(QPen(_PLAYHEAD_COL, 2))
        p.drawLine(px, 0, px, h)
        p.setBrush(QBrush(_PLAYHEAD_COL))
        p.setPen(Qt.PenStyle.NoPen)
        tri = QPolygon([QPoint(px - 6, 0), QPoint(px + 6, 0), QPoint(px, 12)])
        p.drawPolygon(tri)

    # ── Mouse ─────────────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            t = self._x_to_time(event.position().x())
            self.seekRequested.emit(t)
            idx = self._seg_at(event.position().x(), event.position().y())
            if idx is not None:
                self._selected = idx
                self.segmentDoubleClicked  # don't emit on single click
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._dragging and (event.buttons() & Qt.MouseButton.LeftButton):
            self.seekRequested.emit(self._x_to_time(event.position().x()))
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self._seg_at(event.position().x(), event.position().y())
            if idx is not None:
                self.segmentDoubleClicked.emit(idx)

    def wheelEvent(self, event) -> None:
        self._scrollbar.setValue(
            self._scrollbar.value() - event.angleDelta().y() // 3
        )

    def _x_to_time(self, x: float) -> float:
        return max(0.0, min((x + self._scroll_x) / self._px_per_sec, self._duration))

    def _seg_at(self, mx: float, my: float) -> Optional[int]:
        chip_top = _RULER_H + 4
        chip_h   = _TRACK_H - 8
        if not (chip_top <= my <= chip_top + chip_h):
            return None
        t = (mx + self._scroll_x) / self._px_per_sec
        for i, seg in enumerate(self._segments):
            if seg.start <= t <= seg.end:
                return i
        return None
