"""The first-start welcome: where captions appear, what CS2 needs for them,
and the hotkeys. Shown once; reopened from the tray menu ("Welcome & help").

Most first-run confusion is one of three things nothing else explains: the
overlay needs CS2 in Fullscreen Windowed, text chat needs -condebug, and the
hotkeys exist at all. This puts the three on one screen.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout,
)

import hotkeys
from settings_window import LANGUAGES

_STYLE = """
QDialog { background:#121417; }
QWidget { color:#e8e8e8; font-family:'Segoe UI', sans-serif; font-size:13px; }
QLabel#Title { font-size:18px; font-weight:600; }
QLabel#Head { font-size:14px; font-weight:600; color:#9fb3c8; padding-top:6px; }
QLabel#Hint { color:#8a929c; font-size:12px; }
QLabel#Key { font-family:Consolas, monospace; color:#c9cdd3; }
QLabel#Code { background:#1b1f25; border:1px solid #353b45; border-radius:6px;
              padding:4px 10px; font-family:Consolas, monospace; font-size:14px;
              color:#4cd07d; }
QPushButton { background:#23272e; border:1px solid #353b45; border-radius:6px;
              padding:5px 14px; }
QPushButton:hover { background:#2d333c; }
QPushButton#Primary { background:#2f6fd6; border-color:#2f6fd6; }
"""


def _label(text, name=None, rich=False):
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    if name:
        lbl.setObjectName(name)
    if rich:
        lbl.setTextFormat(Qt.RichText)
    return lbl


class WelcomeWindow(QDialog):
    def __init__(self, cfg, condebug=None, on_settings=None, parent=None):
        """condebug: chat_log.condebug_status() -- True, False or None."""
        super().__init__(parent)
        self.setWindowTitle("Welcome to CS Voice Captions")
        self.setStyleSheet(_STYLE)
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(8)

        lay.addWidget(_label("Welcome to CS Voice Captions", "Title"))
        langs = [dict(LANGUAGES).get(c, c) for c in cfg["translation"].get("languages", [])]
        heard = (", ".join(langs[:-1]) + " and " + langs[-1] if len(langs) > 1
                 else langs[0] if langs else "any language")
        lay.addWidget(_label(
            f"It translates CS2 voice chat in <b>{heard}</b> into English, plus "
            "text chat. It runs in the tray; captions appear in the captions "
            "window and on an overlay over the game.", rich=True))

        lay.addWidget(_label("For the overlay", "Head"))
        lay.addWidget(_label(
            "Set CS2's display mode to <b>Fullscreen Windowed</b> (Settings \u2192 "
            "Video). Exclusive fullscreen draws over every other window. The "
            "overlay only appears while there is something to show.", rich=True))

        lay.addWidget(_label("For text chat", "Head"))
        if condebug is True:
            lay.addWidget(_label("\u2714 CS2 is already started with "
                                 "<b>-condebug</b>, so text chat will work.", rich=True))
        else:
            lead = ("CS2's launch options don't have <b>-condebug</b> yet. "
                    if condebug is False else "")
            lay.addWidget(_label(
                f"{lead}In Steam, right-click <b>Counter-Strike 2</b> \u2192 "
                "<b>Properties\u2026</b> \u2192 <b>General</b>, add this to "
                "<b>Launch Options</b>, then restart CS2:", rich=True))
            row = QHBoxLayout()
            code = _label("-condebug", "Code")
            code.setTextInteractionFlags(Qt.TextSelectableByMouse)
            code.setWordWrap(False)
            row.addWidget(code)
            self._copy = QPushButton("Copy")
            self._copy.clicked.connect(self._copy_option)
            row.addWidget(self._copy)
            row.addStretch(1)
            lay.addLayout(row)
            lay.addWidget(_label("It makes CS2 write its chat to a log file, which "
                                 "the app reads. Valve's own option; nothing "
                                 "touches the game.", "Hint"))

        keys = hotkeys.bound(cfg.get("hotkeys"))
        if keys:
            lay.addWidget(_label("Hotkeys (they work while CS2 has focus)", "Head"))
            grid = QGridLayout()
            grid.setHorizontalSpacing(18)
            for i, (name, key) in enumerate(keys):
                k = QLabel(key)
                k.setObjectName("Key")
                grid.addWidget(k, i, 0)
                grid.addWidget(QLabel(hotkeys.ACTIONS[name]), i, 1)
            grid.setColumnStretch(1, 1)
            lay.addLayout(grid)

        lay.addWidget(_label("Right-click a player's name in the captions window to "
                             "mute their chat. Everything else is in Settings; "
                             "hover over an option to see what it does. This "
                             "window is in the tray menu under Welcome & help.",
                             "Hint"))

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        if on_settings is not None:
            settings = QPushButton("Open Settings")
            settings.clicked.connect(lambda: (self.accept(), on_settings()))
            buttons.addWidget(settings)
        ok = QPushButton("Got it")
        ok.setObjectName("Primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        buttons.addWidget(ok)
        lay.addSpacing(6)
        lay.addLayout(buttons)

    def _copy_option(self):
        QApplication.clipboard().setText("-condebug")
        self._copy.setText("Copied")
