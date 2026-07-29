"""Current-page tracking (PLAN.md §M85). Offscreen GUI.

Owner report on an 18-slide 1920x1080 deck: clicking Slide 1 marked *two* thumbnails, and
widening the window walked the current slide forward one page per resize step. One root cause —
``_update_current`` named whichever page sat under the **viewport centre**, which stops being the
page you are looking at once a page is shorter than half the viewport (a 16:9 slide at fit-width
in a tall window is ~403 px in a ~966 px viewport, so the centre lands 1.2 pages down). The fix
tracks the page occupying the **largest visible area**, and keeps a *single*-row thumbnail
selection in step with a view-driven current row.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from app import PdfApp
from store.settings import Settings

# The geometry that exposes it: 16:9 pages in a viewport taller than ~1.125x its width.
_SLIDE_W, _SLIDE_H = 1920, 1080
_SLIDES = 8
# Tall and narrow, like the owner's window before it was widened.
_WIN_W, _WIN_H = 700, 1000


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def slides_pdf(tmp_path) -> str:
    """A deck of landscape 16:9 slides — pages wider than they are tall."""
    path = str(tmp_path / "slides.pdf")
    doc = fitz.open()
    for i in range(_SLIDES):
        page = doc.new_page(width=_SLIDE_W, height=_SLIDE_H)
        page.insert_text((100, 200), f"SLIDE-{i}", fontsize=48)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def win(qapp, slides_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    w = qapp.open_document(slides_pdf)
    w.resize(_WIN_W, _WIN_H)
    w.show()
    qapp.processEvents()
    w.view.fit_width()  # the sticky fit the owner was reading in
    qapp.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def _short_pages(view) -> bool:
    """The geometric precondition of the bug: a page shorter than half the viewport."""
    return view._pages[0]["h"] < view.viewport().height() / 2


# ---- M85.1: track the page by largest visible area ---------------------------


def test_the_repro_geometry_actually_holds(win):
    """Guard the fixture: without short pages the rest of this file proves nothing."""
    assert _short_pages(win.view)


def test_clicking_a_thumbnail_marks_only_that_page(win, qapp):
    """The owner's repro: click Slide 3, then Slide 1 — the view must land on page 0, not 1."""
    view, thumbs = win.view, win.thumbs
    thumbs.setCurrentRow(2)  # a click on Slide 3 (prior state)
    qapp.processEvents()
    thumbs.setCurrentRow(0)  # <-- the action: click Slide 1
    qapp.processEvents()

    assert view.current_page == 0
    assert thumbs.currentRow() == 0
    assert [i.row() for i in thumbs.selectedIndexes()] == [0]


def test_resizing_does_not_walk_the_current_page(win, qapp):
    """Each resize re-applies Fit Width -> goto_page(current); the page must not advance."""
    view = win.view
    view.goto_page(0)
    qapp.processEvents()
    assert view.current_page == 0

    for width in (750, 800, 850, 900):
        win.resize(width, _WIN_H)
        qapp.processEvents()
        assert view.current_page == 0, f"current page drifted while resizing to {width}"


def test_a_page_filling_the_viewport_is_current(win, qapp):
    """The ordinary case still holds: scroll to a page's top and it is the current page."""
    view = win.view
    for target in (0, 3, 5):
        view.goto_page(target)
        qapp.processEvents()
        assert view.current_page == target


def test_the_more_visible_of_two_pages_wins(win, qapp):
    """Scroll so page 2 is mostly off the top — page 3 owns more of the viewport, so it is current."""
    view = win.view
    view.goto_page(2)
    qapp.processEvents()
    # Push most of page 2 off the top; page 3 then covers more of the viewport than page 2.
    bar = view.verticalScrollBar()
    bar.setValue(bar.value() + int(view._pages[2]["h"] * 0.8))
    qapp.processEvents()
    assert view.current_page == 3


def test_two_page_view_marks_the_left_page_of_the_spread(win, qapp):
    """Facing layout: an equally-visible pair resolves to the left page, not the right.

    An explicit zoom, not the sticky Fit Width: fitting a 2x1920 pt spread into a 700 px window
    shrinks the rows until the whole deck is on screen at once, and then there is no spread for
    the viewport to be dominated by.
    """
    view = win.view
    view.set_page_layout("facing")
    # Rows ~432 px in a ~1000 px viewport — still the short-page case. Was 0.4 before M88.1, when
    # a point was one pixel; the same 432 px now costs 0.3, because 100% draws 1.333x larger.
    view.set_zoom(0.3)
    view._center_horizontally()
    qapp.processEvents()
    assert view._pages[2]["h"] < view.viewport().height() / 2

    view.goto_page(2)
    qapp.processEvents()
    assert view.current_page == 2


# ---- M85.2: current row and a single-row selection stay in step --------------


def test_a_view_driven_page_change_moves_a_single_row_selection(win, qapp):
    """Scrolling moves the current row; a lone selected row follows, so only one row is marked."""
    view, thumbs = win.view, win.thumbs
    thumbs.setCurrentRow(0)
    qapp.processEvents()
    assert [i.row() for i in thumbs.selectedIndexes()] == [0]

    view.goto_page(4)
    qapp.processEvents()
    assert thumbs.currentRow() == 4
    assert [i.row() for i in thumbs.selectedIndexes()] == [4]


def test_a_multi_row_selection_survives_scrolling(win, qapp):
    """A Ctrl-click selection staged for a page operation must not be collapsed by a scroll."""
    view, thumbs = win.view, win.thumbs
    # A real Ctrl-click multi-select begins from a plain click, which clears what was there — here
    # that is the marker the document opened with (page 1), which would otherwise ride along.
    thumbs.clearSelection()
    for row in (2, 3, 4):
        thumbs.item(row).setSelected(True)
    qapp.processEvents()
    assert sorted(i.row() for i in thumbs.selectedIndexes()) == [2, 3, 4]

    view.goto_page(6)
    qapp.processEvents()
    assert thumbs.currentRow() == 6
    assert sorted(i.row() for i in thumbs.selectedIndexes()) == [2, 3, 4]


def test_an_empty_selection_stays_empty(win, qapp):
    """Nothing selected is not a single-row selection — scrolling must not create one."""
    view, thumbs = win.view, win.thumbs
    thumbs.clearSelection()
    qapp.processEvents()

    view.goto_page(5)
    qapp.processEvents()
    assert thumbs.currentRow() == 5
    assert thumbs.selectedIndexes() == []
