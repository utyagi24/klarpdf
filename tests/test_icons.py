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
    "stamp", "signature", "watermark",   # M62 — the Stamp ▾ split-button
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


# --- three marks, three jobs (v0.10.1) ----------------------------------------------------------
#
# v0.10.0 shipped one drawing for everything: the portrait fanned-sheets mark. Windows gives an icon
# a square canvas (24x24 for the taskbar at 100% scaling), and that mark spanned only 59% of it,
# against 82-100% for every other app on a typical machine. It read as "tiny". Worse, the installer
# pointed the `.pdf` ProgID DefaultIcon at the app's own icon, so every PDF on disk wore it.
#
#   klarpdf.svg       tile      -> the OS icon. Must SPAN the square canvas.
#   klarpdf-doc.svg   document  -> what Explorer shows for a `.pdf`. Portrait, by design.
#   klarpdf-mark.svg  mark      -> free-standing, in-app only (About dialog).

DOC_ICO_PATH = Path(__file__).resolve().parents[1] / "packaging" / "klarpdf-doc.ico"


def _bbox_span(name, n=24):
    """Fraction of an n x n canvas covered by the artwork's bounding box."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(str(icons.svg_path(name)))
    assert renderer.isValid(), name
    image = QImage(n, n, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, n, n))
    painter.end()
    pts = [(x, y) for y in range(n) for x in range(n) if image.pixelColor(x, y).alpha() > 8]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    w, h = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
    return (w * h) / (n * n), (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


@pytest.mark.parametrize("name", [icons.APP_ICON, icons.BRAND_MARK, icons.DOC_ICON])
def test_the_three_marks_exist_and_are_qtsvg_safe(qapp, name):
    from PySide6.QtSvg import QSvgRenderer

    path = icons.svg_path(name)
    assert path.is_file(), f"{name}.svg missing"
    assert QSvgRenderer(str(path)).isValid(), f"{name}.svg does not parse"
    text = path.read_text(encoding="utf-8")
    for banned in ("<text", "<filter", "<mask", "<style", "<use"):
        assert banned not in text, f"{name}: {banned} is not QtSvg-safe (BRAND.md)"


def test_app_icon_spans_the_square_canvas(qapp):
    """The whole point of the tile: the OS icon must not be a small glyph in a big box.

    v0.10.0 spanned 59% at 24px. Peers: Notepad 91%, VS Code 100%, Word 82%, cmd 84%.
    """
    for n in (16, 24, 32, 48):
        span, cx, cy = _bbox_span(icons.APP_ICON, n)
        assert span >= 0.90, f"app icon spans only {span:.0%} of the {n}px canvas"
        assert abs(cx - (n - 1) / 2) <= 1.0 and abs(cy - (n - 1) / 2) <= 1.0, "not centred"


def test_document_icon_is_portrait_not_a_tile(qapp):
    """A `.pdf` should look like a page, not like the application. It must NOT span the canvas."""
    span, _, _ = _bbox_span(icons.DOC_ICON, 32)
    assert 0.55 <= span <= 0.85, f"doc icon spans {span:.0%}; expected a portrait page"
    app_span, _, _ = _bbox_span(icons.APP_ICON, 32)
    assert app_span > span, "the document icon should not fill more of the canvas than the app tile"


def test_app_icon_and_document_icon_are_different_artwork(qapp):
    a = icons.svg_path(icons.APP_ICON).read_text(encoding="utf-8")
    d = icons.svg_path(icons.DOC_ICON).read_text(encoding="utf-8")
    assert a != d, "the .pdf icon must not be the app icon"


def test_about_dialog_uses_the_free_standing_mark_not_the_tile():
    """A dialog supplies its own background; a container would look boxed-in there."""
    import ui.about as about

    src = Path(about.__file__).read_text(encoding="utf-8")
    assert "icons.brand_mark()" in src
    assert "icons.app_icon()" not in src


def test_installer_points_the_pdf_association_at_the_document_icon():
    r"""Regression: v0.10.0's DefaultIcon was `{app}\klarpdf.exe,0` — the app icon, on every PDF."""
    iss = (Path(__file__).resolve().parents[1] / "packaging" / "installer.iss").read_text(encoding="utf-8")
    line = next(ln for ln in iss.splitlines() if "DefaultIcon" in ln and ln.startswith("Root:"))
    assert "MyAppDocIco" in line, f"DefaultIcon does not use the document icon: {line}"
    assert "MyAppExe" not in line, f"DefaultIcon still points at the exe: {line}"


def test_both_icos_are_built_and_distinct():
    for path in (ICO_PATH, DOC_ICO_PATH):
        raw = path.read_bytes()
        reserved, kind, count = struct.unpack("<HHH", raw[:6])
        assert (reserved, kind) == (0, 1) and count >= 5, f"{path.name} is not a multi-size ICO"
    assert ICO_PATH.read_bytes() != DOC_ICO_PATH.read_bytes(), "app and document .ico are identical"
