"""Pen & shapes model (PLAN.md §GUI feature roadmap, M57 ⭐ — R3 "Markup Tools"). Headless.

``InkStroke`` / ``Line`` / ``Shape`` descriptors beside ``Highlight``: baked via
``add_ink_annot`` / ``add_line_annot`` + ``set_line_ends`` / ``add_rect_annot`` /
``add_circle_annot``, author-tagged, style read back via ``annot.colors`` / ``annot.border``
(no DA parsing). Printing, flatten, and thumbnails inherit automatically via
``apply_annotations``. The Done-when: all four types bake, read back symmetric, and survive
save→reopen→save without drift.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from model.edit_engine import PyMuPDFEngine
from model.export import export_flattened_pdf
from model.page_edits import (
    KLARPDF_AUTHOR,
    InkStroke,
    Line,
    Shape,
    apply_annotations,
    read_klarpdf_annotations,
    strip_klarpdf_annotations,
)
from model.virtual_document import VirtualDocument

_INK = InkStroke(
    (((72.0, 100.0), (90.0, 112.0), (120.0, 104.0), (150.0, 130.0)),),
    color=(0.1, 0.2, 0.9),
    width=3.0,
)
_LINE = Line((80.0, 200.0), (240.0, 260.0), color=(0.0, 0.6, 0.2), width=1.5, arrow_end=True)
_RECT = Shape("rect", (100.0, 300.0, 220.0, 360.0), width=2.5)
_ELLIPSE = Shape("ellipse", (260.0, 300.0, 380.0, 380.0), fill_color=(1.0, 0.9, 0.4))

_ALL = (_INK, _LINE, _RECT, _ELLIPSE)
_KINDS = {
    fitz.PDF_ANNOT_INK,
    fitz.PDF_ANNOT_LINE,
    fitz.PDF_ANNOT_SQUARE,
    fitz.PDF_ANNOT_CIRCLE,
}


def _approx_pts(a, b, abs_tol=0.6):
    for (ax, ay), (bx, by) in zip(a, b):
        assert ax == pytest.approx(bx, abs=abs_tol) and ay == pytest.approx(by, abs=abs_tol)


# ---- bake --------------------------------------------------------------------


def test_all_four_types_bake_author_tagged(a_pdf, tmp_path):
    v = VirtualDocument.from_path(a_pdf)
    for mark in _ALL:
        v.add_annotation(0, mark)
    out = str(tmp_path / "d.pdf")
    PyMuPDFEngine().materialize(v, out)
    doc = fitz.open(out)
    try:
        page = doc[0]  # hold the page: annots orphan if their page proxy is collected
        annots = list(page.annots())
        assert {a.type[0] for a in annots} == _KINDS
        assert all(a.info.get("title") == KLARPDF_AUTHOR for a in annots)
    finally:
        doc.close()


# ---- read back symmetric -----------------------------------------------------


def test_read_back_is_symmetric(a_pdf):
    doc = fitz.open(a_pdf)
    try:
        apply_annotations(doc[0], _ALL)
        back = read_klarpdf_annotations(doc[0])
    finally:
        doc.close()
    by_type = {type(a).__name__: a for a in back}
    assert set(by_type) == {"InkStroke", "Line", "Shape"} and len(back) == 4

    ink = next(a for a in back if isinstance(a, InkStroke))
    _approx_pts(ink.paths[0], _INK.paths[0])
    assert ink.color == pytest.approx(_INK.color) and ink.width == pytest.approx(_INK.width)

    line = next(a for a in back if isinstance(a, Line))
    _approx_pts((line.start, line.end), (_LINE.start, _LINE.end))
    assert (line.arrow_start, line.arrow_end) == (False, True)
    assert line.color == pytest.approx(_LINE.color) and line.width == pytest.approx(_LINE.width)

    shapes = {a.kind: a for a in back if isinstance(a, Shape)}
    assert set(shapes) == {"rect", "ellipse"}
    _approx_pts(
        ((shapes["rect"].rect[0], shapes["rect"].rect[1]),
         (shapes["rect"].rect[2], shapes["rect"].rect[3])),
        ((_RECT.rect[0], _RECT.rect[1]), (_RECT.rect[2], _RECT.rect[3])),
    )
    assert shapes["rect"].fill_color is None              # outline-only stays unfilled
    assert shapes["ellipse"].fill_color == pytest.approx(_ELLIPSE.fill_color)


# ---- save → reopen → save without drift --------------------------------------


def test_two_bake_cycles_do_not_drift(a_pdf, tmp_path):
    v = VirtualDocument.from_path(a_pdf)
    for mark in _ALL:
        v.add_annotation(0, mark)
    first = str(tmp_path / "one.pdf")
    PyMuPDFEngine().materialize(v, first)

    second_doc = VirtualDocument.from_path(first)         # read back (cycle 1)
    second = str(tmp_path / "two.pdf")
    PyMuPDFEngine().materialize(second_doc, second)       # re-bake
    reread = VirtualDocument.from_path(second)            # read back (cycle 2)

    marks = reread.page_annotations(0)
    assert len(marks) == 4
    ink = next(a for a in marks if isinstance(a, InkStroke))
    _approx_pts(ink.paths[0], _INK.paths[0], abs_tol=1.0)
    line = next(a for a in marks if isinstance(a, Line))
    _approx_pts((line.start, line.end), (_LINE.start, _LINE.end), abs_tol=1.0)
    assert line.arrow_end is True and line.arrow_start is False
    rect = next(a for a in marks if isinstance(a, Shape) and a.kind == "rect")
    _approx_pts(
        ((rect.rect[0], rect.rect[1]), (rect.rect[2], rect.rect[3])),
        ((_RECT.rect[0], _RECT.rect[1]), (_RECT.rect[2], _RECT.rect[3])),
        abs_tol=1.0,
    )
    ellipse = next(a for a in marks if isinstance(a, Shape) and a.kind == "ellipse")
    assert ellipse.fill_color == pytest.approx(_ELLIPSE.fill_color, abs=0.02)


# ---- strip + flatten inherit -------------------------------------------------


def test_strip_removes_drawn_marks(a_pdf):
    doc = fitz.open(a_pdf)
    try:
        apply_annotations(doc[0], _ALL)
        strip_klarpdf_annotations(doc[0])
        assert list(doc[0].annots()) == []
    finally:
        doc.close()


def test_flatten_inherits_drawn_marks(a_pdf, tmp_path):
    v = VirtualDocument.from_path(a_pdf)
    v.add_annotation(0, _RECT)
    v.add_annotation(0, _INK)
    out = str(tmp_path / "flat.pdf")
    export_flattened_pdf(v, out)
    with fitz.open(out) as doc:
        assert list(doc[0].annots()) == []                # baked into content
        marked = doc[0].get_pixmap()
    clean = VirtualDocument.from_path(a_pdf)
    clean_out = str(tmp_path / "clean.pdf")
    export_flattened_pdf(clean, clean_out)
    with fitz.open(clean_out) as doc:
        assert marked.samples != doc[0].get_pixmap().samples  # the marks drew real ink


# ---- model geometry helpers --------------------------------------------------


def test_bounding_rects():
    assert _INK.bounding_rect() == (72.0, 100.0, 150.0, 130.0)
    assert _LINE.bounding_rect() == (80.0, 200.0, 240.0, 260.0)
    assert _RECT.bounding_rect() == _RECT.rect


def test_descriptors_are_hashable_and_frozen():
    """They ride frozen PageRefs and undo snapshots — same contract as the older descriptors."""
    assert {_INK, _LINE, _RECT, _ELLIPSE}                 # hashable set members
    with pytest.raises(Exception):
        _LINE.width = 9  # type: ignore[misc]
