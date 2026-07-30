"""The reading bar's page counter (PLAN.md §M91, M91.3). Offscreen GUI.

``[ 10 ] of 320`` — the field is the reader's position and a way to change it; the label is the
document's length. The view is the single source of truth, exactly as it is for ``ZoomWidget``: every
way of moving through the document drives the field, and the field's only power is to call
``goto_page``.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QToolBar

from app import PdfApp
from model.edit_commands import DeleteCommand
from store.settings import Settings


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def win(qapp, a_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    w = qapp.open_document(a_pdf)          # 3 pages
    w.resize(1100, 700)
    w.show()
    qapp.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def _type(win, text: str) -> None:
    """Type into the field and commit, the way Enter or a click-away does."""
    win.page_widget.field.setText(text)
    win.page_widget.field.editingFinished.emit()


# ---- the view drives the field ------------------------------------------------


def test_the_counter_opens_on_page_one_of_the_count(win):
    """The reason this milestone exists: `sidebar_visible` defaults to False, so before M91.3 a
    freshly opened document told the reader nothing at all about where they were."""
    assert win.page_widget.field.text() == "1"
    assert win.page_widget.total.text() == "of 3"


def test_moving_through_the_document_updates_the_field(win):
    """Bound to `currentPageChanged`, so it does not matter *how* the page changed — the wheel,
    PgUp/PgDn, Home/End (M89.1), the sidebar, the outline and Ctrl+G all arrive here."""
    win.view.goto_page(2)
    assert win.page_widget.field.text() == "3"
    win.view.goto_page(0)
    assert win.page_widget.field.text() == "1"


def test_the_field_shows_one_page_in_two_page_mode(win):
    """A recorded non-goal, pinned: Two-Page shows the **current** page as the rest of the app
    already defines it (M85 — largest visible area), not a `2–3` span, so the field, the sidebar
    highlight and the outline tab cannot disagree."""
    win.view.set_page_layout("two")
    win.view.goto_page(1)
    assert win.page_widget.field.text() == str(win.view.current_page + 1)
    assert "–" not in win.page_widget.field.text()


# ---- the field drives the view ------------------------------------------------


def test_typing_a_page_jumps_to_it(win):
    _type(win, "3")
    assert win.view.current_page == 2
    assert win.page_widget.field.text() == "3"


def test_an_out_of_range_page_clamps_and_echoes_the_clamped_value(win):
    """Typing 900 into a 3-page document must not leave 900 on screen — the field is a readout as
    well as an input, and a readout that disagrees with the view is worse than no readout."""
    _type(win, "900")
    assert win.view.current_page == 2
    assert win.page_widget.field.text() == "3"
    _type(win, "0")
    assert win.view.current_page == 0
    assert win.page_widget.field.text() == "1"


def test_garbage_restores_the_live_value(win):
    win.view.goto_page(1)
    _type(win, "")
    assert win.page_widget.field.text() == "2"
    assert win.view.current_page == 1


# ---- the total tracks the document -------------------------------------------


def test_deleting_pages_updates_the_total_and_undo_restores_it(win):
    """The total is pushed from `_on_doc_changed`, not signalled: there is no `pageCountChanged`,
    and a delete changes the count **without** moving the current page — so a total bound to
    `currentPageChanged` would have sat there reading `of 3` after the delete."""
    win.undo_stack.push(DeleteCommand(win.vdoc, [2]))
    assert win.page_widget.total.text() == "of 2"
    win.undo_stack.undo()
    assert win.page_widget.total.text() == "of 3"


# ---- it must not cost the reader anything ------------------------------------


def test_the_field_never_takes_focus_from_the_page(win):
    """ClickFocus, like the zoom combo. The arrow keys are navigation (M78.2) and Space pages
    (M89.2): a field that grabbed focus on a wheel scroll or a Tab pass would swallow them."""
    assert win.page_widget.field.focusPolicy() == Qt.FocusPolicy.ClickFocus
    win.view.setFocus()
    win.page_widget.setFocus()          # what a Tab pass or a wheel-scroll hover amounts to
    assert not win.page_widget.field.hasFocus()
    assert win.page_widget.focusPolicy() != Qt.FocusPolicy.StrongFocus


def test_the_counter_does_not_stretch_across_the_bar(win):
    """Regression, measured: a plain `QWidget` handed to `QToolBar.addWidget` gets a Preferred size
    policy and the toolbar hands it every spare pixel — this one stretched to **627 px** in an
    1100 px window and pushed the whole zoom cluster off the right-hand end. `ZoomWidget` never
    showed it because it fixes its own width."""
    counter = win.page_widget
    assert counter.width() <= counter.sizeHint().width() + 2
    bar = win.findChildren(QToolBar)[0]
    zoom = win.zoom_widget
    assert zoom.isVisible()
    assert zoom.geometry().right() < bar.width(), "the zoom cluster was pushed off the bar"
    assert counter.geometry().right() <= zoom.geometry().left()  # its own group, before zoom


def test_full_screen_carries_no_counter(win):
    """A recorded non-goal: M78 made Full Screen and Slideshow deliberately chrome-free. It needs no
    code of its own — the whole reading bar is hidden — but it needs pinning, because the next
    person to add a floating readout will not know that."""
    win._enter_chromeless(slideshow=False)
    try:
        assert not win._main_toolbar.isVisible()
        assert not win.page_widget.isVisible()
    finally:
        win._exit_chromeless()
    assert win.page_widget.isVisible()
