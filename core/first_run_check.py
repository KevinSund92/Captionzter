"""
core/first_run_check.py
-----------------------
Lightweight check (no Qt) for whether the heavy runtime deps are present.
Used by main.py to decide if the first-run wizard should be shown.
"""

from __future__ import annotations


def needs_setup() -> bool:
    """Return True if any required runtime dependency is missing."""
    try:
        import torch      # noqa: F401
        import whisper    # noqa: F401
        import moviepy    # noqa: F401
        return False
    except ImportError:
        return True
