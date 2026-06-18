# build.spec
# ----------
# PyInstaller spec file for CaptionStudio.
#
# Usage:
#   pip install pyinstaller
#   pyinstaller build.spec
#
# Output: dist/CaptionStudio/CaptionStudio  (or CaptionStudio.exe on Windows)
#
# Notes on bundling strategy
# ─────────────────────────────────────────────────────────────────────────────
# 1. Whisper models are NOT embedded — they are large (39 MB to 1.5 GB).
#    Instead, the app caches them to  <APP_DIR>/models/whisper/  at first run.
#    After that, the app works fully offline.
#
# 2. ffmpeg binary:
#    • macOS/Linux: install ffmpeg system-wide; moviepy finds it via PATH.
#    • Windows:     place ffmpeg.exe + ffprobe.exe inside an  ffmpeg/  folder
#      next to the .spec, then uncomment the binaries line below.
#
# 3. Hidden imports are needed because PyInstaller can't see dynamic imports
#    inside openai-whisper and moviepy.
# ─────────────────────────────────────────────────────────────────────────────

import sys
from pathlib import Path

block_cipher = None

# ── Collect data files ────────────────────────────────────────────────────────
added_files = []

# ── Windows: bundle ffmpeg binaries (uncomment if needed) ────────────────────
# added_binaries = [
#     ("ffmpeg/ffmpeg.exe",   "."),
#     ("ffmpeg/ffprobe.exe",  "."),
# ]
added_binaries = []

hidden_imports = [
    # PyQt6 multimedia
    "PyQt6.QtMultimedia",
    "PyQt6.QtMultimediaWidgets",
    # Pillow
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageFont",
    # numpy (bundled, fast to include)
    "numpy",
]

# torch, whisper, moviepy, imageio are intentionally NOT bundled —
# they are downloaded by the first-run wizard at startup (~600 MB total).

a = Analysis(
    ["main.py"],
    pathex=[str(Path(SPECPATH))],
    binaries=added_binaries,
    datas=added_files,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy",
              "torch", "torchvision", "torchaudio",
              "whisper", "moviepy", "imageio", "imageio_ffmpeg"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CaptionStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # no terminal window on launch
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="assets/icons/app_icon.ico",   # uncomment + add your icon
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="CaptionStudio",
)
