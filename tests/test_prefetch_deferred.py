"""M92.4 — prefetch runs off the scroll's critical path (`PLAN.md` §M92). Offscreen GUI.

The defect, owner-reported 2026-08-01: with smooth scrolling on, *"pages with images slow down and
then pick speed again when pages with text come in"*.

**The stall was entirely prefetch.** Measured across a document alternating text and full-page
images, scrolling one glide-frame at a time: **visible**-page rendering cost **0 ms at every zoom** —
the reader never waits for a page they are looking at, because prefetch had already cached it — while
**prefetch** cost 48/101/166/356 ms at zoom 0.91/1.5/2/3, all of it paid synchronously inside the
scroll handler. Speculative work for pages one or two ahead, on the one thread that also has to
animate.

So M92.4 renders the visible pages exactly as before and *queues* the margin, draining one page per
tick and never while a glide is running. Measured after: at zoom 2 and 3 the frames that miss 60 Hz
**while the page is moving** go from 4 and 5 to **zero**, worst frame from 42 ms and 89 ms to ~1 ms.

These tests pin the mechanism, not the timings: what is painted synchronously, what is queued, what
order it drains in, and — the property that does the work — that a running glide defers it.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent

from app import PdfApp
from store.settings import Settings
from viewer.pdf_view import _WHEEL_NOTCH

NONE = Qt.KeyboardModifier.NoModifier


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def many_pdf(tmp_path) -> str:
    """20 plain pages — enough that a viewport shows a couple and the prefetch margin is real."""
    import pymupdf as fitz

    path = str(tmp_path / "many.pdf")
    doc = fitz.open()
    for i in range(20):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 100), f"page {i + 1}", fontsize=24)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def view(qapp, many_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    win = qapp.open_document(many_pdf)
    win.resize(900, 700)
    win.show()
    qapp.processEvents()
    v = win.view
    v.set_zoom(1.0)
    qapp.processEvents()
    yield v
    win.undo_stack.setClean()
    win.close()


def _scroll_to(view, value):
    view.verticalScrollBar().setValue(value)
    PdfApp.instance().processEvents()


def _drain_all(view, limit=50):
    for _ in range(limit):
        if not view._prefetch_queue:
            break
        view._drain_prefetch()


# ---- what is paid now, and what is deferred --------------------------------------


def test_only_visible_pages_are_rasterised_during_a_scroll(view):
    """**The fix.** After a render pass, every *visible* page is painted and the margin is not —
    the margin is what used to cost 46 ms in a single frame."""
    _scroll_to(view, 3000)
    view._prefetch_queue.clear()
    view._painted.clear()
    view._render_visible()

    first, last = view._visible_range()
    assert set(range(first, last + 1)) <= view._painted, "a visible page was left unpainted"
    for index in view._prefetch_queue:
        assert not (first <= index <= last), "a visible page was deferred instead of painted"
    assert view._prefetch_queue, "nothing was queued — the margin vanished rather than deferring"


def test_the_queue_holds_the_prefetch_margin(view):
    _scroll_to(view, 3000)
    first, last = view._visible_range()
    margin = view._prefetch(first, last)
    view._painted.clear()
    view._render_visible()
    expected = set(range(max(0, first - margin), first)) | \
        set(range(last + 1, min(len(view._pages) - 1, last + margin) + 1))
    assert set(view._prefetch_queue) == expected


def test_the_queue_is_ordered_towards_where_the_reader_is_going(view):
    """Direction of travel first, nearest first — the page being scrolled *towards* is worth having
    before the one being left behind."""
    _scroll_to(view, 2000)
    _scroll_to(view, 4000)                       # ...moving down
    assert view._scroll_dir == 1
    first, last = view._visible_range()
    view._painted.clear()
    view._render_visible()
    ahead = [i for i in view._prefetch_queue if i > last]
    behind = [i for i in view._prefetch_queue if i < first]
    if ahead and behind:
        assert view._prefetch_queue.index(ahead[0]) < view._prefetch_queue.index(behind[0])
    assert ahead == sorted(ahead), "ahead should be nearest-first"

    _scroll_to(view, 2000)                       # ...and now up
    assert view._scroll_dir == -1
    first, last = view._visible_range()
    view._painted.clear()
    view._render_visible()
    ahead = [i for i in view._prefetch_queue if i > last]
    behind = [i for i in view._prefetch_queue if i < first]
    if ahead and behind:
        assert view._prefetch_queue.index(behind[0]) < view._prefetch_queue.index(ahead[0])


def test_the_drain_paints_one_page_per_tick(view):
    """One page per tick because a single page is 4 ms of text and up to 91 ms of image at 3x:
    draining in a loop would move the stall rather than remove it."""
    _scroll_to(view, 3000)
    view._painted.clear()
    view._render_visible()
    queued = len(view._prefetch_queue)
    assert queued >= 2, "fixture: need at least two queued pages for this to mean anything"

    view._drain_prefetch()
    assert len(view._prefetch_queue) == queued - 1

    _drain_all(view)
    assert not view._prefetch_queue
    assert not view._prefetch_timer.isActive(), "the timer kept running with an empty queue"


# ---- the property that actually buys the smooth glide ----------------------------


def test_a_running_glide_defers_the_drain(view):
    """**This is the fix's whole mechanism.** Prefetch is speculative, so it can always wait for the
    animation; without this it competes with the glide for the same frame and the stall returns."""
    _scroll_to(view, 3000)
    view._painted.clear()
    view._render_visible()
    queued = list(view._prefetch_queue)
    assert queued

    view.smooth_scrolling = True
    pt = QPointF(view.viewport().rect().center())
    d = QPoint(0, -_WHEEL_NOTCH)
    view.wheelEvent(QWheelEvent(pt, view.viewport().mapToGlobal(pt), QPoint(0, 0), d,
                                Qt.MouseButton.NoButton, NONE, Qt.ScrollPhase.NoScrollPhase, False))
    assert view._glide_timer.isActive(), "fixture: no glide is running, so this proves nothing"

    before = list(view._prefetch_queue)
    view._drain_prefetch()
    assert view._prefetch_queue == before, "the drain ran while the page was moving"

    view.stop_glide()
    view._drain_prefetch()
    assert len(view._prefetch_queue) == len(before) - 1, "the drain did not resume once settled"


# ---- staleness ------------------------------------------------------------------


def test_a_rebuild_drops_stale_queue_entries(view):
    """A rebuild can change the page *count*, so a queued index is not merely wrong — it can be out
    of range.

    Note what this does **not** assert: that the queue ends up empty. `_build_scene` renders once it
    has finished, which legitimately re-queues a margin for the *new* layout — an earlier version of
    this test demanded an empty queue and failed for that reason. The property that matters is that
    nothing from the old layout survives.
    """
    _scroll_to(view, 3000)
    view._painted.clear()
    view._render_visible()
    assert view._prefetch_queue
    view._prefetch_queue.append(9_999)                  # a page the rebuild will not have
    view._build_scene()
    assert 9_999 not in view._prefetch_queue, "a stale index survived the rebuild"
    assert all(0 <= i < len(view._pages) for i in view._prefetch_queue)


def test_releasing_pixmaps_stops_speculating(view):
    """A background window that just handed its pixels back must not rasterise pages straight into
    the store again."""
    _scroll_to(view, 3000)
    view._painted.clear()
    view._render_visible()
    assert view._prefetch_queue
    view.release_pixmaps(keep_visible=True)
    assert not view._prefetch_queue
    assert not view._prefetch_timer.isActive()


def test_a_visible_page_is_never_left_to_the_queue(view):
    """The reader must never be shown a blank page they are looking at: deferral applies to the
    margin only, whatever the cache state."""
    from viewer.pixmap_cache import pixmap_cache

    pixmap_cache.clear()
    view._painted.clear()
    _scroll_to(view, 5000)
    first, last = view._visible_range()
    assert set(range(first, last + 1)) <= view._painted
