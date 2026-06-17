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

from model.virtual_document import VirtualDocument
from viewer.printing import _page_image, render_to_printer, selected_pages


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
    upright = _page_image(vdoc, 0, zoom=1.0)
    vdoc.set_rotation(0, 90)  # rotate page 0 a quarter turn
    rotated = _page_image(vdoc, 0, zoom=1.0)
    assert (rotated.width(), rotated.height()) == (upright.height(), upright.width())
