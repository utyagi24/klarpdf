"""Two-tier toolbar (PLAN.md §GUI feature roadmap → R6, M71). Offscreen GUI.

The Preview-inspired split: at rest the window shows only the ~10-slot **reading bar** (sidebar ·
save · undo/redo · zoom cluster · rotate · Markup toggle · find); the whole annotate/draw/redact
kit sits on a second **markup bar** the toggle summons. The R6 budget revision this implements —
*the app at rest is a viewer; the markup kit is chrome you summon on demand* — is asserted here:
resting state, the toggle round-trip, app-wide persistence of an explicit choice, every removed
button's verb still reachable through the menus, and the arm/visibility interplay (arming a kit
tool summons the bar; hiding the bar disarms).
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QToolBar

from app import PdfApp
from main_window import MainWindow
from store.settings import Settings
from viewer.tools import ArmedTool


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    qapp.object_clipboard = []
    return qapp


@pytest.fixture
def win(app, a_pdf):
    w = app.open_document(a_pdf)
    w.resize(1200, 700)
    app.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def _reading_bar(win) -> QToolBar:
    return next(b for b in win.findChildren(QToolBar) if b.windowTitle() == "Main")


def _menu_texts(win, title: str) -> set[str]:
    """Action texts of the top-level menu named ``title`` — resolved and read in one scope
    (the shiboken lifetime trap test_menus._titles documents)."""
    for bar_action in win.menuBar().actions():
        if bar_action.text() == title and bar_action.menu() is not None:
            return {a.text().replace("&", "") for a in bar_action.menu().actions() if a.text()}
    raise AssertionError(f"no top-level menu titled {title!r}")


# ---- the resting state -------------------------------------------------------


def test_at_rest_only_the_reading_bar_shows(win):
    """The R6 bar: a plain open shows the reading bar and no markup kit."""
    assert _reading_bar(win).isVisibleTo(win)
    assert not win.markup_bar.isVisibleTo(win)


def test_reading_bar_holds_the_reading_set_and_nothing_else(win):
    """Slot inventory: Sidebar · Save · Undo/Redo · zoom cluster · Rotate · Markup · Find — and
    none of the removed verbs (Open/Print, the page-op buttons, the kit)."""
    texts = {a.text().replace("&", "") for a in _reading_bar(win).actions() if a.text()}
    assert {"Sidebar", "Save", "Undo", "Redo", "Zoom Out", "Zoom In", "Fit Width",
            "Fit Page", "Rotate Left", "Rotate Right", "Markup Toolbar", "Find…"} <= texts
    for gone in ("Open…", "Print…", "Cut Pages", "Copy Pages", "Paste Pages", "Delete Pages",
                 "Insert Pages from File…", "Select", "Grab", "Highlight", "Add Text Box"):
        assert gone not in texts


def test_markup_bar_carries_the_kit(win):
    """The summoned bar: modes · text box · the split-buttons ride as widgets · one Redact slot
    (M72 — the pair became a gesture-detecting single button)."""
    texts = {a.text().replace("&", "") for a in win.markup_bar.actions() if a.text()}
    assert {"Select", "Grab", "Objects", "Add Text Box", "Redact"} <= texts
    assert "Redact Text" not in texts and "Redact Block" not in texts
    # The Markup ▾ / Draw ▾ / style / Stamp ▾ buttons are widgets on this bar.
    for button in (win._markup_button, win._draw_button, win._markup_style_button,
                   win._stamp_button):
        assert button.parent() is win.markup_bar


# ---- the toggle --------------------------------------------------------------


def test_markup_toggle_summons_and_dismisses_the_kit(app, win):
    toggle = win.markup_bar.toggleViewAction()
    assert not toggle.isChecked()
    toggle.trigger()
    app.processEvents()
    assert win.markup_bar.isVisibleTo(win) and toggle.isChecked()
    toggle.trigger()
    app.processEvents()
    assert not win.markup_bar.isVisibleTo(win) and not toggle.isChecked()


def test_markup_toggle_is_on_the_reading_bar_and_in_the_view_menu(win):
    toggle = win.markup_bar.toggleViewAction()
    assert toggle in _reading_bar(win).actions()
    assert "Markup Toolbar" in _menu_texts(win, "&View")


def test_explicit_choice_is_remembered_app_wide(app, win, a_pdf):
    """An explicit toggle persists (like the sidebar): the next window opens with the kit up."""
    win.markup_bar.toggleViewAction().trigger()
    app.processEvents()
    assert app.settings.get_pref("markup_bar_visible") is True
    other = MainWindow(app, a_pdf, app.settings)
    try:
        assert other.markup_bar.isVisibleTo(other)
    finally:
        other.undo_stack.setClean()
        other.close()


# ---- every removed verb stays reachable (menus are the complete catalog) -----


def test_removed_buttons_verbs_stay_in_the_menus(win):
    file_texts = _menu_texts(win, "&File")
    assert {"Open…", "Print…"} <= file_texts
    edit_texts = _menu_texts(win, "&Edit")
    assert {"Cut Pages", "Copy Pages", "Paste Pages", "Delete Pages",
            "Insert Pages from File…"} <= edit_texts
    tools_texts = _menu_texts(win, "&Tools")
    assert {"Select", "Grab", "Objects", "Highlight", "Redact Text"} <= tools_texts


def test_page_ops_stay_on_the_sidebar_context_menu(win):
    """The owner-decided path for page ops with the buttons gone: the sidebar right-click menu."""
    menu = win._build_page_context_menu([0])
    texts = {a.text() for a in menu.actions() if a.text()}
    assert {"Cut", "Copy", "Paste", "Delete", "Duplicate"} <= texts


# ---- arm/visibility interplay ------------------------------------------------


def test_arming_a_kit_tool_summons_the_hidden_bar(app, win):
    """Tools ▸ Highlight with the kit hidden: the bar appears so the armed button is visible —
    but a programmatic summon is not persisted (only the user's explicit toggle is)."""
    assert not win.markup_bar.isVisibleTo(win)
    win._arm_tool(ArmedTool.HIGHLIGHT)
    app.processEvents()
    assert win.view.armed is ArmedTool.HIGHLIGHT
    assert win.markup_bar.isVisibleTo(win)
    assert app.settings.get_pref("markup_bar_visible", None) is None


def test_hiding_the_bar_disarms_a_kit_tool(app, win):
    win.markup_bar.show()
    win._arm_tool(ArmedTool.REDACT_REGION)
    assert win.view.armed is ArmedTool.REDACT_REGION
    win.markup_bar.toggleViewAction().trigger()  # user dismisses the kit
    app.processEvents()
    assert win.view.armed is None


def test_hiding_the_bar_leaves_a_menu_only_arm_alone(app, win):
    """CROP arms from the Tools menu and lights no bar button — dismissing the kit must not
    cancel it (its feedback is the crosshair + drag band, not markup-bar chrome)."""
    win.markup_bar.show()
    app.processEvents()
    win._arm_tool(ArmedTool.CROP)
    win.markup_bar.hide()
    app.processEvents()
    assert win.view.armed is ArmedTool.CROP


def test_mode_survives_dismissing_the_kit(app, win):
    """Grab is a reading tool too: hiding the markup bar keeps the pan mode (Tools menu still
    shows the checked state — only *armed one-shots* would be invisible traps)."""
    from viewer.tools import InteractionMode

    win.markup_bar.show()
    win.view.set_mode(InteractionMode.GRAB)
    win.markup_bar.hide()
    app.processEvents()
    assert win.view.mode is InteractionMode.GRAB
