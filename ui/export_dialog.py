"""
ui/export_dialog.py
-------------------
Export settings dialog shown before rendering.
Lets the user choose resolution, FPS, and bitrate.
Defaults to "same as source" for all settings.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget,
)


class ExportSettingsDialog(QDialog):
    """
    Returns chosen settings via .resolution(), .fps(), .bitrate().
    All return None if the user chose "Same as source".
    """

    # Common resolution presets (label, w, h)
    _RES_PRESETS = [
        ("Same as source", None),
        ("3840 × 2160  (4K)",     (3840, 2160)),
        ("2560 × 1440  (2K)",     (2560, 1440)),
        ("1920 × 1080  (1080p)",  (1920, 1080)),
        ("1280 × 720   (720p)",   (1280,  720)),
        ("854 × 480    (480p)",   ( 854,  480)),
    ]

    _FPS_PRESETS = [
        ("Same as source", None),
        ("60 fps", 60.0),
        ("30 fps", 30.0),
        ("25 fps", 25.0),
        ("24 fps", 24.0),
    ]

    # Bitrate presets (label, bps)
    _BR_PRESETS = [
        ("Same as source", None),
        ("50 000 kbps  (very high)", 50_000_000),
        ("20 000 kbps  (high)",      20_000_000),
        ("10 000 kbps  (medium)",    10_000_000),
        ("5 000 kbps   (standard)",   5_000_000),
        ("2 000 kbps   (low)",        2_000_000),
    ]

    def __init__(self, src_w: int, src_h: int, src_fps: float, src_bitrate: int,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export Settings")
        self.setMinimumWidth(380)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )

        self._src_w       = src_w
        self._src_h       = src_h
        self._src_fps     = src_fps
        self._src_bitrate = src_bitrate

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(14)

        title = QLabel("Export Settings")
        title.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        layout.addWidget(title)

        # Source info
        fps_str = f"{src_fps:.3f}".rstrip("0").rstrip(".") if src_fps else "?"
        info = QLabel(
            f"Source:  {src_w} × {src_h}  ·  {fps_str} fps  ·  {src_bitrate // 1000} kbps"
        )
        info.setStyleSheet("color:#5b9cf6; font-size:11px;")
        layout.addWidget(info)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Resolution
        self._res_combo = QComboBox()
        for label, val in self._RES_PRESETS:
            self._res_combo.addItem(label, val)
        self._res_combo.setCurrentIndex(0)
        form.addRow("Resolution:", self._res_combo)

        # FPS
        self._fps_combo = QComboBox()
        for label, val in self._FPS_PRESETS:
            self._fps_combo.addItem(label, val)
        self._fps_combo.setCurrentIndex(0)
        form.addRow("Frame rate:", self._fps_combo)

        # Bitrate
        self._br_combo = QComboBox()
        for label, val in self._BR_PRESETS:
            self._br_combo.addItem(label, val)
        self._br_combo.setCurrentIndex(0)
        form.addRow("Bitrate:", self._br_combo)

        layout.addLayout(form)

        # Buttons
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Export")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def resolution(self):
        """Returns (w, h) or None."""
        return self._res_combo.currentData()

    def fps(self):
        """Returns float or None."""
        return self._fps_combo.currentData()

    def bitrate(self):
        """Returns int (bps) or None."""
        return self._br_combo.currentData()
