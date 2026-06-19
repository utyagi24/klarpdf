"""Per-page annotation model + materialise (PLAN.md, M20 — PR-A, the keystone foundation).

Headless. Annotations (highlight / text-box) ride the PageRef, so they follow the page through
reorder and are snapshotted for undo/redo; they bake into the output at materialise, on the copy —
the shared read-only sources are never touched.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtGui import QUndoStack

from model.edit_commands import (
    AddAnnotationCommand,
    RemoveAnnotationCommand,
    ReplaceAnnotationCommand,
)
from model.edit_engine import PyMuPDFEngine
from model.page_edits import PDFPROJ_AUTHOR, Highlight, TextBox
from model.virtual_document import VirtualDocument


@pytest.fixture
def text_pdf(tmp_path) -> str:
    path = str(tmp_path / "t.pdf")
    doc = fitz.open()
    for i in range(2):
        page = doc.new_page()
        page.insert_text((72, 100), f"Page {i} HELLO WORLD sample text", fontsize=14)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def vdoc(text_pdf) -> VirtualDocument:
    return VirtualDocument.from_path(text_pdf)


def _word_rects(vdoc, page_index, n=2):
    ref = vdoc.ordered[page_index]
    page = vdoc.sources[ref.source_id][ref.source_page_index]
    return tuple(tuple(w[:4]) for w in page.get_text("words")[:n])


def _materialize(vdoc, tmp_path) -> str:
    out = str(tmp_path / "out.pdf")
    PyMuPDFEngine().materialize(vdoc, out)
    return out


def _annots(out_path, page_index=0):
    with fitz.open(out_path) as doc:
        return [(a.type[1], a.info.get("content", "")) for a in doc[page_index].annots()]


def test_add_annotation_rides_the_pageref(vdoc):
    h = Highlight(_word_rects(vdoc, 0))
    vdoc.add_annotation(0, h)
    assert vdoc.page_annotations(0) == (h,)
    assert vdoc.dirty is True


def test_highlight_survives_materialize_and_keeps_text(vdoc, tmp_path):
    rects = _word_rects(vdoc, 0, 2)
    vdoc.add_annotation(0, Highlight(rects))
    out = _materialize(vdoc, tmp_path)
    assert "Highlight" in [t for t, _ in _annots(out, 0)]
    with fitz.open(out) as doc:
        assert doc[0].get_textbox(fitz.Rect(rects[0])).strip() != ""  # non-destructive


def test_textbox_survives_materialize(vdoc, tmp_path):
    vdoc.add_annotation(0, TextBox((72, 150, 300, 180), "My note"))
    assert ("FreeText", "My note") in _annots(_materialize(vdoc, tmp_path), 0)


def test_annotation_follows_page_reorder(vdoc, tmp_path):
    vdoc.add_annotation(0, TextBox((72, 150, 300, 180), "moved"))
    vdoc.move_pages([0], 2)  # page 0 → the end (now index 1)
    out = _materialize(vdoc, tmp_path)
    assert ("FreeText", "moved") not in _annots(out, 0)  # not on the old slot
    assert ("FreeText", "moved") in _annots(out, 1)      # follows the page


def test_unannotated_pages_have_none(vdoc, tmp_path):
    vdoc.add_annotation(0, TextBox((72, 150, 300, 180), "only here"))
    assert _annots(_materialize(vdoc, tmp_path), 1) == []


def test_undo_redo_restores_annotation(vdoc):
    stack = QUndoStack()
    h = Highlight(_word_rects(vdoc, 0))
    stack.push(AddAnnotationCommand(vdoc, 0, h))
    assert vdoc.page_annotations(0) == (h,)
    stack.undo()
    assert vdoc.page_annotations(0) == ()
    stack.redo()
    assert vdoc.page_annotations(0) == (h,)


def test_clear_annotations(vdoc):
    vdoc.add_annotation(0, TextBox((72, 150, 300, 180), "x"))
    vdoc.clear_annotations(0)
    assert vdoc.page_annotations(0) == ()


def test_remove_annotation_removes_only_that_one(vdoc):
    a = TextBox((72, 150, 300, 180), "a")
    b = TextBox((72, 200, 300, 230), "b")
    vdoc.add_annotation(0, a)
    vdoc.add_annotation(0, b)
    vdoc.remove_annotation(0, a)
    assert vdoc.page_annotations(0) == (b,)


def test_remove_annotation_undo_redo(vdoc):
    stack = QUndoStack()
    a = TextBox((72, 150, 300, 180), "x")
    vdoc.add_annotation(0, a)
    stack.push(RemoveAnnotationCommand(vdoc, 0, a))
    assert vdoc.page_annotations(0) == ()
    stack.undo()
    assert vdoc.page_annotations(0) == (a,)
    stack.redo()
    assert vdoc.page_annotations(0) == ()


def test_snapshot_roundtrips_annotations(vdoc):
    h = Highlight(_word_rects(vdoc, 0))
    vdoc.add_annotation(0, h)
    snap = vdoc.snapshot()
    vdoc.clear_annotations(0)
    vdoc.restore(snap)
    assert vdoc.page_annotations(0) == (h,)


def test_replace_annotation_in_place_preserves_order(vdoc):
    a = TextBox((10, 10, 100, 30), "a")
    b = TextBox((10, 40, 100, 60), "b")
    vdoc.add_annotation(0, a)
    vdoc.add_annotation(0, b)
    moved = TextBox((50, 10, 140, 30), "a")  # same logical box, new position
    vdoc.replace_annotation(0, a, moved)
    assert vdoc.page_annotations(0) == (moved, b)  # swapped in place, order kept


def test_replace_annotation_command_undo_redo(vdoc):
    stack = QUndoStack()
    a = TextBox((10, 10, 100, 30), "old")
    vdoc.add_annotation(0, a)
    b = TextBox((10, 10, 100, 30), "new")
    stack.push(ReplaceAnnotationCommand(vdoc, 0, a, b))
    assert vdoc.page_annotations(0) == (b,)
    stack.undo()
    assert vdoc.page_annotations(0) == (a,)
    stack.redo()
    assert vdoc.page_annotations(0) == (b,)


def test_textbox_fontname_is_carried_into_materialize(vdoc, tmp_path):
    vdoc.add_annotation(0, TextBox((72, 150, 320, 180), "courier", fontname="cour"))
    out = _materialize(vdoc, tmp_path)
    assert "FreeText" in [t for t, _ in _annots(out, 0)]  # bakes in with the chosen font


def test_textbox_on_rotated_page_materializes(vdoc, tmp_path):
    """A text box on a per-page-rotated page bakes in and the page keeps its rotation — so PDF
    /Rotate rotates the annotation with the page (the in-app preview rotation mirrors this)."""
    vdoc.add_annotation(0, TextBox((72, 150, 320, 180), "rot"))
    vdoc.set_rotation(0, 90)
    out = _materialize(vdoc, tmp_path)
    assert ("FreeText", "rot") in _annots(out, 0)
    with fitz.open(out) as doc:
        assert doc[0].rotation == 90


def test_baked_annotations_are_author_tagged(vdoc, tmp_path):
    """Highlights & text-boxes pdfproj writes carry the PDFPROJ_AUTHOR title — the hook a future
    round-trip milestone needs to tell our annotations from foreign ones."""
    vdoc.add_annotation(0, Highlight(_word_rects(vdoc, 0)))
    vdoc.add_annotation(0, TextBox((72, 150, 320, 180), "note"))
    out = _materialize(vdoc, tmp_path)
    with fitz.open(out) as doc:
        titles = {a.info.get("title", "") for a in doc[0].annots()}
    assert titles == {PDFPROJ_AUTHOR}


# ---- M27 styled text boxes: font family / size / colour + box fill + box outline ----
#
# All on the simple FreeText appearance path (no richtext), so the text stays in /Contents and the
# style bakes into the annot's DA (font/size/colour), /C (fill), and /BS /W (outline) — verified
# structurally (the keys) and visually (rendered ink), since a styled annot that doesn't render is
# no use to a print/thumbnail path that rasterises the output.


def _freetext_style(out_path, page_index=0):
    """``(DA, C, BS)`` xref-key strings for the first FreeText annot on the page."""
    with fitz.open(out_path) as doc:
        page = doc[page_index]
        xref = next(a.xref for a in page.annots() if a.type[1] == "FreeText")
        return (
            doc.xref_get_key(xref, "DA")[1],
            doc.xref_get_key(xref, "C")[0],   # "array" when a fill is set, else "null"
            doc.xref_get_key(xref, "BS")[1],  # e.g. "<</Type/Border/W 1.5>>"
        )


def _ink(out_path, rect, page_index=0):
    """Non-white pixel count inside ``rect`` of the rendered output page (a 'did it draw' probe)."""
    with fitz.open(out_path) as doc:
        pix = doc[page_index].get_pixmap(matrix=fitz.Matrix(2, 2), clip=fitz.Rect(rect))
    s = pix.samples
    return sum(
        1
        for i in range(0, len(s), pix.n)
        if s[i] < 250 or s[i + 1] < 250 or s[i + 2] < 250
    )


def test_textbox_font_family_size_colour_bake_into_da(vdoc, tmp_path):
    vdoc.add_annotation(
        0, TextBox((72, 150, 320, 180), "styled", fontsize=14, color=(1, 0, 0), fontname="tiro")
    )
    da, _, _ = _freetext_style(_materialize(vdoc, tmp_path))
    assert "1 0 0 rg" in da          # red text colour
    assert "/TiRo" in da             # Times family (helv→/Helv, cour→/Cour)
    assert "14" in da                # 14 pt


def test_textbox_defaults_bake_no_fill_no_outline(vdoc, tmp_path):
    """A plain TextBox(rect, text) — the existing constructor — still bakes with /Contents text, the
    author title, and neither a fill nor an outline."""
    vdoc.add_annotation(0, TextBox((72, 150, 320, 180), "plain"))
    out = _materialize(vdoc, tmp_path)
    assert ("FreeText", "plain") in _annots(out, 0)   # text still in /Contents (back-compat)
    da, fill, bs = _freetext_style(out)
    assert "/Helv" in da and "11" in da
    assert fill == "null"                              # no /C fill
    assert "/W 0" in bs.replace(" ", " ")              # zero-width border = no outline


def test_textbox_fill_colour_bakes_and_renders(vdoc, tmp_path):
    rect = (200, 300, 380, 360)  # a blank region of the page (text sits up at y≈100)
    vdoc.add_annotation(0, TextBox(rect, "f", fill_color=(0.2, 0.4, 0.9)))
    out = _materialize(vdoc, tmp_path)
    _, fill, _ = _freetext_style(out)
    assert fill == "array"                              # /C carries the fill colour
    assert _ink(out, rect) > 5000                       # the box region is filled, not just text


def test_textbox_fill_makes_the_box_far_more_inked_than_unfilled(vdoc, tmp_path):
    rect = (200, 300, 380, 360)
    vdoc.add_annotation(0, TextBox(rect, "x", fill_color=(0.2, 0.4, 0.9)))
    filled = _ink(_materialize(vdoc, tmp_path), rect)
    vdoc.clear_annotations(0)
    vdoc.add_annotation(0, TextBox(rect, "x"))          # same box, no fill
    nofill = str(tmp_path / "nofill.pdf")
    PyMuPDFEngine().materialize(vdoc, nofill)
    assert filled > _ink(nofill, rect) + 5000           # the fill dominates the box's ink


def test_textbox_outline_bakes_and_renders(vdoc, tmp_path):
    rect = (200, 300, 380, 360)
    vdoc.add_annotation(0, TextBox(rect, "", border_width=2.0))  # outline only, no text/fill
    out = _materialize(vdoc, tmp_path)
    _, fill, bs = _freetext_style(out)
    assert fill == "null"                               # outline is independent of fill
    assert "/W 2" in bs                                 # /BS /W carries the outline width
    assert _ink(out, rect) > 0                          # a black border drew


def test_styled_textbox_roundtrips_through_snapshot(vdoc):
    box = TextBox((10, 10, 100, 30), "s", fontsize=13, color=(0, 0, 1),
                  fontname="cour", fill_color=(1, 1, 0), border_width=1.5)
    vdoc.add_annotation(0, box)
    snap = vdoc.snapshot()
    vdoc.clear_annotations(0)
    vdoc.restore(snap)
    assert vdoc.page_annotations(0) == (box,)           # frozen descriptor restored intact


def test_styled_textbox_survives_reorder(vdoc, tmp_path):
    vdoc.add_annotation(0, TextBox((72, 150, 320, 180), "moved", fill_color=(1, 0.9, 0.6),
                                   border_width=1.0))
    vdoc.move_pages([0], 2)  # page 0 → the end (now index 1)
    out = _materialize(vdoc, tmp_path)
    _, fill, bs = _freetext_style(out, page_index=1)    # style follows the page
    assert fill == "array" and "/W 1" in bs
