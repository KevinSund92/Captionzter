"""
core/safe_area_config.py
------------------------
Platform safe-area definitions as fractions of video dimensions.
Add new platforms here without touching any other file.

Each entry has:
  top    — unsafe fraction from the top edge
  bottom — unsafe fraction from the bottom edge
  right  — unsafe fraction from the right edge
  left   — unsafe fraction from the left edge (usually 0)
"""

from __future__ import annotations

PLATFORMS: dict[str, dict[str, float]] = {
    "TikTok": {
        "top":    0.10,
        "bottom": 0.20,
        "right":  0.15,
        "left":   0.00,
    },
    "YouTube Shorts": {
        "top":    0.08,
        "bottom": 0.18,
        "right":  0.15,
        "left":   0.00,
    },
    "Instagram Reels": {
        "top":    0.12,
        "bottom": 0.25,
        "right":  0.14,
        "left":   0.00,
    },
}

# Aspect-ratio bounds that count as "short-form / vertical" video.
# 9:16 = 0.5625.  We accept anything between roughly 0.45 and 0.70.
_SHORT_FORM_MIN = 0.45
_SHORT_FORM_MAX = 0.70


def is_short_form_video(width: int, height: int) -> bool:
    """Return True when the video has a portrait / short-form aspect ratio."""
    if not width or not height:
        return False
    return _SHORT_FORM_MIN <= width / height <= _SHORT_FORM_MAX
