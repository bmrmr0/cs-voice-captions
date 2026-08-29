"""Optional plain-text transcript of everything that was captioned.

One file per run, named by start time, so you can read back what your
teammates said after the match instead of scrolling the captions window
mid-round. Writing is best-effort: a failure here must never take the
captions down with it.
"""
import os
import threading
import time


class TranscriptWriter:
    def __init__(self, tcfg, default_dir):
        tcfg = tcfg or {}
        self.enabled = bool(tcfg.get("enabled", False))
        self.path = None
        self._fh = None
        self._lock = threading.Lock()
        self._broken = False
        if not self.enabled:
            return
        directory = (tcfg.get("directory") or "").strip() or default_dir
        try:
            os.makedirs(directory, exist_ok=True)
            name = time.strftime("captions-%Y-%m-%d_%H-%M-%S.txt")
            self.path = os.path.join(directory, name)
            self._fh = open(self.path, "a", encoding="utf-8")
            self._fh.write(f"# CS Voice Captions - {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            self._fh.flush()
        except Exception as e:  # noqa: BLE001
            print(f"[transcript] disabled: {e}")
            self.enabled = False
            self.path = None
            self._fh = None

    def add_caption(self, r):
        if not self._fh or self._broken:
            return
        ts = r.get("ts", time.strftime("%H:%M:%S"))
        label = r.get("label", "?")
        marker = "chat" if r.get("kind") == "chat" else "voice"
        scope = r.get("scope") or r.get("lang") or ""
        tag = f" [{scope}]" if scope else ""
        line = f"{ts} ({marker}){tag} {label}: {r.get('text', '')}"
        if r.get("original"):
            line += f"   | original: {r['original']}"
        self._write(line)

    def add_status(self, text):
        self._write(f"{time.strftime('%H:%M:%S')} (status) {text}")

    def _write(self, line):
        if not self._fh or self._broken:
            return
        # Captions arrive from the Qt thread, statuses can arrive from workers.
        with self._lock:
            try:
                self._fh.write(line + "\n")
                self._fh.flush()      # survive a crash / kill mid-match
            except Exception as e:  # noqa: BLE001
                print(f"[transcript] write failed, stopping: {e}")
                self._broken = True

    def close(self):
        with self._lock:
            if self._fh:
                try:
                    self._fh.close()
                except Exception:  # noqa: BLE001
                    pass
                self._fh = None
