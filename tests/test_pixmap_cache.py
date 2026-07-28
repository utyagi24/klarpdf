"""M87.2 — the rendered-page store is bounded by **bytes**, globally, and never evicts what is
on screen.

The defect this fixes was measured on `main` (PR #207): one Ctrl+wheel sweep to max zoom on an
ordinary 60-page Letter document took the process from 127 MB to **4431 MB**, with the cache
sitting at exactly 48 entries throughout — because 48 entries is 89 MB of Letter at 100% and
4.3 GB of the same document at 8x. A page count is not a unit of memory.

Four properties, one section each: the ceiling binds (and binds *across windows*, since the store
used to be per-`PdfView`), the visible band is pinned and survives any eviction pass, a single page
larger than the whole budget still renders, and a window that stops being read gives its pixels
back. Plus the one that only matters because the store is now shared: a closed window must hand its
entries back, where the old per-view dict simply died with the view.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

from app import PdfApp
from store.settings import Settings
from viewer.pixmap_cache import PixmapCache, pixmap_bytes


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def settings(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "view_state.json")
    return qapp.settings


@pytest.fixture
def store(monkeypatch):
    """A private store with test-sized budgets, swapped in for the process-global one.

    Patched on `viewer.pdf_view`, which is where `PdfView` looked the name up at import time, so a
    real view built in this module uses it and the app's own store is left alone.
    """
    import viewer.pdf_view

    cache = PixmapCache(retain_pages=6, byte_ceiling=4 * 1024 * 1024)
    monkeypatch.setattr(viewer.pdf_view, "pixmap_cache", cache)
    return cache


def _pixmap(w: int, h: int) -> QPixmap:
    return QPixmap(w, h)


def _wide_pdf(tmp_path, pages: int = 12, width: int = 612, height: int = 792) -> str:
    """A plain multi-page document — enough pages that a band plus scrollback exceeds the budget."""
    path = str(tmp_path / "wide.pdf")
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=width, height=height)
        page.insert_text((72, 72), f"page {i}", fontsize=11)
    doc.save(path)
    doc.close()
    return path


# ---- the store itself --------------------------------------------------------


def test_pixmap_bytes_is_measured_not_assumed(qapp):
    """The premise of the whole milestone: a pixmap costs `w x h x depth/8`, and Qt's depth is the
    display format's — 32 bpp, not the 24 bpp every projection in PLAN.md assumed before PR #207
    measured it. Asserted as `depth()`, not as the literal 32, so this stays true on a display
    format that is genuinely something else."""
    pixmap = _pixmap(100, 200)
    assert pixmap_bytes(pixmap) == 100 * 200 * (pixmap.depth() // 8)
    assert pixmap.depth() >= 24  # never the 3-bytes-per-pixel the old estimates assumed


def test_byte_ceiling_bounds_the_store_where_a_count_would_not(qapp):
    """The measured defect, in miniature. Ten pages that a 48-entry limit would hold in full sit
    far over a byte ceiling, so the store keeps only what fits."""
    cache = PixmapCache(retain_pages=48, byte_ceiling=4 * 1024 * 1024)
    owner = cache.owner()
    for i in range(10):
        owner.put(("page", i), _pixmap(512, 512))  # ~1 MB each at 32 bpp
    assert len(cache) < 10, "an entry count let all ten in; that is the defect"
    assert cache.total_bytes <= 4 * 1024 * 1024


def test_eviction_is_least_recently_used(qapp):
    cache = PixmapCache(retain_pages=3, byte_ceiling=1 << 30)
    owner = cache.owner()
    for i in range(3):
        owner.put(i, _pixmap(10, 10))
    owner.get(0)                      # 0 is now the most recent, 1 the least
    owner.put(3, _pixmap(10, 10))
    assert owner.get(1) is None
    assert owner.get(0) is not None


def test_the_ceiling_is_global_across_windows(qapp):
    """`_cache` used to be a `PdfView` instance attribute — one per window, so N open documents
    meant N independent budgets and every figure ever quoted was silently per-window. Two owners
    now share one ceiling."""
    cache = PixmapCache(retain_pages=48, byte_ceiling=4 * 1024 * 1024)
    first, second = cache.owner(), cache.owner()
    for i in range(6):
        first.put(i, _pixmap(512, 512))
        second.put(i, _pixmap(512, 512))
    assert cache.total_bytes <= 4 * 1024 * 1024
    assert len(first) + len(second) == len(cache)


def test_owners_do_not_see_each_others_entries(qapp):
    """Two windows can hold the same `(index, zoom, rotation)` key for different documents."""
    cache = PixmapCache()
    first, second = cache.owner(), cache.owner()
    first.put((0, 1.0, 0), _pixmap(10, 10))
    assert second.get((0, 1.0, 0)) is None
    assert first.get((0, 1.0, 0)) is not None


def test_pinned_entries_survive_every_eviction_pass(qapp):
    cache = PixmapCache(retain_pages=2, byte_ceiling=1 << 30)
    owner = cache.owner()
    owner.pin([0, 1, 2])
    for i in range(3):
        owner.put(i, _pixmap(10, 10))
    for i in range(3, 12):            # far past the entry budget
        owner.put(i, _pixmap(10, 10))
    assert all(owner.get(i) is not None for i in (0, 1, 2))


def test_a_page_larger_than_the_whole_budget_still_renders(qapp):
    """An A0 poster at 500% is ~600 MB — bigger than the ceiling on its own. Pinned, it displays
    and the store goes over its nominal ceiling. That is the graceful behaviour, not a leak."""
    cache = PixmapCache(retain_pages=24, byte_ceiling=1024 * 1024)
    owner = cache.owner()
    owner.pin([("huge",)])
    owner.put(("huge",), _pixmap(2000, 2000))  # ~16 MB against a 1 MB ceiling
    assert owner.get(("huge",)) is not None
    assert cache.total_bytes > cache.byte_ceiling


def test_an_unpinned_over_budget_page_is_not_kept(qapp):
    """The flip side: nothing keeps an oversized page that is *not* on screen."""
    cache = PixmapCache(retain_pages=24, byte_ceiling=1024 * 1024)
    owner = cache.owner()
    owner.put(("huge",), _pixmap(2000, 2000))
    assert cache.total_bytes <= cache.byte_ceiling


def test_release_hands_an_owners_entries_back(qapp):
    """Only load-bearing because the store is shared: a window that closed without releasing would
    be a real leak, where the old per-view dict died with the view."""
    cache = PixmapCache()
    first, second = cache.owner(), cache.owner()
    first.pin([0])
    first.put(0, _pixmap(100, 100))
    second.put(0, _pixmap(100, 100))
    first.release()
    assert len(first) == 0 and len(second) == 1
    assert cache.total_bytes == pixmap_bytes(_pixmap(100, 100))
    first.put(0, _pixmap(100, 100))   # the stale pin went too, so this is evictable again
    cache.byte_ceiling = 0
    first.put(1, _pixmap(100, 100))
    assert len(first) == 0


def test_clear_keeping_pinned_drops_only_the_scrollback(qapp):
    cache = PixmapCache()
    owner = cache.owner()
    owner.pin([0, 1])
    for i in range(5):
        owner.put(i, _pixmap(10, 10))
    owner.clear(keep_pinned=True)
    assert len(owner) == 2
    assert all(owner.get(i) is not None for i in (0, 1))


# ---- the view using it -------------------------------------------------------


def test_a_zoom_sweep_stays_inside_the_ceiling(qapp, tmp_path, settings, store):
    """The reported defect end to end: zooming in repeatedly used to accumulate every zoom level's
    pixmaps because only the *count* was bounded. Each step's band is pinned while it paints, so
    the assertion is against the budget plus that band, not against the budget alone."""
    win = qapp.open_document(_wide_pdf(tmp_path))
    qapp.processEvents()
    zoom = win.view.zoom
    for _ in range(8):
        zoom *= 1.25
        win.view.set_zoom(zoom)
        qapp.processEvents()
    # An eviction pass stops when either the budget is met or only pinned entries remain, so the
    # store settles at whichever of the two is larger — never at "one entry per zoom level".
    pinned_keys = store._pinned.get(win.view._cache._id, frozenset())
    pinned_bytes = sum(pixmap_bytes(pm) for (_owner, key), pm in store._entries.items()
                       if key in pinned_keys)
    assert store.total_bytes <= max(store.byte_ceiling, pinned_bytes)
    assert len(store) <= max(store.retain_pages, len(pinned_keys)), "entries grew with the sweep"
    win.close()


def test_scrolling_the_same_band_twice_rasterises_nothing_the_second_time(qapp, tmp_path, settings, store):
    """"No thrash while scrolling." The band is pinned before it is painted, so no page of a pass
    can evict a page of the same pass — true by construction, not by picking a big enough budget.
    The store here is deliberately far too small (6 entries) for the band plus scrollback."""
    win = qapp.open_document(_wide_pdf(tmp_path))
    qapp.processEvents()
    win.view._render_visible()

    puts = []
    real_put = store.put
    store.put = lambda *a, **k: (puts.append(a[1]), real_put(*a, **k))[1]
    try:
        win.view._render_visible()
        win.view._render_visible()
    finally:
        store.put = real_put
    assert puts == [], f"re-rasterised {len(puts)} already-cached pages"
    win.close()


def test_losing_focus_drops_the_scrollback_but_not_the_visible_pages(qapp, tmp_path, settings, store):
    """A deactivated window may still be on screen beside the active one, so its band stays —
    blanking a window the reader can see is a defect traded for memory nobody asked to trade.
    The scrollback, which after a zoom sweep is the bulk of it, goes."""
    store.retain_pages, store.byte_ceiling = 48, 1 << 30   # room for a scrollback to exist at all
    win = qapp.open_document(_wide_pdf(tmp_path))
    qapp.processEvents()
    win.view.set_zoom(win.view.zoom * 1.25)   # leave the previous zoom's pixmaps behind
    qapp.processEvents()
    before = len(store)
    assert before > len(store._pinned.get(win.view._cache._id, ())), "no scrollback to drop"
    win.view.release_pixmaps(keep_visible=True)
    assert len(store) < before, "nothing was released"
    assert len(store) > 0, "the visible band was dropped too"
    assert not win.view._pages[win.view.current_page]["pix"].pixmap().isNull()
    win.close()


def test_minimising_drops_everything_and_restoring_paints_it_back(qapp, tmp_path, settings, store):
    """Minimised there is nothing to blank, so the scene items' own references go too — they are
    the part the store cannot reach."""
    win = qapp.open_document(_wide_pdf(tmp_path))
    qapp.processEvents()
    current = win.view.current_page

    win.view.release_pixmaps(keep_visible=False)
    assert len(store) == 0
    assert all(p["pix"].pixmap().isNull() for p in win.view._pages)

    win.view.restore_pixmaps()
    assert not win.view._pages[current]["pix"].pixmap().isNull()
    win.close()


def test_the_window_wires_activation_and_minimising_to_the_release(qapp, tmp_path, settings, store):
    """The wiring, not the release: `changeEvent` must call it, or the whole tier is dead code."""
    win = qapp.open_document(_wide_pdf(tmp_path))
    qapp.processEvents()
    calls = []
    win.view.release_pixmaps = lambda **kwargs: calls.append(kwargs)
    win.view.restore_pixmaps = lambda: calls.append("restore")

    from PySide6.QtCore import QEvent

    win.isActiveWindow = lambda: False          # i.e. focus went to another window
    win.changeEvent(QEvent(QEvent.Type.ActivationChange))
    assert calls == [{"keep_visible": True}]

    # Minimise / restore for real — Qt delivers the WindowStateChange itself, so this exercises the
    # dispatch as well as the branch.
    calls.clear()
    win.setWindowState(Qt.WindowState.WindowMinimized)
    qapp.processEvents()
    assert calls == [{"keep_visible": False}]

    calls.clear()
    win.setWindowState(Qt.WindowState.WindowNoState)
    qapp.processEvents()
    assert calls == ["restore"]
    win.close()


def test_closing_a_window_hands_its_entries_back(qapp, tmp_path, settings, store):
    """With one shared store this is the difference between a bounded app and a leak. PR #207 ruled
    out a leak on close under the *old* per-view dict, which died with the view; a global store has
    no such luck."""
    win = qapp.open_document(_wide_pdf(tmp_path))
    qapp.processEvents()
    assert len(store) > 0
    win.close()
    qapp.processEvents()
    assert len(store) == 0
