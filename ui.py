"""PySide6 UI: a click-through caption overlay, a history window, and a tray
menu to control it all.

The overlay is a normal top-level window painted on top of the game. It never
injects into or hooks the game, which is what keeps the whole thing anti-cheat
safe. Run CS2 in *Fullscreen Windowed* mode so the overlay is visible.
"""
import time
from html import escape as html_escape

from PySide6.QtCore import Qt, QObject, Signal, QTimer, QPoint
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QTextEdit, QSystemTrayIcon,
    QMenu, QPushButton, QDialog, QCheckBox, QApplication,
)


def make_icon(color=None):
    """The app icon, with a status dot in `color` when given (the tray)."""
    import app_icon
    return app_icon.icon(badge=color)


class Bridge(QObject):
    """Thread-safe channel from the worker threads to the Qt main thread.

    Every cross-thread action goes through a signal: Qt widgets may only be
    touched on the GUI thread, and the audio/STT/chat/hotkey threads all need
    to reach it.
    """
    new_caption = Signal(object)
    status = Signal(str)
    toggle_pause = Signal()
    toggle_overlay = Signal()
    show_window = Signal()
    connection = Signal(str, str)     # capture state, detail -- see audio.py


# (colour, short text) for each capture state. Paused overrides all of them.
_CONNECTION_LOOK = {
    "connected": ("#4cd07d", "Connected to CS2"),
    "waiting": ("#e0a43a", "Waiting for CS2…"),
    "desktop": ("#e0a43a", "Capturing whole desktop"),
    "stopped": ("#e05a5a", "Not capturing"),
    "starting": ("#8a929c", "Starting…"),
}


def connection_look(state, detail="", paused=False):
    if paused:
        return "#8a929c", "Paused"
    color, text = _CONNECTION_LOOK.get(state, _CONNECTION_LOOK["starting"])
    if state == "waiting" and detail == "reconnecting":
        text = "Reconnecting to CS2…"
    return color, text


def _chat_tag(r):
    """"TEAM", or "TEAM · CT Start" when CS2 said where the player was."""
    scope = r.get("scope", "")
    loc = r.get("location", "")
    return html_escape(f"{scope} · {loc}" if scope and loc else scope)


def _gloss_html(r, size_px, block):
    """The slang note for a caption: each word the dictionary knows and what
    it means. Amber and small, so it reads as an aside to the caption rather
    than part of what was said."""
    items = r.get("gloss") or []
    if not items:
        return ""
    body = " &nbsp;·&nbsp; ".join(
        f"<b>{html_escape(w)}</b> = {html_escape(m)}" for w, m in items)
    sep = "<br>" if block else " "
    return f"{sep}<span style='color:#d9b25f;font-size:{size_px}px'>{body}</span>"


class Overlay(QWidget):
    def __init__(self, ocfg, on_move=None):
        super().__init__(None)
        self.ocfg = ocfg
        self._on_move = on_move          # called with (x, y) after a drag
        self._locked = ocfg.get("click_through", True)
        self._drag_from = None

        self._w = ocfg.get("width", 720)
        self._fs = ocfg.get("font_size", 20)
        self._max = ocfg.get("max_lines", 5)
        self._ttl = ocfg.get("line_ttl_s", 12)
        self._x = ocfg.get("x", 40)
        self._y = ocfg.get("y", 40)

        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._apply_flags()

        self.setFixedWidth(self._w)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._layout.setAlignment(Qt.AlignTop)

        self._items = []  # list of (QLabel, expiry_ts)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._prune)
        self._timer.start(500)
        self._clamp_to_screen()
        self.move(self._x, self._y)

    # -- window flags ------------------------------------------------------
    def _apply_flags(self):
        flags = (Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                 | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        if self._locked:
            # Click-through: the mouse belongs to the game, not to us.
            flags |= Qt.WindowTransparentForInput
        was_visible = self.isVisible()
        self.setWindowFlags(flags)
        if was_visible:
            self.show()

    @property
    def locked(self):
        return self._locked

    def set_locked(self, locked):
        """Unlocked = the overlay accepts the mouse so it can be dragged."""
        if locked == self._locked:
            return
        self._locked = locked
        self._apply_flags()
        self._refresh_hint()

    def _refresh_hint(self):
        # Repaint the existing labels so the drag affordance appears/disappears.
        for lbl, _ in self._items:
            lbl.setStyleSheet(self._label_style())

    # -- rendering ---------------------------------------------------------
    def _label_style(self):
        border = ("2px dashed rgba(124,252,124,200)" if not self._locked
                  else "2px solid rgba(0,0,0,0)")
        return (
            "QLabel {"
            " background: rgba(0,0,0,165);"
            " color: #ffffff;"
            " padding: 6px 10px;"
            " border-radius: 8px;"
            f" border: {border};"
            f" font-size: {self._fs}px;"
            " font-family: 'Segoe UI', sans-serif;"
            "}"
        )

    def _mk_label(self, html):
        lbl = QLabel(html)
        lbl.setTextFormat(Qt.RichText)
        lbl.setWordWrap(True)
        lbl.setFixedWidth(self._w)
        lbl.setStyleSheet(self._label_style())
        return lbl

    def _format(self, r):
        label = html_escape(r.get("label", "?"))
        color = r.get("color", "#ffffff")
        text = html_escape(r.get("text", ""))
        small = max(10, int(self._fs * 0.7))

        orig = ""
        if r.get("original"):
            orig = (f" <span style='color:#9aa3ad;font-size:{int(self._fs * 0.8)}px'>"
                    f"({html_escape(r['original'])})</span>")
        note = ""
        if r.get("note"):
            note = (f" <span style='color:#e88;font-size:{small}px'>"
                    f"[{html_escape(r['note'])}]</span>")

        if r.get("kind") == "chat":
            # speech bubble + real player name (from console.log) + chat channel
            scope = _chat_tag(r)
            scope_tag = (f"<span style='color:#888;font-size:{small}px'>[{scope}]</span> "
                         if scope else "")
            return (f"&#128172; <span style='color:{color};font-weight:bold'>{label}</span> "
                    f"{scope_tag}{text}{orig}{note}{_gloss_html(r, small, True)}")

        # voice: coloured dot + speaker name + source-language arrow
        tag = ""
        if r.get("translated"):
            tag = (f"<span style='color:#8a8a8a;font-size:{small}px'>"
                   f"[{html_escape(r.get('lang', '?'))}&#8594;]</span> ")
        dot = f"<span style='color:{color}'>&#9679;</span>"
        return (f"{dot} <span style='color:{color};font-weight:bold'>{label}</span> "
                f"{tag}{text}{orig}{note}{_gloss_html(r, small, True)}")

    # -- public API --------------------------------------------------------
    def apply_settings(self):
        """Re-read size, line count and duration from the config, restyling
        the lines already on screen."""
        o = self.ocfg
        self._fs = int(o.get("font_size", self._fs))
        self._max = max(1, int(o.get("max_lines", self._max)))
        self._ttl = float(o.get("line_ttl_s", self._ttl))
        self._w = int(o.get("width", self._w))
        self.setFixedWidth(self._w)
        for lbl, _ in self._items:
            lbl.setFixedWidth(self._w)
            lbl.setStyleSheet(self._label_style())
        while len(self._items) > self._max:
            old, _ = self._items.pop(0)
            self._drop(old)
        self._clamp_to_screen()
        self._reflow()

    def add_caption(self, r):
        self._add(self._format(r), self._ttl)

    def add_status(self, text):
        self._add(f"<i style='color:#aaaaaa'>{html_escape(text)}</i>", 6)

    def clear(self):
        for lbl, _ in self._items:
            self._drop(lbl)
        self._items = []
        self._reflow()

    # -- dragging ----------------------------------------------------------
    def mousePressEvent(self, ev):
        if not self._locked and ev.button() == Qt.LeftButton:
            self._drag_from = ev.globalPosition().toPoint() - self.pos()
            ev.accept()

    def mouseMoveEvent(self, ev):
        if self._drag_from is not None:
            self.move(ev.globalPosition().toPoint() - self._drag_from)
            ev.accept()

    def mouseReleaseEvent(self, ev):
        if self._drag_from is None:
            return
        self._drag_from = None
        self._x, self._y = self.x(), self.y()
        if self._on_move:
            self._on_move(self._x, self._y)
        ev.accept()

    # -- internals ---------------------------------------------------------
    def _add(self, html, ttl):
        lbl = self._mk_label(html)
        self._layout.addWidget(lbl)
        self._items.append((lbl, time.time() + ttl))
        while len(self._items) > self._max:
            old, _ = self._items.pop(0)
            self._drop(old)
        self._reflow()
        # Note: we intentionally do NOT auto-show here. The overlay is opt-in
        # and its visibility is controlled from the tray menu.

    def _drop(self, lbl):
        self._layout.removeWidget(lbl)
        lbl.deleteLater()

    def _clamp_to_screen(self):
        """Keep the overlay reachable if the saved position came from a monitor
        that is no longer attached."""
        screen = QGuiApplication.screenAt(QPoint(self._x, self._y))
        if screen is not None:
            return
        primary = QGuiApplication.primaryScreen()
        if primary is None:
            return
        geo = primary.availableGeometry()
        self._x = min(max(self._x, geo.x()), geo.x() + max(0, geo.width() - self._w))
        self._y = min(max(self._y, geo.y()), geo.y() + max(0, geo.height() - 100))

    def _reflow(self):
        self.adjustSize()
        # Dragging is the only thing allowed to change our position; a reflow
        # must not yank the overlay back to where it started.
        self.move(self._x, self._y)

    def _prune(self):
        now = time.time()
        kept, changed = [], False
        for lbl, exp in self._items:
            if now >= exp:
                self._drop(lbl)
                changed = True
            else:
                kept.append((lbl, exp))
        self._items = kept
        if changed:
            self._reflow()


class History(QWidget):
    def __init__(self, hcfg=None, on_settings=None, on_clear=None):
        super().__init__()
        self._on_clear = on_clear
        hcfg = hcfg or {}
        self.setWindowTitle("CS Voice Captions")
        self.resize(580, 640)
        self.setStyleSheet("History { background:#121417; }")
        self.view = QTextEdit()
        self.view.setReadOnly(True)
        # Cap the document: a long session would otherwise grow this window's
        # memory without bound.
        self.view.document().setMaximumBlockCount(int(hcfg.get("max_lines", 500)))
        self.view.setStyleSheet(
            "QTextEdit { background:#121417; color:#e8e8e8;"
            " font-size:14px; font-family:'Segoe UI', sans-serif; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        # The tray icon is easy to lose in the overflow area, so the controls
        # live on the window everyone actually has open.
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 6, 8, 6)
        bar.setSpacing(6)
        self._conn = QLabel()
        self._conn.setStyleSheet("QLabel { color:#c9cdd3; font-size:13px;"
                                 " font-family:'Segoe UI', sans-serif; padding-left:2px; }")
        bar.addWidget(self._conn)
        self.set_connection_look(*connection_look("starting"))
        bar.addStretch(1)
        style = ("QPushButton { background:#23272e; color:#e8e8e8; border:1px solid #353b45;"
                 " border-radius:6px; padding:4px 12px; font-size:13px; }"
                 "QPushButton:hover { background:#2d333c; }")
        clear = QPushButton("Clear")
        clear.setToolTip("Clear this window and the game overlay")
        clear.clicked.connect(self.clear)
        buttons = [clear]
        if on_settings is not None:
            settings = QPushButton("Settings")
            settings.clicked.connect(on_settings)
            buttons.append(settings)
        for b in buttons:
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(style)
            bar.addWidget(b)
        layout.addLayout(bar)
        layout.addWidget(self.view)

    def set_connection_look(self, color, text, tooltip=""):
        self._conn.setText(f"<span style='color:{color};font-size:16px'>&#9679;</span>"
                           f"&nbsp;{html_escape(text)}")
        self._conn.setToolTip(tooltip)

    def clear(self):
        self.view.clear()
        if self._on_clear:
            self._on_clear()

    def add_status(self, text):
        self.view.append(f"<i style='color:#888'>· {html_escape(text)}</i>")

    def add_caption(self, r):
        ts = r.get("ts", "")
        label = html_escape(r.get("label", "?"))
        color = r.get("color", "#ffffff")
        text = html_escape(r.get("text", ""))
        orig = (f" <span style='color:#888'>({html_escape(r['original'])})</span>"
                if r.get("original") else "")
        if r.get("kind") == "chat":
            scope = _chat_tag(r)
            tag = f" <span style='color:#888'>[{scope}]</span>" if scope else ""
            marker = "&#128172;"
        else:
            tag = (f" <span style='color:#888'>[{html_escape(r.get('lang', ''))}]</span>"
                   if r.get("translated") else "")
            marker = f"<span style='color:{color}'>&#9679;</span>"
        self.view.append(
            f"<span style='color:#666'>{ts}</span> "
            f"{marker} <b style='color:{color}'>{label}</b>{tag} {text}{orig}"
            f"{_gloss_html(r, 12, False)}"
        )


class TrayController:
    """Owns the tray icon and the actions the hotkeys also drive.

    Every method here runs on the GUI thread: hotkeys reach it through Bridge
    signals, never by calling in directly.
    """

    def __init__(self, app, overlay, history, paused_event, stop_event,
                 overlay_enabled=False, on_overlay_toggle=None, on_quit=None,
                 chat_cfg=None, on_chat_toggle=None, on_settings=None):
        self.app = app
        self.overlay = overlay
        self.history = history
        self.paused_event = paused_event
        self.stop_event = stop_event
        self.overlay_visible = overlay_enabled
        self._on_overlay_toggle = on_overlay_toggle
        self._on_quit = on_quit
        self._chat_cfg = chat_cfg if chat_cfg is not None else {}
        self._connection = ("starting", "")
        self._on_chat_toggle = on_chat_toggle

        self.tray = QSystemTrayIcon(make_icon("#7CFC7C"))
        self.tray.setToolTip("CS Voice Captions")
        self.menu = QMenu()

        self.act_pause = QAction("Pause listening", self.menu)
        self.act_pause.triggered.connect(self.toggle_pause)

        self.act_overlay = QAction("", self.menu)
        self.act_overlay.triggered.connect(self.toggle_overlay)

        self.act_lock = QAction("", self.menu)
        self.act_lock.triggered.connect(self.toggle_lock)

        self.act_hist = QAction("Show captions window", self.menu)
        self.act_hist.triggered.connect(self.show_window)

        self.act_chat = QAction("Show text chat", self.menu)
        self.act_chat.setCheckable(True)
        self.act_chat.triggered.connect(self.toggle_chat)

        act_settings = QAction("Settings…", self.menu)
        if on_settings is not None:
            act_settings.triggered.connect(on_settings)

        act_quit = QAction("Quit", self.menu)
        act_quit.triggered.connect(self.quit)

        for a in (self.act_pause, self.act_overlay, self.act_lock, self.act_hist):
            self.menu.addAction(a)
        self.menu.addSeparator()
        self.menu.addAction(self.act_chat)
        self.menu.addAction(act_settings)
        self.menu.addSeparator()
        self.menu.addAction(act_quit)
        # The settings window can change the same option, so re-read it each
        # time the menu opens rather than trusting a cached state.
        self.menu.aboutToShow.connect(self._sync_labels)

        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)
        self._sync_labels()
        self.tray.show()

    # -- actions -----------------------------------------------------------
    def toggle_pause(self):
        if self.paused_event.is_set():
            self.paused_event.clear()
        else:
            self.paused_event.set()
        self._sync_labels()

    def toggle_overlay(self):
        self.overlay_visible = not self.overlay_visible
        if self.overlay_visible:
            self.overlay.show()
        else:
            self.overlay.hide()
            # Never leave an invisible overlay holding the mouse.
            self.overlay.set_locked(True)
        self._sync_labels()
        if self._on_overlay_toggle:
            self._on_overlay_toggle(self.overlay_visible)

    def toggle_lock(self):
        """Unlock to drag the overlay into place; lock to give the mouse back
        to the game."""
        self.overlay.set_locked(not self.overlay.locked)
        if not self.overlay.locked and not self.overlay_visible:
            self.toggle_overlay()
        else:
            self._sync_labels()

    def set_connection(self, state, detail=""):
        """Slot for Bridge.connection: the capture attached, lost CS2, or
        fell back to the whole desktop."""
        self._connection = (state, detail)
        self._sync_labels()

    def toggle_chat(self):
        self._chat_cfg["show"] = not self._chat_cfg.get("show", True)
        self._sync_labels()
        if self._on_chat_toggle:
            self._on_chat_toggle(self._chat_cfg["show"])

    def show_window(self):
        self.history.show()
        self.history.raise_()
        self.history.activateWindow()

    def quit(self):
        self.stop_event.set()
        if self._on_quit:
            self._on_quit()
        self.tray.hide()
        self.app.quit()

    # -- internals ---------------------------------------------------------
    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window()

    def _sync_labels(self):
        paused = self.paused_event.is_set()
        self.act_pause.setText("Resume listening" if paused else "Pause listening")
        state, detail = self._connection
        color, text = connection_look(state, detail, paused)
        tip = ""
        if state == "desktop" and not paused:
            tip = "Not CS2-only: other programs' audio may be captioned too."
        elif state == "connected" and detail and not paused:
            tip = f"Capturing CS2's audio ({detail})."
        self.tray.setIcon(make_icon(color))
        from version import __version__
        self.tray.setToolTip(f"CS Voice Captions {__version__} — {text}")
        self.history.set_connection_look(color, text, tip)
        self.act_overlay.setText("Hide game overlay" if self.overlay_visible
                                 else "Show game overlay")
        self.act_lock.setText("Lock overlay (click-through)" if not self.overlay.locked
                              else "Unlock overlay to move it")
        self.act_lock.setEnabled(self.overlay_visible or self.overlay.locked)
        self.act_chat.setChecked(bool(self._chat_cfg.get("show", True)))

    def notify(self, title, message):
        try:
            self.tray.showMessage(title, message, make_icon(), 4000)
        except Exception:  # noqa: BLE001
            pass


def should_remind_condebug(status, chat_cfg):
    """Whether to show the -condebug reminder at startup. `status` is
    chat_log.condebug_status(): True (set), False (CS2's launch options
    found without it), None (can't tell)."""
    if status is True:
        return False
    if status is False:
        return bool(chat_cfg.get("remind_condebug", True))
    return not chat_cfg.get("condebug_tip_seen", False)   # unknown: first start only


class CondebugReminder(QDialog):
    """How to turn on text chat: CS2 only writes the log the app reads when
    it is started with -condebug, and nothing in the game says so."""

    _STYLE = """
    QDialog { background:#121417; }
    QWidget { color:#e8e8e8; font-family:'Segoe UI', sans-serif; font-size:13px; }
    QLabel#Title { font-size:16px; font-weight:600; }
    QLabel#Hint { color:#8a929c; font-size:12px; }
    QLabel#Code { background:#1b1f25; border:1px solid #353b45; border-radius:6px;
                  padding:6px 12px; font-family:Consolas, monospace; font-size:15px;
                  color:#4cd07d; }
    QPushButton { background:#23272e; border:1px solid #353b45; border-radius:6px;
                  padding:5px 14px; }
    QPushButton:hover { background:#2d333c; }
    QPushButton#Primary { background:#2f6fd6; border-color:#2f6fd6; }
    QCheckBox::indicator { width:14px; height:14px; border:1px solid #4a525e;
                           border-radius:3px; background:#1b1f25; }
    QCheckBox::indicator:checked { background:#3d8bfd; border-color:#3d8bfd; }
    """

    def __init__(self, missing=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CS Voice Captions \u2014 text chat")
        self.setStyleSheet(self._STYLE)
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)

        title = QLabel("Turn on text chat with -condebug")
        title.setObjectName("Title")
        lay.addWidget(title)
        lead = ("CS2's launch options don't include <b>-condebug</b>, so text chat "
                "won't appear." if missing else
                "Text chat needs one launch option in Steam, <b>-condebug</b>.")
        body = QLabel(
            f"{lead}<br><br>In Steam, right-click <b>Counter-Strike 2</b> \u2192 "
            "<b>Properties\u2026</b> \u2192 <b>General</b>, and add this to "
            "<b>Launch Options</b>:")
        body.setWordWrap(True)
        body.setTextFormat(Qt.RichText)
        lay.addWidget(body)

        row = QHBoxLayout()
        code = QLabel("-condebug")
        code.setObjectName("Code")
        code.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(code)
        self._copy = QPushButton("Copy")
        self._copy.clicked.connect(self._copy_option)
        row.addWidget(self._copy)
        row.addStretch(1)
        lay.addLayout(row)

        why = QLabel(
            "Then restart CS2. It makes the game write its chat to a log file, "
            "which is how the app reads it \u2014 Valve's own option, nothing "
            "touches the game. Voice captions work without it.")
        why.setObjectName("Hint")
        why.setWordWrap(True)
        lay.addWidget(why)

        self._never = None
        if missing:
            note = QLabel("Already added it? Steam saves launch options when it "
                          "closes, so this can show once more.")
            note.setObjectName("Hint")
            note.setWordWrap(True)
            lay.addWidget(note)
            self._never = QCheckBox("Don't remind me again")
            lay.addWidget(self._never)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        ok = QPushButton("OK")
        ok.setObjectName("Primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        buttons.addWidget(ok)
        lay.addLayout(buttons)

    def _copy_option(self):
        QApplication.clipboard().setText("-condebug")
        self._copy.setText("Copied")

    def dont_remind(self):
        return bool(self._never and self._never.isChecked())
