"""Icon resolver + generated app ICO (PLAN.md, M10).

Headless (offscreen, set in conftest): the SVGs must render to non-empty QIcons, a missing name
must degrade to an empty icon rather than crash, and the committed ``packaging/klarpdf.ico`` must be
a well-formed multi-resolution PNG-in-ICO container.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from ui import icons

# Every icon the toolbar/menus reference (main_window._build_actions) + the app icon.
ACTION_ICONS = [
    "open", "save", "undo", "redo", "cut", "copy", "paste", "delete",
    "insert", "find", "zoom-in", "zoom-out", "fit-width", "fit-page",
    "rotate-left", "rotate-right", "sidebar", "select", "grab", "highlight", "textbox",
]
ICO_PATH = Path(__file__).resolve().parents[1] / "packaging" / "klarpdf.ico"


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


# --- app-icon legibility at small sizes (v0.10.0 follow-up) --------------------------------------
#
# The mark shipped in v0.10.0 filled only 53% of the icon canvas and sat off-centre, so the taskbar
# and Explorer entries were ~9x13px of glyph inside a 16px box, and the knot rendered sub-pixel.
# These pin the two fixes: the canvas is filled and centred, and sizes <= 32px come from a
# simplified master where the knot actually survives the downsample.

SMALL_SVG = Path(__file__).resolve().parents[1] / "ui" / "icons" / "klarpdf-small.svg"


def _ink_box(renderer, n):
    """Bounding box of non-transparent pixels when rendered into an n x n square."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    image = QImage(n, n, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, n, n))
    painter.end()
    pts = [(x, y) for y in range(n) for x in range(n) if image.pixelColor(x, y).alpha() > 8]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), max(xs), min(ys), max(ys)


def test_small_master_exists_and_is_qtsvg_safe(qapp):
    from PySide6.QtSvg import QSvgRenderer

    assert SMALL_SVG.is_file(), "the <=32px app-icon master is missing"
    assert QSvgRenderer(str(SMALL_SVG)).isValid()
    text = SMALL_SVG.read_text(encoding="utf-8")
    for banned in ("<text", "<filter", "<mask", "<style", "<use"):
        assert banned not in text, f"{banned} is not QtSvg-safe (BRAND.md)"


@pytest.mark.parametrize("svg", ["klarpdf", "klarpdf-small"])
def test_app_mark_fills_and_centres_its_canvas(qapp, svg):
    """Regression: the v0.10.0 mark filled 53% of the width and sat off-centre."""
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(str(icons.svg_path(svg)))
    n = 256
    x0, x1, y0, y1 = _ink_box(renderer, n)
    height_fill = (y1 - y0 + 1) / n
    assert height_fill >= 0.88, f"{svg}: mark fills only {height_fill:.0%} of the canvas height"
    # Centred to within a pixel — the old mark was 21px left and 27px low on a 256 canvas.
    assert abs((x0 + x1) / 2 - (n - 1) / 2) <= 1.5, f"{svg}: not horizontally centred"
    assert abs((y0 + y1) / 2 - (n - 1) / 2) <= 1.5, f"{svg}: not vertically centred"


def test_knot_survives_at_16px_only_in_the_small_master(qapp):
    """The point of the small master: at 16px the detailed knot is a smudge, the small one is solid.

    Rendered with and without the knot; we compare how much ink it actually contributes.
    """
    import re

    from PySide6.QtCore import QByteArray, QRectF
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    def max_delta(path, n=16):
        text = path.read_text(encoding="utf-8")
        without = re.sub(r'\s*<rect x="1[12]\d"[^>]*rotate\(45 128 167\)[^>]*></rect>', "", text)
        assert without != text, f"knot rect not found in {path.name}"

        def draw(src):
            r = QSvgRenderer(QByteArray(src.encode()))
            img = QImage(n, n, QImage.Format.Format_ARGB32)
            img.fill(Qt.GlobalColor.transparent)
            p = QPainter(img)
            r.render(p, QRectF(0, 0, n, n))
            p.end()
            return img

        a, b = draw(text), draw(without)
        return max(
            max(abs(a.pixelColor(x, y).red() - b.pixelColor(x, y).red()),
                abs(a.pixelColor(x, y).green() - b.pixelColor(x, y).green()),
                abs(a.pixelColor(x, y).blue() - b.pixelColor(x, y).blue()))
            for y in range(n) for x in range(n)
        )

    detailed = max_delta(icons.svg_path("klarpdf"))
    small = max_delta(SMALL_SVG)
    assert small > 150, f"knot barely renders at 16px in the small master (delta {small})"
    assert small > detailed * 2, (
        f"small master's knot ({small}) should be far more legible than the detailed one ({detailed})"
    )


def test_ico_small_entries_come_from_the_small_master():
    """16/24/32 in the .ico must match the small master, not the detailed one."""
    import io
    import struct as _struct

    from PySide6.QtCore import QByteArray, QRectF
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    QGuiApplication.instance() or QGuiApplication([])
    raw = ICO_PATH.read_bytes()
    _, _, count = _struct.unpack("<HHH", raw[:6])
    entries = {}
    for i in range(count):
        w, _h, _c, _r, _p, _b, nbytes, offset = _struct.unpack("<BBBBHHII", raw[6 + 16 * i: 22 + 16 * i])
        entries[w or 256] = raw[offset:offset + nbytes]

    def render(path, n):
        r = QSvgRenderer(str(path))
        img = QImage(n, n, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        r.render(p, QRectF(0, 0, n, n))
        p.end()
        return img

    for n in (16, 24, 32):
        baked = QImage()
        baked.loadFromData(entries[n], "PNG")
        baked = baked.convertToFormat(QImage.Format.Format_ARGB32)
        small = render(SMALL_SVG, n)
        detailed = render(icons.svg_path("klarpdf"), n)

        def diff(a, b):
            return sum(a.pixelColor(x, y) != b.pixelColor(x, y) for y in range(n) for x in range(n))

        assert diff(baked, small) < diff(baked, detailed), (
            f"the {n}px .ico entry looks like the detailed mark — re-run packaging/make_icon.py"
        )
