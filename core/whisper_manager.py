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

# ---------------------------------------------------------------------------
# Ensure ffmpeg is on PATH so Whisper's load_audio() can find it.
# imageio-ffmpeg ships a portable binary but uses a versioned name such as
# ffmpeg-win-x86_64-v7.1.exe.  Whisper calls "ffmpeg" by name, so we create
# a plain ffmpeg.exe alias via hard link in <app>/bin/ and add that to PATH.
# Hard links require no admin rights and use no extra disk space on NTFS.
# ---------------------------------------------------------------------------
def _ensure_ffmpeg_on_path() -> None:
    try:
        import imageio_ffmpeg
        src = Path(imageio_ffmpeg.get_ffmpeg_exe())
        if not src.exists():
            return

        bin_dir = _APP_DIR / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        dest = bin_dir / "ffmpeg.exe"

        if not dest.exists():
            try:
                os.link(src, dest)       # hard link — instant, zero extra disk space
            except OSError:
                import shutil
                shutil.copy2(src, dest)  # fallback: one-time ~100 MB copy

        path_parts = os.environ.get("PATH", "").split(os.pathsep)
        if str(bin_dir) not in path_parts:
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass  # fall through — workers emit a clear error if ffmpeg is still missing

_ensure_ffmpeg_on_path()

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
    transcribes (and optionally translates) a file.

    Translation strategy
    --------------------
    - subtitle_lang == None or same as spoken_lang  → plain transcribe
    - subtitle_lang == "en" and spoken != "en"       → Whisper translate task
      (Whisper has built-in high-quality English translation)
    - any other target language                      → transcribe first, then
      post-translate each segment with deep_translator (GoogleTranslator).
      Requires:  pip install deep-translator
    """

    progress      = pyqtSignal(str)
    segment_ready = pyqtSignal(dict)
    finished      = pyqtSignal(list)
    error         = pyqtSignal(str)

    def __init__(
        self,
        video_path:    str,
        model_name:    str           = "small",
        language:      Optional[str] = None,   # spoken language; None = auto-detect
        subtitle_lang: Optional[str] = None,   # target subtitle language; None = same as spoken
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.video_path    = video_path
        self.model_name    = model_name
        self.language      = language
        self.subtitle_lang = subtitle_lang
        self._cancelled    = False

    def cancel(self) -> None:
        self._cancelled = True

    # ------------------------------------------------------------------
    def run(self) -> None:
        try:
            try:
                import whisper
            except ModuleNotFoundError:
                self.error.emit(
                    "openai-whisper is not installed.\n\n"
                    "Run the following commands in your terminal:\n\n"
                    "  pip install torch torchvision torchaudio "
                    "--index-url https://download.pytorch.org/whl/cpu\n"
                    "  pip install -r requirements.txt\n\n"
                    "Then restart CaptionStudio."
                )
                return

            # Decide Whisper task
            same_lang = (
                self.subtitle_lang is None
                or self.subtitle_lang == self.language
                or (self.language is None and self.subtitle_lang is None)
            )
            whisper_to_english = (
                not same_lang
                and self.subtitle_lang == "en"
            )
            need_post_translate = (
                not same_lang
                and not whisper_to_english
            )

            task = "translate" if whisper_to_english else "transcribe"

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
                task=task,
                word_timestamps=True,
                verbose=False,
            )

            if self._cancelled:
                return

            # ── Optional post-translation ──────────────────────────────
            translator = None
            if need_post_translate and self.subtitle_lang:
                try:
                    from deep_translator import GoogleTranslator
                    src = self.language or "auto"
                    translator = GoogleTranslator(source=src, target=self.subtitle_lang)
                    self.progress.emit(
                        f"Translating to '{self.subtitle_lang}' via Google Translate …"
                    )
                except ImportError:
                    self.error.emit(
                        "deep-translator is not installed.\n\n"
                        "Run:  pip install deep-translator\n\n"
                        "Falling back to original language."
                    )

            segments = []
            for seg in result.get("segments", []):
                if self._cancelled:
                    break
                text = seg["text"].strip()
                if translator:
                    try:
                        text = translator.translate(text) or text
                    except Exception:
                        pass   # keep original on error
                item = {
                    "start": seg["start"],
                    "end":   seg["end"],
                    "text":  text,
                    "words": [
                        {"word": w["word"], "start": w["start"], "end": w["end"]}
                        for w in seg.get("words", [])
                    ],
                }
                segments.append(item)
                self.segment_ready.emit(item)

            self.finished.emit(segments)

        except FileNotFoundError:
            self.error.emit(
                "ffmpeg not found.\n\n"
                "Run:  pip install imageio-ffmpeg\n\n"
                "ffmpeg is required to decode video audio for transcription."
            )
        except Exception as exc:
            self.error.emit(str(exc))


class LanguageDetector(QObject):
    """
    Lightweight worker that loads Whisper 'tiny' and calls detect_language()
    on the first 30 s of the video.  Much faster than a full transcription.
    Emits detected(code) with a two-letter language code, e.g. "en", "sv".
    """

    detected = pyqtSignal(str)   # language code
    error    = pyqtSignal(str)

    def __init__(self, video_path: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.video_path = video_path

    def run(self) -> None:
        try:
            try:
                import whisper
            except ModuleNotFoundError:
                self.error.emit("openai-whisper not installed")
                return

            model = whisper.load_model("tiny", download_root=str(WHISPER_CACHE_DIR))

            # load_audio extracts 30 s by default, then pad_or_trim to exactly 30 s
            audio = whisper.load_audio(self.video_path)
            audio = whisper.pad_or_trim(audio)
            mel   = whisper.log_mel_spectrogram(audio).to(model.device)

            _, probs = model.detect_language(mel)
            lang = max(probs, key=probs.get)
            self.detected.emit(lang)
        except FileNotFoundError:
            self.error.emit(
                "ffmpeg not found.\n\n"
                "Run:  pip install imageio-ffmpeg\n\n"
                "ffmpeg is required to decode video audio for transcription."
            )
        except Exception as exc:
            self.error.emit(str(exc))


def model_is_cached(model_name: str) -> bool:
    """Return True if the named Whisper model already lives in the cache."""
    expected = WHISPER_CACHE_DIR / f"{model_name}.pt"
    return expected.exists()
