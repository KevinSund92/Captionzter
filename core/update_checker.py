"""
core/update_checker.py
----------------------
Background thread that checks GitHub Releases for a newer version.
No Qt UI — emits a signal the main window can connect to.

Usage:
    checker = UpdateChecker("1.0.0", "your-username/caption-studio")
    checker.update_available.connect(lambda tag, url: ...)
    checker.start()
"""

from __future__ import annotations

import urllib.request
import json

from PyQt6.QtCore import QThread, pyqtSignal


def _parse_version(tag: str) -> tuple[int, ...]:
    """Turn 'v1.2.3' or '1.2.3' into (1, 2, 3)."""
    clean = tag.lstrip("v").strip()
    try:
        return tuple(int(x) for x in clean.split("."))
    except ValueError:
        return (0,)


class UpdateChecker(QThread):
    """Fetches the latest GitHub release tag in a background thread.

    Signals:
        update_available(latest_tag, release_url) — only fired when a newer
            version exists.  Never fired on network error (silently ignored).
    """

    update_available = pyqtSignal(str, str)   # (tag, html_url)

    def __init__(self, current_version: str, repo: str, parent=None) -> None:
        """
        current_version: the running app version, e.g. "1.0.0"
        repo:            GitHub repo in "owner/name" form
        """
        super().__init__(parent)
        self._current = current_version
        self._api_url = f"https://api.github.com/repos/{repo}/releases/latest"

    def run(self) -> None:
        try:
            req = urllib.request.Request(
                self._api_url,
                headers={"Accept": "application/vnd.github+json",
                         "User-Agent": "CaptionStudio-UpdateChecker/1.0"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())

            tag      = data.get("tag_name", "")
            html_url = data.get("html_url", "")

            if not tag:
                return

            if _parse_version(tag) > _parse_version(self._current):
                self.update_available.emit(tag, html_url)

        except Exception:
            pass   # network down, rate-limited, private repo — silently ignore
