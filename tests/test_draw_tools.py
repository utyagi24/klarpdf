"""Pen & shapes tools (PLAN.md §GUI feature roadmap, M58 — R3 "Markup Tools"). Offscreen GUI.

Draw interactions over the M57 model: pen path capture with live preview; line / arrow / rect /
ellipse press-drag-release with Shift-constrain (45° / square / circle); move + delete of any
drawn mark (resize deferred). One-shot armed like every other tool; the toolbar stays in budget
via the Draw ▾ split-button. The Done-when: draw/move/delete each type; fixed-width ink.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtCore import Qt

from app import PdfApp
from main_window import MainWindow
from model.page_edits import InkStroke, Line, Shape
from store.settings import Settings
from viewer.tools import ArmedTool


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
    """A scene point for page-0 content coordinates ``(x, y)``."""
    return win.view.scene_rect_for_box(0, (x, y, x + 0.01, y + 0.01)).center()


def _drag(win, tool, start, *moves, modifiers=Qt.KeyboardModifier.NoModifier):
    """Drive a full draw gesture through the overlay (what the mouse routing calls)."""
    overlay = win.view.annotations
    assert overlay.begin_draw(tool, _scene(win, *start)) is True
    for point in moves:
        overlay.update_draw(_scene(win, *point), modifiers)
    overlay.finish_draw()


def _only_mark(win, cls):
    marks = [a for a in win.vdoc.page_annotations(0) if isinstance(a, cls)]
    assert len(marks) == 1
    return marks[0]


# ---- draw each type ----------------------------------------------------------


def test_pen_captures_a_path_and_undoes(win):
    _drag(win, ArmedTool.PEN, (100, 100), (120, 110), (140, 105), (160, 130))
    ink = _only_mark(win, InkStroke)
    assert len(ink.paths) == 1 and len(ink.paths[0]) >= 3   # the samples were captured
    assert ink.paths[0][0] == pytest.approx((100, 100), abs=1.0)
    assert ink.width == 2.0                                  # fixed-width ink (no pressure)
    assert win.undo_stack.undoText() == "Add inkstroke"
    win.undo_stack.undo()
    assert win.vdoc.page_annotations(0) == ()


def test_line_tool_commits_a_line(win):
    _drag(win, ArmedTool.LINE, (100, 200), (220, 260))
    line = _only_mark(win, Line)
    assert line.start == pytest.approx((100, 200), abs=1.0)
    assert line.end == pytest.approx((220, 260), abs=1.0)
    assert (line.arrow_start, line.arrow_end) == (False, False)


def test_line_stamps_the_style_pickers_ends(win):
    """Arrowheads are line style since M74: the picker's ends land on the committed line —
    including both-ended, which the retired Arrow tool never could."""
    from viewer.markup_style import MarkupStyle

    win.view.annotations.set_markup_style(MarkupStyle(line_ends=(False, True)))
    _drag(win, ArmedTool.LINE, (100, 200), (200, 200))
    line = _only_mark(win, Line)
    assert line.arrow_end is True and line.arrow_start is False
    win.undo_stack.undo()
    win.view.annotations.set_markup_style(MarkupStyle(line_ends=(True, True)))
    _drag(win, ArmedTool.LINE, (100, 200), (200, 200))
    line = _only_mark(win, Line)
    assert line.arrow_start is True and line.arrow_end is True


def test_rect_and_ellipse_commit_normalised_rects(win):
    _drag(win, ArmedTool.RECT, (200, 300), (120, 240))      # dragged up-left → normalised
    rect = _only_mark(win, Shape)
    assert rect.kind == "rect"
    assert rect.rect == pytest.approx((120, 240, 200, 300), abs=1.0)
    _drag(win, ArmedTool.ELLIPSE, (250, 300), (330, 360))
    kinds = sorted(a.kind for a in win.vdoc.page_annotations(0) if isinstance(a, Shape))
    assert kinds == ["ellipse", "rect"]


# ---- Shift constraints -------------------------------------------------------


def test_shift_snaps_a_line_to_45_degrees(win):
    _drag(win, ArmedTool.LINE, (100, 100), (200, 108),
          modifiers=Qt.KeyboardModifier.ShiftModifier)      # nearly horizontal → snapped flat
    line = _only_mark(win, Line)
    assert line.end[1] == pytest.approx(100, abs=0.5)       # 0° — the y drift is gone


def test_shift_squares_a_rect(win):
    _drag(win, ArmedTool.RECT, (100, 100), (180, 130),
          modifiers=Qt.KeyboardModifier.ShiftModifier)
    rect = _only_mark(win, Shape)
    x0, y0, x1, y1 = rect.rect
    assert (x1 - x0) == pytest.approx(y1 - y0, abs=0.5)     # square

# ---- gesture hygiene ---------------------------------------------------------


def test_tiny_drag_commits_nothing(win):
    _drag(win, ArmedTool.RECT, (100, 100), (100.8, 100.9))
    assert win.vdoc.page_annotations(0) == ()


def test_off_page_press_does_not_start(win):
    overlay = win.view.annotations
    off = win.view.scene_rect_for_box(0, (-80, -80, -79, -79)).center()
    assert overlay.begin_draw(ArmedTool.PEN, off) is False
    assert overlay.drawing is False


def test_cancel_draw_drops_the_gesture(win):
    overlay = win.view.annotations
    assert overlay.begin_draw(ArmedTool.LINE, _scene(win, 100, 100))
    overlay.update_draw(_scene(win, 200, 150))
    overlay.cancel_draw()                                   # Esc / disarm mid-drag
    assert overlay.drawing is False
    assert win.vdoc.page_annotations(0) == ()


def test_armed_draw_disarms_on_view_disarm(win):
    win.view.arm(ArmedTool.PEN)
    overlay = win.view.annotations
    overlay.begin_draw(ArmedTool.PEN, _scene(win, 100, 100))
    win.view.disarm()                                       # the Esc path
    assert overlay.drawing is False and win.vdoc.page_annotations(0) == ()


# ---- move + delete -----------------------------------------------------------


def test_move_a_shape_translates_it(win):
    win.vdoc.add_annotation(0, Shape("rect", (100.0, 100.0, 160.0, 140.0)))
    win.view.reload()
    win.view.annotations.repaint()
    overlay = win.view.annotations
    assert overlay.begin_move(_scene(win, 130, 120)) is True
    overlay.update_move(_scene(win, 170, 150))              # +40, +30
    overlay.finish_move()
    shape = _only_mark(win, Shape)
    assert shape.rect == pytest.approx((140, 130, 200, 170), abs=1.0)
    assert win.undo_stack.undoText() == "Move shape"
    win.undo_stack.undo()
    assert _only_mark(win, Shape).rect == (100.0, 100.0, 160.0, 140.0)


def test_move_an_ink_stroke_translates_every_point(win):
    ink = InkStroke((((100.0, 100.0), (120.0, 110.0), (140.0, 100.0)),))
    win.vdoc.add_annotation(0, ink)
    win.view.reload()
    overlay = win.view.annotations
    assert overlay.begin_move(_scene(win, 120, 105)) is True
    overlay.update_move(_scene(win, 130, 125))              # +10, +20
    overlay.finish_move()
    moved = _only_mark(win, InkStroke)
    assert moved.paths[0][0] == pytest.approx((110, 120), abs=1.0)
    assert moved.paths[0][2] == pytest.approx((150, 120), abs=1.0)


def test_move_a_thin_line_grabs_via_padding(win):
    win.vdoc.add_annotation(0, Line((100.0, 200.0), (220.0, 200.0)))  # horizontal: no height
    win.view.reload()
    overlay = win.view.annotations
    assert overlay.begin_move(_scene(win, 160, 201)) is True  # 1 pt off — the pad catches it


def test_right_click_remove_labels_for_drawn_marks(win):
    win.vdoc.add_annotation(0, Line((100.0, 200.0), (220.0, 240.0)))
    win.vdoc.add_annotation(0, Shape("ellipse", (300.0, 300.0, 380.0, 360.0)))
    win.view.reload()

    def titles(menu):
        return [a.text() for a in menu.actions() if not a.isSeparator()]

    menu = win._view_context_menu(_scene(win, 160, 220))
    assert titles(menu)[-1] == "Remove line"  # Copy/Cut Object join at M59
    menu = win._view_context_menu(_scene(win, 340, 330))
    assert titles(menu)[-1] == "Remove shape"
    next(a for a in menu.actions() if a.text() == "Remove shape").trigger()
    assert not any(isinstance(a, Shape) for a in win.vdoc.page_annotations(0))


# ---- overlay + toolbar -------------------------------------------------------


def test_repaint_paints_drawn_marks(win):
    before = len(win.view.annotations._items)
    win.vdoc.add_annotation(0, InkStroke((((100.0, 100.0), (150.0, 130.0)),)))
    win.vdoc.add_annotation(0, Line((100.0, 200.0), (220.0, 240.0), arrow_end=True))
    win.vdoc.add_annotation(0, Shape("rect", (250.0, 100.0, 320.0, 160.0)))
    win.view.annotations.repaint()
    assert len(win.view.annotations._items) == before + 3


def test_draw_split_button_groups_the_four_tools(win):
    button = win._draw_button
    assert [a.text() for a in button.menu().actions()] == [
        "Pen", "Line", "Rectangle", "Ellipse"  # Arrow merged into Line (M74)
    ]
    assert button.defaultAction().text() == "Pen"
    actions = {a.text(): a for a in button.menu().actions()}
    button.menu().triggered.emit(actions["Rectangle"])
    assert button.defaultAction().text() == "Rectangle"     # sticky last-used face


def test_drawn_marks_bake_on_save(win, tmp_path, monkeypatch):
    """The end-to-end slice: draw → save → the mark is a real annotation in the file."""
    _drag(win, ArmedTool.LINE, (100, 200), (200, 260))
    import main_window as mw

    target = str(tmp_path / "drawn.pdf")
    monkeypatch.setattr(mw.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (target, "")))
    assert win.save_as() is True
    with fitz.open(target) as doc:
        page = doc[0]
        kinds = [a.type[0] for a in page.annots()]
    assert fitz.PDF_ANNOT_LINE in kinds
