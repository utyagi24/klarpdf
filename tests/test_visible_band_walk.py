"""M87.3 — a render pass costs what is on screen, not what is in the document.

Carried from the M86.1 follow-up. Each `_render_visible` pass walked every page **twice**:
`_visible_range()` scanned all of them to find the intersecting range, then the body looped over
all of them again to drop offscreen pixmaps. Measured at ~6 ms per pass on 320 pages, so even after
M86.1 collapsed three passes to one, ~246 ms of a 40-step zoom sweep went on pages nowhere near the
viewport. Both walks are avoidable: the range is a binary search over a y-sorted list, and the drop
pass only needs the pages *currently holding* a pixmap, which the view can track.

A search rewrite is only as good as its agreement with the thing it replaced, so the bulk of this
file is a differential test: the same brute-force scan, over layouts built to break a naive binary
search — facing pages that share a y, wildly mixed page heights, and a page taller than the
viewport whose top sits far above it.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from app import PdfApp
from store.settings import Settings


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def settings(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "view_state.json")
    return qapp.settings


def _doc(tmp_path, sizes, name="doc.pdf") -> str:
    path = str(tmp_path / name)
    doc = fitz.open()
    for i, (w, h) in enumerate(sizes):
        page = doc.new_page(width=w, height=h)
        page.insert_text((20, 20), f"page {i}", fontsize=10)
    doc.save(path)
    doc.close()
    return path


def _brute_force_range(view):
    """What `_visible_range` did before M87.3, kept as the oracle."""
    view_rect = view.mapToScene(view.viewport().rect()).boundingRect()
    top, bottom = view_rect.top(), view_rect.bottom()
    first = last = None
    for i, p in enumerate(view._pages):
        if p["y"] + p["h"] >= top and p["y"] <= bottom:
            first = i if first is None else first
            last = i
    if first is None:
        return view._current, view._current
    return first, last


def _sweep_scroll(qapp, view, steps=25):
    """Every scroll position from top to bottom, plus the two ends."""
    bar = view.verticalScrollBar()
    span = bar.maximum() - bar.minimum()
    for step in range(steps + 1):
        bar.setValue(bar.minimum() + (span * step) // max(steps, 1))
        qapp.processEvents()
        yield bar.value()


LAYOUTS = {
    "uniform letter": [(612, 792)] * 12,
    "mixed heights": [(612, 200), (612, 1400), (612, 792), (612, 90), (612, 2000), (612, 400)],
    "one page taller than the viewport": [(612, 300), (612, 6000), (612, 300), (612, 300)],
    "a0 among letters": [(612, 792), (2384, 3370), (612, 792), (612, 792)],
    "single page": [(612, 792)],
}


@pytest.mark.parametrize("layout", list(LAYOUTS))
@pytest.mark.parametrize("facing", [False, True])
def test_the_binary_search_agrees_with_the_scan_it_replaced(qapp, tmp_path, settings, layout, facing):
    """The differential test. Every scroll position, both page layouts, five page-size regimes."""
    win = qapp.open_document(_doc(tmp_path, LAYOUTS[layout]))
    win.resize(900, 700)
    qapp.processEvents()
    if facing:
        win.view.set_page_layout("facing")
        qapp.processEvents()

    for zoom in (0.5, 1.0, 2.0):
        win.view.set_zoom(zoom)
        qapp.processEvents()
        for offset in _sweep_scroll(qapp, win.view):
            assert win.view._visible_range() == _brute_force_range(win.view), (
                f"{layout}, facing={facing}, zoom={zoom}, scroll={offset}")
    win.close()


def test_facing_pages_sharing_a_y_are_both_found(qapp, tmp_path, settings):
    """The case that breaks a plain `bisect`: the two pages of a facing row have the *same* y, so
    the search has to step back off the row's second page to find its first."""
    win = qapp.open_document(_doc(tmp_path, [(612, 792)] * 8))
    win.resize(1400, 500)
    qapp.processEvents()
    win.view.set_page_layout("facing")
    win.view.set_zoom(1.0)
    qapp.processEvents()
    tops = win.view._page_tops
    assert tops[0] == tops[1], "not actually a facing row"

    for _offset in _sweep_scroll(qapp, win.view):
        first, last = win.view._visible_range()
        assert (first, last) == _brute_force_range(win.view)
    win.close()


def test_the_page_tops_index_stays_sorted_through_every_relayout(qapp, tmp_path, settings):
    """The binary search's precondition. Zoom, rotation and the facing toggle all rebuild the
    scene, and each rebuild has to leave the index non-decreasing and the same length as `_pages`."""
    win = qapp.open_document(_doc(tmp_path, LAYOUTS["mixed heights"]))
    win.resize(900, 700)
    qapp.processEvents()

    def check(label):
        tops = win.view._page_tops
        assert len(tops) == len(win.view._pages), label
        assert tops == sorted(tops), f"{label}: {tops}"

    check("as opened")
    for zoom in (0.25, 1.0, 4.0):
        win.view.set_zoom(zoom)
        qapp.processEvents()
        check(f"zoom {zoom}")
    win.view.rotate_view(90)
    qapp.processEvents()
    check("rotated")
    win.view.set_page_layout("facing")
    qapp.processEvents()
    check("facing")
    win.close()


# ---- the second walk: dropping what scrolled off ----------------------------


def test_only_the_band_holds_a_pixmap_after_scrolling(qapp, tmp_path, settings):
    """The drop pass is now driven by a tracked set rather than by asking all N pages. The visible
    behaviour it has to preserve: pages outside the band hold nothing."""
    win = qapp.open_document(_doc(tmp_path, [(612, 792)] * 30))
    win.resize(900, 700)
    qapp.processEvents()
    win.view.set_zoom(1.0)
    qapp.processEvents()

    for _offset in _sweep_scroll(qapp, win.view, steps=12):
        lo, hi = win.view.content_band()
        painted = {i for i, p in enumerate(win.view._pages) if not p["pix"].pixmap().isNull()}
        assert painted <= set(range(lo, hi + 1)), f"painted outside the band: {painted - set(range(lo, hi + 1))}"
        assert painted == win.view._painted, "the tracked set drifted from what is actually painted"
    win.close()


def test_the_tracked_set_survives_a_rebuild(qapp, tmp_path, settings):
    """`scene.clear()` destroys the pixmap items, so a stale entry would have the drop pass call
    `setPixmap` on a dead item. Rebuild via a layout change, then re-check the invariant."""
    win = qapp.open_document(_doc(tmp_path, [(612, 792)] * 12))
    win.resize(900, 700)
    qapp.processEvents()
    assert win.view._painted

    win.view.set_page_layout("facing")
    qapp.processEvents()
    painted = {i for i, p in enumerate(win.view._pages) if not p["pix"].pixmap().isNull()}
    assert painted == win.view._painted
    win.close()


def test_a_background_release_empties_the_tracked_set(qapp, tmp_path, settings):
    win = qapp.open_document(_doc(tmp_path, [(612, 792)] * 12))
    win.resize(900, 700)
    qapp.processEvents()
    win.view.release_pixmaps(keep_visible=False)
    assert win.view._painted == set()
    assert all(p["pix"].pixmap().isNull() for p in win.view._pages)

    win.view.restore_pixmaps()
    painted = {i for i, p in enumerate(win.view._pages) if not p["pix"].pixmap().isNull()}
    assert painted and painted == win.view._painted
    win.close()


# ---- and the point of it all ------------------------------------------------


def test_the_pass_does_not_touch_every_page_in_the_document(qapp, tmp_path, settings):
    """The actual claim: cost follows the band, not the document. Counted rather than timed, so it
    cannot flake — a 320-page document must not cost 320 page lookups per pass."""

    class CountingPages(list):
        touches = 0

        def __getitem__(self, index):
            CountingPages.touches += 1
            return list.__getitem__(self, index)

    win = qapp.open_document(_doc(tmp_path, [(612, 792)] * 320))
    win.resize(900, 700)
    qapp.processEvents()
    win.view.set_zoom(1.0)
    qapp.processEvents()

    win.view._pages = CountingPages(win.view._pages)
    CountingPages.touches = 0
    win.view._render_visible()
    touched = CountingPages.touches

    lo, hi = win.view.content_band()
    assert touched < 320, f"the pass still walks the whole document ({touched} lookups)"
    # A generous ceiling: a few lookups per band page across the search, the render and the drop.
    assert touched <= 8 * (hi - lo + 1), f"{touched} lookups for a {hi - lo + 1}-page band"
    win.close()
