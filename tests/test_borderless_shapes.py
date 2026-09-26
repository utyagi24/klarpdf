"""Shapes with no border (M155: #394 and #395). Headless + offscreen GUI.

**#394.** A rectangle or ellipse always had a border. The Colors menu's Border row now ends in a
crossed-out dot, like the Fill row's. A shape always keeps its border or its fill (owner,
2026-09-25): with no fill the No Border dot is greyed out, and with no border the No Fill dot is.

**#395.** A shape from another program with no border came back with one when it was edited. The
model now holds "no border" (``Shape.color is None``), a width of 0 stays 0, and a file that sets
no width reads as the PDF's own 1 pt. The warning before an edit names a cloud edge, and says "its
dash spacing" rather than "its dashed border" for a mark that stays dashed.

The bridge re-saves our shapes through the same code that reads and writes them; its side is in
``tests/test_mcp_shape_borders.py``.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGraphicsRectItem

from app import PdfApp
from main_window import MainWindow
from klarpdf.model.edit_engine import PyMuPDFEngine
from klarpdf.model.foreign_annots import adopt_annotation, degradations
from klarpdf.model.page_edits import (
    KLARPDF_AUTHOR,
    InkStroke,
    Line,
    Shape,
    _dash_array,
    apply_annotations,
    parse_annotation,
    read_klarpdf_annotations,
    restyle_mark,
)
from store.settings import Settings
from viewer.markup_style import ColorsButton, MarkupStyle
from viewer.tools import ArmedTool

_RED, _GREEN, _BLUE, _BLACK = (0.86, 0.10, 0.10), (0.13, 0.60, 0.20), (0.13, 0.35, 0.85), (0, 0, 0)
_YELLOW_FILL, _BLUE_FILL, _PINK_FILL = (1.0, 0.94, 0.60), (0.78, 0.86, 0.97), (0.98, 0.82, 0.88)


def _one_page():
    doc = fitz.open()
    return doc, doc.new_page()


def _stroke_ops(annot) -> bytes:
    """The annotation's own drawing, for asking whether it strokes anything."""
    return annot._getAP()


# ---- the model: "no border" is a shape with no border colour --------------------------------


def test_a_shape_with_no_border_is_written_with_an_empty_border_colour():
    """`None` is written as an empty border colour, and the drawing fills without stroking. It
    must not be `stroke=None`, which PyMuPDF reads as "leave it": a new annotation's is red."""
    doc, page = _one_page()
    apply_annotations(page, (Shape("rect", (100, 100, 200, 160), color=None,
                                   fill_color=_YELLOW_FILL),))
    annot = page.first_annot
    assert annot.colors["stroke"] == []
    assert b"RG" not in _stroke_ops(annot)             # no stroke colour is ever set
    (back,) = read_klarpdf_annotations(page)
    assert back.color is None
    assert back.fill_color == pytest.approx(_YELLOW_FILL, abs=0.01)


@pytest.mark.parametrize("kind", ["rect", "ellipse"])
def test_a_border_width_of_zero_comes_back_as_zero(kind):
    """A width of 0 is kept. MuPDF draws it one pixel wide, and so does Qt, so the number is
    what keeps the mark looking the same. It used to become 2 pt."""
    doc, page = _one_page()
    apply_annotations(page, (Shape(kind, (100, 100, 200, 160), color=_BLACK, width=0.0),))
    (back,) = read_klarpdf_annotations(page)
    assert back.width == 0.0
    assert back.color == pytest.approx(_BLACK)


def test_a_shape_with_neither_border_nor_fill_round_trips_as_it_is():
    """The app never makes one, but a file can hold one, and adopting or re-saving it must not
    invent a border for it."""
    doc, page = _one_page()
    apply_annotations(page, (Shape("rect", (100, 100, 200, 160), color=None, fill_color=None),))
    (back,) = read_klarpdf_annotations(page)
    assert back.color is None and back.fill_color is None


def _no_width(page, make):
    """An annotation with no `/BS` and no `/Border`: the file sets no width at all."""
    annot = make(page)
    annot.set_info(title="Alice")
    annot.update()
    page.parent.xref_set_key(annot.xref, "BS", "null")
    annot.update()
    assert annot.border["width"] == -1                  # how PyMuPDF reports "not set"
    return annot


@pytest.mark.parametrize("make", [
    lambda p: p.add_rect_annot(fitz.Rect(100, 100, 200, 160)),
    lambda p: p.add_circle_annot(fitz.Rect(100, 100, 200, 160)),
    lambda p: p.add_line_annot(fitz.Point(100, 100), fitz.Point(200, 160)),
    lambda p: p.add_ink_annot([[(100, 100), (150, 130), (200, 160)]]),
], ids=["rect", "ellipse", "line", "pen"])
def test_a_file_that_sets_no_width_reads_as_one_point(make):
    """The PDF's default width is 1 pt, and MuPDF draws 1 pt. It used to read as 2 pt, so an
    edit doubled the line."""
    doc, page = _one_page()
    annot = _no_width(page, make)
    assert b"1 w" in _stroke_ops(annot)                 # what the file's own drawing uses
    assert parse_annotation(annot).width == 1.0


def test_restyle_with_no_colour_removes_a_shapes_border_only():
    """`None` is No Border for a shape. A line or pen stroke is nothing but its stroke, so it keeps
    its colour, the way it ignores a fill."""
    shape = Shape("rect", (0, 0, 10, 10), color=_RED, width=4.0, fill_color=_YELLOW_FILL)
    line = Line((0, 0), (10, 10), color=_GREEN)
    ink = InkStroke((((0, 0), (5, 5)),), color=_BLUE)
    assert restyle_mark(shape, None, 4.0, _YELLOW_FILL).color is None
    assert restyle_mark(line, None, 2.0, None).color == _GREEN
    assert restyle_mark(ink, None, 2.0, None).color == _BLUE


# ---- #395: another program's borderless shapes ----------------------------------------------


def _issue_395_page(page):
    """The two rectangles from the #395 report, by "Alice"."""
    a = page.add_rect_annot(fitz.Rect(100, 100, 200, 160))
    a.set_colors(stroke=[], fill=_YELLOW_FILL)          # no border colour
    a.set_info(title="Alice")
    a.update()
    b = page.add_rect_annot(fitz.Rect(300, 100, 400, 160))
    b.set_colors(stroke=_BLACK, fill=_BLUE_FILL)
    b.set_border(width=0)                               # border width 0
    b.set_info(title="Alice")
    b.update()
    return a, b


def test_the_issue_395_shapes_adopt_as_they_are_with_nothing_to_warn_about():
    doc, page = _one_page()
    no_colour, zero_width = _issue_395_page(page)
    assert degradations(no_colour) == [] and degradations(zero_width) == []
    left, right = adopt_annotation(no_colour), adopt_annotation(zero_width)
    assert left.color is None
    assert left.fill_color == pytest.approx(_YELLOW_FILL, abs=0.01)
    assert right.color == pytest.approx(_BLACK) and right.width == 0.0


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


def test_editing_the_issue_395_shapes_in_the_app_keeps_their_borders(app, tmp_path, monkeypatch):
    """The report's steps: double-click each rectangle (what `_adopt_foreign_annotation` does),
    then save. Before M155 the left one came back with a red 1 pt border and the right one with a
    black 2 pt border, and no warning was shown for either."""
    src = str(tmp_path / "borderless-shapes.pdf")
    doc, page = _one_page()
    _issue_395_page(page)
    doc.save(src)
    doc.close()
    warned = []
    monkeypatch.setattr(MainWindow, "_confirm_degrade",
                        lambda self, mark, lost: warned.append(lost) or True)
    window = MainWindow(app, src, app.settings)
    try:
        for mark in list(window.view.annotations.foreign_annotations(0)):
            assert window._adopt_foreign_annotation(0, mark) is True
        out = str(tmp_path / "saved.pdf")
        PyMuPDFEngine().materialize(window.vdoc, out)
    finally:
        window.undo_stack.setClean()
        window.close()
    assert warned == []
    saved = fitz.open(out)
    try:
        page = saved[0]                                 # an annotation dies with its page object
        left, right = sorted(page.annots(), key=lambda a: a.rect.x0)
        assert left.info["title"] == right.info["title"] == KLARPDF_AUTHOR
        assert left.colors["stroke"] == [] and b"RG" not in _stroke_ops(left)
        assert right.colors["stroke"] == pytest.approx(list(_BLACK))
        assert right.border["width"] == 0.0
    finally:
        saved.close()


# ---- the warning before an edit: what it says about the border ------------------------------


def _foreign_rect(page, *, width=1.0, dashes=None, raw=None, kind="rect"):
    add = page.add_rect_annot if kind == "rect" else page.add_freetext_annot
    annot = (add(fitz.Rect(100, 100, 200, 160)) if kind == "rect"
             else add(fitz.Rect(100, 100, 200, 160), "a box"))
    if kind == "rect":
        annot.set_colors(stroke=_BLUE)
        annot.set_border(width=width, dashes=dashes)
    annot.set_info(title="Alice")
    annot.update()
    for key, value in (raw or {}).items():
        page.parent.xref_set_key(annot.xref, key, value)
    annot.update()
    return annot


def test_a_cloud_edge_is_reported():
    """MuPDF draws a cloud edge; the model has none, so an edit drew a plain border silently."""
    doc, page = _one_page()
    cloudy = _foreign_rect(page, raw={"BE": "<</S/C/I 1>>"})
    assert "its cloud-shaped edge" in degradations(cloudy)


def test_a_cloud_style_at_strength_zero_is_not_reported():
    """MuPDF draws strength 0 as a plain border, so nothing changes and nothing is said."""
    doc, page = _one_page()
    flat = _foreign_rect(page, raw={"BE": "<</S/C/I 0>>"})
    assert "its cloud-shaped edge" not in degradations(flat)


def test_a_dashed_shape_is_warned_about_its_spacing_not_its_dashes():
    """It stays dashed: only a spacing other than ours changes. The warning used to say "its
    dashed border", which told the reader the dashes would go."""
    doc, page = _one_page()
    other = _foreign_rect(page, width=1.0, dashes=[3, 3])
    assert degradations(other) == ["its dash spacing"]
    assert parse_annotation(other).dashed is True


def test_a_dashed_shape_with_our_own_spacing_warns_about_nothing():
    doc, page = _one_page()
    ours = _foreign_rect(page, width=2.0, dashes=_dash_array(2.0))
    assert degradations(ours) == []


def test_a_fractional_dash_is_compared_as_the_file_spells_it():
    """PyMuPDF rounds each dash to a whole number, which would make `[3.4 2]` look like our own
    `[3 2]` at 1 pt. The file's own numbers are what is compared."""
    doc, page = _one_page()
    fine = _foreign_rect(page, raw={"BS": "<</W 1/S/D/D[3.4 2]>>"})
    assert fine.border["dashes"] == tuple(_dash_array(1.0))
    assert degradations(fine) == ["its dash spacing"]


def test_a_damaged_dash_list_does_not_stop_the_edit():
    """A dash list that is not plain numbers falls back to PyMuPDF's reading of it. Parsing it
    raised, and the warning is on the way to every edit of another program's mark."""
    doc, page = _one_page()
    damaged = _foreign_rect(page)
    page.parent.xref_set_key(damaged.xref, "BS", "<</W 1/S/D/D[3 /foo]>>")
    assert degradations(damaged) == ["its dash spacing"]


def test_a_dashed_text_box_still_reports_its_dashed_border():
    """A text box has no dashes at all, so for it the dash is what is lost."""
    doc, page = _one_page()
    box = _foreign_rect(page, kind="freetext", raw={"BS": "<</W 1/S/D/D[3 3]>>"})
    assert "its dashed border" in degradations(box)


# ---- the Colors menu ------------------------------------------------------------------------


def test_the_border_row_ends_in_a_no_border_dot_that_needs_a_fill():
    """The default style has no fill, so No Border is greyed out: a shape with neither would not
    show. No Fill is offered, because there is a border."""
    button = ColorsButton()
    border_dot = button._border_row.remove_button
    fill_dot = button._fill_row.remove_button
    assert border_dot is not None
    assert not border_dot.isEnabled()
    assert "choose a fill first" in border_dot.toolTip()
    assert fill_dot.isEnabled() and fill_dot.toolTip() == "Remove fill"


def test_with_a_fill_no_border_is_offered_and_then_no_fill_is_not():
    button = ColorsButton()
    seen = []
    button.styleChanged.connect(lambda style, changes: seen.append(changes))
    button._set_fill(_YELLOW_FILL)
    border_dot = button._border_row.remove_button
    assert border_dot.isEnabled()
    assert border_dot.toolTip() == "Remove border (rectangles and ellipses)"
    border_dot.click()
    assert seen[-1] == {"border": False}
    assert button.style().border is False
    assert button.style().color == pytest.approx(_RED)  # kept for the pen and the line
    fill_dot = button._fill_row.remove_button
    assert not fill_dot.isEnabled()
    assert "choose a border colour first" in fill_dot.toolTip()
    fill_dot.click()                                    # a greyed-out dot does nothing
    assert len(seen) == 2 and button.style().fill_color == pytest.approx(_YELLOW_FILL)


def test_a_border_colour_turns_the_border_back_on():
    button = ColorsButton()
    button.set_style(MarkupStyle(fill_color=_YELLOW_FILL, border=False))
    button._border_row.buttons["Blue"].click()
    assert button.style().border is True
    assert button.style().color == pytest.approx(_BLUE)
    assert button._fill_row.remove_button.isEnabled()


# ---- drawing --------------------------------------------------------------------------------


def _scene(win, x: float, y: float):
    return win.view.scene_rect_for_box(0, (x, y, x + 0.01, y + 0.01)).center()


def _drag(win, tool, start, *moves):
    overlay = win.view.annotations
    assert overlay.begin_draw(tool, _scene(win, *start)) is True
    for point in moves:
        overlay.update_draw(_scene(win, *point), Qt.KeyboardModifier.NoModifier)
    overlay.finish_draw()


def _marks(win, cls):
    return [a for a in win.vdoc.page_annotations(0) if isinstance(a, cls)]


def _choose_fill_and_no_border(win):
    win._colors_button._fill_row.buttons["Yellow"].click()
    win._colors_button._border_row.remove_button.click()
    assert win.view.annotations.current_markup_style.border is False


@pytest.mark.parametrize("tool", [ArmedTool.RECT, ArmedTool.ELLIPSE])
def test_a_shape_drawn_with_no_border_has_none_in_the_saved_file(win, tool, tmp_path):
    _choose_fill_and_no_border(win)
    _drag(win, tool, (100, 100), (180, 150))
    (shape,) = _marks(win, Shape)
    assert shape.color is None
    assert shape.fill_color == pytest.approx(_YELLOW_FILL)
    out = str(tmp_path / "drawn.pdf")
    PyMuPDFEngine().materialize(win.vdoc, out)
    saved = fitz.open(out)
    try:
        page = saved[0]
        (annot,) = [a for a in page.annots() if a.info.get("title") == KLARPDF_AUTHOR]
        assert annot.colors["stroke"] == []
    finally:
        saved.close()


def test_the_pen_and_the_line_keep_their_colour_with_no_border_chosen(win):
    """No Border is for rectangles and ellipses. The pen and the line ignore it, as they ignore
    the fill, and draw in the last colour chosen."""
    win._colors_button._border_row.buttons["Blue"].click()
    _choose_fill_and_no_border(win)
    _drag(win, ArmedTool.PEN, (100, 100), (140, 120), (170, 130))
    _drag(win, ArmedTool.LINE, (100, 200), (220, 260))
    assert _marks(win, InkStroke)[0].color == pytest.approx(_BLUE)
    assert _marks(win, Line)[0].color == pytest.approx(_BLUE)


def test_a_new_shape_with_no_fill_always_gets_its_border(win):
    """A style can hold no border and no fill when it was loaded from a shape that arrived that
    way in a file. The shape drawn with it gets its border back, so it can be seen."""
    win.view.annotations.set_markup_style(MarkupStyle(color=_GREEN, border=False, fill_color=None))
    _drag(win, ArmedTool.RECT, (100, 100), (180, 150))
    (shape,) = _marks(win, Shape)
    assert shape.color == pytest.approx(_GREEN)


def test_the_drag_shows_the_fill_and_no_border(win):
    """What the drag shows is what is drawn: the fill, with no outline."""
    _choose_fill_and_no_border(win)
    overlay = win.view.annotations
    assert overlay.begin_draw(ArmedTool.RECT, _scene(win, 100, 100)) is True
    overlay.update_draw(_scene(win, 180, 150), Qt.KeyboardModifier.NoModifier)
    item = overlay._draw_item
    assert item.pen().style() == Qt.PenStyle.NoPen
    assert item.brush().color().getRgbF()[:3] == pytest.approx(_YELLOW_FILL, abs=0.01)
    overlay.cancel_draw()


def test_the_page_shows_a_shape_with_no_border_without_an_outline(win):
    win.vdoc.add_annotation(0, Shape("rect", (100.0, 100.0, 180.0, 150.0), color=None,
                                     fill_color=_YELLOW_FILL))
    win.view.reload()
    drawn = [i for i in win.view.annotations._items if isinstance(i, QGraphicsRectItem)
             and i.brush().style() != Qt.BrushStyle.NoBrush]
    assert drawn
    assert all(i.pen().style() == Qt.PenStyle.NoPen for i in drawn)


# ---- restyling a selection ------------------------------------------------------------------


def _add(win, *shapes):
    for shape in shapes:
        win.vdoc.add_annotation(0, shape)
    win.view.reload()
    return sorted(_marks(win, Shape), key=lambda a: a.rect[0])


def test_no_border_on_a_selected_shape_and_back_keeps_its_width(win):
    (shape,) = _add(win, Shape("rect", (100.0, 100.0, 180.0, 150.0), color=_RED, width=4.0,
                               fill_color=_YELLOW_FILL))
    win.view.annotations.select_object(0, shape)
    win._colors_button._border_row.remove_button.click()
    (restyled,) = _marks(win, Shape)
    assert restyled.color is None and restyled.width == 4.0
    assert win.undo_stack.undoText() == "Restyle shape"
    win._colors_button._border_row.buttons["Blue"].click()
    (back,) = _marks(win, Shape)
    assert back.color == pytest.approx(_BLUE) and back.width == 4.0


def test_in_a_group_a_shape_with_no_fill_keeps_its_border(win):
    """The buttons show the first shape's style, which has a fill, so No Border is offered. The
    second shape has no fill, and would disappear, so it keeps its border."""
    filled, unfilled = _add(
        win,
        Shape("rect", (100.0, 100.0, 160.0, 140.0), color=_RED, fill_color=_YELLOW_FILL),
        Shape("rect", (200.0, 100.0, 260.0, 140.0), color=_GREEN, fill_color=None),
    )
    overlay = win.view.annotations
    overlay.select_object(0, filled)
    overlay.toggle_object(0, unfilled)
    win._colors_button._border_row.remove_button.click()
    left, right = sorted(_marks(win, Shape), key=lambda a: a.rect[0])
    assert left.color is None
    assert right.color == pytest.approx(_GREEN)
    assert win.undo_stack.undoText() == "Restyle shape"


def test_in_a_group_a_shape_with_no_border_keeps_its_fill(win):
    borderless, bordered = _add(
        win,
        Shape("rect", (100.0, 100.0, 160.0, 140.0), color=None, fill_color=_YELLOW_FILL),
        Shape("rect", (200.0, 100.0, 260.0, 140.0), color=_RED, fill_color=_PINK_FILL),
    )
    overlay = win.view.annotations
    overlay.select_object(0, bordered)                  # its style has a border: No Fill offered
    overlay.toggle_object(0, borderless)
    win._colors_button._fill_row.remove_button.click()
    left, right = sorted(_marks(win, Shape), key=lambda a: a.rect[0])
    assert left.color is None and left.fill_color == pytest.approx(_YELLOW_FILL)
    assert right.fill_color is None and right.color == pytest.approx(_RED)


def test_a_shape_that_arrived_with_neither_still_takes_other_changes(win):
    (hidden,) = _add(win, Shape("rect", (100.0, 100.0, 160.0, 140.0), color=None,
                                fill_color=None))
    win.view.annotations.select_object(0, hidden)
    win._opacity_button._slider.setValue(50)
    (shape,) = _marks(win, Shape)
    assert shape.opacity == pytest.approx(0.5)
    assert shape.color is None and shape.fill_color is None


def test_selecting_a_shape_with_no_border_keeps_the_pens_colour(win):
    """It has no colour to load. The pen and the line go on drawing in theirs."""
    win._colors_button._border_row.buttons["Blue"].click()
    (shape,) = _add(win, Shape("rect", (100.0, 100.0, 160.0, 140.0), color=None,
                               fill_color=_YELLOW_FILL))
    win.view.annotations.select_object(0, shape)
    style = win.view.annotations.current_markup_style
    assert style.border is False
    assert style.color == pytest.approx(_BLUE)
    assert win._colors_button.style() == style
