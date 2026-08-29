"""Read CS2 text chat from the game's own console.log.

This is exactly how CSTranslator and similar tools work, and it's VAC-safe: the
game writes console.log itself when launched with the official `-condebug`
launch option, and we just tail that text file. No memory reading, no injection.

Chat lines look like:
    [ALL] Player‎: message text
    [TEAM] Player‎ [DEAD]: message text
(names may contain a U+200E left-to-right mark and a " [DEAD]" suffix.)
"""
import os
import queue
import re
import threading
import time

_CONSOLE_REL = os.path.join(
    "steamapps", "common", "Counter-Strike Global Offensive",
    "game", "csgo", "console.log",
)


def _steam_path():
    try:
        import winreg
    except Exception:  # noqa: BLE001
        return None
    candidates = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ]
    for hive, key, value in candidates:
        try:
            with winreg.OpenKey(hive, key) as k:
                path, _ = winreg.QueryValueEx(k, value)
            if path and os.path.isdir(path):
                return path
        except OSError:
            continue
    return None


def _library_paths(steam):
    libs = [steam]
    vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
    try:
        with open(vdf, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        for m in re.finditer(r'"path"\s*"([^"]+)"', text):
            p = m.group(1).encode().decode("unicode_escape")
            if os.path.isdir(p) and p not in libs:
                libs.append(p)
    except Exception:  # noqa: BLE001
        pass
    return libs


def find_console_log(override=""):
    """Return the console.log path (it may not exist until CS2 writes it with
    -condebug), or None if CS2 can't be located."""
    if override:
        return override
    steam = _steam_path()
    if not steam:
        return None
    for lib in _library_paths(steam):
        p = os.path.join(lib, _CONSOLE_REL)
        if os.path.isdir(os.path.dirname(p)):
            return p
    return None


# Decorations CS2 hangs off the player name: alive/dead state, team, spectator.
_NAME_DECORATIONS = re.compile(
    r"\s*(\[DEAD\]|\*DEAD\*|\(Counter-Terrorist\)|\(Terrorist\)|\*SPEC\*)\s*",
    re.IGNORECASE)

# Bidi marks CS2 embeds around names (LRM / RLM).
_BIDI_MARKS = dict.fromkeys(map(ord, "‎‏‪‫‬"), None)

# The scope tag must open the line (CS2 prefixes a timestamp when the player
# has con_timestamp on). Anchoring matters: without it, a chat *message* that
# merely contains "[ALL] " would itself be parsed as a chat line, letting any
# player forge a line attributed to someone else.
_CHAT_RE = re.compile(
    r"^\s*(?:\d{2}/\d{2}/\d{4}\s*-\s*\d{2}:\d{2}:\d{2}:\s*)?"
    r"\[(ALL|TEAM)\]\s+(.*)$")


def _clean_name(name):
    name = name.translate(_BIDI_MARKS)
    name = _NAME_DECORATIONS.sub(" ", name)
    return " ".join(name.split())


def parse_chat_line(line, scopes):
    """Return {scope, name, message} for a chat line, else None."""
    m = _CHAT_RE.match(line.rstrip())
    if not m:
        return None
    scope, rest = m.group(1), m.group(2)
    if scope not in scopes:
        return None
    name, sep, msg = rest.partition(": ")
    if not sep:
        return None
    name = _clean_name(name)
    msg = msg.strip()
    if not name or not msg:
        return None
    return {"scope": scope, "name": name, "message": msg}


class ChatLogReader(threading.Thread):
    def __init__(self, path, scopes, out_queue, stop_event):
        super().__init__(daemon=True, name="chat-log")
        self.path = path
        self.scopes = {s.upper() for s in scopes}
        self.out = out_queue
        self.stop_event = stop_event

    def run(self):
        while not self.stop_event.is_set() and not (self.path and os.path.isfile(self.path)):
            time.sleep(2.0)
        if self.stop_event.is_set():
            return
        print(f"[chat] tailing {self.path}")
        try:
            f = open(self.path, "r", encoding="utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            print(f"[chat] cannot open console.log: {e}")
            return

        with f:
            f.seek(0, os.SEEK_END)
            last_size = self._size()
            while not self.stop_event.is_set():
                line = f.readline()
                if not line:
                    size = self._size()
                    if size < last_size:        # log cleared (new match) -> restart
                        f.seek(0)
                    last_size = size
                    time.sleep(0.2)
                    continue
                ev = parse_chat_line(line, self.scopes)
                if ev:
                    try:
                        self.out.put_nowait(ev)
                    except queue.Full:
                        pass

    def _size(self):
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0
