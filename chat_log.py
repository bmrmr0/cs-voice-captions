"""Read CS2 text chat from the game's own console.log.

This is exactly how CSTranslator and similar tools work, and it's VAC-safe: the
game writes console.log itself when launched with the official `-condebug`
launch option, and we just tail that text file. No memory reading, no injection.

Chat lines look like:
    [ALL] Player<U+200E>: message text
    [TEAM] Player<U+200E> [DEAD]: message text
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



CS2_APP_ID = "730"
_VDF_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def _parse_vdf(text):
    """Steam's KeyValues text format, as nested dicts with lower-cased keys
    (Steam itself has written both "apps" and "Apps" over the years)."""
    root, stack, cur, key = {}, [], None, None
    cur = root
    for m in _VDF_TOKEN.finditer(text):
        word, brace = m.group(1), m.group(2)
        if brace == "{":
            child = {}
            if key is not None:
                cur[key.lower()] = child
            stack.append(cur)
            cur, key = child, None
        elif brace == "}":
            cur, key = (stack.pop() if stack else root), None
        elif key is None:
            key = word
        else:
            cur[key.lower()] = word
            key = None
    return root


def _vdf_get(node, *path):
    for part in path:
        if not isinstance(node, dict):
            return None
        node = node.get(part.lower())
    return node


def _active_steam_account():
    """The Steam account logged in right now, as its userdata folder name.
    Steam keeps it in the registry while it runs; 0 or missing when it
    doesn't."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Valve\Steam\ActiveProcess") as k:
            value, _ = winreg.QueryValueEx(k, "ActiveUser")
        return str(value) if value else None
    except Exception:  # noqa: BLE001
        return None


def _current_account_config(steam, account=None):
    """localconfig.vdf of the account in use: the logged-in one if Steam
    says, else whichever was saved most recently.

    Only this one counts. A PC with several Steam accounts keeps every one's
    settings, and an old account having -condebug says nothing about the
    account playing now -- checking "any account" reported -condebug present
    on a machine where the logged-in account had none."""
    import glob
    account = account or _active_steam_account()
    if account:
        path = os.path.join(steam, "userdata", account, "config", "localconfig.vdf")
        if os.path.isfile(path):
            return path
    found = glob.glob(os.path.join(steam, "userdata", "*", "config", "localconfig.vdf"))
    return max(found, key=os.path.getmtime) if found else None


def condebug_status(console_log=None, steam=None, account=None):
    """Whether CS2 is set to write console.log, which text chat depends on.

    True if the current Steam account's launch options for CS2 include
    -condebug (or CS2 wrote the log in the last ten minutes, which proves
    it); False if they don't; None if it can't be told -- no Steam, or this
    account has never run CS2. Only the launch-option string is read.
    """
    log = console_log or find_console_log("")
    try:
        if log and time.time() - os.path.getmtime(log) < 600:
            # Steam often writes launch options to disk only when it exits,
            # so a just-added -condebug can be missing from the file while
            # already working. A freshly written log settles it.
            return True
    except OSError:
        pass
    steam = steam or _steam_path()
    if not steam:
        return None
    cfg = _current_account_config(steam, account)
    if not cfg:
        return None
    try:
        with open(cfg, "r", encoding="utf-8", errors="replace") as f:
            tree = _parse_vdf(f.read())
    except OSError:
        return None
    app = _vdf_get(tree, "UserLocalConfigStore", "Software", "Valve", "Steam",
                   "apps", CS2_APP_ID)
    if not isinstance(app, dict):
        return None                          # this account never ran CS2
    options = app.get("launchoptions", "")
    return bool(re.search(r"(?:^|\s)[-+]condebug(?:\s|$)", options, re.IGNORECASE))

# Decorations CS2 hangs off the player name: alive/dead state, team, spectator.
_NAME_DECORATIONS = re.compile(
    r"\s*(\[DEAD\]|\*DEAD\*|\(Counter-Terrorist\)|\(Terrorist\)|\*SPEC\*)\s*",
    re.IGNORECASE)

# Bidi marks CS2 embeds around names (LRM / RLM).
_BIDI_MARKS = dict.fromkeys(map(ord, "\u200e\u200f\u202a\u202b\u202c"), None)

# The scope tag must open the line (CS2 prefixes a timestamp when the player
# has con_timestamp on). Anchoring matters: without it, a chat *message* that
# merely contains "[ALL] " would itself be parsed as a chat line, letting any
# player forge a line attributed to someone else.
#
# Two timestamp shapes are accepted. CS2 writes "10/04 22:08:48  [ALL] ..."
# (no year, no dash, no trailing colon); CS:GO wrote
# "10/04/2024 - 22:08:48: [ALL] ...". Only accepting the CS:GO one meant every
# CS2 chat line failed to match and text chat silently never appeared.
#
# Team chat is tagged with the player's side, "[CT]" or "[T]" -- not "[TEAM]",
# which CS2 never writes. Accepting only ALL/TEAM meant team chat, where most
# callouts happen, was never picked up at all.
_CHAT_RE = re.compile(
    r"^\s*(?:\d{2}/\d{2}(?:/\d{4})?\s*-?\s*\d{2}:\d{2}:\d{2}(?:\.\d+)?:?\s*)?"
    r"\[(ALL|TEAM|CT|T)\]\s+(.*)$")

_SIDE_TAGS = {"CT": "TEAM", "T": "TEAM"}

# A living player's name carries where they are, after a small at-sign:
# "Player<U+200E><U+FE6B>CT Start: nice bro".
_LOCATION_MARK = "\ufe6b"


def _clean_name(name):
    name = name.translate(_BIDI_MARKS)
    name = _NAME_DECORATIONS.sub(" ", name)
    return " ".join(name.split())


def parse_chat_line(line, scopes):
    """Return {scope, name, message} for a chat line, else None."""
    m = _CHAT_RE.match(line.rstrip())
    if not m:
        return None
    side, rest = m.group(1), m.group(2)
    scope = _SIDE_TAGS.get(side, side)
    if scope not in scopes:
        return None
    name, sep, msg = rest.partition(": ")
    if not sep:
        return None
    name, _, location = name.partition(_LOCATION_MARK)
    name = _clean_name(name)
    msg = msg.strip()
    if not name or not msg:
        return None
    return {"scope": scope, "side": side if side in _SIDE_TAGS else "",
            "name": name, "location": _clean_name(location), "message": msg}


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
            # Binary, so the position is a real byte offset we can compare
            # against the file size, and so a line CS2 is still halfway
            # through writing can be held back until its newline arrives
            # instead of being parsed as a truncated message.
            f = open(self.path, "rb")
        except Exception as e:  # noqa: BLE001
            print(f"[chat] cannot open console.log: {e}")
            return

        with f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            pending = b""
            while not self.stop_event.is_set():
                chunk = f.read(65536)
                if not chunk:
                    if self._size() < pos:
                        # CS2 truncates console.log every launch. Start over
                        # from the top of the new file.
                        f.seek(0)
                        pos, pending = 0, b""
                    time.sleep(0.2)
                    continue
                pos += len(chunk)
                pending += chunk
                *lines, pending = pending.split(b"\n")
                for raw in lines:
                    ev = parse_chat_line(raw.decode("utf-8", errors="replace"),
                                         self.scopes)
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
