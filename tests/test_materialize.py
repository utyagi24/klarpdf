"""Materialize-on-save preserves OCR text, remaps the outline, and keeps form fields.

These are the M1 keystone correctness checks (PLAN.md, Verification). The default
PyMuPDFEngine is authoritative; a lighter check covers the PyPdfEngine fallback. Where useful
we cross-check the written file with a *different* engine (pypdf reader) than the writer.
"""

from __future__ import annotations

import pymupdf as fitz

from model.edit_engine import PyMuPDFEngine, PyPdfEngine
from model.virtual_document import PageRef, VirtualDocument
from tests.conftest import A_TEXT, B_TEXT


def _field_names(path: str) -> list[str]:
    doc = fitz.open(path)
    names = [w.field_name for page in doc for w in page.widgets()]
    doc.close()
    return names


def test_text_survives_reorder(a_pdf, tmp_path):
    vd = VirtualDocument.from_path(a_pdf)
    vd.move_page(2, 0)  # A2 to the front
    out = str(tmp_path / "out.pdf")
    PyMuPDFEngine().materialize(vd, out)

    doc = fitz.open(out)
    try:
        assert doc.page_count == 3
        assert A_TEXT[2] in doc[0].get_text("text")  # moved page's OCR text intact
        assert A_TEXT[0] in doc[1].get_text("text")
    finally:
        doc.close()


def test_outline_remaps_and_drops_dangling(a_pdf, tmp_path):
    vd = VirtualDocument.from_path(a_pdf)
    vd.delete_page(1)  # deletes A1 — the "Section 1.1" bookmark target
    out = str(tmp_path / "out.pdf")
    PyMuPDFEngine().materialize(vd, out)

    doc = fitz.open(out)
    try:
        toc = doc.get_toc(simple=True)  # [level, title, page]
    finally:
        doc.close()
    # Section 1.1 dropped (no dangling); survivors point at NEW 1-based indices; levels repaired.
    assert toc == [[1, "Chapter 1", 1], [1, "Chapter 2", 2]]


def test_merge_preserves_both_form_fields_dedup(a_pdf, b_pdf, tmp_path):
    vd = VirtualDocument.from_path(a_pdf)
    b_id = vd.open_source(b_pdf)
    vd.append_pages([PageRef(b_id, 0)])  # merge B's page 0 (also a "name" field)
    out = str(tmp_path / "merged.pdf")
    PyMuPDFEngine().materialize(vd, out)

    names = _field_names(out)
    # Both fields survive; the colliding B field is auto-renamed, not dropped/overwritten.
    assert len(names) == 2
    assert len(set(names)) == 2
    assert any(n == "name" for n in names)
    assert all(n.startswith("name") for n in names)

    # Cross-check with a different engine (pypdf reader).
    from pypdf import PdfReader

    fields = PdfReader(out).get_fields()
    assert fields is not None and len(fields) == 2


def test_rotation_override_is_absolute(a_pdf, tmp_path):
    vd = VirtualDocument.from_path(a_pdf)
    vd.set_rotation(0, 90)
    vd.set_rotation(0, 270)  # absolute: final angle wins, not 90+270
    out = str(tmp_path / "rot.pdf")
    PyMuPDFEngine().materialize(vd, out)

    doc = fitz.open(out)
    try:
        assert doc[0].rotation == 270
    finally:
        doc.close()


def test_pypdf_fallback_page_order_and_outline(a_pdf, tmp_path):
    vd = VirtualDocument.from_path(a_pdf)
    vd.move_page(2, 0)
    vd.delete_page(2)  # drop what is now A1
    out = str(tmp_path / "fallback.pdf")
    PyPdfEngine().materialize(vd, out)

    doc = fitz.open(out)
    try:
        assert doc.page_count == 2
        assert A_TEXT[2] in doc[0].get_text("text")
        assert A_TEXT[0] in doc[1].get_text("text")
        toc = doc.get_toc(simple=True)
    finally:
        doc.close()
    # Outline rebuilt; Section 1.1 (A1) dropped, survivors remapped.
    assert [t[1] for t in toc] == ["Chapter 1", "Chapter 2"]
    assert [t[2] for t in toc] == [2, 1]
