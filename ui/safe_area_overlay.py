"""
ui/safe_area_overlay.py
-----------------------
QGraphicsObject that draws semi-transparent safe-area overlays in the scene.
Lives above the video item but below caption items (controlled by z-values).
Mouse events are ignored so caption dragging works through it.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QRectF, QSizeF
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QGraphicsObject, QStyleOptionGraphicsItem, QWidget

from core.safe_area_config import PLATFORMS


_FILL   = QColor(210, 40, 40, 55)   # translucent red fill (~22 % opacity)
_DASH   = QColor(210, 40, 40, 200)  # dashed border
_LABEL  = QColor(255, 255, 255, 210)


class SafeAreaOverlay(QGraphicsObject):
    """Renders unsafe-area rectangles for a chosen social-media platform."""

    def __init__(self) -> None:
        super().__init__()
        self._platform:   str    = next(iter(PLATFORMS))
        self._scene_size: QSizeF = QSizeF(1080, 1920)
        self._active:     bool   = False

        self.setZValue(1)           # above video (0), below captions (2)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(False)

    # ── Public API ────────────────────────────────────────────────────────

    def set_scene_size(self, size: QSizeF) -> None:
        self._scene_size = size
        self.prepareGeometryChange()
        self.update()

    def set_platform(self, platform: str) -> None:
        if platform in PLATFORMS:
            self._platform = platform
            self.update()

    def set_active(self, active: bool) -> None:
        self._active = active
        self.update()

    def is_active(self) -> bool:
        return self._active

    # ── QGraphicsItem ─────────────────────────────────────────────────────

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self._scene_size.width(), self._scene_size.height())

    def paint(self, painter: QPainter,
              option: QStyleOptionGraphicsItem,
              widget: QWidget | None = None) -> None:
        if not self._active:
            return

        cfg = PLATFORMS.get(self._platform, {})
        W   = self._scene_size.width()
        H   = self._scene_size.height()

        dash_pen = QPen(_DASH, max(2.0, W * 0.003), Qt.PenStyle.DashLine)
        dash_pen.setDashPattern([6, 4])

        unsafe_rects: list[QRectF] = []

        top    = cfg.get("top",    0.0)
        bottom = cfg.get("bottom", 0.0)
        right  = cfg.get("right",  0.0)
        left   = cfg.get("left",   0.0)

        if top    > 0: unsafe_rects.append(QRectF(0,           0,           W,         H * top))
        if bottom > 0: unsafe_rects.append(QRectF(0,           H*(1-bottom),W,         H * bottom))
        if right  > 0: unsafe_rects.append(QRectF(W*(1-right), 0,           W * right, H))
        if left   > 0: unsafe_rects.append(QRectF(0,           0,           W * left,  H))

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        for rect in unsafe_rects:
            painter.setBrush(QBrush(_FILL))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(rect)

            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(dash_pen)
            painter.drawRect(rect)

        # Small label in the safe zone
        safe_top    = H * top + H * 0.01
        safe_left   = W * left + W * 0.02
        label_font  = QFont("Arial", max(10, int(H * 0.018)))
        label_font.setBold(True)
        painter.setFont(label_font)
        painter.setPen(QPen(_LABEL))
        painter.drawText(
            QRectF(safe_left, safe_top, W * 0.5, H * 0.04),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"Safe Area: {self._platform}",
        )
