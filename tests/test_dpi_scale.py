"""M88 — what "100%" means, and rendering at the screen's real resolution.

Owner-reported: *"why does the document appear smaller in our app compared to Edge and Brave at the
same zoom percentage?"* Because ``actual_size`` meant "1 PDF point per pixel", a point is 1/72" and
the display is 96 logical DPI, so a 612 pt Letter page drew 612 px = **6.375 inches**: we showed
75% of physical size and called it 100%. Browsers and Acrobat define 100% as true physical size, so
Edge's 100% was our 133%.

Investigating it surfaced a second, worse defect: ``devicePixelRatio`` was handled **nowhere**. The
owner's machine pairs a 1.75x laptop panel with a 1.0x external Dell, both at 96 logical DPI; we
rendered at logical resolution and handed Qt a pixmap with no ratio set, so on the laptop every page
was upscaled 1.75x and the text was **blurry — on the higher-resolution screen of the two**.

The fix is one idea: **three scales where there was one**.

* ``zoom`` — what the reader asks for and the % indicator shows. 1.0 == physical size.
* ``scale`` == ``zoom x logicalDpi/72`` — scene units per PDF point. All *geometry* uses this.
* ``device_scale`` == ``scale x devicePixelRatio`` — only the rasteriser and the cache key use it.

The DPR tests fake the ratio rather than requiring a HiDPI screen: the offscreen platform these run
on is always 1.0, and the property under test ("geometry is independent of DPR, resolution is not")
is exactly the one a fake can carry honestly. The parts a fake cannot certify — that the panel
really does look sharp, and that dragging between screens re-renders live — are hands-on Windows
checks, recorded in PROGRESS.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from app import PdfApp
from store.settings import Settings
from viewer.pdf_view import PdfView
from model.virtual_document import VirtualDocument

LETTER_W, LETTER_H = 612.0, 792.0   # US Letter in PDF points = 8.5" x 11"


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def settings(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "view_state.json")
    return qapp.settings


@pytest.fixture
def letter(tmp_path) -> str:
    path = str(tmp_path / "letter.pdf")
    doc = fitz.open()
    for i in range(4):
        page = doc.new_page(width=LETTER_W, height=LETTER_H)
        page.insert_text((72, 72), f"page {i}", fontsize=12)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def win(qapp, settings, letter):
    w = qapp.open_document(letter)
    w.resize(1000, 800)
    qapp.processEvents()
    yield w
    w.close()


def _fake_dpr(view, dpr):
    """Pin this view's devicePixelRatio and re-read the metrics, as a screen change would."""
    view.devicePixelRatioF = lambda: dpr     # instance attr shadows the QWidget method
    view._on_screen_changed()


# ---- M88.1 100% means physical size -------------------------------------------


def test_one_hundred_percent_is_physical_size(win):
    """The reported bug, as a measurement: at 100% a Letter page is 8.5 real inches wide.

    Expressed in inches rather than pixels so it states the *claim* — a ruler held to the screen
    reads 8.5" — instead of restating the arithmetic that produces it.
    """
    view = win.view
    view.set_zoom(1.0)
    dpi = view._logical_dpi
    page_w = view._pages[0]["w"]
    assert page_w / dpi == pytest.approx(8.5, abs=0.01)
    assert view._pages[0]["h"] / dpi == pytest.approx(11.0, abs=0.01)


def test_the_old_behaviour_is_what_changed_not_the_zoom_number(win):
    """Before M88.1 this page was 612 px = 6.375" and still called 100%. The *number* the reader
    sees is untouched — only what it draws — which is why the % indicator needed no migration."""
    view = win.view
    view.set_zoom(1.0)
    assert view.zoom == 1.0                                  # still "100%" to the reader
    assert view._pages[0]["w"] == pytest.approx(LETTER_W * 4 / 3, abs=0.5)   # 816, not 612
    assert view._pages[0]["w"] / LETTER_W == pytest.approx(4 / 3, abs=0.01)  # Edge's 100% = our 133%


def test_scale_is_zoom_times_the_dpi_factor(win):
    """The definition, at several magnifications — the one place the DPI correction lives."""
    view = win.view
    factor = view._logical_dpi / 72.0
    for zoom in (0.5, 1.0, 2.0, 4.0):
        view.set_zoom(zoom)
        assert view.scale == pytest.approx(zoom * factor)
        assert view._pages[0]["w"] == pytest.approx(LETTER_W * zoom * factor, abs=0.5)


def test_fit_page_still_fits(win, qapp):
    """The correction multiplies every layout by 1.333, so a Fit that forgot to divide it back out
    would overshoot the viewport by exactly that much. Guards `_fit_zoom`'s conversion."""
    view = win.view
    view.fit_page()
    qapp.processEvents()
    page = view._pages[view.current_page]
    assert page["h"] <= view.viewport().height()
    assert page["w"] <= view.viewport().width()
    # ...and actually fills it, so "fits" cannot be satisfied by being trivially tiny.
    assert page["h"] > view.viewport().height() * 0.7


def test_fit_width_still_fits(win, qapp):
    view = win.view
    view.fit_width()
    qapp.processEvents()
    page = view._pages[view.current_page]
    assert page["w"] <= view.viewport().width()
    assert page["w"] > view.viewport().width() * 0.7


# ---- M88.4 Ctrl+0 is true physical size ----------------------------------------


def test_actual_size_is_physically_correct(win):
    """The menu item's name stops being a lie: Ctrl+0 gives a page you can measure with a ruler."""
    view = win.view
    view.set_zoom(3.7)
    view.actual_size()
    assert view.zoom == 1.0
    assert view._pages[0]["w"] / view._logical_dpi == pytest.approx(8.5, abs=0.01)


# ---- M88.2 honour devicePixelRatio ----------------------------------------------


def test_device_scale_multiplies_in_the_ratio(win):
    view = win.view
    view.set_zoom(1.0)
    _fake_dpr(view, 2.0)
    assert view.device_scale == pytest.approx(view.scale * 2.0)


def test_the_pixmap_is_rendered_at_device_resolution_and_carries_the_ratio(win):
    """Both halves matter. Rendering more pixels without telling Qt the ratio would lay the page out
    1.75x too large; setting the ratio without rendering more pixels would just blur differently."""
    view = win.view
    view.set_zoom(1.0)
    _fake_dpr(view, 2.0)
    pixmap = view._render_pixmap(0)
    assert pixmap.devicePixelRatio() == pytest.approx(2.0)
    assert pixmap.width() == pytest.approx(LETTER_W * view.scale * 2.0, abs=2)
    # The layout size Qt derives from it is back at `scale` — which is what leaves geometry alone.
    assert pixmap.deviceIndependentSize().width() == pytest.approx(LETTER_W * view.scale, abs=2)


def test_geometry_is_unchanged_by_dpr_but_resolution_is_not(win):
    """The contract of M88.2 in one test: a higher-DPR screen changes *pixels*, never *layout*."""
    view = win.view
    view.set_zoom(1.0)

    _fake_dpr(view, 1.0)
    layout_1x = [(p["x"], p["y"], p["w"], p["h"]) for p in view._pages]
    px_1x = view._render_pixmap(0).width()
    scale_1x = view.scale

    _fake_dpr(view, 1.75)
    layout_175x = [(p["x"], p["y"], p["w"], p["h"]) for p in view._pages]
    px_175x = view._render_pixmap(0).width()

    assert layout_175x == layout_1x           # identical geometry...
    assert view.scale == scale_1x
    assert px_175x == pytest.approx(px_1x * 1.75, abs=2)   # ...at 1.75x the pixels


def test_a_pixmap_is_never_shared_between_two_ratios(win):
    """The cache is process-global (M87.2), so keying on zoom would hand the 1.0x Dell's pixmap to
    the 1.75x laptop — the blur this milestone fixes, cached and durable."""
    view = win.view
    view.set_zoom(1.0)
    _fake_dpr(view, 1.0)
    key_1x = view._pixmap_key(0)
    _fake_dpr(view, 1.75)
    assert view._pixmap_key(0) != key_1x


def test_page_bytes_accounts_for_the_ratio(win):
    """`_page_bytes` sizes M87.1's prefetch allowance off the *layout*, which is in logical units.
    Without the dpr**2 term it under-counts by 3.06x on the owner's panel — on exactly the machine
    that can least afford the mistake."""
    view = win.view
    view.set_zoom(1.0)
    _fake_dpr(view, 1.0)
    at_1x = view._page_bytes(0)
    _fake_dpr(view, 2.0)
    assert view._page_bytes(0) == pytest.approx(at_1x * 4, rel=0.01)


# ---- M88.3 the ratio changes at runtime ------------------------------------------


def test_a_screen_change_re_renders_at_the_new_ratio(win, qapp):
    """Dragging the window between the 1.75x panel and the 1.0x Dell changes DPR live. A value
    sampled once at construction is correct only on the screen the window opened on."""
    view = win.view
    view.set_zoom(1.0)
    _fake_dpr(view, 1.0)
    qapp.processEvents()
    before = view._pages[0]["pix"].pixmap().width()

    _fake_dpr(view, 2.0)
    qapp.processEvents()
    after = view._pages[0]["pix"].pixmap().width()
    assert after == pytest.approx(before * 2, abs=2), "the page did not re-render for the new screen"


def test_an_unchanged_screen_does_not_rebuild(win):
    """`screenChanged` also fires for moves between two identical screens; rebuilding the scene and
    re-rasterising the band for a no-op would make a window drag stutter."""
    view = win.view
    view.set_zoom(1.0)
    assert view._refresh_display() is False   # nothing changed since construction


def test_a_screen_with_no_metrics_falls_back_rather_than_collapsing(win):
    """A screen reporting 0 DPI would make `scale` zero and lay every page out at nothing. The
    fallback keeps the viewer usable on a platform that cannot answer."""
    view = win.view
    view.devicePixelRatioF = lambda: 1.0
    view.screen = lambda: None            # no screen to ask
    from PySide6.QtGui import QGuiApplication
    if QGuiApplication.primaryScreen() is None:   # pragma: no cover - offscreen always has one
        pytest.skip("platform reports no primary screen")
    view._refresh_display()
    assert view.scale > 0
    assert view._logical_dpi > 0


# ---- the mapping stays invertible at the new scale --------------------------------


def test_scene_and_page_coordinates_still_round_trip(win):
    """Every hit-test in the app goes through this pair. They divide by `scale`, so a site left on
    `zoom` would be wrong by 1.333 — a third of an inch of drift on every click."""
    view = win.view
    view.set_zoom(1.0)
    box = (100.0, 150.0, 260.0, 190.0)
    back = view.local_box_from_scene_rect(0, view.scene_rect_for_box(0, box))
    assert all(a == pytest.approx(b, abs=0.01) for a, b in zip(box, back))


def test_a_scene_point_maps_to_the_page_point_under_it(win):
    """`page_and_local_at` is what turns a click into a page coordinate."""
    view = win.view
    view.set_zoom(1.0)
    page = view._pages[0]
    # 200 points in from the page's top-left corner, converted through the layout scale.
    scene_pt = view.mapToScene(0, 0)
    scene_pt.setX(page["x"] + 200.0 * view.scale)
    scene_pt.setY(page["y"] + 200.0 * view.scale)
    index, local = view.page_and_local_at(scene_pt)
    assert index == 0
    assert local.x() == pytest.approx(200.0, abs=0.01)
    assert local.y() == pytest.approx(200.0, abs=0.01)


def test_the_view_survives_construction_before_it_has_a_screen(qapp, letter):
    """A `PdfView` is built before it is shown, so `_refresh_display` runs against whatever
    `screen()` returns then. It must produce a usable scale either way."""
    view = PdfView(VirtualDocument.from_path(letter))
    assert view.scale > 0
    assert view.device_scale > 0
    assert view._pages[0]["w"] == pytest.approx(LETTER_W * view.scale, abs=0.5)
