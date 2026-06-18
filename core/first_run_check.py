"""
core/first_run_check.py
-----------------------
Checks whether the first-run setup has been completed.

Strategy: after the wizard installs everything it writes a marker file
"setup_complete" to the user-data directory (%LOCALAPPDATA%\CaptionStudio\).
We check for that file rather than trying to import torch/whisper.

Using LOCALAPPDATA instead of the app install dir so it works when the app
is installed to Program Files (which is not user-writable).
"""

from __future__ import annotations

import os
import sys


def _user_data_dir() -> str:
    """Return a user-writable directory for CaptionStudio data."""
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        return os.path.join(local, "CaptionStudio")
    # Fallback for non-Windows / dev mode
    app_dir = os.environ.get("CAPTION_STUDIO_APP_DIR",
                             os.path.dirname(os.path.abspath(__file__)))
    return app_dir


def packages_dir() -> str:
    """Return the path where pip packages are installed by the wizard."""
    return os.path.join(_user_data_dir(), "packages")


def _marker_path() -> str:
    return os.path.join(_user_data_dir(), "setup_complete")


def needs_setup() -> bool:
    """Return True if the first-run wizard has not completed yet."""
    return not os.path.exists(_marker_path())


def mark_setup_complete() -> None:
    """Write the marker file after successful setup."""
    try:
        os.makedirs(_user_data_dir(), exist_ok=True)
        with open(_marker_path(), "w") as f:
            f.write("1")
    except Exception:
        pass
