"""
ui/main_window.py
-----------------
CaptionStudio — Main application window.

Video is displayed via QGraphicsView + QGraphicsVideoItem so that the
caption overlay (a QGraphicsObject in the same scene) is guaranteed to
render on top on Windows without native-HWND z-order conflicts.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt, QRectF, QSettings, QSizeF, QThread, QTimer, QUrl, pyqtSlot
from PyQt6.QtGui import (
    QAction, QDragEnterEvent, QDropEvent, QKeySequence, QPainter,
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QGraphicsScene,
    QGraphicsView, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QSizePolicy, QSlider, QSplitter,
    QStatusBar, QTextEdit, QToolBar, QVBoxLayout, QWidget,
)

from core.caption_model import CaptionSegment, CaptionStyle, get_caption_blocks
from core.safe_area_config import PLATFORMS, is_short_form_video
from ui.safe_area_overlay import SafeAreaOverlay
from core.whisper_manager import (
    AVAILABLE_MODELS, WHISPER_LANGUAGES, WhisperTranscriber,
    LanguageDetector, model_is_cached,
)
from ui.caption_canvas import CaptionCanvas
from ui.style_panel import StylePanel
from ui.timeline_widget import TimelineWidget


# ────────────────────────────────────────────────────────────────────────────
# Drop-zone widget
# ────────────────────────────────────────────────────────────────────────────

class DropZone(QWidget):
    def __init__(self, on_file_cb, parent=None):
        super().__init__(parent)
        self._cb = on_file_cb
        self.setAcceptDrops(True)
        self.setMinimumHeight(80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        lbl = QLabel("Drop a video here\nor click to browse")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color:#4a5168; font-size:12px; line-height:1.5;")
        lay = QVBoxLayout(self)
        lay.addWidget(lbl)

        self.setStyleSheet(
            "DropZone { border:2px dashed #2e3340; border-radius:10px; background:#181b22; }"
            "DropZone:hover { border-color:#3d74c4; background:#1a1f2e; }"
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith((".mp4", ".mov", ".mkv", ".avi", ".webm")):
                self._cb(path)
                break

    def mousePressEvent(self, event) -> None:
        self._cb(None)


# ────────────────────────────────────────────────────────────────────────────
# Segment editor dialog
# ────────────────────────────────────────────────────────────────────────────

class SegmentEditDialog(QDialog):
    def __init__(self, segment: CaptionSegment, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Caption")
        self.setMinimumWidth(420)
        self._seg = segment

        layout = QVBoxLayout(self)

        timing = QHBoxLayout()
        timing.addWidget(QLabel("Start (s):"))
        self._start_edit = QLineEdit(f"{segment.start:.3f}")
        timing.addWidget(self._start_edit)
        timing.addWidget(QLabel("End (s):"))
        self._end_edit = QLineEdit(f"{segment.end:.3f}")
        timing.addWidget(self._end_edit)
        layout.addLayout(timing)

        layout.addWidget(QLabel("Text:"))
        self._text_edit = QTextEdit()
        self._text_edit.setPlainText(segment.text)
        self._text_edit.setFixedHeight(80)
        layout.addWidget(self._text_edit)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def result_segment(self) -> CaptionSegment:
        try:
            start = float(self._start_edit.text())
        except ValueError:
            start = self._seg.start
        try:
            end = float(self._end_edit.text())
        except ValueError:
            end = self._seg.end
        new_text = self._text_edit.toPlainText().strip()
        # If the text changed, word-level timestamps are no longer valid —
        # clear them so get_caption_blocks uses seg.text instead of stale tokens.
        words = self._seg.words if new_text == self._seg.text else []
        return CaptionSegment(
            text=new_text,
            start=start, end=end, words=words,
            position=self._seg.position,
            text_align=self._seg.text_align,
        )


# ────────────────────────────────────────────────────────────────────────────
# Main window
# ────────────────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("CaptionStudio")
        self.resize(1280, 780)
        self.setMinimumSize(960, 600)

        self._video_path:      Optional[str]                = None
        self._segments:        List[CaptionSegment]         = []
        self._style:           CaptionStyle                 = CaptionStyle()
        self._worker:          Optional[WhisperTranscriber] = None
        self._worker_thread:   Optional[QThread]            = None
        self._detect_worker    = None
        self._detect_thread:   Optional[QThread]            = None
        self._export_worker    = None   # strong ref to prevent GC
        self._export_thread:   Optional[QThread]            = None
        self._active_seg_idx:   Optional[int]   = None
        self._active_block_start: Optional[float] = None

        self._build_menu()
        self._build_toolbar()
        self._build_central()
        self._build_status_bar()

        # ── Media player ───────────────────────────────────────────────────
        self._player       = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_output)
        self._player.setVideoOutput(self._video_item)
        self._audio_output.setVolume(0.8)

        self._player.positionChanged.connect(self._on_player_position)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.positionChanged.connect(
            lambda ms: self._timeline.set_position(ms / 1000.0)
        )
        self._player.playbackStateChanged.connect(self._on_playback_state)

        self._video_item.nativeSizeChanged.connect(self._on_native_size_changed)

        self._caption_timer = QTimer(self)
        self._caption_timer.setInterval(50)
        self._caption_timer.timeout.connect(self._sync_caption_overlay)

        self._apply_dark_theme()
        # Push default style to caption item and timeline
        self._caption_item.set_style(self._style)
        self._timeline.set_style(self._style)

        # Restore persisted safe-area settings
        _s = QSettings("CaptionStudio", "CaptionStudio")
        _platform = _s.value("safe_area/platform", next(iter(PLATFORMS)))
        if _platform in PLATFORMS:
            idx = self._platform_combo.findText(_platform)
            if idx >= 0:
                self._platform_combo.setCurrentIndex(idx)
            self._safe_area_overlay.set_platform(_platform)
        # Toggle is only restored visually after a video is loaded and confirmed short-form

    # ────────────────────────────────────────────────────────────────────────
    # UI builders
    # ────────────────────────────────────────────────────────────────────────

    def _build_menu(self) -> None:
        mb = self.menuBar()

        file_menu  = mb.addMenu("&File")
        open_act   = QAction("&Open Video …", self, shortcut=QKeySequence.StandardKey.Open)
        open_act.triggered.connect(lambda: self._load_video(None))
        file_menu.addAction(open_act)

        export_act = QAction("&Export …", self, shortcut="Ctrl+E")
        export_act.triggered.connect(self._export)
        file_menu.addAction(export_act)

        file_menu.addSeparator()
        file_menu.addAction(QAction("&Quit", self, shortcut="Ctrl+Q",
                                    triggered=self.close))

        help_menu = mb.addMenu("&Help")
        help_menu.addAction(QAction("&About", self, triggered=self._about))

    def _build_toolbar(self) -> None:
        tb: QToolBar = self.addToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(__import__('PyQt6.QtCore', fromlist=['QSize']).QSize(16, 16))

        def _tbtn(label: str, accent: str = "") -> QPushButton:
            btn = QPushButton(label)
            btn.setFixedHeight(34)
            if accent:
                btn.setStyleSheet(
                    f"QPushButton {{ background:{accent}; border:none; border-radius:6px;"
                    f" padding:6px 18px; color:#fff; font-weight:600; font-size:12px; }}"
                    f"QPushButton:hover {{ background:{accent}dd; }}"
                    f"QPushButton:pressed {{ background:{accent}99; }}"
                    f"QPushButton:disabled {{ background:#21252e; color:#3d4050;"
                    f" border:1px solid #2e3340; }}"
                )
            return btn

        self._open_btn = _tbtn("📂  Open Video")
        self._open_btn.clicked.connect(lambda: self._load_video(None))
        tb.addWidget(self._open_btn)
        tb.addSeparator()

        self._transcribe_btn = _tbtn("🎙  Transcribe", "#1a6b3c")
        self._transcribe_btn.setEnabled(False)
        self._transcribe_btn.clicked.connect(self._start_transcription)
        tb.addWidget(self._transcribe_btn)

        self._cancel_btn = _tbtn("✕  Cancel")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._cancel_transcription)
        tb.addWidget(self._cancel_btn)
        tb.addSeparator()

        self._export_btn = _tbtn("⬆  Export MP4", "#1a4a8a")
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._export)
        tb.addWidget(self._export_btn)

    def _build_central(self) -> None:
        outer = QSplitter(Qt.Orientation.Horizontal, self)
        self.setCentralWidget(outer)

        # ── LEFT PANEL ────────────────────────────────────────────────────
        left = QWidget()
        left.setFixedWidth(240)
        left.setStyleSheet("QWidget { background:#0d0f14; }")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(10, 10, 10, 10)
        lv.setSpacing(10)

        self._drop_zone = DropZone(self._load_video)
        lv.addWidget(self._drop_zone)

        self._file_lbl = QLabel("No file loaded")
        self._file_lbl.setWordWrap(True)
        self._file_lbl.setStyleSheet("color:#4a5168; font-size:11px; padding:2px 0;")
        lv.addWidget(self._file_lbl)

        lang_box = QGroupBox("Language")
        lg = QVBoxLayout(lang_box)
        lg.setSpacing(4)

        lg.addWidget(QLabel("Spoken in video:"))
        self._lang_combo = QComboBox()
        for display_name, code in WHISPER_LANGUAGES:
            self._lang_combo.addItem(display_name, userData=code)
        en_index = next(
            (i for i, (n, _) in enumerate(WHISPER_LANGUAGES) if n == "English"), 0
        )
        self._lang_combo.setCurrentIndex(en_index)
        lg.addWidget(self._lang_combo)

        lg.addWidget(QLabel("Subtitle language:"))
        self._subtitle_lang_combo = QComboBox()
        self._subtitle_lang_combo.addItem("Same as spoken", userData=None)
        for display_name, code in WHISPER_LANGUAGES[1:]:   # skip Auto-detect
            self._subtitle_lang_combo.addItem(display_name, userData=code)
        lg.addWidget(self._subtitle_lang_combo)

        lv.addWidget(lang_box)

        model_box = QGroupBox("Whisper Model")
        ml = QVBoxLayout(model_box)
        self._model_combo = QComboBox()
        for name, desc in AVAILABLE_MODELS:
            cached = "✓ cached" if model_is_cached(name) else "↓ download"
            self._model_combo.addItem(f"{name}  [{cached}]", userData=name)
        self._model_combo.setCurrentIndex(2)
        ml.addWidget(self._model_combo)
        lv.addWidget(model_box)

        lv.addStretch()
        outer.addWidget(left)

        # ── CENTER: QGraphicsView ─────────────────────────────────────────
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)

        # Scene + video item + caption item — all in the same scene so there
        # are no native-HWND z-order issues on Windows.
        self._scene      = QGraphicsScene(self)
        self._video_item = QGraphicsVideoItem()
        self._video_item.setZValue(0)
        self._scene.addItem(self._video_item)

        self._safe_area_overlay = SafeAreaOverlay()
        self._scene.addItem(self._safe_area_overlay)   # z=1 (set in SafeAreaOverlay)

        self._caption_item = CaptionCanvas()
        self._caption_item.setZValue(2)
        self._caption_item.positionChanged.connect(self._on_canvas_position)
        self._scene.addItem(self._caption_item)

        self._gfx_view = QGraphicsView(self._scene)
        self._gfx_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._gfx_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._gfx_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._gfx_view.setStyleSheet("background: #000; border: none;")
        self._gfx_view.setMinimumSize(480, 270)
        self._gfx_view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        cv.addWidget(self._gfx_view, stretch=1)

        # ── Safe-area floating controls (top-right of view) ───────────────
        self._safe_controls = QWidget(self._gfx_view)
        sc_layout = QHBoxLayout(self._safe_controls)
        sc_layout.setContentsMargins(6, 4, 6, 4)
        sc_layout.setSpacing(4)

        self._safe_toggle = QPushButton("⊞  Safe Area")
        self._safe_toggle.setCheckable(True)
        self._safe_toggle.setEnabled(False)   # enabled only for short-form videos
        self._safe_toggle.setFixedHeight(28)
        self._safe_toggle.setStyleSheet(
            "QPushButton { background:#181b22; border:1px solid #2e3340; border-radius:6px;"
            " padding:4px 10px; color:#9ba3b5; font-size:11px; }"
            "QPushButton:checked { background:#1e3a5f; border-color:#3d74c4; color:#7eb8f7; }"
            "QPushButton:disabled { color:#2e3340; border-color:#1a1d25; }"
            "QPushButton:hover:!disabled { background:#21252e; border-color:#3d4457; }"
        )
        sc_layout.addWidget(self._safe_toggle)

        self._platform_combo = QComboBox()
        self._platform_combo.setFixedHeight(28)
        self._platform_combo.setEnabled(False)
        self._platform_combo.setStyleSheet(
            "QComboBox { background:#181b22; border:1px solid #2e3340; border-radius:6px;"
            " padding:4px 10px; color:#9ba3b5; font-size:11px; }"
            "QComboBox QAbstractItemView { background:#1e2230; color:#c8cdd8;"
            " selection-background-color:#1e3a5f; }"
        )
        for name in PLATFORMS:
            self._platform_combo.addItem(name)
        sc_layout.addWidget(self._platform_combo)

        self._safe_controls.adjustSize()
        self._safe_controls.hide()   # shown after a short-form video is loaded

        self._safe_toggle.toggled.connect(self._on_safe_toggle)
        self._platform_combo.currentTextChanged.connect(self._on_safe_platform_changed)

        # Transport bar
        transport = QWidget()
        transport.setFixedHeight(52)
        transport.setStyleSheet("QWidget { background:#0d0f14; border-top:1px solid #1e2028; }")
        tv = QHBoxLayout(transport)
        tv.setContentsMargins(12, 8, 12, 8)
        tv.setSpacing(10)

        self._play_btn = QPushButton("▶")
        self._play_btn.setFixedSize(34, 34)
        self._play_btn.setEnabled(False)
        self._play_btn.setStyleSheet(
            "QPushButton { background:#21252e; border:1px solid #2e3340; border-radius:17px;"
            " font-size:13px; color:#c8cdd8; }"
            "QPushButton:hover { background:#2a2f3d; border-color:#3d74c4; }"
            "QPushButton:pressed { background:#181c25; }"
            "QPushButton:disabled { color:#2e3340; border-color:#1e2028; }"
        )
        self._play_btn.clicked.connect(self._toggle_play)
        tv.addWidget(self._play_btn)

        self._seek_slider = QSlider(Qt.Orientation.Horizontal)
        self._seek_slider.setRange(0, 0)
        self._seek_slider.sliderMoved.connect(lambda v: self._player.setPosition(v))
        tv.addWidget(self._seek_slider)

        self._time_lbl = QLabel("0:00 / 0:00")
        self._time_lbl.setFixedWidth(80)
        self._time_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._time_lbl.setStyleSheet("color:#4a5168; font-size:11px; font-family:monospace;")
        tv.addWidget(self._time_lbl)
        cv.addWidget(transport)

        # ── Timeline ──────────────────────────────────────────────────────
        self._timeline = TimelineWidget()
        self._timeline.seekRequested.connect(
            lambda t: self._player.setPosition(int(t * 1000))
        )
        self._timeline.segmentSelected.connect(self._on_timeline_segment_selected)
        self._timeline.segmentDoubleClicked.connect(self._on_segment_double_clicked_by_idx)
        cv.addWidget(self._timeline)

        # Export progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setFixedHeight(18)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("Export: %p%")
        self._progress_bar.hide()
        cv.addWidget(self._progress_bar)

        outer.addWidget(center)

        # ── RIGHT PANEL ───────────────────────────────────────────────────
        self._style_panel = StylePanel()
        self._style_panel.setMinimumWidth(280)
        self._style_panel.setMaximumWidth(380)
        self._style_panel.setStyleSheet("QWidget { background:#0d0f14; }")
        self._style_panel.styleChanged.connect(self._on_style_changed)
        self._style_panel.resetPositions.connect(self._reset_all_positions)
        self._style_panel.segmentAlignChanged.connect(self._on_segment_align_changed)
        self._style_panel.segmentAnimChanged.connect(self._on_segment_anim_changed)
        self._style_panel.positionPreset.connect(self._on_position_preset)
        self._style_panel.positionNudge.connect(self._on_position_nudge)
        self._style_panel.allToggled.connect(self._on_all_mode_toggled)
        outer.addWidget(self._style_panel)

        outer.setStretchFactor(0, 0)   # left panel: fixed
        outer.setStretchFactor(1, 1)   # center: takes all spare space
        outer.setStretchFactor(2, 0)   # right panel: fixed
        outer.setSizes([240, 9999, 300])

    def _build_status_bar(self) -> None:
        self._status_bar = QStatusBar(self)
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready — open a video to begin.")

    # ────────────────────────────────────────────────────────────────────────
    # Video native size → fit scene
    # ────────────────────────────────────────────────────────────────────────

    def _on_native_size_changed(self, size: QSizeF) -> None:
        self._video_item.setSize(size)
        rect = QRectF(0, 0, size.width(), size.height())
        self._scene.setSceneRect(rect)
        self._gfx_view.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        self._caption_item.set_scene_size(size)
        self._safe_area_overlay.set_scene_size(size)

        short_form = is_short_form_video(int(size.width()), int(size.height()))
        self._safe_toggle.setEnabled(short_form)
        self._platform_combo.setEnabled(short_form)
        if short_form:
            self._safe_controls.show()
            self._safe_controls.adjustSize()
            self._reposition_safe_controls()
        else:
            self._safe_controls.hide()
            self._safe_area_overlay.set_active(False)
            self._safe_toggle.setChecked(False)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._scene.sceneRect().isValid():
            self._gfx_view.fitInView(
                self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio
            )
        self._reposition_safe_controls()

    def _reposition_safe_controls(self) -> None:
        """Keep the safe-area controls pinned to the top-right of the graphics view."""
        if not self._safe_controls.isVisible():
            return
        vw = self._gfx_view.width()
        w  = self._safe_controls.width()
        self._safe_controls.move(vw - w - 6, 6)

    # ── Safe-area slots ───────────────────────────────────────────────────

    def _on_safe_toggle(self, checked: bool) -> None:
        self._safe_area_overlay.set_active(checked)
        settings = QSettings("CaptionStudio", "CaptionStudio")
        settings.setValue("safe_area/enabled", checked)

    def _on_safe_platform_changed(self, platform: str) -> None:
        self._safe_area_overlay.set_platform(platform)
        settings = QSettings("CaptionStudio", "CaptionStudio")
        settings.setValue("safe_area/platform", platform)

    # ────────────────────────────────────────────────────────────────────────
    # File loading
    # ────────────────────────────────────────────────────────────────────────

    def _load_video(self, path: Optional[str]) -> None:
        if path is None:
            path, _ = QFileDialog.getOpenFileName(
                self, "Open Video", "",
                "Video files (*.mp4 *.mov *.mkv *.avi *.webm);;All files (*)"
            )
        if not path:
            return

        self._video_path = path
        name = Path(path).name
        self._file_lbl.setText(f"📄 {name}")
        self._file_lbl.setToolTip(path)
        self._status_bar.showMessage(f"Loaded: {name}")

        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.pause()

        self._play_btn.setEnabled(True)
        self._transcribe_btn.setEnabled(True)

        self._segments.clear()
        self._timeline.set_segments([])
        self._export_btn.setEnabled(False)

        self._caption_item.set_preview_text("Caption preview")

        # Auto-detect spoken language in background
        self._start_language_detection(path)

    # ────────────────────────────────────────────────────────────────────────
    # Language auto-detection
    # ────────────────────────────────────────────────────────────────────────

    def _start_language_detection(self, path: str) -> None:
        # Cancel any previous detection still running
        if self._detect_thread and self._detect_thread.isRunning():
            self._detect_thread.quit()

        self._status_bar.showMessage("Detecting spoken language …")
        self._lang_combo.setEnabled(False)

        worker = LanguageDetector(path)
        thread = QThread(self)
        worker.moveToThread(thread)
        self._detect_worker = worker
        self._detect_thread = thread

        worker.detected.connect(self._on_language_detected)
        worker.error.connect(self._on_language_detect_error)
        thread.started.connect(worker.run)
        worker.detected.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.start()

    @pyqtSlot(str)
    def _on_language_detected(self, code: str) -> None:
        self._lang_combo.setEnabled(True)
        idx = next(
            (i for i, (_, c) in enumerate(WHISPER_LANGUAGES) if c == code), -1
        )
        if idx >= 0:
            self._lang_combo.setCurrentIndex(idx)
            name = WHISPER_LANGUAGES[idx][0]
            self._status_bar.showMessage(f"Detected spoken language: {name}")
        else:
            self._status_bar.showMessage(f"Detected language code '{code}' (not in list)")

    @pyqtSlot(str)
    def _on_language_detect_error(self, _msg: str) -> None:
        # Detection failed silently — just re-enable the combo
        self._lang_combo.setEnabled(True)
        self._status_bar.showMessage(
            f"Loaded: {Path(self._video_path).name} — could not auto-detect language"
        )

    # ────────────────────────────────────────────────────────────────────────
    # Transcription
    # ────────────────────────────────────────────────────────────────────────

    def _start_transcription(self) -> None:
        if not self._video_path:
            return

        self._segments.clear()
        self._timeline.set_segments([])
        self._export_btn.setEnabled(False)

        spoken_lang   = self._lang_combo.currentData()
        subtitle_lang = self._subtitle_lang_combo.currentData()
        self._worker = WhisperTranscriber(
            video_path=self._video_path,
            model_name=self._model_combo.currentData(),
            language=spoken_lang,
            subtitle_lang=subtitle_lang,
        )
        self._worker_thread = QThread()
        self._worker.moveToThread(self._worker_thread)

        self._worker.progress.connect(self._on_transcription_progress)
        self._worker.segment_ready.connect(self._on_segment_ready)
        self._worker.finished.connect(self._on_transcription_done)
        self._worker.error.connect(self._on_transcription_error)
        self._worker_thread.started.connect(self._worker.run)

        self._worker_thread.start()
        self._transcribe_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._status_bar.showMessage("Transcribing …")

    def _cancel_transcription(self) -> None:
        if self._worker:
            self._worker.cancel()
        if self._worker_thread:
            self._worker_thread.quit()
        self._transcribe_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._status_bar.showMessage("Transcription cancelled.")

    @pyqtSlot(str)
    def _on_transcription_progress(self, msg: str) -> None:
        self._status_bar.showMessage(msg)

    @pyqtSlot(dict)
    def _on_segment_ready(self, seg_dict: dict) -> None:
        seg = CaptionSegment.from_whisper_dict(seg_dict)
        self._segments.append(seg)
        self._timeline.set_segments(self._segments)

    def _refresh_segment_list(self) -> None:
        self._timeline.set_segments(self._segments)

    @pyqtSlot(list)
    def _on_transcription_done(self, _segments: list) -> None:
        self._worker_thread.quit()
        self._transcribe_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._export_btn.setEnabled(bool(self._segments))
        self._status_bar.showMessage(
            f"Transcription complete — {len(self._segments)} segments. "
            "Double-click a segment to edit."
        )

    @pyqtSlot(str)
    def _on_transcription_error(self, msg: str) -> None:
        self._worker_thread.quit()
        self._transcribe_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        QMessageBox.critical(self, "Transcription Error", msg)

    # ────────────────────────────────────────────────────────────────────────
    # Segment editing
    # ────────────────────────────────────────────────────────────────────────

    def _on_segment_double_clicked_by_idx(self, idx: int) -> None:
        if not (0 <= idx < len(self._segments)):
            return
        dlg = SegmentEditDialog(self._segments[idx], self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._segments[idx] = dlg.result_segment()
            self._refresh_segment_list()
            self._caption_item.set_preview_text(self._segments[idx].text)

    # ────────────────────────────────────────────────────────────────────────
    # Export
    # ────────────────────────────────────────────────────────────────────────

    def _export(self) -> None:
        if not self._video_path or not self._segments:
            QMessageBox.warning(self, "Nothing to export",
                                "Load a video and transcribe it first.")
            return

        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Output Video", "", "MP4 Video (*.mp4)"
        )
        if not out_path:
            return
        if not out_path.lower().endswith(".mp4"):
            out_path += ".mp4"

        from core.export_engine import ExportWorker

        style  = self._style_panel.current_style()
        worker = ExportWorker(self._video_path, out_path, self._segments, style)
        thread = QThread(self)
        worker.moveToThread(thread)

        # Keep strong references so GC doesn't collect before thread finishes
        self._export_worker = worker
        self._export_thread = thread

        worker.progress.connect(self._progress_bar.setValue)
        worker.status.connect(self._status_bar.showMessage)
        worker.finished.connect(lambda p: self._on_export_done(p, thread))
        worker.error.connect(lambda e: self._on_export_error(e, thread))
        thread.started.connect(worker.run)

        self._progress_bar.setValue(0)
        self._progress_bar.show()
        self._export_btn.setEnabled(False)
        self._status_bar.showMessage("Starting export …")
        thread.start()

    def _on_export_done(self, path: str, thread: QThread) -> None:
        thread.quit()
        self._export_worker = None
        self._progress_bar.hide()
        self._export_btn.setEnabled(True)
        self._status_bar.showMessage(f"Export complete: {path}")
        QMessageBox.information(self, "Export Complete", f"Saved to:\n{path}")

    def _on_export_error(self, msg: str, thread: QThread) -> None:
        thread.quit()
        self._export_worker = None
        self._progress_bar.hide()
        self._export_btn.setEnabled(True)
        self._status_bar.showMessage("Export failed.")
        QMessageBox.critical(self, "Export Error", msg)

    # ────────────────────────────────────────────────────────────────────────
    # Playback
    # ────────────────────────────────────────────────────────────────────────

    def _toggle_play(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            self._caption_timer.stop()
        else:
            self._player.play()
            self._caption_timer.start()

    def _on_player_position(self, pos_ms: int) -> None:
        self._seek_slider.blockSignals(True)
        self._seek_slider.setValue(pos_ms)
        self._seek_slider.blockSignals(False)
        duration = self._player.duration() or 1
        self._time_lbl.setText(
            f"{self._fmt_ms(pos_ms)} / {self._fmt_ms(duration)}"
        )
        # Keep active segment in sync while paused (timer is stopped then)
        if self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self._sync_caption_overlay()

    def _on_duration_changed(self, dur_ms: int) -> None:
        self._seek_slider.setRange(0, dur_ms)
        self._timeline.set_duration(dur_ms / 1000.0)

    @pyqtSlot(QMediaPlayer.PlaybackState)
    def _on_playback_state(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._play_btn.setText("⏸" if playing else "▶")
        if not playing:
            self._caption_timer.stop()

    def _sync_caption_overlay(self) -> None:
        pos_s = self._player.position() / 1000.0
        for seg_idx, seg in enumerate(self._segments):
            if seg.start <= pos_s < seg.end:
                nx, ny = seg.position if seg.position else self._style.position
                self._caption_item.set_position_override(nx, ny)

                if seg_idx != self._active_seg_idx:
                    self._active_seg_idx = seg_idx
                    self._active_block_start = None   # force block-change on next check
                    self._timeline.set_selected(seg_idx)
                    self._caption_item.set_align_override(seg.text_align)

                blocks = get_caption_blocks(seg, self._style)
                active = blocks[0]
                for b in blocks:
                    if b[0] <= pos_s:
                        active = b
                b_start, _, lines, tokens = active

                # Re-trigger animation each time a new block becomes visible
                if b_start != self._active_block_start:
                    self._active_block_start = b_start
                    anim = seg.animation if seg.animation is not None \
                           else self._style.animation
                    self._caption_item.set_animation(anim or "none", b_start)

                self._caption_item.set_display(lines, tokens, pos_s)
                return

        # No active segment
        if self._active_seg_idx is not None:
            self._active_seg_idx    = None
            self._active_block_start = None
            self._timeline.set_selected(None)
        self._caption_item.set_display([], [], pos_s)

    # ────────────────────────────────────────────────────────────────────────
    # Style
    # ────────────────────────────────────────────────────────────────────────

    def _on_style_changed(self, style: CaptionStyle) -> None:
        self._style = style
        self._caption_item.set_style(style)
        self._timeline.set_style(style)
        if self._style_panel.scope_is_all():
            for seg in self._segments:
                seg.text_align = style.text_align
                seg.animation  = style.animation
            self._caption_item.set_align_override(None)

    def _on_timeline_segment_selected(self, idx: int) -> None:
        """Single-click on a timeline chip → switch to Selected mode for that segment."""
        self._active_seg_idx = idx
        self._timeline.set_selected(idx)
        # Switch panel to Selected mode without triggering allToggled→clear_selection loop
        self._style_panel.set_all_mode(False)

    def _on_all_mode_toggled(self, active: bool) -> None:
        """ALL button toggled in the style panel."""
        if active:
            # Return to ALL mode — clear timeline chip selection
            self._active_seg_idx = None
            self._timeline.clear_selection()
            self._timeline.set_selected(None)

    def _on_segment_align_changed(self, align: str) -> None:
        if self._active_seg_idx is not None and \
                0 <= self._active_seg_idx < len(self._segments):
            self._segments[self._active_seg_idx].text_align = align
            self._caption_item.set_align_override(align)

    def _on_segment_anim_changed(self, anim: str) -> None:
        if self._active_seg_idx is not None and \
                0 <= self._active_seg_idx < len(self._segments):
            seg = self._segments[self._active_seg_idx]
            seg.animation = anim
            self._caption_item.set_animation(anim, seg.start)

    def _reset_all_positions(self) -> None:
        # Reset position and alignment on every segment
        for seg in self._segments:
            seg.position   = None
            seg.text_align = None
        # Reset the style default to centre
        self._style.position  = (0.5, 0.9)
        self._style.text_align = "center"
        self._style_panel.apply_position(0.5, 0.9)
        self._style_panel.reset_align_to_center()
        self._caption_item.set_position_override(0.5, 0.9)
        self._caption_item.set_align_override(None)
        self._status_bar.showMessage("All positions and alignment reset to centre.")

    def _on_position_preset(self, nx: float, ny: float) -> None:
        if self._style_panel.scope_is_all():
            for seg in self._segments:
                seg.position = (nx, ny)
            self._style.position = (nx, ny)
            self._caption_item.set_position_override(nx, ny)
        elif self._active_seg_idx is not None and \
                0 <= self._active_seg_idx < len(self._segments):
            self._segments[self._active_seg_idx].position = (nx, ny)
            self._caption_item.set_position_override(nx, ny)
        else:
            self._status_bar.showMessage(
                "Select a sentence in the timeline first, or enable ALL sentences."
            )

    def _on_position_nudge(self, dx: float, dy: float) -> None:
        if self._style_panel.scope_is_all():
            base = self._style.position
            nx = max(0.0, min(1.0, base[0] + dx))
            ny = max(0.0, min(1.0, base[1] + dy))
            self._on_canvas_position(nx, ny)
            self._caption_item.set_position_override(nx, ny)
        elif self._active_seg_idx is not None and \
                0 <= self._active_seg_idx < len(self._segments):
            seg  = self._segments[self._active_seg_idx]
            base = seg.position if seg.position else self._style.position
            nx = max(0.0, min(1.0, base[0] + dx))
            ny = max(0.0, min(1.0, base[1] + dy))
            self._on_canvas_position(nx, ny)
            self._caption_item.set_position_override(nx, ny)
        else:
            self._status_bar.showMessage(
                "Select a sentence in the timeline first, or enable ALL sentences."
            )

    def _on_canvas_position(self, nx: float, ny: float) -> None:
        if self._style_panel.scope_is_all():
            # Move every segment (and the style default) to the new position
            for seg in self._segments:
                seg.position = (nx, ny)
            self._style_panel.apply_position(nx, ny)
        elif self._active_seg_idx is not None and \
                0 <= self._active_seg_idx < len(self._segments):
            self._segments[self._active_seg_idx].position = (nx, ny)
        else:
            self._style_panel.apply_position(nx, ny)
            self._style.position = (nx, ny)

    # ────────────────────────────────────────────────────────────────────────
    # Helpers
    # ────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _fmt_ms(ms: int) -> str:
        s = ms // 1000; m = s // 60; s %= 60
        return f"{m}:{s:02d}"

    @staticmethod
    def _fmt_time(t: float) -> str:
        s = int(t); m = s // 60; s %= 60
        return f"{m}:{s:02d}"

    def _about(self) -> None:
        QMessageBox.about(
            self, "About CaptionStudio",
            "<h3>CaptionStudio 1.0</h3>"
            "<p>Add styled captions to videos using OpenAI Whisper.</p>"
            "<p>Built with PyQt6 · ffmpeg</p>"
        )

    def _apply_dark_theme(self) -> None:
        self.setStyleSheet("""
            /* ── Base ─────────────────────────────────────────────── */
            QMainWindow, QDialog        { background:#111318; color:#e2e2e2; }
            QWidget                     { background:#111318; color:#e2e2e2;
                                          font-family:'Segoe UI', Arial, sans-serif;
                                          font-size:12px; }

            /* ── Group boxes ──────────────────────────────────────── */
            QGroupBox {
                border: 1px solid #2a2d35;
                border-radius: 8px;
                margin-top: 14px;
                padding-top: 10px;
                padding-bottom: 6px;
                background: #181b22;
                color: #9ba3b5;
                font-size: 11px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 10px;
                padding: 0 4px;
                background: #181b22;
            }

            /* ── Buttons ──────────────────────────────────────────── */
            QPushButton {
                background: #21252e;
                border: 1px solid #2e3340;
                border-radius: 6px;
                padding: 5px 12px;
                color: #c8cdd8;
                font-size: 12px;
            }
            QPushButton:hover           { background:#2a2f3d; border-color:#3d4457; color:#e2e2e2; }
            QPushButton:pressed         { background:#181c25; }
            QPushButton:checked         { background:#1e3a5f; border-color:#3d74c4; color:#7eb8f7; }
            QPushButton:checked:hover   { background:#264a78; }
            QPushButton:disabled        { color:#3d4050; border-color:#1e2028; background:#161920; }

            /* ── Toolbar buttons (larger) ─────────────────────────── */
            QToolBar                    { background:#0d0f14; border-bottom:1px solid #1e2028;
                                          spacing:4px; padding:5px 8px; }
            QToolBar QPushButton        { padding:6px 16px; font-size:12px; font-weight:500; }

            /* ── Combos ───────────────────────────────────────────── */
            QComboBox {
                background: #21252e;
                border: 1px solid #2e3340;
                border-radius: 6px;
                padding: 5px 10px;
                color: #c8cdd8;
                min-height: 28px;
            }
            QComboBox:hover             { border-color:#3d4457; }
            QComboBox::drop-down        { border:none; width:24px; }
            QComboBox::down-arrow       { image:none; width:0; height:0;
                                          border-left:5px solid transparent;
                                          border-right:5px solid transparent;
                                          border-top:5px solid #6b7280; }
            QComboBox QAbstractItemView {
                background:#1e2230; color:#c8cdd8;
                selection-background-color:#1e3a5f;
                border:1px solid #2e3340; border-radius:4px;
                outline:none;
            }

            /* QSpinBox rendered by Fusion+palette — no stylesheet override. */

            /* ── Inputs ───────────────────────────────────────────── */
            QLineEdit, QTextEdit {
                background: #21252e;
                border: 1px solid #2e3340;
                border-radius: 6px;
                padding: 5px 8px;
                color: #e2e2e2;
                selection-background-color: #1e3a5f;
            }
            QLineEdit:focus, QTextEdit:focus { border-color:#3d74c4; }

            /* ── Sliders ──────────────────────────────────────────── */
            QSlider::groove:horizontal  { background:#21252e; height:4px;
                                          border-radius:2px; border:none; }
            QSlider::sub-page:horizontal { background:#3d74c4; border-radius:2px; }
            QSlider::handle:horizontal  { background:#5b9cf6; width:14px; height:14px;
                                          margin:-5px 0; border-radius:7px;
                                          border:2px solid #111318; }

            /* ── Progress bar ─────────────────────────────────────── */
            QProgressBar {
                background:#21252e; border:1px solid #2e3340; border-radius:4px;
                color:#9ba3b5; font-size:11px; text-align:center;
            }
            QProgressBar::chunk        { background:#3d74c4; border-radius:3px; }

            /* ── Scroll bars ──────────────────────────────────────── */
            QScrollBar:vertical {
                background:#111318; width:8px; margin:0;
            }
            QScrollBar::handle:vertical {
                background:#2e3340; border-radius:4px; min-height:30px;
            }
            QScrollBar::handle:vertical:hover { background:#3d4457; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
            QScrollBar:horizontal {
                background:#111318; height:8px; margin:0;
            }
            QScrollBar::handle:horizontal {
                background:#2e3340; border-radius:4px; min-width:30px;
            }
            QScrollBar::handle:horizontal:hover { background:#3d4457; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0; }

            /* ── Menus ────────────────────────────────────────────── */
            QMenuBar                    { background:#0d0f14; color:#c8cdd8;
                                          border-bottom:1px solid #1e2028; padding:2px; }
            QMenuBar::item              { padding:4px 10px; border-radius:4px; }
            QMenuBar::item:selected     { background:#21252e; }
            QMenu                       { background:#1e2230; color:#c8cdd8;
                                          border:1px solid #2e3340; border-radius:6px;
                                          padding:4px; }
            QMenu::item                 { padding:6px 24px 6px 12px; border-radius:4px; }
            QMenu::item:selected        { background:#1e3a5f; color:#e2e2e2; }
            QMenu::separator            { height:1px; background:#2e3340; margin:4px 0; }

            /* ── Labels / misc ────────────────────────────────────── */
            QLabel                      { color:#c8cdd8; background:transparent; }
            QCheckBox                   { color:#c8cdd8; spacing:6px; }
            QCheckBox::indicator        { width:16px; height:16px; border-radius:4px;
                                          border:1px solid #2e3340; background:#21252e; }
            QCheckBox::indicator:checked { background:#3d74c4; border-color:#3d74c4; }

            /* ── Status bar ───────────────────────────────────────── */
            QStatusBar                  { background:#0d0f14; color:#6b7280;
                                          font-size:11px; border-top:1px solid #1e2028; }

            /* ── Graphics view ────────────────────────────────────── */
            QGraphicsView               { background:#000; border:none; }

            /* ── Font combo ───────────────────────────────────────── */
            QFontComboBox               { min-height:28px; }

            /* ── Splitter handle ──────────────────────────────────── */
            QSplitter::handle           { background:#1e2028; width:1px; height:1px; }
        """)
