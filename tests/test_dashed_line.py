"""Dashed stroke style for drawn marks (owner request during R6 testing). Headless + offscreen GUI.

PyMuPDF bakes a dash pattern as a PDF ``/BS /D`` border on line / square / circle / ink
annotations, and reads it back on reopen — so a solid/dashed **boolean** on the drawn descriptors
round-trips with no extra model state. The style picker's old "Width" sub-menu becomes "Line
Style": the three thicknesses plus a Solid/Dashed radio group. Dashes apply to the drawn types
only (pen · line · rect · ellipse), the same applicability rule as width and fill.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from app import PdfApp
from model.edit_engine import PyMuPDFEngine
from model.page_edits import InkStroke, Line, Shape, restyle_mark
from model.virtual_document import VirtualDocument
from store.settings import Settings
from viewer.markup_style import LineStylingButton, MarkupStyle
from viewer.tools import ArmedTool


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


def _materialize(vdoc, tmp_path, name="out.pdf") -> str:
    out = str(tmp_path / name)
    PyMuPDFEngine().materialize(vdoc, out)
    return out


def _scene(win, x, y):
    return win.view.scene_rect_for_box(0, (x, y, x + 0.01, y + 0.01)).center()


def _drag(win, tool, start, *moves):
    from PySide6.QtCore import Qt

    overlay = win.view.annotations
    assert overlay.begin_draw(tool, _scene(win, *start)) is True
    for point in moves:
        overlay.update_draw(_scene(win, *point), Qt.KeyboardModifier.NoModifier)
    overlay.finish_draw()


def _only(win, cls):
    marks = [a for a in win.vdoc.page_annotations(0) if isinstance(a, cls)]
    assert len(marks) == 1
    return marks[0]


# ---- the library actually does this (bake + round-trip) ----------------------


@pytest.mark.parametrize("mark", [
    Line((100, 200), (260, 200), dashed=True),
    Shape("rect", (100, 260, 260, 320), dashed=True),
    Shape("ellipse", (100, 340, 260, 400), dashed=True),
    InkStroke((((100, 440), (160, 460), (220, 445), (280, 470)),), dashed=True),
])
def test_dashed_marks_bake_and_reopen_dashed(tmp_path, a_pdf, mark):
    v1 = VirtualDocument.from_path(a_pdf)
    v1.add_annotation(0, mark)
    reopened = VirtualDocument.from_path(_materialize(v1, tmp_path))
    same = [a for a in reopened.page_annotations(0) if isinstance(a, type(mark))]
    assert len(same) == 1 and same[0].dashed is True


def test_solid_stays_solid_through_a_round_trip(tmp_path, a_pdf):
    v1 = VirtualDocument.from_path(a_pdf)
    v1.add_annotation(0, Line((100, 200), (260, 200)))  # dashed defaults False
    reopened = VirtualDocument.from_path(_materialize(v1, tmp_path))
    line = next(a for a in reopened.page_annotations(0) if isinstance(a, Line))
    assert line.dashed is False


def test_dash_border_lands_in_the_saved_file(tmp_path, a_pdf):
    """The saved annotation itself carries the dash array + dashed style, not just our model."""
    v1 = VirtualDocument.from_path(a_pdf)
    v1.add_annotation(0, Line((100, 200), (260, 200), width=2.0, dashed=True))
    with fitz.open(_materialize(v1, tmp_path)) as doc:
        page = doc[0]
        borders = [a.border for a in page.annots() if a.type[0] == fitz.PDF_ANNOT_LINE]
        assert len(borders) == 1
        assert borders[0]["dashes"]              # a non-empty dash array
        assert borders[0]["style"] == "D"        # dashed border style


def test_dash_pattern_scales_with_width():
    from model.page_edits import _dash_array

    thin, thick = _dash_array(1.0), _dash_array(4.0)
    assert thick[0] > thin[0] and thick[1] > thin[1]   # a thick line dashes more boldly


# ---- restyle in place --------------------------------------------------------


def test_restyle_mark_toggles_dashed_and_none_keeps_it():
    line = Line((0, 0), (10, 0), dashed=True)
    solid = restyle_mark(line, line.color, line.width, None, 1.0, None, dashed=False)
    assert solid.dashed is False
    kept = restyle_mark(line, line.color, line.width, None, 1.0, None, None)  # dashed omitted
    assert kept.dashed is True
    shape = Shape("rect", (0, 0, 10, 10))
    assert restyle_mark(shape, shape.color, shape.width, None, 1.0, None, True).dashed is True


def test_selected_mark_restyles_to_dashed_one_undo_step(win):
    line = Line((100.0, 200.0), (260.0, 200.0))
    win.vdoc.add_annotation(0, line)
    win.view.reload()
    win.view.annotations.repaint()
    win.view.annotations.select_object(0, line)
    before = win.undo_stack.count()
    win._on_markup_style_changed(MarkupStyle(dashed=True))
    got = _only(win, Line)
    assert got.dashed is True
    assert win.undo_stack.count() == before + 1
    win.undo_stack.undo()
    assert _only(win, Line).dashed is False


def test_selecting_a_dashed_mark_loads_it_into_the_picker(win):
    line = Line((100.0, 200.0), (260.0, 200.0), dashed=True)
    win.vdoc.add_annotation(0, line)
    win.view.reload()
    win.view.annotations.repaint()
    win._on_object_selected(line)
    assert win._line_style_button.style().dashed is True


def test_from_mark_reads_dashed():
    assert MarkupStyle.from_mark(Shape("rect", (0, 0, 5, 5), dashed=True)).dashed is True
    assert MarkupStyle.from_mark(InkStroke((((0, 0), (5, 5)),), dashed=True)).dashed is True


# ---- drawing stamps the sticky dash choice -----------------------------------


def test_drawn_line_stamps_the_sticky_dash(win):
    win.view.annotations.set_markup_style(MarkupStyle(dashed=True))
    _drag(win, ArmedTool.LINE, (100, 200), (260, 200))
    assert _only(win, Line).dashed is True


def test_drawn_shape_and_pen_stamp_the_sticky_dash(win):
    win.view.annotations.set_markup_style(MarkupStyle(dashed=True))
    _drag(win, ArmedTool.RECT, (100, 260), (260, 320))
    assert _only(win, Shape).dashed is True
    win.view.annotations.set_markup_style(MarkupStyle(dashed=True))
    _drag(win, ArmedTool.PEN, (100, 440), (160, 460), (220, 445))
    assert _only(win, InkStroke).dashed is True


# ---- the picker UI -----------------------------------------------------------


def test_line_style_menu_has_widths_and_dash_styles():
    # M78.6: widths + dash live at the top of the Line Styling button's menu (Arrowheads is a
    # sub-menu below them).
    btn = LineStylingButton()
    labels = [a.text() for a in btn.menu().actions() if a.text() and not a.menu()]
    assert labels == ["Thin", "Medium", "Thick", "Solid", "Dashed"]
    assert any(a.text() == "Arrowheads" and a.menu() for a in btn.menu().actions())


def test_picking_dashed_emits_and_ticks():
    btn = LineStylingButton()
    seen = []
    btn.styleChanged.connect(seen.append)
    btn._set_dashed(True)
    assert seen and seen[-1].dashed is True
    assert btn._dash_actions[True].isChecked()
    btn.set_style(MarkupStyle(dashed=False))  # load without emitting
    assert btn._dash_actions[False].isChecked()
    assert len(seen) == 1


def test_width_and_dash_are_independent_radio_groups():
    """A stroke has a width AND a dash style at once — the two groups don't clobber each other."""
    btn = LineStylingButton()
    btn._set_width(4.0)
    btn._set_dashed(True)
    assert btn.style().width == 4.0 and btn.style().dashed is True
    assert btn._width_actions[4.0].isChecked() and btn._dash_actions[True].isChecked()
