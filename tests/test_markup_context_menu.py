"""Markup context menu (PLAN.md §GUI feature roadmap → R6, M76). Offscreen GUI.

Right-click on already-marked text offers Preview's change set: the curated highlight colours
(+ No Highlight) and Underline / Strike Out toggles — recolour a mark, or add/remove the other
markup layers on the same words, **in place** through the M59.10 merge machinery (recolour =
trim/absorb, never stacking). One undo step per action; the existing "Remove <noun>" still takes
the clicked mark itself.
"""

from __future__ import annotations

import pytest

from app import PdfApp
from model.page_edits import Highlight, Strikeout, Underline, marks_over, remove_markup
from store.settings import Settings
from viewer.markup_style import HIGHLIGHT_COLORS

YELLOW = HIGHLIGHT_COLORS[0][1]
GREEN = HIGHLIGHT_COLORS[1][1]


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


def _entry(menu, text: str):
    action = next((a for a in menu.actions() if a.text() == text), None)
    assert action is not None, f"no menu entry {text!r} in {[a.text() for a in menu.actions()]}"
    return action


def _highlights(win, page_index=0) -> list:
    return [a for a in win.vdoc.page_annotations(page_index) if isinstance(a, Highlight)]


# ---- recolour ----------------------------------------------------------------


def test_recolour_in_place_one_undo_step(win):
    box = _word_box(win)
    win.vdoc.add_annotation(0, Highlight((box,), color=YELLOW))
    win.view.reload()
    menu = _menu_over(win, box)
    swatches = {a.text(): a for a in menu.actions() if a.isCheckable()}
    assert swatches["Yellow"].isChecked()          # the mark's colour is ticked
    before = win.undo_stack.count()
    _entry(menu, "Green").trigger()
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
    _entry(_menu_over(win, box), "Yellow").trigger()
    assert win.undo_stack.count() == before        # nothing changed → nothing pushed


# ---- add / remove the other layers -------------------------------------------


def test_add_a_strikeout_over_a_highlight(win):
    box = _word_box(win)
    win.vdoc.add_annotation(0, Highlight((box,), color=YELLOW))
    win.view.reload()
    menu = _menu_over(win, box)
    strike = _entry(menu, "Strike Out")
    assert not strike.isChecked()                  # no strikeout layer yet
    before = win.undo_stack.count()
    strike.trigger()
    annots = win.vdoc.page_annotations(0)
    assert len(marks_over(annots, (box,), Strikeout)) == 1
    assert len(_highlights(win)) == 1              # the highlight is untouched
    assert win.undo_stack.count() == before + 1


def test_remove_one_layer_leaves_the_other(win):
    box = _word_box(win)
    win.vdoc.add_annotation(0, Highlight((box,), color=YELLOW))
    win.vdoc.add_annotation(0, Strikeout((box,)))
    win.view.reload()
    menu = _menu_over(win, box)
    strike = _entry(menu, "Strike Out")
    assert strike.isChecked()                      # the layer exists → ticked
    before = win.undo_stack.count()
    strike.trigger()                               # toggle off = remove that layer
    annots = win.vdoc.page_annotations(0)
    assert marks_over(annots, (box,), Strikeout) == []
    assert len(_highlights(win)) == 1              # the highlight layer survives
    assert win.undo_stack.count() == before + 1
    win.undo_stack.undo()
    assert len(marks_over(win.vdoc.page_annotations(0), (box,), Strikeout)) == 1


def test_underline_toggle_adds_in_the_sticky_line_colour(win):
    box = _word_box(win)
    win.vdoc.add_annotation(0, Highlight((box,), color=YELLOW))
    win.view.reload()
    _entry(_menu_over(win, box), "Underline").trigger()
    underlines = marks_over(win.vdoc.page_annotations(0), (box,), Underline)
    assert len(underlines) == 1
    assert underlines[0].color == win._markup_line_color


def test_no_highlight_from_an_underline_hit_removes_the_layer(win):
    """Right-click the underline where a highlight also sits: No Highlight strips the highlight
    layer and leaves the clicked underline."""
    box = _word_box(win)
    win.vdoc.add_annotation(0, Underline((box,)))
    win.vdoc.add_annotation(0, Highlight((box,), color=YELLOW))
    win.view.reload()
    # Hit the underline specifically (annotation_at may return either; find via the menu of the
    # underline's own removal entry when the underline is the hit — build the menu and use its
    # No Highlight entry either way: the layer verbs are the same from both hits).
    menu = _menu_over(win, box)
    no_hl = _entry(menu, "No Highlight")
    assert no_hl.isEnabled() and not no_hl.isChecked()
    no_hl.trigger()
    assert _highlights(win) == []
    assert len(marks_over(win.vdoc.page_annotations(0), (box,), Underline)) == 1


def test_swatch_on_an_underline_lays_a_highlight_under_it(win):
    box = _word_box(win)
    win.vdoc.add_annotation(0, Underline((box,)))
    win.view.reload()
    menu = _menu_over(win, box)
    no_hl = _entry(menu, "No Highlight")
    assert no_hl.isChecked() and not no_hl.isEnabled()   # no layer → tick says so, disabled
    _entry(menu, "Green").trigger()
    highlights = _highlights(win)
    assert len(highlights) == 1 and highlights[0].color == GREEN
    assert len(marks_over(win.vdoc.page_annotations(0), (box,), Underline)) == 1


# ---- trim semantics (the merge machinery, not whole-mark deletion) -----------


def test_removing_a_layer_trims_to_the_clicked_words(win):
    """An underline wider than the clicked highlight loses only the covered span — the removal
    half of the merge (trim, split, drop), never whole-mark deletion beyond the words."""
    b0, b1 = _word_box(win, n=0), _word_box(win, 0, 0)
    # Build a two-span underline on the same line: the word's box plus a run to its right.
    extension = (b0[2] + 6.0, b0[1], b0[2] + 60.0, b0[3])
    win.vdoc.add_annotation(0, Underline((b0, extension)))
    win.vdoc.add_annotation(0, Highlight((b0,), color=YELLOW))
    win.view.reload()
    _entry(_menu_over(win, b0), "Underline").trigger()   # remove the layer over the highlight
    remaining = marks_over(win.vdoc.page_annotations(0), (extension,), Underline)
    assert len(remaining) == 1                            # the right-hand run survives…
    assert marks_over(win.vdoc.page_annotations(0), (b0,), Underline) == []  # …the covered span is gone


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
