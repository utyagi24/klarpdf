"""Printing render path (PLAN.md, M12).

The system print *dialog* can't run headless, but ``QPrinter`` can write to a PDF file — so we
point a printer at a temp PDF and assert the render path produces the right pages. This exercises
``render_to_printer`` + ``selected_pages`` end to end (page count, range, current page, rotation)
without a real printer or dialog.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QApplication

from model.edit_engine import PyMuPDFEngine
from model.virtual_document import VirtualDocument
from viewer.printing import ScaleMode, _page_image, _scale_factor, render_to_printer, selected_pages


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def vdoc(a_pdf):
    return VirtualDocument.from_path(a_pdf)


def _pdf_printer(out_path: str) -> QPrinter:
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(out_path)
    return printer


def test_print_all_pages_to_pdf(qapp, vdoc, tmp_path):
    out = str(tmp_path / "out.pdf")
    printer = _pdf_printer(out)
    assert render_to_printer(printer, vdoc, current_page=0) is True
    with fitz.open(out) as doc:
        assert doc.page_count == vdoc.page_count == 3  # every page printed


def test_print_page_range_to_pdf(qapp, vdoc, tmp_path):
    out = str(tmp_path / "range.pdf")
    printer = _pdf_printer(out)
    printer.setPrintRange(QPrinter.PrintRange.PageRange)
    printer.setFromTo(2, 3)  # pages 2..3 (1-based)
    assert render_to_printer(printer, vdoc, current_page=0) is True
    with fitz.open(out) as doc:
        assert doc.page_count == 2


def test_selected_pages_resolves_each_range_mode(qapp):
    printer = QPrinter()
    printer.setPrintRange(QPrinter.PrintRange.AllPages)
    assert selected_pages(printer, 3, current_page=1) == [0, 1, 2]

    printer.setPrintRange(QPrinter.PrintRange.CurrentPage)
    assert selected_pages(printer, 3, current_page=1) == [1]

    printer.setPrintRange(QPrinter.PrintRange.PageRange)
    printer.setFromTo(2, 2)
    assert selected_pages(printer, 3, current_page=0) == [1]


def test_print_current_page_only(qapp, vdoc, tmp_path):
    out = str(tmp_path / "current.pdf")
    printer = _pdf_printer(out)
    printer.setPrintRange(QPrinter.PrintRange.CurrentPage)
    assert render_to_printer(printer, vdoc, current_page=2) is True
    with fitz.open(out) as doc:
        assert doc.page_count == 1


def test_print_honours_rotation_override(qapp, vdoc):
    """The rendered page image is rotated to match the page's override (swapped dimensions),
    so the print reflects what the viewer shows."""
    with PyMuPDFEngine().render_output(vdoc) as rendered:
        upright = _page_image(rendered, 0, zoom=1.0)
    vdoc.set_rotation(0, 90)  # rotate page 0 a quarter turn
    with PyMuPDFEngine().render_output(vdoc) as rendered:
        rotated = _page_image(rendered, 0, zoom=1.0)
    assert (rotated.width(), rotated.height()) == (upright.height(), upright.width())


# ---- edits-aware render (M25): print/preview/export show the page-edit layer ----


def test_render_output_applies_edits(qapp, vdoc):
    """render_output bakes the page-edit layer in (delete + redaction + highlight), so print /
    preview / Save-as-PDF are WYSIWYG and a pending redaction prints as removed."""
    from model.page_edits import Highlight, Redaction

    word = vdoc.sources[vdoc.ordered[0].source_id][0].get_text("words")[0]  # (x0,y0,x1,y1,text,..)
    vdoc.add_annotation(0, Redaction((tuple(word[:4]),)))  # destroy page 0's first word
    vdoc.add_annotation(1, Highlight((tuple(word[:4]),)))  # highlight (non-destructive) on page 1
    vdoc.delete_page(2)

    with PyMuPDFEngine().render_output(vdoc) as rendered:
        assert rendered.page_count == 2  # the delete is reflected
        assert word[4] not in rendered[0].get_text("text")  # redaction destroyed the word
        assert any(a.type[0] == fitz.PDF_ANNOT_HIGHLIGHT for a in rendered[1].annots())


def test_print_reflects_deleted_page(qapp, vdoc, tmp_path):
    """render_to_printer prints the edits-applied render, not the raw source pages."""
    vdoc.delete_page(1)  # 3 -> 2 pages
    out = str(tmp_path / "edited.pdf")
    printer = _pdf_printer(out)
    assert render_to_printer(printer, vdoc, current_page=0) is True
    with fitz.open(out) as doc:
        assert doc.page_count == 2


def test_scale_factor_modes():
    """FIT fills the area (aspect kept); ACTUAL is 1:1 render-px->device-px (here dpi match);
    SHRINK is ACTUAL but never enlarges past FIT."""
    # Oversized image (100 px) in a smaller area (50 px) at matching 72 dpi.
    assert _scale_factor(ScaleMode.FIT, 50, 50, 100, 100, 72, 72) == 0.5
    assert _scale_factor(ScaleMode.ACTUAL, 50, 50, 100, 100, 72, 72) == 1.0
    assert _scale_factor(ScaleMode.SHRINK, 50, 50, 100, 100, 72, 72) == 0.5  # oversized -> fit

    # Undersized image (40 px) in a bigger area (50 px): SHRINK keeps actual size, FIT enlarges.
    assert _scale_factor(ScaleMode.FIT, 50, 50, 40, 40, 72, 72) == 1.25
    assert _scale_factor(ScaleMode.SHRINK, 50, 50, 40, 40, 72, 72) == 1.0  # not enlarged
