"""An auto-width box narrower than its own text keeps it on one line (M166, #430).

Microsoft Edge shrinks a text box to fit when it moves one, by its own font's widths, which can leave
the box a point or two short of ours. KlarPDF showed such a box's text whole, and a save that drew it
again let MuPDF wrap it, so the last word fell below a one-line box and no viewer showed it. A box is
now widened where it enters the model, so the view and every save see the same box.

The bridge goes through the same read; its side is in ``tests/test_mcp_textbox_narrow.py``. Headless.
"""

from __future__ import annotations

import dataclasses
import random

import pymupdf as fitz
import pytest

from klarpdf.model.edit_engine import PyMuPDFEngine
from klarpdf.model.page_edits import (
    KLARPDF_AUTHOR,
    TextBox,
    apply_annotations,
    read_klarpdf_annotations,
    textbox_height,
    textbox_lines,
)
from klarpdf.model.virtual_document import VirtualDocument
from tests.conftest import write_cut_off_textbox, write_narrow_textbox


def _read(path: str) -> TextBox:
    with fitz.open(path) as doc:
        (box,) = read_klarpdf_annotations(doc[0])
    return box


def _nudge(path: str, out: str, dy: float = 1.0) -> None:
    """Move the box and save, which draws it again from the model: the issue's steps."""
    vdoc = VirtualDocument.from_path(path)
    (old,) = vdoc.page_annotations(0)
    x0, y0, x1, y1 = old.rect
    vdoc.replace_annotation(0, old, dataclasses.replace(old, rect=(x0, y0 + dy, x1, y1 + dy)))
    PyMuPDFEngine().materialize(vdoc, out)


def _drawing(path: str) -> bytes:
    with fitz.open(path) as doc:
        page = doc[0]
        (annot,) = [a for a in page.annots() if a.info.get("title") == KLARPDF_AUTHOR]
        return annot._getAP()


def _shown(path: str) -> str:
    """What a viewer can see of the box: its text, as the drawing's clip leaves it."""
    with fitz.open(path) as doc:
        page = doc[0]
        (annot,) = [a for a in page.annots() if a.info.get("title") == KLARPDF_AUTHOR]
        return " ".join(annot.get_text("text").split())


def _first_line_at(path: str) -> tuple[float, float]:
    with fitz.open(path) as doc:
        page = doc[0]
        (annot,) = [a for a in page.annots() if a.info.get("title") == KLARPDF_AUTHOR]
        line = next(line for block in annot.get_text("rawdict")["blocks"]
                    for line in block.get("lines", []))
        return line["spans"][0]["chars"][0]["origin"]


# ---- the issue's steps -----------------------------------------------------------------------


def test_a_narrow_box_saves_its_text_on_one_line(tmp_path):
    src = write_narrow_textbox(str(tmp_path / "narrow.pdf"))
    out = str(tmp_path / "nudged.pdf")
    _nudge(src, out)
    assert _drawing(out).count(b"Tj") == 1
    assert _shown(out) == "JANE DOE"


def test_a_box_a_save_already_cut_off_gets_its_word_back(tmp_path):
    """The owner's form: the second word was in ``/Contents`` all along, and only the drawing had
    lost it. The drawing shows one line, so the box reads as one line, and a redraw restores it."""
    src = write_cut_off_textbox(str(tmp_path / "cut.pdf"))
    assert _shown(src) == "JANE"
    out = str(tmp_path / "nudged.pdf")
    _nudge(src, out)
    assert _shown(out) == "JANE DOE"


def test_the_text_stays_where_it_was(tmp_path):
    src = write_narrow_textbox(str(tmp_path / "narrow.pdf"))
    x, y = _first_line_at(src)
    out = str(tmp_path / "nudged.pdf")
    _nudge(src, out, dy=3.0)
    assert _first_line_at(out) == pytest.approx((x, y + 3), abs=0.01)


def test_only_the_right_edge_moves(tmp_path):
    src = write_narrow_textbox(str(tmp_path / "narrow.pdf"))
    with fitz.open(src) as doc:
        page = doc[0]
        file_rect = page.first_annot.rect
    box = _read(src)
    assert box.rect[0] == pytest.approx(file_rect.x0)
    assert box.rect[1] == pytest.approx(file_rect.y0)
    assert box.rect[3] == pytest.approx(file_rect.y1)
    assert box.rect[2] > file_rect.x1


def test_the_view_and_the_file_agree_on_the_lines(tmp_path):
    src = write_narrow_textbox(str(tmp_path / "narrow.pdf"))
    box = _read(src)
    assert textbox_lines(box) == ["JANE DOE"]
    out = str(tmp_path / "nudged.pdf")
    _nudge(src, out)
    assert _shown(out) == textbox_lines(box)[0]


# ---- what it has to measure ------------------------------------------------------------------


@pytest.mark.parametrize("fontname", ["helv", "tiro", "cour"])
@pytest.mark.parametrize("border", [0.0, 1.0, 2.5])
def test_every_font_and_border_stays_on_one_line(tmp_path, fontname, border):
    """MuPDF wraps at the width less three borders, so a box that only fits its text wraps with an
    outline on."""
    src = write_narrow_textbox(str(tmp_path / "narrow.pdf"), fontname=fontname, border_width=border)
    out = str(tmp_path / "nudged.pdf")
    _nudge(src, out)
    assert _drawing(out).count(b"Tj") == 1
    assert _shown(out) == "JANE DOE"


def test_a_box_whose_text_starts_further_in_is_not_clipped_on_the_right(tmp_path):
    """The drawing clips to the box, so the room needed is the text's start plus its width. A box
    only as wide as the text keeps it on one line and cuts off its end."""
    widest = fitz.get_text_length("JANE DOE", fontname="helv", fontsize=12)
    doc = fitz.open()
    page = doc.new_page()
    apply_annotations(page, (TextBox((150.0, 82.0, 400.0, 102.0), "JANE DOE", fontsize=12,
                                     origin=(6.0, 14.0)),))
    annot = page.first_annot
    narrowed = f"[150 740 {150 + widest + 3:.3f} 760]"      # holds the text, not its start as well
    doc.xref_set_key(annot.xref, "Rect", narrowed)
    doc.xref_set_key(int(doc.xref_get_key(annot.xref, "AP/N")[1].split()[0]), "BBox", narrowed)
    path = str(tmp_path / "inset.pdf")
    doc.save(path)
    doc.close()
    box = _read(path)
    assert box.origin[0] == pytest.approx(6.0, abs=0.01)
    assert box.rect[2] >= 150 + 6 + widest


def test_the_longest_explicit_line_decides(tmp_path):
    text = "A\nJANE DOE\nB"
    src = write_narrow_textbox(str(tmp_path / "narrow.pdf"), text=text)
    width = fitz.get_text_length("JANE DOE", fontname="helv", fontsize=12)
    box = _read(src)
    assert box.auto_width and box.rect[2] - box.rect[0] >= width


def test_a_trailing_space_asks_for_no_room(tmp_path):
    """MuPDF does not wrap on one, so a box that holds the words needs nothing more."""
    width = fitz.get_text_length("JANE DOE", fontname="helv", fontsize=12)
    box = TextBox((150.0, 82.0, 150.0 + 2 + width + 0.5, 102.0), "JANE DOE ", fontsize=12)
    doc = fitz.open()
    page = doc.new_page()
    apply_annotations(page, (box,))
    path = str(tmp_path / "trailing.pdf")
    doc.save(path)
    doc.close()
    with fitz.open(path) as check:
        page = check[0]
        file_rect = page.first_annot.rect
    assert _read(path).rect[2] == pytest.approx(file_rect.x1)


def test_any_narrow_box_redraws_on_one_line(tmp_path):
    """Seeded, because the wrap has no slack at all: a width that lands a rounding error short of
    the text wraps."""
    rng = random.Random(430)
    for case in range(60):
        size = rng.choice([8, 9.5, 11, 12, 13.7, 17.5])
        text = rng.choice(["JANE DOE", "A B", "Print Name Here", "x y z w v"])
        short = rng.uniform(0.0, 3.0)
        src = write_narrow_textbox(str(tmp_path / f"n{case}.pdf"), short_by=max(short, 0.001),
                                   text=text, fontsize=size)
        out = str(tmp_path / f"o{case}.pdf")
        _nudge(src, out)
        assert _drawing(out).count(b"Tj") == 1, (size, text, short)


# ---- what it leaves alone --------------------------------------------------------------------


def test_a_box_wide_enough_is_left_as_it_is(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    apply_annotations(page, (TextBox((150.0, 82.0, 300.0, 102.0), "JANE DOE", fontsize=12),))
    path = str(tmp_path / "wide.pdf")
    doc.save(path)
    doc.close()
    assert _read(path).rect == pytest.approx((150.0, 82.0, 300.0, 102.0))


def test_a_box_wrapped_on_purpose_is_left_as_it_is(tmp_path):
    text = "a note wrapped at this width on purpose"
    lines = textbox_lines(TextBox((150.0, 82.0, 210.0, 82.0), text, fontsize=12, auto_width=False))
    height = textbox_height(len(lines), 12, 0.0)          # room for its lines, so nothing to fix
    box = TextBox((150.0, 82.0, 210.0, 82.0 + height), text, fontsize=12, auto_width=False)
    doc = fitz.open()
    page = doc.new_page()
    apply_annotations(page, (box,))
    path = str(tmp_path / "wrapped.pdf")
    doc.save(path)
    doc.close()
    back = _read(path)
    assert not back.auto_width
    assert back.rect == pytest.approx(box.rect)


def test_a_save_that_redraws_nothing_leaves_the_file_s_box_alone(tmp_path):
    src = write_narrow_textbox(str(tmp_path / "narrow.pdf"))
    with fitz.open(src) as doc:
        page = doc[0]
        before = page.first_annot.rect
    out = str(tmp_path / "same.pdf")
    PyMuPDFEngine().materialize(VirtualDocument.from_path(src), out)
    with fitz.open(out) as doc:
        page = doc[0]
        assert page.first_annot.rect == before


def test_repeated_saves_do_not_grow_the_box(tmp_path):
    path = write_narrow_textbox(str(tmp_path / "narrow.pdf"))
    widths = []
    for save in range(4):
        out = str(tmp_path / f"save{save}.pdf")
        _nudge(path, out)
        widths.append(_read(out).rect[2] - _read(out).rect[0])
        path = out
    assert max(widths) - min(widths) < 0.001


def test_a_box_at_the_page_edge_still_saves(tmp_path):
    """Widening can carry a box past the page. Its text was already running there, so nothing is
    lost, and PyMuPDF has to accept the rectangle."""
    text = "JANE DOE"
    width = fitz.get_text_length(text, fontname="helv", fontsize=12)
    doc = fitz.open()
    page = doc.new_page()
    page_width = page.rect.width
    box = page.add_freetext_annot(fitz.Rect(100, 82, 400, 102), text, fontsize=12, fontname="helv")
    box.set_info(title="klarpdf")
    box.update()
    x0 = page_width - width + 0.5
    doc.xref_set_key(box.xref, "Rect", f"[{x0:.3f} 740 {page_width:.3f} 760]")
    path = str(tmp_path / "edge.pdf")
    doc.save(path)
    doc.close()
    out = str(tmp_path / "nudged.pdf")
    _nudge(path, out)
    assert _drawing(out).count(b"Tj") == 1

