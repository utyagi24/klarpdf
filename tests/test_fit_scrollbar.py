"""A fit never chases its own scrollbar (PLAN.md §M160, #420). Offscreen GUI.

A fit sized the page from the viewport as it stood. The viewport's width depends on whether a
scrollbar is showing, and whether one shows depends on the page. On a one-page document, in a
window close to the page's own shape, the two chased each other: each fit showed or hid a bar, each
bar resized the viewport and fitted again, and the loop doubled every 250 ms until the window hung.
It was reported as a hang on toggling the sidebar, which produced such a window on a laptop. There
were two ways in, one per fit:

* **Fit Width**: a page too tall without the vertical bar and short enough with it.
* **Fit Page**: rounding left the fitted page 5e-13 px wider than the space it was fitted to. Qt
  counts any excess as overflow, so a page that fitted exactly brought both bars at once.

The fit now sizes the page from the viewport without its scrollbars, so a scrollbar coming or
going cannot change the zoom.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from app import PdfApp
from klarpdf.model.virtual_document import VirtualDocument
from viewer.pdf_view import PdfView

#: Events processed after each resize. A view caught in the loop changes its zoom on nearly every
#: pass (measured: 13 changes in 13 passes), and a settled one changes it once at most.
_PASSES = 12


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


def _letter_pdf(path, pages: int) -> str:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=612, height=792)   # Letter, as reported; new_page's default is A4
    doc.save(path)
    doc.close()
    return str(path)


@pytest.fixture
def one_page(tmp_path) -> str:
    return _letter_pdf(tmp_path / "one.pdf", 1)


def _open(qapp, path, width, height) -> PdfView:
    view = PdfView(VirtualDocument.from_path(path))
    view.resize(width, height)
    view.show()
    qapp.processEvents()
    view.open_at({})
    qapp.processEvents()
    return view


def _resize(qapp, view, width, height) -> int:
    """Resize ``view`` and let the event loop run. Returns how many times the zoom changed."""
    changes: list[float] = []
    view.zoomChanged.connect(changes.append)
    try:
        view.resize(width, height)
        for _ in range(_PASSES):
            qapp.processEvents()
    finally:
        view.zoomChanged.disconnect(changes.append)
    return len(changes)


def _state(view) -> tuple[int, int, float, float, bool, bool]:
    vp, scene = view.viewport().size(), view.sceneRect()
    return (vp.width(), vp.height(), scene.width(), scene.height(),
            view.verticalScrollBar().isVisible(), view.horizontalScrollBar().isVisible())


def test_fit_width_settles_where_the_page_fits_only_beside_its_scrollbar(qapp, one_page):
    """The Fit Width loop. A Letter page fitted 700 px wide is too tall for a view under ~880 px
    and short enough once the bar takes its width, so heights near there were the loop. The sweep
    has to cross that band to mean anything, so the test asks to see it."""
    view = _open(qapp, one_page, 700, 700)
    view.fit_width()
    qapp.processEvents()
    in_the_band = 0
    for height in range(860, 920, 2):
        changes = _resize(qapp, view, 700, height)
        assert changes <= 1, f"the zoom changed {changes} times at 700x{height}"
        vp_w, vp_h, scene_w, scene_h, vbar, hbar = _state(view)
        assert not hbar, f"700x{height}"
        assert scene_w <= vp_w and (vbar or scene_h <= vp_h), f"700x{height}"
        if vbar:
            assert scene_w == pytest.approx(vp_w), f"700x{height}: fitted beside the bar"
        elif vp_w - scene_w >= 1:
            in_the_band += 1   # fitted to leave room for a bar that it then did not need
    assert in_the_band, "the sweep never reached the heights that looped"


def test_fit_page_settles_where_the_window_has_the_pages_shape(qapp, one_page):
    """The Fit Page loop: an 800 px view looped at 624 px wide, where the page fits both ways at
    once. A single page in Fit Page never needs a scrollbar, and it fills the view one way."""
    view = _open(qapp, one_page, 900, 800)
    for width in range(660, 590, -2):
        changes = _resize(qapp, view, width, 800)
        assert changes <= 1, f"the zoom changed {changes} times at {width}x800"
        vp_w, vp_h, scene_w, scene_h, vbar, hbar = _state(view)
        assert not (vbar or hbar), f"{width}x800"
        assert scene_w <= vp_w and scene_h <= vp_h, f"{width}x800"
        assert scene_w == pytest.approx(vp_w) or scene_h == pytest.approx(vp_h), f"{width}x800"


def test_a_fitted_page_is_never_a_hair_wider_than_its_space(qapp, one_page):
    """The rounding behind the Fit Page loop, checked without Qt deciding anything: the scene built
    from a fitted zoom is no bigger than the space, at every size. 410 of these 1,200 widths came
    out 5e-13 px over before the fit stepped the zoom down."""
    view = _open(qapp, one_page, 600, 600)
    sizes = view._page_sizes()
    for size in range(300, 1500):
        zoom = view._fit_zoom_in(size, 10_000, False)
        _placed, scene_w, _h = view._layout(view._scale_at(zoom), sizes)
        assert scene_w <= size, f"Fit Width {scene_w!r} in {size}"
        zoom = view._fit_zoom_in(10_000, size, True)
        _placed, _w, scene_h = view._layout(view._scale_at(zoom), sizes)
        assert scene_h <= size, f"Fit Page {scene_h!r} in {size}"


@pytest.mark.parametrize("fit", ["width", "page"])
def test_a_long_document_still_fits_beside_its_scrollbar(qapp, tmp_path, fit):
    """The ordinary case is unchanged: eight pages are always taller than the view, so the bar is
    always there and the page fits the width beside it, with no horizontal bar."""
    view = _open(qapp, _letter_pdf(tmp_path / "eight.pdf", 8), 700, 700)
    getattr(view, f"fit_{fit}")()
    qapp.processEvents()
    for width in (520, 600, 700, 820):
        assert _resize(qapp, view, width, 700) <= 1
        vp_w, _vp_h, scene_w, _scene_h, vbar, hbar = _state(view)
        assert vbar and not hbar, f"{width}x700"
        assert scene_w <= vp_w, f"{width}x700"
        if fit == "width":
            assert scene_w == pytest.approx(vp_w), f"{width}x700"
