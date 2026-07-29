"""Menu-bar structure (R1 polish, owner-decided during the stack review). Offscreen GUI.

The Tools menu is the tranche's one budgeted top-level menu (PLAN.md §GUI feature roadmap, UI
budget): interaction *modes* live there — Select/Grab and the armed one-shots — while Rotate
moved to Edit beside the other page operations (it is a real, saved edit; its old View placement
read as a view-only spin), and View keeps only what never touches the file.
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
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    for w in list(qapp._windows.values()):
        w.close()
    qapp._windows.clear()
    yield qapp
    for w in list(qapp._windows.values()):
        w.undo_stack.setClean()
        w.close()
    qapp._windows.clear()


def _titles(win, menu_title: str) -> list[str]:
    """Action texts of the top-level menu named ``menu_title`` (separators skipped).

    Resolved and read in one scope on purpose: letting the ``menuBar().actions()`` wrappers be
    garbage-collected while holding only the ``a.menu()`` wrapper lets shiboken tear down the
    C++ QMenu underneath it ("Internal C++ object already deleted")."""
    for bar_action in win.menuBar().actions():
        if bar_action.text() == menu_title and bar_action.menu() is not None:
            return [a.text() for a in bar_action.menu().actions()
                    if not a.isSeparator() and a.text()]
    raise AssertionError(f"no top-level menu titled {menu_title!r}")


def test_menu_bar_order(app, b_pdf):
    win = app.open_document(b_pdf)
    titles = [a.text() for a in win.menuBar().actions() if a.menu() is not None]
    assert titles == ["&File", "&Edit", "&View", "&Tools", "&Help"]


def test_tools_menu_holds_the_modes_and_armed_tools(app, b_pdf):
    win = app.open_document(b_pdf)
    # Grouped to mirror the markup toolbar (modes · draw · text · stamps · redact), then the
    # menu-only one-shots. Draw precedes text markup as it does on the bar; stamps and redact
    # are split rather than run together (the menu has no slot budget forcing them into one).
    assert _titles(win, "&Tools") == [
        "Select", "Grab", "Objects",
        "Pen", "Line", "Rectangle", "Ellipse",  # Arrow merged into Line (M74)
        # Note (M90.1) closes the text-markup group: it is a text-markup verb, and it rides the
        # Markup ▾ dropdown rather than a toolbar slot of its own.
        "Add Text Box", "Highlight", "Underline", "Strike Out", "Note",
        # One entry, not two (M69.3): a watermark is a Stamp with `under=True`, so stamps and
        # watermarks are one dialog with a Place control rather than two menu items.
        "Stamp / Watermark…", "Signature / Image…",
        # One Redact, not the explicit text/block verbs (owner call): the menu offers the same
        # gesture-detecting Redact the markup bar does, plus the Find-and-Redact dialog.
        "Redact", "Find and Redact…",
        "Add Form Field",
        "Crop Pages", "Remove Crop",
    ]


def test_tools_menu_groups_match_the_markup_toolbar(app, b_pdf):
    """The separators fall on the toolbar's group seams, not mid-group. Reading the raw action
    list (separators included) as runs between dividers guards the *grouping*, which the flat
    ``_titles`` sequence above cannot see."""
    win = app.open_document(b_pdf)
    for bar_action in win.menuBar().actions():
        if bar_action.text() == "&Tools" and bar_action.menu() is not None:
            groups, current = [], []
            for a in bar_action.menu().actions():
                if a.isSeparator():
                    groups.append(current)
                    current = []
                elif a.text():
                    current.append(a.text())
            groups.append(current)
            break
    assert groups == [
        ["Select", "Grab", "Objects"],
        ["Pen", "Line", "Rectangle", "Ellipse"],
        ["Add Text Box", "Highlight", "Underline", "Strike Out", "Note"],
        ["Stamp / Watermark…", "Signature / Image…"],
        ["Redact", "Find and Redact…"],
        ["Add Form Field"],
        ["Crop Pages", "Remove Crop"],
    ]


def test_rotate_lives_in_edit_with_the_page_ops(app, b_pdf):
    win = app.open_document(b_pdf)
    edit = _titles(win, "&Edit")
    assert "Rotate Left" in edit and "Rotate Right" in edit
    assert edit.index("Rotate Left") > edit.index("Delete Pages")  # grouped with the page ops
    assert edit.index("Rotate Right") < edit.index("Insert Pages from File…")
    # The shortcuts rode along — muscle memory is untouched by the move.
    assert win._a_rotl.shortcut() == QKeySequence("Ctrl+L")
    assert win._a_rotr.shortcut() == QKeySequence("Ctrl+R")


def test_view_menu_keeps_only_view_state(app, b_pdf):
    win = app.open_document(b_pdf)
    view = _titles(win, "&View")
    assert view == ["Zoom Out", "Zoom In", "Actual Size", "Fit Width", "Fit Page",
                    "Go to &Page…", "Night Reading Mode",
                    "Full Screen", "Slideshow", "Two-Page View",  # M78 — the reading modes
                    "&Sidebar",
                    "&Markup Toolbar"]  # M71 — chrome visibility is view state
