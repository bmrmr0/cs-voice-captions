"""CS Voice Captions - entry point.

Pipelines (all external to the game - no memory reads, no injection):

    cs2.exe audio (proc-tap) -+
    your mic (soundcard) -----+-> VAD -> utterance queue -> STT worker -+
                              |                                         +-> UI
    CS2 console.log ----------+-> chat queue -> chat worker (translate) -+
"""
import os
import queue
import sys
import threading
import time
import warnings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Force UTF-8 console output so non-Latin text (e.g. Cyrillic) never crashes
# logging on Windows' default cp1252 console.
for _stream in (sys.stdout, sys.stderr):
    try:
        # line_buffering so the log console shows progress live (and isn't lost
        # to block-buffering when output is redirected).
        _stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:  # noqa: BLE001
        pass


def _start_file_log():
    """Mirror stdout/stderr into a log file next to the app.

    The windowed exe has no console at all, so without this every diagnostic
    the troubleshooting docs mention would be lost. Kept small: one file,
    truncated each run, best-effort.
    """
    try:
        base = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
                else os.path.dirname(os.path.abspath(__file__)))
        log = open(os.path.join(base, "cs-voice-captions.log"), "w",
                   encoding="utf-8", errors="replace", buffering=1)
    except Exception:  # noqa: BLE001
        return

    class _Tee:
        def __init__(self, stream, sink):
            self._stream, self._sink = stream, sink

        def write(self, data):
            if self._stream is not None:
                try:
                    self._stream.write(data)
                except Exception:  # noqa: BLE001
                    pass
            try:
                self._sink.write(data)
            except Exception:  # noqa: BLE001
                pass

        def flush(self):
            for target in (self._stream, self._sink):
                try:
                    if target is not None:
                        target.flush()
                except Exception:  # noqa: BLE001
                    pass

        def isatty(self):
            return False

    sys.stdout = _Tee(sys.stdout, log)
    sys.stderr = _Tee(sys.stderr, log)


_start_file_log()

# WASAPI capture sporadically warns about buffer discontinuities -- harmless.
warnings.filterwarnings("ignore", message=".*data discontinuity.*")

import config as config_mod                       # noqa: E402
import chat_log                                   # noqa: E402
import hotkeys as hotkeys_mod                     # noqa: E402
from audio import SoundcardSource, ProcessSource  # noqa: E402
from stt import Transcriber                       # noqa: E402
from transcript import TranscriptWriter           # noqa: E402
from ui import Bridge, Overlay, History, TrayController  # noqa: E402
from PySide6.QtCore import QTimer                 # noqa: E402
from PySide6.QtWidgets import QApplication        # noqa: E402

SOURCES = {
    "teammates": {"label": "TEAM", "color": "#7CFC7C"},
    "my_mic":    {"label": "ME",   "color": "#7CC0FF"},
}


def build_audio_sources(cfg, out_queue, stop_event, paused_event, status_cb=None):
    sources = []
    cap = cfg.get("capture", {})
    if cfg["sources"].get("teammates", False):
        backend = cap.get("teammates_backend", "process")
        if backend == "process":
            sources.append(ProcessSource("teammates", out_queue, cfg["vad"],
                                         stop_event, paused_event,
                                         cap.get("cs2_process_name", "cs2.exe"),
                                         cap.get("fallback_to_loopback", True),
                                         status_cb))
        else:
            sources.append(SoundcardSource("teammates", out_queue, cfg["vad"],
                                           stop_event, paused_event,
                                           kind="loopback", status_cb=status_cb))
    if cfg["sources"].get("my_mic", False):
        sources.append(SoundcardSource("my_mic", out_queue, cfg["vad"],
                                       stop_event, paused_event,
                                       kind="mic", status_cb=status_cb))
    return sources


class STTWorker(threading.Thread):
    def __init__(self, utt_queue, cfg, bridge, stop_event):
        super().__init__(daemon=True, name="stt-worker")
        self.q = utt_queue
        self.cfg = cfg
        self.bridge = bridge
        self.stop_event = stop_event

    def run(self):
        self.bridge.status.emit("Loading models… (first run downloads Whisper)")
        try:
            transcriber = Transcriber(self.cfg, status_cb=self.bridge.status.emit)
        except Exception as e:  # noqa: BLE001
            self.bridge.status.emit(f"Model load failed: {e}")
            print(f"[stt] fatal: {e}")
            return

        speaker_id = None
        try:
            from diarize import SpeakerIdentifier
            speaker_id = SpeakerIdentifier(self.cfg.get("diarization", {}))
        except Exception as e:  # noqa: BLE001
            print(f"[diarize] init failed: {e}")

        device = getattr(transcriber, "device_summary", "CPU")
        self.bridge.status.emit(f"Listening on {device}.")

        while not self.stop_event.is_set():
            try:
                source_key, audio = self.q.get(timeout=0.3)
            except queue.Empty:
                continue
            try:
                result = transcriber.process(source_key, audio)
            except Exception as e:  # noqa: BLE001
                print(f"[stt] process error: {e}")
                continue
            if not result:
                continue

            meta = SOURCES.get(source_key, {})
            label = meta.get("label", source_key)
            color = meta.get("color", "#ffffff")
            if source_key == "teammates" and speaker_id is not None:
                sid = speaker_id.identify(audio)
                if sid:
                    label, color = sid
            result["label"] = label
            result["color"] = color
            result["ts"] = time.strftime("%H:%M:%S")
            print(f"[caption:{result['label']}] {result.get('lang', '')}: {result['text']}")
            self.bridge.new_caption.emit(result)


class ChatWorker(threading.Thread):
    """Translates parsed text-chat events and emits them as captions."""

    def __init__(self, chat_queue, cfg, bridge, stop_event):
        super().__init__(daemon=True, name="chat-worker")
        self.q = chat_queue
        self.cfg = cfg
        self.bridge = bridge
        self.stop_event = stop_event

    def run(self):
        from translate import make_text_translator
        tcfg = self.cfg.get("translation", {})
        target = tcfg.get("target_language", "English")
        target_is_en = target.lower() in ("english", "en")
        translator = make_text_translator(self.cfg)

        while not self.stop_event.is_set():
            try:
                ev = self.q.get(timeout=0.3)
            except queue.Empty:
                continue
            text = ev["message"]
            original = None
            translated = False
            # Skip pointless translation of plain-ASCII chat when target is English.
            if translator and not (target_is_en and text.isascii()):
                tr = translator.translate(text)
                if tr and tr.strip() and tr.strip() != text.strip():
                    original, text, translated = text, tr, True
            scope = ev.get("scope", "")
            self.bridge.new_caption.emit({
                "kind": "chat",
                "scope": scope,
                "label": ev.get("name", "?"),
                "color": "#7CFC7C" if scope == "TEAM" else "#e8e8e8",
                "text": text,
                "original": original,
                "translated": translated,
                "lang": "",
                "ts": time.strftime("%H:%M:%S"),
            })


def main():
    cfg = config_mod.load()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("CS Voice Captions")

    bridge = Bridge()
    stop_event = threading.Event()
    paused_event = threading.Event()

    writer = TranscriptWriter(cfg.get("transcript", {}),
                              os.path.join(config_mod.project_root(), "transcripts"))

    def save_overlay_pos(x, y):
        cfg["overlay"]["x"], cfg["overlay"]["y"] = int(x), int(y)
        config_mod.save(cfg)

    overlay = Overlay(cfg["overlay"], on_move=save_overlay_pos)
    history = History(cfg.get("history", {}))

    bridge.new_caption.connect(
        lambda r: (overlay.add_caption(r), history.add_caption(r), writer.add_caption(r)))
    bridge.status.connect(
        lambda t: (overlay.add_status(t), history.add_status(t), writer.add_status(t)))

    overlay_enabled = cfg["overlay"].get("enabled", False)
    if overlay_enabled:
        overlay.show()
    if cfg["history"].get("enabled", True):
        history.show()

    def save_overlay_enabled(visible):
        cfg["overlay"]["enabled"] = bool(visible)
        config_mod.save(cfg)

    tray = TrayController(app, overlay, history, paused_event, stop_event,
                          overlay_enabled=overlay_enabled,
                          on_overlay_toggle=save_overlay_enabled,
                          on_quit=writer.close)

    # Hotkey callbacks fire on the hotkey thread; signals hop them to the GUI
    # thread before anything touches a widget.
    bridge.toggle_pause.connect(tray.toggle_pause)
    bridge.toggle_overlay.connect(tray.toggle_overlay)
    bridge.show_window.connect(tray.show_window)
    hk = hotkeys_mod.start(
        cfg.get("hotkeys", {}),
        {
            "toggle_pause": bridge.toggle_pause.emit,
            "toggle_overlay": bridge.toggle_overlay.emit,
            "show_window": bridge.show_window.emit,
        },
        stop_event,
    )

    utt_queue = queue.Queue(maxsize=50)
    audio_sources = build_audio_sources(cfg, utt_queue, stop_event, paused_event,
                                        bridge.status.emit)
    if not audio_sources:
        bridge.status.emit("No audio sources enabled — check config.json")
    for s in audio_sources:
        s.start()

    STTWorker(utt_queue, cfg, bridge, stop_event).start()

    # Text chat (console.log).
    if cfg.get("chat", {}).get("enabled", True):
        path = chat_log.find_console_log(cfg["chat"].get("console_log_path", ""))
        if path:
            chat_queue = queue.Queue(maxsize=200)
            chat_log.ChatLogReader(path, cfg["chat"].get("scopes", ["ALL", "TEAM"]),
                                   chat_queue, stop_event).start()
            ChatWorker(chat_queue, cfg, bridge, stop_event).start()
            if os.path.isfile(path):
                bridge.status.emit("Chat: reading CS2 console.log")
            else:
                bridge.status.emit("Chat: add -condebug to CS2 launch options "
                                   "(waiting for console.log)")
        else:
            bridge.status.emit("Chat: CS2 not found — set chat.console_log_path "
                               "in config.json")

    if hk is not None:
        summary = hk.summary()
        if summary:
            bridge.status.emit(summary)
    if writer.enabled and writer.path:
        bridge.status.emit(f"Transcript: {writer.path}")

    # Qt's event loop blocks Python's signal handling, so Ctrl+C in the console
    # is only noticed if the interpreter gets a slice of time now and then.
    ticker = QTimer()
    ticker.timeout.connect(lambda: None)
    ticker.start(250)

    print("[main] running. Control it from the tray icon. Ctrl+C to quit.")
    try:
        exit_code = app.exec()
    except KeyboardInterrupt:
        exit_code = 0
    stop_event.set()
    if hk is not None:
        hk.stop()
    writer.close()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
