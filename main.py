"""
CaptionStudio — Entry point.
Run directly: python main.py
Bundle:       pyinstaller build.spec
"""
import sys
import os

# ── PyInstaller runtime hook ─────────────────────────────────────────────────
# When frozen, __file__ lives inside the temp _MEIPASS bundle; we need the
# *real* executable's directory so we can resolve user-writable paths for
# Whisper model caches, exports, etc.
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

os.environ["CAPTION_STUDIO_APP_DIR"] = APP_DIR

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from ui.main_window import MainWindow


def main() -> None:
    # High-DPI is on by default in Qt6; just make sure rounding policy is clean
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setStyle("Fusion")   # cross-platform style — renders combo arrows correctly
    app.setApplicationName("CaptionStudio")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("CaptionStudio")

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
