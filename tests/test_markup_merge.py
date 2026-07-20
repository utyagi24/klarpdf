"""Text markup merges instead of stacking (PLAN.md §GUI feature roadmap, M59.10 — R3).

Highlight / underline / strikeout are paint on text, not stacked objects: marking an already
marked span must fold into what is there. Before this, every pass appended a fresh descriptor —
two identical-looking highlights took two Removes to clear, and re-marking in a new colour left
the old one hidden underneath. The rules (see :func:`merge_markup`): same colour absorbs,
a different colour trims the span it covers, and everything is one undo step.
"""

from __future__ import annotations

import pytest

from app import PdfApp
from main_window import MainWindow
from model.page_edits import (
    Highlight,
    Redaction,
    Strikeout,
    TextBox,
    Underline,
    merge_markup,
)
from store.settings import Settings

YELLOW = (1.0, 0.86, 0.10)
GREEN = (0.10, 0.70, 0.30)

# One text line's band; x runs 70 → 220. A second line 20pt below.
LINE1 = (70.0, 66.0, 220.0, 80.0)
LINE2 = (70.0, 86.0, 220.0, 100.0)


def _bar(x0, x1, line=LINE1):
    return (x0, line[1], x1, line[3])


# ---- same colour: absorb ------------------------------------------------------


def test_remarking_the_same_span_is_a_no_op():
    before = (Highlight((LINE1,), color=YELLOW),)
    after = merge_markup(before, (LINE1,), Highlight, YELLOW)
    assert after == before                                # nothing to undo, nothing stacked


def test_same_colour_overlap_merges_into_one_mark():
    before = (Highlight((_bar(70, 150),), color=YELLOW),)
    after = merge_markup(before, (_bar(120, 220),), Highlight, YELLOW)
    (mark,) = after                                       # one mark, not two
    assert mark.rects == ((70.0, LINE1[1], 220.0, LINE1[3]),)


def test_same_colour_extends_beyond_the_original():
    before = (Highlight((_bar(100, 150),), color=YELLOW),)
    after = merge_markup(before, (_bar(70, 220),), Highlight, YELLOW)
    (mark,) = after
    assert mark.rects == ((70.0, LINE1[1], 220.0, LINE1[3]),)


def test_marking_one_line_of_a_multi_line_mark_absorbs_the_whole_mark():
    """Only line 2 is re-marked, but the mark it touches folds in whole — the result is visually
    identical and, crucially, a single descriptor."""
    before = (Highlight((LINE1, LINE2), color=YELLOW),)
    after = merge_markup(before, (LINE2,), Highlight, YELLOW)
    (mark,) = after
    assert sorted(mark.rects) == sorted((LINE1, LINE2))


def test_a_bridging_pass_chains_two_marks_into_one():
    before = (
        Highlight((_bar(70, 110),), color=YELLOW),
        Highlight((_bar(180, 220),), color=YELLOW),
    )
    after = merge_markup(before, (_bar(100, 190),), Highlight, YELLOW)
    (mark,) = after
    assert mark.rects == ((70.0, LINE1[1], 220.0, LINE1[3]),)


def test_disjoint_spans_stay_independent_marks():
    """Merging is scoped to what you actually overlapped. Two separate yellow spans on one line
    are two marks — removing one must not take the other with it."""
    before = (Highlight((_bar(70, 110),), color=YELLOW),)
    after = merge_markup(before, (_bar(180, 220),), Highlight, YELLOW)
    assert len(after) == 2


def test_a_round_tripped_colour_still_counts_as_the_same_colour():
    """A saved-and-reopened mark's colour comes back through PDF floats; an exact == would treat
    a re-highlight as a colour change and split the mark."""
    reopened = (Highlight((_bar(70, 150),), color=(1.0, 0.8600000143, 0.1000000015)),)
    after = merge_markup(reopened, (_bar(120, 220),), Highlight, YELLOW)
    assert len(after) == 1


# ---- different colour: trim ---------------------------------------------------


def test_full_coverage_in_a_new_colour_replaces_the_mark():
    before = (Highlight((LINE1,), color=YELLOW),)
    after = merge_markup(before, (LINE1,), Highlight, GREEN)
    (mark,) = after                                       # the yellow is gone, not buried
    assert mark.color == GREEN


def test_a_new_colour_through_the_middle_splits_the_old_mark():
    """The owner's rule: only the span you painted changes colour. The remainder keeps its own,
    which means the old mark splits into the runs either side."""
    before = (Highlight((_bar(70, 220),), color=YELLOW),)
    after = merge_markup(before, (_bar(120, 170),), Highlight, GREEN)
    yellow = [m for m in after if m.color == YELLOW]
    green = [m for m in after if m.color == GREEN]
    assert len(yellow) == 1 and len(green) == 1
    assert sorted(yellow[0].rects) == [_bar(70, 120), _bar(170, 220)]
    assert green[0].rects == (_bar(120, 170),)


def test_a_new_colour_over_one_end_trims_rather_than_splits():
    before = (Highlight((_bar(70, 220),), color=YELLOW),)
    after = merge_markup(before, (_bar(150, 260),), Highlight, GREEN)
    yellow = [m for m in after if m.color == YELLOW]
    assert yellow[0].rects == (_bar(70, 150),)


def test_a_sliver_left_behind_is_dropped_not_left_as_a_hairline():
    before = (Highlight((_bar(70, 220),), color=YELLOW),)
    after = merge_markup(before, (_bar(70.5, 220),), Highlight, GREEN)
    assert [m.color for m in after] == [GREEN]


# ---- scoping: types stay independent -----------------------------------------


def test_underline_does_not_merge_with_a_highlight_on_the_same_words():
    """A yellow wash and a red underline over the same line are legitimately separate layers."""
    before = (Highlight((LINE1,), color=YELLOW),)
    after = merge_markup(before, (LINE1,), Underline, GREEN)
    assert {type(m).__name__ for m in after} == {"Highlight", "Underline"}


def test_other_annotations_pass_through_untouched():
    box = TextBox((10.0, 10.0, 90.0, 40.0), "note")
    redaction = Redaction((LINE1,))
    after = merge_markup((box, redaction), (LINE1,), Highlight, YELLOW)
    assert box in after and redaction in after


def test_strikeout_merges_on_its_own_type():
    before = (Strikeout((_bar(70, 150),), color=GREEN),)
    after = merge_markup(before, (_bar(120, 220),), Strikeout, GREEN)
    assert len(after) == 1


def test_the_merged_mark_takes_the_absorbed_mark_s_z_position():
    """Re-marking must not shuffle paint order against a co-located mark of another type."""
    under = Underline((LINE1,), color=GREEN)
    before = (Highlight((_bar(70, 150),), color=YELLOW), under)
    after = merge_markup(before, (_bar(120, 220),), Highlight, YELLOW)
    assert [type(m).__name__ for m in after] == ["Highlight", "Underline"]


def test_no_existing_mark_just_adds_on_top():
    under = Underline((LINE1,), color=GREEN)
    after = merge_markup((under,), (LINE1,), Highlight, YELLOW)
    assert [type(m).__name__ for m in after] == ["Underline", "Highlight"]


def test_empty_selection_changes_nothing():
    before = (Highlight((LINE1,), color=YELLOW),)
    assert merge_markup(before, (), Highlight, GREEN) is before


# ---- the GUI path (offscreen) -------------------------------------------------


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    return qapp


@pytest.fixture
def win(app, a_pdf):
    w = MainWindow(app, a_pdf, app.settings)
    yield w
    w.undo_stack.setClean()
    w.close()


def _select_first_word(win):
    ref = win.vdoc.ordered[0]
    page = win.vdoc.sources[ref.source_id][ref.source_page_index]
    word = page.get_text("words")[0]
    center = win.view.scene_rect_for_box(0, word[:4]).center()
    assert win.view.selection.select_word_at(center) is True


def test_highlighting_twice_leaves_one_mark(win):
    for _ in range(2):
        _select_first_word(win)
        win._highlight_selection()
    marks = [a for a in win.vdoc.page_annotations(0) if isinstance(a, Highlight)]
    assert len(marks) == 1


def test_one_remove_clears_a_twice_highlighted_word(win):
    """The bug as reported: the second pass used to stack, so Remove had to be run twice."""
    for _ in range(2):
        _select_first_word(win)
        win._highlight_selection()
    page_index, mark = win.view.annotations.annotation_at(
        win.view.scene_rect_for_box(0, win.vdoc.page_annotations(0)[0].rects[0]).center()
    )
    win._remove_annotation(page_index, mark)
    assert win.vdoc.page_annotations(0) == ()


def test_rehighlighting_in_a_new_colour_recolours_in_place(win):
    _select_first_word(win)
    win._highlight_selection()
    win._set_highlight_color(GREEN)
    _select_first_word(win)
    win._highlight_selection()
    marks = [a for a in win.vdoc.page_annotations(0) if isinstance(a, Highlight)]
    assert len(marks) == 1 and marks[0].color == GREEN


def test_a_merging_pass_is_one_undo_step(win):
    _select_first_word(win)
    win._highlight_selection()
    win._set_highlight_color(GREEN)
    _select_first_word(win)
    win._highlight_selection()
    win.undo_stack.undo()
    marks = [a for a in win.vdoc.page_annotations(0) if isinstance(a, Highlight)]
    assert len(marks) == 1 and marks[0].color != GREEN    # back to the first colour, in one step


def test_an_identical_second_pass_pushes_no_undo_step(win):
    _select_first_word(win)
    win._highlight_selection()
    depth = win.undo_stack.count()
    _select_first_word(win)
    win._highlight_selection()
    assert win.undo_stack.count() == depth                # nothing changed → nothing to undo
    assert win.view.selection.selected_words() == []      # but the selection still cleared


def test_redaction_still_stacks_plainly(win):
    """Redaction deliberately stayed off the merge path — destructive, colourless, and
    overlapping rects already union at apply time."""
    for _ in range(2):
        _select_first_word(win)
        win._redact_selection()
    assert len([a for a in win.vdoc.page_annotations(0) if isinstance(a, Redaction)]) == 2
