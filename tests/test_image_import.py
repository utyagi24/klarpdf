"""Image import (PLAN.md, M35). Headless + offscreen GUI.

Drag a local raster image (PNG/JPEG/…) from Explorer onto the Pages sidebar → it inserts as a new
page, exactly like a dropped PDF. The only new piece is the model converting each image to a
one-page PDF source (PyMuPDF ``convert_to_pdf``); after that it is just another registered source,
flowing through reorder / materialize / export unchanged. Reuses M17's drop + insert plumbing.
"""

from __future__ import annotations

import os

import pymupdf as fitz
import pytest
from PySide6.QtCore import QMimeData, QUrl

from app import PdfApp
from model.edit_engine import PyMuPDFEngine
from model.virtual_document import IMAGE_EXTENSIONS, VirtualDocument
from organize.thumbnail_panel import ThumbnailPanel
from store.settings import Settings


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


def _make_image(path: str) -> str:
    """Render a small image file (via a pixmap) so it is a real, decodable raster. The file
    extension drives the saved format (``.png`` / ``.jpg`` / …)."""
    doc = fitz.open()
    page = doc.new_page(width=240, height=160)
    page.insert_text((20, 90), "IMG", fontsize=40)
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
    pix.save(path)  # extension drives the format
    doc.close()
    return path


# ---- model: open_image_source ----------------------------------------------


def test_image_extensions_cover_common_formats():
    assert {".png", ".jpg", ".jpeg"} <= IMAGE_EXTENSIONS


def test_open_image_source_registers_a_one_page_pdf(a_pdf, tmp_path):
    img = _make_image(str(tmp_path / "pic.png"))
    v = VirtualDocument.from_path(a_pdf)
    source_id = v.open_image_source(img)
    src = v.sources[source_id]
    assert src.is_pdf and src.page_count == 1   # the image became a 1-page PDF source
    assert source_id in v.sources


def test_open_image_source_is_idempotent(a_pdf, tmp_path):
    img = _make_image(str(tmp_path / "pic.png"))
    v = VirtualDocument.from_path(a_pdf)
    first = v.open_image_source(img)
    same = v.sources[first]
    assert v.open_image_source(img) == first and v.sources[first] is same


def test_imported_image_page_survives_materialize(a_pdf, tmp_path):
    from model.edit_commands import InsertCommand
    from PySide6.QtGui import QUndoStack

    img = _make_image(str(tmp_path / "pic.png"))
    v = VirtualDocument.from_path(a_pdf)
    base = v.page_count
    source_id = v.open_image_source(img)
    from model.virtual_document import PageRef

    QUndoStack().push(InsertCommand(v, base, [PageRef(source_id, 0)], text="img"))
    assert v.page_count == base + 1

    out = str(tmp_path / "out.pdf")
    PyMuPDFEngine().materialize(v, out)
    with fitz.open(out) as doc:
        assert doc.page_count == base + 1          # the image page is in the output
        assert doc[base].get_pixmap().width > 0    # and it renders


def test_open_image_source_does_not_hold_the_file_open(a_pdf, tmp_path):
    """The registered source is in-memory bytes (converted PDF), so the image file isn't locked —
    it can be deleted right after import (the imported page still materializes)."""
    img = _make_image(str(tmp_path / "pic.png"))
    v = VirtualDocument.from_path(a_pdf)
    source_id = v.open_image_source(img)
    os.remove(img)  # would fail/raise on a held handle on Windows
    assert v.sources[source_id].page_count == 1


# ---- UI: the drop path accepts images ---------------------------------------


@pytest.fixture
def panel(qapp, a_pdf):
    p = ThumbnailPanel(VirtualDocument.from_path(a_pdf))
    p.source_key = "doc"
    p.resize(180, 800)
    p.show()
    qapp.processEvents()
    return p


def _file_mime(*paths: str) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    return mime


def test_drop_filter_keeps_images_and_pdfs(panel, b_pdf, tmp_path):
    png = _make_image(str(tmp_path / "pic.png"))
    jpg = _make_image(str(tmp_path / "pic.jpg"))
    txt = tmp_path / "note.txt"
    txt.write_text("nope")
    result = panel._dropped_file_paths(_file_mime(b_pdf, png, jpg, str(txt)))
    got = {os.path.normpath(p) for p in result}
    assert got == {os.path.normpath(b_pdf), os.path.normpath(png), os.path.normpath(jpg)}


def test_image_drop_inserts_a_page_and_undoes(qapp, a_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    win = qapp.open_document(a_pdf)
    try:
        img = _make_image(str(tmp_path / "pic.png"))
        base = win.vdoc.page_count
        win._on_files_dropped([img], 0)  # drop at the top → insert before page 0
        assert win.vdoc.page_count == base + 1
        assert win.vdoc.ordered[0].source_id == os.path.normpath(img) or \
            win.vdoc.ordered[0].source_id.endswith("pic.png")  # the image page landed first
        win.undo_stack.undo()
        assert win.vdoc.page_count == base  # undoable, like any insert
    finally:
        win.undo_stack.setClean()
        win.close()


def test_dropping_a_mix_of_image_and_pdf(qapp, a_pdf, b_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    win = qapp.open_document(a_pdf)
    try:
        img = _make_image(str(tmp_path / "pic.png"))
        base = win.vdoc.page_count
        with fitz.open(b_pdf) as d:
            b_pages = d.page_count
        win._on_files_dropped([img, b_pdf], base)  # append image page + B's pages
        assert win.vdoc.page_count == base + 1 + b_pages
    finally:
        win.undo_stack.setClean()
        win.close()
