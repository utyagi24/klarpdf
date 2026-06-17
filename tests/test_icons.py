"""Icon resolver + generated app ICO (PLAN.md, M10).

Headless (offscreen, set in conftest): the SVGs must render to non-empty QIcons, a missing name
must degrade to an empty icon rather than crash, and the committed ``packaging/pdfproj.ico`` must be
a well-formed multi-resolution PNG-in-ICO container.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from ui import icons

# Every icon the toolbar/menus reference (main_window._build_actions) + the app icon.
ACTION_ICONS = [
    "open", "save", "undo", "redo", "cut", "copy", "paste", "delete",
    "insert", "find", "zoom-in", "zoom-out", "fit-width", "fit-page",
    "rotate-left", "rotate-right", "sidebar",
]
ICO_PATH = Path(__file__).resolve().parents[1] / "packaging" / "pdfproj.ico"


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def test_every_referenced_icon_has_an_svg():
    missing = [n for n in ACTION_ICONS + [icons.APP_ICON] if not icons.svg_path(n).exists()]
    assert not missing, f"missing SVG assets: {missing}"


@pytest.mark.parametrize("name", ACTION_ICONS)
def test_action_icon_renders_non_empty(qapp, name):
    icon = icons.icon(name)
    assert not icon.isNull()
    assert not icon.pixmap(QSize(20, 20)).isNull()


def test_app_icon_renders(qapp):
    assert not icons.app_icon().isNull()


def test_missing_icon_is_empty_not_crash(qapp):
    assert icons.icon("no-such-icon-xyz").isNull()  # graceful: empty QIcon, no exception


def test_icon_is_cached(qapp):
    assert icons.icon("undo") is icons.icon("undo")  # lru_cache: one QIcon per name


def _opaque_luminance(icon, size=48):
    """Mean luminance of an icon's near-opaque pixels (its glyph strokes)."""
    image = icon.pixmap(QSize(size, size)).toImage()
    lums = []
    for y in range(image.height()):
        for x in range(image.width()):
            c = image.pixelColor(x, y)
            if c.alpha() > 200:
                lums.append((c.red() + c.green() + c.blue()) // 3)
    return lums


def test_action_icons_tint_to_theme_text_colour(qapp):
    """Regression: dark-theme glyphs must follow the (light) text colour, not stay near-black.

    Reported bug — hard-coded dark strokes were nearly invisible on a dark toolbar.
    """
    original = qapp.palette()
    try:
        dark = QPalette(original)
        dark.setColor(QPalette.ColorRole.ButtonText, QColor("white"))
        qapp.setPalette(dark)
        icons.refresh_for_theme()
        lums = _opaque_luminance(icons.icon("undo"))
        assert lums, "icon had no opaque pixels"
        assert min(lums) > 180  # tinted toward white (was ~43 for #2b2b2b)
    finally:
        qapp.setPalette(original)
        icons.refresh_for_theme()


def test_app_icon_keeps_its_colours(qapp):
    """The full-colour app mark must NOT be tinted to a flat silhouette."""
    image = icons.app_icon().pixmap(QSize(64, 64)).toImage()
    colours = {
        (c.red(), c.green(), c.blue())
        for y in range(image.height())
        for x in range(image.width())
        if (c := image.pixelColor(x, y)).alpha() > 200
    }
    assert len(colours) > 3  # multiple distinct colours survive (red band, white page, …)


def test_app_ico_is_valid_multi_resolution():
    raw = ICO_PATH.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", raw[:6])
    assert (reserved, kind) == (0, 1)  # reserved=0, type=1 (icon)
    assert count >= 5  # several sizes baked in
    sizes = []
    png_magic = bytes([0x89, 0x50, 0x4E, 0x47])
    for i in range(count):
        w, h, _c, _r, _planes, _bpp, nbytes, offset = struct.unpack(
            "<BBBBHHII", raw[6 + 16 * i: 22 + 16 * i]
        )
        sizes.append(w or 256)  # 0 encodes 256 in the ICO spec
        assert raw[offset:offset + 4] == png_magic  # PNG-compressed entry
        assert nbytes > 0
    assert 256 in sizes and 16 in sizes
