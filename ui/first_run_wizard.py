"""
ui/first_run_wizard.py
----------------------
First-run setup wizard shown when heavy dependencies (torch, whisper, ffmpeg)
are not yet installed.

Install strategy
----------------
PyInstaller freezes the app into a standalone exe — sys.executable IS that exe,
not Python, so "sys.executable -m pip" does nothing useful.

Instead we:
  1. Find a real Python interpreter via the Windows 'py' launcher or PATH.
  2. Install all packages with --target=<APP_DIR>/packages/ so they land in a
     known location outside the frozen bundle.
  3. main.py inserts that directory into sys.path at startup so torch/whisper
     are importable in every subsequent launch.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QSizePolicy,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget,
)


# ── Setup steps ───────────────────────────────────────────────────────────────
# Each entry: (display_name, weight)
# weight = relative share of total progress (used for the per-step bar)

_STEPS: List[Tuple[str, int]] = [
    ("PyTorch (AI engine)",    4),   # largest download
    ("Whisper (speech AI)",    2),
    ("Whisper model weights",  3),   # ~244 MB model download
    ("ffmpeg (video tools)",   1),
]


class SetupWorker(QThread):
    """Runs all install steps in sequence; emits progress updates."""

    step_started  = pyqtSignal(int, str)   # (step_index, label)
    step_progress = pyqtSignal(int, int)   # (step_index, 0-100)
    log_line      = pyqtSignal(str)
    finished      = pyqtSignal()
    error         = pyqtSignal(str)

    def run(self) -> None:
        try:
            self._install_torch()
            self._install_whisper_deps()
            self._download_whisper_model()
            self._ensure_ffmpeg()
            self.finished.emit()
        except Exception as exc:
            self.error.emit(str(exc))

    # ── Individual steps ──────────────────────────────────────────────────

    def _install_torch(self) -> None:
        self.step_started.emit(0, "Installing PyTorch…")
        self._run_pip(
            [
                "install", "torch", "torchvision", "torchaudio",
                "--index-url", "https://download.pytorch.org/whl/cpu",
            ],
            step=0,
        )

    def _install_whisper_deps(self) -> None:
        self.step_started.emit(1, "Installing Whisper and video tools…")
        self._run_pip(
            [
                "install",
                "openai-whisper>=20231117",
                "moviepy>=1.0.3",
                "imageio>=2.33.0",
                "imageio-ffmpeg==0.6.0",
                "Pillow>=10.0.0",
                "numpy>=1.24.0",
            ],
            step=1,
        )

    def _download_whisper_model(self) -> None:
        self.step_started.emit(2, "Downloading Whisper 'small' model (~244 MB)…")
        self.step_progress.emit(2, 0)
        self.log_line.emit("Downloading whisper small model…")

        pkg_dir  = self._pkg_dir()
        app_dir  = os.environ.get("CAPTION_STUDIO_APP_DIR", ".")
        model_dir = os.path.join(app_dir, "models", "whisper")
        code = (
            f"import sys; sys.path.insert(0, {pkg_dir!r})\n"
            f"import os; os.makedirs({model_dir!r}, exist_ok=True)\n"
            f"os.environ['WHISPER_CACHE'] = {model_dir!r}\n"
            "import whisper\n"
            "whisper.load_model('small')\n"
            "print('MODEL_OK')\n"
        )
        python = self._find_python()
        result = subprocess.run(
            [python, "-c", code],
            capture_output=True, text=True, env=self._env(),
        )
        for line in (result.stdout + result.stderr).splitlines():
            self.log_line.emit(line)
        if result.returncode != 0 or "MODEL_OK" not in result.stdout:
            raise RuntimeError(
                f"Whisper model download failed:\n{result.stderr or result.stdout}"
            )
        self.step_progress.emit(2, 100)

    def _ensure_ffmpeg(self) -> None:
        self.step_started.emit(3, "Setting up ffmpeg…")
        self.step_progress.emit(3, 0)
        self.log_line.emit("Locating ffmpeg binary…")

        pkg_dir = self._pkg_dir()
        code = (
            f"import sys; sys.path.insert(0, {pkg_dir!r})\n"
            "import imageio_ffmpeg\n"
            "p = imageio_ffmpeg.get_ffmpeg_exe()\n"
            "print('FFMPEG_OK:', p)\n"
        )
        python = self._find_python()
        result = subprocess.run(
            [python, "-c", code],
            capture_output=True, text=True, env=self._env(),
        )
        for line in (result.stdout + result.stderr).splitlines():
            self.log_line.emit(line)
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg setup failed:\n{result.stderr or result.stdout}"
            )
        self.step_progress.emit(3, 100)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _pkg_dir(self) -> str:
        """Directory where packages are installed (next to the exe)."""
        app_dir = os.environ.get("CAPTION_STUDIO_APP_DIR",
                                 os.path.dirname(os.path.abspath(__file__)))
        d = os.path.join(app_dir, "packages")
        os.makedirs(d, exist_ok=True)
        return d

    def _find_python(self) -> str:
        """Find a real Python interpreter — NOT the frozen exe."""
        # 1. Windows 'py' launcher (installed alongside any Python on Windows)
        py = shutil.which("py")
        if py:
            self.log_line.emit(f"Using Python launcher: {py}")
            return py
        # 2. python3 / python on PATH
        for name in ("python3", "python"):
            p = shutil.which(name)
            if p and p != sys.executable:
                self.log_line.emit(f"Using Python: {p}")
                return p
        # 3. Common Windows install locations
        for base in (
            os.path.expanduser("~\\AppData\\Local\\Programs\\Python"),
            "C:\\Python311", "C:\\Python310", "C:\\Python312", "C:\\Python313", "C:\\Python314",
        ):
            for sub in os.listdir(base) if os.path.isdir(base) else []:
                candidate = os.path.join(base, sub, "python.exe")
                if os.path.isfile(candidate):
                    self.log_line.emit(f"Found Python: {candidate}")
                    return candidate
        raise RuntimeError(
            "Python 3.10+ not found on this machine.\n"
            "Please install Python from https://python.org and try again."
        )

    def _run_pip(self, args: list, step: int) -> None:
        python  = self._find_python()
        target  = self._pkg_dir()
        # Use 'py -3' syntax if it's the py launcher
        if os.path.basename(python).lower() == "py.exe":
            base_cmd = [python, "-3", "-m", "pip"]
        else:
            base_cmd = [python, "-m", "pip"]
        cmd = base_cmd + args + ["--target", target]
        self.log_line.emit(f"Running: {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=self._env(),
        )
        lines_seen = 0
        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.rstrip()
            self.log_line.emit(line)
            lines_seen += 1
            self.step_progress.emit(step, min(95, lines_seen * 3))
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"pip exited with code {proc.returncode}")
        self.step_progress.emit(step, 100)

    def _env(self) -> dict:
        return os.environ.copy()


# ── Wizard dialog ─────────────────────────────────────────────────────────────

class FirstRunWizard(QDialog):
    """Modal dialog that walks the user through the first-time setup."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("CaptionStudio — First-time Setup")
        self.setMinimumSize(520, 420)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowModality(Qt.WindowModality.ApplicationModal)

        self._worker: SetupWorker | None = None
        self._stack = QStackedWidget()

        self._stack.addWidget(self._build_welcome_page())    # 0
        self._stack.addWidget(self._build_progress_page())   # 1
        self._stack.addWidget(self._build_done_page())       # 2
        self._stack.addWidget(self._build_error_page())      # 3

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._stack)

    # ── Pages ─────────────────────────────────────────────────────────────

    def _build_welcome_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(40, 40, 40, 32)
        v.setSpacing(16)

        title = QLabel("Welcome to CaptionStudio")
        title.setFont(QFont("Arial", 18, QFont.Weight.Bold))
        v.addWidget(title)

        body = QLabel(
            "Before you can start captioning videos, a few components need to\n"
            "be downloaded and installed (around 600 MB total):\n\n"
            "  •  PyTorch — AI engine for speech recognition\n"
            "  •  Whisper — OpenAI speech-to-text model\n"
            "  •  Whisper model weights (~244 MB)\n"
            "  •  ffmpeg — video processing tools\n\n"
            "This only happens once. Click Install to begin."
        )
        body.setWordWrap(True)
        body.setStyleSheet("color:#b0b8cc; font-size:13px; line-height:1.5;")
        v.addWidget(body)

        v.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedWidth(90)
        cancel_btn.clicked.connect(self.reject)
        install_btn = QPushButton("Install")
        install_btn.setFixedWidth(110)
        install_btn.setDefault(True)
        install_btn.setStyleSheet(
            "QPushButton { background:#1a6b3c; color:#fff; border-radius:4px; padding:6px 16px; }"
            "QPushButton:hover { background:#1e7d47; }"
            "QPushButton:disabled { background:#1a3a2a; color:#4a7a5a; }"
        )
        self._install_btn = install_btn
        install_btn.clicked.connect(self._start_install)
        btn_row.addWidget(cancel_btn)
        btn_row.addSpacing(8)
        btn_row.addWidget(install_btn)
        v.addLayout(btn_row)

        return w

    def _build_progress_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(40, 32, 40, 28)
        v.setSpacing(10)

        title = QLabel("Installing components…")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        v.addWidget(title)

        self._step_label = QLabel("Starting…")
        self._step_label.setStyleSheet("color:#5b9cf6; font-size:12px;")
        v.addWidget(self._step_label)

        v.addSpacing(8)

        # Per-step rows
        self._step_bars: List[QProgressBar] = []
        for name, _ in _STEPS:
            row = QHBoxLayout()
            lbl = QLabel(name)
            lbl.setFixedWidth(200)
            lbl.setStyleSheet("color:#8891aa; font-size:12px;")
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setFixedHeight(14)
            bar.setTextVisible(False)
            bar.setStyleSheet(
                "QProgressBar { border-radius:3px; background:#1e2230; }"
                "QProgressBar::chunk { background:#3d74c4; border-radius:3px; }"
            )
            row.addWidget(lbl)
            row.addWidget(bar, 1)
            v.addLayout(row)
            self._step_bars.append(bar)

        v.addSpacing(8)

        self._log_box = QTextEdit()
        self._log_box.setReadOnly(True)
        self._log_box.setFixedHeight(130)
        self._log_box.setStyleSheet(
            "QTextEdit { background:#0d0f14; color:#4a5168; "
            "font-family:Consolas,monospace; font-size:10px; border:1px solid #1e2230; }"
        )
        v.addWidget(self._log_box)

        v.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._cancel_install_btn = QPushButton("Cancel")
        self._cancel_install_btn.setFixedWidth(90)
        self._cancel_install_btn.clicked.connect(self._on_cancel_install)
        btn_row.addWidget(self._cancel_install_btn)
        v.addLayout(btn_row)

        return w

    def _build_done_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(40, 60, 40, 40)
        v.setSpacing(16)
        v.addStretch()

        icon = QLabel("✓")
        icon.setFont(QFont("Arial", 48))
        icon.setStyleSheet("color:#1e7d47;")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(icon)

        title = QLabel("Setup complete!")
        title.setFont(QFont("Arial", 18, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(title)

        body = QLabel("Everything is installed. CaptionStudio is ready to use.")
        body.setStyleSheet("color:#8891aa; font-size:13px;")
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(body)

        v.addStretch()

        launch_btn = QPushButton("Launch CaptionStudio")
        launch_btn.setFixedWidth(200)
        launch_btn.setDefault(True)
        launch_btn.setStyleSheet(
            "QPushButton { background:#1a6b3c; color:#fff; border-radius:4px; padding:8px 20px; font-size:13px; }"
            "QPushButton:hover { background:#1e7d47; }"
        )
        launch_btn.clicked.connect(self.accept)

        center = QHBoxLayout()
        center.addStretch()
        center.addWidget(launch_btn)
        center.addStretch()
        v.addLayout(center)
        v.addSpacing(20)

        return w

    def _build_error_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(40, 40, 40, 32)
        v.setSpacing(16)

        title = QLabel("Setup failed")
        title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title.setStyleSheet("color:#e05555;")
        v.addWidget(title)

        self._error_label = QLabel("")
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet("color:#b0b8cc; font-size:12px;")
        v.addWidget(self._error_label)

        hint = QLabel(
            "Make sure you have an internet connection and try again.\n"
            "If the problem persists, run setup.bat manually from the install folder."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#5a6278; font-size:11px;")
        v.addWidget(hint)

        v.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(90)
        close_btn.clicked.connect(self.reject)
        retry_btn = QPushButton("Retry")
        retry_btn.setFixedWidth(90)
        retry_btn.setStyleSheet(
            "QPushButton { background:#1a4a8a; color:#fff; border-radius:4px; padding:6px; }"
            "QPushButton:hover { background:#1e5aa0; }"
        )
        retry_btn.clicked.connect(self._retry)
        btn_row.addWidget(close_btn)
        btn_row.addSpacing(8)
        btn_row.addWidget(retry_btn)
        v.addLayout(btn_row)

        return w

    # ── Logic ─────────────────────────────────────────────────────────────

    def _start_install(self) -> None:
        # Disable install button immediately to prevent double-clicks
        self._install_btn.setEnabled(False)
        self._install_btn.setText("Installing…")

        self._stack.setCurrentIndex(1)
        self.raise_()
        self.activateWindow()
        for bar in self._step_bars:
            bar.setValue(0)
        self._log_box.clear()

        self._worker = SetupWorker(self)
        self._worker.step_started.connect(self._on_step_started)
        self._worker.step_progress.connect(self._on_step_progress)
        self._worker.log_line.connect(self._on_log_line)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_step_started(self, idx: int, label: str) -> None:
        self._step_label.setText(f"▸  {label}")

    def _on_step_progress(self, idx: int, pct: int) -> None:
        if 0 <= idx < len(self._step_bars):
            self._step_bars[idx].setValue(pct)

    def _on_log_line(self, line: str) -> None:
        self._log_box.append(line)
        sb = self._log_box.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_finished(self) -> None:
        from core.first_run_check import mark_setup_complete
        mark_setup_complete()
        self._stack.setCurrentIndex(2)

    def _on_error(self, msg: str) -> None:
        self._error_label.setText(msg)
        self._stack.setCurrentIndex(3)

    def _on_cancel_install(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait()
        self.reject()

    def _retry(self) -> None:
        self._install_btn.setEnabled(True)
        self._install_btn.setText("Install")
        self._stack.setCurrentIndex(0)
