"""
core/export_engine.py
---------------------
Composites captions over video using ffmpeg via subprocess.
Filtergraph is written to a temp file to avoid Windows command-line length limits.

Font-size matching with preview
--------------------------------
The QGraphicsScene uses the video's native pixel dimensions as scene units.
QFont point sizes are rendered at ~96 Dpi in that space, so 1 pt ≈ 1.333 px.
We apply the same ratio to ffmpeg's fontsize so the text is the same physical
size in the exported video as it appears in the preview.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
from typing import List

from PyQt6.QtCore import QObject, pyqtSignal

from core.caption_model import CaptionSegment, CaptionStyle, get_caption_blocks


# ── ffmpeg / ffprobe discovery ────────────────────────────────────────────────

def _ffmpeg_bin() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def _ffprobe_bin() -> str:
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        for name in ("ffprobe.exe", "ffprobe"):
            probe = os.path.join(os.path.dirname(exe), name)
            if os.path.exists(probe):
                return probe
    except Exception:
        pass
    return "ffprobe"


# ── Source probing ────────────────────────────────────────────────────────────

def _probe(source: str) -> dict:
    cmd = [
        _ffprobe_bin(), "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        source,
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
        data = json.loads(out)
        video = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "video"), {}
        )
        w = int(video.get("width", 1920))
        h = int(video.get("height", 1080))
        bitrate = int(
            video.get("bit_rate")
            or data.get("format", {}).get("bit_rate")
            or 5_000_000
        )
        return {"width": w, "height": h, "bitrate": bitrate}
    except Exception:
        return {"width": 1920, "height": 1080, "bitrate": 5_000_000}


# ── Text / path escaping ──────────────────────────────────────────────────────

def _escape_text(text: str) -> str:
    text = text.replace("\\", "\\\\")
    text = text.replace("'",  "’")   # typographic apostrophe avoids quoting issues
    text = text.replace(":",  "\\:")
    text = text.replace(",",  "\\,")
    text = text.replace("[",  "\\[")
    text = text.replace("]",  "\\]")
    return text


def _escape_fontpath(path: str) -> str:
    path = path.replace("\\", "/")
    # Escape the Windows drive-letter colon: C:/ → C\:/
    if len(path) >= 2 and path[1] == ":":
        path = path[0] + "\\:" + path[2:]
    return path


def _ffmpeg_color(hex_color: str) -> str:
    """Return colour in the format ffmpeg drawtext expects: #RRGGBB."""
    h = hex_color.strip()
    return h if h.startswith("#") else f"#{h}"


# ── Font metrics via Pillow (for karaoke word positioning) ────────────────────

def _make_pil_font(style: CaptionStyle, fontsize_px: int):
    """Return a Pillow ImageFont, or None if Pillow is unavailable."""
    try:
        from PIL import ImageFont
        if style.font_path and os.path.isfile(style.font_path):
            return ImageFont.truetype(style.font_path, fontsize_px)
        # Try common system Arial locations on Windows
        for path in (
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\arial.ttf",
            "/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf",
        ):
            if os.path.isfile(path):
                return ImageFont.truetype(path, fontsize_px)
        return ImageFont.load_default()
    except Exception:
        return None


def _text_width(pil_font, text: str, fontsize_px: int) -> float:
    """Return pixel width of text using Pillow, falling back to estimation."""
    if pil_font is None:
        return len(text) * fontsize_px * 0.55
    try:
        return pil_font.getlength(text)
    except Exception:
        try:
            bb = pil_font.getbbox(text)
            return bb[2] - bb[0]
        except Exception:
            return len(text) * fontsize_px * 0.55


# ── Filtergraph builder ───────────────────────────────────────────────────────

def _build_filtergraph(segments: List[CaptionSegment], style: CaptionStyle,
                       video_w: int = 1920, video_h: int = 1080) -> str:
    """
    Build a drawtext filtergraph matching the preview:
    - Same block splits (words_per_line × rows_visible)
    - Font size: Qt pt → ffmpeg px (× 96/72)
    - Karaoke: base line in fg + per-word overlay in highlight colour
    """
    bold        = getattr(style, "bold", True)
    lsp         = getattr(style, "letter_spacing", 0)   # extra px between chars
    wsp         = getattr(style, "word_spacing",   0)   # extra px between words

    font_arg = ""
    if style.font_path and os.path.isfile(style.font_path):
        font_arg = f":fontfile='{_escape_fontpath(style.font_path)}'"

    fontsize_px = max(1, round(style.font_size * 96 / 72))

    fg  = _ffmpeg_color(style.color)
    hl  = _ffmpeg_color(style.highlight_color)
    ol  = _ffmpeg_color(style.outline_color)
    olw = style.outline_width

    # Always need Pillow font metrics so we can pin all blocks to the same
    # vertical anchor regardless of how many lines a given block has.
    pil_font = _make_pil_font(style, fontsize_px)

    _LINE_SPACING = 4   # must stay in sync with non-karaoke drawtext line_spacing
    try:
        pil_asc, pil_desc = pil_font.getmetrics()
        line_h = float(pil_asc + pil_desc + _LINE_SPACING)
    except Exception:
        pil_asc  = fontsize_px
        line_h   = fontsize_px * 1.2 + _LINE_SPACING

    # Anchor all blocks against the maximum expected row count so the caption
    # area never shifts up/down between blocks (e.g. last block may have fewer
    # lines than rows_visible, but its top stays at the same pixel).
    max_lines = max(1, style.rows_visible)

    bold_arg = ":bold=1" if bold else ""

    def _line_width(text: str) -> float:
        """Measure a full line including letter/word spacing."""
        ws = text.split(" ")
        w  = sum(_text_width_lsp(pil_font, w, fontsize_px, lsp) for w in ws)
        sp_w = _text_width(pil_font, " ", fontsize_px) + wsp
        return w + sp_w * max(0, len(ws) - 1)

    def _text_width_lsp(font, text: str, size: int, letter_sp: int) -> float:
        if letter_sp == 0:
            return _text_width(font, text, size)
        total = sum(_text_width(font, ch, size) + letter_sp for ch in text)
        return max(0.0, total - letter_sp)

    def _emit_drawtext(text: str, color: str, x: int, baseline: int,
                       t0: float, t1: float) -> None:
        """Emit one drawtext filter, baseline-anchored."""
        esc = _escape_text(text)
        parts.append(
            f"drawtext=text='{esc}'"
            f":fontsize={fontsize_px}:fontcolor={color}"
            f":borderw={olw}:bordercolor={ol}"
            f":x={x}:y={baseline}-ascent"
            f"{bold_arg}{font_arg}"
            f":enable='between(t\\,{t0:.3f}\\,{t1:.3f})'"
        )

    def _emit_word(word: str, fg_color: str, hl_color,
                   x_start: int, baseline: int,
                   block_t0: float, block_t1: float,
                   word_t0: float, word_t1: float) -> None:
        """
        Draw one word.  If lsp > 0, explode into per-character drawtexts so
        letter spacing is applied exactly as in the preview.
        hl_color=None means plain (no karaoke) mode.
        """
        if lsp == 0:
            _emit_drawtext(word, fg_color, x_start, baseline, block_t0, block_t1)
            if hl_color:
                _emit_drawtext(word, hl_color, x_start, baseline, word_t0, word_t1)
        else:
            cx = x_start
            for ch in word:
                ch_w = round(_text_width(pil_font, ch, fontsize_px))
                _emit_drawtext(ch, fg_color, cx, baseline, block_t0, block_t1)
                if hl_color:
                    _emit_drawtext(ch, hl_color, cx, baseline, word_t0, word_t1)
                cx += ch_w + lsp

    parts = []
    for seg in segments:
        cx, cy = seg.position   if seg.position   else style.position
        s_align = seg.text_align if seg.text_align else getattr(style, "text_align", "center")

        fixed_block_top = round((video_h - max_lines * line_h) * cy)

        blocks = get_caption_blocks(seg, style)
        for b_start, b_end, lines, tokens_per_line in blocks:

            has_karaoke = style.karaoke and any(tl for tl in tokens_per_line)

            if not has_karaoke:
                if lsp == 0 and wsp == 0:
                    # ── Fast path: single multi-line drawtext ─────────────
                    # Use baseline_px - ascent so vertical position matches
                    # the per-word spaced path exactly.
                    baseline_px = fixed_block_top + round(pil_asc)
                    raw_text = r"\n".join(_escape_text(ln) for ln in lines)
                    if s_align == "left":
                        x_expr = f"w*{cx:.4f}"
                    elif s_align == "right":
                        x_expr = f"w*{cx:.4f}-text_w"
                    else:
                        x_expr = f"w*{cx:.4f}-text_w/2"
                    parts.append(
                        f"drawtext=text='{raw_text}'"
                        f":fontsize={fontsize_px}"
                        f":fontcolor={fg}:borderw={olw}:bordercolor={ol}"
                        f":x={x_expr}:y={baseline_px}-ascent:line_spacing={_LINE_SPACING}"
                        f"{bold_arg}{font_arg}"
                        f":enable='between(t\\,{b_start:.3f}\\,{b_end:.3f})'"
                    )
                else:
                    # ── Spaced path: per-word (per-char if lsp) drawtexts ──
                    for line_idx, line_text in enumerate(lines):
                        baseline_px = fixed_block_top + round(pil_asc + line_idx * line_h)
                        lw = _line_width(line_text)
                        if s_align == "left":
                            lx = round(video_w * cx)
                        elif s_align == "right":
                            lx = round(video_w * cx - lw)
                        else:
                            lx = round(video_w * cx - lw / 2)
                        x = float(lx)
                        words = line_text.split(" ")
                        for w_idx, word in enumerate(words):
                            _emit_word(word, fg, None, round(x), baseline_px,
                                       b_start, b_end, b_start, b_end)
                            x += _text_width_lsp(pil_font, word, fontsize_px, lsp)
                            if w_idx < len(words) - 1:
                                x += _text_width(pil_font, " ", fontsize_px) + wsp
            else:
                # ── Karaoke block ─────────────────────────────────────────
                for line_idx, (line_text, line_tokens) in enumerate(
                    zip(lines, tokens_per_line)
                ):
                    baseline_px = fixed_block_top + round(pil_asc + line_idx * line_h)
                    lw = _line_width(line_text)
                    if s_align == "left":
                        line_x = round(video_w * cx)
                    elif s_align == "right":
                        line_x = round(video_w * cx - lw)
                    else:
                        line_x = round(video_w * cx - lw / 2)

                    x = float(line_x)
                    for t_idx, token in enumerate(line_tokens):
                        word   = token.word.strip()
                        word_w = _text_width_lsp(pil_font, word, fontsize_px, lsp)
                        gap    = _text_width(pil_font, " ", fontsize_px) + wsp \
                                 if t_idx < len(line_tokens) - 1 else 0
                        _emit_word(word, fg, hl, round(x), baseline_px,
                                   b_start, b_end, token.start, token.end)
                        x += word_w + gap

    return ",".join(parts) if parts else "null"


# ── Worker ────────────────────────────────────────────────────────────────────

class ExportWorker(QObject):
    progress = pyqtSignal(int)
    status   = pyqtSignal(str)
    finished = pyqtSignal(str)
    error    = pyqtSignal(str)

    def __init__(self, source_path, output_path, segments, style, parent=None):
        super().__init__(parent)
        self.source_path = source_path
        self.output_path = output_path
        self.segments    = segments
        self.style       = style
        self._cancelled  = False
        self._proc       = None

    def cancel(self) -> None:
        self._cancelled = True
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def run(self) -> None:
        filter_file = None
        try:
            self.status.emit("Probing source video …")
            self.progress.emit(2)

            info    = _probe(self.source_path)
            w, h    = info["width"], info["height"]
            bitrate = info["bitrate"]
            vbr     = min(max(bitrate, 1_000_000), 50_000_000)
            self.status.emit(f"Source: {w}×{h}  {vbr // 1000} kbps")
            self.progress.emit(5)

            fg_text = _build_filtergraph(self.segments, self.style, w, h)

            # Write filter to temp file — avoids Windows 32 k command-line limit
            fd, filter_file = tempfile.mkstemp(suffix=".txt", prefix="cs_filter_")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(fg_text)

            self.progress.emit(8)

            ffmpeg = _ffmpeg_bin()
            cmd = [
                ffmpeg, "-y",
                "-i", self.source_path,
                "-filter_script:v", filter_file,
                "-c:v", "libx264",
                "-b:v", str(vbr),
                "-maxrate", str(int(vbr * 1.5)),
                "-bufsize", str(vbr * 2),
                "-preset", "fast",
                "-c:a", "copy",
                "-progress", "pipe:1",
                "-nostats",
                self.output_path,
            ]

            self.status.emit("Encoding — please wait …")
            self.progress.emit(10)

            duration_s = self.segments[-1].end if self.segments else 0.0

            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            stderr_lines: list = []
            threading.Thread(
                target=lambda: [stderr_lines.append(l) for l in self._proc.stderr],
                daemon=True,
            ).start()

            out_time_re = re.compile(r"out_time_ms=(\d+)")
            for line in self._proc.stdout:
                if self._cancelled:
                    self._proc.terminate()
                    return
                m = out_time_re.search(line)
                if m and duration_s:
                    elapsed = int(m.group(1)) / 1_000_000
                    pct = 10 + int(min(elapsed / duration_s, 1.0) * 88)
                    self.progress.emit(pct)
                    self.status.emit(f"Encoding … {elapsed:.1f}s / {duration_s:.1f}s")

            self._proc.wait()

            if self._proc.returncode != 0:
                stderr = "".join(stderr_lines)
                self.error.emit(
                    f"ffmpeg failed (code {self._proc.returncode}):\n{stderr[-2000:]}"
                )
                return

            self.progress.emit(100)
            self.finished.emit(self.output_path)

        except FileNotFoundError:
            self.error.emit(
                "ffmpeg not found.\n\nInstall:  pip install imageio-ffmpeg"
            )
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            if filter_file and os.path.exists(filter_file):
                try:
                    os.unlink(filter_file)
                except OSError:
                    pass
