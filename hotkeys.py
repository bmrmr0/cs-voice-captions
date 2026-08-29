"""System-wide hotkeys, so the app stays controllable while CS2 has focus.

Uses the plain Win32 RegisterHotKey API through ctypes: Windows delivers a
WM_HOTKEY message to a thread of ours, and we hand it to a callback. This is
just the documented way for a normal desktop app to claim a shortcut. It does
not hook the keyboard, read other processes, or send input to the game, so it
stays on the right side of the anti-cheat line the rest of the app is built on.

No-ops cleanly on non-Windows and if a combination is already taken by another
program.
"""
import ctypes
import ctypes.wintypes
import sys
import threading

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

_MODIFIERS = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN, "super": MOD_WIN, "meta": MOD_WIN,
}

# Virtual-key codes for the keys people actually bind. Letters and digits are
# their ASCII values, so they need no table.
_VK = {
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D, "esc": 0x1B,
    "escape": 0x1B, "backspace": 0x08, "insert": 0x2D, "delete": 0x2E,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "pause": 0x13, "scrolllock": 0x91, "numlock": 0x90,
    "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE,
    ",": 0xBC, ".": 0xBE, "/": 0xBF, "\\": 0xDC, "`": 0xC0,
}


def parse(spec):
    """"ctrl+alt+p" -> (modifiers, vk). Returns None if unparseable."""
    if not spec:
        return None
    mods, key = 0, None
    for raw in str(spec).lower().split("+"):
        part = raw.strip()
        if not part:
            continue
        if part in _MODIFIERS:
            mods |= _MODIFIERS[part]
        else:
            key = part
    if not key:
        return None
    if len(key) == 1 and (key.isalpha() or key.isdigit()):
        vk = ord(key.upper())
    elif key in _VK:
        vk = _VK[key]
    else:
        return None
    # Bare keys would swallow the key globally, including inside the game.
    if mods == 0:
        return None
    return mods | MOD_NOREPEAT, vk


class HotkeyManager(threading.Thread):
    """Registers hotkeys on its own thread and pumps their messages.

    Callbacks run on this thread, so anything touching Qt widgets must hop to
    the GUI thread (emit a signal) rather than acting directly.
    """

    def __init__(self, bindings, stop_event=None):
        """bindings: {"ctrl+alt+p": callable, ...}"""
        super().__init__(daemon=True, name="hotkeys")
        self.bindings = bindings
        self.stop_event = stop_event
        self.registered = []      # [(spec, id)]
        self.failed = []          # [(spec, reason)]
        self._thread_id = None
        self._ready = threading.Event()

    def run(self):
        if not sys.platform.startswith("win"):
            print("[hotkeys] global hotkeys are Windows-only; skipping")
            self._ready.set()
            return
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()

        actions = {}
        for hk_id, (spec, fn) in enumerate(self.bindings.items(), start=1):
            parsed = parse(spec)
            if parsed is None:
                self.failed.append((spec, "unrecognised combination"))
                continue
            mods, vk = parsed
            if user32.RegisterHotKey(None, hk_id, mods, vk):
                actions[hk_id] = fn
                self.registered.append((spec, hk_id))
            else:
                self.failed.append((spec, "already taken by another program"))

        for spec, why in self.failed:
            print(f"[hotkeys] could not bind {spec!r}: {why}")
        if self.registered:
            print("[hotkeys] bound " + ", ".join(s for s, _ in self.registered))
        self._ready.set()
        if not actions:
            return

        msg = ctypes.wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if self.stop_event is not None and self.stop_event.is_set():
                    break
                if msg.message == WM_HOTKEY:
                    fn = actions.get(msg.wParam)
                    if fn:
                        try:
                            fn()
                        except Exception as e:  # noqa: BLE001
                            print(f"[hotkeys] handler error: {e}")
        finally:
            for _, hk_id in self.registered:
                try:
                    user32.UnregisterHotKey(None, hk_id)
                except Exception:  # noqa: BLE001
                    pass

    def stop(self):
        """Break the message pump so the thread can unregister and exit."""
        if self._thread_id and sys.platform.startswith("win"):
            try:
                ctypes.windll.user32.PostThreadMessageW(
                    self._thread_id, WM_QUIT, 0, 0)
            except Exception:  # noqa: BLE001
                pass

    def summary(self):
        """One line for the status area, or None if nothing got bound."""
        if not self.registered:
            return None
        self._ready.wait(timeout=2.0)
        return "Hotkeys: " + ", ".join(s for s, _ in self.registered)


def start(cfg_hotkeys, handlers, stop_event=None):
    """Wire config names to callables and start the manager.

    handlers: {"toggle_pause": fn, ...}. Returns the manager, or None when
    hotkeys are disabled or nothing is bound.
    """
    cfg_hotkeys = cfg_hotkeys or {}
    if not cfg_hotkeys.get("enabled", True):
        return None
    bindings = {}
    for name, fn in handlers.items():
        spec = (cfg_hotkeys.get(name) or "").strip()
        if spec and spec not in bindings:
            bindings[spec] = fn
    if not bindings:
        return None
    mgr = HotkeyManager(bindings, stop_event)
    mgr.start()
    return mgr
