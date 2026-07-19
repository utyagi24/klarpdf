"""Outline sidebar + Go to Page (PLAN.md §GUI feature roadmap, M45). Offscreen GUI.

The sidebar grows a Pages | Outline tab switcher only for a document whose origin carries an
outline; a TOC-less document keeps the bare Pages panel — no tab, no tab bar (owner rule:
inapplicable chrome is invisible, not greyed out). The tree is the **live** ``remapped_toc()``
(deletes/reorders reflected as they happen, back on undo), selecting an entry jumps the view,
and the visible page's entry highlights as the view moves. Ctrl+G opens Go to Page.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QTabWidget

import main_window
from app import PdfApp
from model.edit_commands import DeleteCommand, InsertCommand
from model.virtual_document import PageRef
from organize.thumbnail_panel import ThumbnailPanel
from store.settings import Settings

_PAGE_ROLE = Qt.ItemDataRole.UserRole


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


def _items(win) -> list[tuple[str, int]]:
    """``(title, 0-based page)`` preorder from the outline tree."""
    out: list[tuple[str, int]] = []

    def walk(item):
        out.append((item.text(0), item.data(0, _PAGE_ROLE)))
        for i in range(item.childCount()):
            walk(item.child(i))

    for i in range(win.outline.topLevelItemCount()):
        walk(win.outline.topLevelItem(i))
    return out


def _overwrite(path: str, page_texts: list[str], toc: list | None = None) -> None:
    """Replace the file at ``path`` with a fresh document (sources are in-memory — no open handle)."""
    doc = fitz.open()
    for text in page_texts:
        doc.new_page().insert_text((72, 72), text, fontsize=11)
    if toc:
        doc.set_toc(toc)
    doc.save(path)
    doc.close()


# ---- tab materialisation (owner rule: no TOC → no tab and no tab bar) -----------


def test_toc_doc_mounts_pages_outline_tabs(app, a_pdf):
    win = app.open_document(a_pdf)
    tabs = win.pages_dock.widget()
    assert isinstance(tabs, QTabWidget)
    assert [tabs.tabText(i) for i in range(tabs.count())] == ["Pages", "Outline"]
    assert tabs.widget(0) is win.thumbs and tabs.widget(1) is win.outline
    assert win.pages_dock.windowTitle() == "Sidebar"


def test_switcher_keeps_the_pages_panel_width_bounds(app, a_pdf):
    """The tab container must carry the Pages panel's min/max width — a QTabWidget doesn't
    inherit its children's constraints, so without this the sidebar of a TOC'd document was
    freely resizable while a TOC-less one stayed capped."""
    win = app.open_document(a_pdf)
    tabs = win.pages_dock.widget()
    assert tabs.minimumWidth() == win.thumbs.minimumWidth()
    assert tabs.maximumWidth() == win.thumbs.maximumWidth()


def test_tocless_doc_keeps_the_bare_pages_panel(app, b_pdf):
    win = app.open_document(b_pdf)
    assert win.outline is None
    assert win.pages_dock.widget() is win.thumbs  # the panel itself — no tab container at all
    assert isinstance(win.pages_dock.widget(), ThumbnailPanel)
    assert win.pages_dock.windowTitle() == "Pages"


def test_tree_matches_the_outline_structure(app, a_pdf):
    win = app.open_document(a_pdf)
    # a_pdf: Chapter 1 (p1) > Section 1.1 (p2); Chapter 2 (p3) — pages 0-based in item data.
    assert _items(win) == [("Chapter 1", 0), ("Section 1.1", 1), ("Chapter 2", 2)]
    assert win.outline.topLevelItemCount() == 2
    chapter1 = win.outline.topLevelItem(0)
    assert chapter1.childCount() == 1 and chapter1.child(0).text(0) == "Section 1.1"
    assert chapter1.isExpanded()  # nested entries are visible by default


# ---- navigation -----------------------------------------------------------------


def test_selecting_an_entry_jumps_the_view(app, a_pdf):
    win = app.open_document(a_pdf)
    app.processEvents()
    chapter2 = win.outline.topLevelItem(1)
    win.outline.setCurrentItem(chapter2)  # a user click / keyboard move lands here
    assert win.view.current_page == 2


def test_view_scroll_highlights_the_entry_of_the_page_in_view(app, a_pdf):
    win = app.open_document(a_pdf)
    app.processEvents()
    win.view.goto_page(1)  # scroll, not a tree click — currentPageChanged drives the highlight
    assert win.outline.currentItem().text(0) == "Section 1.1"
    win.view.goto_page(2)
    assert win.outline.currentItem().text(0) == "Chapter 2"


def test_pages_before_or_between_bookmarks(app, tmp_path):
    # First bookmark on page 3 (1-based); a page before it highlights nothing, a page after a
    # bookmark (but before the next) highlights the nearest preceding entry.
    path = str(tmp_path / "late.pdf")
    _overwrite(path, [f"P{i}" for i in range(5)], toc=[[1, "Late", 3], [1, "Later", 5]])
    win = app.open_document(path)
    win.outline.set_current(0)
    assert win.outline.currentItem() is None
    win.outline.set_current(3)  # 1-based page 4: after "Late" (p3), before "Later" (p5)
    assert win.outline.currentItem().text(0) == "Late"
    win.outline.set_current(4)
    assert win.outline.currentItem().text(0) == "Later"


# ---- live remap during editing --------------------------------------------------


def test_outline_follows_a_delete_and_undo(app, a_pdf):
    win = app.open_document(a_pdf)
    win._delete_rows([1])  # Section 1.1's target page — the entry drops, Chapter 2 remaps
    assert _items(win) == [("Chapter 1", 0), ("Chapter 2", 1)]
    win.undo_stack.undo()
    assert _items(win) == [("Chapter 1", 0), ("Section 1.1", 1), ("Chapter 2", 2)]


def test_outline_follows_a_reorder(app, a_pdf):
    win = app.open_document(a_pdf)
    win._reorder([2], 0)  # Chapter 2's page to the front
    assert _items(win) == [("Chapter 1", 1), ("Section 1.1", 2), ("Chapter 2", 0)]


def test_emptied_outline_keeps_the_tab_with_an_empty_tree(app, a_pdf, b_pdf):
    # Deleting every bookmarked page empties the tree but the tab stays (the origin still has an
    # outline; undo brings the entries back) — the switcher never tears down mid-session.
    win = app.open_document(a_pdf)
    source_id = win.vdoc.open_source(b_pdf)
    win.undo_stack.push(InsertCommand(win.vdoc, 3, [PageRef(source_id, 0), PageRef(source_id, 1)],
                                      text="insert"))
    win.undo_stack.push(DeleteCommand(win.vdoc, [0, 1, 2]))
    assert win.outline is not None and _items(win) == []
    assert isinstance(win.pages_dock.widget(), QTabWidget)
    win.undo_stack.undo()
    assert _items(win) == [("Chapter 1", 0), ("Section 1.1", 1), ("Chapter 2", 2)]


def test_collapsed_branch_stays_collapsed_across_an_edit(app, a_pdf):
    win = app.open_document(a_pdf)
    win.outline.topLevelItem(0).setExpanded(False)  # user folds Chapter 1
    win._reorder([2], 0)  # any edit repopulates the tree
    assert win.outline.topLevelItem(0).isExpanded() is False


# ---- reload-in-place remounts the sidebar ---------------------------------------


def test_reload_to_a_tocless_file_unmounts_the_tab(app, a_pdf):
    win = app.open_document(a_pdf)
    _overwrite(a_pdf, ["ALPHA-zero-A0"])  # the file loses its outline on disk
    win._reset_to_file(a_pdf)
    assert win.outline is None
    assert win.pages_dock.widget() is win.thumbs
    assert win.pages_dock.windowTitle() == "Pages"


def test_reload_to_a_toc_file_mounts_the_tab(app, b_pdf):
    win = app.open_document(b_pdf)
    assert win.outline is None
    _overwrite(b_pdf, ["BETA-zero-B0", "BETA-one-B1"], toc=[[1, "Grown", 2]])
    win._reset_to_file(b_pdf)
    assert win.outline is not None
    assert _items(win) == [("Grown", 1)]
    assert win.pages_dock.widget().widget(0) is win.thumbs  # thumbs re-homed into the switcher


# ---- Go to Page… (Ctrl+G) -------------------------------------------------------


def test_goto_page_dialog_jumps(app, a_pdf, monkeypatch):
    win = app.open_document(a_pdf)
    asked = {}

    def fake_get_int(parent, title, label, value, minimum, maximum, step):
        asked.update(label=label, value=value, minimum=minimum, maximum=maximum)
        return 3, True

    monkeypatch.setattr(main_window.QInputDialog, "getInt", staticmethod(fake_get_int))
    calls: list[int] = []
    monkeypatch.setattr(win.view, "goto_page", calls.append)
    win._goto_page_dialog()
    assert calls == [2]  # dialog takes 1-based, the view is 0-based
    assert (asked["minimum"], asked["maximum"]) == (1, 3)
    assert asked["value"] == win.view.current_page + 1  # prefilled with the page in view


def test_goto_page_dialog_cancel_stays_put(app, a_pdf, monkeypatch):
    win = app.open_document(a_pdf)
    monkeypatch.setattr(main_window.QInputDialog, "getInt",
                        staticmethod(lambda *a, **k: (2, False)))
    calls: list[int] = []
    monkeypatch.setattr(win.view, "goto_page", calls.append)
    win._goto_page_dialog()
    assert calls == []


def test_goto_page_has_the_ctrl_g_shortcut(app, a_pdf):
    win = app.open_document(a_pdf)
    actions = [a for a in win.findChildren(QAction) if a.text() == "Go to &Page…"]
    assert len(actions) == 1
    assert actions[0].shortcut() == QKeySequence("Ctrl+G")
