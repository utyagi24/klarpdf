"""Two owner-reported papercuts found while verifying M86, plus the design decision behind a
third report that turned out not to be a bug. Offscreen GUI.

* **Ctrl+= zoom-in alias (M89.4, pulled out of M89 and shipped early).** "Zoom with Ctrl+- is
  working but not with Ctrl++". Qt's `StandardKey.ZoomIn` resolves to `Ctrl++`, and on a US layout
  `+` *is* Shift+`=`, so that sequence physically demands Ctrl+Shift+= and plain Ctrl+= matched
  nothing. `Ctrl+-` is unshifted, which is why Zoom Out worked and Zoom In looked dead.
* **The open marker.** "Reopening the document lands me at the last page but in the thumbnail bar
  the page is not selected". The panel's current row starts at -1 and opening restores a page
  without *changing* it, so `currentPageChanged` never fired and nothing was ever marked.
* **Zoom is not restored on reopen — by design**, not a bug: v0.9.1 (PR #61) made documents open at
  Fit Page because a remembered magnification kept reopening them too large for the window. Pinned
  here so the next reader does not "fix" it back.
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QKeySequence

from app import PdfApp
from store.settings import Settings


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def settings(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "view_state.json")
    return qapp.settings


def _menu_action(win, text):
    """Actions are added to their menu, not to the window, so walk the menu bar."""
    for menu_action in win.menuBar().actions():
        menu = menu_action.menu()
        if menu is None:
            continue
        for action in menu.actions():
            if action.text() == text:
                return action
    raise AssertionError(f"no menu action named {text!r}")


def _zoom_in_action(win):
    return _menu_action(win, "Zoom In")


# ---- M89.4: the Ctrl+= alias -------------------------------------------------


def test_ctrl_equals_is_bound_to_zoom_in(qapp, a_pdf, settings):
    """The reported gap. `Ctrl+=` must be a live binding, not just `Ctrl++`."""
    win = qapp.open_document(a_pdf)
    qapp.processEvents()
    bindings = [s.toString() for s in _zoom_in_action(win).shortcuts()]
    assert "Ctrl+=" in bindings
    win.close()


def test_the_standard_accelerator_survives_the_alias(qapp, a_pdf, settings):
    """`Ctrl++` keeps working and stays *first*, so the menu row still shows the standard
    accelerator rather than advertising the alias."""
    win = qapp.open_document(a_pdf)
    qapp.processEvents()
    action = _zoom_in_action(win)
    bindings = [s.toString() for s in action.shortcuts()]
    assert bindings[0] == QKeySequence(QKeySequence.StandardKey.ZoomIn).toString()
    assert len(bindings) > 1
    win.close()


def test_ctrl_equals_actually_zooms(qapp, a_pdf, settings):
    """Bound *and* wired: triggering the action zooms in one step."""
    win = qapp.open_document(a_pdf)
    qapp.processEvents()
    before = win.view.zoom
    _zoom_in_action(win).trigger()
    qapp.processEvents()
    assert win.view.zoom > before
    win.close()


def test_zoom_out_needs_no_alias(qapp, a_pdf, settings):
    """`Ctrl+-` is an unshifted key, so Zoom Out was never broken — no alias, nothing to regress."""
    win = qapp.open_document(a_pdf)
    qapp.processEvents()
    action = _menu_action(win, "Zoom Out")
    assert [s.toString() for s in action.shortcuts()] == ["Ctrl+-"]
    win.close()


# ---- the sidebar's you-are-here marker on open -------------------------------


def test_opening_marks_the_current_page_in_the_sidebar(qapp, a_pdf, settings):
    """A freshly opened document marks page 1 — current *and* selected, exactly as a click leaves
    it. Before this, the sidebar opened with nothing marked at all."""
    win = qapp.open_document(a_pdf)
    qapp.processEvents()
    assert win.view.current_page == 0
    assert win.thumbs.currentRow() == 0
    assert [i.row() for i in win.thumbs.selectedIndexes()] == [0]
    win.close()


def test_reopening_restores_the_remembered_page(qapp, a_pdf, settings):
    """The defect underneath the marker report — long-standing, and nothing covered it.

    `open_at` passed `self._current` to `goto_page`, but `_build_scene` renders at the end of its
    rebuild and that render re-derives the current page from a viewport still scrolled to the top,
    resetting the field to 0. So the remembered page was read, stored, immediately overwritten, and
    the view opened on page 1 every time. `open_at` now carries the page in a local across the
    rebuild, the way `rotate_view` and `set_page_layout` already do.
    """
    win = qapp.open_document(a_pdf)
    qapp.processEvents()
    win.view.goto_page(2)
    qapp.processEvents()
    win.undo_stack.setClean()
    win.close()
    assert settings.get_doc_state(a_pdf)["page"] == 2      # saved correctly all along

    again = qapp.open_document(a_pdf)
    qapp.processEvents()
    assert again.view.current_page == 2                    # ... and now actually restored
    again.close()


def test_reopening_marks_the_remembered_page(qapp, a_pdf, settings):
    """The owner's report: reopen lands on the remembered page, and the sidebar now says so."""
    win = qapp.open_document(a_pdf)
    qapp.processEvents()
    win.view.goto_page(2)
    qapp.processEvents()
    assert win.view.current_page == 2
    win.undo_stack.setClean()
    win.close()                                   # closeEvent persists {page, rotation, zoom}

    again = qapp.open_document(a_pdf)
    qapp.processEvents()
    assert again.view.current_page == 2           # the page itself was already restored
    assert again.thumbs.currentRow() == 2         # ... and now the marker follows it
    assert [i.row() for i in again.thumbs.selectedIndexes()] == [2]
    again.close()


def test_the_open_marker_does_not_scroll_the_view(qapp, a_pdf, settings):
    """Seeding the marker must not feed back into the view — `pageActivated` would re-issue a
    `goto_page` and could undo the position `open_at` just restored."""
    win = qapp.open_document(a_pdf)
    qapp.processEvents()
    win.view.goto_page(2)
    qapp.processEvents()
    win.undo_stack.setClean()
    win.close()

    again = qapp.open_document(a_pdf)
    qapp.processEvents()
    jumps: list[int] = []
    again.thumbs.pageActivated.connect(jumps.append)
    again.thumbs.mark_open_page(again.view.current_page)   # idempotent, and silent
    assert jumps == []
    assert again.thumbs.currentRow() == 2
    again.close()


# ---- the decision behind the third report ------------------------------------


def test_zoom_is_saved_but_a_document_opens_at_fit_page(qapp, a_pdf, settings):
    """Not a bug (v0.9.1, PR #61). The magnification is written to the state file — kept as a seam
    for a future "restore my zoom" — but a document opens at **Fit Page**, because a remembered
    zoom kept reopening documents too large for the window."""
    win = qapp.open_document(a_pdf)
    qapp.processEvents()
    win.view.set_zoom(3.0)
    qapp.processEvents()
    win.undo_stack.setClean()
    win.close()

    assert settings.get_doc_state(a_pdf)["zoom"] == pytest.approx(3.0)   # saved...

    again = qapp.open_document(a_pdf)
    qapp.processEvents()
    assert again.view.zoom != pytest.approx(3.0)                        # ...but not restored
    assert again.view._fit_mode == "page"                               # it opened at Fit Page
    _, page_h = again.view._natural_size(again.view.current_page)
    assert page_h * again.view.zoom <= again.view.viewport().height() + 2
    again.close()


def test_page_and_rotation_do_resume(qapp, a_pdf, settings):
    """The other half of the same decision: page and rotation are what resume."""
    win = qapp.open_document(a_pdf)
    qapp.processEvents()
    win.view.goto_page(1)
    win.view.rotate_view(90)
    qapp.processEvents()
    win.undo_stack.setClean()
    win.close()

    again = qapp.open_document(a_pdf)
    qapp.processEvents()
    assert again.view.current_page == 1
    assert again.view.rotation == 90
    again.close()
