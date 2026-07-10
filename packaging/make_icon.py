"""Render ``ui/icons/klarpdf.svg`` to ``packaging/klarpdf.ico`` (PLAN.md, M10).

The app icon is authored as readable SVG (the unit of audit); this script bakes it into the
multi-resolution ``.ico`` that PyInstaller embeds in ``klarpdf.exe`` and Inno Setup uses as the
installer/shortcut icon. Pure offline: renders with Qt (already a dependency) and writes a standard
PNG-compressed ICO container by hand — no Pillow, no network. Re-run after editing the SVG:

    py -3.12 packaging/make_icon.py

The generated ``packaging/klarpdf.ico`` is committed so the build needs no extra step.
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # headless render, no display needed

from PySide6.QtCore import QBuffer, QByteArray, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# Two icons, two jobs (ui/icons.py):
#   klarpdf.ico      the APPLICATION (exe, taskbar, Start Menu, Add/Remove Programs)
#   klarpdf-doc.ico  a `.pdf` DOCUMENT (the ProgID DefaultIcon in installer.iss)
# Until v0.10.0 the installer pointed DefaultIcon at `klarpdf.exe,0`, so every PDF wore the app's
# icon. A document is not the program that opens it.
ICON_PAIRS = (
    (ROOT / "ui" / "icons" / "klarpdf.svg", ROOT / "packaging" / "klarpdf.ico"),
    (ROOT / "ui" / "icons" / "klarpdf-doc.svg", ROOT / "packaging" / "klarpdf-doc.ico"),
)

SVG, ICO = ICON_PAIRS[0]  # back-compat for anything importing these names

# Standard Windows icon sizes. 256 is stored PNG-compressed (Vista+), the rest also as PNG —
# every modern Windows shell reads PNG-in-ICO.
SIZES = (16, 24, 32, 48, 64, 128, 256)


def _render_png(renderer: QSvgRenderer, size: int) -> bytes:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    data = QByteArray()  # must outlive the QBuffer — QBuffer does not own it
    buffer = QBuffer(data)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def _pack_ico(pngs: list[tuple[int, bytes]]) -> bytes:
    """Build an ICO container around PNG images (ICONDIR + ICONDIRENTRY[] + data)."""
    count = len(pngs)
    header = struct.pack("<HHH", 0, 1, count)  # reserved, type=icon, count
    offset = 6 + 16 * count
    entries, blobs = b"", b""
    for size, png in pngs:
        dim = 0 if size >= 256 else size  # 0 means 256 in the ICO spec
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        blobs += png
    return header + entries + blobs


def main() -> int:
    QGuiApplication.instance() or QGuiApplication([])
    for svg, ico in ICON_PAIRS:
        if not svg.exists():
            print(f"missing source SVG: {svg}", file=sys.stderr)
            return 1
        renderer = QSvgRenderer(str(svg))
        if not renderer.isValid():
            print(f"invalid SVG: {svg}", file=sys.stderr)
            return 1
        pngs = [(s, _render_png(renderer, s)) for s in SIZES]
        ico.write_bytes(_pack_ico(pngs))
        print(f"wrote {ico.name} ({ico.stat().st_size} bytes) from {svg.name}, "
              f"sizes {', '.join(map(str, SIZES))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
