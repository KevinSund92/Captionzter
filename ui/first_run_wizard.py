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

def _python_from_registry() -> List[str]:
    """Read Python install paths from the Windows registry."""
    results = []
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for root_key in (r"Software\Python\PythonCore",
                             r"Software\Wow6432Node\Python\PythonCore"):
                try:
                    with winreg.OpenKey(hive, root_key) as core:
                        i = 0
                        while True:
                            try:
                                ver = winreg.EnumKey(core, i)
                                i += 1
                                try:
                                    with winreg.OpenKey(core, rf"{ver}\InstallPath") as ip:
                                        install_dir, _ = winreg.QueryValueEx(ip, "ExecutablePath")
                                        if install_dir and os.path.isfile(install_dir):
                                            results.append(install_dir)
                                except OSError:
                                    # Try default value
                                    try:
                                        with winreg.OpenKey(core, rf"{ver}\InstallPath") as ip:
                                            install_dir, _ = winreg.QueryValueEx(ip, "")
                                            p = os.path.join(install_dir, "python.exe")
                                            if os.path.isfile(p):
                                                results.append(p)
                                    except OSError:
                                        pass
                            except OSError:
                                break
                except OSError:
                    continue
    except Exception:
        pass
    return results


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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cancelled = False
        self._current_proc: Optional[subprocess.Popen] = None

    def cancel(self) -> None:
        """Signal cancellation and kill any running subprocess."""
        self._cancelled = True
        if self._current_proc and self._current_proc.poll() is None:
            self._current_proc.kill()

    def run(self) -> None:
        try:
            self._install_torch()
            self._check_cancelled()
            self._install_whisper_deps()
            self._check_cancelled()
            self._download_whisper_model()
            self._check_cancelled()
            self._ensure_ffmpeg()
            self.finished.emit()
        except _CancelledError:
            pass   # silently stop — wizard handles UI reset
        except Exception as exc:
            self.error.emit(str(exc))

    # ── Individual steps ──────────────────────────────────────────────────

    def _install_torch(self) -> None:
        self.step_started.emit(0, "Downloading PyTorch (~200 MB)…")
        self._run_pip(
            [
                "install", "torch", "torchvision", "torchaudio",
                "--index-url", "https://download.pytorch.org/whl/cpu",
            ],
            step=0,
        )

    def _install_whisper_deps(self) -> None:
        self.step_started.emit(1, "Downloading Whisper and video tools…")
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
        self.step_started.emit(2, "Downloading Whisper model (~244 MB)…")
        self.step_progress.emit(2, 0)

        pkg_dir   = self._pkg_dir()
        app_dir   = os.environ.get("CAPTION_STUDIO_APP_DIR", ".")
        model_dir = os.path.join(app_dir, "models", "whisper")

        # whisper uses tqdm which writes "\rXX%|..." to stderr — capture it
        code = (
            "import sys, os\n"
            f"sys.path.insert(0, {pkg_dir!r})\n"
            f"os.makedirs({model_dir!r}, exist_ok=True)\n"
            f"os.environ['WHISPER_CACHE'] = {model_dir!r}\n"
            "import whisper\n"
            "whisper.load_model('small')\n"
            "print('MODEL_OK', flush=True)\n"
        )
        python = self._find_python()
        proc = subprocess.Popen(
            [python, "-c", code],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=self._env(), bufsize=0,
        )
        self._current_proc = proc

        stdout_buf = []

        # Read stderr char-by-char to catch tqdm \r progress lines
        import threading
        def _read_stderr():
            buf = ""
            while True:
                ch = proc.stderr.read(1)  # type: ignore[union-attr]
                if not ch:
                    break
                if ch in ("\r", "\n"):
                    line = buf.strip()
                    buf = ""
                    if not line:
                        continue
                    self.log_line.emit(line)
                    # tqdm format: "  X%|█..."  or "100%|..."
                    pct = _parse_tqdm_pct(line)
                    if pct is not None:
                        self.step_progress.emit(2, pct)
                else:
                    buf += ch

        t = threading.Thread(target=_read_stderr, daemon=True)
        t.start()

        for line in proc.stdout:  # type: ignore[union-attr]
            stdout_buf.append(line.rstrip())
        t.join()
        proc.wait()

        self._current_proc = None
        if self._cancelled:
            raise _CancelledError()
        if proc.returncode != 0 or "MODEL_OK" not in "\n".join(stdout_buf):
            raise RuntimeError(
                "Whisper model download failed. Check your internet connection."
            )
        self.step_progress.emit(2, 100)

    def _ensure_ffmpeg(self) -> None:
        self.step_started.emit(3, "Setting up ffmpeg…")
        self.step_progress.emit(3, 0)
        self.log_line.emit("Downloading ffmpeg binary…")

        pkg_dir = self._pkg_dir()
        code = (
            "import sys\n"
            f"sys.path.insert(0, {pkg_dir!r})\n"
            "import imageio_ffmpeg\n"
            "p = imageio_ffmpeg.get_ffmpeg_exe()\n"
            "print('FFMPEG_OK:', p, flush=True)\n"
        )
        python = self._find_python()
        proc = subprocess.Popen(
            [python, "-c", code],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=self._env(),
        )
        self._current_proc = proc
        out_lines = []
        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.rstrip()
            self.log_line.emit(line)
            out_lines.append(line)
        proc.wait()
        self._current_proc = None
        if self._cancelled:
            raise _CancelledError()
        if proc.returncode != 0:
            raise RuntimeError("ffmpeg setup failed:\n" + "\n".join(out_lines))
        self.step_progress.emit(3, 100)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _check_cancelled(self) -> None:
        if self._cancelled:
            raise _CancelledError()

    def _pkg_dir(self) -> str:
        app_dir = os.environ.get("CAPTION_STUDIO_APP_DIR",
                                 os.path.dirname(os.path.abspath(__file__)))
        d = os.path.join(app_dir, "packages")
        os.makedirs(d, exist_ok=True)
        return d

    def _find_python(self) -> str:
        """Find a real Python 3 interpreter on this machine."""
        candidates = []

        # 1. Windows registry — most reliable, works even without PATH
        candidates += _python_from_registry()

        # 2. %LOCALAPPDATA%\Programs\Python\* (user install, default on Windows)
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            base = os.path.join(local_app, "Programs", "Python")
            if os.path.isdir(base):
                for sub in sorted(os.listdir(base), reverse=True):
                    p = os.path.join(base, sub, "python.exe")
                    if os.path.isfile(p):
                        candidates.append(p)

        # 3. System-wide installs
        for root in (
            r"C:\Python314", r"C:\Python313", r"C:\Python312",
            r"C:\Python311", r"C:\Python310",
        ):
            p = os.path.join(root, "python.exe")
            if os.path.isfile(p):
                candidates.append(p)

        # 4. Program Files
        for pf in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
            if not pf:
                continue
            for sub in ("Python314", "Python313", "Python312", "Python311", "Python310"):
                p = os.path.join(pf, sub, "python.exe")
                if os.path.isfile(p):
                    candidates.append(p)

        # 5. PATH fallback
        for name in ("python3.exe", "python.exe"):
            p = shutil.which(name)
            if p and os.path.isfile(p) and p != sys.executable:
                candidates.append(p)

        self.log_line.emit(f"Python candidates: {candidates}")

        # Verify each candidate actually runs Python 3
        for p in candidates:
            try:
                r = subprocess.run([p, "--version"],
                                   capture_output=True, text=True, timeout=5)
                ver = (r.stdout + r.stderr).strip()
                if r.returncode == 0 and "Python 3" in ver:
                    self.log_line.emit(f"Using: {p}  ({ver})")
                    return p
            except Exception as e:
                self.log_line.emit(f"  skipped {p}: {e}")

        raise RuntimeError(
            "Python 3.10+ not found on this machine.\n"
            "Please install Python from https://python.org then run the app again."
        )

    def _run_pip(self, args: list, step: int) -> None:
        """Run pip with --target, reading output char-by-char to catch \\r progress."""
        python = self._find_python()
        target = self._pkg_dir()
        cmd = [python, "-m", "pip"] + args + ["--target", target, "--progress-bar", "on"]
        self.log_line.emit(f"Running: {' '.join(cmd)}")

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=self._env(), bufsize=0,
        )
        self._current_proc = proc

        buf = ""
        last_pct = 0
        while True:
            ch = proc.stdout.read(1)  # type: ignore[union-attr]
            if not ch:
                break
            if ch in ("\r", "\n"):
                line = _strip_ansi(buf).strip()
                buf = ""
                if not line:
                    continue
                self.log_line.emit(line)
                # pip progress lines: "189.9/200.0 MB  5.2 MB/s"
                pct = _parse_pip_pct(line)
                if pct is not None and pct > last_pct:
                    last_pct = pct
                    self.step_progress.emit(step, pct)
                elif last_pct < 5:
                    # No progress info yet — pulse slowly
                    last_pct = min(last_pct + 1, 10)
                    self.step_progress.emit(step, last_pct)
            else:
                buf += ch

        proc.wait()
        self._current_proc = None
        if self._cancelled:
            raise _CancelledError()
        if proc.returncode != 0:
            raise RuntimeError(f"pip failed with exit code {proc.returncode}")
        self.step_progress.emit(step, 100)

    def _env(self) -> dict:
        e = os.environ.copy()
        # Force tqdm to write plain text (no fancy terminal codes)
        e["TQDM_DISABLE"] = "0"
        e["PYTHONUNBUFFERED"] = "1"
        return e


class _CancelledError(Exception):
    pass


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from a string."""
    import re
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def _parse_pip_pct(line: str) -> Optional[int]:
    """
    Parse percentage from pip download progress lines.
    pip outputs: '   189.9/200.0 MB  5.2 MB/s  eta 0:00:02'
    or with progress bar: '━━━━━ 189.9/200.0 MB ...'
    """
    import re
    # Pattern: <downloaded>/<total> MB
    m = re.search(r"([\d.]+)\s*/\s*([\d.]+)\s*[MmGgKk][Bb]", line)
    if m:
        try:
            done  = float(m.group(1))
            total = float(m.group(2))
            if total > 0:
                return min(99, int(done / total * 100))
        except ValueError:
            pass
    return None


def _parse_tqdm_pct(line: str) -> Optional[int]:
    """
    Parse percentage from tqdm output lines.
    tqdm format: ' 42%|████      | 103M/244M ...'
    """
    import re
    m = re.match(r"\s*(\d+)%\s*\|", line)
    if m:
        return min(99, int(m.group(1)))
    return None


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
            self._worker.cancel()
            self._worker.wait(3000)   # give it 3s to die cleanly
        self.reject()

    def _retry(self) -> None:
        self._install_btn.setEnabled(True)
        self._install_btn.setText("Install")
        self._stack.setCurrentIndex(0)
