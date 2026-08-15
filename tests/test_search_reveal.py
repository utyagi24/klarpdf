"""Where a revealed search hit ends up on screen. Offscreen GUI.

`ensureVisible` scrolls the **minimum** distance, so it parks a hit hard against whichever edge the
view travelled towards: stepping forward through results left every match ~60 px above the bottom of
the window, stepping back left it ~60 px below the top. Same call, same margin — the asymmetry is
the direction of travel, and it is why Previous felt right while Next felt like it had not scrolled
far enough (owner-reported 2026-08-13; measured over 14 consecutive presses each way on a 320-page
prospectus, bottom margin 59.6–60.4 px on every single Next).

Nothing caught it because the one assertion that touched the reveal used `intersects`, which a hit
one pixel inside the edge satisfies. These tests assert **placement**, not mere presence: that is
the whole difference between "technically on screen" and "revealed".
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from app import PdfApp
from main_window import MainWindow
from store.settings import Settings

NEEDLE = "FINDME"


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    return qapp


@pytest.fixture
def spread_pdf(tmp_path) -> str:
    """Six tall pages carrying one hit each, low down the page, so every step must scroll.

    Plus three trailing blank pages. Centring is clamped by the scroll range, so a hit in the last
    screenful of a document legitimately cannot be centred — the tail keeps the stepped hits away
    from that boundary, which is asserted separately.
    """
    path = str(tmp_path / "spread.pdf")
    doc = fitz.open()
    for i in range(6):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 700), f"page {i} {NEEDLE} here", fontsize=14)
    for _ in range(3):
        doc.new_page(width=612, height=792)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def pair_pdf(tmp_path) -> str:
    """Two hits a couple of lines apart, mid-page — a step between them needs no scrolling."""
    path = str(tmp_path / "pair.pdf")
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 380), f"first {NEEDLE} here", fontsize=14)
    page.insert_text((72, 410), f"second {NEEDLE} here", fontsize=14)
    doc.save(path)
    doc.close()
    return path


def _win(app, path):
    w = MainWindow(app, path, app.settings)
    w.resize(900, 800)
    w.show()
    app.processEvents()
    return w


def _placement(win) -> float:
    """How far the current hit sits from the middle of the window, as a fraction of its height.

    0.0 is dead centre, ±0.5 is hard against an edge. A fraction rather than pixels so the
    assertion says what it means at any window size — the bug was invisible at small ones.
    """
    view = win.view
    idx, _total = view.search.position()
    page_index, boxes, _snippet = view.search.hits()[idx]
    rect = view.scene_rect_for_box(page_index, boxes[0])
    visible = view.mapToScene(view.viewport().rect()).boundingRect()
    return (rect.center().y() - visible.center().y()) / visible.height()


def _fully_visible(win) -> bool:
    view = win.view
    idx, _total = view.search.position()
    page_index, boxes, _snippet = view.search.hits()[idx]
    visible = view.mapToScene(view.viewport().rect()).boundingRect()
    return all(visible.contains(view.scene_rect_for_box(page_index, box)) for box in boxes)


def test_stepping_forward_does_not_park_the_hit_at_the_bottom(app, spread_pdf):
    """The regression. Every Next used to land at +0.45-ish — the bottom edge of the window."""
    win = _win(app, spread_pdf)
    try:
        assert win.view.search.search(NEEDLE) == 6
        for _ in range(5):
            win.view.search.next()
            app.processEvents()
            assert _fully_visible(win)
            assert abs(_placement(win)) < 0.25, "hit is not near the middle of the window"
    finally:
        win.undo_stack.setClean()
        win.close()


def test_forward_and_back_leave_the_hit_in_the_same_place(app, spread_pdf):
    """Direction of travel must not decide where a match lands — that asymmetry *was* the bug."""
    win = _win(app, spread_pdf)
    try:
        win.view.search.search(NEEDLE)
        forward = []
        for _ in range(4):
            win.view.search.next()
            app.processEvents()
            forward.append(_placement(win))
        backward = []
        for _ in range(3):
            win.view.search.prev()
            app.processEvents()
            backward.append(_placement(win))
        assert max(forward) - min(backward) < 0.2, (
            f"forward {forward} and backward {backward} land in different parts of the window"
        )
    finally:
        win.undo_stack.setClean()
        win.close()


def test_a_hit_already_in_view_does_not_move_the_page(app, pair_pdf):
    """Revealing is not re-centring: stepping between two matches on the same screen must leave the
    page still, or reading a dense page turns into a slideshow."""
    win = _win(app, pair_pdf)
    try:
        assert win.view.search.search(NEEDLE) == 2
        app.processEvents()
        before = win.view.verticalScrollBar().value()
        win.view.search.next()
        app.processEvents()
        assert win.view.verticalScrollBar().value() == before
        assert _fully_visible(win)
    finally:
        win.undo_stack.setClean()
        win.close()


def test_a_hit_on_the_first_page_is_still_fully_visible(app, spread_pdf):
    """Centring is clamped by the scroll range, so a hit near either end of the document lands as
    close to the middle as the document allows — and must never be pushed off the edge instead."""
    win = _win(app, spread_pdf)
    try:
        win.view.search.search(NEEDLE)
        win.view.search.goto(0)
        app.processEvents()
        assert _fully_visible(win)
        win.view.search.goto(5)                 # …and the same at the far end
        app.processEvents()
        assert _fully_visible(win)
    finally:
        win.undo_stack.setClean()
        win.close()
