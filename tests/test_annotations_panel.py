"""Annotations sidebar tab (PLAN.md §GUI feature roadmap → R6, M77). Offscreen GUI.

A third sidebar tab beside Pages | Outline listing every mark in the document — ours and foreign
— as "p. N · type · snippet" rows; click jumps + selects (the M47 pattern). The tab exists only
while the document has marks (owner rule: inapplicable chrome is invisible, not greyed out) and
tracks edits/undo live, including its own appearance and disappearance.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtWidgets import QTabWidget

from app import PdfApp
from model.page_edits import Highlight, TextBox
from store.settings import Settings


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def win(qapp, a_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
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


# ---- existence follows the marks ---------------------------------------------


def test_clean_document_shows_no_annotations_tab(win):
    assert _tab_labels(win) == ["Pages", "Outline"]  # a_pdf has an outline, no marks
    assert win.annotations_panel is None


def test_first_mark_summons_the_tab_and_undo_dismisses_it(win):
    win._add_annotation(0, Highlight((_word_box(win),)))
    assert _tab_labels(win) == ["Pages", "Outline", "Annotations"]
    assert win.annotations_panel is not None and win.annotations_panel.count() == 1
    win.undo_stack.undo()
    assert _tab_labels(win) == ["Pages", "Outline"]  # the last mark went — so did the tab
    assert win.annotations_panel is None
    win.undo_stack.redo()
    assert _tab_labels(win) == ["Pages", "Outline", "Annotations"]


def test_toc_less_marked_doc_gets_pages_and_annotations(qapp, b_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    w = qapp.open_document(b_pdf)  # B.pdf: no outline
    try:
        assert _tab_labels(w) == []  # bare Pages panel, no tab bar at all
        w._add_annotation(0, TextBox((72, 150, 300, 180), "note"))
        assert _tab_labels(w) == ["Pages", "Annotations"]
    finally:
        w.undo_stack.setClean()
        w.close()


def test_foreign_marks_alone_summon_the_tab(qapp, foreign_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
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
    box = _word_box(win)
    win._add_annotation(0, Highlight((box,)))
    win._add_annotation(2, TextBox((72, 150, 300, 180), "remember the milk"))
    panel = win.annotations_panel
    texts = [panel.item(i).text() for i in range(panel.count())]
    assert len(texts) == 2
    assert texts[0].startswith("p. 1 · highlight")
    assert "ALPHA-zero-A0" in texts[0]           # the covered text is the snippet
    assert texts[1].startswith("p. 3 · text box") or texts[1].startswith("p. 3 · ")
    assert "remember the milk" in texts[1]


def test_list_follows_add_and_remove_live(win):
    box = _word_box(win)
    win._add_annotation(0, Highlight((box,)))
    assert win.annotations_panel.count() == 1
    win._add_annotation(1, TextBox((72, 150, 300, 180), "second"))
    assert win.annotations_panel.count() == 2
    win.undo_stack.undo()
    assert win.annotations_panel.count() == 1


# ---- click jumps + selects ---------------------------------------------------


def test_click_jumps_and_selects_a_free_placed_mark(win):
    mark = TextBox((72.0, 150.0, 300.0, 180.0), "jump target")
    win._add_annotation(2, mark)
    panel = win.annotations_panel
    panel._on_item_clicked(panel.item(0))
    assert win.view.current_page == 2
    assert any(m is mark for _p, m in win.view.annotations.selected_objects)


def test_click_on_a_text_markup_jumps_without_object_selection(win):
    win._add_annotation(1, Highlight((_word_box(win, 1),)))
    panel = win.annotations_panel
    panel._on_item_clicked(panel.item(0))
    assert win.view.current_page == 1
    assert win.view.annotations.selected_objects == []  # text-anchored: nothing to select


def test_click_outlines_a_foreign_mark(qapp, foreign_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    w = qapp.open_document(foreign_pdf)
    try:
        panel = w.annotations_panel
        panel._on_item_clicked(panel.item(0))
        assert w.view.annotations._selection_items   # the foreign outline is up
    finally:
        w.undo_stack.setClean()
        w.close()


def test_reload_keeps_the_active_tab_by_label(win):
    """The tab set can change across a remount — the active tab is matched by label, so adding a
    mark while reading the Outline keeps Outline current."""
    tabs = win.pages_dock.widget()
    tabs.setCurrentIndex(1)  # Outline
    win._add_annotation(0, Highlight((_word_box(win),)))  # remounts with a third tab
    tabs = win.pages_dock.widget()
    assert tabs.tabText(tabs.currentIndex()) == "Outline"
