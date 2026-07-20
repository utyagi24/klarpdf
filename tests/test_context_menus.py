"""Context menus everywhere (PLAN.md §GUI feature roadmap, M46). Offscreen GUI.

The view's right-click menu is built by hit state — our annotation → Remove; a live text
selection → Copy / Highlight Selection / Redact Selection; an internal link → Go to Page N; an
external link → Copy Link Address (clipboard only — never navigated, the app stays offline); a
bare page → the routed View-menu navigation QActions. The Pages sidebar menu grows Rotate
Left/Right. Menus are built unexec'd (`_view_context_menu` / `_build_page_context_menu`), so
tests assert contents and trigger actions without popping UI.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtGui import QGuiApplication

from app import PdfApp
from model.page_edits import Highlight
from store.settings import Settings
from viewer.tools import ArmedTool

_LINK_BOX = (72, 300, 200, 320)  # page 0 → page 4 (internal GoTo)
_URI_BOX = (72, 340, 200, 360)   # page 0 → https://example.org/spec (external)
_URI = "https://example.org/spec"


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    qapp.object_clipboard = []
    for w in list(qapp._windows.values()):
        w.close()
    qapp._windows.clear()
    yield qapp
    for w in list(qapp._windows.values()):
        w.undo_stack.setClean()
        w.close()
    qapp._windows.clear()


@pytest.fixture
def menu_pdf(tmp_path) -> str:
    """5 text pages; page 0 carries an internal GoTo link and an external URI link."""
    path = str(tmp_path / "menus.pdf")
    doc = fitz.open()
    for i in range(5):
        doc.new_page().insert_text((72, 72), f"MENU page {i}", fontsize=14)
    doc[0].insert_link({"kind": fitz.LINK_GOTO, "from": fitz.Rect(*_LINK_BOX), "page": 3,
                        "to": fitz.Point(0, 0)})
    doc[0].insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(*_URI_BOX), "uri": _URI})
    doc.save(path)
    doc.close()
    return path


def _win(app, path):
    w = app.open_document(path)
    app.processEvents()
    return w


def _titles(menu) -> list[str]:
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def _word_center(win, page_index=0):
    ref = win.vdoc.ordered[page_index]
    page = win.vdoc.sources[ref.source_id][ref.source_page_index]
    word = page.get_text("words")[0]
    return win.view.scene_rect_for_box(page_index, word[:4]).center()


def _bare_point(win):
    return win.view.scene_rect_for_box(0, (300, 500, 360, 520)).center()  # no text/link there


# ---- the view menu, state by state ----------------------------------------------


def test_bare_page_menu_routes_the_view_actions(app, menu_pdf):
    win = _win(app, menu_pdf)
    menu = win._view_context_menu(_bare_point(win))
    entries = [a for a in menu.actions() if not a.isSeparator()]
    # Paste Object (M59) leads — the one verb acting at the clicked spot; disabled while the
    # object clipboard is empty.
    assert entries[0].text() == "Paste Object" and entries[0].isEnabled() is False
    # The rest are the *same* QAction objects as the View menu — labels/shortcuts single-sourced.
    assert entries[1:] == [win._a_fitw, win._a_fitp, win._a_actual,
                           win._a_rotl, win._a_rotr, win._a_goto]


def test_selection_menu_offers_copy_highlight_redact(app, menu_pdf):
    win = _win(app, menu_pdf)
    assert win.view.selection.select_word_at(_word_center(win)) is True
    menu = win._view_context_menu(_bare_point(win))  # anywhere: the selection owns the menu
    assert _titles(menu) == ["Copy", "Highlight Selection", "Underline Selection",
                             "Strike Out Selection", "Redact Selection"]
    assert menu.actions()[0] is win._a_copy_text  # the real Copy action, shortcut and all


def test_selection_menu_highlight_applies_now(app, menu_pdf):
    win = _win(app, menu_pdf)
    win.view.selection.select_word_at(_word_center(win))
    menu = win._view_context_menu(_bare_point(win))
    next(a for a in menu.actions() if a.text() == "Highlight Selection").trigger()
    highlights = [a for a in win.vdoc.page_annotations(0) if isinstance(a, Highlight)]
    assert len(highlights) == 1
    assert win.view.selection.selected_words() == []  # selection consumed by the apply


def test_annotation_menu_removes_on_trigger(app, menu_pdf):
    win = _win(app, menu_pdf)
    rect = (72, 60, 180, 90)
    win._add_annotation(0, Highlight((rect,)))
    center = win.view.scene_rect_for_box(0, rect).center()
    menu = win._view_context_menu(center)
    assert _titles(menu) == ["Remove highlight"]
    menu.actions()[0].trigger()
    assert win.vdoc.page_annotations(0) == ()
    assert win.undo_stack.canUndo()  # removal is undoable, like the pre-M46 menu


def test_annotation_hit_wins_over_a_live_selection(app, menu_pdf):
    win = _win(app, menu_pdf)
    rect = (240, 500, 320, 530)  # away from the selected word
    win._add_annotation(0, Highlight((rect,)))
    win.view.selection.select_word_at(_word_center(win))
    menu = win._view_context_menu(win.view.scene_rect_for_box(0, rect).center())
    assert _titles(menu) == ["Remove highlight"]  # most specific hit first


def test_internal_link_menu_goes_to_target(app, menu_pdf):
    win = _win(app, menu_pdf)
    menu = win._view_context_menu(win.view.scene_rect_for_box(0, _LINK_BOX).center())
    assert _titles(menu) == ["Go to Page 4"]
    menu.actions()[0].trigger()
    assert win.view.current_page == 3


def test_external_link_menu_copies_the_address(app, menu_pdf):
    win = _win(app, menu_pdf)
    QGuiApplication.clipboard().setText("")  # a stale clipboard must not fake the assert
    menu = win._view_context_menu(win.view.scene_rect_for_box(0, _URI_BOX).center())
    assert _titles(menu) == ["Copy Link Address"]
    menu.actions()[0].trigger()
    assert QGuiApplication.clipboard().text() == _URI


def test_external_link_is_still_not_click_navigable(app, menu_pdf):
    win = _win(app, menu_pdf)
    center = win.view.scene_rect_for_box(0, _URI_BOX).center()
    assert win.view.links.link_at(center) is None       # no jump target
    assert win.view.links.navigate_at(center) is False  # a click does nothing (offline app)


# ---- toolbar tools agree with the menu (select-then-click applies immediately) --


def test_toolbar_highlight_applies_to_a_live_selection(app, menu_pdf):
    # "Select text, click Highlight" acts at once — same meaning as the context menu; the
    # toolbar no longer arms-and-waits while a selection sits there (owner call, M46 review).
    win = _win(app, menu_pdf)
    win.view.selection.select_word_at(_word_center(win))
    win._arm_tool(ArmedTool.HIGHLIGHT)
    assert win.view.armed is None  # applied, not armed
    highlights = [a for a in win.vdoc.page_annotations(0) if isinstance(a, Highlight)]
    assert len(highlights) == 1
    assert win.view.selection.selected_words() == []


def test_toolbar_redact_text_applies_to_a_live_selection(app, menu_pdf):
    from model.page_edits import Redaction

    win = _win(app, menu_pdf)
    win.view.selection.select_word_at(_word_center(win))
    win._arm_tool(ArmedTool.REDACT_TEXT)
    assert win.view.armed is None
    assert any(isinstance(a, Redaction) for a in win.vdoc.page_annotations(0))


def test_toolbar_highlight_arms_when_nothing_is_selected(app, menu_pdf):
    win = _win(app, menu_pdf)
    win._arm_tool(ArmedTool.HIGHLIGHT)
    assert win.view.armed is ArmedTool.HIGHLIGHT  # the arm-then-drag flow is unchanged
    win._arm_tool(ArmedTool.HIGHLIGHT)
    assert win.view.armed is None  # click again → toggle off, as before


# ---- the Pages-sidebar menu -----------------------------------------------------


def test_sidebar_menu_lists_all_verbs_with_rotate(app, menu_pdf):
    win = _win(app, menu_pdf)
    menu = win._build_page_context_menu([0])
    assert _titles(menu) == ["Cut", "Copy", "Paste", "Delete", "Duplicate",
                             "Rotate Left", "Rotate Right",
                             "Insert Pages from File…", "Insert Blank Page", "Export as PDF…"]


def test_sidebar_menu_disables_row_verbs_without_a_selection(app, menu_pdf):
    win = _win(app, menu_pdf)
    by_title = {a.text(): a for a in win._build_page_context_menu([]).actions()}
    for title in ("Cut", "Copy", "Delete", "Duplicate", "Rotate Left", "Rotate Right",
                  "Export as PDF…"):
        assert by_title[title].isEnabled() is False
    assert by_title["Paste"].isEnabled() is False  # empty page clipboard
    assert by_title["Insert Pages from File…"].isEnabled() is True
    assert by_title["Insert Blank Page"].isEnabled() is True


def test_sidebar_menu_paste_enables_with_clipboard(app, menu_pdf):
    win = _win(app, menu_pdf)
    win.thumbs.setCurrentRow(0)
    win._copy_pages([0])
    by_title = {a.text(): a for a in win._build_page_context_menu([]).actions()}
    assert by_title["Paste"].isEnabled() is True


def test_sidebar_menu_rotate_right_rotates_the_selection(app, menu_pdf):
    win = _win(app, menu_pdf)
    win.thumbs.setCurrentRow(1)  # selects row 1
    menu = win._build_page_context_menu(win.thumbs.selected_rows())
    next(a for a in menu.actions() if a.text() == "Rotate Right").trigger()
    assert win.vdoc.ordered[1].rotation_override == 90
    win.undo_stack.undo()
    assert win.vdoc.ordered[1].rotation_override is None
