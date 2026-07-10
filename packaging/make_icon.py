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
SVG = ROOT / "ui" / "icons" / "klarpdf.svg"
SVG_SMALL = ROOT / "ui" / "icons" / "klarpdf-small.svg"
ICO = ROOT / "packaging" / "klarpdf.ico"

# Standard Windows icon sizes. 256 is stored PNG-compressed (Vista+), the rest also as PNG —
# every modern Windows shell reads PNG-in-ICO.
SIZES = (16, 24, 32, 48, 64, 128, 256)

# At or below this size, render the simplified small master (ui/icons.py uses the same threshold).
# Explorer's small/medium views and the taskbar pull the 16/24/32 entries; the knot and the back
# leaves are sub-pixel there and only muddy the silhouette.
SMALL_MASTER_MAX = 32


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
    if not SVG.exists():
        print(f"missing source SVG: {SVG}", file=sys.stderr)
        return 1
    QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(str(SVG))
    if not renderer.isValid():
        print(f"invalid SVG: {SVG}", file=sys.stderr)
        return 1

    small = None
    if SVG_SMALL.exists():
        small = QSvgRenderer(str(SVG_SMALL))
        if not small.isValid():
            print(f"invalid SVG: {SVG_SMALL}", file=sys.stderr)
            return 1
    else:
        # Not fatal: the icon just loses crispness at 16-32px. Say so rather than fail the build.
        print(f"note: {SVG_SMALL.name} absent — small sizes use the detailed mark", file=sys.stderr)

    pngs = [(s, _render_png(small if (small and s <= SMALL_MASTER_MAX) else renderer, s)) for s in SIZES]
    ICO.write_bytes(_pack_ico(pngs))
    detail = f"{'/'.join(str(s) for s in SIZES if s <= SMALL_MASTER_MAX)} from {SVG_SMALL.name}" if small else "single master"
    print(f"wrote {ICO} ({ICO.stat().st_size} bytes, sizes {', '.join(map(str, SIZES))}; {detail})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
