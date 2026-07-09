"""Icon resolver — load the hand-authored SVGs in ``ui/icons/`` as ``QIcon``s (PLAN.md, M10).

The SVGs are the unit of audit (readable vector source, no opaque binary, no network). We render
them with ``QSvgRenderer`` rather than relying on Qt's optional ``qsvgicon`` icon-engine plugin, so
icons are robust in the frozen build whether or not that plugin is bundled (the ``ui/icons`` folder
is added to the PyInstaller bundle via ``packaging/klarpdf.spec`` ``datas``).

**Theming:** the monochrome action glyphs are *tinted to the palette's button-text colour* at render
time, so they read as dark glyphs on a light theme and light glyphs on a dark theme (the SVGs' own
stroke colour is just a placeholder shape). The full-colour app icon is left untinted. Tinting
happens when an icon is first requested — i.e. against the theme active at window-build time; a live
OS theme switch is picked up on the next run (see ``refresh_for_theme``).

``icon(name)`` is cached, so the toolbar can ask for the same icon per window cheaply. A
``QApplication`` must exist before calling (QPixmap needs a GUI app) — true for every real and
headless-test caller.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer

# Sizes baked into each QIcon. The toolbar uses ~20px; menus ~16px; HiDPI may ask for more.
_RENDER_SIZES = (16, 20, 24, 32, 48)

# App-icon name (a filled mark) vs the line-style action icons.
APP_ICON = "klarpdf"


def icons_dir() -> Path:
    """Directory holding the SVGs, resolved for both source runs and the frozen bundle."""
    meipass = getattr(sys, "_MEIPASS", None)  # set by PyInstaller at runtime
    if meipass:
        return Path(meipass) / "ui" / "icons"
    return Path(__file__).resolve().parent / "icons"


def svg_path(name: str) -> Path:
    return icons_dir() / f"{name}.svg"


def _glyph_color() -> QColor:
    """Tint colour for the monochrome action icons: the theme's button-text colour."""
    app = QGuiApplication.instance()
    if app is None:
        return QColor("#2b2b2b")
    return app.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.ButtonText)


def _tinted(pixmap: QPixmap, color: QColor) -> QPixmap:
    """Repaint a rendered glyph in ``color``, preserving its (anti-aliased) alpha shape."""
    out = QPixmap(pixmap.size())
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.drawPixmap(0, 0, pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(out.rect(), color)
    painter.end()
    return out


def _render(name: str, tint: bool) -> QIcon:
    path = svg_path(name)
    result = QIcon()
    if not path.exists():
        return result  # caller still gets a valid (empty) QIcon — never crashes the UI
    renderer = QSvgRenderer(str(path))
    color = _glyph_color() if tint else None
    for size in _RENDER_SIZES:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        result.addPixmap(_tinted(pixmap, color) if color is not None else pixmap)
    return result


@lru_cache(maxsize=None)
def icon(name: str) -> QIcon:
    """A monochrome action icon as a multi-resolution ``QIcon``, tinted to the theme's text
    colour (empty ``QIcon`` if the SVG is missing)."""
    return _render(name, tint=True)


@lru_cache(maxsize=None)
def app_icon() -> QIcon:
    """The full-colour application/window icon (taskbar, title bar) — never tinted."""
    return _render(APP_ICON, tint=False)


def refresh_for_theme() -> None:
    """Drop cached action icons so they re-tint against the current palette on next request.

    Call after a runtime palette/theme change; callers must then re-fetch their icons (e.g.
    rebuild the toolbar). The app icon is colour-fixed and intentionally not cleared.
    """
    icon.cache_clear()
