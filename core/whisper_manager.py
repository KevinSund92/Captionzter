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

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

# Suppress CMD window on Windows for all subprocesses
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

from PyQt6.QtCore import QObject, pyqtSignal

# ---------------------------------------------------------------------------
# Model cache directory — next to the executable when frozen, project-root
# when running from source.
# ---------------------------------------------------------------------------
# Store models in LOCALAPPDATA so it works when app is installed to Program Files
_LOCAL = os.environ.get("LOCALAPPDATA", "")
if _LOCAL:
    WHISPER_CACHE_DIR = Path(_LOCAL) / "CaptionStudio" / "models" / "whisper"
else:
    _APP_DIR = Path(os.environ.get("CAPTION_STUDIO_APP_DIR", Path(__file__).parent.parent))
    WHISPER_CACHE_DIR = _APP_DIR / "models" / "whisper"
WHISPER_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Ensure ffmpeg is on PATH so Whisper's load_audio() can find it.
# imageio-ffmpeg ships a portable binary but uses a versioned name such as
# ffmpeg-win-x86_64-v7.1.exe.  Whisper calls "ffmpeg" by name, so we create
# a plain ffmpeg.exe alias via hard link in <app>/bin/ and add that to PATH.
# Hard links require no admin rights and use no extra disk space on NTFS.
# ---------------------------------------------------------------------------
def _ensure_ffmpeg_alias() -> str:
    """
    Find the imageio_ffmpeg binary and create ffmpeg.exe in LOCALAPPDATA/CaptionStudio/bin/.
    Returns the bin dir path (already added to PATH in the current process).
    Returns empty string if not found.
    """
    try:
        local = os.environ.get("LOCALAPPDATA", "")
        bin_dir = os.path.join(local, "CaptionStudio", "bin") if local else ""
        if not bin_dir:
            return ""
        os.makedirs(bin_dir, exist_ok=True)
        dest = os.path.join(bin_dir, "ffmpeg.exe")
        if os.path.isfile(dest):
            return bin_dir  # already set up

        # Find imageio_ffmpeg in packages/
        pkg = str(_packages_dir())
        sys.path.insert(0, pkg)
        try:
            import imageio_ffmpeg
            src = imageio_ffmpeg.get_ffmpeg_exe()
        finally:
            if pkg in sys.path:
                sys.path.remove(pkg)

        if not os.path.isfile(src):
            return ""
        try:
            os.link(src, dest)
        except OSError:
            import shutil
            shutil.copy2(src, dest)
        return bin_dir
    except Exception:
        return ""


def _ffmpeg_setup_snippet(pkg_dir: str) -> str:
    """Return Python code that ensures ffmpeg is on PATH inside a subprocess."""
    # If system ffmpeg is available, nothing to do — it's already on PATH
    # and is likely a full build (supports AV1, HEVC, etc.)
    try:
        r = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, creationflags=_NO_WINDOW, timeout=5
        )
        if r.returncode == 0:
            return ""  # system ffmpeg already on PATH — no extra setup needed
    except Exception:
        pass
    bin_dir = _ensure_ffmpeg_alias()
    if bin_dir:
        # Fast path: ffmpeg.exe alias already exists, just add to PATH
        return (
            "import os\n"
            f"_bin = {bin_dir!r}\n"
            "if _bin not in os.environ.get('PATH',''):\n"
            "    os.environ['PATH'] = _bin + os.pathsep + os.environ.get('PATH','')\n"
        )
    # Fallback: find via imageio_ffmpeg inside the subprocess
    return (
        "try:\n"
        f"    import sys, os; sys.path.insert(0, {pkg_dir!r})\n"
        "    import imageio_ffmpeg\n"
        "    _ff = imageio_ffmpeg.get_ffmpeg_exe()\n"
        "    _bd = os.path.dirname(_ff)\n"
        "    os.environ['PATH'] = _bd + os.pathsep + os.environ.get('PATH','')\n"
        "except Exception as _e:\n"
        f"    raise RuntimeError('ffmpeg not found. Re-run setup.') from _e\n"
    )


def _packages_dir() -> Path:
    """Return the packages directory where pip installed whisper/torch."""
    from core.first_run_check import packages_dir
    return Path(packages_dir())


def _find_worker_python() -> str:
    """Find the Python interpreter that has whisper/torch installed."""
    # 1. Embedded Python downloaded by the wizard
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        embed = os.path.join(local, "..", "..", "Downloads", "caption_studio",
                             "caption_studio", "python", "python.exe")
    app_dir = os.environ.get("CAPTION_STUDIO_APP_DIR", "")
    candidates = []
    if app_dir:
        candidates.append(os.path.join(app_dir, "python", "python.exe"))
    # 2. Windows registry
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for root_key in (r"Software\Python\PythonCore",):
                try:
                    with winreg.OpenKey(hive, root_key) as core:
                        i = 0
                        while True:
                            try:
                                ver = winreg.EnumKey(core, i); i += 1
                                try:
                                    with winreg.OpenKey(core, rf"{ver}\InstallPath") as ip:
                                        p, _ = winreg.QueryValueEx(ip, "ExecutablePath")
                                        if p and os.path.isfile(p):
                                            candidates.append(p)
                                except OSError:
                                    pass
                            except OSError:
                                break
                except OSError:
                    pass
    except Exception:
        pass
    # 3. LOCALAPPDATA Python installs
    if local:
        base = os.path.join(local, "Programs", "Python")
        if os.path.isdir(base):
            for sub in sorted(os.listdir(base), reverse=True):
                p = os.path.join(base, sub, "python.exe")
                if os.path.isfile(p):
                    candidates.append(p)
    # 4. PATH
    for name in ("python3.exe", "python.exe"):
        p = shutil.which(name)
        if p and os.path.isfile(p) and p != sys.executable:
            candidates.append(p)

    pkg_dir = str(_packages_dir())
    for p in candidates:
        if not os.path.isfile(p):
            continue
        try:
            r = subprocess.run([p, "--version"], capture_output=True, text=True, timeout=5, creationflags=_NO_WINDOW)
            if r.returncode == 0 and "Python 3" in (r.stdout + r.stderr):
                # Prefer Python that can actually import whisper from our packages/
                check = subprocess.run(
                    [p, "-c", f"import sys; sys.path.insert(0,{pkg_dir!r}); import whisper"],
                    capture_output=True, timeout=30, creationflags=_NO_WINDOW,
                )
                if check.returncode == 0:
                    return p
        except Exception:
            pass

    # Fallback: return first Python 3 found even if whisper check failed
    for p in candidates:
        if not os.path.isfile(p):
            continue
        try:
            r = subprocess.run([p, "--version"], capture_output=True, text=True, timeout=5, creationflags=_NO_WINDOW)
            if r.returncode == 0 and "Python 3" in (r.stdout + r.stderr):
                return p
        except Exception:
            pass

    raise RuntimeError(
        "No Python interpreter found. Please restart CaptionStudio to re-run setup."
    )


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
        self._proc         = None

    def cancel(self) -> None:
        self._cancelled = True
        if self._proc and self._proc.poll() is None:
            self._proc.kill()

    # ------------------------------------------------------------------
    def run(self) -> None:
        try:
            python = _find_worker_python()
            pkg_dir = str(_packages_dir())
            model_dir = str(WHISPER_CACHE_DIR)

            same_lang = (
                self.subtitle_lang is None
                or self.subtitle_lang == self.language
                or (self.language is None and self.subtitle_lang is None)
            )
            whisper_to_english = not same_lang and self.subtitle_lang == "en"
            task = "translate" if whisper_to_english else "transcribe"
            language_arg = self.language or ""

            self.progress.emit(f"Loading Whisper '{self.model_name}' model …")

            script = (
                "import sys, json\n"
                f"sys.path.insert(0, {pkg_dir!r})\n"
                + _ffmpeg_setup_snippet(pkg_dir) +
                "import whisper\n"
                f"model = whisper.load_model({self.model_name!r}, download_root={model_dir!r})\n"
                "sys.stderr.write('TRANSCRIBING\\n'); sys.stderr.flush()\n"
                f"result = model.transcribe({self.video_path!r}, "
                f"language={language_arg!r} or None, task={task!r}, "
                "word_timestamps=True, verbose=False)\n"
                "for seg in result.get('segments', []):\n"
                "    print(json.dumps({"
                "'start':seg['start'],'end':seg['end'],'text':seg['text'].strip(),"
                "'words':[{'word':w['word'],'start':w['start'],'end':w['end']} for w in seg.get('words',[])]}), flush=True)\n"
                "print('__DONE__', flush=True)\n"
            )

            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            proc = subprocess.Popen(
                [python, "-c", script],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env, creationflags=_NO_WINDOW,
            )
            self._proc = proc

            stderr_lines: list = []

            def _read_stderr():
                for line in proc.stderr:
                    line = line.strip()
                    if line == "TRANSCRIBING":
                        self.progress.emit("Transcribing — this may take a moment …")
                    else:
                        stderr_lines.append(line)

            import threading
            t = threading.Thread(target=_read_stderr, daemon=True)
            t.start()

            segments = []
            for raw in proc.stdout:
                if self._cancelled:
                    proc.kill()
                    break
                raw = raw.strip()
                if raw == "__DONE__":
                    break
                try:
                    item = json.loads(raw)
                    segments.append(item)
                    self.segment_ready.emit(item)
                except Exception:
                    pass

            t.join()
            proc.wait()
            self._proc = None

            if self._cancelled:
                return
            if proc.returncode != 0:
                err = "\n".join(stderr_lines[-20:])
                raise RuntimeError(f"Whisper subprocess failed:\n{err}")

            self.finished.emit(segments)

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
            python = _find_worker_python()
            pkg_dir = str(_packages_dir())
            model_dir = str(WHISPER_CACHE_DIR)

            script = (
                "import sys\n"
                f"sys.path.insert(0, {pkg_dir!r})\n"
                + _ffmpeg_setup_snippet(pkg_dir) +
                "import whisper\n"
                f"model = whisper.load_model('tiny', download_root={model_dir!r})\n"
                f"audio = whisper.load_audio({self.video_path!r})\n"
                "audio = whisper.pad_or_trim(audio)\n"
                "mel = whisper.log_mel_spectrogram(audio).to(model.device)\n"
                "_, probs = model.detect_language(mel)\n"
                "lang = max(probs, key=probs.get)\n"
                "print(lang, flush=True)\n"
            )

            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            proc = subprocess.run(
                [python, "-c", script],
                capture_output=True, text=True, env=env, creationflags=_NO_WINDOW,
            )
            if proc.returncode != 0:
                self.error.emit(proc.stderr[-500:] if proc.stderr else "Language detection failed")
                return
            lang = proc.stdout.strip().splitlines()[-1].strip()
            if lang:
                self.detected.emit(lang)
        except Exception as exc:
            self.error.emit(str(exc))


def model_is_cached(model_name: str) -> bool:
    """Return True if the named Whisper model already lives in the cache."""
    expected = WHISPER_CACHE_DIR / f"{model_name}.pt"
    return expected.exists()
