"""
ui/style_panel.py
-----------------
Right-hand panel: all caption styling controls.
Emits styleChanged(CaptionStyle) whenever the user adjusts anything.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox, QColorDialog, QFileDialog, QFontComboBox,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSlider, QSpinBox, QVBoxLayout, QWidget,
)
from PyQt6.QtCore import Qt

from core.caption_model import CaptionStyle
from core.font_resolver import resolve_font_family


class ColorButton(QPushButton):
    """A button that shows a colour swatch and opens a QColorDialog."""

    colorChanged = pyqtSignal(str)   # hex string

    def __init__(self, initial: str = "#FFFFFF", parent: QWidget | None = None):
        super().__init__(parent)
        self._color = initial
        self._refresh()
        self.clicked.connect(self._pick)

    def color(self) -> str:
        return self._color

    def set_color(self, hex_color: str) -> None:
        self._color = hex_color
        self._refresh()

    def _refresh(self) -> None:
        self.setFixedSize(40, 28)
        self.setStyleSheet(
            f"background:{self._color}; border:1px solid #888; border-radius:4px;"
        )

    def _pick(self) -> None:
        col = QColorDialog.getColor(QColor(self._color), self, "Pick colour")
        if col.isValid():
            self._color = col.name()
            self._refresh()
            self.colorChanged.emit(self._color)


class StylePanel(QWidget):
    """
    Emits styleChanged whenever any control changes.
    """

    styleChanged = pyqtSignal(object)   # CaptionStyle

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._style = CaptionStyle()
        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(12)

        # ── Font ──────────────────────────────────────────────────────────
        font_box = QGroupBox("Font")
        fl = QVBoxLayout(font_box)

        self._font_combo = QFontComboBox()
        self._font_combo.currentFontChanged.connect(self._on_font_combo)
        fl.addWidget(self._font_combo)

        # Resolved path row — shows where the font file was found
        path_row = QHBoxLayout()
        self._font_path_edit = QLineEdit()
        self._font_path_edit.setPlaceholderText("Resolved font path …")
        self._font_path_edit.setReadOnly(True)
        path_row.addWidget(self._font_path_edit)
        browse_btn = QPushButton("Browse…")
        browse_btn.setFixedWidth(72)
        browse_btn.setToolTip("Load a custom .ttf / .otf file")
        browse_btn.clicked.connect(self._browse_font)
        path_row.addWidget(browse_btn)
        fl.addLayout(path_row)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Size"))
        self._size_spin = QSpinBox()
        self._size_spin.setRange(10, 200)
        self._size_spin.setValue(self._style.font_size)
        self._size_spin.valueChanged.connect(self._emit)
        size_row.addWidget(self._size_spin)
        fl.addLayout(size_row)
        root.addWidget(font_box)

        # ── Colour ────────────────────────────────────────────────────────
        col_box = QGroupBox("Colours")
        cl = QVBoxLayout(col_box)

        for label, attr, default in [
            ("Text",      "_fg_btn",  "#FFFFFF"),
            ("Outline",   "_ol_btn",  "#000000"),
            ("Highlight", "_hl_btn",  "#FFD700"),
        ]:
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            btn = ColorButton(default)
            btn.colorChanged.connect(self._emit)
            setattr(self, attr, btn)
            row.addWidget(btn)
            row.addStretch()
            cl.addLayout(row)

        ol_row = QHBoxLayout()
        ol_row.addWidget(QLabel("Outline width"))
        self._outline_spin = QSpinBox()
        self._outline_spin.setRange(0, 10)
        self._outline_spin.setValue(self._style.outline_width)
        self._outline_spin.valueChanged.connect(self._emit)
        ol_row.addWidget(self._outline_spin)
        cl.addLayout(ol_row)
        root.addWidget(col_box)

        # ── Layout ────────────────────────────────────────────────────────
        layout_box = QGroupBox("Layout")
        ll2 = QVBoxLayout(layout_box)

        wpl_row = QHBoxLayout()
        wpl_row.addWidget(QLabel("Words per row"))
        self._wpl_spin = QSpinBox()
        self._wpl_spin.setRange(0, 20)
        self._wpl_spin.setValue(0)
        self._wpl_spin.setSpecialValueText("unlimited")
        self._wpl_spin.setToolTip("Words per row (0 = no limit)")
        self._wpl_spin.valueChanged.connect(self._emit)
        wpl_row.addWidget(self._wpl_spin)
        ll2.addLayout(wpl_row)

        rv_row = QHBoxLayout()
        rv_row.addWidget(QLabel("Rows visible"))
        self._rv_spin = QSpinBox()
        self._rv_spin.setRange(1, 6)
        self._rv_spin.setValue(1)
        self._rv_spin.setToolTip("How many rows are visible at once")
        self._rv_spin.valueChanged.connect(self._emit)
        rv_row.addWidget(self._rv_spin)
        ll2.addLayout(rv_row)

        root.addWidget(layout_box)

        # ── Karaoke ───────────────────────────────────────────────────────
        kar_box = QGroupBox("Karaoke Mode")
        kl = QVBoxLayout(kar_box)
        self._karaoke_chk = QCheckBox("Word-by-word highlight")
        self._karaoke_chk.toggled.connect(self._emit)
        kl.addWidget(self._karaoke_chk)
        root.addWidget(kar_box)

        root.addStretch()

    # ── Slots ─────────────────────────────────────────────────────────────

    def _on_font_combo(self, font: QFont) -> None:
        family = font.family()
        path = resolve_font_family(family)
        self._style.font_path = path
        self._font_path_edit.setText(path or "")
        self._font_path_edit.setToolTip(path or "Font file not found — export will use ffmpeg default")
        self._emit()

    def _browse_font(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Font File", "", "Font files (*.ttf *.otf *.woff)"
        )
        if path:
            self._font_path_edit.setText(path)
            self._style.font_path = path
            self._emit()

    def _emit(self, *_) -> None:
        self._style.font_size       = self._size_spin.value()
        self._style.color           = self._fg_btn.color()
        self._style.outline_color   = self._ol_btn.color()
        self._style.highlight_color = self._hl_btn.color()
        self._style.outline_width   = self._outline_spin.value()
        self._style.words_per_line  = self._wpl_spin.value()
        self._style.rows_visible    = self._rv_spin.value()
        self._style.karaoke         = self._karaoke_chk.isChecked()
        if not self._style.font_path:
            self._style.font_path = None
        self.styleChanged.emit(self._style)

    # ── Public ────────────────────────────────────────────────────────────

    def current_style(self) -> CaptionStyle:
        self._emit()
        return self._style

    def apply_position(self, nx: float, ny: float) -> None:
        """Called by canvas when user drags the caption handle."""
        self._style.position = (nx, ny)
        # Don't call _emit here to avoid feedback loop — just update internal state
