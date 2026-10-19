"""Write assets/icon.ico (and icon.png) from app_icon.py's drawing.

    .venv\Scripts\python tools\make_icon.py

build.py runs this itself, so the exe's icon can never drift from the one
the app draws at runtime. The .ico holds a PNG per size, which every
supported Windows version reads.
"""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

import app_icon  # noqa: E402


def _png(image):
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    image.save(buf, "PNG")
    return bytes(data)


def write_ico(path, sizes=(16, 20, 24, 32, 40, 48, 64, 128, 256)):
    pngs = [(s, _png(app_icon.render(s))) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = len(header) + 16 * len(pngs)
    entries, blobs = b"", b""
    for s, data in pngs:
        dim = 0 if s >= 256 else s            # 0 means 256 in an ICO entry
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    with open(path, "wb") as f:
        f.write(header + entries + blobs)


def main():
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)  # noqa: F841
    out = os.path.join(ROOT, "assets")
    os.makedirs(out, exist_ok=True)
    write_ico(os.path.join(out, "icon.ico"))
    app_icon.render(256).save(os.path.join(out, "icon.png"))
    print(f"[icon] wrote {os.path.join('assets', 'icon.ico')} and icon.png")


if __name__ == "__main__":
    main()
