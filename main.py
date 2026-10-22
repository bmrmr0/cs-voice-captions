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
    """Mirror stdout/stderr into a log file in the app's data folder.

    The windowed exe has no console at all, so without this every diagnostic
    the troubleshooting docs mention would be lost. Kept small: one file,
    truncated each run, best-effort.

    In the data folder (%LOCALAPPDATA%\CSVoiceCaptions, or beside the exe in
    portable mode) rather than next to the exe: the exe is meant to be one
    file with nothing written beside it, and the log holds other players'
    names and chat, which has no business sitting in a Downloads folder.
    """
    try:
        import config as config_mod
        log = open(os.path.join(config_mod.data_dir(), "cs-voice-captions.log"), "w",
                   encoding="utf-8", errors="replace", buffering=1)
    except Exception:  # noqa: BLE001
        return

    class _Tee:
        def __init__(self, stream, sink):
            self._stream, self._sink = stream, sink
            self._at_line_start = True

        def _stamp(self, data):
            """Prefix each line with the wall-clock time.

            Without this the log says what happened but not when, and the
            useful thing about a capture log is how events cluster: whether
            six discarded fragments were six separate noises or one sentence
            chopped into pieces.
            """
            out = []
            for line in data.splitlines(keepends=True):
                if self._at_line_start:
                    out.append(time.strftime("%H:%M:%S "))
                out.append(line)
                self._at_line_start = line.endswith("\n")
            return "".join(out)

        def write(self, data):
            data = self._stamp(data)
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


if __name__ == "__main__":
    # Before anything else, the log file in particular: a second copy used to
    # truncate the log the first was still writing. If one is already
    # running, bring its window forward instead and leave.
    import single_instance
    if not single_instance.acquire():
        # Same build: the running copy shows its window and we leave. Newer
        # build: it quits, and we wait for its lock and carry on as the app.
        if single_instance.handshake() != "bye" or not single_instance.wait_for_lock():
            sys.exit(0)
    _start_file_log()

# WASAPI capture sporadically warns about buffer discontinuities -- harmless.
warnings.filterwarnings("ignore", message=".*data discontinuity.*")

import config as config_mod                       # noqa: E402
import chat_log                                   # noqa: E402
import hotkeys as hotkeys_mod                     # noqa: E402
from audio import SoundcardSource, ProcessSource  # noqa: E402
from stt import Transcriber                       # noqa: E402
from transcript import TranscriptWriter           # noqa: E402
from ui import (Bridge, Overlay, History, TrayController,  # noqa: E402
                CondebugReminder, should_remind_condebug)
from settings_window import SettingsWindow        # noqa: E402
from reply_window import ReplyWindow              # noqa: E402
import autostart                                  # noqa: E402
import updates                                    # noqa: E402
from PySide6.QtCore import QTimer                 # noqa: E402
from PySide6.QtWidgets import QApplication        # noqa: E402

SOURCES = {
    "teammates": {"label": "TEAM", "color": "#7CFC7C"},
    "my_mic":    {"label": "ME",   "color": "#7CC0FF"},
}


def build_audio_sources(cfg, out_queue, stop_event, paused_event, status_cb=None,
                        connection_cb=None):
    sources = []
    cap = cfg.get("capture", {})
    if cfg["sources"].get("teammates", False):
        backend = cap.get("teammates_backend", "process")
        if backend == "process":
            sources.append(ProcessSource("teammates", out_queue, cfg["vad"],
                                         stop_event, paused_event,
                                         cap.get("cs2_process_name", "cs2.exe"),
                                         cap.get("fallback_to_loopback", True),
                                         status_cb, connection_cb))
        else:
            sources.append(SoundcardSource("teammates", out_queue, cfg["vad"],
                                           stop_event, paused_event,
                                           kind="loopback", status_cb=status_cb,
                                           connection_cb=connection_cb))
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
        from translate import make_text_translator, _guess_sources
        import glossary
        tcfg = self.cfg.get("translation", {})
        target = tcfg.get("target_language", "English")
        target_is_en = target.lower() in ("english", "en")
        ccfg = self.cfg.setdefault("chat", {})
        # Built even if translation starts switched off, so the settings
        # window can turn it on without a restart. Only "off" as the engine
        # means no translator at all.
        translator = make_text_translator(
            {**self.cfg, "chat": {**ccfg, "translate": True}})
        recent_lines = {}            # (scope, player) -> (their last line, when)
        REPEAT_WINDOW_S = 60

        while not self.stop_event.is_set():
            try:
                ev = self.q.get(timeout=0.3)
            except queue.Empty:
                continue
            # Read per message so the tray / settings toggles apply at once.
            # Hidden chat is not translated either: no request is sent for a
            # line nobody will see.
            if not ccfg.get("show", True):
                continue
            if ev.get("name") in ccfg.get("muted", ()):
                continue
            # The same player sending the same line again (spam, or a bound
            # key held down) floods the window with copies. Shown once; the
            # repeats are dropped before they cost a translation request.
            # While the spam keeps coming the window keeps extending, so a
            # line resent every few seconds stays hidden. Different players
            # saying the same thing ("gg" all round) are each still shown.
            sender = (ev.get("scope", ""), ev.get("name", ""))
            said = " ".join(ev["message"].lower().split())
            now = time.monotonic()
            last = recent_lines.get(sender)
            recent_lines[sender] = (said, now)
            if last and last[0] == said and now - last[1] < REPEAT_WINDOW_S:
                print(f"[chat] skipped repeat from {ev.get('name', '?')}: {ev['message'][:50]}")
                continue
            text = ev["message"]
            original = None
            translated = False
            gloss = []
            slang = tcfg.get("slang", True)
            langs = _guess_sources(text)
            # A message that is nothing but a known slang phrase gets its
            # meaning directly, and costs no translation request.
            meaning = glossary.get().phrase(text, langs) if slang else None
            swapped = []
            if meaning:
                original, text, translated = text, meaning, True
            # Skip pointless translation of plain-ASCII chat when target is English.
            elif (translator and ccfg.get("translate", True)
                    and not (target_is_en and text.isascii())):
                # The translator gets slang already turned into English
                # ("с вх" -> "с wallhack"); the caption still shows what the
                # player actually typed as the original.
                source = text
                if slang:
                    source, swapped = glossary.get().pretranslate(text, langs)
                # Every word swapped ("изи катка" -> "easy match") leaves
                # nothing to translate -- and nothing for the fallback
                # translator to recognise the language by, so sending it
                # would come back empty. It already is the translation.
                tr = (source if swapped and source.isascii()
                      else translator.translate(source))
                if tr and tr.strip() and tr.strip() != text.strip():
                    original, text, translated = text, tr, True
            # Explain slang in what the player actually typed. Plain-ASCII
            # lines are never translated, but transliterated swearing
            # ("cyka blyat") is still worth explaining.
            if slang and not meaning:
                gloss = glossary.get().terms(original or text, langs, skip=swapped)
            scope = ev.get("scope", "")
            print(f"[chat:{scope}] {ev.get('name', '?')}: {text}"
                  + (f"   (from: {original})" if original else "")
                  + (f"   [swapped before translating: {', '.join(swapped)}]" if swapped else "")
                  + ("   [slang: " + "; ".join(f"{w} = {m}" for w, m in gloss) + "]"
                     if gloss else ""))
            self.bridge.new_caption.emit({
                "kind": "chat",
                "scope": scope,
                "location": ev.get("location", ""),
                "label": ev.get("name", "?"),
                "color": "#7CFC7C" if scope == "TEAM" else "#e8e8e8",
                "text": text,
                "original": original,
                "translated": translated,
                "gloss": gloss,
                "lang": "",
                "ts": time.strftime("%H:%M:%S"),
            })


def main():
    cfg = config_mod.load()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("CS Voice Captions")
    import app_icon
    app.setWindowIcon(app_icon.icon())     # captions window, settings, taskbar

    bridge = Bridge()
    stop_event = threading.Event()
    paused_event = threading.Event()

    writer = TranscriptWriter(cfg.get("transcript", {}),
                              os.path.join(config_mod.project_root(), "transcripts"))

    def save_overlay_pos(x, y):
        cfg["overlay"]["x"], cfg["overlay"]["y"] = int(x), int(y)
        config_mod.save(cfg)

    overlay = Overlay(cfg["overlay"], on_move=save_overlay_pos)

    def on_setting_change(section, key):
        # Everything else the window changes is read live by its consumer;
        # the overlay has widgets to redraw and the transcript a file to open.
        if (section, key) == ("overlay", "enabled"):
            # The tray owns showing and hiding (and remembers it); bring it
            # in line with the checkbox.
            if bool(cfg["overlay"]["enabled"]) != tray.overlay_visible:
                tray.toggle_overlay()
        elif section == "overlay":
            overlay.apply_settings()
        elif (section, key) == ("transcript", "enabled"):
            path = writer.set_enabled(cfg["transcript"]["enabled"])
            bridge.status.emit(f"Transcript: {path}" if path else "Transcript stopped")

    settings = SettingsWindow(cfg, on_change=on_setting_change)
    reply = ReplyWindow(cfg)
    autostart.repair()          # the exe may have moved since it was turned on

    def mute_player(name):
        # ChatWorker reads this list for every line, so it applies at once.
        muted = cfg.setdefault("chat", {}).setdefault("muted", [])
        if name not in muted:
            muted.append(name)
            config_mod.save(cfg)
        bridge.status.emit(f"Muted {name}'s chat. Unmute in Settings.")

    history = History(cfg.get("history", {}), on_settings=settings.open,
                      on_clear=overlay.clear, on_mute=mute_player)

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
                          on_quit=writer.close,
                          chat_cfg=cfg["chat"],
                          on_chat_toggle=lambda _shown: config_mod.save(cfg),
                          on_settings=settings.open,
                          on_reply=reply.open,
                          keys=dict(hotkeys_mod.bound(cfg.get("hotkeys"))))

    # Hotkey callbacks fire on the hotkey thread; signals hop them to the GUI
    # thread before anything touches a widget.
    bridge.toggle_pause.connect(tray.toggle_pause)
    bridge.toggle_overlay.connect(tray.toggle_overlay)
    bridge.show_window.connect(tray.show_window)
    bridge.connection.connect(tray.set_connection)
    bridge.show_reply.connect(reply.open)
    bridge.update_available.connect(tray.notify_update)
    bridge.update_available.connect(
        lambda tag, url: bridge.status.emit(f"Version {tag} is available: {url}"))
    # A later launch of the exe asks for the window instead of starting a
    # second copy -- or, if it is a newer build, takes over from this one.
    # Kept referenced so the server lives as long as the app.
    import single_instance
    instance_server = single_instance.listen(tray.show_window, tray.quit, app)  # noqa: F841
    hk = hotkeys_mod.start(
        cfg.get("hotkeys", {}),
        {
            "toggle_pause": bridge.toggle_pause.emit,
            "toggle_overlay": bridge.toggle_overlay.emit,
            "show_window": bridge.show_window.emit,
            "reply": bridge.show_reply.emit,
        },
        stop_event,
    )

    utt_queue = queue.Queue(maxsize=50)
    audio_sources = build_audio_sources(cfg, utt_queue, stop_event, paused_event,
                                        bridge.status.emit, bridge.connection.emit)
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
        summary = hk.summary({
            (cfg["hotkeys"].get(name) or "").strip(): hotkeys_mod.ACTIONS[name]
            for name in hotkeys_mod.ACTIONS})
        if summary:
            bridge.status.emit(summary)
    if writer.enabled and writer.path:
        bridge.status.emit(f"Transcript: {writer.path}")

    # Text chat depends on CS2's -condebug launch option, and nothing in the
    # game tells you it's missing. Check Steam's own record of CS2's launch
    # options and, if it isn't there, say how to add it -- every start until
    # it's added (or the user says stop), or once when it can't be told.
    reminders = []

    def remind_condebug():
        ccfg = cfg.setdefault("chat", {})
        if not ccfg.get("enabled", True):
            return
        status = chat_log.condebug_status(
            chat_log.find_console_log(ccfg.get("console_log_path", "")))
        print("[chat] -condebug in CS2's launch options: "
              + {True: "yes", False: "no", None: "can't tell"}[status])
        if not should_remind_condebug(status, ccfg):
            return
        dlg = CondebugReminder(missing=status is False)

        def done(_result):
            ccfg["condebug_tip_seen"] = True
            if dlg.dont_remind():
                ccfg["remind_condebug"] = False
            config_mod.save(cfg)

        dlg.finished.connect(done)
        reminders.append(dlg)            # keep it alive while it is open
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    QTimer.singleShot(1500, remind_condebug)
    if cfg.get("updates", {}).get("check", True):
        updates.check_async(bridge.update_available.emit)

    # Qt's event loop blocks Python's signal handling, so Ctrl+C in the console
    # is only noticed if the interpreter gets a slice of time now and then.
    ticker = QTimer()
    ticker.timeout.connect(lambda: None)
    ticker.start(250)

    from version import __version__
    print(f"[main] CS Voice Captions {__version__} running. "
          "Control it from the tray icon. Ctrl+C to quit.")
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
