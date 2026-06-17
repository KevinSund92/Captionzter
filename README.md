# CaptionStudio

A professional-grade, installable desktop application for adding dynamic,
styled captions to MP4 videos using local OpenAI Whisper transcription.

---

## Project Structure

```
caption_studio/
│
├── main.py                     ← Entry point (frozen-safe APP_DIR resolution)
├── build.spec                  ← PyInstaller bundle configuration
├── requirements.txt
│
├── core/                       ← Business logic, no Qt imports
│   ├── __init__.py
│   ├── caption_model.py        ← CaptionSegment, CaptionStyle, MoviePy renderer
│   ├── export_engine.py        ← ExportWorker (QThread) — ffmpeg via MoviePy
│   └── whisper_manager.py      ← WhisperTranscriber (QThread) + model caching
│
├── ui/                         ← PyQt6 UI components
│   ├── __init__.py
│   ├── main_window.py          ← MainWindow — layout, wiring, media player
│   ├── caption_canvas.py       ← Draggable caption overlay over video widget
│   └── style_panel.py          ← Font, colour, outline, karaoke controls
│
├── assets/
│   ├── fonts/                  ← Ship any bundled fonts here (TTF/OTF)
│   └── icons/                  ← App icon (app_icon.ico / .icns / .png)
│
└── models/
    └── whisper/                ← Auto-created at runtime; Whisper .pt files live here
```

### Architectural decisions

| Concern | Choice | Reason |
|---|---|---|
| UI framework | PyQt6 | Mature, cross-platform, ships QVideoWidget |
| Video processing | MoviePy + ffmpeg | High-quality codec control via libx264 |
| Transcription | openai-whisper (local) | Works offline after first download |
| Threading | QThread + QObject workers | Keeps UI responsive during transcription/export |
| Caption overlay | Custom QWidget (CaptionCanvas) | Transparent overlay; avoids compositing in OpenGL |
| Packaging | PyInstaller COLLECT mode | Multi-file dist — smaller, faster than single-file |

---

## Whisper Model Caching (Offline Strategy)

Whisper models are **not** embedded in the installer (they are up to 1.5 GB).
Instead:

1. On first run, the selected model downloads from OpenAI's CDN into
   `<APP_DIR>/models/whisper/` — right next to the executable.
2. On every subsequent run, the model loads from that local path **without
   any network access**.
3. The UI shows `✓ cached` or `↓ download` next to each model name.
4. Enterprise deployments can pre-populate the `models/whisper/` directory
   before shipping the installer.

The cache directory is set via environment variables before Whisper is
imported (`WHISPER_CACHE` and `XDG_CACHE_HOME`), so the library honours it
transparently.

---

## Installation & Running from Source

```bash
# 1. Clone / unzip the project
cd caption_studio

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install PyTorch (CPU — fastest to install)
pip install torch torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/cpu

# 4. Install remaining dependencies
pip install -r requirements.txt

# 5. Ensure ffmpeg is on PATH
#    macOS:   brew install ffmpeg
#    Ubuntu:  sudo apt install ffmpeg
#    Windows: download from https://ffmpeg.org/download.html and add to PATH

# 6. Run
python main.py
```

---

## Building a Distributable Executable

```bash
pip install pyinstaller
pyinstaller build.spec
```

Output is in `dist/CaptionStudio/`.

**Windows:** Place `ffmpeg.exe` and `ffprobe.exe` in an `ffmpeg/` folder next
to `build.spec`, then uncomment the `added_binaries` block in `build.spec`.

**macOS:** Code-sign with `--codesign-identity` in the spec and notarise via
`xcrun altool` for Gatekeeper compatibility.

**Linux:** Ship ffmpeg via the package manager or bundle it manually alongside
the executable.

---

## Feature Summary

- **Drag-and-drop** video loading (MP4, MOV, MKV, AVI, WebM)
- **Language selector** — 90+ languages; skips auto-detection for better accuracy
- **Whisper model selector** — tiny → large-v3; cached locally for offline use
- **Real-time preview** — QVideoWidget with transport controls
- **Draggable caption position** — click-drag the overlay handle on the preview
- **Style controls** — system font or custom TTF/OTF, size, text colour,
  outline colour, outline width, karaoke highlight colour
- **Karaoke mode** — word-by-word highlighting during playback and export
- **Segment list** — click any segment to jump to it in the preview
- **Export** — composites captions over the source video using libx264 + AAC
- **Dark theme** — purpose-built dark UI; no system theme dependency

---

## Key Files to Customise

| File | What to change |
|---|---|
| `core/caption_model.py` → `CaptionStyle` | Default caption style values |
| `core/whisper_manager.py` → `AVAILABLE_MODELS` | Add / remove Whisper model variants |
| `ui/style_panel.py` | Add new style controls (shadow, background box, etc.) |
| `ui/caption_canvas.py` | Change handle appearance or add multi-line support |
| `build.spec` | Icon path, output name, ffmpeg binaries |
