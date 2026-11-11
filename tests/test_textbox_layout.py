"""A text box looks even, and its text is saved where KlarPDF shows it (M164, #427). Offscreen GUI.

PyMuPDF draws a FreeText's text at the top of the box and pads it by the outline's width, while the
view centred it, so 12 pt text in a 20 pt box saved 4.5 pt higher than it showed. The file now
follows the view: one layout (``textbox_lines``, ``textbox_origin``) decides where the text goes,
the view draws from it, and a save moves the text of PyMuPDF's drawing there.

The first fix went the other way, moving the view to the top, and the owner rejected it on testing:
the boxes went top-heavy, and turning an outline on moved the text. So the tests here hold what they
asked for: the *saved* box is even, an outline moves nothing, and two boxes with the same text are
the same height.

The headless platform resolves no real font (see ``tests/test_whitespace_fidelity.py``), so the view
is compared with the file through the baseline each line is placed at, its top plus its font's
ascent, never through pixels. That a ``QGraphicsSimpleTextItem`` draws its baseline exactly that
far below its top was measured on Windows' own font engine (``PLAN.md`` §M164). The file is real,
so the even look is measured from its rendered ink. The bridge re-saves our boxes through the same
code; its side is in ``tests/test_mcp_textbox_layout.py``.
"""

from __future__ import annotations

import dataclasses

import pymupdf as fitz
import pytest
from PySide6.QtGui import QFontMetricsF
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsSimpleTextItem

from app import PdfApp
from klarpdf.model.edit_engine import PyMuPDFEngine
from klarpdf.model.page_edits import (
    _FREETEXT_BASELINE,
    _FREETEXT_INSET,
    KLARPDF_AUTHOR,
    TEXTBOX_PAD,
    TEXTBOX_PITCH,
    TextBox,
    apply_annotations,
    read_klarpdf_annotations,
    strip_klarpdf_annotations,
    textbox_height,
    textbox_lines,
    textbox_origin,
    translate_mark,
)
from klarpdf.model.virtual_document import VirtualDocument
from store.settings import Settings
from tests.conftest import write_top_aligned_textbox
from viewer.annotations import _TextBoxFrame
from viewer.text_format_bar import TextBoxStyle
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


def _baked_lines(page) -> list[tuple[float, float, float]]:
    """``(x, baseline, bottom)`` of each line ``page`` shows, top first, once its marks are baked."""
    page.parent.bake()
    lines = {}
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            x, y = line["spans"][0]["chars"][0]["origin"]
            lines[round(y, 3)] = (x, y, line["bbox"][3])
    return [lines[y] for y in sorted(lines)]


def _saved_lines(box: TextBox) -> list[tuple[float, float, float]]:
    """The lines of ``box`` as a save draws it: the text every other viewer shows."""
    doc = fitz.open()
    page = doc.new_page()
    apply_annotations(page, (box,))
    lines = _baked_lines(page)
    doc.close()
    return lines


def _painted_lines(win, box: TextBox) -> list[tuple[float, float]]:
    """``(x, baseline)`` of each line the view paints for ``box``, top first, in page points."""
    win.vdoc.add_annotation(0, box)
    win.view.annotations.repaint()
    frames = [it for it in win.view.annotations._items if isinstance(it, _TextBoxFrame)]
    items = sorted((c for c in frames[-1].childItems() if isinstance(c, QGraphicsSimpleTextItem)),
                   key=lambda it: it.pos().y())
    return [(it.pos().x(), it.pos().y() + QFontMetricsF(it.font()).ascent()) for it in items]


def _ink_gaps(box: TextBox) -> tuple[float, float]:
    """The space above and below ``box``'s ink inside the box, in points, from the rendered file."""
    doc = fitz.open()
    page = doc.new_page()
    apply_annotations(page, (box,))
    scale = 8
    edge = box.border_width
    x0, y0, x1, y1 = box.rect
    pix = page.get_pixmap(clip=fitz.Rect(x0 + edge, y0 + edge, x1 - edge, y1 - edge),
                          matrix=fitz.Matrix(scale, scale))
    rows = [y for y in range(pix.height)
            if any(pix.pixel(x, y)[0] < 128 for x in range(0, pix.width, 3))]
    doc.close()
    return edge + rows[0] / scale, edge + (pix.height - 1 - rows[-1]) / scale


def _reopened(box: TextBox, tmp_path, name="box.pdf", rotation=0):
    """``box`` saved and read back, with the page it was read from still open."""
    path = str(tmp_path / name)
    doc = fitz.open()
    page = doc.new_page()
    if rotation:
        page.set_rotation(rotation)
    apply_annotations(page, (box,))
    doc.save(path)
    doc.close()
    doc = fitz.open(path)
    page = doc[0]
    (back,) = read_klarpdf_annotations(page)
    return doc, page, back


# ---- what the library does ---------------------------------------------------------------------


@pytest.mark.parametrize("fontname", ["helv", "tiro", "cour"])
@pytest.mark.parametrize("fontsize", [8.0, 12.0, 24.0])
@pytest.mark.parametrize("border", [0.0, 2.0])
def test_pymupdf_draws_a_text_box_by_fixed_ratios(fontname, fontsize, border):
    """The numbers a save corrects for, taken from PyMuPDF's own drawing. An upgrade that moves one
    fails here, with the reason, rather than quietly putting the file out of step with the view."""
    rect = (100.0, 100.0, 400.0, 300.0)
    doc = fitz.open()
    page = doc.new_page()
    annot = page.add_freetext_annot(fitz.Rect(rect), "Ag one\nAg two\nAg three", fontsize=fontsize,
                                    fontname=fontname, border_width=border)
    annot.update()
    inset = _FREETEXT_INSET * border
    assert [(x, y) for x, y, _ in _baked_lines(page)] == [
        pytest.approx((rect[0] + inset, rect[1] + inset + _FREETEXT_BASELINE * fontsize
                       + row * TEXTBOX_PITCH * fontsize), abs=0.01)
        for row in range(3)
    ]


# ---- the saved box is even ---------------------------------------------------------------------


@pytest.mark.parametrize("fontname", ["helv", "tiro", "cour"])
@pytest.mark.parametrize("fontsize", [8.0, 12.0, 24.0])
@pytest.mark.parametrize("border", [0.0, 1.0])
@pytest.mark.parametrize("height", [32.0, 60.0])
def test_the_saved_text_is_centred_in_its_box(fontname, fontsize, border, height):
    """What the owner asked for, measured in the file: as much space above the capitals as below."""
    above, below = _ink_gaps(TextBox((100, 100, 400, 100 + height), "HHHH", fontsize=fontsize,
                                     fontname=fontname, border_width=border))
    assert above == pytest.approx(below, abs=0.5)


def test_a_one_line_box_at_the_smallest_size_is_even():
    """The box #427 is about: 12 pt text in a 20 pt box was 1 pt from the top and 10 from the bottom."""
    above, below = _ink_gaps(TextBox((150, 82, 230, 102), "JANE DOE", fontsize=12.0))
    assert above == pytest.approx(below, abs=0.5)


def test_several_lines_are_centred_as_a_block():
    above, below = _ink_gaps(TextBox((100, 100, 400, 200), "HHHH\nHHHH\nHHHH", fontsize=12.0))
    assert above == pytest.approx(below, abs=0.5)


def test_text_too_tall_for_its_box_starts_at_the_top_and_runs_off_the_bottom():
    """Centred, it would lose its first line. This is where PyMuPDF puts it anyway."""
    box = TextBox((100, 100, 300, 120), "one\ntwo\nthree", fontsize=12.0)
    assert textbox_origin(box, 3)[1] == pytest.approx(100 + _FREETEXT_BASELINE * 12.0)


def test_a_saved_box_keeps_its_fill_and_its_outline_where_they_were():
    """A save moves the text, and only the text: the fill and the outline are the whole box."""
    doc = fitz.open()
    page = doc.new_page()
    apply_annotations(page, (TextBox((100, 100, 300, 140), "HHHH", fontsize=12.0, border_width=1.0,
                                     fill_color=(1.0, 1.0, 0.0)),))
    pix = page.get_pixmap(clip=fitz.Rect(100, 100, 300, 140))
    assert pix.pixel(150, 3) == (255, 255, 0)            # the fill still reaches the box's top
    assert pix.pixel(150, pix.height - 4) == (255, 255, 0)
    doc.close()


# ---- the view shows it where the file has it ----------------------------------------------------


@pytest.mark.parametrize("box", [
    TextBox((150, 82, 230, 102), "JANE DOE", fontsize=12.0),                 # the reported box
    TextBox((100, 100, 400, 300), "one\ntwo\nthree", fontsize=24.0, fontname="tiro"),
    TextBox((100, 100, 400, 300), "bordered\nnote", fontsize=8.0, fontname="cour",
            border_width=1.0),
    TextBox((100, 100, 400, 300), "a thick border", fontsize=12.0, border_width=4.0),
    TextBox((100, 100, 180, 200), "some words that wrap in a narrow box", fontsize=12.0,
            auto_width=False),
], ids=["reported", "three-lines", "outlined", "thick-border", "wrapped"])
def test_the_view_puts_each_line_where_the_file_does(win, box):
    painted = _painted_lines(win, box)
    saved = [(x, y) for x, y, _ in _saved_lines(box)]
    assert painted == [pytest.approx(line, abs=0.01) for line in saved]


# ---- an outline moves nothing -------------------------------------------------------------------


@pytest.mark.parametrize("text", ["JANE DOE", "two\nlines"])
def test_an_outline_does_not_move_the_saved_text(text):
    rect = (100.0, 100.0, 300.0, 140.0)
    plain = _saved_lines(TextBox(rect, text, fontsize=12.0))
    outlined = _saved_lines(TextBox(rect, text, fontsize=12.0, border_width=1.0))
    assert [line[:2] for line in outlined] == [pytest.approx(line[:2], abs=0.01) for line in plain]


def test_an_outline_does_not_move_the_text_in_the_view(win):
    rect = (100.0, 100.0, 300.0, 140.0)
    plain = _painted_lines(win, TextBox(rect, "JANE DOE", fontsize=12.0))
    outlined = _painted_lines(win, TextBox(rect, "JANE DOE", fontsize=12.0, border_width=1.0))
    assert outlined == [pytest.approx(line, abs=0.01) for line in plain]


@pytest.mark.parametrize("text", ["JANE DOE", "two\nlines"])
def test_an_outline_does_not_change_the_box(win, text):
    """Turning the outline on in the editor must not resize the box, or the centred text moves."""
    win.view.set_zoom(3.0)
    win.view.arm(ArmedTool.TEXTBOX)
    ov = win.view.annotations
    ov._on_style_changed(TextBoxStyle(fontsize=12.0))
    ov.place_textbox(win.view.scene_rect_for_box(0, (100, 120, 120, 140)).center())
    ov._editor.setPlainText(text)
    before = ov._editor_rect
    ov._style = TextBoxStyle(fontsize=12.0, border_width=1.0)
    ov.reposition_editor()
    assert ov._editor_rect == pytest.approx(before)


# ---- line breaks --------------------------------------------------------------------------------


def _drawn_text(page) -> list[bytes]:
    return [line for line in page.first_annot._getAP().splitlines() if line.endswith(b" Tj")]


@pytest.mark.parametrize("width", [80.0, 110.0, 160.0])
def test_a_fixed_width_box_breaks_where_the_view_does(width):
    box = TextBox((100, 100, 100 + width, 200), "some words to wrap at a narrow width",
                  fontsize=12.0, auto_width=False)
    doc = fitz.open()
    page = doc.new_page()
    apply_annotations(page, (box,))
    assert [line.decode() for line in _drawn_text(page)] == [f"({text}) Tj"
                                                             for text in textbox_lines(box)]
    doc.close()


def test_wrapping_does_not_change_what_the_box_says(tmp_path):
    """The drawing is handed the wrapped lines, but the box's text stays what was typed."""
    text = "some words to wrap at a narrow width"
    doc, page, back = _reopened(TextBox((100, 100, 180, 200), text, fontsize=12.0, auto_width=False),
                                tmp_path)
    assert back.text == text
    assert back.auto_width is False
    doc.close()


def test_a_box_that_wraps_only_because_of_the_pad_reopens_wrapped(tmp_path):
    """Its text is wider than the room inside the pad but narrower than the box, which is where
    a guess from the width alone read it as unwrapped and showed one line against the file's two."""
    width = fitz.get_text_length("eleven char", fontname="helv", fontsize=12.0) + 2.0
    box = TextBox((100, 100, 100 + width, 140), "eleven char", fontsize=12.0, auto_width=False)
    assert textbox_lines(box) == ["eleven", "char"]
    doc, page, back = _reopened(box, tmp_path)
    assert back.auto_width is False
    assert textbox_lines(back) == ["eleven", "char"]
    doc.close()


def test_a_narrowed_box_whose_text_still_fits_reopens_as_one_line(tmp_path):
    doc, page, back = _reopened(TextBox((100, 100, 170, 120), "11/05/2026", fontsize=12.0,
                                        auto_width=False), tmp_path)
    assert textbox_lines(back) == ["11/05/2026"]
    doc.close()


# ---- box heights --------------------------------------------------------------------------------


def _reopen_editor(ov, box: TextBox) -> None:
    ov._editing = (0, box)
    ov._open_editor(0, box.rect, text=box.text, style=TextBoxStyle.from_textbox(box),
                    auto_width=box.auto_width)


@pytest.mark.parametrize("zoom", [1.0, 1.75, 3.0])
@pytest.mark.parametrize("width", [74.0, 69.8])
def test_narrowing_a_box_adds_no_line_its_text_does_not_need(win, zoom, width):
    """Issue 2 of the owner's test: 60 pt of text in a 70 pt box still fits on one line, but the
    editor estimated two and made the box 33 pt tall, at some zooms and not others."""
    win.view.set_zoom(zoom)
    ov = win.view.annotations
    box = TextBox((100, 100, 100 + width, 120), "11/05/2026", fontsize=12.0, border_width=1.0,
                  auto_width=False)
    _reopen_editor(ov, box)
    assert ov._editor_rect[3] - ov._editor_rect[1] == pytest.approx(20.0)
    ov._close_editor()


@pytest.mark.parametrize("zoom", [1.0, 1.75, 3.0])
def test_a_box_is_the_same_height_at_every_zoom(win, zoom):
    win.view.set_zoom(zoom)
    win.view.arm(ArmedTool.TEXTBOX)
    ov = win.view.annotations
    ov._on_style_changed(TextBoxStyle(fontsize=12.0))
    ov.place_textbox(win.view.scene_rect_for_box(0, (100, 120, 120, 140)).center())
    ov._editor.setPlainText("11/05/2026")
    assert ov._editor_rect[3] - ov._editor_rect[1] == pytest.approx(20.0)
    ov._editor.setPlainText("one\ntwo\nthree")
    assert ov._editor_rect[3] - ov._editor_rect[1] == pytest.approx(textbox_height(3, 12.0, 0.0))
    ov._close_editor()


def test_reopening_a_box_and_changing_nothing_leaves_it_alone(win):
    """Opening the editor measures the box afresh, at whatever the zoom is, and that alone used to
    resize it when it closed."""
    box = TextBox((100, 100, 300, 137), "JANE DOE", fontsize=12.0)
    win.vdoc.add_annotation(0, box)
    ov = win.view.annotations
    _reopen_editor(ov, box)
    ov._commit_textbox()
    assert win.vdoc.page_annotations(0)[-1] is box


@pytest.mark.parametrize("border", [0.0, 2.0])
def test_a_long_note_sized_by_the_width_handle_keeps_its_last_line(win, border):
    """The file clips its text to the box, and the width handle sized the box from Qt's line
    spacing, which is tighter than the file's. On Windows a 12 pt box lost the bottom of its last
    line from five lines on; the headless font's spacing is looser, so 30 lines are needed here.

    Every line has to be counted, not only the last one seen: a line wholly outside the clip is not
    extracted at all, and with a border the 30th line was, so the 29th looked like the last."""
    box = TextBox((100, 100, 300, 120), "\n".join(["gjpqy Jay"] * 30), fontsize=12.0,
                  border_width=border, auto_width=False)
    rect = win.view.annotations._reflow_textbox_rect(box, 200.0, 0)
    saved = _saved_lines(TextBox(rect, box.text, fontsize=12.0, border_width=border))
    assert len(saved) == 30
    assert saved[-1][2] <= rect[3] - border / 2


def test_a_long_note_typed_in_the_editor_keeps_its_last_line(win):
    win.view.set_zoom(2.0)
    win.view.arm(ArmedTool.TEXTBOX)
    ov = win.view.annotations
    ov._on_style_changed(TextBoxStyle(fontsize=12.0))
    ov.place_textbox(win.view.scene_rect_for_box(0, (60, 60, 80, 80)).center())
    ov._editor.setPlainText("\n".join(["gjpqy Jay"] * 40))
    rect = ov._editor_rect
    saved = _saved_lines(TextBox(rect, ov._editor.toPlainText(), fontsize=12.0))
    assert len(saved) == 40
    assert saved[-1][2] <= rect[3]


# ---- a box already saved stays where the file has it --------------------------------------------


@pytest.fixture
def old_pdf(tmp_path) -> str:
    return write_top_aligned_textbox(str(tmp_path / "old.pdf"))


def _first_line(path: str) -> tuple[float, float]:
    """Where the one box in ``path`` starts its first line, from the box's top-left."""
    with fitz.open(path) as doc:
        page = doc[0]
        (box,) = read_klarpdf_annotations(page)
    return box.origin


def test_the_view_shows_a_saved_box_where_the_file_has_it(win, old_pdf):
    """Text the owner lined up against a form's underline must not drop 4.5 pt below it when this
    version opens the file. It is a real box: drawn at the top, as every earlier version saved it."""
    with fitz.open(old_pdf) as doc:
        page = doc[0]
        (box,) = read_klarpdf_annotations(page)
        saved = [line for line in _baked_lines(page) if line[0] >= box.rect[0]]    # not the form's
    assert _painted_lines(win, box) == [pytest.approx(line[:2], abs=0.01) for line in saved]


def test_a_save_that_redraws_a_saved_box_does_not_move_its_text(old_pdf, tmp_path):
    before = _first_line(old_pdf)
    vdoc = VirtualDocument.from_path(old_pdf)
    vdoc.rotate_pages([0], 90)
    out = str(tmp_path / "rotated.pdf")
    PyMuPDFEngine().materialize(vdoc, out)
    assert _first_line(out) == pytest.approx(before, abs=0.01)


def test_redrawing_a_saved_box_changes_no_pixel():
    """Found on the owner's own document. Reading the baseline back rounded to two decimals moved a
    box by 0.005 pt, which is invisible but changed 752 pixels of the page. The box's position here
    is an awkward one on purpose: a round number survives any rounding.

    The position is checked as a number as well. Whether a shift that small changes a pixel depends
    on where it falls, so the pixels alone passed with the rounding put back."""
    doc = fitz.open()
    page = doc.new_page()
    box = page.add_freetext_annot(fitz.Rect(121.03157, 643.08372, 216.94284, 663.11033), "JANE DOE",
                                  fontsize=12, fontname="helv", text_color=(0, 0, 0))
    box.set_info(title=KLARPDF_AUTHOR)
    box.update()
    before = page.get_pixmap(dpi=100).samples
    marks = read_klarpdf_annotations(page)
    assert marks[0].origin == pytest.approx((0.0, 9.6), abs=0.001)
    strip_klarpdf_annotations(page)
    assert page.get_pixmap(dpi=100).samples != before
    apply_annotations(page, marks)
    assert page.get_pixmap(dpi=100).samples == before
    doc.close()


def test_moving_a_saved_box_carries_its_text_with_it(old_pdf, tmp_path):
    vdoc = VirtualDocument.from_path(old_pdf)
    (old,) = vdoc.page_annotations(0)
    vdoc.replace_annotation(0, old, translate_mark(old, 10.0, 3.0))
    out = str(tmp_path / "moved.pdf")
    PyMuPDFEngine().materialize(vdoc, out)
    assert _first_line(out) == pytest.approx(old.origin, abs=0.01)


def test_editing_a_saved_box_gives_it_the_even_layout(win, old_pdf):
    with fitz.open(old_pdf) as doc:
        page = doc[0]
        (box,) = read_klarpdf_annotations(page)
    win.vdoc.add_annotation(0, box)
    ov = win.view.annotations
    _reopen_editor(ov, box)
    ov._editor.setPlainText(box.text + " edited")
    ov._commit_textbox()
    assert win.vdoc.page_annotations(0)[-1].origin is None


def test_resizing_a_saved_box_gives_it_the_even_layout(win, old_pdf):
    with fitz.open(old_pdf) as doc:
        page = doc[0]
        (box,) = read_klarpdf_annotations(page)
    win.vdoc.add_annotation(0, box)
    ov = win.view.annotations
    ov._set_selection([(0, box)])
    ov._resize_handle = "e"
    ov._resize_page = 0
    ov._resize_marks = [box]
    ov._resize_textbox = box
    ov._resize_rect = (box.rect[0], box.rect[1], box.rect[2] + 30, box.rect[3])
    ov._resize_ghost = QGraphicsRectItem()
    win.view.scene().addItem(ov._resize_ghost)
    ov.finish_resize()
    assert win.vdoc.page_annotations(0)[-1].origin is None


@pytest.mark.parametrize("box", [
    TextBox((100, 100, 300, 137), "centred", fontsize=12.0),
    TextBox((100, 100, 300, 137), "outlined", fontsize=12.0, border_width=1.0),
    TextBox((100, 100, 300, 200), "two\nlines", fontsize=12.0, fontname="tiro"),
    TextBox((100, 100, 300, 200), "\n\nblank lines above", fontsize=12.0),
    TextBox((100, 100, 300, 200), " \n \nspaces above", fontsize=12.0),
    TextBox((100, 100, 300, 200), "   \nmore spaces above", fontsize=12.0),
    TextBox((100, 100, 180, 200), "some words that wrap in a narrow box", fontsize=12.0,
            auto_width=False),
], ids=["plain", "outlined", "two-lines", "blank-above", "space-above", "spaces-above", "wrapped"])
@pytest.mark.parametrize("rotation", [0, 90, 270])
def test_saving_a_box_over_and_over_does_not_move_its_text(box, rotation, tmp_path):
    """A new box reads back at the place the layout gave it, so the two agree. If they did not,
    every save would walk the text across the box."""
    doc, page, back = _reopened(box, tmp_path, "first.pdf", rotation)
    laid_out = textbox_origin(box, len(textbox_lines(box)))
    assert textbox_origin(back, 0) == pytest.approx(laid_out, abs=0.01)
    doc.close()
    doc, page, again = _reopened(back, tmp_path, "second.pdf", rotation)
    assert textbox_origin(again, 0) == pytest.approx(laid_out, abs=0.01)
    assert dataclasses.replace(again, origin=None) == box
    doc.close()


# ---- the editor ---------------------------------------------------------------------------------


@pytest.mark.parametrize("zoom", [1.0, 1.3, 1.75, 2.4])
@pytest.mark.parametrize("border", [0.0, 1.0])
def test_the_editor_shows_the_text_where_it_will_stay(win, zoom, border):
    """While typing, the first line sits where the committed box, and the file, will put it, so
    committing the edit does not move the text.

    A widget sits on whole pixels, so half a pixel is the most it can miss by. An earlier version
    allowed a pixel and still failed at 175% on Linux, by 0.016 px. It rounded the box's corner and
    its offset separately, and the test's own target a third time."""
    view = win.view
    view.set_zoom(zoom)
    view.arm(ArmedTool.TEXTBOX)
    ov = view.annotations
    ov._on_style_changed(TextBoxStyle(fontsize=12.0, border_width=border))
    ov.place_textbox(view.scene_rect_for_box(0, (100, 120, 120, 140)).center())
    editor = ov._editor
    editor.setPlainText("JANE DOE")
    win.repaint()
    PdfApp.instance().processEvents()
    line = editor.cursorRect(editor.textCursor())
    origin = editor.viewport().mapTo(view.viewport(), line.topLeft())
    baseline = origin.y() + QFontMetricsF(editor.font()).ascent()
    left, drawn = textbox_origin(TextBox(ov._editor_rect, "JANE DOE", fontsize=12.0,
                                         border_width=border), 1)
    want = view.viewportTransform().map(
        view.scene_rect_for_box(0, (left, drawn, left + 1, drawn + 1)).topLeft())
    assert abs(origin.x() - want.x()) <= 0.5 + 1e-6
    assert abs(baseline - want.y()) <= 0.5 + 1e-6


def test_the_text_starts_the_same_pad_in_from_the_edge():
    """The same gap with and without an outline, and only a thick outline widens it."""
    rect = (100.0, 100.0, 300.0, 140.0)
    assert _saved_lines(TextBox(rect, "x"))[0][0] == pytest.approx(100 + TEXTBOX_PAD, abs=0.01)
    wide = _saved_lines(TextBox(rect, "x", border_width=4.0))[0][0]
    assert wide == pytest.approx(100 + _FREETEXT_INSET * 4.0, abs=0.01)
