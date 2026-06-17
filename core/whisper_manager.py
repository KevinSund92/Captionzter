"""
core/whisper_manager.py
-----------------------
Handles local Whisper model download, caching, and transcription.

PyInstaller strategy
--------------------
Whisper downloads models to ~/.cache/whisper by default, which works at
runtime but the models are NOT embedded in the bundle (they are large, up to
~3 GB for "large-v3").  Instead we:

  1. Redirect the cache to  <APP_DIR>/models/whisper/  so the model travels
     *next to* the installed executable — ideal for offline use.
  2. On first launch the model is downloaded once over the internet.
  3. On every subsequent launch (online or offline) it loads from the local
     cache without any network access.

This keeps the installer small while still supporting fully offline operation
after the initial model download.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal

# ---------------------------------------------------------------------------
# Model cache directory — next to the executable when frozen, project-root
# when running from source.
# ---------------------------------------------------------------------------
_APP_DIR = Path(os.environ.get("CAPTION_STUDIO_APP_DIR", Path(__file__).parent.parent))
WHISPER_CACHE_DIR = _APP_DIR / "models" / "whisper"
WHISPER_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Tell Whisper (and the underlying huggingface hub) where to cache models.
os.environ["WHISPER_CACHE"] = str(WHISPER_CACHE_DIR)
# Also point XDG_CACHE_HOME so openai-whisper's torch.hub.load finds it.
os.environ["XDG_CACHE_HOME"] = str(WHISPER_CACHE_DIR)

AVAILABLE_MODELS = [
    ("tiny",    "~39 MB  — fastest, lowest accuracy"),
    ("base",    "~74 MB  — fast, decent accuracy"),
    ("small",   "~244 MB — good balance (recommended)"),
    ("medium",  "~769 MB — high accuracy, slower"),
    ("large-v3","~1.5 GB — best accuracy, requires 8 GB+ RAM"),
]

WHISPER_LANGUAGES = [
    ("Auto-detect", None),
    ("Afrikaans",   "af"), ("Albanian",  "sq"), ("Amharic",    "am"),
    ("Arabic",      "ar"), ("Armenian",  "hy"), ("Assamese",   "as"),
    ("Azerbaijani", "az"), ("Bashkir",   "ba"), ("Basque",     "eu"),
    ("Belarusian",  "be"), ("Bengali",   "bn"), ("Bosnian",    "bs"),
    ("Breton",      "br"), ("Bulgarian", "bg"), ("Burmese",    "my"),
    ("Catalan",     "ca"), ("Chinese",   "zh"), ("Croatian",   "hr"),
    ("Czech",       "cs"), ("Danish",    "da"), ("Dutch",      "nl"),
    ("English",     "en"), ("Estonian",  "et"), ("Faroese",    "fo"),
    ("Finnish",     "fi"), ("French",    "fr"), ("Galician",   "gl"),
    ("Georgian",    "ka"), ("German",    "de"), ("Greek",      "el"),
    ("Gujarati",    "gu"), ("Haitian",   "ht"), ("Hausa",      "ha"),
    ("Hawaiian",    "haw"),("Hebrew",    "he"), ("Hindi",      "hi"),
    ("Hungarian",   "hu"), ("Icelandic", "is"), ("Indonesian", "id"),
    ("Italian",     "it"), ("Japanese",  "ja"), ("Javanese",   "jw"),
    ("Kannada",     "kn"), ("Kazakh",    "kk"), ("Khmer",      "km"),
    ("Korean",      "ko"), ("Lao",       "lo"), ("Latin",      "la"),
    ("Latvian",     "lv"), ("Lingala",   "ln"), ("Lithuanian", "lt"),
    ("Macedonian",  "mk"), ("Malagasy",  "mg"), ("Malay",      "ms"),
    ("Malayalam",   "ml"), ("Maltese",   "mt"), ("Maori",      "mi"),
    ("Marathi",     "mr"), ("Mongolian", "mn"), ("Nepali",     "ne"),
    ("Norwegian",   "no"), ("Occitan",   "oc"), ("Pashto",     "ps"),
    ("Persian",     "fa"), ("Polish",    "pl"), ("Portuguese", "pt"),
    ("Punjabi",     "pa"), ("Romanian",  "ro"), ("Russian",    "ru"),
    ("Sanskrit",    "sa"), ("Serbian",   "sr"), ("Shona",      "sn"),
    ("Sindhi",      "sd"), ("Sinhala",   "si"), ("Slovak",     "sk"),
    ("Slovenian",   "sl"), ("Somali",    "so"), ("Spanish",    "es"),
    ("Sundanese",   "su"), ("Swahili",   "sw"), ("Swedish",    "sv"),
    ("Tagalog",     "tl"), ("Tajik",     "tg"), ("Tamil",      "ta"),
    ("Tatar",       "tt"), ("Telugu",    "te"), ("Thai",       "th"),
    ("Tibetan",     "bo"), ("Turkish",   "tr"), ("Turkmen",    "tk"),
    ("Ukrainian",   "uk"), ("Urdu",      "ur"), ("Uzbek",      "uz"),
    ("Vietnamese",  "vi"), ("Welsh",     "cy"), ("Yiddish",    "yi"),
    ("Yoruba",      "yo"),
]


class WhisperTranscriber(QObject):
    """
    Worker object (moves to a QThread) that loads a Whisper model and
    transcribes a file, emitting progress and results via Qt signals.
    """

    progress   = pyqtSignal(str)          # status message
    segment_ready = pyqtSignal(dict)      # one {start, end, text, words} dict
    finished   = pyqtSignal(list)         # full list of segments
    error      = pyqtSignal(str)

    def __init__(
        self,
        video_path: str,
        model_name: str = "small",
        language: Optional[str] = None,   # None → auto-detect
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.video_path = video_path
        self.model_name = model_name
        self.language   = language
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    # ------------------------------------------------------------------
    # Main worker method — call this from a QThread
    # ------------------------------------------------------------------
    def run(self) -> None:
        try:
            import whisper  # local import so the app starts without whisper loaded

            self.progress.emit(f"Loading Whisper '{self.model_name}' model …")
            model = whisper.load_model(
                self.model_name,
                download_root=str(WHISPER_CACHE_DIR),
            )

            if self._cancelled:
                return

            self.progress.emit("Transcribing — this may take a moment …")

            result = model.transcribe(
                self.video_path,
                language=self.language,
                word_timestamps=True,   # needed for karaoke mode
                verbose=False,
            )

            if self._cancelled:
                return

            segments = []
            for seg in result.get("segments", []):
                if self._cancelled:
                    break
                item = {
                    "start": seg["start"],
                    "end":   seg["end"],
                    "text":  seg["text"].strip(),
                    "words": [
                        {"word": w["word"], "start": w["start"], "end": w["end"]}
                        for w in seg.get("words", [])
                    ],
                }
                segments.append(item)
                self.segment_ready.emit(item)

            self.finished.emit(segments)

        except Exception as exc:
            self.error.emit(str(exc))


def model_is_cached(model_name: str) -> bool:
    """Return True if the named Whisper model already lives in the cache."""
    # openai-whisper stores models as  <cache>/base.pt, small.pt, etc.
    expected = WHISPER_CACHE_DIR / f"{model_name}.pt"
    return expected.exists()
