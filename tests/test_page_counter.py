"""The reading bar's page counter (PLAN.md §M91, M91.3). Offscreen GUI.

``[ 10 ] of 320`` — the field is the reader's position and a way to change it; the label is the
document's length. The view is the single source of truth, exactly as it is for ``ZoomWidget``: every
way of moving through the document drives the field, and the field's only power is to call
``goto_page``.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
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
    """Type into the field and commit, the way Enter or a click-away does.

    **Really typed** (`keyClicks`), not `setText`. Since M91.4 a commit only applies when the
    *reader* changed the value — Qt's `isModified` flag is the question asked, and `setText` clears
    it. The old helper set the text behind the flag's back, which is exactly why it could not have
    caught the defect that rule fixes: an *unmodified* commit re-seating the view.
    """
    field = win.page_widget.field
    field.selectAll()               # ... so the typing replaces what is there, as a reader's does
    QTest.keyClicks(field, text)
    field.editingFinished.emit()


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
    """Emptying the field and committing restores the live page. (Letters cannot get in at all —
    the validator refuses them keystroke by keystroke — so this is the reachable half of "garbage".)
    """
    win.view.goto_page(1)
    field = win.page_widget.field
    field.selectAll()
    QTest.keyClick(field, Qt.Key.Key_Delete)
    field.editingFinished.emit()
    assert field.text() == "2"
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


# ---- M91.4: the field must not fight the reader ------------------------------


def test_clicking_the_field_and_away_does_not_move_the_view(win):
    """**The owner's "the first page flickers but stays at 1".**

    `editingFinished` fires on *every* focus-out with valid contents — Qt does not require the text
    to have changed — so merely clicking the field and clicking away re-applied the number it was
    showing. Not the no-op it looks like: `goto_page` re-seats the view on that page's **top**, so a
    reader who clicked the field, read on, and clicked back onto the page was yanked backwards.
    """
    field = win.page_widget.field
    win.view.verticalScrollBar().setValue(300)     # part-way down page 1; still page 1
    PdfApp.instance().processEvents()
    field.setFocus(Qt.FocusReason.MouseFocusReason)
    PdfApp.instance().processEvents()
    assert field.hasFocus() and field.text() == "1"
    win.view.setFocus()                            # click back on the page → focus-out → commit
    PdfApp.instance().processEvents()
    assert win.view.verticalScrollBar().value() == 300


def test_space_in_the_field_pages_the_document(win):
    """The other half of the same report. The field is integer-validated, so `Space` can never be
    valid input — but `QLineEdit` accepted the key and the validator dropped the character, leaving
    the reader's commonest gesture dead for as long as the field held focus."""
    field, vbar = win.page_widget.field, win.view.verticalScrollBar()
    field.setFocus(Qt.FocusReason.MouseFocusReason)
    PdfApp.instance().processEvents()
    before = vbar.value()
    QTest.keyClick(field, Qt.Key.Key_Space)
    PdfApp.instance().processEvents()
    assert vbar.value() > before
    assert win.view.current_page == 1
    assert field.text() == "2"                     # ... and the readout followed


def test_enter_hands_the_keyboard_back_to_the_page(win):
    """A page field is a one-shot instruction, not a place to leave the focus — otherwise typing a
    page number costs the reader every reading key until they remember to click the document."""
    field = win.page_widget.field
    field.setFocus(Qt.FocusReason.MouseFocusReason)
    PdfApp.instance().processEvents()
    field.selectAll()
    QTest.keyClicks(field, "3")
    assert field.hasFocus()
    QTest.keyClick(field, Qt.Key.Key_Return)
    PdfApp.instance().processEvents()
    assert win.view.current_page == 2
    assert win.view.hasFocus() and not field.hasFocus()


def test_a_typed_page_still_jumps_to_the_page_you_are_on(win):
    """The `isModified` guard must not cost the deliberate case: typing the page you are already
    reading is a request to go back to its **top**, the same thing clicking its thumbnail means."""
    win.view.goto_page(1)
    win.view.verticalScrollBar().setValue(win.view.verticalScrollBar().value() + 60)
    PdfApp.instance().processEvents()
    moved = win.view.verticalScrollBar().value()
    _type(win, "2")
    assert win.view.current_page == 1
    assert win.view.verticalScrollBar().value() < moved      # re-seated on the page's top


def test_the_counter_shows_the_restored_page_when_a_document_reopens(qapp, a_pdf, tmp_path):
    """**The owner's second report**: reopen a document closed on page 10 and the view is on page 10
    while the counter reads 1.

    `open_at` assigns `_current` directly — the fit has to be sized against that page's row before a
    scene exists to derive it from — so `_update_current` found the page it already held and stayed
    silent. The sidebar had a private workaround (`mark_open_page`); the counter did not, and nor
    would the next indicator. Announced at the source now.
    """
    qapp.settings = Settings(tmp_path / "reopen.json")
    first = qapp.open_document(a_pdf)
    first.resize(1100, 700)
    first.show()
    qapp.processEvents()
    first.view.goto_page(2)
    assert first.page_widget.field.text() == "3"
    first.undo_stack.setClean()
    first.close()
    qapp.processEvents()

    again = qapp.open_document(a_pdf)
    again.resize(1100, 700)
    again.show()
    qapp.processEvents()
    try:
        assert again.view.current_page == 2                  # the view resumed…
        assert again.page_widget.field.text() == "3"         # …and said so
    finally:
        again.undo_stack.setClean()
        again.close()


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
    assert counter.geometry().right() < bar.width(), "the counter ran off the bar"
    # Owner placement (2026-07-30): its own group **after** the zoom/fit cluster and before rotate.
    assert counter.geometry().left() >= zoom.geometry().right()
    rotate = bar.widgetForAction(win._a_rotl)
    assert rotate is not None and rotate.geometry().left() >= counter.geometry().right()
    assert rotate.geometry().right() < bar.width(), "rotate was pushed off the bar"


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
