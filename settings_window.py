"""The settings window.

Only settings that are safe to change while the app is running are offered
here: what is shown, which languages are captioned, how strict the voice
detector is, and how the overlay looks. Each one is read live by the part of
the app that uses it, so a change applies at once without a restart.

Deliberately left out, because changing them mid-session would break or stall
something: the speech model and device (a re-download and a recompile), the
capture backend and process name, hotkeys (registered once at startup), and
the console.log path. Those remain editable in config.json.
"""
import copy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget, QFormLayout,
)

import config as config_mod

# Every setting this window owns, as (section, key). "Restore defaults" resets
# exactly these and nothing else.
OWNED = [
    ("chat", "show"), ("chat", "translate"),
    ("translation", "only_foreign"), ("translation", "show_original"),
    ("translation", "languages"),
    ("vad", "min_utterance_s"), ("vad", "speech_threshold"),
    ("overlay", "font_size"), ("overlay", "width"),
    ("overlay", "max_lines"), ("overlay", "line_ttl_s"),
]

# Spoken languages offered as checkboxes. English is not here: whether English
# speech is shown is its own switch.
LANGUAGES = [
    ("ru", "Russian"), ("uk", "Ukrainian"), ("pl", "Polish"), ("tr", "Turkish"),
    ("de", "German"), ("es", "Spanish"), ("pt", "Portuguese"), ("fr", "French"),
]

_STYLE = """
QWidget#Settings { background:#121417; }
QWidget { color:#e8e8e8; font-family:'Segoe UI', sans-serif; font-size:13px; }
QGroupBox { border:1px solid #2a2f37; border-radius:8px; margin-top:14px;
            padding:12px 10px 8px 10px; font-weight:600; }
QGroupBox::title { subcontrol-origin: margin; left:10px; padding:0 4px; color:#9fb3c8; }
QLabel#Hint { color:#8a929c; font-size:12px; }
QSpinBox, QDoubleSpinBox { background:#1b1f25; border:1px solid #353b45;
            border-radius:5px; padding:3px 6px; min-width:84px; }
QPushButton { background:#23272e; border:1px solid #353b45; border-radius:6px;
            padding:5px 14px; }
QPushButton:hover { background:#2d333c; }
QCheckBox:disabled { color:#5c636d; }
QCheckBox::indicator { width:14px; height:14px; border:1px solid #4a525e;
            border-radius:3px; background:#1b1f25; }
QCheckBox::indicator:hover { border-color:#6b7584; }
QCheckBox::indicator:checked { background:#3d8bfd; border-color:#3d8bfd; }
QCheckBox::indicator:disabled { background:#15181d; border-color:#2a2f37; }
QCheckBox::indicator:checked:disabled { background:#2a4670; border-color:#2a4670; }
"""


class SettingsWindow(QWidget):
    def __init__(self, cfg, on_change=None):
        super().__init__()
        self.cfg = cfg
        self._on_change = on_change
        self.setObjectName("Settings")
        self.setWindowTitle("CS Voice Captions — Settings")
        self.setStyleSheet(_STYLE)
        self.setMinimumWidth(460)

        # Saving is debounced: dragging a slider fires dozens of changes.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(lambda: config_mod.save(self.cfg))

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 8, 14, 14)
        root.addWidget(self._build_captions())
        root.addWidget(self._build_languages())
        root.addWidget(self._build_voice())
        root.addWidget(self._build_overlay())

        note = QLabel("Changes apply immediately and are saved automatically.")
        note.setObjectName("Hint")
        buttons = QHBoxLayout()
        buttons.addWidget(note, 1)
        reset = QPushButton("Restore defaults")
        reset.clicked.connect(self.restore_defaults)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addWidget(reset)
        buttons.addWidget(close)
        root.addLayout(buttons)

        self.refresh()

    # -- sections ----------------------------------------------------------
    def _build_captions(self):
        box = QGroupBox("Captions")
        lay = QVBoxLayout(box)
        self.cb_chat = QCheckBox("Show text chat messages")
        self.cb_chat_tr = QCheckBox("Translate foreign text chat")
        self.cb_english = QCheckBox("Show English speech (normally hidden — you can already understand it)")
        self.cb_original = QCheckBox("Show the original text beside each translation")
        for cb in (self.cb_chat, self.cb_chat_tr, self.cb_english, self.cb_original):
            lay.addWidget(cb)
        self.cb_chat.toggled.connect(lambda v: self._set("chat", "show", v))
        self.cb_chat_tr.toggled.connect(lambda v: self._set("chat", "translate", v))
        self.cb_english.toggled.connect(
            lambda v: self._set("translation", "only_foreign", not v))
        self.cb_original.toggled.connect(
            lambda v: self._set("translation", "show_original", v))
        return box

    def _build_languages(self):
        box = QGroupBox("Spoken languages to caption")
        lay = QVBoxLayout(box)
        hint = QLabel("On short clips Whisper's language guess is unreliable, so "
                      "naming the languages you need removes most junk captions.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.cb_any = QCheckBox("Any language")
        self.cb_any.toggled.connect(self._any_toggled)
        lay.addWidget(self.cb_any)
        grid = QGridLayout()
        self.lang_boxes = {}
        for i, (code, name) in enumerate(LANGUAGES):
            cb = QCheckBox(name)
            cb.toggled.connect(self._languages_changed)
            self.lang_boxes[code] = cb
            grid.addWidget(cb, i // 4, i % 4)
        lay.addLayout(grid)
        return box

    def _build_voice(self):
        box = QGroupBox("Voice detection")
        form = QFormLayout(box)
        self.sp_phrase = QDoubleSpinBox()
        self.sp_phrase.setRange(0.0, 5.0)
        self.sp_phrase.setSingleStep(0.5)
        self.sp_phrase.setDecimals(1)
        self.sp_phrase.setSuffix(" s")
        self.sp_phrase.valueChanged.connect(
            lambda v: self._set("vad", "min_utterance_s", round(float(v), 2)))
        form.addRow("Shortest phrase to caption", self.sp_phrase)

        row = QHBoxLayout()
        left = QLabel("Catch more")
        left.setObjectName("Hint")
        right = QLabel("Stricter")
        right.setObjectName("Hint")
        self.sl_sens = QSlider(Qt.Horizontal)
        self.sl_sens.setRange(30, 80)          # speech_threshold 0.30 .. 0.80
        self.sl_sens.valueChanged.connect(
            lambda v: self._set("vad", "speech_threshold", round(v / 100, 2)))
        row.addWidget(left)
        row.addWidget(self.sl_sens, 1)
        row.addWidget(right)
        form.addRow("Voice detector", row)
        return box

    def _build_overlay(self):
        box = QGroupBox("Game overlay")
        form = QFormLayout(box)
        self.sp_font = self._spin(12, 40, 1, " px", "overlay", "font_size")
        self.sp_width = self._spin(400, 1600, 20, " px", "overlay", "width")
        self.sp_lines = self._spin(1, 10, 1, "", "overlay", "max_lines")
        self.sp_ttl = self._spin(3, 30, 1, " s", "overlay", "line_ttl_s")
        form.addRow("Text size", self.sp_font)
        form.addRow("Width", self.sp_width)
        form.addRow("Lines shown at once", self.sp_lines)
        form.addRow("Seconds each line stays", self.sp_ttl)
        return box

    def _spin(self, lo, hi, step, suffix, section, key):
        sp = QSpinBox()
        sp.setRange(lo, hi)
        sp.setSingleStep(step)
        sp.setSuffix(suffix)
        sp.valueChanged.connect(lambda v: self._set(section, key, int(v)))
        return sp

    # -- state -------------------------------------------------------------
    def _get(self, section, key):
        return self.cfg.get(section, {}).get(key, config_mod.DEFAULTS[section][key])

    def _set(self, section, key, value):
        if getattr(self, "_loading", False):
            return
        self.cfg.setdefault(section, {})[key] = value
        self._save_timer.start()
        if self._on_change:
            self._on_change(section, key)

    def _any_toggled(self, checked):
        if getattr(self, "_loading", False):
            return
        if not checked and not any(cb.isChecked() for cb in self.lang_boxes.values()):
            # Unticking "Any" with nothing selected would leave an empty list,
            # which means "any" again -- the box would just re-tick itself.
            # Start from the default selection instead.
            self._loading = True
            for code in config_mod.DEFAULTS["translation"]["languages"]:
                if code in self.lang_boxes:
                    self.lang_boxes[code].setChecked(True)
            self._loading = False
        self._languages_changed()

    def _languages_changed(self, *_):
        if getattr(self, "_loading", False):
            return
        any_lang = self.cb_any.isChecked()
        for cb in self.lang_boxes.values():
            cb.setEnabled(not any_lang)
        if any_lang:
            self._set("translation", "languages", [])
            return
        chosen = [c for c, cb in self.lang_boxes.items() if cb.isChecked()]
        # Keep any codes added by hand in config.json that have no checkbox.
        extra = [c for c in self._get("translation", "languages")
                 if c not in self.lang_boxes]
        langs = chosen + extra
        if not langs:
            # An empty list means "any language", so unticking the last box
            # would silently do the opposite of what it looks like. Say so.
            self._loading = True
            self.cb_any.setChecked(True)
            for cb in self.lang_boxes.values():
                cb.setEnabled(False)
            self._loading = False
        self._set("translation", "languages", langs)

    def refresh(self):
        """Load the widgets from the config without firing change handlers."""
        self._loading = True
        try:
            self.cb_chat.setChecked(bool(self._get("chat", "show")))
            self.cb_chat_tr.setChecked(bool(self._get("chat", "translate")))
            self.cb_english.setChecked(not self._get("translation", "only_foreign"))
            self.cb_original.setChecked(bool(self._get("translation", "show_original")))
            langs = [str(c).lower() for c in self._get("translation", "languages")]
            self.cb_any.setChecked(not langs)
            for code, cb in self.lang_boxes.items():
                cb.setChecked(code in langs)
                cb.setEnabled(bool(langs))
            self.sp_phrase.setValue(float(self._get("vad", "min_utterance_s")))
            self.sl_sens.setValue(int(round(float(self._get("vad", "speech_threshold")) * 100)))
            self.sp_font.setValue(int(self._get("overlay", "font_size")))
            self.sp_width.setValue(int(self._get("overlay", "width")))
            self.sp_lines.setValue(int(self._get("overlay", "max_lines")))
            self.sp_ttl.setValue(int(self._get("overlay", "line_ttl_s")))
        finally:
            self._loading = False

    def restore_defaults(self):
        for section, key in OWNED:
            self.cfg.setdefault(section, {})[key] = copy.deepcopy(
                config_mod.DEFAULTS[section][key])
        self.refresh()
        config_mod.save(self.cfg)
        if self._on_change:
            for section, key in OWNED:
                self._on_change(section, key)

    def open(self):
        self.refresh()          # the tray may have changed something meanwhile
        self.show()
        self.raise_()
        self.activateWindow()
