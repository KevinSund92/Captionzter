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
    lbl.setStyleSheet("color:#6b7280; font-size:11px; background:transparent;")
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
    segmentAnimChanged  = pyqtSignal(str)            # animation for selected seg
    positionPreset      = pyqtSignal(float, float)   # (nx, ny) absolute
    positionNudge       = pyqtSignal(float, float)   # (dx, dy) relative
    allToggled          = pyqtSignal(bool)            # True = ALL mode active

    def __init__(self, parent=None):
        super().__init__(parent)
        self._style = CaptionStyle()
        self._font_path: str | None = None
        self._build_ui()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        from PyQt6.QtWidgets import QScrollArea

        # Scrollable container so the panel never gets clipped on small screens
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(scroll.Shape.NoFrame)

        inner = QWidget()
        root = QVBoxLayout(inner)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        root.addWidget(self._build_font_box())
        root.addWidget(self._build_colour_box())
        root.addWidget(self._build_rows_box())
        root.addWidget(self._build_position_box())
        root.addWidget(self._build_alignment_box())
        root.addWidget(self._build_animation_box())
        root.addWidget(self._build_karaoke_box())
        root.addStretch()

        scroll.setWidget(inner)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

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
        self._bold_btn.setFixedSize(30, 28)
        self._bold_btn.setToolTip("Bold")
        self._bold_btn.setStyleSheet(
            "QPushButton { font-weight:bold; font-size:13px; font-family:serif; }"
        )
        self._bold_btn.toggled.connect(self._emit)
        row.addWidget(self._bold_btn)

        row.addStretch()
        browse_btn = QPushButton("Browse…")
        browse_btn.setToolTip("Load a custom .ttf / .otf font file")
        browse_btn.clicked.connect(self._browse_font)
        row.addWidget(browse_btn)
        v.addLayout(row)

        # Spacing row — use grid so labels+spinners stay aligned at any width
        sp = QGridLayout()
        sp.setSpacing(6)
        sp.setColumnStretch(0, 1)
        sp.setColumnStretch(1, 0)
        sp.setColumnStretch(2, 1)
        sp.setColumnStretch(3, 0)
        sp.addWidget(_label("Letter sp."), 0, 0)
        self._letter_sp_spin = _spin(-10, 50, 0, "Extra px between characters")
        self._letter_sp_spin.setFixedWidth(60)
        self._letter_sp_spin.valueChanged.connect(self._emit)
        sp.addWidget(self._letter_sp_spin, 0, 1)
        sp.addWidget(_label("Word sp."), 0, 2)
        self._word_sp_spin = _spin(-20, 100, 0, "Extra px between words")
        self._word_sp_spin.setFixedWidth(60)
        self._word_sp_spin.valueChanged.connect(self._emit)
        sp.addWidget(self._word_sp_spin, 0, 3)
        v.addLayout(sp)

        self._font_status = QLabel("Using system font")
        self._font_status.setStyleSheet("color:#4a5168; font-size:10px; background:transparent;")
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

    # ── Words & Rows group ────────────────────────────────────────────────

    def _build_rows_box(self) -> QGroupBox:
        box = QGroupBox("Words & Rows")
        g   = QGridLayout(box)
        g.setSpacing(6)
        g.setColumnStretch(0, 1)
        g.setColumnStretch(1, 0)
        g.setColumnStretch(2, 1)
        g.setColumnStretch(3, 0)

        g.addWidget(_label("Words / row"), 0, 0)
        self._wpl_spin = _spin(0, 20, 0, "Words per row (0 = unlimited)")
        self._wpl_spin.setSpecialValueText("∞")
        self._wpl_spin.setFixedWidth(60)
        self._wpl_spin.valueChanged.connect(self._emit)
        g.addWidget(self._wpl_spin, 0, 1)

        g.addWidget(_label("Rows"), 0, 2)
        self._rv_spin = _spin(1, 6, 1, "Rows visible at once")
        self._rv_spin.setFixedWidth(60)
        self._rv_spin.valueChanged.connect(self._emit)
        g.addWidget(self._rv_spin, 0, 3)

        return box

    # ── Position group ────────────────────────────────────────────────────

    def _build_position_box(self) -> QGroupBox:
        box = QGroupBox("Position")
        v   = QVBoxLayout(box)
        v.setSpacing(6)

        # "All" scope toggle — connect BEFORE setChecked so the green style applies on startup
        self._all_btn = QPushButton("ALL sentences")
        self._all_btn.setCheckable(True)
        self._all_btn.setFixedHeight(28)
        self._all_btn.setToolTip(
            "ON → position/alignment changes affect ALL sentences\n"
            "OFF → changes affect only the selected sentence"
        )
        self._all_btn.toggled.connect(self._on_all_toggled)
        self._all_btn.setChecked(True)   # fires _on_all_toggled → green style applied
        v.addWidget(self._all_btn)

        # Preset position buttons
        presets = QHBoxLayout()
        presets.setSpacing(4)
        for label, tip, nx, ny in [
            ("↑ Top",    "Top centre",    0.5, 0.08),
            ("· Mid",    "Middle centre", 0.5, 0.50),
            ("↓ Bottom", "Bottom centre", 0.5, 0.88),
        ]:
            btn = QPushButton(label)
            btn.setFixedHeight(28)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _, x=nx, y=ny: self._on_preset(x, y))
            presets.addWidget(btn)
        v.addLayout(presets)

        # Arrow nudge buttons (move ±1 % of screen)
        nudge_grid = QGridLayout()
        nudge_grid.setSpacing(3)
        for (row, col, label, tip, dx, dy) in [
            (0, 1, "▲", "Move up",    0,     -0.01),
            (1, 0, "◀", "Move left", -0.01,  0    ),
            (1, 2, "▶", "Move right", 0.01,  0    ),
            (2, 1, "▼", "Move down",  0,      0.01),
        ]:
            btn = QPushButton(label)
            btn.setFixedSize(32, 28)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _, dx=dx, dy=dy: self._on_nudge(dx, dy))
            nudge_grid.addWidget(btn, row, col)
        v.addLayout(nudge_grid)

        # Reset
        reset_btn = QPushButton("↺  Reset all positions + alignment")
        reset_btn.setToolTip("Move every sentence to centre, reset alignment to centered")
        reset_btn.clicked.connect(self.resetPositions.emit)
        v.addWidget(reset_btn)

        return box

    # ── Alignment group ───────────────────────────────────────────────────

    def _build_alignment_box(self) -> QGroupBox:
        box = QGroupBox("Alignment")
        al  = QHBoxLayout(box)
        al.setSpacing(4)

        self._align_group = QButtonGroup(self)
        self._align_btns  = {}
        for lbl, val, tip in [
            ("◀  L", "left",   "Text starts at anchor, grows right →"),
            ("≡  C", "center", "Text centred on anchor"),
            ("R  ▶", "right",  "Text ends at anchor, grows left ←"),
        ]:
            btn = QPushButton(lbl)
            btn.setCheckable(True)
            btn.setFixedHeight(28)
            btn.setMinimumWidth(52)
            btn.setToolTip(tip)
            self._align_group.addButton(btn)
            self._align_btns[val] = btn
            al.addWidget(btn)
        self._align_btns["center"].setChecked(True)
        self._align_group.buttonClicked.connect(self._on_align_clicked)
        al.addStretch()
        return box

    # ── Animation group ───────────────────────────────────────────────────

    def _build_animation_box(self) -> QGroupBox:
        box = QGroupBox("Animation")
        v   = QVBoxLayout(box)
        v.setSpacing(6)

        # Type buttons — 2×2 grid
        g = QGridLayout()
        g.setSpacing(4)
        self._anim_group = QButtonGroup(self)
        self._anim_btns  = {}
        entries = [
            ("None",     "none",     "No animation"),
            ("Pop-in",   "pop",      "Scale up then settle on entry"),
            ("Slide In", "slide_in", "Slide up into position on entry"),
            ("Shake",    "shake",    "Decaying horizontal shake on entry"),
        ]
        for i, (lbl, val, tip) in enumerate(entries):
            btn = QPushButton(lbl)
            btn.setCheckable(True)
            btn.setFixedHeight(30)
            btn.setToolTip(tip)
            self._anim_group.addButton(btn)
            self._anim_btns[val] = btn
            g.addWidget(btn, i // 2, i % 2)
        self._anim_btns["none"].setChecked(True)
        self._anim_group.buttonClicked.connect(self._on_anim_clicked)
        v.addLayout(g)

        # Duration + Intensity row
        params = QGridLayout()
        params.setSpacing(6)
        params.setColumnStretch(0, 1)
        params.setColumnStretch(1, 0)
        params.setColumnStretch(2, 1)
        params.setColumnStretch(3, 0)

        params.addWidget(_label("Duration"), 0, 0)
        self._anim_dur_spin = QSpinBox()
        self._anim_dur_spin.setRange(50, 3000)
        self._anim_dur_spin.setSingleStep(50)
        self._anim_dur_spin.setValue(350)
        self._anim_dur_spin.setSuffix(" ms")
        self._anim_dur_spin.setFixedWidth(80)
        self._anim_dur_spin.setToolTip("How long the animation plays (milliseconds)")
        self._anim_dur_spin.valueChanged.connect(self._emit)
        params.addWidget(self._anim_dur_spin, 0, 1)

        params.addWidget(_label("Intensity"), 0, 2)
        self._anim_int_spin = QSpinBox()
        self._anim_int_spin.setRange(10, 300)
        self._anim_int_spin.setSingleStep(10)
        self._anim_int_spin.setValue(100)
        self._anim_int_spin.setSuffix(" %")
        self._anim_int_spin.setFixedWidth(72)
        self._anim_int_spin.setToolTip(
            "Pop-in: overshoot amount\n"
            "Slide In: slide distance\n"
            "Shake: amplitude & speed"
        )
        self._anim_int_spin.valueChanged.connect(self._emit)
        params.addWidget(self._anim_int_spin, 0, 3)

        v.addLayout(params)
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
        if checked:
            self._all_btn.setStyleSheet(
                "QPushButton { background:#1a4a2e; border:1px solid #2d7a4a; color:#5eba7d;"
                " font-weight:600; border-radius:6px; padding:5px 12px; }"
                "QPushButton:hover { background:#1f5c38; border-color:#3d9c60; }"
            )
        else:
            self._all_btn.setStyleSheet("")
        self.allToggled.emit(checked)

    def set_all_mode(self, active: bool) -> None:
        """Programmatically activate/deactivate ALL mode without side effects."""
        self._all_btn.setChecked(active)

    def _on_preset(self, nx: float, ny: float) -> None:
        self._style.position = (nx, ny)
        self.positionPreset.emit(nx, ny)

    def _on_nudge(self, dx: float, dy: float) -> None:
        self.positionNudge.emit(dx, dy)

    def _on_align_clicked(self, _btn) -> None:
        align = self._current_align()
        if self._all_btn.isChecked():
            self._emit()
        else:
            self.segmentAlignChanged.emit(align)

    def _on_anim_clicked(self, _btn) -> None:
        anim = self._current_anim()
        if self._all_btn.isChecked():
            self._emit()
        else:
            self.segmentAnimChanged.emit(anim)

    def _current_anim(self) -> str:
        checked = self._anim_group.checkedButton()
        return next((v for v, b in self._anim_btns.items() if b is checked), "none")

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
        self._style.animation      = self._current_anim()
        self._style.anim_duration  = self._anim_dur_spin.value() / 1000.0
        self._style.anim_intensity = self._anim_int_spin.value() / 100.0
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

    def load_segment_state(self, seg) -> None:
        """Reflect a segment's per-segment overrides in the panel without emitting signals.
        Call after switching to Selected mode so the UI shows what is active for that segment."""
        # Determine effective animation for this segment
        anim = seg.animation if seg.animation is not None else self._style.animation
        dur_ms = round((seg.anim_duration  if seg.anim_duration  is not None
                        else self._style.anim_duration) * 1000)
        int_pct = round((seg.anim_intensity if seg.anim_intensity is not None
                         else self._style.anim_intensity) * 100)

        self._anim_group.blockSignals(True)
        btn = self._anim_btns.get(anim or "none")
        if btn:
            btn.setChecked(True)
        self._anim_group.blockSignals(False)

        self._anim_dur_spin.blockSignals(True)
        self._anim_dur_spin.setValue(max(50, min(3000, dur_ms)))
        self._anim_dur_spin.blockSignals(False)

        self._anim_int_spin.blockSignals(True)
        self._anim_int_spin.setValue(max(10, min(300, int_pct)))
        self._anim_int_spin.blockSignals(False)

        # Also reflect alignment override
        align = seg.text_align if seg.text_align is not None else self._style.text_align
        self._align_group.blockSignals(True)
        ab = self._align_btns.get(align or "center")
        if ab:
            ab.setChecked(True)
        self._align_group.blockSignals(False)
