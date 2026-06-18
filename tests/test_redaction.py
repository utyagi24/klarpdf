"""Redaction model + materialise (PLAN.md, M21 — the keystone, true destructive removal).

Headless. A redaction rides the PageRef like an annotation (so it follows the page, snapshots for
undo/redo, and bakes in at materialise), but at save it runs PyMuPDF's destructive
``apply_redactions`` on the output copy — the content under the box is *physically gone*, not just
covered. The shared read-only sources are never touched.

The security-critical assertions verify the saved output has **no recoverable text** under the box,
cross-checked with Poppler's ``pdftotext`` (a different engine than the PyMuPDF writer) when it is
available on the machine running the suite.
"""

from __future__ import annotations

import shutil
import subprocess

import pymupdf as fitz
import pytest
from PySide6.QtGui import QUndoStack

from model.edit_commands import AddAnnotationCommand, RemoveAnnotationCommand
from model.edit_engine import PyMuPDFEngine
from model.page_edits import Highlight, Redaction
from model.virtual_document import VirtualDocument

# Distinct, single-token strings so a word box is exact and a leak is unambiguous.
SECRET = "SECRETDATA"
PUBLIC = "PUBLICINFO"


@pytest.fixture
def secret_pdf(tmp_path) -> str:
    path = str(tmp_path / "secret.pdf")
    doc = fitz.open()
    for _ in range(2):
        page = doc.new_page()
        page.insert_text((72, 100), SECRET, fontsize=14)   # to be redacted
        page.insert_text((72, 200), PUBLIC, fontsize=14)   # must survive
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def vdoc(secret_pdf) -> VirtualDocument:
    return VirtualDocument.from_path(secret_pdf)


def _word_box(vdoc, page_index: int, token: str) -> tuple:
    ref = vdoc.ordered[page_index]
    page = vdoc.sources[ref.source_id][ref.source_page_index]
    return next(tuple(w[:4]) for w in page.get_text("words") if w[4] == token)


def _materialize(vdoc, tmp_path) -> str:
    out = str(tmp_path / "out.pdf")
    PyMuPDFEngine().materialize(vdoc, out)
    return out


# ---- model plumbing (rides the PageRef, undo/redo, snapshot) --------------------


def test_redaction_rides_the_pageref(vdoc):
    r = Redaction((_word_box(vdoc, 0, SECRET),))
    vdoc.add_annotation(0, r)
    assert vdoc.page_annotations(0) == (r,)
    assert vdoc.dirty is True


def test_redaction_follows_page_reorder(vdoc, tmp_path):
    vdoc.add_annotation(0, Redaction((_word_box(vdoc, 0, SECRET),)))
    vdoc.move_pages([0], 2)  # page 0 → the end (now index 1)
    out = _materialize(vdoc, tmp_path)
    with fitz.open(out) as doc:
        assert SECRET in doc[0].get_text()       # the unredacted page kept its text
        assert SECRET not in doc[1].get_text()   # redaction followed the moved page


def test_redaction_undo_redo(vdoc):
    stack = QUndoStack()
    r = Redaction((_word_box(vdoc, 0, SECRET),))
    stack.push(AddAnnotationCommand(vdoc, 0, r))
    assert vdoc.page_annotations(0) == (r,)
    stack.undo()
    assert vdoc.page_annotations(0) == ()
    stack.redo()
    assert vdoc.page_annotations(0) == (r,)


def test_remove_redaction_undo_redo(vdoc):
    stack = QUndoStack()
    r = Redaction((_word_box(vdoc, 0, SECRET),))
    vdoc.add_annotation(0, r)
    stack.push(RemoveAnnotationCommand(vdoc, 0, r))
    assert vdoc.page_annotations(0) == ()
    stack.undo()
    assert vdoc.page_annotations(0) == (r,)


def test_redaction_snapshot_roundtrips(vdoc):
    r = Redaction((_word_box(vdoc, 0, SECRET),))
    vdoc.add_annotation(0, r)
    snap = vdoc.snapshot()
    vdoc.clear_annotations(0)
    vdoc.restore(snap)
    assert vdoc.page_annotations(0) == (r,)


# ---- the keystone: true destructive removal (leak verification) -----------------


def test_redaction_removes_text_under_the_box(vdoc, tmp_path):
    vdoc.add_annotation(0, Redaction((_word_box(vdoc, 0, SECRET),)))
    out = _materialize(vdoc, tmp_path)
    with fitz.open(out) as doc:
        page0 = doc[0]
        assert SECRET not in page0.get_text()                  # gone from the page entirely
        assert page0.get_textbox(fitz.Rect(_word_box(vdoc, 0, SECRET))).strip() == ""  # nothing under it
        assert PUBLIC in page0.get_text()                      # neighbouring text survives


def test_redaction_annotation_is_consumed_not_left_behind(vdoc, tmp_path):
    """apply_redactions removes the redaction annotation itself — the output carries no annot that
    could be deleted to reveal the content (the cover-only data-leak trap)."""
    vdoc.add_annotation(0, Redaction((_word_box(vdoc, 0, SECRET),)))
    out = _materialize(vdoc, tmp_path)
    with fitz.open(out) as doc:
        assert list(doc[0].annots()) == []


def test_unredacted_page_keeps_its_text(vdoc, tmp_path):
    vdoc.add_annotation(0, Redaction((_word_box(vdoc, 0, SECRET),)))
    out = _materialize(vdoc, tmp_path)
    with fitz.open(out) as doc:
        assert SECRET in doc[1].get_text()  # page 1 was never marked


def test_highlight_is_not_destructive_contrast(vdoc, tmp_path):
    """Sanity contrast: a highlight over the same word leaves the text intact (only redaction is
    destructive), so the leak check above is exercising real removal, not a fixture quirk."""
    vdoc.add_annotation(0, Highlight((_word_box(vdoc, 0, SECRET),)))
    out = _materialize(vdoc, tmp_path)
    with fitz.open(out) as doc:
        assert SECRET in doc[0].get_text()


def test_multiple_rects_in_one_redaction_all_removed(vdoc, tmp_path):
    """A multi-rect redaction (e.g. a per-line 'Redact Selection') removes every rect's content."""
    rects = (_word_box(vdoc, 0, SECRET), _word_box(vdoc, 0, PUBLIC))
    vdoc.add_annotation(0, Redaction(rects))
    out = _materialize(vdoc, tmp_path)
    with fitz.open(out) as doc:
        text = doc[0].get_text()
        assert SECRET not in text and PUBLIC not in text  # both bars removed


def test_has_redactions(vdoc):
    assert vdoc.has_redactions() is False
    vdoc.add_annotation(0, Redaction((_word_box(vdoc, 0, SECRET),)))
    assert vdoc.has_redactions() is True


def test_reload_from_file_is_point_of_no_return(vdoc, tmp_path):
    """After a redacted save, reloading from the clean output drops the original (un-redacted)
    bytes from memory — so the secret cannot be resurrected by an undo + re-save."""
    vdoc.add_annotation(0, Redaction((_word_box(vdoc, 0, SECRET),)))
    out = _materialize(vdoc, tmp_path)
    vdoc.reload_from_file(out)
    assert vdoc.has_redactions() is False                 # the redaction descriptor is gone
    src = vdoc.sources[vdoc.origin_source_id]
    assert SECRET not in src[0].get_text()                # gone from the in-memory source too
    assert PUBLIC in src[0].get_text()                    # untouched content remains


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="Poppler pdftotext not installed")
def test_redaction_leak_check_poppler_cross_engine(vdoc, tmp_path):
    """Cross-check the removal with a *different* engine (Poppler) than the PyMuPDF writer, so the
    'text is gone' claim doesn't rest on the same library that performed the redaction."""
    vdoc.add_annotation(0, Redaction((_word_box(vdoc, 0, SECRET),)))
    out = _materialize(vdoc, tmp_path)
    # Scope to the redacted page only (-f/-l), so SECRET on the *other* (unredacted) page — which
    # mirrors the page-scoped get_text() assertion above — doesn't mask a real leak.
    result = subprocess.run(
        ["pdftotext", "-f", "1", "-l", "1", out, "-"], capture_output=True, text=True, check=True
    )
    assert SECRET not in result.stdout  # provably gone, per Poppler
    assert PUBLIC in result.stdout      # untouched text still extractable
