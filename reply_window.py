"""The reply helper: type in English, get it in the teammates' language on
the clipboard, and paste it into the game chat yourself.

Nothing is typed into the game. Sending input to CS2 is exactly what this
app promises never to do, so the last step -- Ctrl+V and Enter in the chat
box -- is always the player's own.

What you type is sent to the same translation service as chat (Google, then
MyMemory), since it is about to be posted in a public chat anyway.
"""
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

import config as config_mod
from settings_window import LANGUAGES

_STYLE = """
QWidget#Reply { background:#121417; }
QWidget { color:#e8e8e8; font-family:'Segoe UI', sans-serif; font-size:13px; }
QLineEdit, QComboBox { background:#1b1f25; border:1px solid #353b45;
            border-radius:5px; padding:5px 7px; }
QComboBox QAbstractItemView { background:#1b1f25; selection-background-color:#2d333c; }
QPushButton { background:#23272e; border:1px solid #353b45; border-radius:6px;
            padding:5px 14px; }
QPushButton:hover { background:#2d333c; }
QLabel#Result { font-size:15px; padding:4px 2px; }
QLabel#Hint { color:#8a929c; font-size:12px; }
"""


class ReplyWindow(QWidget):
    _done = Signal(int, object)          # request number, translation or None

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self._request = 0
        self.setObjectName("Reply")
        self.setWindowTitle("Reply in chat")
        self.setStyleSheet(_STYLE)
        self.setMinimumWidth(460)
        # Above the game (in Fullscreen Windowed), like the overlay.
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Type in English, press Enter")
        self.input.returnPressed.connect(self.translate)
        self.lang = QComboBox()
        for code, name in LANGUAGES:
            self.lang.addItem(name, code)
        self.lang.setToolTip("The language to reply in. Remembered for next time.")
        self.lang.currentIndexChanged.connect(self._lang_changed)
        go = QPushButton("Translate && copy")
        go.clicked.connect(self.translate)

        top = QHBoxLayout()
        top.addWidget(self.input, 1)
        top.addWidget(self.lang)
        top.addWidget(go)
        self.result = QLabel()
        self.result.setObjectName("Result")
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.hint = QLabel()
        self.hint.setObjectName("Hint")
        self.hint.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.addLayout(top)
        lay.addWidget(self.result)
        lay.addWidget(self.hint)
        self._done.connect(self._finished)
        self._reset_hint()

    def _rcfg(self):
        return self.cfg.setdefault("reply", {})

    def _lang_changed(self, _i):
        self._rcfg()["language"] = self.lang.currentData()
        config_mod.save(self.cfg)

    def _reset_hint(self):
        self.hint.setText("The translation is copied for you to paste into the "
                          "game chat (Ctrl+V). Nothing is typed into the game. "
                          "Esc closes.")

    def open(self):
        code = self._rcfg().get("language", "ru")
        codes = [c for c, _ in LANGUAGES]
        self.lang.blockSignals(True)
        self.lang.setCurrentIndex(codes.index(code) if code in codes else 0)
        self.lang.blockSignals(False)
        self.input.selectAll()
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(ev)

    def translate(self):
        text = self.input.text().strip()
        if not text:
            return
        from translate import make_reply_translator
        translator = make_reply_translator(self.cfg, self.lang.currentData())
        if translator is None:
            self.result.setText("")
            self.hint.setText("Translation is switched off (chat.translator in "
                              "config.json).")
            return
        self._request += 1
        n = self._request
        self.result.setText("")
        self.hint.setText("Translating…")

        def run():
            try:
                out = translator.translate(text)
            except Exception as e:  # noqa: BLE001
                print(f"[reply] translation failed: {e}")
                out = None
            self._done.emit(n, out)

        threading.Thread(target=run, daemon=True, name="reply").start()

    def _finished(self, n, out):
        if n != self._request:
            return                    # an older request; a newer one is coming
        if not out:
            self.hint.setText("Couldn't translate right now (offline, or the "
                              "service is refusing). Try again in a moment.")
            return
        QGuiApplication.clipboard().setText(out)
        self.result.setText(out)
        self.hint.setText("Copied. Back in the game, open chat and press Ctrl+V.")
