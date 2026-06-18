"""Per-page annotation model + materialise (PLAN.md, M20 — PR-A, the keystone foundation).

Headless. Annotations (highlight / text-box) ride the PageRef, so they follow the page through
reorder and are snapshotted for undo/redo; they bake into the output at materialise, on the copy —
the shared read-only sources are never touched.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtGui import QUndoStack

from model.edit_commands import AddAnnotationCommand
from model.edit_engine import PyMuPDFEngine
from model.page_edits import Highlight, TextBox
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


def test_snapshot_roundtrips_annotations(vdoc):
    h = Highlight(_word_rects(vdoc, 0))
    vdoc.add_annotation(0, h)
    snap = vdoc.snapshot()
    vdoc.clear_annotations(0)
    vdoc.restore(snap)
    assert vdoc.page_annotations(0) == (h,)
