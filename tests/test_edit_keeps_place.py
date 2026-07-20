"""Editing a page keeps your place, and moves the current page onto it (M59.9).

Reported: with page 2 current, scroll a little so page 3 shows, mark up page 3 — and the view
jumped back to page 2. ``reload()`` ended with ``goto_page(self._current)``, snapping the scrollbar
to the current page's top after *every* edit; and "current" is whichever page owns the viewport
centre, which needn't be the page you just edited.

Now a content-only edit (annotation / form fill) leaves the page geometry alone, so the scroll
offset is kept as-is, and the edited page becomes current *without* scrolling. Structural edits
(insert / delete / reorder) still anchor to the current page — their layout genuinely moved.
"""

from __future__ import annotations

import pytest

from app import PdfApp
from main_window import MainWindow
from model.page_edits import Highlight, Shape
from store.settings import Settings


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    qapp.object_clipboard = None
    return qapp


@pytest.fixture
def win(app, a_pdf):
    w = MainWindow(app, a_pdf, app.settings)
    w.resize(700, 500)
    w.show()                      # offscreen; needed for a real viewport + scrollbar range
    yield w
    w.undo_stack.setClean()
    w.close()


def _scroll_to(win, value: int) -> int:
    bar = win.view.verticalScrollBar()
    bar.setValue(value)
    return bar.value()


def test_annotating_a_page_does_not_move_the_scroll(win):
    """The reported bug, straight through: park the scroll mid-document, mark up a page, and the
    viewport must not budge."""
    target = _scroll_to(win, win.view.verticalScrollBar().maximum() // 2)
    win.vdoc.add_annotation(1, Highlight(((100.0, 100.0, 200.0, 114.0),)))
    win._add_annotation(2, Shape("rect", (100.0, 300.0, 200.0, 360.0)))
    assert win.view.verticalScrollBar().value() == target      # stayed exactly put


def test_the_edited_page_becomes_current(win):
    _scroll_to(win, 0)
    assert win.view.current_page == 0
    win._add_annotation(2, Shape("rect", (100.0, 300.0, 200.0, 360.0)))
    assert win.view.current_page == 2          # follows the edit, not the viewport centre


def test_making_a_page_current_does_not_scroll(win):
    """Following the edit must not itself become a jump."""
    target = _scroll_to(win, 0)
    win._add_annotation(2, Shape("rect", (100.0, 300.0, 200.0, 360.0)))
    assert win.view.current_page == 2
    assert win.view.verticalScrollBar().value() == target


def test_undo_leaves_the_current_page_alone(win):
    """The marker is consumed once, so an undo (which records no page) doesn't yank the sidebar."""
    _scroll_to(win, 0)
    win._add_annotation(2, Shape("rect", (100.0, 300.0, 200.0, 360.0)))
    assert win.view.current_page == 2
    win.view.set_current_page(0)
    win.undo_stack.undo()
    assert win.view.current_page == 0          # undo recorded nothing → left where it was


def test_a_structural_edit_still_anchors_to_the_current_page(win):
    """Deleting a page remaps the layout, so the old scroll offset is meaningless — that case
    keeps the current-page anchor rather than preserving a now-bogus offset."""
    _scroll_to(win, win.view.verticalScrollBar().maximum())
    before = win.vdoc.page_count
    win._delete_rows([0])
    assert win.vdoc.page_count == before - 1
    page = win.view._pages[win.view.current_page]
    assert win.view.verticalScrollBar().value() == pytest.approx(int(page["y"]) - 8, abs=40)


def test_set_current_page_ignores_out_of_range(win):
    win.view.set_current_page(999)
    assert win.view.current_page < win.vdoc.page_count
