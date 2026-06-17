# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bat
# First-time setup (Windows) — creates .venv, installs deps, downloads Whisper small model
setup.bat

# Run
run.bat
# or
.venv\Scripts\python main.py
```

## Architecture

### Layer split: `core/` vs `ui/`

`core/` has **no Qt imports** — safe to import headless for tests or export jobs.  
`ui/` owns all PyQt6 widgets and wires everything together.

### Data flow

```
WhisperTranscriber (QThread)
  → segment_ready(dict) → MainWindow._on_segment_ready()
      → CaptionSegment appended to self._segments
          → TimelineWidget.set_segments()
          → QTimer (50 ms) → _sync_caption_overlay()
              → CaptionCanvas.set_display() / set_animation()
```

Export:  
`MainWindow._export()` → `ExportWorker(QThread)` → `_build_filtergraph()` → ffmpeg subprocess with a temp `.txt` filtergraph file (avoids Windows cmd-line length limits).

### Key data classes (`core/caption_model.py`)

- **`CaptionSegment`** — one Whisper segment: `text`, `start`, `end`, `words: List[WordToken]`, plus per-segment overrides: `position`, `text_align`, `animation`.
- **`CaptionStyle`** — global style defaults: `font_path`, `font_size`, `bold`, `color`, `outline_*`, `highlight_color`, `karaoke`, `words_per_line`, `rows_visible`, `text_align`, `position`, `letter_spacing`, `word_spacing`, `animation`.
- **`get_caption_blocks(seg, style)`** — splits one segment into `(start, end, lines, tokens_per_line)` blocks based on `words_per_line × rows_visible`. This is called in both the preview timer and the export filtergraph builder — keep them consistent.

### Scope model (ALL vs Selected)

`StylePanel` emits `allToggled(bool)`. When ALL is active, changes to position/alignment/animation propagate to every `CaptionSegment`. When a timeline chip is clicked, `segmentSelected(idx)` deactivates ALL mode and scopes changes to that segment only. The ALL button press clears the timeline selection. No segment selected + no ALL = status bar message only (no silent fallback).

### Caption canvas (`ui/caption_canvas.py`)

`CaptionCanvas` is a `QGraphicsObject` inside a `QGraphicsScene` that also holds the `QGraphicsVideoItem`. The item's scene position **is** the anchor point; `text_align` controls which edge of text sits at the anchor (left/center/right). Animation is applied via `painter.save() / scale() / translate() / restore()` in `paint()`, driven by a 16 ms `QTimer` that runs only during the animation window.

### Export filtergraph (`core/export_engine.py`)

- Font size: Qt pt → ffmpeg px via `× 96/72`.
- Vertical position: `y = fixed_block_top + pil_asc - ascent` (ffmpeg `ascent` variable). `fixed_block_top` is computed once per segment using `rows_visible` (not the actual line count) to prevent vertical jumping between blocks.
- Bold: expressed by choosing a bold font file (`_resolve_effective_font`), not `:bold=1` (removed in newer ffmpeg).
- Letter spacing: no ffmpeg equivalent — exploded into per-character `drawtext` calls.
- Animations: ffmpeg expression strings (`alpha=`, `x=`, `y=` with `t` variable). Pop-in uses alpha fade; slide-in uses `y` offset with ease-out; shake uses decaying sine on `x`.
- Fast path (no lsp/wsp/animation): single multi-line `drawtext` per block. Falls through to per-word path when any of those are active.

### Whisper / language detection

`WhisperTranscriber` and `LanguageDetector` both live in `core/whisper_manager.py` and are moved to `QThread` before `run()` is called. `LanguageDetector` uses the `tiny` model on the first 30 s of audio; it fires on every video load and updates the "Spoken in video" combo. Models are cached in `<project>/models/whisper/` (set via `WHISPER_CACHE` env var before `import whisper`).

### Theme

Dark palette is set via `QPalette` in `main.py` (not stylesheet) so Fusion renders native widgets (spinboxes, checkboxes) correctly with their own arrows/indicators. The main stylesheet in `MainWindow._apply_dark_theme()` handles layout, borders, and colours for custom/composite widgets only. **Do not add `QSpinBox` rules to the stylesheet** — it breaks Qt's internal subcontrol rendering.

### Adding a new platform safe area

Edit `core/safe_area_config.py` — add an entry to `PLATFORMS`. No other file needs changing.
