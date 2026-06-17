"""Icon resolver — load the hand-authored SVGs in ``ui/icons/`` as ``QIcon``s (PLAN.md, M10).

The SVGs are the unit of audit (readable vector source, no opaque binary, no network). We render
them with ``QSvgRenderer`` rather than relying on Qt's optional ``qsvgicon`` icon-engine plugin, so
icons are robust in the frozen build whether or not that plugin is bundled (the ``ui/icons`` folder
is added to the PyInstaller bundle via ``packaging/pdfproj.spec`` ``datas``).

``icon(name)`` is cached, so the toolbar can ask for the same icon per window cheaply. A
``QApplication`` must exist before calling (QPixmap needs a GUI app) — true for every real and
headless-test caller.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# Sizes baked into each QIcon. The toolbar uses ~20px; menus ~16px; HiDPI may ask for more.
_RENDER_SIZES = (16, 20, 24, 32, 48)

# App-icon name (a filled mark) vs the line-style action icons.
APP_ICON = "pdfproj"


def icons_dir() -> Path:
    """Directory holding the SVGs, resolved for both source runs and the frozen bundle."""
    meipass = getattr(sys, "_MEIPASS", None)  # set by PyInstaller at runtime
    if meipass:
        return Path(meipass) / "ui" / "icons"
    return Path(__file__).resolve().parent / "icons"


def svg_path(name: str) -> Path:
    return icons_dir() / f"{name}.svg"


@lru_cache(maxsize=None)
def icon(name: str) -> QIcon:
    """Return the named icon as a multi-resolution ``QIcon`` (empty if the SVG is missing)."""
    path = svg_path(name)
    result = QIcon()
    if not path.exists():
        return result  # caller still gets a valid (empty) QIcon — never crashes the UI
    renderer = QSvgRenderer(str(path))
    for size in _RENDER_SIZES:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        result.addPixmap(pixmap)
    return result


def app_icon() -> QIcon:
    """The application/window icon (taskbar, title bar)."""
    return icon(APP_ICON)
