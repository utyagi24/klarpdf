"""Object z-order — Bring to Front / Forward / Backward / Send to Back (PLAN.md, M59.8).

A page's annotation tuple **is** its z-order: later entries paint on top (in the viewer and, via
``apply_annotations``, in the saved PDF), and the hit-tests walk it reversed so the topmost wins.
So raising a mark is a pure list reorder — one undo step, moving paint order and click order
together. Complements M59.6's geometry hit-testing: that lets you *reach* an inner mark, this
decides what sits on top when marks genuinely overlap.
"""

from __future__ import annotations

import pytest

from app import PdfApp
from main_window import MainWindow
from model.page_edits import Line, Shape, reorder_marks
from model.virtual_document import VirtualDocument
from store.settings import Settings


# ---- the reorder primitive (headless) ---------------------------------------


def _names(marks):
    return [m.kind for m in marks]


def _row(*kinds):
    """Shapes tagged by their ``kind`` so order is easy to read in assertions."""
    return tuple(Shape(k, (float(i * 10), 0.0, float(i * 10 + 5), 5.0))
                 for i, k in enumerate(kinds))


def test_bring_to_front_moves_to_the_end():
    marks = _row("a", "b", "c")
    assert _names(reorder_marks(marks, [marks[0]], "front")) == ["b", "c", "a"]


def test_send_to_back_moves_to_the_start():
    marks = _row("a", "b", "c")
    assert _names(reorder_marks(marks, [marks[2]], "back")) == ["c", "a", "b"]


def test_forward_and_backward_step_one_place():
    marks = _row("a", "b", "c")
    assert _names(reorder_marks(marks, [marks[0]], "forward")) == ["b", "a", "c"]
    assert _names(reorder_marks(marks, [marks[2]], "backward")) == ["a", "c", "b"]


def test_a_group_keeps_its_relative_order():
    marks = _row("a", "b", "c", "d")
    out = reorder_marks(marks, [marks[0], marks[2]], "front")
    assert _names(out) == ["b", "d", "a", "c"]        # a before c, as they were


def test_a_contiguous_run_steps_together():
    marks = _row("a", "b", "c", "d")
    out = reorder_marks(marks, [marks[1], marks[2]], "forward")
    assert _names(out) == ["a", "d", "b", "c"]        # b,c both moved past d, order kept


def test_already_at_the_end_is_a_no_op():
    marks = _row("a", "b", "c")
    assert reorder_marks(marks, [marks[2]], "front") is marks or \
        reorder_marks(marks, [marks[2]], "front") == marks
    assert reorder_marks(marks, [marks[0]], "back") == marks
    assert reorder_marks(marks, [marks[2]], "forward") == marks
    assert reorder_marks(marks, [marks[0]], "backward") == marks


def test_an_unknown_action_or_absent_mark_changes_nothing():
    marks = _row("a", "b")
    assert reorder_marks(marks, [marks[0]], "sideways") == marks
    assert reorder_marks(marks, [Shape("z", (99.0, 99.0, 100.0, 100.0))], "front") == marks


def test_an_equal_but_distinct_copy_is_still_found():
    """Descriptors are value objects, so a caller can hand back an equal-but-distinct copy —
    the reorder resolves it rather than silently doing nothing (the M59.7 lesson)."""
    marks = _row("a", "b", "c")
    stale = Shape(marks[0].kind, marks[0].rect)
    assert stale is not marks[0] and stale == marks[0]
    assert _names(reorder_marks(marks, [stale], "front")) == ["b", "c", "a"]


def test_set_annotations_reorders_and_dirties(a_pdf):
    vd = VirtualDocument.from_path(a_pdf)
    first, second = Shape("rect", (0.0, 0.0, 5.0, 5.0)), Line((1.0, 1.0), (2.0, 2.0))
    vd.add_annotation(0, first)
    vd.add_annotation(0, second)
    vd.dirty = False
    vd.set_annotations(0, (second, first))
    assert vd.page_annotations(0) == (second, first)
    assert vd.dirty is True
    vd.dirty = False
    vd.set_annotations(0, (second, first))            # same order → not an edit
    assert vd.dirty is False


# ---- the window wiring (offscreen GUI) --------------------------------------


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
    w = MainWindow(app, a_pdf, app.settings)
    yield w
    w.undo_stack.setClean()
    w.close()


def _three_shapes(win):
    for i, kind in enumerate(("rect", "ellipse", "rect")):
        win.vdoc.add_annotation(0, Shape(kind, (100.0 + i, 100.0, 160.0 + i, 140.0)))
    win.view.reload()
    return list(win.vdoc.page_annotations(0))


def test_bring_to_front_is_one_undo_step(win):
    shapes = _three_shapes(win)
    win.view.annotations.select_object(0, shapes[0])
    assert win._reorder_objects("front") is True
    assert win.vdoc.page_annotations(0)[-1].rect == shapes[0].rect   # now topmost
    assert win.undo_stack.undoText() == "Bring to front"
    win.undo_stack.undo()
    assert win.vdoc.page_annotations(0)[0].rect == shapes[0].rect    # back to the bottom


def test_send_to_back_and_step_actions(win):
    shapes = _three_shapes(win)
    win.view.annotations.select_object(0, shapes[2])
    assert win._reorder_objects("back") is True
    assert win.vdoc.page_annotations(0)[0].rect == shapes[2].rect
    assert win.undo_stack.undoText() == "Send to back"
    win.view.annotations.select_object(0, win.vdoc.page_annotations(0)[0])
    assert win._reorder_objects("forward") is True
    assert win.vdoc.page_annotations(0)[1].rect == shapes[2].rect    # stepped up one


def test_reorder_keeps_the_objects_selected(win):
    shapes = _three_shapes(win)
    win.view.annotations.select_object(0, shapes[0])
    win._reorder_objects("front")
    sel = win.view.annotations.selected_objects
    assert len(sel) == 1 and sel[0][1].rect == shapes[0].rect


def test_a_group_raises_together(win):
    shapes = _three_shapes(win)
    win.view.annotations.select_objects(0, [shapes[0], shapes[1]])
    assert win._reorder_objects("front") is True
    order = [s.rect for s in win.vdoc.page_annotations(0)]
    assert order == [shapes[2].rect, shapes[0].rect, shapes[1].rect]
    assert len(win.view.annotations.selected_objects) == 2


def test_reorder_with_no_selection_does_nothing(win):
    _three_shapes(win)
    win.view.annotations.clear_object_selection()
    at = win.undo_stack.index()
    assert win._reorder_objects("front") is False
    assert win.undo_stack.index() == at


def test_reordering_changes_which_mark_a_click_hits(win):
    """The payoff: z-order drives the topmost-wins hit test, so raising a mark makes it the one
    you grab where they overlap."""
    win.vdoc.add_annotation(0, Shape("rect", (100.0, 100.0, 200.0, 200.0)))
    win.vdoc.add_annotation(0, Shape("ellipse", (100.0, 100.0, 200.0, 200.0)))
    win.view.reload()
    under, over = win.vdoc.page_annotations(0)
    pt = win.view.scene_rect_for_box(0, (150.0, 150.0, 150.01, 150.01)).center()
    assert win.view.annotations.drawn_mark_at(pt)[1].kind == over.kind      # topmost wins
    win.view.annotations.select_object(0, under)
    win._reorder_objects("front")
    assert win.view.annotations.drawn_mark_at(pt)[1].kind == under.kind     # now it's on top


def test_context_menu_offers_z_order_and_selects_the_mark(win):
    shapes = _three_shapes(win)
    win.view.annotations.clear_object_selection()
    centre = win.view.scene_rect_for_box(0, shapes[0].rect).center()
    menu = win._view_context_menu(centre)
    labels = [a.text() for a in menu.actions() if not a.isSeparator()]
    for expected in ("Bring to Front", "Bring Forward", "Send Backward", "Send to Back"):
        assert expected in labels
    # right-clicking a mark selects it, so the verbs have an unambiguous target
    assert len(win.view.annotations.selected_objects) == 1


def test_context_menu_disables_the_end_it_is_already_at(win):
    shapes = _three_shapes(win)
    top = shapes[-1]
    menu = win._view_context_menu(win.view.scene_rect_for_box(0, top.rect).center())
    by_label = {a.text(): a for a in menu.actions() if not a.isSeparator()}
    assert by_label["Bring to Front"].isEnabled() is False    # already topmost
    assert by_label["Send to Back"].isEnabled() is True


def test_z_order_actions_carry_shortcuts(win):
    keys = {k: a.shortcut().toString() for k, a in win._a_z_actions.items()}
    assert keys["front"] and keys["back"] and keys["forward"] and keys["backward"]
    assert keys["front"] != keys["forward"]
