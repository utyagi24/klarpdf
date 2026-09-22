"""Zoom and resize keep the page you are reading (PLAN.md §M151, #357, #359). Offscreen GUI.

Near either end of a document the view stops short: it cannot scroll above the first page or below
the last, and a document smaller than the window is centred in it. Both reports came from reading
the position back from that stopped view. Zooming out with the buttons on page 1 put the middle of
the window on page 2, so zooming back in went to page 2 (#357). Narrowing a Fit Width window on
page 10 of 23 re-read the page from a view already stopped at the end, until page 1 won (#359).

The owner's calls (2026-09-21) are pinned here too: with a fit on, a resize keeps the line at the
top of the window where it is; the zoom buttons return to where you were until you scroll; and
Ctrl+wheel zooms on whatever is under the mouse on screen, as it always has.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent

from app import PdfApp
from klarpdf.model.virtual_document import VirtualDocument
from store.settings import Settings
from viewer.pdf_view import _MAX_ZOOM, _MIN_ZOOM, _PAGE_GAP, _WHEEL_NOTCH, PdfView


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


def _pdf(tmp_path, name: str, count: int, size: tuple[int, int]) -> str:
    path = str(tmp_path / name)
    doc = fitz.open()
    for i in range(count):
        page = doc.new_page(width=size[0], height=size[1])
        page.insert_text((72, size[1] / 2), f"PAGE-{i + 1}", fontsize=24)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def letters(tmp_path) -> str:
    """Twelve portrait pages — the shape of the #357 report."""
    return _pdf(tmp_path, "letters.pdf", 12, (612, 792))


@pytest.fixture
def report(tmp_path) -> str:
    """Twenty-three portrait pages — the page count of the NADA report in #359."""
    return _pdf(tmp_path, "report.pdf", 23, (612, 792))


@pytest.fixture
def slides(tmp_path) -> str:
    """Eight 16:9 slides: short pages, so two of them fit a tall window whole."""
    return _pdf(tmp_path, "slides.pdf", 8, (1920, 1080))


@pytest.fixture
def make_view(qapp):
    made: list[PdfView] = []

    def make(path: str, width: int, height: int, page: int = 0) -> PdfView:
        view = PdfView(VirtualDocument.from_path(path))
        view.resize(width, height)
        view.show()
        qapp.processEvents()
        view.open_at({"page": page})
        qapp.processEvents()
        made.append(view)
        return view

    yield make
    for view in made:
        view.deleteLater()


def _zoom_all_the_way(qapp, view, step) -> None:
    for _ in range(40):
        before = view.zoom
        step()
        qapp.processEvents()
        if view.zoom == before:
            return


def _under_centre(view) -> tuple[int, float, float]:
    """The content under the middle of the window, as it is on screen."""
    return view._content_at(view.mapToScene(view.viewport().rect().center()))


def _top_line(view) -> tuple[int, float]:
    """The content on the reader's top line, as it is on screen: its page, and how many pixels
    down that page it sits at the current zoom."""
    pt = view.mapToScene(QPoint(view.viewport().width() // 2, _PAGE_GAP))
    page, _fx, fy = view._content_at(pt)
    return page, fy * view._pages[page]["h"]


def _page_top_on_top_line(view, page: int) -> float:
    """How far the page's top edge is from the top line, in pixels — 0 when it sits on it."""
    return view._pages[page]["y"] - view.mapToScene(QPoint(0, 0)).y() - _PAGE_GAP


def _wheel(view, pos: QPoint, units: int) -> None:
    pt = QPointF(pos)
    delta = QPoint(0, units)
    view.wheelEvent(QWheelEvent(pt, view.viewport().mapToGlobal(pt), delta, delta,
                                Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
                                Qt.ScrollPhase.NoScrollPhase, False))


# ---- #357: the zoom buttons return to where you were --------------------------------------------


@pytest.mark.parametrize("page", [0, 11], ids=["first page", "last page"])
def test_zooming_out_and_back_in_returns_to_the_same_spot(qapp, make_view, letters, page):
    """The #357 repro: Fit Page, then all the way in and out, twice. Every return to the largest
    zoom holds the same spot as the first, and the page marked current never moves.

    900 px tall, the report's own height: at 25% the page next to the one being read is then
    whole on screen too, so the page marked current is a tie that the page read has to win."""
    view = make_view(letters, 800, 900, page)
    view.fit_page()
    qapp.processEvents()

    _zoom_all_the_way(qapp, view, view.zoom_in)
    assert view.zoom == pytest.approx(_MAX_ZOOM)
    first = _under_centre(view)
    assert first[0] == page

    for _ in range(2):
        _zoom_all_the_way(qapp, view, view.zoom_out)
        assert view.zoom == pytest.approx(_MIN_ZOOM)
        # The precondition: the window is stopped at an end, with another page in its middle.
        assert _under_centre(view)[0] != page
        assert view.current_page == page

        _zoom_all_the_way(qapp, view, view.zoom_in)
        back = _under_centre(view)
        assert back[0] == page
        assert back[1] == pytest.approx(first[1], abs=0.001)
        assert back[2] == pytest.approx(first[2], abs=0.001)
        assert view.current_page == page


def test_the_buttons_return_to_the_same_side_of_the_page(qapp, make_view, letters):
    """Sideways is the same mechanism: zoomed in on the left of the page, zoomed out until the page
    is narrower than the window and centred in it, and back in — the left of the page again."""
    view = make_view(letters, 800, 700, 0)
    _zoom_all_the_way(qapp, view, view.zoom_in)
    view.horizontalScrollBar().setValue(view.horizontalScrollBar().minimum())
    qapp.processEvents()
    first = _under_centre(view)
    assert first[1] < 0.2

    _zoom_all_the_way(qapp, view, view.zoom_out)
    _zoom_all_the_way(qapp, view, view.zoom_in)
    assert _under_centre(view)[1] == pytest.approx(first[1], abs=0.001)


def test_scrolling_after_zooming_out_starts_the_next_zoom_from_the_screen(qapp, make_view, letters):
    """The remembered spot lasts only until the reader moves the view. After a scroll, what the
    window shows is where they are, and the next zoom holds that."""
    view = make_view(letters, 800, 700, 0)
    _zoom_all_the_way(qapp, view, view.zoom_out)
    vbar = view.verticalScrollBar()
    vbar.setValue(vbar.value() + 700)
    qapp.processEvents()
    here = _under_centre(view)
    assert here[0] >= 2

    view.zoom_in()
    qapp.processEvents()
    after = _under_centre(view)
    assert after[0] == here[0]
    assert after[2] == pytest.approx(here[2], abs=0.01)


def test_ctrl_wheel_zooms_on_what_is_under_the_mouse(qapp, make_view, letters):
    """Owner's call (2026-09-21): the wheel does not return to where you were. Zoomed out on page 1
    until page 2 is under the mouse, zooming back in with the wheel goes to page 2, whether or not
    the mouse moved — the reader can see what they are pointing at."""
    view = make_view(letters, 800, 700, 0)
    pos = view.viewport().rect().center()
    assert view._anchor_at(pos)[0] == 0
    for _ in range(12):
        _wheel(view, pos, -_WHEEL_NOTCH)
        qapp.processEvents()
    under_mouse = view._anchor_at(pos)
    assert under_mouse[0] == 1

    _wheel(view, pos, _WHEEL_NOTCH)
    qapp.processEvents()
    after = view._anchor_at(pos)
    assert after[0] == 1
    assert after[2] == pytest.approx(under_mouse[2], abs=0.01)


# ---- #359 and the owner's rule: a resize with a fit on keeps the top line ----------------------


def test_narrowing_a_fit_width_window_keeps_the_page(qapp, make_view, report):
    """The #359 repro: page 10 of 23 at Fit Width, narrowed until every page fits, then widened.
    Page 10 stays current the whole way, and comes back with its top on the top line."""
    view = make_view(report, 800, 700, 9)
    view.fit_width()
    view.goto_page(9)
    qapp.processEvents()

    for width in list(range(790, 39, -10)) + list(range(50, 801, 10)):
        view.resize(width, 700)
        qapp.processEvents()
        assert view.current_page == 9, f"lost page 10 at width {width}"
    assert _page_top_on_top_line(view, 9) == pytest.approx(0, abs=1)


@pytest.mark.parametrize("fit", ["width", "page"])
def test_a_resize_keeps_the_top_line_where_it_is(qapp, make_view, report, fit):
    """Owner's rule: narrowing zooms out and shows more below the top line; widening zooms in and
    shows less; the line itself stays put. Before M151 every resize jumped to the top of the page
    that filled most of the window."""
    view = make_view(report, 800, 700, 9)
    view.fit_width() if fit == "width" else view.fit_page()
    p = view._pages[9]
    view.verticalScrollBar().setValue(round(p["y"] + 0.4 * p["h"]))
    qapp.processEvents()
    page, down = _top_line(view)
    fraction = down / view._pages[page]["h"]

    for width in (700, 600, 750, 900, 800):
        before = view.zoom
        view.resize(width, 700 if fit == "width" else width * 7 // 8)
        qapp.processEvents()
        assert view.zoom != before
        now_page, now_down = _top_line(view)
        assert now_page == page
        assert now_down == pytest.approx(fraction * view._pages[page]["h"], abs=1)


def test_a_taller_window_at_the_end_gives_the_top_line_back(qapp, make_view, report):
    """At the end of the document a taller window has to show more above: there is nothing more
    below. Made shorter again, the line that was at the top is back at the top."""
    view = make_view(report, 800, 700, 22)
    view.fit_width()
    vbar = view.verticalScrollBar()
    vbar.setValue(vbar.maximum())
    qapp.processEvents()
    before = _top_line(view)

    view.resize(800, 1000)
    qapp.processEvents()
    assert _top_line(view) != before           # the precondition: the end pushed the line down
    view.resize(800, 700)
    qapp.processEvents()
    after = _top_line(view)
    assert after[0] == before[0]
    assert after[1] == pytest.approx(before[1], abs=1)


def test_opening_the_sidebar_keeps_the_top_line(qapp, report, tmp_path):
    """The resize the owner named: the sidebar takes width from the page area, and the top line
    stays where it is."""
    qapp.settings = Settings(tmp_path / "vs.json")
    win = qapp.open_document(report)
    try:
        win.resize(1100, 800)
        win.show()
        win.pages_dock.setVisible(False)
        qapp.processEvents()
        view = win.view
        view.fit_width()
        p = view._pages[9]
        view.verticalScrollBar().setValue(round(p["y"] + 0.4 * p["h"]))
        qapp.processEvents()
        page, down = _top_line(view)
        fraction = down / view._pages[page]["h"]
        before_zoom = view.zoom

        win.pages_dock.toggleViewAction().trigger()
        qapp.processEvents()
        assert win.pages_dock.isVisible()
        assert view.zoom < before_zoom             # the page area really did get narrower
        now_page, now_down = _top_line(view)
        assert now_page == page
        assert now_down == pytest.approx(fraction * view._pages[page]["h"], abs=1)
    finally:
        win.undo_stack.setClean()
        win.close()


# ---- the page marked current is the page the view was sent to ---------------------------------


def test_the_last_slide_reopens_as_the_current_page(qapp, make_view, slides):
    """Reopened on the last slide at Fit Page, the window holds the last two slides whole. The tie
    used to go to the earlier one, so the page counter and the sidebar said slide 7."""
    view = make_view(slides, 700, 1000, 7)
    assert view._visible_range()[0] <= 6       # the precondition: slide 7 is on screen too
    assert view.current_page == 7


def test_a_search_hit_on_the_last_slide_marks_that_slide_current(qapp, make_view, slides):
    view = make_view(slides, 700, 1000, 0)
    view.ensure_box_visible(7, (100, 500, 400, 560))
    qapp.processEvents()
    assert view._visible_range()[0] <= 6
    assert view.current_page == 7


def test_scrolling_away_from_a_stopped_view_uses_the_screen_again(qapp, make_view, slides):
    """The preference for the page the view was sent to lasts until the reader scrolls. Scrolled
    up so two slides are whole again, the earlier one is current, as it always was (M85)."""
    view = make_view(slides, 700, 1000, 7)
    vbar = view.verticalScrollBar()
    p = view._pages[5]
    vbar.setValue(round(p["y"]) - _PAGE_GAP)
    qapp.processEvents()
    assert view.current_page == 5
