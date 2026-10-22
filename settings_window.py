"""The settings window.

Only settings that are safe to change while the app is running are offered
here: what is shown, which languages are captioned, how strict the voice
detector is, and how the overlay looks. Each one is read live by the part of
the app that uses it, so a change applies at once without a restart.

Deliberately left out, because changing them mid-session would break or stall
something: the device (a recompile), the capture backend and process name,
hotkeys (registered once at startup), and the console.log path. Those remain
editable in config.json. The speech model is the one exception: offered
here, but applied at the next start, and the window says so.
"""
import copy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QMenu, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget,
    QFormLayout,
)

import autostart
import config as config_mod
import hotkeys

# Every setting this window owns, as (section, key). "Restore defaults" resets
# exactly these and nothing else.
OWNED = [
    ("chat", "show"), ("chat", "translate"),
    ("translation", "only_foreign"), ("translation", "show_original"),
    ("translation", "languages"),
    ("translation", "slang"),
    ("vad", "min_utterance_s"), ("vad", "speech_threshold"), ("stt", "model"),
    ("overlay", "font_size"), ("overlay", "width"),
    ("overlay", "max_lines"), ("overlay", "line_ttl_s"),
    ("overlay", "position"), ("overlay", "enabled"), ("overlay", "opacity"),
    ("transcript", "enabled"), ("updates", "check"),
]

# Speech models offered. Others (tiny, base, a path) can still be set in
# config.json; the box then shows them as they are.
MODELS = [("small", "Standard"), ("medium", "Better, slower")]

# Overlay placements offered in the Position box; see Overlay.PRESETS.
POSITIONS = [
    ("custom", "Where I dragged it"),
    ("top-left", "Top left"), ("top-center", "Top centre"),
    ("top-right", "Top right"), ("bottom-left", "Bottom left"),
    ("bottom-center", "Bottom centre"), ("bottom-right", "Bottom right"),
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
QComboBox { background:#1b1f25; border:1px solid #353b45; border-radius:5px;
            padding:3px 6px; }
QComboBox QAbstractItemView { background:#1b1f25; selection-background-color:#2d333c; }
QMenu { background:#1b1f25; border:1px solid #353b45; }
QMenu::item { padding:4px 18px; }
QMenu::item:selected { background:#2d333c; }
QSpinBox, QDoubleSpinBox { background:#1b1f25; border:1px solid #353b45;
            border-radius:5px; padding:3px 6px; min-width:84px; }
QPushButton { background:#23272e; border:1px solid #353b45; border-radius:6px;
            padding:5px 14px; }
QPushButton:hover { background:#2d333c; }
QCheckBox:disabled { color:#5c636d; }
QToolTip { background:#23272e; color:#e8e8e8; border:1px solid #3a414c;
            padding:6px; font-size:12px; }
QCheckBox::indicator { width:14px; height:14px; border:1px solid #4a525e;
            border-radius:3px; background:#1b1f25; }
QCheckBox::indicator:hover { border-color:#6b7584; }
QCheckBox::indicator:checked { background:#3d8bfd; border-color:#3d8bfd; }
QCheckBox::indicator:disabled { background:#15181d; border-color:#2a2f37; }
QCheckBox::indicator:checked:disabled { background:#2a4670; border-color:#2a4670; }
"""


def _tip(text):
    """Rich text, so Qt wraps a long tooltip instead of drawing one very wide
    line across the screen."""
    import html
    return f"<qt>{html.escape(text)}</qt>"


def _tipped(widget, text):
    widget.setToolTip(_tip(text))
    return widget


def _row(form, label, field, text):
    """A form row whose label and field both explain themselves on hover."""
    lbl = _tipped(QLabel(label), text)
    field.setToolTip(_tip(text))
    form.addRow(lbl, field)


class SettingsWindow(QWidget):
    def __init__(self, cfg, on_change=None):
        super().__init__()
        self.cfg = cfg
        self._on_change = on_change
        # The model is loaded once at start; a change applies after a restart.
        self._running_model = self._get("stt", "model")
        self.setObjectName("Settings")
        from version import __version__
        self.setWindowTitle(f"CS Voice Captions {__version__} — Settings")
        self.setStyleSheet(_STYLE)
        self.setMinimumWidth(860)

        # Saving is debounced: dragging a slider fires dozens of changes.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(lambda: config_mod.save(self.cfg))

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 8, 14, 14)
        # Two columns: in one the window grew taller than a laptop screen.
        # What is shown on the left, how it is heard and drawn on the right;
        # the groups are split so the columns come out about the same height.
        columns = QHBoxLayout()
        columns.setSpacing(14)
        for groups in ((self._build_captions(), self._build_languages(),
                        self._build_general()),
                       (self._build_voice(), self._build_overlay(),
                        self._build_hotkeys())):
            col = QVBoxLayout()
            for g in groups:
                col.addWidget(g)
            col.addStretch(1)
            columns.addLayout(col, 1)
        root.addLayout(columns)

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
        self.cb_chat = _tipped(QCheckBox("Show text chat"),
            "Show what players type in all-chat and team chat. "
            "Also in the tray menu.")
        self.cb_chat_tr = _tipped(QCheckBox("Translate chat"),
            "Translate chat lines that aren't in English. Lines are sent to "
            "Google Translate (or MyMemory if Google is unavailable); "
            "in-match chat is public, and nothing else leaves your PC.")
        self.cb_english = _tipped(QCheckBox("Also caption English speech"),
            "English voice chat is hidden by default, because you can hear "
            "it yourself. Turn this on to caption it too.")
        self.cb_original = _tipped(QCheckBox("Show the original beside translations"),
            "Show what was actually said, in grey, after each translation. "
            "Handy for checking a translation that looks odd.")
        for cb in (self.cb_chat, self.cb_chat_tr, self.cb_english, self.cb_original):
            lay.addWidget(cb)
        self.bt_muted = _tipped(QPushButton(),
            "Players whose chat is hidden. Right-click a name in the captions "
            "window to mute it; pick a name here to show their chat again.")
        self.bt_muted.clicked.connect(self._muted_menu)
        row = QHBoxLayout()
        row.addWidget(self.bt_muted)
        row.addStretch(1)
        lay.addLayout(row)
        self.cb_chat.toggled.connect(lambda v: self._set("chat", "show", v))
        self.cb_chat_tr.toggled.connect(lambda v: self._set("chat", "translate", v))
        self.cb_english.toggled.connect(
            lambda v: self._set("translation", "only_foreign", not v))
        self.cb_original.toggled.connect(
            lambda v: self._set("translation", "show_original", v))
        return box

    def _build_languages(self):
        box = _tipped(QGroupBox("Translate speech in"),
            "Which spoken languages get captioned. On short clips the "
            "language guess is unreliable: noise often comes out as a "
            "random language. Naming the ones you need keeps most junk "
            "captions away.")
        lay = QVBoxLayout(box)
        grid = QGridLayout()
        self.cb_any = _tipped(QCheckBox("Any language"),
            "Caption every foreign language. Expect more junk captions: "
            "short noises often get mistaken for some language or other.")
        self.cb_any.toggled.connect(self._any_toggled)
        grid.addWidget(self.cb_any, 0, 0)
        self.lang_boxes = {}
        for i, (code, name) in enumerate(LANGUAGES, start=1):
            cb = _tipped(QCheckBox(name), f"Caption speech in {name}.")
            cb.toggled.connect(self._languages_changed)
            self.lang_boxes[code] = cb
            grid.addWidget(cb, i // 3, i % 3)
        lay.addLayout(grid)
        lay.addSpacing(6)
        self.cb_slang = _tipped(QCheckBox("Explain slang and swear words"),
            "Machine translation gets gaming slang and insults wrong: "
            "\u201c\u0435\u0441\u0442\u044c!\u201d means \u201cGot him!\u201d, "
            "not \u201cYes\u201d. Fixes what it can, and adds a short note "
            "under the caption for the rest.")
        self.cb_slang.toggled.connect(lambda v: self._set("translation", "slang", v))
        row = QHBoxLayout()
        row.addWidget(self.cb_slang, 1)
        edit = _tipped(QPushButton("Edit dictionary\u2026"),
            "Open your own slang file to add words. It is merged with the "
            "built-in dictionary and used a few seconds after you save.")
        edit.clicked.connect(self._edit_dictionary)
        row.addWidget(edit)
        lay.addLayout(row)
        return box

    def _edit_dictionary(self):
        """Open the user's own slang.json (made from a template the first
        time) in whatever edits .json files on this machine."""
        import os
        import glossary
        try:
            os.startfile(glossary.user_file())
        except Exception as e:  # noqa: BLE001
            print(f"[settings] could not open the dictionary: {e}")

    def _build_voice(self):
        box = QGroupBox("Voice")
        form = QFormLayout(box)
        self.sp_phrase = QDoubleSpinBox()
        self.sp_phrase.setRange(0.0, 5.0)
        self.sp_phrase.setSingleStep(0.5)
        self.sp_phrase.setDecimals(1)
        self.sp_phrase.setSuffix(" s")
        self.sp_phrase.valueChanged.connect(
            lambda v: self._set("vad", "min_utterance_s", round(float(v), 2)))
        _row(form, "Ignore speech shorter than", self.sp_phrase,
             "Speech shorter than this isn't captioned. Very short clips are "
             "often noise or mistranslated, but most callouts are under two "
             "seconds, so raising it costs real ones. Default 0.7 s.")

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        left = QLabel("Catch more")
        left.setObjectName("Hint")
        right = QLabel("Stricter")
        right.setObjectName("Hint")
        self.co_model = QComboBox()
        for key, name in MODELS:
            self.co_model.addItem(name, key)
        self.co_model.currentIndexChanged.connect(self._model_changed)
        _row(form, "Translation quality", self.co_model,
             "Better uses the larger \u201cmedium\u201d speech model. On 21 "
             "Russian test clips it translated 8 better than Standard and 4 "
             "worse, and takes about 0.85 s a clip instead of 0.3 s. It "
             "downloads 1.5 GB once and needs a couple of minutes to get "
             "ready the first time. Applies the next time the app starts.")
        self.lb_model = QLabel("Restart the app to switch.")
        self.lb_model.setObjectName("Hint")
        self.lb_model.setVisible(False)
        form.addRow("", self.lb_model)

        self.sl_sens = QSlider(Qt.Horizontal)
        self.sl_sens.setRange(30, 80)          # speech_threshold 0.30 .. 0.80
        self.sl_sens.valueChanged.connect(
            lambda v: self._set("vad", "speech_threshold", round(v / 100, 2)))
        row.addWidget(left)
        row.addWidget(self.sl_sens, 1)
        row.addWidget(right)
        holder = QWidget()
        holder.setLayout(row)
        _row(form, "Speech detection", holder,
             "How sure it must be that someone is talking. Move toward "
             "Stricter if game sounds or music get captioned; toward Catch "
             "more if quiet teammates are missed.")
        return box

    def _build_overlay(self):
        box = _tipped(QGroupBox("Game overlay"),
            "The captions drawn over the game. Turn it on from the tray menu; "
            "it needs CS2 in Fullscreen Windowed mode.")
        form = QFormLayout(box)
        key = dict(hotkeys.bound(self.cfg.get("hotkeys"))).get("toggle_overlay")
        self.cb_overlay = _tipped(QCheckBox(
            "Show the overlay in game" + (f"  ({key})" if key else "")),
            "Captions drawn over CS2. Needs CS2 in Fullscreen Windowed: "
            "exclusive fullscreen covers every other window. Invisible "
            "until there is something to show.")
        self.cb_overlay.toggled.connect(lambda v: self._set("overlay", "enabled", v))
        form.addRow(self.cb_overlay)
        self.sp_font = self._spin(12, 40, 1, " px", "overlay", "font_size")
        self.sp_width = self._spin(400, 1600, 20, " px", "overlay", "width")
        self.sp_lines = self._spin(1, 10, 1, "", "overlay", "max_lines")
        self.sp_ttl = self._spin(3, 30, 1, " s", "overlay", "line_ttl_s")
        self.sp_opacity = self._spin(0, 100, 5, " %", "overlay", "opacity")
        _row(form, "Text size", self.sp_font, "Size of the overlay's text.")
        _row(form, "Width", self.sp_width, "How wide the overlay is; longer captions wrap.")
        _row(form, "Lines on screen", self.sp_lines,
             "How many captions the overlay shows at once. The oldest goes first.")
        _row(form, "Seconds per line", self.sp_ttl,
             "How long each caption stays on the overlay before it fades out.")
        _row(form, "Background", self.sp_opacity,
             "How dark the box behind the text is. 0 % is no box at all; "
             "higher is easier to read over a bright map.")
        self.co_pos = QComboBox()
        for key, name in POSITIONS:
            self.co_pos.addItem(name, key)
        self.co_pos.currentIndexChanged.connect(
            lambda _i: self._set("overlay", "position", self.co_pos.currentData()))
        _row(form, "Position", self.co_pos,
             "Where the overlay sits on the screen CS2 is on (the main screen "
             "when CS2 isn't running). At the bottom it "
             "grows upwards as captions arrive. Dragging the overlay (unlock "
             "it from the tray menu) puts it wherever you drop it.")
        return box

    def _build_hotkeys(self):
        box = _tipped(QGroupBox("Hotkeys"),
            "Work while CS2 has focus. To change them, set hotkeys.* in "
            "config.json; see the configuration guide.")
        form = QFormLayout(box)
        keys = hotkeys.bound(self.cfg.get("hotkeys"))
        for name, key in keys:
            k = QLabel(key)
            k.setStyleSheet("color:#c9cdd3; font-family:Consolas, monospace;")
            form.addRow(QLabel(hotkeys.ACTIONS[name]), k)
        if not keys:
            off = QLabel("Turned off in config.json.")
            off.setObjectName("Hint")
            form.addRow(off)
        return box

    def _build_general(self):
        box = QGroupBox("General")
        lay = QVBoxLayout(box)
        self.cb_autostart = _tipped(QCheckBox("Start with Windows"),
            "Start the app, in the tray, when you sign in to Windows.")
        if not autostart.supported():
            self.cb_autostart.setEnabled(False)
            self.cb_autostart.setToolTip(_tip(
                "Only available in the built exe, not when run from source."))
        self.cb_autostart.toggled.connect(self._autostart_toggled)
        self.cb_transcript = _tipped(QCheckBox("Save transcripts"),
            "Write every caption to a text file, one per session, so you can "
            "read back what was said after the match. Saved in the "
            "\u201ctranscripts\u201d folder in the app's data folder.")
        self.cb_transcript.toggled.connect(
            lambda v: self._set("transcript", "enabled", v))
        self.cb_updates = _tipped(QCheckBox("Tell me when there's a new version"),
            "On start, look at the app's GitHub releases page and say if a "
            "newer version is out. Nothing is downloaded or sent.")
        self.cb_updates.toggled.connect(lambda v: self._set("updates", "check", v))
        for cb in (self.cb_autostart, self.cb_transcript, self.cb_updates):
            lay.addWidget(cb)
        return box

    def _model_changed(self, _i):
        key = self.co_model.currentData()
        if key is not None:
            self._set("stt", "model", key)
        self.lb_model.setVisible(self._get("stt", "model") != self._running_model)

    def _autostart_toggled(self, on):
        if getattr(self, "_loading", False):
            return
        if not autostart.set_enabled(on):
            self._loading = True
            self.cb_autostart.setChecked(autostart.is_enabled())
            self._loading = False

    def _muted(self):
        return self.cfg.setdefault("chat", {}).setdefault("muted", [])

    def _update_muted_button(self):
        n = len(self._muted())
        self.bt_muted.setText(f"Muted players ({n})\u2026" if n else "No muted players")
        self.bt_muted.setEnabled(bool(n))

    def _muted_menu(self):
        menu = QMenu(self)
        for name in sorted(self._muted(), key=str.lower):
            menu.addAction(f"Unmute {name}").triggered.connect(
                lambda _c=False, n=name: self._unmute(n))
        menu.addSeparator()
        menu.addAction("Unmute everyone").triggered.connect(lambda: self._unmute(None))
        menu.exec(self.bt_muted.mapToGlobal(self.bt_muted.rect().bottomLeft()))

    def _unmute(self, name):
        muted = self._muted()
        if name is None:
            muted.clear()
        elif name in muted:
            muted.remove(name)
        self._update_muted_button()
        self._set("chat", "muted", muted)

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
            self.cb_slang.setChecked(bool(self._get("translation", "slang")))
            self.sp_phrase.setValue(float(self._get("vad", "min_utterance_s")))
            self.sl_sens.setValue(int(round(float(self._get("vad", "speech_threshold")) * 100)))
            self.sp_font.setValue(int(self._get("overlay", "font_size")))
            self.sp_width.setValue(int(self._get("overlay", "width")))
            self.sp_lines.setValue(int(self._get("overlay", "max_lines")))
            self.sp_ttl.setValue(int(self._get("overlay", "line_ttl_s")))
            self.sp_opacity.setValue(int(self._get("overlay", "opacity")))
            self.cb_overlay.setChecked(bool(self._get("overlay", "enabled")))
            model = self._get("stt", "model")
            mkeys = [k for k, _ in MODELS]
            if model not in mkeys and self.co_model.findData(model) < 0:
                self.co_model.addItem(str(model), model)
            self.co_model.setCurrentIndex(self.co_model.findData(model))
            self.lb_model.setVisible(model != self._running_model)
            keys = [k for k, _ in POSITIONS]
            pos = self._get("overlay", "position")
            self.co_pos.setCurrentIndex(keys.index(pos) if pos in keys else 0)
            self.cb_autostart.setChecked(autostart.supported() and autostart.is_enabled())
            self.cb_transcript.setChecked(bool(self._get("transcript", "enabled")))
            self.cb_updates.setChecked(bool(self._get("updates", "check")))
            self._update_muted_button()
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
        from ui import bring_to_front
        bring_to_front(self)
