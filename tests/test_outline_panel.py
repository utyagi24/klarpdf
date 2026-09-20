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
from edit_commands import DeleteCommand, InsertCommand
from klarpdf.model.virtual_document import PageRef
from organize.thumbnail_panel import ThumbnailPanel
from store.settings import Settings

_PAGE_ROLE = Qt.ItemDataRole.UserRole


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    # The Outline tab is opt-in since M79.1 (Pages alone by default); this file tests the tab
    # itself, so it is switched on the way a reader who wants outlines would leave it.
    qapp.settings.set_pref("sidebar_tabs", ["annotations", "outline"])
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


# ---- an entry lands on its heading, not the page top (M150, #362) --------------


@pytest.fixture
def positioned_outline_pdf(tmp_path) -> str:
    """Four Letter pages; the bookmarks state where on their page they point.

    Written as raw outline objects rather than through ``set_toc``, which can only emit ``/XYZ``
    and misplaces even that on a 270° page — the two reasons this milestone exists. ``new_page``'s
    default is A4, so the size is given: a destination's y is measured from the page's own bottom
    edge, and 792 on an 842 pt page is 50 pt down rather than the top.
    """
    path = str(tmp_path / "positioned.pdf")
    doc = fitz.open()
    for i in range(4):
        doc.new_page(width=612, height=792).insert_text((72, 72), f"PAGE {i}", fontsize=11)
    tails = ["/Fit", "/XYZ 40 600 0", "/FitH 200"]      # page 1, page 2 mid, page 3 low
    items = [doc.get_new_xref() for _ in tails]
    root = doc.get_new_xref()
    for n, (item, tail) in enumerate(zip(items, tails)):
        nxt = f"/Next {items[n + 1]} 0 R " if n + 1 < len(items) else ""
        doc.update_object(
            item,
            f"<< /Title (Entry {n}) /Parent {root} 0 R {nxt}"
            f"/Dest [{doc.page_xref(n)} 0 R {tail}] >>",
        )
    doc.update_object(
        root,
        f"<< /Type /Outlines /First {items[0]} 0 R /Last {items[-1]} 0 R /Count {len(items)} >>",
    )
    doc.xref_set_key(doc.pdf_catalog(), "Outlines", f"{root} 0 R")
    doc.save(path)
    doc.close()
    return path


def _activate(win, row: int):
    """Click the outline entry at preorder position ``row``."""
    items = []

    def walk(item):
        items.append(item)
        for i in range(item.childCount()):
            walk(item.child(i))

    for i in range(win.outline.topLevelItemCount()):
        walk(win.outline.topLevelItem(i))
    win.outline._on_item_clicked(items[row], 0)


def _top_of(view, index: int) -> int:
    from viewer.pdf_view import _PAGE_GAP

    return int(view._pages[index]["y"]) - _PAGE_GAP


def test_an_entry_with_no_position_lands_on_the_page_top(app, positioned_outline_pdf):
    """``/Fit``: the behaviour M45 shipped, and the one that must not change."""
    win = app.open_document(positioned_outline_pdf)
    _activate(win, 0)
    assert win.view.verticalScrollBar().value() == _top_of(win.view, 0)


def test_an_entry_lands_on_the_spot_its_bookmark_names(app, positioned_outline_pdf):
    """#362's second half: ``main_window`` wired ``entryActivated`` straight to ``goto_page``,
    so a bookmark that named a spot was read for its page and nothing else."""
    win = app.open_document(positioned_outline_pdf)
    _activate(win, 1)                                    # /XYZ 40 600 on page 2
    expected = int(win.view._pages[1]["y"] + (792.0 - 600.0) * win.view.scale) - 14
    assert win.view.verticalScrollBar().value() == pytest.approx(expected, abs=2)
    assert win.view.verticalScrollBar().value() > _top_of(win.view, 1)


def test_a_fith_entry_uses_its_top(app, positioned_outline_pdf):
    """``/FitH 200`` states a top and no left — a form ``get_toc`` reports as ``view: 'FitH,592'``
    and ``set_toc`` cannot write at all, so before M150 it reached the panel as a bare page."""
    win = app.open_document(positioned_outline_pdf)
    _activate(win, 2)
    expected = int(win.view._pages[2]["y"] + (792.0 - 200.0) * win.view.scale) - 14
    assert win.view.verticalScrollBar().value() == pytest.approx(expected, abs=2)


def test_the_spot_follows_a_reorder_with_its_page(app, positioned_outline_pdf):
    """The spot rides in the same remapped entry as the page number, so the two cannot disagree
    about which bookmark they belong to."""
    win = app.open_document(positioned_outline_pdf)
    win.vdoc.move_pages([1], 3)           # the /XYZ 40 600 target slides from index 1 to 2
    win.view.reload()
    win.outline.populate()
    rows = _items(win)
    assert ("Entry 1", 2) in rows         # it followed the move
    _activate(win, rows.index(("Entry 1", 2)))
    expected = int(win.view._pages[2]["y"] + (792.0 - 600.0) * win.view.scale) - 14
    assert win.view.verticalScrollBar().value() == pytest.approx(expected, abs=2)


def test_a_spot_near_the_end_of_the_document_scrolls_as_far_as_it_can(app, positioned_outline_pdf):
    """There is nothing below the last page, so a spot low on it cannot reach the window's top.

    Qt clamps the scroll, which is right — and worth pinning, because the obvious way to write the
    test above is to move the target to the *end*, where the clamp silently makes a broken
    implementation and a working one produce the same number.
    """
    win = app.open_document(positioned_outline_pdf)
    win.vdoc.move_pages([1], 4)           # the /XYZ 40 600 target goes to the end
    win.view.reload()
    win.outline.populate()
    rows = _items(win)
    _activate(win, rows.index(("Entry 1", 3)))
    bar = win.view.verticalScrollBar()
    wanted = int(win.view._pages[3]["y"] + (792.0 - 600.0) * win.view.scale) - 14
    assert wanted > bar.maximum()         # the fixture really does ask for more than there is
    assert bar.value() == bar.maximum()


@pytest.fixture
def mixed_width_outline_pdf(tmp_path) -> str:
    """Four portrait pages, **one landscape**, and bookmarks that name a left margin.

    The landscape page is what gives the horizontal bar something to move: the scene is sized to
    the *widest* row while Fit Width fits the *current* page, so every portrait page sits centred
    in a wider band. `SpaceX-EUProspectus-outlined.pdf` is exactly this shape — 2 landscape pages
    among 400, and 101 bookmarks all saying `/XYZ 72 805.68`.
    """
    path = str(tmp_path / "mixedwidth.pdf")
    doc = fitz.open()
    for i in range(4):
        doc.new_page(width=612, height=792).insert_text((72, 72), f"PAGE {i}", fontsize=11)
    doc.new_page(width=1224, height=792).insert_text((72, 72), "WIDE", fontsize=11)
    items = [doc.get_new_xref() for _ in range(2)]
    root = doc.get_new_xref()
    for n, item in enumerate(items):
        nxt = f"/Next {items[n + 1]} 0 R " if n + 1 < len(items) else ""
        doc.update_object(
            item,
            f"<< /Title (Entry {n}) /Parent {root} 0 R {nxt}"
            f"/Dest [{doc.page_xref(n)} 0 R /XYZ 40 600 0] >>",
        )
    doc.update_object(
        root, f"<< /Type /Outlines /First {items[0]} 0 R /Last {items[-1]} 0 R /Count 2 >>"
    )
    doc.xref_set_key(doc.pdf_catalog(), "Outlines", f"{root} 0 R")
    doc.save(path)
    doc.close()
    return path


def test_fit_width_is_not_thrown_off_centre_by_an_entrys_left(app, mixed_width_outline_pdf):
    """Owner-reported on `SpaceX-EUProspectus-outlined.pdf` (2026-09-20): *"I set my view to fit
    width and clicking on any entry in the TOC throws my page off center."*

    All 101 of that file's bookmarks say ``/XYZ 72 805.68`` — a left of 72 pt, the page's own
    margin. At Fit Width the strip sits in a wider scene, so the horizontal bar has range and rests
    centred; putting that 72 pt at the window's left edge, which is the literal reading of the
    destination, threw the bar from 191 to 344 and cut 139 px off the page's left side.

    The precondition is asserted: with no horizontal range there is nothing to throw off centre,
    and the test would pass whatever the code did.
    """
    win = app.open_document(mixed_width_outline_pdf)
    win.view.fit_width()
    bar = win.view.horizontalScrollBar()
    assert bar.maximum() > 0, "no horizontal range — this fixture cannot show the defect"
    resting = bar.value()
    _activate(win, 1)                                    # /XYZ 40 600 — a left of 40 pt
    assert bar.value() == resting
    assert win.view.verticalScrollBar().value() > _top_of(win.view, 1), "…and it still moved down"
