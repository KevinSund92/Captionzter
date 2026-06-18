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

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
import tempfile
import threading
from typing import List

from PyQt6.QtCore import QObject, pyqtSignal

from core.caption_model import CaptionSegment, CaptionStyle, get_caption_blocks


# ── ffmpeg / ffprobe discovery ────────────────────────────────────────────────

def _cs_bin_dir() -> str:
    """Return the LOCALAPPDATA/CaptionStudio/bin/ directory (created by whisper_manager)."""
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        return os.path.join(local, "CaptionStudio", "bin")
    return ""


def _ffmpeg_bin() -> str:
    # Prefer system ffmpeg on PATH — likely a full build with all codecs (AV1, HEVC, etc.)
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, creationflags=_NO_WINDOW, timeout=5
        )
        if result.returncode == 0:
            return "ffmpeg"
    except Exception:
        pass
    # Fall back to the ffmpeg.exe alias in LOCALAPPDATA/CaptionStudio/bin/
    bin_dir = _cs_bin_dir()
    alias = os.path.join(bin_dir, "ffmpeg.exe")
    if os.path.isfile(alias):
        return alias
    # Alias not yet created — try to create it now via whisper_manager helper
    try:
        from core.whisper_manager import _ensure_ffmpeg_alias
        _ensure_ffmpeg_alias()
        if os.path.isfile(alias):
            return alias
    except Exception:
        pass
    # Last resort: ask imageio_ffmpeg directly (works in dev mode)
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


# ── Source probing ────────────────────────────────────────────────────────────

def _probe(source: str) -> dict:
    """Probe video using ffmpeg -i (no ffprobe — imageio_ffmpeg only ships ffmpeg)."""
    try:
        result = subprocess.run(
            [_ffmpeg_bin(), "-i", source],
            capture_output=True, text=True,
            creationflags=_NO_WINDOW,
        )
        text = result.stderr  # ffmpeg always writes info to stderr

        w, h = 0, 0
        m = re.search(r"(\d{2,5})x(\d{2,5})", text)
        if m:
            w, h = int(m.group(1)), int(m.group(2))

        bitrate = 0
        m = re.search(r"bitrate:\s*(\d+)\s*kb/s", text)
        if m:
            bitrate = int(m.group(1)) * 1000

        fps = 0.0
        m = re.search(r"([\d.]+)\s*fps", text)
        if m:
            fps = float(m.group(1))

        return {"width": w, "height": h, "bitrate": bitrate, "fps": fps}
    except Exception:
        return {"width": 0, "height": 0, "bitrate": 0, "fps": 0.0}


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


# ── Font resolution (bold handled via font file, not drawtext :bold flag) ────

# Common Windows bold/regular font pairs: (regular_suffix, bold_suffix)
_BOLD_STEM_SUFFIXES = [("", "bd"), ("", "b"), ("", "-Bold"), ("", "Bold")]

_SYSTEM_FONTS = {
    True: [   # bold
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\calibrib.ttf",
        r"C:\Windows\Fonts\verdanab.ttf",
    ],
    False: [  # regular
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\calibri.ttf",
        r"C:\Windows\Fonts\verdana.ttf",
    ],
}


def _resolve_effective_font(style: CaptionStyle) -> str:
    """
    Return the font file path for ffmpeg drawtext.
    Newer ffmpeg removed :bold=1, so bold is expressed by choosing a bold
    font file.  When the user selected a custom font we try to find its bold
    sibling; otherwise we fall back to a system bold/regular font.
    """
    bold = getattr(style, "bold", True)

    if style.font_path and os.path.isfile(style.font_path):
        if not bold:
            return style.font_path
        # Try common bold-variant filename patterns next to the chosen file.
        root, ext = os.path.splitext(style.font_path)
        for reg_sfx, bold_sfx in _BOLD_STEM_SUFFIXES:
            if root.lower().endswith(reg_sfx.lower()):
                candidate = root[: len(root) - len(reg_sfx)] + bold_sfx + ext
                if os.path.isfile(candidate):
                    return candidate
        # Bold sibling not found — use the regular file (better than failing).
        return style.font_path

    # No custom font: pick a system font that matches the bold setting.
    for path in _SYSTEM_FONTS[bold]:
        if os.path.isfile(path):
            return path
    # Last resort: try the other weight rather than returning nothing.
    for path in _SYSTEM_FONTS[not bold]:
        if os.path.isfile(path):
            return path
    return ""


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

    effective_font = _resolve_effective_font(style)
    font_arg = f":fontfile='{_escape_fontpath(effective_font)}'" if effective_font else ""

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

    style_anim      = getattr(style, "animation",      "none") or "none"
    style_anim_dur  = max(0.05, getattr(style, "anim_duration",  0.35))
    style_anim_int  = max(0.0,  getattr(style, "anim_intensity", 1.0))

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

    def _anim_x_expr(base_x: int, b_start: float, anim: str, dur: float, intensity: float) -> str:
        tr = f"(t-{b_start:.3f})"
        if anim == "shake":
            freq = round(50 + 20 * intensity)
            amp  = round(14 * intensity)
            shake = f"if(lt({tr},{dur:.3f}),sin({tr}*{freq})*{amp}*exp(-{tr}*6/{dur:.3f}),0)"
            return f"{base_x}+{shake}"
        return str(base_x)

    def _anim_y_expr(base_y: str, b_start: float, anim: str, total_line_h: float,
                     dur: float, intensity: float) -> str:
        tr = f"(t-{b_start:.3f})"
        if anim == "slide_in":
            slide_px = round((total_line_h + 40) * intensity)
            offset = f"if(lt({tr},{dur:.3f}),pow(1-{tr}/{dur:.3f},3)*{slide_px},0)"
            return f"{base_y}+{offset}"
        return base_y

    def _anim_alpha_expr(b_start: float, anim: str, dur: float, intensity: float) -> str:
        tr = f"(t-{b_start:.3f})"
        if anim == "pop":
            fade = min(dur * 0.5, 0.15 / max(0.1, intensity))
            return f"if(lt({tr},{fade:.3f}),{tr}/{fade:.3f},1)"
        return "1"

    def _emit_drawtext(text: str, color: str, x_expr: str, y_expr: str,
                       alpha_expr: str, t0: float, t1: float) -> None:
        esc = _escape_text(text)
        alpha_arg = f":alpha='{alpha_expr}'" if alpha_expr != "1" else ""
        parts.append(
            f"drawtext=text='{esc}'"
            f":fontsize={fontsize_px}:fontcolor={color}"
            f":borderw={olw}:bordercolor={ol}"
            f":x={x_expr}:y={y_expr}"
            f"{alpha_arg}{font_arg}"
            f":enable='between(t\\,{t0:.3f}\\,{t1:.3f})'"
        )

    def _emit_word(word: str, fg_color: str, hl_color,
                   x_start: int, baseline: int,
                   block_t0: float, block_t1: float,
                   word_t0: float, word_t1: float,
                   anim: str, total_h: float,
                   dur: float, intensity: float) -> None:
        alpha = _anim_alpha_expr(block_t0, anim, dur, intensity)

        if lsp == 0:
            xe  = _anim_x_expr(x_start, block_t0, anim, dur, intensity)
            ye  = _anim_y_expr(f"{baseline}-ascent", block_t0, anim, total_h, dur, intensity)
            _emit_drawtext(word, fg_color, xe, ye, alpha, block_t0, block_t1)
            if hl_color:
                _emit_drawtext(word, hl_color, xe, ye, "1", word_t0, word_t1)
        else:
            cx = x_start
            for ch in word:
                ch_w = round(_text_width(pil_font, ch, fontsize_px))
                xe   = _anim_x_expr(cx, block_t0, anim, dur, intensity)
                ye   = _anim_y_expr(f"{baseline}-ascent", block_t0, anim, total_h, dur, intensity)
                _emit_drawtext(ch, fg_color, xe, ye, alpha, block_t0, block_t1)
                if hl_color:
                    _emit_drawtext(ch, hl_color, xe, ye, "1", word_t0, word_t1)
                cx += ch_w + lsp

    parts = []
    for seg in segments:
        cx, cy = seg.position   if seg.position   else style.position
        s_align = seg.text_align if seg.text_align else getattr(style, "text_align", "center")
        seg_anim = (seg.animation if seg.animation is not None else style_anim) or "none"
        seg_dur  = seg.anim_duration  if seg.anim_duration  is not None else style_anim_dur
        seg_int  = seg.anim_intensity if seg.anim_intensity is not None else style_anim_int

        fixed_block_top = round((video_h - max_lines * line_h) * cy)

        blocks = get_caption_blocks(seg, style)
        for b_start, b_end, lines, tokens_per_line in blocks:

            has_karaoke = style.karaoke and any(tl for tl in tokens_per_line)

            total_block_h = len(lines) * line_h

            if not has_karaoke:
                if lsp == 0 and wsp == 0 and seg_anim == "none":
                    # ── Fast path: single multi-line drawtext ─────────────
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
                        f"{font_arg}"
                        f":enable='between(t\\,{b_start:.3f}\\,{b_end:.3f})'"
                    )
                else:
                    # ── Spaced/animated path: per-word drawtexts ──────────
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
                                       b_start, b_end, b_start, b_end,
                                       seg_anim, total_block_h, seg_dur, seg_int)
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
                                   b_start, b_end, token.start, token.end,
                                   seg_anim, total_block_h, seg_dur, seg_int)
                        x += word_w + gap

    return ",".join(parts) if parts else "null"


# ── Worker ────────────────────────────────────────────────────────────────────

class ExportWorker(QObject):
    progress = pyqtSignal(int)
    status   = pyqtSignal(str)
    finished = pyqtSignal(str)
    error    = pyqtSignal(str)

    def __init__(self, source_path, output_path, segments, style,
                 res_override=None, fps_override=None, bitrate_override=None,
                 parent=None):
        super().__init__(parent)
        self.source_path      = source_path
        self.output_path      = output_path
        self.segments         = segments
        self.style            = style
        self.res_override     = res_override    # (w, h) or None
        self.fps_override     = fps_override    # float or None
        self.bitrate_override = bitrate_override  # int (bps) or None
        self._cancelled       = False
        self._proc            = None

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
            src_w, src_h = info["width"], info["height"]
            w, h = self.res_override if self.res_override else (src_w, src_h)
            bitrate = self.bitrate_override if self.bitrate_override else info["bitrate"]
            vbr     = min(max(bitrate, 1_000_000), 50_000_000)
            self.status.emit(f"Source: {src_w}×{src_h}  →  {w}×{h}  {vbr // 1000} kbps")
            self.progress.emit(5)

            fg_text = _build_filtergraph(self.segments, self.style, w, h)
            # Prepend scale filter if resolution changed
            if self.res_override and self.res_override != (src_w, src_h):
                fg_text = f"scale={w}:{h}," + fg_text

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
            ]
            # FPS override
            if self.fps_override:
                cmd += ["-r", str(self.fps_override)]
            cmd += [
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
                creationflags=_NO_WINDOW,
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
