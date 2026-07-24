"""Multi-object selection — Objects mode: marquee + Ctrl-click, group style / move / delete (M59.6).

The single-object selection of M59 (click a mark) grows into a group: a dedicated **Objects** mode
where a drag marquees the drawn marks inside, Ctrl-click toggles one, and dragging a member moves
the whole group — then the M59.5 picker restyles the group in one undo step. One page per group.
Copy/paste of a multi-selection is a later pass. Offscreen GUI, driving the overlay like the mouse
routing does.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from app import PdfApp
from main_window import MainWindow
from model.page_edits import InkStroke, Line, Shape
from store.settings import Settings
from viewer.tools import InteractionMode


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


def _scene(win, x: float, y: float):
    return win.view.scene_rect_for_box(0, (x, y, x + 0.01, y + 0.01)).center()


def _shapes(win, *rects, color=(0.86, 0.10, 0.10)):
    """Add rect shapes to page 0, reload, and return them as they live in the model."""
    for r in rects:
        win.vdoc.add_annotation(0, Shape("rect", r, color=color, width=2.0))
    win.view.reload()
    return [a for a in win.vdoc.page_annotations(0) if isinstance(a, Shape)]


def _sel_marks(win):
    return [m for _p, m in win.view.annotations.selected_objects]


# ---- marquee + Ctrl-click selection -----------------------------------------


def test_marquee_selects_the_marks_it_covers(win):
    _shapes(win, (100, 100, 160, 140), (200, 100, 260, 140))
    ov = win.view.annotations
    assert ov.begin_marquee(_scene(win, 90, 90)) is True
    ov.update_marquee(_scene(win, 270, 150))
    ov.finish_marquee()
    assert len(ov.selected_objects) == 2


def test_marquee_leaves_out_marks_outside_the_box(win):
    _shapes(win, (100, 100, 160, 140), (400, 100, 460, 140))
    ov = win.view.annotations
    ov.begin_marquee(_scene(win, 90, 90))
    ov.update_marquee(_scene(win, 200, 150))          # only the first shape is inside
    ov.finish_marquee()
    marks = _sel_marks(win)
    assert len(marks) == 1 and marks[0].rect == pytest.approx((100, 100, 160, 140))


def test_marquee_catches_a_zero_height_line_via_padding(win):
    win.vdoc.add_annotation(0, Line((100.0, 300.0), (220.0, 300.0)))  # horizontal: no height
    win.view.reload()
    ov = win.view.annotations
    ov.select_in_rect(0, (90, 290, 240, 310))
    assert any(isinstance(m, Line) for m in _sel_marks(win))


def test_ctrl_marquee_unions_with_the_current_group(win):
    shapes = _shapes(win, (100, 100, 160, 140), (200, 100, 260, 140), (300, 100, 360, 140))
    ov = win.view.annotations
    ov.select_object(0, shapes[0])
    ov.select_in_rect(0, (190, 90, 370, 150), add=True)   # add the other two
    assert len(ov.selected_objects) == 3


def test_ctrl_click_toggles_membership(win):
    shapes = _shapes(win, (100, 100, 160, 140), (200, 100, 260, 140))
    ov = win.view.annotations
    ov.select_object(0, shapes[0])
    ov.toggle_object(0, shapes[1])            # add
    assert len(ov.selected_objects) == 2
    ov.toggle_object(0, shapes[0])            # remove
    assert _sel_marks(win) == [shapes[1]]


def test_a_group_lives_on_one_page(win):
    win.vdoc.add_annotation(0, Shape("rect", (100.0, 100.0, 160.0, 140.0)))
    win.vdoc.add_annotation(1, Shape("rect", (100.0, 100.0, 160.0, 140.0)))
    win.view.reload()
    s0 = win.vdoc.page_annotations(0)[0]
    s1 = win.vdoc.page_annotations(1)[0]
    ov = win.view.annotations
    ov.select_object(0, s0)
    ov.toggle_object(1, s1)                   # a different page → fresh selection, not a union
    assert ov.selected_objects == [(1, s1)]


# ---- group operations (one undo step each) ----------------------------------


def test_group_restyle_is_one_undo_step(win):
    shapes = _shapes(win, (100, 100, 160, 140), (200, 100, 260, 140))
    ov = win.view.annotations
    ov.select_in_rect(0, (90, 90, 270, 150))
    win._colors_button._set_color((0.0, 0.0, 1.0))
    recoloured = [a for a in win.vdoc.page_annotations(0) if isinstance(a, Shape)]
    assert all(s.color == pytest.approx((0.0, 0.0, 1.0)) for s in recoloured)
    assert win.undo_stack.undoText() == "Restyle 2 objects"
    win.undo_stack.undo()
    assert all(s.color == pytest.approx((0.86, 0.10, 0.10))
               for s in win.vdoc.page_annotations(0) if isinstance(s, Shape))


def test_group_move_is_one_undo_step(win):
    _shapes(win, (100, 100, 160, 140), (200, 100, 260, 140))
    ov = win.view.annotations
    ov.select_in_rect(0, (90, 90, 270, 150))
    assert ov.begin_move(_scene(win, 130, 120)) is True     # grab a member → group move
    ov.update_move(_scene(win, 160, 150))                   # +30, +30
    ov.finish_move()
    rects = sorted(s.rect for s in win.vdoc.page_annotations(0) if isinstance(s, Shape))
    assert rects[0] == pytest.approx((130, 130, 190, 170), abs=1.0)
    assert rects[1] == pytest.approx((230, 130, 290, 170), abs=1.0)
    assert win.undo_stack.undoText() == "Move 2 objects"


def test_group_delete_is_one_undo_step(win):
    _shapes(win, (100, 100, 160, 140), (200, 100, 260, 140))
    ov = win.view.annotations
    ov.select_in_rect(0, (90, 90, 270, 150))
    assert ov.remove_selected_objects() is True
    assert [a for a in win.vdoc.page_annotations(0) if isinstance(a, Shape)] == []
    assert win.undo_stack.undoText() == "Delete 2 objects"
    win.undo_stack.undo()
    assert len([a for a in win.vdoc.page_annotations(0) if isinstance(a, Shape)]) == 2


def test_group_move_keeps_the_group_selected(win):
    _shapes(win, (100, 100, 160, 140), (200, 100, 260, 140))
    ov = win.view.annotations
    ov.select_in_rect(0, (90, 90, 270, 150))
    ov.begin_move(_scene(win, 130, 120))
    ov.update_move(_scene(win, 150, 130))
    ov.finish_move()
    assert len(ov.selected_objects) == 2                    # the moved group stays selected


def test_restyle_skips_a_text_box_in_a_mixed_group(win):
    """A marquee can grab a text box too, but the stroke picker only restyles the drawn marks —
    the text box (its own format bar) rides along for move/delete, untouched by colour."""
    from model.page_edits import TextBox

    win.vdoc.add_annotation(0, Shape("rect", (100.0, 100.0, 160.0, 140.0)))
    win.vdoc.add_annotation(0, TextBox((200.0, 100.0, 300.0, 140.0), "note", color=(0.0, 0.0, 0.0)))
    win.view.reload()
    win.view.annotations.select_in_rect(0, (90, 90, 320, 150))
    win._colors_button._set_color((0.0, 0.0, 1.0))
    shape = [a for a in win.vdoc.page_annotations(0) if isinstance(a, Shape)][0]
    box = [a for a in win.vdoc.page_annotations(0) if isinstance(a, TextBox)][0]
    assert shape.color == pytest.approx((0.0, 0.0, 1.0))    # drawn mark recoloured
    assert box.color == pytest.approx((0.0, 0.0, 0.0))      # text box left alone
    assert win.undo_stack.undoText() == "Restyle shape"     # one drawn mark changed → singular


# ---- single-object semantics still hold + the mode wiring -------------------


def test_selected_object_is_none_for_a_group(win):
    shapes = _shapes(win, (100, 100, 160, 140), (200, 100, 260, 140))
    ov = win.view.annotations
    ov.select_object(0, shapes[0])
    assert ov.selected_object == (0, shapes[0])             # exactly one → the single seam works
    ov.toggle_object(0, shapes[1])
    assert ov.selected_object is None                       # a group → the single seam is None
    assert len(ov.selected_objects) == 2


# ---- precise hit-testing: a mark inside a pen loop stays reachable ----------


def _loop_around_a_box(win):
    """A rectangle + a diagonal line, then a closed pen loop drawn around the rectangle. The loop
    is added last, so it is topmost — the exact case where bounding-box hit-testing made it swallow
    every click inside it."""
    win.vdoc.add_annotation(0, Shape("rect", (100.0, 100.0, 160.0, 140.0)))
    win.vdoc.add_annotation(0, Line((300.0, 300.0), (400.0, 400.0)))
    loop = InkStroke((((40.0, 40.0), (240.0, 40.0), (240.0, 220.0), (40.0, 220.0), (40.0, 40.0)),))
    win.vdoc.add_annotation(0, loop)
    win.view.reload()


def test_click_inside_the_loop_reaches_the_inner_box(win):
    _loop_around_a_box(win)
    hit = win.view.annotations.drawn_mark_at(_scene(win, 130, 120))   # rect centre, loop interior
    assert hit is not None and isinstance(hit[1], Shape)              # the box, not the pen loop


def test_click_on_the_loop_stroke_selects_the_loop(win):
    _loop_around_a_box(win)
    hit = win.view.annotations.drawn_mark_at(_scene(win, 40, 130))    # on the loop's left edge
    assert hit is not None and isinstance(hit[1], InkStroke)


def test_click_in_a_lines_box_but_off_the_segment_misses_it(win):
    _loop_around_a_box(win)
    # Inside the diagonal line's bounding box but far from the segment → no longer a false hit.
    assert win.view.annotations.drawn_mark_at(_scene(win, 305, 395)) is None
    hit = win.view.annotations.drawn_mark_at(_scene(win, 350, 350))   # on the segment
    assert hit is not None and isinstance(hit[1], Line)


def test_objects_mode_action_switches_mode(win):
    win._a_objects.trigger()
    assert win.view._mode is InteractionMode.OBJECT
    win._a_select.trigger()
    assert win.view._mode is InteractionMode.SELECT


def test_object_mode_press_on_empty_starts_a_marquee(win):
    _shapes(win, (100, 100, 160, 140))
    win.view.set_mode(InteractionMode.OBJECT)
    # A press far from any mark begins the rubber-band (routed by PdfView in object mode).
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QPointF, QEvent

    pos = QPointF(win.view.mapFromScene(_scene(win, 40, 40)))
    glob = QPointF(win.view.viewport().mapToGlobal(pos.toPoint()))
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, pos, glob, Qt.MouseButton.LeftButton,
                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    win.view.mousePressEvent(ev)
    assert win.view.annotations.marqueeing is True
    win.view.annotations.finish_marquee()   # tidy up the band
