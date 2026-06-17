"""
core/caption_model.py
---------------------
Pure-Python data classes for captions + a MoviePy/PIL render helper.
No Qt imports — this module is safe to run headless (e.g. during export).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class WordToken:
    word:  str
    start: float   # seconds
    end:   float


@dataclass
class CaptionSegment:
    text:     str
    start:    float
    end:      float
    words:      List[WordToken]              = field(default_factory=list)
    position:   Optional[Tuple[float,float]] = None   # (nx, ny) override; None = use style
    text_align: Optional[str]               = None   # "left"|"center"|"right"; None = use style

    @classmethod
    def from_whisper_dict(cls, d: dict) -> "CaptionSegment":
        words = [WordToken(w["word"], w["start"], w["end"]) for w in d.get("words", [])]
        return cls(text=d["text"], start=d["start"], end=d["end"], words=words)


@dataclass
class CaptionStyle:
    font_path:      Optional[str] = None   # None → PIL default
    font_size:      int           = 48
    color:          str           = "#FFFFFF"
    outline_color:  str           = "#000000"
    outline_width:  int           = 2
    highlight_color: str          = "#FFD700"   # karaoke highlight
    karaoke:        bool          = False
    words_per_line: int           = 0                   # 0 = no limit
    rows_visible:   int           = 1                   # rows shown at once
    text_align:     str           = "center"            # "left" | "center" | "right"
    position:       Tuple[float, float] = (0.5, 0.85)  # normalized (x, y)
    bold:           bool          = True
    letter_spacing: int           = 0    # extra pixels between characters
    word_spacing:   int           = 0    # extra pixels between words

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "CaptionStyle":
        obj = cls()
        for k, v in d.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        return obj


# ---------------------------------------------------------------------------
# MoviePy render helper
# ---------------------------------------------------------------------------

def get_caption_blocks(
    seg: "CaptionSegment",
    style: "CaptionStyle",
) -> List[Tuple[float, float, List[str], List[List["WordToken"]]]]:
    """
    Split one segment into display blocks of (rows_visible) lines each.
    Returns list of (start_s, end_s, lines, tokens_per_line).

    With word timestamps (from karaoke mode) each block's timing is exact.
    Without word timestamps the segment duration is split equally.
    """
    wpl = style.words_per_line
    rv  = max(1, style.rows_visible)

    if not wpl:
        # No line splitting — whole segment is a single block
        return [(seg.start, seg.end, [seg.text], [seg.words])]

    words_per_block = wpl * rv

    if seg.words:
        tokens = seg.words
        blocks = []
        for i in range(0, max(len(tokens), 1), words_per_block):
            group = tokens[i:i + words_per_block]
            if not group:
                break
            lines, t_lines = [], []
            for j in range(0, len(group), wpl):
                lt = group[j:j + wpl]
                lines.append(" ".join(t.word.strip() for t in lt))
                t_lines.append(list(lt))
            blocks.append((group[0].start, group[-1].end, lines, t_lines))
        return blocks or [(seg.start, seg.end, [seg.text], [seg.words])]

    # No word tokens — split text and divide time equally
    words = seg.text.split()
    if not words:
        return [(seg.start, seg.end, [""], [[]])]
    n_blocks = max(1, (len(words) + words_per_block - 1) // words_per_block)
    dt = (seg.end - seg.start) / n_blocks
    blocks = []
    for i in range(n_blocks):
        bw = words[i * words_per_block:(i + 1) * words_per_block]
        lines = [" ".join(bw[j:j + wpl]) for j in range(0, len(bw), wpl)]
        t0 = seg.start + i * dt
        blocks.append((t0, t0 + dt, lines, [[] for _ in lines]))
    return blocks


def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def make_caption_clip(
    segment: CaptionSegment,
    style: CaptionStyle,
    video_size: Tuple[int, int],
    current_time: Optional[float] = None,   # for karaoke highlight
):
    """
    Returns a MoviePy TextClip (or ImageClip for karaoke) timed to the
    segment's start/end.  Requires moviepy and Pillow.
    """
    from moviepy.editor import ImageClip
    from PIL import Image, ImageDraw, ImageFont

    W, H = video_size
    cx, cy = style.position
    px, py = int(cx * W), int(cy * H)

    # ── Choose font ──────────────────────────────────────────────────────
    try:
        if style.font_path and os.path.isfile(style.font_path):
            font = ImageFont.truetype(style.font_path, style.font_size)
        else:
            font = ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    # ── Build text / karaoke image ───────────────────────────────────────
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    text = segment.text
    fg   = _hex_to_rgb(style.color)
    ol   = _hex_to_rgb(style.outline_color)
    hl   = _hex_to_rgb(style.highlight_color)

    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = px - tw // 2
    ty = py - th // 2

    # Outline
    for dx in range(-style.outline_width, style.outline_width + 1):
        for dy in range(-style.outline_width, style.outline_width + 1):
            if dx == 0 and dy == 0:
                continue
            draw.text((tx + dx, ty + dy), text, font=font, fill=(*ol, 255))

    if style.karaoke and current_time is not None and segment.words:
        # Render each word with its own colour
        x_cursor = tx
        for token in segment.words:
            w_color = hl if token.start <= current_time < token.end else fg
            draw.text((x_cursor, ty), token.word, font=font, fill=(*w_color, 255))
            wb = draw.textbbox((0, 0), token.word, font=font)
            x_cursor += wb[2] - wb[0]
    else:
        draw.text((tx, ty), text, font=font, fill=(*fg, 255))

    arr = __import__("numpy").array(img)
    clip = (
        ImageClip(arr, ismask=False)
        .set_start(segment.start)
        .set_end(segment.end)
        .set_duration(segment.end - segment.start)
    )
    return clip
