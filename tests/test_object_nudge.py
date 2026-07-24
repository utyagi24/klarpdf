"""Arrow-key object nudge (PLAN.md §GUI roadmap, M78.2).

Arrow keys move the object selection a step at a time — 1 pt per press, Shift = 10 pt — clamped to
the page; with nothing selected the arrows fall through to the view's normal scrolling. Every
movable object nudges (text boxes included), a group shifts as one step, and the undo is keyed to
held-vs-tapped: a held key's auto-repeat coalesces into a single undo step (so the first Undo never
throws the object back to its origin), while each discrete tap is its own step.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent

from app import PdfApp
from main_window import MainWindow
from model.page_edits import Line, Shape, TextBox
from store.settings import Settings


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


def _add(win, *marks):
    for mark in marks:
        win.vdoc.add_annotation(0, mark)
    win.view.reload()
    return win.vdoc.page_annotations(0)


def _only(win, cls):
    marks = [a for a in win.vdoc.page_annotations(0) if isinstance(a, cls)]
    assert len(marks) == 1
    return marks[0]


def _key(win, key, *, shift=False, autorep=False):
    mods = Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier
    win.view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, mods, "", autorep))


# ---- the step size -----------------------------------------------------------


def test_a_tap_nudges_one_point(win):
    _add(win, Shape("rect", (100.0, 100.0, 160.0, 140.0)))
    win.view.annotations.select_object(0, _only(win, Shape))
    _key(win, Qt.Key.Key_Right)
    assert _only(win, Shape).rect == pytest.approx((101, 100, 161, 140))
    _key(win, Qt.Key.Key_Down)
    assert _only(win, Shape).rect == pytest.approx((101, 101, 161, 141))


def test_shift_nudges_ten_points(win):
    _add(win, Shape("rect", (100.0, 100.0, 160.0, 140.0)))
    win.view.annotations.select_object(0, _only(win, Shape))
    _key(win, Qt.Key.Key_Left, shift=True)
    assert _only(win, Shape).rect == pytest.approx((90, 100, 150, 140))
    _key(win, Qt.Key.Key_Up, shift=True)
    assert _only(win, Shape).rect == pytest.approx((90, 90, 150, 130))


def test_a_text_box_nudges_too(win):
    _add(win, TextBox((100.0, 100.0, 200.0, 140.0), "note"))
    win.view.annotations.select_object(0, _only(win, TextBox))
    _key(win, Qt.Key.Key_Right)
    assert _only(win, TextBox).rect == pytest.approx((101, 100, 201, 140))


# ---- a group nudges as one step ----------------------------------------------


def test_a_group_nudges_as_one_step(win):
    _add(win, Shape("rect", (100.0, 100.0, 160.0, 140.0)),
         Line((200.0, 200.0), (260.0, 240.0)))
    ov = win.view.annotations
    marks = list(win.vdoc.page_annotations(0))
    ov.select_objects(0, marks)
    _key(win, Qt.Key.Key_Right, shift=True)
    shape = _only(win, Shape)
    line = _only(win, Line)
    assert shape.rect == pytest.approx((110, 100, 170, 140))
    assert line.start == pytest.approx((210, 200)) and line.end == pytest.approx((270, 240))
    assert win.undo_stack.undoText() == "Move 2 objects"
    win.undo_stack.undo()                                  # one step returns both
    assert _only(win, Shape).rect == pytest.approx((100, 100, 160, 140))
    assert _only(win, Line).start == pytest.approx((200, 200))


# ---- held vs tapped: the undo coalescing -------------------------------------


def test_held_key_sweep_is_one_undo(win):
    _add(win, Shape("rect", (100.0, 100.0, 160.0, 140.0)))
    win.view.annotations.select_object(0, _only(win, Shape))
    _key(win, Qt.Key.Key_Right)                            # the initial (non-repeat) press
    for _ in range(4):
        _key(win, Qt.Key.Key_Right, autorep=True)         # the held-key auto-repeats
    assert _only(win, Shape).rect == pytest.approx((105, 100, 165, 140))
    win.undo_stack.undo()                                  # a single Undo…
    assert _only(win, Shape).rect == pytest.approx((100, 100, 160, 140))  # …back to the origin


def test_each_tap_is_its_own_undo(win):
    _add(win, Shape("rect", (100.0, 100.0, 160.0, 140.0)))
    win.view.annotations.select_object(0, _only(win, Shape))
    for _ in range(3):
        _key(win, Qt.Key.Key_Right)                        # three discrete taps (no auto-repeat)
    assert _only(win, Shape).rect == pytest.approx((103, 100, 163, 140))
    win.undo_stack.undo()
    assert _only(win, Shape).rect == pytest.approx((102, 100, 162, 140))  # only the last tap undone


def test_a_new_tap_after_a_held_sweep_does_not_merge_in(win):
    """The tap that follows a held sweep is a fresh undo step — auto-repeat gates the merge, not
    mere adjacency on the stack."""
    _add(win, Shape("rect", (100.0, 100.0, 160.0, 140.0)))
    win.view.annotations.select_object(0, _only(win, Shape))
    _key(win, Qt.Key.Key_Right)
    _key(win, Qt.Key.Key_Right, autorep=True)              # sweep: two coalesced into one step
    _key(win, Qt.Key.Key_Right)                            # a separate tap
    assert _only(win, Shape).rect == pytest.approx((103, 100, 163, 140))
    win.undo_stack.undo()                                  # undoes only the trailing tap
    assert _only(win, Shape).rect == pytest.approx((102, 100, 162, 140))


# ---- clamping and the no-selection no-op -------------------------------------


def test_nudge_clamps_at_the_page_edge(win):
    pw, _ph = win.view._unrotated_size(0)
    _add(win, Shape("rect", (pw - 60.0, 100.0, pw - 1.0, 140.0)))  # 1 pt from the right edge
    win.view.annotations.select_object(0, _only(win, Shape))
    _key(win, Qt.Key.Key_Right, shift=True)               # a 10 pt push, but only 1 pt of room
    assert _only(win, Shape).rect[2] == pytest.approx(pw)  # flush to the edge, not past it


def test_nudge_flush_against_the_edge_is_a_no_op(win):
    pw, _ph = win.view._unrotated_size(0)
    _add(win, Shape("rect", (pw - 60.0, 100.0, pw, 140.0)))  # already flush right
    win.view.annotations.select_object(0, _only(win, Shape))
    before = win.undo_stack.index()
    assert win.view.annotations.nudge_selection(5.0, 0.0) is False
    assert win.undo_stack.index() == before               # nothing pushed


def test_arrow_with_no_selection_does_not_nudge(win):
    """With nothing selected the arrow keys are left for the view's scrolling — nudge is a no-op and
    reports it didn't consume the key."""
    _add(win, Shape("rect", (100.0, 100.0, 160.0, 140.0)))
    ov = win.view.annotations
    ov.clear_object_selection()
    before = win.undo_stack.index()
    assert ov.nudge_selection(1.0, 0.0) is False
    _key(win, Qt.Key.Key_Right)
    assert _only(win, Shape).rect == pytest.approx((100, 100, 160, 140))
    assert win.undo_stack.index() == before
