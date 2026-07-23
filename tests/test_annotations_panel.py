"""Annotations sidebar tab (PLAN.md §GUI feature roadmap → R6, M77). Offscreen GUI.

A sidebar tab beside Pages | Outline listing the document's **text markups** — highlights,
underlines, strike-outs and notes, ours and foreign — as "p. N · type · snippet" rows; click
jumps (M77.1 narrowed it from every mark: drawings, text boxes, stamps and fields are placed
objects with no passage to read back, and they buried the markups). The tab is offered only while
the document has marks it would list (owner rule: inapplicable chrome is invisible, not greyed out)
and its rows track edits and undo live — but never its own existence: since M79.3 a mark **offers**
the tab, and mounting it is the reader's move (see `test_sidebar_tabs.py` for that rule itself).
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtWidgets import QTabWidget

from app import PdfApp
from model.content_marks import ImageStamp
from model.page_edits import Highlight, Line, Shape, Strikeout, TextBox, Underline
from store.settings import Settings


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def win(qapp, a_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.settings.set_pref("sidebar_tabs", ["annotations", "outline"])
    w = qapp.open_document(a_pdf)
    w.show()
    qapp.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


@pytest.fixture
def foreign_pdf(tmp_path) -> str:
    """One page with body text and a *foreign* highlight (no KlarPDF author tag)."""
    path = str(tmp_path / "foreign.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "FOREIGN-page text body", fontsize=12)
    annot = page.add_highlight_annot(fitz.Rect(70, 60, 160, 80))
    annot.set_info(content="their comment")
    annot.update()
    doc.save(path)
    doc.close()
    return path


def _tab_labels(win) -> list[str]:
    widget = win.pages_dock.widget()
    if not isinstance(widget, QTabWidget):
        return []
    return [widget.tabText(i) for i in range(widget.count())]


def _word_box(win, page_index=0) -> tuple:
    ref = win.vdoc.ordered[page_index]
    page = win.vdoc.sources[ref.source_id][ref.source_page_index]
    return tuple(page.get_text("words")[0][:4])


def _show_annotations(win) -> None:
    """Mount the tab the way a reader does: the ▾ entry a mark just made offerable (M79.3)."""
    win._sidebar_tab_actions["annotations"].setChecked(True)


# ---- existence follows the marks ---------------------------------------------


def test_clean_document_shows_no_annotations_tab(win):
    assert _tab_labels(win) == ["Pages", "Outline"]  # a_pdf has an outline, no marks
    assert win.annotations_panel is None


def test_a_mark_offers_the_tab_and_the_reader_mounts_it(win):
    win._add_annotation(0, Highlight((_word_box(win),)))
    assert _tab_labels(win) == ["Pages", "Outline"]  # M79.3: no panel arrives on its own…
    assert win._sidebar_tab_actions["annotations"].isVisible()   # …the ▾ offers one
    _show_annotations(win)
    assert _tab_labels(win) == ["Pages", "Outline", "Annotations"]
    assert win.annotations_panel is not None and win.annotations_panel.count() == 1
    win.undo_stack.undo()
    assert win.annotations_panel.count() == 0        # the row goes; the tab is the reader's
    win.undo_stack.redo()
    assert win.annotations_panel.count() == 1


def test_toc_less_marked_doc_gets_pages_and_annotations(qapp, b_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.settings.set_pref("sidebar_tabs", ["annotations", "outline"])
    w = qapp.open_document(b_pdf)  # B.pdf: no outline
    try:
        assert _tab_labels(w) == []  # bare Pages panel, no tab bar at all
        w._add_annotation(0, Highlight((_word_box(w),)))
        _show_annotations(w)
        assert _tab_labels(w) == ["Pages", "Annotations"]
    finally:
        w.undo_stack.setClean()
        w.close()


def test_objects_alone_do_not_summon_the_tab(qapp, b_pdf, tmp_path):
    """A page of drawings has marks but nothing the list would show, and a tab over an empty list
    is the dead chrome the tab's whole existence rule exists to prevent (M77.1)."""
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.settings.set_pref("sidebar_tabs", ["annotations", "outline"])
    w = qapp.open_document(b_pdf)
    try:
        w._add_annotation(0, TextBox((72, 150, 300, 180), "note to self"))
        w._add_annotation(0, Line((72, 200), (300, 240)))
        assert _tab_labels(w) == []
        assert w.annotations_panel is None
        assert not w._sidebar_tab_actions["annotations"].isVisible()  # not even offered
        w._add_annotation(0, Highlight((_word_box(w),)))   # …one markup, and it is on offer
        assert w._sidebar_tab_actions["annotations"].isVisible()
        _show_annotations(w)
        assert _tab_labels(w) == ["Pages", "Annotations"]
        assert w.annotations_panel.count() == 1            # listing the markup alone
    finally:
        w.undo_stack.setClean()
        w.close()


def test_foreign_marks_alone_summon_the_tab(qapp, foreign_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.settings.set_pref("sidebar_tabs", ["annotations", "outline"])
    w = qapp.open_document(foreign_pdf)
    try:
        assert _tab_labels(w) == ["Pages", "Annotations"]  # foreign-only doc lists too
        texts = [w.annotations_panel.item(i).text()
                 for i in range(w.annotations_panel.count())]
        assert len(texts) == 1
        assert texts[0].startswith("p. 1 · ")
        assert "their comment" in texts[0]
    finally:
        w.undo_stack.setClean()
        w.close()


# ---- rows --------------------------------------------------------------------


def test_rows_carry_page_type_and_snippet(win):
    win._add_annotation(0, Highlight((_word_box(win),)))
    win._add_annotation(2, Strikeout((_word_box(win, 2),)))
    _show_annotations(win)
    panel = win.annotations_panel
    texts = [panel.item(i).text() for i in range(panel.count())]
    assert len(texts) == 2
    assert texts[0].startswith("p. 1 · highlight")
    assert "ALPHA-zero-A0" in texts[0]           # the covered text is the snippet
    assert texts[1].startswith("p. 3 · ")
    assert "ALPHA-two-A2" in texts[1]


def test_objects_never_reach_the_list(win):
    """The narrowing (M77.1): drawings, text boxes, stamps and fields are placed objects — found
    where they sit, arranged in Objects mode — and listing them buried the markups."""
    win._add_annotation(0, Highlight((_word_box(win),)))
    win._add_annotation(0, TextBox((72, 150, 300, 180), "remember the milk"))
    win._add_annotation(1, Line((72, 200), (300, 240)))
    win._add_annotation(1, Shape("rect", (72, 260, 300, 320)))
    win._add_annotation(2, ImageStamp((72, 100, 200, 160), "sig.png"))
    _show_annotations(win)
    panel = win.annotations_panel
    texts = [panel.item(i).text() for i in range(panel.count())]
    assert len(texts) == 1
    assert texts[0].startswith("p. 1 · highlight")


def test_list_follows_add_and_remove_live(win):
    win._add_annotation(0, Highlight((_word_box(win),)))
    _show_annotations(win)
    assert win.annotations_panel.count() == 1
    win._add_annotation(1, Underline((_word_box(win, 1),)))
    assert win.annotations_panel.count() == 2
    win.undo_stack.undo()
    assert win.annotations_panel.count() == 1


# ---- click jumps + selects ---------------------------------------------------


def test_click_on_a_text_markup_jumps_without_object_selection(win):
    win._add_annotation(1, Highlight((_word_box(win, 1),)))
    _show_annotations(win)
    panel = win.annotations_panel
    panel._on_item_clicked(panel.item(0))
    assert win.view.current_page == 1
    assert win.view.annotations.selected_objects == []  # text-anchored: nothing to select


def test_click_outlines_a_foreign_mark(qapp, foreign_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.settings.set_pref("sidebar_tabs", ["annotations", "outline"])
    w = qapp.open_document(foreign_pdf)
    try:
        panel = w.annotations_panel
        panel._on_item_clicked(panel.item(0))
        assert w.view.annotations._selection_items   # the foreign outline is up
    finally:
        w.undo_stack.setClean()
        w.close()


def test_reload_keeps_the_active_tab_by_label(win):
    """The tab set can change across a remount — the active tab is matched by label, so asking for
    Annotations while reading the Outline keeps Outline current."""
    tabs = win.pages_dock.widget()
    tabs.setCurrentIndex(1)  # Outline
    win._add_annotation(0, Highlight((_word_box(win),)))
    _show_annotations(win)                               # remounts with a third tab
    tabs = win.pages_dock.widget()
    assert tabs.tabText(tabs.currentIndex()) == "Outline"
