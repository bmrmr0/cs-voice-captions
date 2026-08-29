"""Configuration loading / merging for CS Voice Captions.

`config.json` (next to the .exe, or in the project root when running from
source) is merged on top of these defaults, so the user only needs to specify
the keys they want to change.
"""
import copy
import json
import os
import sys
import tempfile

# Default configuration. Mirrors config.example.json so the app still runs if
# that file is missing or partially filled in.
DEFAULTS = {
    # Which audio streams to caption.
    "sources": {
        "teammates": True,   # CS2's audio (teammates' voice), captured per-process
        "my_mic": False,     # off: don't caption your own voice
    },

    # How the teammate stream is captured.
    "capture": {
        "cs2_process_name": "cs2.exe",
        # "process" = CS2 audio only (proc-tap). "loopback" = whole desktop.
        "teammates_backend": "process",
        # If the proc-tap backend can't start (not installed, unsupported
        # Windows build), fall back to whole-desktop loopback instead of
        # silently capturing nothing.
        "fallback_to_loopback": True,
    },

    # Read CS2 *text* chat from console.log (needs the -condebug launch option).
    # Safe: the game writes this log itself; we only tail the file.
    "chat": {
        "enabled": True,
        "console_log_path": "",        # blank = auto-detect via Steam
        "scopes": ["ALL", "TEAM"],     # which chat channels to show
        "translate": True,             # translate foreign chat
        "translator": "google",        # "google" | "off"
                                       # the one thing that uses the network
    },

    # Speech-to-text (Whisper via OpenVINO).
    "stt": {
        # "auto" prefers the NPU, then the CPU. The GPU is never picked
        # automatically -- on a gaming machine that is what runs the game.
        # Force one with "NPU", "CPU", "GPU", or an exact name like "GPU.1".
        "device": "auto",
        # tiny | base | small | medium | large-v3-turbo, a HuggingFace repo id,
        # or a path to an OpenVINO model folder. Downloaded once on first run
        # and kept in the app data folder.
        "model": "small",
        "beam_size": 1,               # >1 is more accurate and slower
    },

    # Translation. Whisper translates speech in any language straight to
    # English in a single local pass -- no server, no API key, nothing to
    # install alongside.
    "translation": {
        "enabled": True,
        "target_language": "English",  # Whisper's translate task only outputs English
        # Show ONLY foreign speech and hide English. Off by default: Whisper
        # guesses "en" on short, noisy CS2 clips, and trusting that guess is
        # what made an earlier build throw away 95% of its captions.
        "only_foreign": False,
        "translate_sources": ["teammates"],  # don't translate your own mic
        "show_original": False,       # also show the original (foreign) text
    },

    # Voice-activity detection / utterance segmentation.
    "vad": {
        "aggressiveness": 2,          # 0..3 (webrtcvad); higher = stricter
        "silence_ms": 500,            # trailing silence that ends an utterance (per phrase)
        # Force-flush long speech into separate captions. The old value of 6
        # was cutting 19% of real utterances mid-sentence, which also hurts
        # language detection -- Whisper does better on a complete phrase.
        "max_utterance_s": 8,
        "min_speech_ms": 200,         # drop clips with less real speech than this
        # Fraction of the clip that must actually be speech. Without this a
        # single word adrift in six seconds of gunfire reaches Whisper, which
        # will cheerfully invent a whole sentence out of it.
        "min_speech_ratio": 0.25,
        "min_chars": 2,               # drop transcripts shorter than this
        "preroll_ms": 300,            # audio kept from just before speech starts
    },

    # Speaker identification for the teammate stream (distinct voices ->
    # coloured dot + auto name like P1/P2). Needs requirements-diarization.txt;
    # gracefully disables itself if those aren't installed.
    "diarization": {
        "enabled": True,
        "similarity_threshold": 0.6,    # lower = groups noisy CS2 voices together
        "max_speakers": 5,              # roughly a team's size
    },

    # On-screen overlay drawn ON TOP of the game. Off by default -- the normal
    # window below is the default display. Toggle it from the tray menu.
    "overlay": {
        "enabled": False,
        "max_lines": 5,
        "font_size": 20,
        "x": 40,
        "y": 40,
        "width": 720,
        "line_ttl_s": 12,             # seconds a line stays before fading out
        "click_through": True,        # mouse passes through to the game
    },

    # The main captions window (shown by default; this is the default display).
    "history": {
        "enabled": True,
        "max_lines": 500,             # cap so a long session can't eat memory
    },

    # Global hotkeys, active even while CS2 has focus. Blank = unbound.
    # Format: "ctrl+alt+shift+win+<key>", e.g. "ctrl+alt+p", "f8".
    "hotkeys": {
        "enabled": True,
        "toggle_pause": "ctrl+alt+p",
        "toggle_overlay": "ctrl+alt+o",
        "show_window": "ctrl+alt+c",
    },

    # Write every caption to a plain-text transcript you can read after the
    # match. Files land next to config.json in a "transcripts" folder.
    "transcript": {
        "enabled": False,
        "directory": "",              # blank = <app folder>/transcripts
    },
}


def app_dir():
    """The folder the app lives in: next to the .exe when frozen, else the
    repo root (this file's own directory)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def bundle_dir():
    """Where read-only data bundled *inside* the exe is unpacked at runtime.
    Same as app_dir() when running from source."""
    return getattr(sys, "_MEIPASS", None) or app_dir()


def data_dir():
    """Where the app writes: settings, the downloaded model, the compiled
    device cache, transcripts.

    LOCALAPPDATA, so the executable itself stays a single self-contained file
    you can download and run from anywhere. Drop a file named `portable.txt`
    next to the exe to keep everything in its own folder instead -- useful on
    a USB stick.
    """
    here = app_dir()
    if os.path.isfile(os.path.join(here, "portable.txt")):
        return here
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "CSVoiceCaptions")
    try:
        os.makedirs(path, exist_ok=True)
        return path
    except Exception:  # noqa: BLE001
        return here


# Kept for callers that just want the settings file location.
def project_root():
    return data_dir()


def config_path():
    """The optional settings file.

    The exe ships with no config.json at all -- every default above is compiled
    in, so a bare executable is fully configured. Dropping a config.json next
    to it (or letting the app save one) overrides only the keys it contains.
    """
    return os.path.join(data_dir(), "config.json")


def _deep_merge(base, over):
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load():
    cfg = copy.deepcopy(DEFAULTS)
    path = config_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                user = json.load(f)
            cfg = _deep_merge(cfg, user)
        except Exception as e:  # noqa: BLE001
            print(f"[config] could not read {path}: {e} -- using defaults")
    return cfg


def save(cfg):
    """Persist the running config, atomically so a crash mid-write can't leave
    a truncated config.json behind. Returns True on success."""
    path = config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[config] could not write {path}: {e}")
        return False
