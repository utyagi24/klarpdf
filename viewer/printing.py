"""Printing render path (PLAN.md, M12 + M25).

Rendering is **edits-aware**: it rasterises the same in-memory output document a Save would write
(:meth:`model.edit_engine.PyMuPDFEngine.render_output`), so the printout shows the page order,
rotations, form values, highlights, text boxes, and (destructive) redactions exactly as they will
be saved. A *pending* redaction therefore prints as removed without becoming a point of no return —
the render copy is a throwaway; the shared sources and the undo stack are never touched.

The system print *dialog* can't run headless, but ``QPrinter`` can write to a PDF file, so the
render path (:func:`render_to_printer` + :func:`selected_pages`) is exercised headlessly by
pointing a ``QPrinter`` at a PDF output. The page->image step (:func:`_page_image`) is also the
engine the planned image-export feature reuses.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf as fitz
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtPrintSupport import QAbstractPrintDialog, QPrintDialog, QPrinter
from PySide6.QtWidgets import QDialog, QMessageBox

from model.edit_engine import PyMuPDFEngine

# Raster render cap: printers report 600–1200 dpi, but rasterising a full page at that is huge
# (an A4 at 1200 dpi is ~70 MB) for no visible gain on a basic print. Render at most this, then let
# the painter smooth-scale up to the page.
_MAX_RENDER_DPI = 300


def print_document(vdoc, current_page: int, parent=None) -> bool:
    """Show the system print dialog and print the chosen pages. Returns True if a job was sent,
    False if the user cancelled."""
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    if vdoc.path:
        printer.setDocName(Path(vdoc.path).stem)
    printer.setFromTo(1, vdoc.page_count)  # advertise the valid range to the dialog
    dialog = QPrintDialog(printer, parent)
    dialog.setOption(QAbstractPrintDialog.PrintDialogOption.PrintPageRange, True)
    dialog.setOption(QAbstractPrintDialog.PrintDialogOption.PrintCurrentPage, True)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    return render_to_printer(printer, vdoc, current_page, parent)


def selected_pages(printer: QPrinter, page_count: int, current_page: int) -> list[int]:
    """Resolve the dialog's range choice to a list of 0-based page indices."""
    rng = printer.printRange()
    if rng == QPrinter.PrintRange.PageRange:
        lo = max(1, printer.fromPage())
        hi = min(page_count, printer.toPage() or page_count)
        return list(range(lo - 1, hi))
    if rng == QPrinter.PrintRange.CurrentPage:
        return [current_page] if 0 <= current_page < page_count else []
    return list(range(page_count))  # AllPages (and Selection, which we treat as all)


def render_to_printer(printer: QPrinter, vdoc, current_page: int, parent=None) -> bool:
    """Paint the selected pages of ``vdoc`` (edits applied), fit to each printable page. Returns
    False if the document could not be rendered or the job could not start."""
    try:
        rendered = PyMuPDFEngine().render_output(vdoc)
    except Exception as exc:  # a malformed edit shouldn't crash the app mid-print
        if parent is not None:
            QMessageBox.critical(parent, "Print", f"Could not render the document:\n{exc}")
        return False
    try:
        pages = selected_pages(printer, rendered.page_count, current_page)
        if not pages:
            return False
        painter = QPainter()
        if not painter.begin(printer):
            if parent is not None:
                QMessageBox.warning(parent, "Print", "Could not start the print job.")
            return False
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            zoom = min(printer.resolution(), _MAX_RENDER_DPI) / 72.0
            area = QRectF(printer.pageLayout().paintRectPixels(printer.resolution()))
            for i, index in enumerate(pages):
                if i:
                    printer.newPage()
                _draw_centered(painter, area, _page_image(rendered, index, zoom))
        finally:
            painter.end()
        return True
    finally:
        rendered.close()


def _page_image(doc: fitz.Document, index: int, zoom: float) -> QImage:
    """Rasterise ``doc[index]`` at ``zoom`` (1.0 == 72 dpi). The doc is the edits-applied render
    output, so rotation and every page edit are already baked into the page."""
    page = doc[index]
    pm = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    # .copy() detaches from the PyMuPDF sample buffer, which is freed when pm goes out of scope.
    return QImage(pm.samples, pm.width, pm.height, pm.stride, QImage.Format.Format_RGB888).copy()


def _draw_centered(painter: QPainter, area: QRectF, img: QImage) -> None:
    iw, ih = img.width(), img.height()
    if iw == 0 or ih == 0:
        return
    scale = min(area.width() / iw, area.height() / ih)  # fit to the printable area, keep aspect
    w, h = iw * scale, ih * scale
    x = area.x() + (area.width() - w) / 2
    y = area.y() + (area.height() - h) / 2
    painter.drawImage(QRectF(x, y, w, h), img)
