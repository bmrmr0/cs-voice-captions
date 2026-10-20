"""Start with Windows: a value under the current user's Run key that points
at this exe, the standard per-user mechanism (no admin rights needed).

Only meaningful for the built exe -- run from source there is no single
file to start -- so `supported()` says so and the Settings option is
disabled there.
"""
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "CS Voice Captions"


def supported():
    return sys.platform.startswith("win") and bool(getattr(sys, "frozen", False))


def command():
    return f'"{sys.executable}"'


def current():
    """The registered command, or None if the app isn't set to start."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            value, _ = winreg.QueryValueEx(k, VALUE)
        return value or None
    except OSError:
        return None


def is_enabled():
    return current() is not None


def set_enabled(on):
    """Returns True on success."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, VALUE, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(k, VALUE)
                except FileNotFoundError:
                    pass
        return True
    except OSError as e:
        print(f"[autostart] could not {'enable' if on else 'disable'}: {e}")
        return False


def repair():
    """If the exe was moved since startup was turned on, point the entry at
    where it is now -- otherwise Windows would silently start nothing."""
    if supported() and is_enabled() and current() != command():
        set_enabled(True)
