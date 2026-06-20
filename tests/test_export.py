"""Export → Flattened PDF (PLAN.md, M31.5). Headless.

Export writes a *derived* copy via ``Document.bake()``: annotations and form widgets become
permanent page **content** — text-preserving (not rasterised) — so the marks can't be moved /
removed / re-edited in any tool, the opposite of Save's editable, round-trippable output (M31).
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

import main_window as mw
from app import PdfApp
from main_window import MainWindow
from model.edit_engine import PyMuPDFEngine
from model.export import export_flattened_pdf
from model.page_edits import Highlight, Redaction, TextBox
from model.virtual_document import VirtualDocument
from store.settings import Settings


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


def _word_rects(vdoc, page_index=0, n=2):
    ref = vdoc.ordered[page_index]
    page = vdoc.sources[ref.source_id][ref.source_page_index]
    return tuple(tuple(w[:4]) for w in page.get_text("words")[:n])


def _annot_count(path, page_index=0) -> int:
    doc = fitz.open(path)
    page = doc[page_index]
    n = len(list(page.annots()))
    doc.close()
    return n


def _text(path, page_index=0) -> str:
    doc = fitz.open(path)
    page = doc[page_index]
    t = page.get_text("text")
    doc.close()
    return t


# ---- flatten bakes annotations into content --------------------------------


def test_export_bakes_annotations_into_content(vdoc, tmp_path):
    vdoc.add_annotation(0, Highlight(_word_rects(vdoc)))
    vdoc.add_annotation(0, TextBox((72, 200, 320, 240), "BOXNOTE"))
    out = str(tmp_path / "flat.pdf")
    export_flattened_pdf(vdoc, out)
    # The marks are gone as annotations (baked into the page) ...
    assert _annot_count(out) == 0
    # ... but the box text survived as real, searchable text (not rasterised).
    assert "BOXNOTE" in _text(out)
    assert "HELLO" in _text(out)  # the body text layer is intact


def test_save_keeps_annotations_export_flattens_them(vdoc, tmp_path):
    """Contrast: Save (materialize) keeps annotations editable; Export flattens them away."""
    vdoc.add_annotation(0, TextBox((72, 200, 320, 240), "NOTE"))
    saved = str(tmp_path / "saved.pdf")
    PyMuPDFEngine().materialize(vdoc, saved)
    exported = str(tmp_path / "exported.pdf")
    export_flattened_pdf(vdoc, exported)
    assert _annot_count(saved) == 1   # Save As stays editable (annotation preserved)
    assert _annot_count(exported) == 0  # Export locks (baked into content)


def test_flattened_annotations_do_not_round_trip(vdoc, tmp_path):
    """Reopening a flattened export finds no editable marks — they are page content now, not our
    author-tagged annotations (the locked, opt-out counterpart to M31's round-trip)."""
    vdoc.add_annotation(0, Highlight(_word_rects(vdoc)))
    vdoc.add_annotation(0, TextBox((72, 200, 320, 240), "LOCKED"))
    out = str(tmp_path / "flat.pdf")
    export_flattened_pdf(vdoc, out)

    reopened = VirtualDocument.from_path(out)
    assert reopened.page_annotations(0) == ()        # nothing comes back as editable
    assert reopened.has_baked_pdfproj_annotations() is False
    assert "LOCKED" in _text(out)                    # the text is still there, just not editable


def test_export_reflects_page_order(vdoc, tmp_path):
    vdoc.add_annotation(0, TextBox((72, 200, 320, 240), "MOVED"))
    vdoc.move_pages([0], 2)  # page 0 (with the box) → the end
    out = str(tmp_path / "flat.pdf")
    export_flattened_pdf(vdoc, out)
    assert "MOVED" not in _text(out, 0)  # not on the old slot
    assert "MOVED" in _text(out, 1)      # baked onto the page that moved


# ---- redaction is applied destructively in the export ----------------------


def test_export_applies_pending_redaction_without_committing_it(vdoc, tmp_path):
    """A pending redaction is destructive in the *exported* copy, but the working document keeps
    it (still undoable) — Export is a side artifact like Print, not a redaction point-of-no-return."""
    # Redact the whole text band on page 0.
    vdoc.add_annotation(0, Redaction(((60, 88, 540, 112),)))
    out = str(tmp_path / "flat.pdf")
    export_flattened_pdf(vdoc, out)
    assert "HELLO" not in _text(out)                 # the redacted text is gone from the export
    assert _annot_count(out) == 0                    # and no redaction annotation lingers
    # The working document is untouched: the redaction is still pending (undoable), not committed.
    assert vdoc.has_redactions() is True


# ---- form fields are flattened too -----------------------------------------


def test_export_flattens_filled_form_fields(a_pdf, tmp_path):
    """bake() also flattens widgets: a filled field becomes static content (no interactive widget),
    with its entered value preserved as visible text."""
    v = VirtualDocument.from_path(a_pdf)
    v.set_field_value("name", "FLATTENED-VALUE")
    out = str(tmp_path / "flat.pdf")
    export_flattened_pdf(v, out)
    doc = fitz.open(out)
    try:
        widgets = [w for page in doc for w in (page.widgets() or [])]
        assert widgets == []                          # no interactive fields remain
    finally:
        doc.close()
    assert "FLATTENED-VALUE" in _text(out)            # the value is baked in as content


def test_export_preserves_outline(a_pdf, tmp_path):
    """Baking annotations/widgets does not disturb the document outline."""
    v = VirtualDocument.from_path(a_pdf)
    out = str(tmp_path / "flat.pdf")
    export_flattened_pdf(v, out)
    doc = fitz.open(out)
    try:
        titles = [entry[1] for entry in doc.get_toc()]
    finally:
        doc.close()
    assert "Chapter 1" in titles and "Chapter 2" in titles


# ---- File ▸ Export ▸ Flattened PDF… wiring ----------------------------------


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "view_state.json")
    qapp.page_clipboard = []
    return qapp


def test_export_menu_action_writes_a_flattened_file(app, a_pdf, tmp_path, monkeypatch):
    """The File ▸ Export ▸ Flattened PDF… handler writes the chosen file and leaves the working
    document untouched (no path change, stays clean — Export is a side artifact, like Print)."""
    win = MainWindow(app, a_pdf, app.settings)
    win.vdoc.add_annotation(0, TextBox((72, 200, 320, 240), "UI-EXPORT"))
    target = str(tmp_path / "ui_export.pdf")
    monkeypatch.setattr(mw.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (target, "")))

    win._export_flattened_pdf()

    assert _annot_count(target) == 0 and "UI-EXPORT" in _text(target)  # flattened, text preserved
    assert win.vdoc.path == a_pdf            # working document path unchanged
    assert win.vdoc.page_annotations(0)      # and its editable annotation is still there


def test_export_menu_action_cancelled_writes_nothing(app, a_pdf, tmp_path, monkeypatch):
    win = MainWindow(app, a_pdf, app.settings)
    monkeypatch.setattr(mw.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    win._export_flattened_pdf()  # cancelling the dialog returns cleanly and writes nothing
    assert {p.name for p in tmp_path.glob("*.pdf")} == {"A.pdf"}  # only the fixture file exists
