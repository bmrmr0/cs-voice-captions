"""PySide6 UI: a click-through caption overlay, a history window, and a tray
menu to control it all.

The overlay is a normal top-level window painted on top of the game. It never
injects into or hooks the game, which is what keeps the whole thing anti-cheat
safe. Run CS2 in *Fullscreen Windowed* mode so the overlay is visible.
"""
import time
from html import escape as html_escape

from PySide6.QtCore import Qt, QObject, Signal, QTimer, QPoint
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QAction, QGuiApplication
from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QTextEdit, QSystemTrayIcon, QMenu,
)


def make_icon(color):
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(8, 8, 48, 48)
    p.end()
    return QIcon(pm)


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
            scope = html_escape(r.get("scope", ""))
            scope_tag = (f"<span style='color:#888;font-size:{small}px'>[{scope}]</span> "
                         if scope else "")
            return (f"&#128172; <span style='color:{color};font-weight:bold'>{label}</span> "
                    f"{scope_tag}{text}{orig}{note}")

        # voice: coloured dot + speaker name + source-language arrow
        tag = ""
        if r.get("translated"):
            tag = (f"<span style='color:#8a8a8a;font-size:{small}px'>"
                   f"[{html_escape(r.get('lang', '?'))}&#8594;]</span> ")
        dot = f"<span style='color:{color}'>&#9679;</span>"
        return (f"{dot} <span style='color:{color};font-weight:bold'>{label}</span> "
                f"{tag}{text}{orig}{note}")

    # -- public API --------------------------------------------------------
    def add_caption(self, r):
        self._add(self._format(r), self._ttl)

    def add_status(self, text):
        self._add(f"<i style='color:#aaaaaa'>{html_escape(text)}</i>", 6)

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
    def __init__(self, hcfg=None):
        super().__init__()
        hcfg = hcfg or {}
        self.setWindowTitle("CS Voice Captions")
        self.resize(580, 640)
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
        layout.addWidget(self.view)

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
            scope = html_escape(r.get("scope", ""))
            tag = f" <span style='color:#888'>[{scope}]</span>" if scope else ""
            marker = "&#128172;"
        else:
            tag = (f" <span style='color:#888'>[{html_escape(r.get('lang', ''))}]</span>"
                   if r.get("translated") else "")
            marker = f"<span style='color:{color}'>&#9679;</span>"
        self.view.append(
            f"<span style='color:#666'>{ts}</span> "
            f"{marker} <b style='color:{color}'>{label}</b>{tag} {text}{orig}"
        )


class TrayController:
    """Owns the tray icon and the actions the hotkeys also drive.

    Every method here runs on the GUI thread: hotkeys reach it through Bridge
    signals, never by calling in directly.
    """

    def __init__(self, app, overlay, history, paused_event, stop_event,
                 overlay_enabled=False, on_overlay_toggle=None, on_quit=None):
        self.app = app
        self.overlay = overlay
        self.history = history
        self.paused_event = paused_event
        self.stop_event = stop_event
        self.overlay_visible = overlay_enabled
        self._on_overlay_toggle = on_overlay_toggle
        self._on_quit = on_quit

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

        act_quit = QAction("Quit", self.menu)
        act_quit.triggered.connect(self.quit)

        for a in (self.act_pause, self.act_overlay, self.act_lock, self.act_hist):
            self.menu.addAction(a)
        self.menu.addSeparator()
        self.menu.addAction(act_quit)

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
        self.tray.setIcon(make_icon("#888888" if paused else "#7CFC7C"))
        self.tray.setToolTip("CS Voice Captions" + (" (paused)" if paused else ""))
        self.act_overlay.setText("Hide game overlay" if self.overlay_visible
                                 else "Show game overlay")
        self.act_lock.setText("Lock overlay (click-through)" if not self.overlay.locked
                              else "Unlock overlay to move it")
        self.act_lock.setEnabled(self.overlay_visible or self.overlay.locked)

    def notify(self, title, message):
        try:
            self.tray.showMessage(title, message, make_icon("#7CFC7C"), 4000)
        except Exception:  # noqa: BLE001
            pass
