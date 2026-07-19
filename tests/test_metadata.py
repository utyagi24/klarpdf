"""Properties + metadata (PLAN.md §GUI feature roadmap, M53).

One dialog, three verbs — view · edit · remove all — over **both stores**: the Info dict *and*
the XMP packet (Acrobat-class viewers prefer XMP). Edit keeps them consistent; remove clears
both, or the strip is a false promise — verified cross-engine with pypdf, not just PyMuPDF.
Also the carry-through regression: ``insert_pdf`` copies neither store, so before M53 every
materialised save silently stripped the document's metadata.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtGui import QUndoStack

import main_window as mw
from app import PdfApp
from main_window import MainWindow
from model.edit_commands import SetMetadataCommand
from model.edit_engine import PyMuPDFEngine
from model.export import export_selected_pages
from model.virtual_document import VirtualDocument
from store.settings import Settings
from ui.properties_dialog import PropertiesDialog, pdf_date_display

_XMP_MARKER = "XMP-ORIGINAL-PACKET-MARKER"


@pytest.fixture
def meta_pdf(tmp_path) -> str:
    """Two text pages with both metadata stores populated: a full Info dict and an XMP packet
    carrying a recognisable marker."""
    path = str(tmp_path / "meta.pdf")
    doc = fitz.open()
    for i in range(2):
        doc.new_page().insert_text((72, 72), f"META page {i}", fontsize=12)
    doc.set_metadata(
        {
            "title": "Original Title",
            "author": "Original Author",
            "subject": "Original Subject",
            "keywords": "alpha, beta",
            "creator": "OriginApp",
            "producer": "OriginLib 1.0",
            "creationDate": "D:20260101120000Z",
        }
    )
    doc.set_xml_metadata(
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:title><rdf:Alt><rdf:li xml:lang="x-default">'
        f"{_XMP_MARKER}</rdf:li></rdf:Alt></dc:title>"
        "</rdf:Description></rdf:RDF></x:xmpmeta>"
        '<?xpacket end="w"?>'
    )
    doc.save(path)
    doc.close()
    return path


def _saved(vdoc, tmp_path, name="out.pdf") -> str:
    out = str(tmp_path / name)
    PyMuPDFEngine().materialize(vdoc, out)
    return out


# ---- carry-through: an untouched save keeps both stores ----------------------


def test_save_carries_both_stores_through(meta_pdf, tmp_path):
    """The regression M53 fixes: insert_pdf copies neither store, so a plain reorder-free save
    used to strip title/author/XMP. Now both carry through byte-for-byte."""
    v = VirtualDocument.from_path(meta_pdf)
    out = _saved(v, tmp_path)
    with fitz.open(out) as doc:
        assert doc.metadata["title"] == "Original Title"
        assert doc.metadata["author"] == "Original Author"
        assert doc.metadata["producer"] == "OriginLib 1.0"
        assert _XMP_MARKER in doc.get_xml_metadata()


def test_save_carries_metadata_through_page_edits(meta_pdf, tmp_path):
    v = VirtualDocument.from_path(meta_pdf)
    v.move_page(0, 1)
    out = _saved(v, tmp_path)
    with fitz.open(out) as doc:
        assert doc.metadata["title"] == "Original Title"
        assert _XMP_MARKER in doc.get_xml_metadata()


def test_extract_carries_metadata(meta_pdf, tmp_path):
    """The M51 extract is a Save-like artifact — the subset carries the stores too."""
    v = VirtualDocument.from_path(meta_pdf)
    out = str(tmp_path / "x.pdf")
    export_selected_pages(v, [0], out)
    with fitz.open(out) as doc:
        assert doc.metadata["title"] == "Original Title"
        assert _XMP_MARKER in doc.get_xml_metadata()


# ---- edit: both stores agree -------------------------------------------------


def test_edit_writes_both_stores_consistently(meta_pdf, tmp_path):
    v = VirtualDocument.from_path(meta_pdf)
    values = v.effective_metadata()
    values["title"] = "Edited Title"
    values["author"] = "Edited Author"
    v.set_metadata_override(values)
    out = _saved(v, tmp_path)
    with fitz.open(out) as doc:
        assert doc.metadata["title"] == "Edited Title"       # Info dict
        xmp = doc.get_xml_metadata()
        assert "Edited Title" in xmp and "Edited Author" in xmp  # XMP agrees
        assert _XMP_MARKER not in xmp                        # the stale packet is replaced
        assert doc.metadata["producer"] == "OriginLib 1.0"   # untouched provenance carries

    from pypdf import PdfReader                              # independent engine

    reader = PdfReader(out)
    assert reader.metadata.title == "Edited Title"


# ---- remove all: both stores cleared, cross-engine ---------------------------


def test_remove_clears_both_stores_cross_engine(meta_pdf, tmp_path):
    v = VirtualDocument.from_path(meta_pdf)
    v.set_metadata_override({})
    assert v.metadata_is_removed()
    out = _saved(v, tmp_path)
    with fitz.open(out) as doc:
        assert all(
            not doc.metadata[k] for k in ("title", "author", "subject", "keywords", "producer")
        )
        assert not doc.get_xml_metadata()                    # the XMP packet is gone too

    # "Shows clean in Acrobat-class viewers, not just KlarPDF": an independent engine agrees —
    # no surviving Info values and no /Metadata stream in the catalog.
    from pypdf import PdfReader

    reader = PdfReader(out)
    info = reader.metadata
    assert info is None or all(not v for v in info.values())
    assert "/Metadata" not in reader.trailer["/Root"]


# ---- model state: undo, effective view, reload -------------------------------


def test_metadata_edit_rides_the_undo_stack(meta_pdf):
    v = VirtualDocument.from_path(meta_pdf)
    stack = QUndoStack()
    values = v.effective_metadata()
    values["title"] = "Undoable"
    stack.push(SetMetadataCommand(v, values))
    assert v.effective_metadata()["title"] == "Undoable" and v.dirty
    stack.push(SetMetadataCommand(v, {}))
    assert stack.undoText() == "Remove document metadata"
    assert v.metadata_is_removed()
    stack.undo()
    assert v.effective_metadata()["title"] == "Undoable"     # back to the edit
    stack.undo()
    assert v.effective_metadata()["title"] == "Original Title"  # back to the origin's
    assert v.metadata_override is None


def test_reload_from_file_recaptures_the_saved_stores(meta_pdf, tmp_path):
    v = VirtualDocument.from_path(meta_pdf)
    values = v.effective_metadata()
    values["title"] = "Saved Title"
    v.set_metadata_override(values)
    out = _saved(v, tmp_path)
    v.reload_from_file(out)
    assert v.metadata_override is None                       # fresh baseline, no pending verb
    assert v.effective_metadata()["title"] == "Saved Title"  # read back from the saved file
    assert "Saved Title" in v.origin_xmp


# ---- the dialog --------------------------------------------------------------


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    return qapp


def test_dialog_views_current_values_and_stages_nothing_untouched(qapp, meta_pdf):
    v = VirtualDocument.from_path(meta_pdf)
    dialog = PropertiesDialog(v)
    assert dialog._edits["title"].text() == "Original Title"
    assert dialog._provenance["producer"].text() == "OriginLib 1.0"
    assert dialog._provenance["creationDate"].text() == "2026-01-01 12:00:00"
    assert dialog.staged_override() is None                  # view-only visit → no command


def test_dialog_stages_an_edit(qapp, meta_pdf):
    v = VirtualDocument.from_path(meta_pdf)
    dialog = PropertiesDialog(v)
    dialog._edits["title"].setText("Dialog Title")
    staged = dialog.staged_override()
    assert staged["title"] == "Dialog Title"
    assert staged["author"] == "Original Author"             # untouched fields carried


def test_dialog_stages_remove_all_and_blanks_the_fields(qapp, meta_pdf):
    v = VirtualDocument.from_path(meta_pdf)
    dialog = PropertiesDialog(v)
    dialog._remove_button.click()
    assert dialog.staged_override() == {}                    # the remove-all sentinel
    assert dialog._edits["title"].text() == ""
    assert dialog._provenance["producer"].text() == "—"      # shows exactly what OK applies


def test_pdf_date_display_parses_and_falls_back():
    assert pdf_date_display("D:20260101120000Z") == "2026-01-01 12:00:00"
    assert pdf_date_display("D:20260101") == "2026-01-01"
    assert pdf_date_display("garbage") == "garbage"


# ---- the menu wiring ---------------------------------------------------------


def test_properties_action_pushes_one_undoable_command(app, meta_pdf, monkeypatch):
    win = MainWindow(app, meta_pdf, app.settings)
    import ui.properties_dialog as pd

    monkeypatch.setattr(pd.PropertiesDialog, "exec", lambda self: 1)
    monkeypatch.setattr(pd.PropertiesDialog, "staged_override", lambda self: {})
    win._show_properties()
    assert win.vdoc.metadata_is_removed()
    assert not win.undo_stack.isClean()                      # a real document edit
    win.undo_stack.undo()
    assert win.vdoc.metadata_override is None


def test_properties_action_cancelled_changes_nothing(app, meta_pdf, monkeypatch):
    win = MainWindow(app, meta_pdf, app.settings)
    import ui.properties_dialog as pd

    monkeypatch.setattr(pd.PropertiesDialog, "exec", lambda self: 0)
    win._show_properties()
    assert win.vdoc.metadata_override is None and win.undo_stack.isClean()
