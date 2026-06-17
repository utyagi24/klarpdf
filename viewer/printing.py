"""Basic printing via the system dialog (PLAN.md, M12).

Renders each selected page to a raster image with PyMuPDF (at the printer resolution, capped to keep
memory sane) and paints it, scaled to fit the printable area, onto a ``QPrinter`` through a
``QPainter``. Honours the page's rotation override so the print matches what the viewer shows.
Read-only: printing never mutates the document.

The flow splits into a thin dialog entry point (:func:`print_document`) and pure-ish helpers
(:func:`selected_pages`, :func:`render_to_printer`) so the render path is testable headlessly by
pointing a ``QPrinter`` at a PDF output file — no real printer or dialog required.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf as fitz
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter, QTransform
from PySide6.QtPrintSupport import QAbstractPrintDialog, QPrintDialog, QPrinter
from PySide6.QtWidgets import QDialog, QMessageBox

# Raster render cap: printers report 600–1200 dpi, but rasterising a full page at that is huge
# (an A4 at 1200 dpi is ~70 MB) for no visible gain on a basic print. Render at most this, then let
# the painter smooth-scale up to the page.
_MAX_RENDER_DPI = 300


def print_document(vdoc, current_page: int, parent=None) -> bool:
    """Show the system print dialog for ``vdoc`` and print the chosen pages. Returns True if a job
    was sent, False if the user cancelled."""
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


def _page_image(vdoc, index: int, zoom: float) -> QImage:
    """Rasterise page ``index`` at ``zoom`` (1.0 == 72 dpi), applying any rotation override."""
    ref = vdoc.ordered[index]
    page = vdoc.sources[ref.source_id][ref.source_page_index]
    pm = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    # .copy() detaches from the PyMuPDF sample buffer, which is freed when pm goes out of scope.
    img = QImage(pm.samples, pm.width, pm.height, pm.stride, QImage.Format.Format_RGB888).copy()
    if ref.rotation_override is not None:
        extra = (ref.rotation_override - page.rotation) % 360
        if extra:
            img = img.transformed(QTransform().rotate(extra))
    return img


def _draw_centered(painter: QPainter, area: QRectF, img: QImage) -> None:
    iw, ih = img.width(), img.height()
    if iw == 0 or ih == 0:
        return
    scale = min(area.width() / iw, area.height() / ih)  # fit, preserve aspect
    w, h = iw * scale, ih * scale
    x = area.x() + (area.width() - w) / 2
    y = area.y() + (area.height() - h) / 2
    painter.drawImage(QRectF(x, y, w, h), img)


def render_to_printer(printer: QPrinter, vdoc, current_page: int, parent=None) -> bool:
    """Paint the selected pages onto ``printer``. Returns False if the job could not start."""
    pages = selected_pages(printer, vdoc.page_count, current_page)
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
            _draw_centered(painter, area, _page_image(vdoc, index, zoom))
    finally:
        painter.end()
    return True
