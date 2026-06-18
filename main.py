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

# Add user-data packages dir to sys.path so torch/whisper (installed by the
# first-run wizard into %LOCALAPPDATA%\CaptionStudio\packages\) are importable.
# LOCALAPPDATA is always user-writable, even when the app is in Program Files.
from core.first_run_check import packages_dir as _packages_dir
_pkg_dir = _packages_dir()
if os.path.isdir(_pkg_dir) and _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

from PyQt6.QtWidgets import QApplication, QDialog
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette


def _dark_palette() -> QPalette:
    """
    Set a dark QPalette so Fusion renders all native widgets (spinboxes,
    scrollbars, checkboxes …) with dark colours without needing stylesheet
    overrides that break Qt's internal subcontrol rendering.
    """
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window,          QColor(0x11, 0x13, 0x18))
    p.setColor(QPalette.ColorRole.WindowText,      QColor(0xe2, 0xe2, 0xe2))
    p.setColor(QPalette.ColorRole.Base,            QColor(0x21, 0x25, 0x2e))
    p.setColor(QPalette.ColorRole.AlternateBase,   QColor(0x18, 0x1b, 0x22))
    p.setColor(QPalette.ColorRole.ToolTipBase,     QColor(0x1e, 0x22, 0x30))
    p.setColor(QPalette.ColorRole.ToolTipText,     QColor(0xc8, 0xcd, 0xd8))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(0x4a, 0x51, 0x68))
    p.setColor(QPalette.ColorRole.Text,            QColor(0xc8, 0xcd, 0xd8))
    p.setColor(QPalette.ColorRole.Button,          QColor(0x21, 0x25, 0x2e))
    p.setColor(QPalette.ColorRole.ButtonText,      QColor(0xc8, 0xcd, 0xd8))
    p.setColor(QPalette.ColorRole.BrightText,      QColor(0xff, 0xff, 0xff))
    p.setColor(QPalette.ColorRole.Link,            QColor(0x5b, 0x9c, 0xf6))
    p.setColor(QPalette.ColorRole.Highlight,       QColor(0x3d, 0x74, 0xc4))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(0xff, 0xff, 0xff))
    # Disabled state
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(0x3d, 0x40, 0x50))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       QColor(0x3d, 0x40, 0x50))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(0x3d, 0x40, 0x50))
    return p


def main() -> None:
    # High-DPI is on by default in Qt6; just make sure rounding policy is clean
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(_dark_palette())   # dark palette → Fusion renders native widgets correctly
    app.setApplicationName("CaptionStudio")
    app.setApplicationVersion("1.1.9")
    app.setOrganizationName("CaptionStudio")

    # First-run check — show setup wizard if heavy deps are missing
    from core.first_run_check import needs_setup
    if needs_setup():
        from ui.first_run_wizard import FirstRunWizard
        wizard = FirstRunWizard()
        if wizard.exec() != QDialog.DialogCode.Accepted:
            sys.exit(0)   # user cancelled or setup failed
        # Wizard may have just created packages/ — add it now so imports work
        if _pkg_dir not in sys.path and os.path.isdir(_pkg_dir):
            sys.path.insert(0, _pkg_dir)

    from ui.main_window import MainWindow
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
