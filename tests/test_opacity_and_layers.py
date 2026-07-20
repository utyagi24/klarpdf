"""Object opacity + the redaction preview's layer (M59.9).

Two things z-order can't fix, both reported in testing:

* A filled shape hid the page text, and Send to Back didn't help — it never could: PDF annotations
  always paint **above** the page content stream, so no ordering puts one behind text. The lever is
  opacity (``/CA``), which PDF applies to the whole annotation (outline *and* fill together).
* The redaction preview sat above every mark, but a save applies redactions **first** (destructively,
  into the content) and *then* adds the annotations — so in the file a drawn mark lands on top of
  the redaction box. The preview now matches.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from app import PdfApp
from main_window import MainWindow
from model.edit_engine import PyMuPDFEngine
from model.page_edits import (
    InkStroke,
    Line,
    Redaction,
    Shape,
    apply_annotations,
    read_klarpdf_annotations,
    restyle_mark,
)
from model.virtual_document import VirtualDocument
from store.settings import Settings
from viewer.markup_style import MarkupStyle
from viewer.tools import ArmedTool


# ---- opacity: model, bake, round-trip ---------------------------------------


def test_marks_default_to_opaque():
    assert Shape("rect", (0.0, 0.0, 1.0, 1.0)).opacity == 1.0
    assert Line((0.0, 0.0), (1.0, 1.0)).opacity == 1.0
    assert InkStroke((((0.0, 0.0),),)).opacity == 1.0


def test_opacity_bakes_and_reads_back(a_pdf, tmp_path):
    v = VirtualDocument.from_path(a_pdf)
    v.add_annotation(0, Shape("rect", (100.0, 100.0, 200.0, 160.0),
                              fill_color=(0.2, 0.4, 0.9), opacity=0.25))
    out = str(tmp_path / "translucent.pdf")
    PyMuPDFEngine().materialize(v, out)
    reopened = VirtualDocument.from_path(out)
    shape = [a for a in reopened.page_annotations(0) if isinstance(a, Shape)][0]
    assert shape.opacity == pytest.approx(0.25, abs=0.01)
    assert shape.fill_color == pytest.approx((0.2, 0.4, 0.9), abs=0.02)


def test_opacity_survives_two_save_cycles(a_pdf, tmp_path):
    v = VirtualDocument.from_path(a_pdf)
    v.add_annotation(0, Line((10.0, 10.0), (90.0, 90.0), opacity=0.5))
    first = str(tmp_path / "one.pdf")
    PyMuPDFEngine().materialize(v, first)
    second = str(tmp_path / "two.pdf")
    PyMuPDFEngine().materialize(VirtualDocument.from_path(first), second)
    line = [a for a in VirtualDocument.from_path(second).page_annotations(0)
            if isinstance(a, Line)][0]
    assert line.opacity == pytest.approx(0.5, abs=0.01)


def test_an_untagged_annot_reads_back_opaque(a_pdf):
    """A mark saved before opacity existed has no /CA — it must read back solid, not transparent."""
    doc = fitz.open(a_pdf)
    try:
        apply_annotations(doc[0], (Shape("rect", (10.0, 10.0, 50.0, 50.0)),))
        back = read_klarpdf_annotations(doc[0])
        assert back[0].opacity == 1.0
    finally:
        doc.close()


def test_restyle_carries_opacity():
    shape = Shape("rect", (0.0, 0.0, 10.0, 10.0))
    out = restyle_mark(shape, (1.0, 0.0, 0.0), 2.0, None, 0.5)
    assert out.opacity == 0.5


# ---- opacity: the picker + the drawn marks (offscreen GUI) ------------------


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    qapp.object_clipboard = None
    return qapp


@pytest.fixture
def win(app, a_pdf):
    w = MainWindow(app, a_pdf, app.settings)
    yield w
    w.undo_stack.setClean()
    w.close()


def _scene(win, x, y):
    return win.view.scene_rect_for_box(0, (x, y, x + 0.01, y + 0.01)).center()


def _only(win, cls):
    marks = [a for a in win.vdoc.page_annotations(0) if isinstance(a, cls)]
    assert len(marks) == 1
    return marks[0]


def test_a_drawn_shape_takes_the_picker_opacity(win):
    win.view.annotations.set_markup_style(
        MarkupStyle(fill_color=(0.9, 0.9, 0.2), opacity=0.5)
    )
    ov = win.view.annotations
    assert ov.begin_draw(ArmedTool.RECT, _scene(win, 100, 100)) is True
    ov.update_draw(_scene(win, 200, 160))
    ov.finish_draw()
    assert _only(win, Shape).opacity == 0.5


def test_the_picker_restyles_a_selected_mark_to_translucent(win):
    win.vdoc.add_annotation(0, Shape("rect", (100.0, 100.0, 200.0, 160.0),
                                     fill_color=(0.9, 0.9, 0.2)))
    win.view.reload()
    win.view.annotations.select_object(0, _only(win, Shape))
    win._markup_style_button._set_opacity(0.25)
    assert _only(win, Shape).opacity == 0.25
    assert win.undo_stack.undoText() == "Restyle shape"


def test_the_opacity_menu_offers_solid_through_quarter(win):
    labels = [a.text() for a in win._markup_style_button._opacity_menu.actions()]
    assert labels == ["Solid", "75%", "50%", "25%"]


def test_selecting_a_translucent_mark_loads_its_opacity(win):
    win.vdoc.add_annotation(0, Shape("rect", (100.0, 100.0, 200.0, 160.0), opacity=0.5))
    win.view.reload()
    win.view.annotations.select_object(0, _only(win, Shape))
    assert win._markup_style_button.style().opacity == 0.5


# ---- the redaction preview layer -------------------------------------------


def _z_of(win, cls) -> float:
    """The scene z of the painted item for the single mark of ``cls``."""
    from PySide6.QtWidgets import QGraphicsRectItem

    zs = [i.zValue() for i in win.view.annotations._items if isinstance(i, QGraphicsRectItem)]
    return zs


def test_redaction_previews_below_the_marks_it_will_be_saved_under(win):
    """A save bakes the redaction into the *content* and then adds annotations on top, so a drawn
    mark must preview above the redaction box — it used to preview below it."""
    win.vdoc.add_annotation(0, Redaction(((100.0, 100.0, 300.0, 200.0),)))
    win.vdoc.add_annotation(0, Shape("rect", (120.0, 120.0, 280.0, 180.0)))
    win.view.reload()
    win.view.annotations.repaint()
    items = win.view.annotations._items
    redaction_z = min(i.zValue() for i in items)
    shape_z = max(i.zValue() for i in items)
    assert redaction_z < shape_z          # the mark paints over the redaction, as it will in the file
    assert redaction_z > 0                # …but still above the page itself, hiding what it destroys
