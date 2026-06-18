"""
ui/update_dialog.py
-------------------
Auto-update dialog: downloads the new installer and runs it silently.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import urllib.request

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QVBoxLayout,
)

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


class _DownloadWorker(QThread):
    progress = pyqtSignal(int)   # 0-100
    finished = pyqtSignal(str)   # path to downloaded file
    error    = pyqtSignal(str)

    def __init__(self, url: str, dest: str, parent=None):
        super().__init__(parent)
        self._url  = url
        self._dest = dest

    def run(self) -> None:
        try:
            def _reporthook(block, bsize, total):
                if total > 0:
                    self.progress.emit(min(99, int(block * bsize / total * 100)))

            urllib.request.urlretrieve(self._url, self._dest, _reporthook)
            self.progress.emit(100)
            self.finished.emit(self._dest)
        except Exception as exc:
            self.error.emit(str(exc))


class UpdateDialog(QDialog):
    """
    Shows update info, downloads installer, runs it silently, closes app.
    """

    def __init__(self, tag: str, html_url: str, download_url: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CaptionStudio Update")
        self.setFixedWidth(420)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )

        self._tag          = tag
        self._html_url     = html_url
        self._download_url = download_url
        self._worker: _DownloadWorker | None = None
        self._dest: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(12)

        title = QLabel(f"CaptionStudio {tag} is available")
        title.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        layout.addWidget(title)

        self._status = QLabel("Click Update to download and install automatically.")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color:#8891aa; font-size:12px;")
        layout.addWidget(self._status)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setFixedHeight(12)
        self._bar.setTextVisible(False)
        self._bar.setStyleSheet(
            "QProgressBar { border-radius:3px; background:#1e2230; }"
            "QProgressBar::chunk { background:#3d74c4; border-radius:3px; }"
        )
        self._bar.hide()
        layout.addWidget(self._bar)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._later_btn = QPushButton("Later")
        self._later_btn.setFixedWidth(80)
        self._later_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._later_btn)
        btn_row.addSpacing(8)

        self._update_btn = QPushButton("Update now")
        self._update_btn.setFixedWidth(110)
        self._update_btn.setDefault(True)
        self._update_btn.setStyleSheet(
            "QPushButton { background:#1a6b3c; color:#fff; border-radius:4px; padding:6px 14px; }"
            "QPushButton:hover { background:#1e7d47; }"
            "QPushButton:disabled { background:#1a3a2a; color:#4a7a5a; }"
        )
        self._update_btn.clicked.connect(self._start_download)
        btn_row.addWidget(self._update_btn)

        layout.addLayout(btn_row)

    def _start_download(self) -> None:
        if not self._download_url:
            import webbrowser
            webbrowser.open(self._html_url)
            self.reject()
            return

        self._update_btn.setEnabled(False)
        self._update_btn.setText("Downloading…")
        self._later_btn.setEnabled(False)
        self._bar.show()
        self._status.setText("Downloading update…")

        suffix = os.path.basename(self._download_url)
        self._dest = os.path.join(tempfile.gettempdir(), suffix)

        self._worker = _DownloadWorker(self._download_url, self._dest, self)
        self._worker.progress.connect(self._bar.setValue)
        self._worker.finished.connect(self._on_downloaded)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_downloaded(self, path: str) -> None:
        self._status.setText("Installing update — the app will restart.")
        self._bar.setValue(100)
        # Run the Inno Setup installer silently; it replaces files and relaunches
        subprocess.Popen(
            [path, "/SILENT", "/NORESTART"],
            creationflags=_NO_WINDOW,
        )
        # Close app so the installer can overwrite files
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().quit()

    def _on_error(self, msg: str) -> None:
        self._status.setText(f"Download failed: {msg}\nOpening browser instead.")
        self._bar.hide()
        self._update_btn.setEnabled(True)
        self._update_btn.setText("Open download page")
        self._update_btn.clicked.disconnect()
        self._update_btn.clicked.connect(self._open_browser)
        self._later_btn.setEnabled(True)

    def _open_browser(self) -> None:
        import webbrowser
        webbrowser.open(self._html_url)
        self.reject()
