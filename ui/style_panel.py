"""
ui/style_panel.py
-----------------
Right-hand styling panel.  Compact two-column grid layout.
Emits styleChanged(CaptionStyle) whenever anything changes.
"""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QColorDialog, QFileDialog, QFontComboBox,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

from core.caption_model import CaptionStyle
from core.font_resolver import resolve_font_family


# ── Colour swatch button ──────────────────────────────────────────────────────

class ColorButton(QPushButton):
    colorChanged = pyqtSignal(str)

    def __init__(self, initial: str = "#FFFFFF", parent=None):
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
        self.setFixedSize(36, 26)
        self.setStyleSheet(
            f"QPushButton {{ background:{self._color}; border:1px solid #666;"
            f" border-radius:3px; }}"
            f"QPushButton:hover {{ border-color:#999; }}"
        )

    def _pick(self) -> None:
        col = QColorDialog.getColor(QColor(self._color), self, "Pick colour")
        if col.isValid():
            self._color = col.name()
            self._refresh()
            self.colorChanged.emit(self._color)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("color:#aaa; font-size:11px;")
    return lbl

def _spin(lo: int, hi: int, val: int, tip: str = "") -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(val)
    if tip:
        s.setToolTip(tip)
    return s


# ── Panel ─────────────────────────────────────────────────────────────────────

class StylePanel(QWidget):

    styleChanged        = pyqtSignal(object)   # CaptionStyle
    resetPositions      = pyqtSignal()
    segmentAlignChanged = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._style = CaptionStyle()
        self._font_path: str | None = None
        self._build_ui()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        root.addWidget(self._build_font_box())
        root.addWidget(self._build_colour_box())
        root.addWidget(self._build_layout_box())
        root.addWidget(self._build_karaoke_box())
        root.addStretch()

    # ── Font group ────────────────────────────────────────────────────────

    def _build_font_box(self) -> QGroupBox:
        box = QGroupBox("Font")
        v   = QVBoxLayout(box)
        v.setSpacing(6)

        # Font picker
        self._font_combo = QFontComboBox()
        self._font_combo.currentFontChanged.connect(self._on_font_combo)
        v.addWidget(self._font_combo)

        # Size + Bold + browse on same row
        row = QHBoxLayout()
        row.addWidget(_label("Size"))
        self._size_spin = _spin(10, 200, self._style.font_size)
        self._size_spin.setFixedWidth(58)
        self._size_spin.valueChanged.connect(self._emit)
        row.addWidget(self._size_spin)

        self._bold_btn = QPushButton("B")
        self._bold_btn.setCheckable(True)
        self._bold_btn.setChecked(True)
        self._bold_btn.setFixedSize(28, 26)
        self._bold_btn.setToolTip("Bold")
        self._bold_btn.setStyleSheet(
            "QPushButton { font-weight:bold; font-size:13px; }"
        )
        self._bold_btn.toggled.connect(self._emit)
        row.addWidget(self._bold_btn)

        row.addStretch()
        browse_btn = QPushButton("Browse font…")
        browse_btn.setToolTip("Load a custom .ttf / .otf file not in the list")
        browse_btn.clicked.connect(self._browse_font)
        row.addWidget(browse_btn)
        v.addLayout(row)

        # Spacing row
        sp = QHBoxLayout()
        sp.addWidget(_label("Letter sp."))
        self._letter_sp_spin = _spin(-10, 50, 0, "Extra space between each character (px)")
        self._letter_sp_spin.setFixedWidth(52)
        self._letter_sp_spin.valueChanged.connect(self._emit)
        sp.addWidget(self._letter_sp_spin)
        sp.addSpacing(8)
        sp.addWidget(_label("Word sp."))
        self._word_sp_spin = _spin(-20, 100, 0, "Extra space between words (px)")
        self._word_sp_spin.setFixedWidth(52)
        self._word_sp_spin.valueChanged.connect(self._emit)
        sp.addWidget(self._word_sp_spin)
        sp.addStretch()
        v.addLayout(sp)

        # Status line (tiny, shows resolved filename)
        self._font_status = QLabel("Using system font")
        self._font_status.setStyleSheet("color:#777; font-size:10px;")
        v.addWidget(self._font_status)

        return box

    # ── Colours group ─────────────────────────────────────────────────────

    def _build_colour_box(self) -> QGroupBox:
        box = QGroupBox("Colours")
        g   = QGridLayout(box)
        g.setSpacing(6)
        g.setColumnStretch(1, 1)

        for row, (lbl, attr, default) in enumerate([
            ("Text",      "_fg_btn", "#FFFFFF"),
            ("Outline",   "_ol_btn", "#000000"),
            ("Highlight", "_hl_btn", "#FFD700"),
        ]):
            g.addWidget(_label(lbl), row, 0)
            btn = ColorButton(default)
            btn.colorChanged.connect(self._emit)
            setattr(self, attr, btn)
            g.addWidget(btn, row, 1, Qt.AlignmentFlag.AlignLeft)

        g.addWidget(_label("Outline width"), 3, 0)
        self._outline_spin = _spin(0, 10, self._style.outline_width)
        self._outline_spin.setFixedWidth(58)
        self._outline_spin.valueChanged.connect(self._emit)
        g.addWidget(self._outline_spin, 3, 1, Qt.AlignmentFlag.AlignLeft)

        return box

    # ── Layout group ──────────────────────────────────────────────────────

    def _build_layout_box(self) -> QGroupBox:
        box = QGroupBox("Layout")
        v   = QVBoxLayout(box)
        v.setSpacing(6)

        # Words/row + Rows on one line
        wr = QHBoxLayout()
        wr.addWidget(_label("Words/row"))
        self._wpl_spin = _spin(0, 20, 0, "Words per row (0 = unlimited)")
        self._wpl_spin.setSpecialValueText("∞")
        self._wpl_spin.setFixedWidth(58)
        self._wpl_spin.valueChanged.connect(self._emit)
        wr.addWidget(self._wpl_spin)
        wr.addSpacing(12)
        wr.addWidget(_label("Rows"))
        self._rv_spin = _spin(1, 6, 1, "Rows visible at once")
        self._rv_spin.setFixedWidth(58)
        self._rv_spin.valueChanged.connect(self._emit)
        wr.addWidget(self._rv_spin)
        wr.addStretch()
        v.addLayout(wr)

        # "All" scope toggle — sits on its own row so it's prominent
        self._all_btn = QPushButton("ALL sentences")
        self._all_btn.setCheckable(True)
        self._all_btn.setChecked(True)   # on by default
        self._all_btn.setFixedHeight(28)
        self._all_btn.setToolTip(
            "ON → position/alignment changes affect ALL sentences\n"
            "OFF → changes affect only the selected sentence"
        )
        self._all_btn.toggled.connect(self._on_all_toggled)
        v.addWidget(self._all_btn)

        # Alignment row
        al = QHBoxLayout()
        al.setSpacing(4)
        al.addWidget(_label("Align"))

        self._align_group = QButtonGroup(self)
        self._align_btns  = {}
        for lbl, val, tip in [
            ("◀  L", "left",   "Text starts at anchor, grows right →"),
            ("≡  C", "center", "Text centred on anchor"),
            ("R  ▶", "right",  "Text ends at anchor, grows left ←"),
        ]:
            btn = QPushButton(lbl)
            btn.setCheckable(True)
            btn.setFixedHeight(26)
            btn.setMinimumWidth(44)
            btn.setToolTip(tip)
            self._align_group.addButton(btn)
            self._align_btns[val] = btn
            al.addWidget(btn)
        self._align_btns["center"].setChecked(True)
        self._align_group.buttonClicked.connect(self._on_align_clicked)
        al.addStretch()
        v.addLayout(al)

        # Reset button
        reset_btn = QPushButton("↺  Reset all positions + alignment")
        reset_btn.setToolTip("Move every sentence to centre, reset alignment to centered")
        reset_btn.clicked.connect(self.resetPositions.emit)
        v.addWidget(reset_btn)

        return box

    # ── Karaoke group ─────────────────────────────────────────────────────

    def _build_karaoke_box(self) -> QGroupBox:
        box = QGroupBox("Karaoke")
        v   = QVBoxLayout(box)
        self._karaoke_chk = QCheckBox("Word-by-word highlight")
        self._karaoke_chk.toggled.connect(self._emit)
        v.addWidget(self._karaoke_chk)
        return box

    # ── Slots ─────────────────────────────────────────────────────────────

    def _on_all_toggled(self, checked: bool) -> None:
        """Visual feedback: highlight the button when active."""
        if checked:
            self._all_btn.setStyleSheet(
                "QPushButton { background:#2a5a2a; border:1px solid #5a5; color:#aea; font-weight:bold; }"
                "QPushButton:hover { background:#336633; }"
            )
        else:
            self._all_btn.setStyleSheet("")   # revert to theme default

    def _on_align_clicked(self, _btn) -> None:
        align = self._current_align()
        if self._all_btn.isChecked():
            # Emit via styleChanged so main_window applies it to all segments
            self._emit()
        else:
            self.segmentAlignChanged.emit(align)

    def _current_align(self) -> str:
        checked = self._align_group.checkedButton()
        return next((v for v, b in self._align_btns.items() if b is checked), "center")

    def _on_font_combo(self, font: QFont) -> None:
        path = resolve_font_family(font.family())
        self._font_path = path
        self._style.font_path = path
        if path:
            self._font_status.setText(f"✓  {os.path.basename(path)}")
            self._font_status.setStyleSheet("color:#6b6; font-size:10px;")
            self._font_status.setToolTip(path)
        else:
            self._font_status.setText("⚠  File not found — export uses ffmpeg default")
            self._font_status.setStyleSheet("color:#a66; font-size:10px;")
            self._font_status.setToolTip("")
        self._emit()

    def _browse_font(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Font File", "", "Font files (*.ttf *.otf)"
        )
        if path:
            self._font_path = path
            self._style.font_path = path
            self._font_status.setText(f"✓  {os.path.basename(path)}")
            self._font_status.setStyleSheet("color:#6b6; font-size:10px;")
            self._font_status.setToolTip(path)
            self._emit()

    def _emit(self, *_) -> None:
        self._style.font_size      = self._size_spin.value()
        self._style.color          = self._fg_btn.color()
        self._style.outline_color  = self._ol_btn.color()
        self._style.highlight_color = self._hl_btn.color()
        self._style.outline_width  = self._outline_spin.value()
        self._style.words_per_line = self._wpl_spin.value()
        self._style.rows_visible   = self._rv_spin.value()
        self._style.karaoke        = self._karaoke_chk.isChecked()
        self._style.text_align     = self._current_align()
        self._style.font_path      = self._font_path
        self._style.bold           = self._bold_btn.isChecked()
        self._style.letter_spacing = self._letter_sp_spin.value()
        self._style.word_spacing   = self._word_sp_spin.value()
        self.styleChanged.emit(self._style)

    # ── Public ────────────────────────────────────────────────────────────

    def current_style(self) -> CaptionStyle:
        self._emit()
        return self._style

    def scope_is_all(self) -> bool:
        return self._all_btn.isChecked()

    def reset_align_to_center(self) -> None:
        """Reset the alignment buttons to 'center' without emitting a signal."""
        self._align_btns["center"].setChecked(True)

    def apply_position(self, nx: float, ny: float) -> None:
        self._style.position = (nx, ny)
