"""
core/font_resolver.py
---------------------
Resolves a Qt font-family name to an on-disk .ttf / .otf file path.
On Windows: reads the registry key that maps display names to filenames.
Falls back to scanning C:\\Windows\\Fonts if the registry lookup misses.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from typing import Optional

_FONTS_DIR = r"C:\Windows\Fonts"


@lru_cache(maxsize=1)
def _build_registry_map() -> dict[str, str]:
    """Return {lowercase_family: absolute_path} from the Windows font registry."""
    mapping: dict[str, str] = {}
    if sys.platform != "win32":
        return mapping
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts",
        )
        idx = 0
        while True:
            try:
                name, filename, _ = winreg.EnumValue(key, idx)
                idx += 1
                # name is like "Arial Bold (TrueType)" — strip the suffix
                family = name.split("(")[0].strip().lower()
                # filename may be bare ("arial.ttf") or a full path
                if not os.path.isabs(filename):
                    filename = os.path.join(_FONTS_DIR, filename)
                if os.path.isfile(filename):
                    mapping[family] = filename
            except OSError:
                break
        winreg.CloseKey(key)
    except Exception:
        pass
    return mapping


@lru_cache(maxsize=1)
def _build_scan_map() -> dict[str, str]:
    """Scan the Fonts folder and build {stem_lower: path} as fallback."""
    mapping: dict[str, str] = {}
    if not os.path.isdir(_FONTS_DIR):
        return mapping
    for fname in os.listdir(_FONTS_DIR):
        if fname.lower().endswith((".ttf", ".otf")):
            stem = os.path.splitext(fname)[0].lower()
            mapping[stem] = os.path.join(_FONTS_DIR, fname)
    return mapping


def resolve_font_family(family: str) -> Optional[str]:
    """
    Given a font family name (e.g. "Arial", "Segoe UI"), return the path
    to a matching font file, or None if not found.
    """
    key = family.strip().lower()

    # 1. Exact registry match
    reg = _build_registry_map()
    if key in reg:
        return reg[key]

    # 2. Prefix match in registry (handles "Arial" matching "Arial Regular")
    for reg_key, path in reg.items():
        if reg_key.startswith(key) or key.startswith(reg_key):
            return path

    # 3. Scan-based fallback
    scan = _build_scan_map()
    # Try exact stem
    for stem_key in (key, key.replace(" ", ""), key.replace(" ", "-")):
        if stem_key in scan:
            return scan[stem_key]

    # 4. Partial stem match
    for stem_key, path in scan.items():
        if key in stem_key or stem_key in key:
            return path

    return None
