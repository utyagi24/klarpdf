"""A text box's fill is what its own drawing paints, not only what its ``/C`` says (M163, #426).

Microsoft Edge keeps our author tag when it moves one of our text boxes, adds ``/C [0 0 0]`` and
draws the box with no background. ``parse_annotation`` read that ``/C`` as the box's fill, so the
box came back solid black under its black text, and a save that rewrote the page wrote the black
box into the file. Every viewer shows a box's drawing, so the fill now comes from ``/C`` only when
that drawing paints one.

The bridge re-saves our text boxes through the same code; its side is in
``tests/test_mcp_freetext_fill.py``. Headless.
"""

from __future__ import annotations

import dataclasses
import random

import pymupdf as fitz
import pytest

from klarpdf.model.edit_engine import PyMuPDFEngine
from klarpdf.model.foreign_annots import adopt_annotation
from klarpdf.model.page_edits import (
    KLARPDF_AUTHOR,
    TextBox,
    _content_operators,
    apply_annotations,
    read_klarpdf_annotations,
)
from klarpdf.model.virtual_document import VirtualDocument
from tests.conftest import FILLED_RECT, write_edge_moved_textbox

_YELLOW = (1.0, 1.0, 0.0)


@pytest.fixture
def edge_pdf(tmp_path) -> str:
    return write_edge_moved_textbox(str(tmp_path / "edge.pdf"))


def _box_drawing(path: str) -> bytes:
    """The drawing of the one text box KlarPDF wrote into ``path``."""
    with fitz.open(path) as doc:
        (annot,) = [a for a in doc[0].annots() if a.info.get("title") == KLARPDF_AUTHOR]
        return annot._getAP()


def _one_box(box: TextBox):
    doc = fitz.open()
    page = doc.new_page()
    apply_annotations(page, (box,))
    return doc, page


# ---- reading a box back ----------------------------------------------------------------------


def test_a_box_edge_moved_reads_back_with_no_fill(edge_pdf):
    with fitz.open(edge_pdf) as doc:
        (box,) = read_klarpdf_annotations(doc[0])
    assert box.fill_color is None
    assert (box.text, box.fontsize, box.color) == ("JANE DOE", 12.0, (0.0, 0.0, 0.0))


def test_a_box_we_filled_still_reads_its_fill():
    doc, page = _one_box(TextBox((150, 82, 230, 102), "JANE DOE", fill_color=_YELLOW))
    (box,) = read_klarpdf_annotations(page)
    assert box.fill_color == pytest.approx(_YELLOW)


def test_a_box_with_no_drawing_keeps_its_fill(tmp_path):
    """MuPDF draws a box that has no drawing of its own from its ``/C``, so ``/C`` is its fill."""
    doc, page = _one_box(TextBox((150, 82, 230, 102), "JANE DOE", fill_color=_YELLOW))
    doc.xref_set_key(page.first_annot.xref, "AP", "null")
    reopened = fitz.open("pdf", doc.tobytes())
    (box,) = read_klarpdf_annotations(reopened[0])
    assert box.fill_color == pytest.approx(_YELLOW)


def test_text_that_reads_like_an_operator_is_not_a_fill():
    """The box's own words are in its drawing: ``(a f b) Tj`` holds an ``f`` that paints nothing."""
    doc, page = _one_box(TextBox((150, 82, 300, 102), "a f b* B (f) \\ f"))
    doc.xref_set_key(page.first_annot.xref, "C", "[0 0 0]")
    assert b"f" in page.first_annot._getAP().split()       # what a lexer must not mistake for a fill
    (box,) = read_klarpdf_annotations(page)
    assert box.fill_color is None


def test_the_lexer_finishes_on_any_bytes():
    """Every branch must consume a byte: one that did not would hang the opening of a document,
    which is what a break of the string branch did while M163 was being checked."""
    rng = random.Random(163)
    alphabet = b"()<>[]{}/%\\ \n\rfFBbIDEI0123.-"
    for _ in range(2000):
        data = bytes(rng.choice(alphabet) for _ in range(rng.randrange(1, 60)))
        assert sum(1 for _ in _content_operators(data)) <= len(data)


def test_a_fill_drawn_through_a_nested_form_counts():
    """A drawing may paint its background inside a form it draws, rather than in its own stream."""
    doc, page = _one_box(TextBox((150, 82, 230, 102), "JANE DOE", fill_color=_YELLOW))
    annot = page.first_annot
    inner = int(doc.xref_get_key(annot.xref, "AP/N")[1].split()[0])
    outer = doc.get_new_xref()
    doc.update_object(outer, f"<< /Type /XObject /Subtype /Form /BBox [0 0 80 20] "
                             f"/Resources << /XObject << /Fm0 {inner} 0 R >> >> >>")
    doc.update_stream(outer, b"q /Fm0 Do Q")
    doc.xref_set_key(annot.xref, "AP/N", f"{outer} 0 R")
    (box,) = read_klarpdf_annotations(page)
    assert box.fill_color == pytest.approx(_YELLOW)


def test_an_edge_box_from_another_author_adopts_with_no_fill(tmp_path):
    """Adoption (M68) reads a foreign box with the same parser, so the same check applies."""
    path = write_edge_moved_textbox(str(tmp_path / "theirs.pdf"), author="Someone")
    with fitz.open(path) as doc:
        page = doc[0]
        box = adopt_annotation(page.first_annot)
    assert isinstance(box, TextBox) and box.fill_color is None


# ---- saving it again -------------------------------------------------------------------------


def test_moving_the_box_again_saves_it_with_no_fill(edge_pdf, tmp_path):
    vdoc = VirtualDocument.from_path(edge_pdf)
    (old,) = vdoc.page_annotations(0)
    x0, y0, x1, y1 = old.rect
    vdoc.replace_annotation(0, old, dataclasses.replace(old, rect=(x0, y0 + 3, x1, y1 + 3)))
    out = str(tmp_path / "moved.pdf")
    PyMuPDFEngine().materialize(vdoc, out)
    assert not FILLED_RECT.search(_box_drawing(out))


def test_rotating_the_page_saves_the_box_with_no_fill(edge_pdf, tmp_path):
    """A rotation rewrites the page, so its marks are stripped and drawn again from the model."""
    vdoc = VirtualDocument.from_path(edge_pdf)
    vdoc.rotate_pages([0], 90)
    out = str(tmp_path / "rotated.pdf")
    PyMuPDFEngine().materialize(vdoc, out)
    assert not FILLED_RECT.search(_box_drawing(out))


# ---- with M164's drawing ---------------------------------------------------------------------


def _move_down(path: str, out: str) -> None:
    """Move the box 3 pt down and save, which draws it again from the model."""
    vdoc = VirtualDocument.from_path(path)
    (old,) = vdoc.page_annotations(0)
    x0, y0, x1, y1 = old.rect
    vdoc.replace_annotation(0, old, dataclasses.replace(old, rect=(x0, y0 + 3, x1, y1 + 3)))
    PyMuPDFEngine().materialize(vdoc, out)


def _text_origin(path: str) -> tuple[float, float]:
    """Where the one box in ``path`` starts its text, read from its drawing."""
    with fitz.open(path) as doc:
        page = doc[0]
        (annot,) = [a for a in page.annots() if a.info.get("title") == KLARPDF_AUTHOR]
        (line,) = [line for block in annot.get_text("rawdict")["blocks"]
                   for line in block.get("lines", [])]
        return line["spans"][0]["chars"][0]["origin"]


def _draw_it_another_way(path: str, out: str) -> None:
    """Give the box a drawing PyMuPDF did not write: no ``cm`` line, the text placed by its own
    ``Tm``, and a font in ``/DA`` KlarPDF has no name for. Edge's own file holds personal data, so
    this stands in for what the fixture leaves as PyMuPDF wrote it."""
    with fitz.open(path) as doc:
        page = doc[0]
        annot = page.first_annot
        drawing = int(doc.xref_get_key(annot.xref, "AP/N")[1].split()[0])
        doc.update_stream(drawing, b"q\nBT\n/Helv 12 Tf\n0 g\n1 0 0 1 153 746.5 Tm\n"
                                   b"(JANE DOE) Tj\nET\nQ\n")
        doc.xref_set_key(annot.xref, "DA", "(0 g /Arial 12 Tf)")
        doc.save(out)


def test_a_box_we_filled_keeps_it_through_repeated_saves(tmp_path):
    """A save rewrites the box's drawing to place its text (M164), and that drawing is what says
    whether the box is filled. So a rewrite that lost the fill would lose it for good."""
    path = str(tmp_path / "filled.pdf")
    doc, _ = _one_box(TextBox((150, 82, 230, 102), "JANE DOE", fill_color=_YELLOW))
    doc.save(path)
    doc.close()
    for save in range(3):
        out = str(tmp_path / f"filled{save}.pdf")
        _move_down(path, out)
        assert FILLED_RECT.search(_box_drawing(out))
        (box,) = VirtualDocument.from_path(out).page_annotations(0)
        assert box.fill_color == pytest.approx(_YELLOW)
        path = out


def test_a_box_another_program_drew_moves_with_its_text_and_stays_unfilled(edge_pdf, tmp_path):
    """A box read back keeps where its drawing put the text (M164), whoever drew it. Moving it
    carries the text along, the same distance, with no fill."""
    foreign = str(tmp_path / "foreign.pdf")
    _draw_it_another_way(edge_pdf, foreign)
    x, y = _text_origin(foreign)
    assert (x, y) == pytest.approx((153, 842 - 746.5)), "fixture's text is not where it was put"
    out = str(tmp_path / "moved.pdf")
    _move_down(foreign, out)
    assert not FILLED_RECT.search(_box_drawing(out))
    assert _text_origin(out) == pytest.approx((x, y + 3), abs=0.01)
