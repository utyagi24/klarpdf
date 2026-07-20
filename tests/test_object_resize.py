"""Object resize — selection handles, single + group scaling (PLAN.md §GUI roadmap, M59.7).

Builds on M59.6's selection: handles appear around what's selected, and dragging one scales it.
A lone **line** gets endpoint handles (re-aim it by an end — its box is degenerate when
axis-aligned); a lone **text box** gets none (its size is font-driven, owned by the format bar);
everything else, including any group, gets the eight-handle box. A group scales every member about
the selection's bounds, so its internal arrangement is preserved. One undo step each.

The handle component (``viewer/resize_handles``) is deliberately standalone — M62 (stamp placement)
and M69 (field creation) are scheduled to reuse it.
"""

from __future__ import annotations

import pytest

from app import PdfApp
from main_window import MainWindow
from model.page_edits import InkStroke, Line, Shape, TextBox, scale_mark
from store.settings import Settings
from viewer.resize_handles import MIN_SIZE, resized_rect


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


def _add(win, *marks):
    for mark in marks:
        win.vdoc.add_annotation(0, mark)
    win.view.reload()
    return win.vdoc.page_annotations(0)


def _only(win, cls):
    marks = [a for a in win.vdoc.page_annotations(0) if isinstance(a, cls)]
    assert len(marks) == 1
    return marks[0]


# ---- the model primitive -----------------------------------------------------


def test_scale_mark_scales_each_geometry():
    shape = Shape("rect", (100.0, 100.0, 200.0, 200.0))
    assert scale_mark(shape, 2.0, 0.5, 100.0, 100.0).rect == pytest.approx((100, 100, 300, 150))
    line = Line((100.0, 100.0), (200.0, 200.0))
    scaled = scale_mark(line, 2.0, 2.0, 100.0, 100.0)
    assert scaled.start == pytest.approx((100, 100)) and scaled.end == pytest.approx((300, 300))
    ink = InkStroke((((100.0, 100.0), (150.0, 200.0)),))
    pts = scale_mark(ink, 2.0, 1.0, 100.0, 100.0).paths[0]
    assert pts[0] == pytest.approx((100.0, 100.0))
    assert pts[1] == pytest.approx((200.0, 200.0))


def test_scale_mark_repositions_a_text_box_without_stretching_it():
    """A text box hugs its text, so its size is a function of text + font (the format bar's job).
    In a group scale it travels along but keeps its dimensions."""
    box = TextBox((100.0, 100.0, 200.0, 140.0), "note")
    moved = scale_mark(box, 2.0, 2.0, 100.0, 100.0)
    assert moved.rect == pytest.approx((100, 100, 200, 140))       # same size…
    moved = scale_mark(box, 2.0, 2.0, 0.0, 0.0)
    assert moved.rect == pytest.approx((200, 200, 300, 240))       # …new position


def test_scale_mark_returns_none_for_an_unresizable_mark():
    from model.page_edits import Highlight

    assert scale_mark(Highlight(((0.0, 0.0, 10.0, 10.0),)), 2.0, 2.0, 0.0, 0.0) is None


# ---- the reusable rect maths -------------------------------------------------


def test_resized_rect_moves_the_dragged_edges():
    rect = (100.0, 100.0, 200.0, 150.0)
    assert resized_rect(rect, "se", 50.0, 50.0) == pytest.approx((100, 100, 250, 200))
    assert resized_rect(rect, "nw", 20.0, 10.0) == pytest.approx((120, 110, 200, 150))
    assert resized_rect(rect, "e", 25.0, 99.0) == pytest.approx((100, 100, 225, 150))  # y ignored
    assert resized_rect(rect, "n", 99.0, -20.0) == pytest.approx((100, 80, 200, 150))  # x ignored


def test_resized_rect_keeps_aspect_with_shift():
    rect = (0.0, 0.0, 100.0, 50.0)                       # 2:1
    out = resized_rect(rect, "se", 100.0, 0.0, keep_aspect=True)
    assert out == pytest.approx((0, 0, 200, 100))        # grew proportionally, not just in x


def test_resized_rect_never_collapses():
    out = resized_rect((0.0, 0.0, 100.0, 50.0), "se", -500.0, -500.0)
    assert out[2] - out[0] >= MIN_SIZE and out[3] - out[1] >= MIN_SIZE


# ---- which handles appear ----------------------------------------------------


def test_a_shape_gets_the_eight_handle_box(win):
    _add(win, Shape("rect", (100.0, 100.0, 160.0, 140.0)))
    win.view.annotations.select_object(0, _only(win, Shape))
    assert set(win.view.annotations._handles._items) == {
        "nw", "n", "ne", "e", "se", "s", "sw", "w"
    }


def test_a_line_gets_endpoint_handles(win):
    _add(win, Line((100.0, 200.0), (220.0, 240.0)))
    win.view.annotations.select_object(0, _only(win, Line))
    assert set(win.view.annotations._handles._items) == {"p0", "p1"}


def test_a_text_box_gets_no_handles(win):
    _add(win, TextBox((100.0, 100.0, 200.0, 140.0), "note"))
    win.view.annotations.select_object(0, _only(win, TextBox))
    assert win.view.annotations._handles.visible is False


def test_clearing_the_selection_hides_the_handles(win):
    _add(win, Shape("rect", (100.0, 100.0, 160.0, 140.0)))
    ov = win.view.annotations
    ov.select_object(0, _only(win, Shape))
    assert ov._handles.visible is True
    ov.clear_object_selection()
    assert ov._handles.visible is False


def test_handle_at_finds_the_corner_under_the_point(win):
    _add(win, Shape("rect", (100.0, 100.0, 160.0, 140.0)))
    ov = win.view.annotations
    ov.select_object(0, _only(win, Shape))
    assert ov.handle_at(_scene(win, 160, 140)) == "se"
    assert ov.handle_at(_scene(win, 400, 400)) is None


# ---- resizing ----------------------------------------------------------------


def test_resize_a_shape_by_its_corner(win):
    _add(win, Shape("rect", (100.0, 100.0, 160.0, 140.0)))
    ov = win.view.annotations
    ov.select_object(0, _only(win, Shape))
    assert ov.begin_resize("se", _scene(win, 160, 140)) is True
    ov.update_resize(_scene(win, 200, 180))
    ov.finish_resize()
    assert _only(win, Shape).rect == pytest.approx((100, 100, 200, 180), abs=1.0)
    assert win.undo_stack.undoText() == "Resize shape"
    win.undo_stack.undo()
    assert _only(win, Shape).rect == pytest.approx((100, 100, 160, 140), abs=1.0)


def test_resize_a_line_by_an_endpoint(win):
    _add(win, Line((100.0, 200.0), (220.0, 240.0)))
    ov = win.view.annotations
    ov.select_object(0, _only(win, Line))
    assert ov.begin_resize("p1", _scene(win, 220, 240)) is True
    ov.update_resize(_scene(win, 300, 300))
    ov.finish_resize()
    line = _only(win, Line)
    assert line.end == pytest.approx((300, 300), abs=1.0)
    assert line.start == pytest.approx((100, 200), abs=1.0)      # the other end stayed put
    assert win.undo_stack.undoText() == "Resize line"


def test_resize_an_ink_stroke_scales_every_point(win):
    _add(win, InkStroke((((100.0, 100.0), (120.0, 140.0), (140.0, 100.0)),)))
    ov = win.view.annotations
    ov.select_object(0, _only(win, InkStroke))
    assert ov.begin_resize("se", _scene(win, 140, 140)) is True
    ov.update_resize(_scene(win, 180, 180))                       # bounds 40x40 → 80x80
    ov.finish_resize()
    pts = _only(win, InkStroke).paths[0]
    assert pts[0] == pytest.approx((100, 100), abs=1.0)           # anchored top-left
    assert pts[2] == pytest.approx((180, 100), abs=1.0)           # x doubled about the anchor


def test_resize_a_group_scales_every_member_as_one_undo(win):
    _add(win, Shape("rect", (100.0, 100.0, 160.0, 140.0)),
         Shape("rect", (200.0, 100.0, 260.0, 140.0)))
    ov = win.view.annotations
    ov.select_in_rect(0, (90, 90, 270, 150))                      # union bounds 100,100–260,140
    assert ov.begin_resize("se", _scene(win, 260, 140)) is True
    ov.update_resize(_scene(win, 420, 180))                       # 160x40 → 320x80 (2x, 2x)
    ov.finish_resize()
    rects = sorted(s.rect for s in win.vdoc.page_annotations(0) if isinstance(s, Shape))
    assert rects[0] == pytest.approx((100, 100, 220, 180), abs=1.5)
    assert rects[1] == pytest.approx((300, 100, 420, 180), abs=1.5)   # arrangement preserved
    assert win.undo_stack.undoText() == "Resize 2 objects"
    win.undo_stack.undo()
    back = sorted(s.rect for s in win.vdoc.page_annotations(0) if isinstance(s, Shape))
    assert back[0] == pytest.approx((100, 100, 160, 140), abs=1.0)


def test_resize_keeps_the_object_selected_and_rehandles(win):
    _add(win, Shape("rect", (100.0, 100.0, 160.0, 140.0)))
    ov = win.view.annotations
    ov.select_object(0, _only(win, Shape))
    ov.begin_resize("se", _scene(win, 160, 140))
    ov.update_resize(_scene(win, 200, 180))
    ov.finish_resize()
    assert len(ov.selected_objects) == 1
    assert ov._handles.visible is True                            # handles follow the new bounds


def test_resize_clamps_to_the_page(win):
    _add(win, Shape("rect", (100.0, 100.0, 160.0, 140.0)))
    ov = win.view.annotations
    ov.select_object(0, _only(win, Shape))
    ov.begin_resize("se", _scene(win, 160, 140))
    ov.update_resize(_scene(win, 5000, 5000))                     # way off the page
    ov.finish_resize()
    pw, ph = win.view._unrotated_size(0)
    x0, y0, x1, y1 = _only(win, Shape).rect
    assert x1 <= pw + 1.0 and y1 <= ph + 1.0                      # never pushed off the sheet


def test_escape_cancels_an_in_flight_resize(win):
    _add(win, Shape("rect", (100.0, 100.0, 160.0, 140.0)))
    ov = win.view.annotations
    ov.select_object(0, _only(win, Shape))
    ov.begin_resize("se", _scene(win, 160, 140))
    ov.update_resize(_scene(win, 300, 300))
    ov.cancel_resize()
    assert ov.resizing is False
    assert _only(win, Shape).rect == pytest.approx((100, 100, 160, 140))   # nothing committed


# ---- regression: a pasted object is selected, so its first resize works ------


def _paste_of(win, mark):
    """Copy ``mark`` to the object clipboard and paste it, as the UI does."""
    win._app.object_clipboard = mark
    win._paste_object()
    return [a for a in win.vdoc.page_annotations(0) if isinstance(a, type(mark))][-1]


def test_pasting_selects_the_pasted_object(win):
    """Paste used to leave nothing selected, so no resize handles were up — the first drag on the
    pasted mark grabbed its *body* and moved it, which read as "the resize didn't take effect".
    (Reported for lines, where the endpoint you grab sits right on the body; shapes did it too.)"""
    _add(win, Line((100.0, 400.0), (220.0, 400.0)))
    pasted = _paste_of(win, _only(win, Line))
    ov = win.view.annotations
    assert ov.selected_objects == [(0, pasted)]
    assert set(ov._handles._items) == {"p0", "p1"}      # endpoint handles are up immediately


def test_first_resize_of_a_pasted_line_takes_effect(win):
    _add(win, Line((100.0, 400.0), (220.0, 400.0)))
    pasted = _paste_of(win, _only(win, Line))
    ov = win.view.annotations
    handle = ov.handle_at(_scene(win, *pasted.end))
    assert handle == "p1"                                # a handle is there on the *first* try
    assert ov.begin_resize(handle, _scene(win, *pasted.end)) is True
    ov.update_resize(_scene(win, 320.0, 300.0))
    ov.finish_resize()
    lines = [a for a in win.vdoc.page_annotations(0) if isinstance(a, Line)]
    assert lines[-1].end == pytest.approx((320, 300), abs=1.5)
    assert win.undo_stack.undoText() == "Resize line"    # resized, not moved


def test_first_resize_of_a_pasted_shape_takes_effect(win):
    _add(win, Shape("rect", (100.0, 400.0, 160.0, 440.0)))
    pasted = _paste_of(win, _only(win, Shape))
    ov = win.view.annotations
    handle = ov.handle_at(_scene(win, pasted.rect[2], pasted.rect[3]))
    assert handle == "se"
    assert ov.begin_resize(handle, _scene(win, pasted.rect[2], pasted.rect[3])) is True
    ov.update_resize(_scene(win, 300.0, 500.0))
    ov.finish_resize()
    assert win.undo_stack.undoText() == "Resize shape"


def test_resize_works_first_time_after_pasting_then_moving(win):
    """The reported bug: copy → paste → **move** → resize did nothing on the first try.

    ``finish_move`` built the moved descriptor twice — the model kept one instance and the
    re-selection held the other. ``replace_annotation`` matches by identity, so the next edit on
    the selection found nothing and silently no-opped; re-clicking the mark (which returns the
    model's instance) made a second attempt work."""
    _add(win, Line((100.0, 400.0), (220.0, 400.0)))
    pasted = _paste_of(win, _only(win, Line))          # the copy is the last Line on the page
    ov = win.view.annotations

    def last_line():
        return [a for a in win.vdoc.page_annotations(0) if isinstance(a, Line)][-1]

    mid = ((pasted.start[0] + pasted.end[0]) / 2.0, pasted.start[1])
    assert ov.begin_move(_scene(win, *mid)) is True
    ov.update_move(_scene(win, mid[0] + 60, mid[1] + 60))
    ov.finish_move()
    moved = last_line()
    # The selection must hold the *same instance* the page now holds, or the next edit no-ops.
    assert ov.selected_objects[0][1] is moved

    handle = ov.handle_at(_scene(win, *moved.end))
    assert handle == "p1"
    assert ov.begin_resize(handle, _scene(win, *moved.end)) is True
    ov.update_resize(_scene(win, 380.0, 300.0))
    ov.finish_resize()
    assert last_line().end == pytest.approx((380, 300), abs=1.5)   # took effect first try
    assert win.undo_stack.undoText() == "Resize line"


def test_move_then_resize_works_for_a_shape_too(win):
    """Same root cause, not line-specific — lock it for shapes as well."""
    _add(win, Shape("rect", (100.0, 400.0, 160.0, 440.0)))
    ov = win.view.annotations
    ov.select_object(0, _only(win, Shape))
    assert ov.begin_move(_scene(win, 130, 420)) is True
    ov.update_move(_scene(win, 190, 480))
    ov.finish_move()
    moved = _only(win, Shape)
    assert ov.selected_objects[0][1] is moved
    ov.begin_resize("se", _scene(win, moved.rect[2], moved.rect[3]))
    ov.update_resize(_scene(win, 400.0, 600.0))
    ov.finish_resize()
    assert _only(win, Shape).rect[2] == pytest.approx(400, abs=1.5)
    assert win.undo_stack.undoText() == "Resize shape"


def test_a_no_op_resize_commits_nothing(win):
    _add(win, Shape("rect", (100.0, 100.0, 160.0, 140.0)))
    ov = win.view.annotations
    ov.select_object(0, _only(win, Shape))
    at = win.undo_stack.index()
    ov.begin_resize("se", _scene(win, 160, 140))
    ov.finish_resize()                                            # released without moving
    assert win.undo_stack.index() == at
