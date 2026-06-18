"""
core/first_run_check.py
-----------------------
Checks whether the first-run setup has been completed.

Strategy: after the wizard installs everything it writes a marker file
"setup_complete" next to the executable. We check for that file rather
than trying to import torch/whisper (which are installed into the bundled
Python's site-packages and may not be importable until the process restarts).
"""

from __future__ import annotations

import os
import sys


def _marker_path() -> str:
    if getattr(sys, "frozen", False):
        app_dir = os.path.dirname(sys.executable)
    else:
        app_dir = os.environ.get("CAPTION_STUDIO_APP_DIR",
                                 os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(app_dir, "setup_complete")


def needs_setup() -> bool:
    """Return True if the first-run wizard has not completed yet."""
    return not os.path.exists(_marker_path())


def mark_setup_complete() -> None:
    """Write the marker file after successful setup."""
    try:
        with open(_marker_path(), "w") as f:
            f.write("1")
    except Exception:
        pass
