"""A slow page is stretched while a window edge moves, and drawn once it rests (PLAN.md §M152,
M152.1). Offscreen GUI.

Two causes of #360, both about drawing a page nobody needs drawn yet:

* **Every resize step redrew the page.** With a fit on, a drag changes the zoom at every mouse
  move, and on the NADA cover each step took about a second. Now, when the pages on screen took
  longer than ``_DRAW_BUDGET_S`` to draw last time, each step shows their pictures stretched to
  the new size, and they are drawn once the edge has rested for ``_RESIZE_SETTLE_MS``.
* **A page that only touched the view was drawn in full.** A page counts as on screen only when at
  least one whole pixel of it is in view.

The rest of the suite counts every page as quick (conftest ``_quick_pages``), so a resize there
draws at once. These tests set the budget themselves.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtCore import QRectF
from PySide6.QtTest import QTest

import viewer.pdf_view
from app import PdfApp
from klarpdf.model.virtual_document import VirtualDocument
from viewer.pdf_view import _DRAW_BUDGET_S, PdfView


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def eight_pages(tmp_path) -> str:
    path = str(tmp_path / "eight.pdf")
    doc = fitz.open()
    for i in range(8):
        page = doc.new_page()   # Letter
        page.insert_text((72, 72), f"page {i + 1}", fontsize=24)
    doc.save(path)
    doc.close()
    return path


def _open(qapp, path, state=None) -> PdfView:
    view = PdfView(VirtualDocument.from_path(path))
    view.resize(600, 700)
    view.show()
    qapp.processEvents()
    view.open_at(state or {})
    qapp.processEvents()
    return view


@pytest.fixture
def view(qapp, eight_pages):
    v = _open(qapp, eight_pages)
    yield v
    v.deleteLater()


@pytest.fixture
def slow(monkeypatch):
    """Every page drawn counts as slow: any drawing takes longer than no time at all."""
    monkeypatch.setattr(viewer.pdf_view, "_DRAW_BUDGET_S", 0.0)


@pytest.fixture(autouse=True)
def long_wait(monkeypatch):
    """A wait for the edge to rest that cannot run out by itself during a test, so a slow machine
    cannot draw before the test looks. :func:`_rest` ends it."""
    monkeypatch.setattr(viewer.pdf_view, "_RESIZE_SETTLE_MS", 60_000)


@pytest.fixture
def drawings(monkeypatch):
    """The source page numbers PyMuPDF was asked to draw, in order: whole pages, and since M152.2
    pieces, which are drawn from the page's display list."""
    seen: list[int] = []
    real_page, real_list, real_make = fitz.Page.get_pixmap, fitz.DisplayList.get_pixmap, fitz.Page.get_displaylist

    inside = []   # PyMuPDF draws a whole page through a display list of its own

    def page_drawn(self, *args, **kwargs):
        seen.append(self.number)
        inside.append(True)
        try:
            return real_page(self, *args, **kwargs)
        finally:
            inside.pop()

    def list_made(self, *args, **kwargs):
        dlist = real_make(self, *args, **kwargs)
        dlist.page_number = self.number
        return dlist

    def piece_drawn(self, *args, **kwargs):
        if not inside:
            seen.append(self.page_number)
        return real_list(self, *args, **kwargs)

    monkeypatch.setattr(fitz.Page, "get_pixmap", page_drawn)
    monkeypatch.setattr(fitz.Page, "get_displaylist", list_made)
    monkeypatch.setattr(fitz.DisplayList, "get_pixmap", piece_drawn)
    return seen


def _settle(qapp, view) -> None:
    """End any wait for an edge to rest, and draw every piece still to draw (M152.2).

    With every page slow, the window's own first resizes start the wait, and a page is drawn in
    pieces over several turns."""
    if view._settle_timer.isActive():
        _rest(qapp, view)
    for _ in range(500):
        if not view._piece_timer.isActive() and not view._prefetch_timer.isActive():
            return
        QTest.qWait(5)
    pytest.fail("the pieces never finished")


def _rest(qapp, view) -> None:
    """The edge rests: let the view's own timer run out, through its real connection."""
    assert view._settle_timer.isActive()
    view._settle_timer.start(1)
    QTest.qWait(30)
    qapp.processEvents()
    assert not view._settle_timer.isActive()


def _step(qapp, view, dx: int = 40) -> None:
    view.resize(view.width() + dx, view.height())
    qapp.processEvents()


def _stretched_to_fit(view, index: int) -> bool:
    """Whether page ``index`` shows a picture drawn for another size, stretched over the page.
    Within one stretched pixel: the smaller picture's last pixel is rounded up, then stretched."""
    page = view._pages[index]
    item = page["pix"]
    shown = item.sceneBoundingRect()
    return (not item.pixmap().isNull() and item.scale() != 1.0
            and shown.width() == pytest.approx(page["w"], abs=item.scale())
            and shown.height() == pytest.approx(page["h"], abs=item.scale()))


def _drawn_for_this_size(view, index: int) -> bool:
    item = view._pages[index]["pix"]
    pixmap = item.pixmap()
    return (not pixmap.isNull() and item.scale() == 1.0
            and pixmap.width() == pytest.approx(view._pages[index]["w"] * view._dpr, abs=1))


# ---- a slow page is stretched while the edge moves -----------------------------


def test_a_slow_page_is_stretched_while_the_edge_moves(view, qapp, slow, drawings):
    view.fit_width()
    _settle(qapp, view)
    width_before = view._pages[0]["w"]
    drawings.clear()

    _step(qapp, view)

    assert view._pages[0]["w"] > width_before          # the fit zoomed in
    assert drawings == []
    assert _stretched_to_fit(view, 0)
    assert view._settle_timer.isActive()


def test_no_step_draws_until_the_edge_rests(view, qapp, slow, drawings):
    view.fit_width()
    _settle(qapp, view)
    drawings.clear()

    for _ in range(5):
        _step(qapp, view, 8)

    assert drawings == []
    assert _stretched_to_fit(view, 0)


def test_each_step_restarts_the_wait(view, qapp, slow):
    """The page is drawn once the edge has *rested*, not a fixed time after the drag began."""
    view.fit_width()
    _settle(qapp, view)
    _step(qapp, view, 8)
    view._settle_timer.start(50)            # most of the wait has passed

    _step(qapp, view, 8)

    assert view._settle_timer.remainingTime() > 50


def test_the_pages_on_screen_are_drawn_when_the_edge_rests(view, qapp, slow, drawings):
    view.fit_width()
    _settle(qapp, view)
    for _ in range(3):
        _step(qapp, view, 8)
    first, last = view._visible_range()
    drawings.clear()

    _rest(qapp, view)
    assert drawings and set(drawings) <= set(range(first - 2, last + 3))   # the pages on screen first

    _settle(qapp, view)
    for i in range(first, last + 1):
        assert _drawn_for_this_size(view, i)
    assert not view._stand_ins


def test_a_quick_page_is_drawn_at_every_step(view, qapp, drawings):
    """Today's behaviour, kept: conftest counts every page as quick, so nothing is stretched."""
    view.fit_width()
    qapp.processEvents()
    drawings.clear()

    _step(qapp, view)

    assert 0 in drawings
    assert _drawn_for_this_size(view, 0)
    assert not view._settle_timer.isActive()


def test_the_decision_follows_the_pages_on_screen(view, qapp, monkeypatch, drawings):
    """With the app's own budget, a slow page stretches and a quick one elsewhere does not. The
    cover's time is set by hand: a real 1 s page would make the suite slow."""
    monkeypatch.setattr(viewer.pdf_view, "_DRAW_BUDGET_S", _DRAW_BUDGET_S)
    view.fit_width()
    qapp.processEvents()

    def cover_is_slow():
        view._draw_rate.update({i: 1e-12 for i in range(8)})
        view._draw_rate[0] = 1e-3            # seconds a pixel: about 250 s for the page

    view.goto_page(5)
    qapp.processEvents()
    cover_is_slow()
    drawings.clear()
    _step(qapp, view)
    assert drawings and not view._settle_timer.isActive()

    view.goto_page(0)
    qapp.processEvents()
    cover_is_slow()
    drawings.clear()
    _step(qapp, view)
    assert drawings == [] and view._settle_timer.isActive()


def test_a_size_already_drawn_shows_its_own_picture(view, qapp, slow, drawings):
    """Dragging back to where the drag began finds that size's picture in the store, sharp."""
    view.fit_width()
    _settle(qapp, view)
    drawings.clear()

    _step(qapp, view, 40)
    _step(qapp, view, -40)

    assert drawings == []
    assert _drawn_for_this_size(view, 0)
    assert 0 not in view._stand_ins


def test_the_drawing_ahead_waits_for_the_edge_to_rest(qapp, eight_pages, slow, drawings):
    """At Fit Page in a wide window the height sets the zoom, so a wider step keeps the zoom and
    the queue of pages to draw ahead survives it. That queue must wait while the edge moves."""
    view = PdfView(VirtualDocument.from_path(eight_pages))
    view.resize(900, 700)
    view.show()
    qapp.processEvents()
    view.open_at({})                        # draws page 1 and queues the pages after it
    assert view._prefetch_queue              # the precondition: something to draw ahead
    zoom = view.zoom

    _step(qapp, view)
    assert view.zoom == zoom and view._settle_timer.isActive()
    drawings.clear()
    view._drain_prefetch()
    QTest.qWait(50)

    assert drawings == []
    view.deleteLater()


def test_a_minimized_window_does_not_draw_when_the_wait_ends(view, qapp, slow, drawings):
    view.fit_width()
    _settle(qapp, view)
    _step(qapp, view)

    view.release_pixmaps(keep_visible=False)

    assert not view._settle_timer.isActive()
    assert not view._stand_ins


def test_an_edit_while_waiting_draws_at_once(view, qapp, slow, drawings):
    """An edit empties the store, so there is nothing left to stretch: drawing starts at once,
    with the picture from before the edit standing in (M152.2)."""
    view.fit_width()
    _settle(qapp, view)
    _step(qapp, view)
    drawings.clear()

    view.reload()

    assert not view._settle_timer.isActive()
    assert 0 in drawings
    _settle(qapp, view)
    assert _drawn_for_this_size(view, 0)


# ---- a page counts only when a whole pixel of it is in view ----------------------


def test_a_page_counts_only_with_a_whole_pixel_in_view(view):
    p0, p1 = view._pages[0], view._pages[1]
    end = p0["y"] + p0["h"]

    def first_at(top):
        return view._pages_in(QRectF(0, top, 500, 600))[0]

    assert first_at(end) == 1               # touching: 0 px of page 1 shows
    assert first_at(end - 0.5) == 1         # a sliver thinner than a pixel
    assert first_at(end - 1.0) == 0         # a whole pixel

    def last_at(bottom):
        return view._pages_in(QRectF(0, bottom - 600, 500, 600))[1]

    assert last_at(p1["y"]) == 0
    assert last_at(p1["y"] + 0.5) == 0
    assert last_at(p1["y"] + 1.0) == 1


def test_fit_page_does_not_draw_the_page_that_ends_at_the_top(qapp, eight_pages, drawings):
    """At Fit Page, moving to page 2 leaves page 1 ending exactly at the top of the view (#360)."""
    view = _open(qapp, eight_pages, {"page": 5})
    view.goto_page(1)
    p0 = view._pages[0]
    top = view.mapToScene(view.viewport().rect()).boundingRect().top()
    assert p0["y"] + p0["h"] == pytest.approx(top)      # the precondition: touching
    assert 0 not in drawings
    view.deleteLater()


def test_fit_width_does_not_draw_a_sliver_left_by_rounding(qapp, eight_pages, drawings):
    """At Fit Width the scroll bar rounds page 2's top down and leaves a sliver of page 1 (#360).
    The window width that leaves one depends on the scroll bar's width, so it is searched for."""
    view = _open(qapp, eight_pages)
    for width in range(600, 760, 3):
        view.resize(width, 700)
        qapp.processEvents()
        view.fit_width()
        view.goto_page(1)
        p0 = view._pages[0]
        sliver = p0["y"] + p0["h"] - view.mapToScene(view.viewport().rect()).boundingRect().top()
        if 0.25 < sliver < 0.75:
            break
    else:
        pytest.fail("no window width left a sliver of page 1")
    view.goto_page(6)
    view.release_pixmaps(keep_visible=False)    # nothing in the store: a visible page is drawn
    drawings.clear()

    view.goto_page(1)

    assert 0 not in drawings
    assert 1 in drawings
    view.deleteLater()
