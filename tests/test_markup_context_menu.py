"""Markup context menu (PLAN.md §GUI feature roadmap → R6, M76; reshaped M76.1). Offscreen GUI.

Right-click on already-marked text offers Preview's change set as Preview lays it out (owner
call at the M78 test pass): three sections — Highlight · Underline · Strike Out — each a header
over a **horizontal row of colour dots** ending in a slashed **remove** dot (the standard
"no colour" glyph; the tooltip carries the verb). A colour recolours the layer in place through
the M59.10 merge (trim/absorb, never stacking) or lays that layer on; the slashed dot removes
it; the ring marks each layer's current state. One undo step per dot — and the rows are the
complete menu: no trailing "Remove <noun>" wording a second removal path (the owner report).
"""

from __future__ import annotations

import pytest

from app import PdfApp
from model.page_edits import Highlight, Strikeout, Underline, marks_over, remove_markup
from store.settings import Settings
from viewer.markup_style import HIGHLIGHT_COLORS, TEXT_LINE_COLORS, SwatchRowAction

YELLOW = HIGHLIGHT_COLORS[0][1]
GREEN = HIGHLIGHT_COLORS[1][1]
LINE_RED = TEXT_LINE_COLORS[0][1]
LINE_BLUE = TEXT_LINE_COLORS[1][1]


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


def _word_box(win, page_index=0, n=0) -> tuple:
    ref = win.vdoc.ordered[page_index]
    page = win.vdoc.sources[ref.source_id][ref.source_page_index]
    return tuple(page.get_text("words")[n][:4])


def _menu_over(win, box: tuple):
    win.view.annotations.repaint()
    return win._view_context_menu(win.view.scene_rect_for_box(0, box).center())


def _rows(menu) -> list[SwatchRowAction]:
    return [a for a in menu.actions() if isinstance(a, SwatchRowAction)]


def _row(menu, title: str) -> SwatchRowAction:
    row = next((r for r in _rows(menu) if r.title == title), None)
    assert row is not None, f"no swatch row {title!r} in {[r.title for r in _rows(menu)]}"
    return row


def _highlights(win, page_index=0) -> list:
    return [a for a in win.vdoc.page_annotations(page_index) if isinstance(a, Highlight)]


# ---- the shape the owner asked for -------------------------------------------


def test_menu_is_three_swatch_rows_and_nothing_else(win):
    """Preview's layout: Highlight · Underline · Strike Out sections, each one dot row — and no
    worded entries at all."""
    box = _word_box(win)
    win.vdoc.add_annotation(0, Highlight((box,), color=YELLOW))
    win.view.reload()
    menu = _menu_over(win, box)
    assert [r.title for r in _rows(menu)] == ["Highlight", "Underline", "Strike Out"]
    assert [a.text() for a in menu.actions() if a.text()] == []  # dots, not words
    assert [b for r in _rows(menu) for b in r.buttons] == (
        [n for n, _ in HIGHLIGHT_COLORS] + [n for n, _ in TEXT_LINE_COLORS] * 2)


def test_exactly_one_removal_path_per_layer(win):
    """The owner report: a highlight's menu offered both "No Highlight" and "Remove highlight".
    Now each row's slashed dot is the one removal control, tooltipped with the verb."""
    box = _word_box(win)
    win.vdoc.add_annotation(0, Highlight((box,), color=YELLOW))
    win.view.reload()
    menu = _menu_over(win, box)
    texts = [a.text() for a in menu.actions() if a.text()]
    assert "Remove highlight" not in texts and "No Highlight" not in texts
    row = _row(menu, "Highlight")
    assert row.remove_button.toolTip() == "Remove highlight"
    row.remove_button.click()
    assert _highlights(win) == []               # the dot removes…
    assert win.undo_stack.canUndo()             # …undoably, like the old entry


def test_rows_ring_the_current_state(win):
    box = _word_box(win)
    win.vdoc.add_annotation(0, Highlight((box,), color=YELLOW))
    win.vdoc.add_annotation(0, Underline((box,), color=LINE_BLUE))
    win.view.reload()
    menu = _menu_over(win, box)
    assert _row(menu, "Highlight").active == YELLOW
    assert _row(menu, "Underline").active == LINE_BLUE
    assert _row(menu, "Strike Out").active is None   # absent → the slashed dot wears the ring


# ---- recolour ----------------------------------------------------------------


def test_recolour_in_place_one_undo_step(win):
    box = _word_box(win)
    win.vdoc.add_annotation(0, Highlight((box,), color=YELLOW))
    win.view.reload()
    before = win.undo_stack.count()
    _row(_menu_over(win, box), "Highlight").buttons["Green"].click()
    marks = _highlights(win)
    assert len(marks) == 1                         # recoloured, never stacked
    assert marks[0].color == GREEN
    assert marks[0].rects == (box,)                # same words
    assert win.undo_stack.count() == before + 1    # one undo step
    win.undo_stack.undo()
    assert _highlights(win)[0].color == YELLOW


def test_recolour_to_the_same_colour_is_a_no_op(win):
    box = _word_box(win)
    win.vdoc.add_annotation(0, Highlight((box,), color=YELLOW))
    win.view.reload()
    before = win.undo_stack.count()
    _row(_menu_over(win, box), "Highlight").buttons["Yellow"].click()
    assert win.undo_stack.count() == before        # nothing changed → nothing pushed


def test_underline_recolours_from_its_own_row(win):
    """New with the rows: the line layers get direct colour choice too, not just add/remove."""
    box = _word_box(win)
    win.vdoc.add_annotation(0, Underline((box,), color=LINE_RED))
    win.view.reload()
    _row(_menu_over(win, box), "Underline").buttons["Blue"].click()
    underlines = marks_over(win.vdoc.page_annotations(0), (box,), Underline)
    assert len(underlines) == 1 and underlines[0].color == LINE_BLUE


# ---- add / remove the other layers -------------------------------------------


def test_add_a_strikeout_over_a_highlight(win):
    box = _word_box(win)
    win.vdoc.add_annotation(0, Highlight((box,), color=YELLOW))
    win.view.reload()
    before = win.undo_stack.count()
    _row(_menu_over(win, box), "Strike Out").buttons["Red"].click()
    annots = win.vdoc.page_annotations(0)
    strikes = marks_over(annots, (box,), Strikeout)
    assert len(strikes) == 1 and strikes[0].color == LINE_RED
    assert len(_highlights(win)) == 1              # the highlight is untouched
    assert win.undo_stack.count() == before + 1


def test_remove_one_layer_leaves_the_other(win):
    box = _word_box(win)
    win.vdoc.add_annotation(0, Highlight((box,), color=YELLOW))
    win.vdoc.add_annotation(0, Strikeout((box,)))
    win.view.reload()
    before = win.undo_stack.count()
    _row(_menu_over(win, box), "Strike Out").remove_button.click()
    annots = win.vdoc.page_annotations(0)
    assert marks_over(annots, (box,), Strikeout) == []
    assert len(_highlights(win)) == 1              # the highlight layer survives
    assert win.undo_stack.count() == before + 1
    win.undo_stack.undo()
    assert len(marks_over(win.vdoc.page_annotations(0), (box,), Strikeout)) == 1


def test_remove_dot_on_an_absent_layer_is_a_no_op(win):
    """Radio semantics: the ringed slashed dot says "none here"; clicking it pushes nothing."""
    box = _word_box(win)
    win.vdoc.add_annotation(0, Highlight((box,), color=YELLOW))
    win.view.reload()
    before = win.undo_stack.count()
    _row(_menu_over(win, box), "Underline").remove_button.click()
    assert win.undo_stack.count() == before


def test_highlight_dot_from_an_underline_hit_lays_the_wash(win):
    box = _word_box(win)
    win.vdoc.add_annotation(0, Underline((box,)))
    win.view.reload()
    _row(_menu_over(win, box), "Highlight").buttons["Green"].click()
    highlights = _highlights(win)
    assert len(highlights) == 1 and highlights[0].color == GREEN
    assert len(marks_over(win.vdoc.page_annotations(0), (box,), Underline)) == 1


# ---- trim semantics (the merge machinery, not whole-mark deletion) -----------


def test_removing_a_layer_trims_to_the_clicked_words(win):
    """An underline wider than the clicked highlight loses only the covered span — the removal
    half of the merge (trim, split, drop), never whole-mark deletion beyond the words."""
    b0 = _word_box(win, n=0)
    extension = (b0[2] + 6.0, b0[1], b0[2] + 60.0, b0[3])  # a run to the word's right, same line
    win.vdoc.add_annotation(0, Underline((b0, extension)))
    win.vdoc.add_annotation(0, Highlight((b0,), color=YELLOW))
    win.view.reload()
    _row(_menu_over(win, b0), "Underline").remove_button.click()
    remaining = marks_over(win.vdoc.page_annotations(0), (extension,), Underline)
    assert len(remaining) == 1                            # the right-hand run survives…
    assert marks_over(win.vdoc.page_annotations(0), (b0,), Underline) == []  # …the span is gone


def test_remove_markup_model_semantics():
    """The model primitive directly: trim / split / drop, others untouched."""
    line = (10.0, 100.0, 90.0, 112.0)
    hl = Highlight(((30.0, 100.0, 60.0, 112.0),))
    ul = Underline((line,))
    annots = (ul, hl)
    # Erase the middle third of the underline → it splits into two runs; the highlight survives.
    result = remove_markup(annots, ((30.0, 100.0, 60.0, 112.0),), Underline)
    ULs = [m for m in result if isinstance(m, Underline)]
    assert len(ULs) == 1 and len(ULs[0].rects) == 2
    assert hl in result
    # Erasing the whole span drops the mark outright.
    result = remove_markup(annots, (line,), Underline)
    assert all(not isinstance(m, Underline) for m in result)
    # Nothing overlapping → the tuple comes back equal (no phantom undo step).
    assert remove_markup(annots, ((300.0, 300.0, 340.0, 312.0),), Underline) == annots
