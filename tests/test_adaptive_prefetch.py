"""M87.1 — the prefetch band shrinks as pages get heavier.

`_PREFETCH = 2` was a fixed constant: two pages above and two below the viewport, rendered ahead
whatever they cost. Measured on `main` (PR #207): at zoom >= 2 the visible band is 2 pages and the
render band 6, so **67% of everything rendered is prefetch** — 237 MB visible against 473 MB
prefetched at 8x, and 57% waste even at 1.0x. Those are pages the reader cannot reach without
several more scrolls, rendered at exactly the zoom where a page costs most.

The band is now bought with a byte allowance instead of a page count. The three things that have to
stay true: normal-zoom behaviour is unchanged, the *visible* pages are never starved however heavy
they get, and the annotation overlay's content marks ride the same band as the pixmaps they sit on.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from app import PdfApp
from store.settings import Settings
from viewer import pdf_view
from viewer.pdf_view import _PREFETCH, _PREFETCH_BYTES


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def settings(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "view_state.json")
    return qapp.settings


def _doc(tmp_path, pages=12, width=612, height=792, name="doc.pdf") -> str:
    path = str(tmp_path / name)
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=width, height=height)
        page.insert_text((36, 36), f"page {i}", fontsize=11)
    doc.save(path)
    doc.close()
    return path


def _open(qapp, path, zoom=None):
    win = qapp.open_document(path)
    win.resize(900, 700)
    qapp.processEvents()
    if zoom is not None:
        win.view.set_zoom(zoom)
        qapp.processEvents()
    return win


# ---- the allowance -----------------------------------------------------------


def test_ordinary_pages_at_ordinary_zoom_keep_the_full_prefetch(qapp, tmp_path, settings):
    """"Normal-zoom behaviour unchanged" — a Letter page at 100% is ~1.85 MB, so the allowance buys
    ~26 pages and the fixed cap of 2 is what binds, exactly as before."""
    win = _open(qapp, _doc(tmp_path), zoom=1.0)
    first, last = win.view._visible_range()
    assert win.view._page_bytes(first) < _PREFETCH_BYTES // 8   # nowhere near the allowance
    assert win.view._prefetch(first, last) == _PREFETCH
    win.close()


def test_the_band_shrinks_as_the_pages_get_heavier(qapp, tmp_path, settings):
    """The premise, end to end: zooming in makes each page cost more, and the band gives way."""
    win = _open(qapp, _doc(tmp_path))
    bands = []
    for zoom in (1.0, 2.0, 4.0, 8.0):
        win.view.set_zoom(zoom)
        qapp.processEvents()
        first, last = win.view._visible_range()
        bands.append(win.view._prefetch(first, last))
    assert bands == sorted(bands, reverse=True), f"band did not shrink monotonically: {bands}"
    assert bands[0] == _PREFETCH and bands[-1] == 0, bands
    win.close()


def test_a_large_format_page_shrinks_the_band_without_any_zoom(qapp, tmp_path, settings):
    """It is page *size* that drives memory, not zoom (PLAN.md §M87: three wildly different A4s
    produce byte-identical pixmaps, and the A0 from a 0.1 MB file is 16.6x any of them). At 100% an
    A0 sheet is ~32 MB, which buys one page of prefetch where a Letter sheet buys the full two; at
    200% it is ~128 MB and buys none."""
    a0 = _doc(tmp_path, pages=6, width=2384, height=3370, name="a0.pdf")   # ISO A0 in points
    win = _open(qapp, a0, zoom=1.0)
    first, last = win.view._visible_range()
    assert _PREFETCH_BYTES // 2 < win.view._page_bytes(first) <= _PREFETCH_BYTES
    assert win.view._prefetch(first, last) == 1

    win.view.set_zoom(2.0)
    qapp.processEvents()
    first, last = win.view._visible_range()
    assert win.view._prefetch(first, last) == 0
    win.close()


def test_the_visible_pages_are_never_starved(qapp, tmp_path, settings):
    """The band can fall to zero prefetch; it can never fall below what is on screen."""
    win = _open(qapp, _doc(tmp_path), zoom=8.0)
    first, last = win.view._visible_range()
    lo, hi = win.view.content_band()
    assert lo <= first and hi >= last
    for i in range(first, last + 1):
        assert not win.view._pages[i]["pix"].pixmap().isNull(), f"page {i} is on screen and blank"
    win.close()


def test_the_allowance_is_what_binds_not_the_page_count(qapp, tmp_path, settings, monkeypatch):
    """Same document, same zoom, smaller allowance → smaller band. Pins the mechanism rather than
    the constant, so re-tuning `_PREFETCH_BYTES` does not silently make this test vacuous."""
    win = _open(qapp, _doc(tmp_path), zoom=1.0)
    first, last = win.view._visible_range()
    assert win.view._prefetch(first, last) == _PREFETCH
    monkeypatch.setattr(pdf_view, "_PREFETCH_BYTES", win.view._page_bytes(first))
    assert win.view._prefetch(first, last) == 1
    monkeypatch.setattr(pdf_view, "_PREFETCH_BYTES", 0)
    assert win.view._prefetch(first, last) == 0
    win.close()


def test_the_heaviest_page_in_view_sets_the_band(qapp, tmp_path, settings):
    """Scaled by the heaviest visible page, not the average — in a mixed document the big sheet is
    what would blow the band, and it is the one the reader is most likely looking at."""
    path = str(tmp_path / "mixed.pdf")
    doc = fitz.open()
    doc.new_page(width=612, height=792)                # Letter
    doc.new_page(width=2384, height=3370)              # A0
    doc.save(path)
    doc.close()
    win = _open(qapp, path, zoom=1.0)
    assert win.view._prefetch(0, 0) == _PREFETCH       # the Letter sheet alone: the full band
    assert win.view._prefetch(1, 1) == 1               # the A0 alone: one page
    assert win.view._prefetch(0, 1) == 1               # both in view: the A0 sets it, not the mean
    win.close()


# ---- what rides the band -----------------------------------------------------


def test_content_marks_ride_the_same_band_as_the_pixmaps(qapp, tmp_path, settings):
    """`content_band` exists so the annotation overlay's rasterised marks are as lazy as the pages
    they sit on. It has to follow the *adaptive* band, or a heavy page's marks out-live its pixmap."""
    win = _open(qapp, _doc(tmp_path), zoom=1.0)
    first, last = win.view._visible_range()
    lo, hi = win.view.content_band()
    prefetch = win.view._prefetch(first, last)
    assert (lo, hi) == (max(0, first - prefetch), min(win.view._vdoc.page_count - 1, last + prefetch))

    win.view.set_zoom(8.0)
    qapp.processEvents()
    first, last = win.view._visible_range()
    assert win.view.content_band() == (first, last), "marks still prefetching at 8x"
    win.close()


def test_a_smaller_band_renders_fewer_pages(qapp, tmp_path, settings):
    """The point of the whole milestone: fewer pixmaps produced, not merely fewer kept."""
    import viewer.pixmap_cache as pc

    store = pc.PixmapCache(retain_pages=64, byte_ceiling=1 << 40)
    import viewer.pdf_view

    original = viewer.pdf_view.pixmap_cache
    viewer.pdf_view.pixmap_cache = store
    try:
        win = _open(qapp, _doc(tmp_path), zoom=1.0)
        at_100 = len(store)
        store.clear()
        win.view.set_zoom(8.0)
        qapp.processEvents()
        first, last = win.view._visible_range()
        assert len(store) == last - first + 1, "prefetched pages were still rendered at 8x"
        assert len(store) < at_100
        win.close()
    finally:
        viewer.pdf_view.pixmap_cache = original
