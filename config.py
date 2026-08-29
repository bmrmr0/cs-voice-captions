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
        "translator": "google",        # "google" | "lmstudio" | "off"
    },

    # Speech-to-text engine (faster-whisper).
    "stt": {
        # "auto" = use the GPU when it has headroom, fall back to CPU the moment
        # the game maxes the GPU (so captions never cost you frames). The
        # single-file exe has no CUDA, so it always runs CPU. Force "cpu"/"cuda".
        "device": "auto",
        "gpu_model": "medium",        # model used on the GPU (when it's free)
        "cpu_model": "small",         # model used on CPU (GPU busy, or no GPU)
        "gpu_busy_threshold": 95,     # % GPU usage above which we switch to CPU
        "cpu_compute_type": "int8",   # int8 | int8_float32 | float32
        "gpu_compute_type": "float16",# float16 | int8_float16 | float32
        "cpu_threads": 4,             # cap CPU threads so STT doesn't starve the game
        "beam_size": 5,
    },

    # Translation layer.
    "translation": {
        "enabled": True,
        "engine": "whisper",          # "whisper" (audio->English) or "lmstudio"
        "target_language": "English", # only "English" is supported by whisper engine
        "only_foreign": True,         # only show foreign speech; skip English (friends/teammates/noise)
        # Whisper guesses "en" for short/noisy clips. Only *skip* a clip as
        # "already English" when it is at least this confident, otherwise show
        # it -- a stray English line beats a silently dropped Russian callout.
        "only_foreign_min_prob": 0.6,
        "translate_sources": ["teammates"],  # don't translate your own mic
        "show_original": False,       # also show the original (foreign) text
        "lmstudio": {
            "base_url": "http://localhost:1234/v1",
            "model": "local-model",   # any model id loaded in LM Studio
            "api_key": "lm-studio",
        },
    },

    # Voice-activity detection / utterance segmentation.
    "vad": {
        "aggressiveness": 2,          # 0..3 (webrtcvad); higher = stricter
        "silence_ms": 500,            # trailing silence that ends an utterance (per phrase)
        "max_utterance_s": 6,         # force-flush so long speech becomes separate captions
        "min_speech_ms": 200,         # drop clips with less real speech than this
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


def project_root():
    """Where config.json lives.

    Frozen (PyInstaller) build -> next to the .exe, so config.json stays
    user-editable. Running from source -> the repo root, which is this file's
    own directory.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def config_path():
    return os.path.join(project_root(), "config.json")


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
