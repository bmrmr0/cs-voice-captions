"""The app's icon, drawn in code.

A dark rounded tile holding a green speech bubble with two caption lines:
voice chat, turned into text. Drawn rather than loaded from an image so every
size is rendered sharp instead of scaled down, and so the same drawing serves
the exe (tools/make_icon.py writes it to assets/icon.ico at build time), the
windows, and the tray -- where a small dot in the corner shows whether CS2 is
being heard. Deliberately nothing from the game's own artwork.
"""
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (QColor, QIcon, QImage, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap)

TILE_TOP = "#2b3342"
TILE_BOTTOM = "#14181f"
BUBBLE = "#4cd07d"
INK = "#12161c"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _draw(p, s):
    """Paint the icon into an s x s square."""
    p.setRenderHint(QPainter.Antialiasing, True)

    # Tile. Small sizes get a little less margin so the bubble stays legible.
    m = s * (0.02 if s <= 24 else 0.04)
    tile = QRectF(m, m, s - 2 * m, s - 2 * m)
    grad = QLinearGradient(tile.topLeft(), tile.bottomLeft())
    grad.setColorAt(0, QColor(TILE_TOP))
    grad.setColorAt(1, QColor(TILE_BOTTOM))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(tile, s * 0.22, s * 0.22)
    if s >= 32:
        p.setPen(QPen(QColor(255, 255, 255, 28), max(1.0, s / 128)))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(tile.adjusted(0.5, 0.5, -0.5, -0.5), s * 0.22, s * 0.22)

    # Speech bubble with a tail at the lower left. At tray sizes it takes
    # more of the tile, or the caption lines inside blur into the bubble.
    small = s <= 24
    if small:
        bx, by, bw, bh = s * 0.10, s * 0.15, s * 0.80, s * 0.56
    else:
        bx, by, bw, bh = s * 0.16, s * 0.20, s * 0.68, s * 0.48
    r = s * (0.16 if small else 0.13)
    bubble = QPainterPath()
    bubble.addRoundedRect(QRectF(bx, by, bw, bh), r, r)
    tail = QPainterPath()
    tail.moveTo(QPointF(bx + bw * 0.20, by + bh - 1))
    tail.lineTo(QPointF(bx + bw * 0.12, by + bh + s * (0.12 if small else 0.15)))
    tail.lineTo(QPointF(bx + bw * 0.44, by + bh - 1))
    tail.closeSubpath()
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(BUBBLE))
    p.drawPath(bubble.united(tail))

    # Two caption lines: one full, one shorter. Snapped to whole pixels at
    # small sizes so they stay crisp instead of anti-aliasing to grey.
    lh = 2.0 if s <= 20 else max(2.0, s * 0.075)
    lx = bx + bw * 0.17
    p.setBrush(QColor(INK))
    for frac, ly in ((0.66, by + bh * 0.32), (0.42, by + bh * 0.62)):
        top = ly - lh / 2
        if small:
            top, lx = round(top), round(lx)
        p.drawRoundedRect(QRectF(lx, top, round(bw * frac) if small else bw * frac, lh),
                          lh / 2, lh / 2)


def pixmap(size, badge=None):
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    _draw(p, size)
    if badge:
        # Status dot in the lower right, ringed in the tile colour so it
        # separates from the bubble at tray sizes.
        d = size * 0.42
        rect = QRectF(size - d - size * 0.01, size - d - size * 0.01, d, d)
        p.setPen(QPen(QColor(TILE_BOTTOM), max(1.5, size * 0.07)))
        p.setBrush(QColor(badge))
        p.drawEllipse(rect)
    p.end()
    return QPixmap.fromImage(img)


def icon(badge=None):
    """A QIcon with a sharp pixmap at every common size."""
    ic = QIcon()
    for s in SIZES:
        ic.addPixmap(pixmap(s, badge))
    return ic


def render(size):
    """A plain QImage, for writing the .ico at build time."""
    return pixmap(size).toImage()
