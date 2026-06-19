"""Printing render path (PLAN.md, M12 + M25).

Rendering is **edits-aware**: it rasterises the same in-memory output document a Save would write
(:meth:`model.edit_engine.PyMuPDFEngine.render_output`), so the print, the live preview, and the
"Save as PDF" output all show the page order, rotations, form values, highlights, text boxes, and
(destructive) redactions exactly as they will be saved. A *pending* redaction therefore prints as
removed without becoming a point of no return — the render copy is a throwaway; the shared sources
and the undo stack are never touched.

The system print *dialog* can't run headless, but ``QPrinter`` can write to a PDF file, so the
render path (:func:`render_to_printer` + :func:`selected_pages` + :func:`_scale_factor`) is
exercised headlessly by pointing a ``QPrinter`` at a PDF output. Three entry points share it:
 * :func:`print_document` — the system print dialog;
 * :func:`print_preview`  — a ``QPrintPreviewDialog`` with a live fit/actual/shrink selector;
 * :func:`save_as_pdf`    — a ``QPrinter`` PDF destination ("Save as PDF": a rendered, fixed-layout
   snapshot, deliberately distinct from the lossless object-level Save As).
"""

from __future__ import annotations

import enum
from pathlib import Path

import pymupdf as fitz
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtPrintSupport import (
    QAbstractPrintDialog,
    QPrintDialog,
    QPrintPreviewDialog,
    QPrintPreviewWidget,
    QPrinter,
)
from PySide6.QtWidgets import QComboBox, QDialog, QFileDialog, QMessageBox, QToolBar

from model.edit_engine import PyMuPDFEngine

# Raster render cap: printers report 600–1200 dpi, but rasterising a full page at that is huge
# (an A4 at 1200 dpi is ~70 MB) for no visible gain on a basic print. Render at most this, then let
# the painter smooth-scale up to the page.
_MAX_RENDER_DPI = 300


class ScaleMode(enum.Enum):
    """How a page image is sized onto the paper's printable area."""

    FIT = "Fit to page"          # scale up or down to fill the printable area, preserving aspect
    ACTUAL = "Actual size"       # 1 PDF point = 1/72 inch on paper; clipped if larger than the page
    SHRINK = "Shrink oversized"  # actual size, but scale down anything larger than the page


def print_document(vdoc, current_page: int, parent=None) -> bool:
    """Show the system print dialog and print the chosen pages. Returns True if a job was sent."""
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    if vdoc.path:
        printer.setDocName(Path(vdoc.path).stem)
    printer.setFromTo(1, vdoc.page_count)  # advertise the valid range to the dialog
    dialog = QPrintDialog(printer, parent)
    dialog.setOption(QAbstractPrintDialog.PrintDialogOption.PrintPageRange, True)
    dialog.setOption(QAbstractPrintDialog.PrintDialogOption.PrintCurrentPage, True)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    return render_to_printer(printer, vdoc, current_page, parent=parent)


def print_preview(vdoc, current_page: int, parent=None) -> None:
    """Open a print preview with a live fit/actual/shrink selector. The dialog's own Print button
    prints through the same ``QPrinter`` (honouring the selected scale)."""
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    if vdoc.path:
        printer.setDocName(Path(vdoc.path).stem)
    dialog = QPrintPreviewDialog(printer, parent)
    rendered = PyMuPDFEngine().render_output(vdoc)  # built once; reused across repaints
    state = {"scale": ScaleMode.FIT}

    def repaint(p: QPrinter) -> None:
        _paint(p, rendered, selected_pages(p, rendered.page_count, current_page), state["scale"])

    dialog.paintRequested.connect(repaint)

    # Drop a scale-mode combo onto the preview's toolbar so fit/actual/shrink is live + visible.
    preview = dialog.findChild(QPrintPreviewWidget)
    toolbar = dialog.findChild(QToolBar)
    if preview is not None and toolbar is not None:
        combo = QComboBox()
        for mode in ScaleMode:
            combo.addItem(mode.value, mode)
        toolbar.addSeparator()
        toolbar.addWidget(combo)
        combo.currentIndexChanged.connect(
            lambda _i: (state.__setitem__("scale", combo.currentData()), preview.updatePreview())
        )
    try:
        dialog.exec()
    finally:
        rendered.close()


def save_as_pdf(vdoc, current_page: int, parent=None) -> bool:
    """"Save as PDF" — render every page to a PDF file via a ``QPrinter`` PDF destination. This is a
    rasterised, fixed-layout snapshot (no selectable text), distinct from the lossless Save As."""
    suggested = str(Path(vdoc.path).with_suffix(".pdf")) if vdoc.path else ""
    path, _ = QFileDialog.getSaveFileName(parent, "Save as PDF", suggested, "PDF files (*.pdf)")
    if not path:
        return False
    if not path.lower().endswith(".pdf"):
        path += ".pdf"
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(path)
    return render_to_printer(printer, vdoc, current_page, parent=parent)


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


def render_to_printer(
    printer: QPrinter, vdoc, current_page: int, scale: ScaleMode = ScaleMode.FIT, parent=None
) -> bool:
    """Paint the selected pages of ``vdoc`` (edits applied) onto ``printer``. Returns False if the
    document could not be rendered or the job could not start."""
    try:
        rendered = PyMuPDFEngine().render_output(vdoc)
    except Exception as exc:  # a malformed edit shouldn't crash the app mid-print
        if parent is not None:
            QMessageBox.critical(parent, "Print", f"Could not render the document:\n{exc}")
        return False
    try:
        pages = selected_pages(printer, rendered.page_count, current_page)
        return _paint(printer, rendered, pages, scale, parent)
    finally:
        rendered.close()


def _paint(printer: QPrinter, doc: fitz.Document, pages: list[int], scale: ScaleMode, parent=None) -> bool:
    if not pages:
        return False
    painter = QPainter()
    if not painter.begin(printer):
        if parent is not None:
            QMessageBox.warning(parent, "Print", "Could not start the print job.")
        return False
    try:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        render_dpi = min(printer.resolution(), _MAX_RENDER_DPI)
        zoom = render_dpi / 72.0
        for i, index in enumerate(pages):
            if i:
                printer.newPage()
            img = _page_image(doc, index, zoom)
            area = QRectF(printer.pageLayout().paintRectPixels(printer.resolution()))
            factor = _scale_factor(scale, area.width(), area.height(),
                                   img.width(), img.height(), printer.resolution(), render_dpi)
            _draw_centered(painter, area, img, factor)
    finally:
        painter.end()
    return True


def _page_image(doc: fitz.Document, index: int, zoom: float) -> QImage:
    """Rasterise ``doc[index]`` at ``zoom`` (1.0 == 72 dpi). The doc is the edits-applied render
    output, so rotation and every page edit are already baked into the page."""
    page = doc[index]
    pm = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    # .copy() detaches from the PyMuPDF sample buffer, which is freed when pm goes out of scope.
    return QImage(pm.samples, pm.width, pm.height, pm.stride, QImage.Format.Format_RGB888).copy()


def _scale_factor(
    scale: ScaleMode, area_w: float, area_h: float, iw: int, ih: int, printer_dpi: int, render_dpi: int
) -> float:
    """The image-to-device scale for ``scale``. FIT fills the printable area (aspect kept); ACTUAL
    maps 1 PDF point to 1/72 inch (= printer_dpi / render_dpi, since the image was rendered at
    render_dpi); SHRINK is ACTUAL but never enlarges past FIT."""
    if iw == 0 or ih == 0:
        return 1.0
    fit = min(area_w / iw, area_h / ih)
    if scale is ScaleMode.FIT:
        return fit
    actual = printer_dpi / render_dpi
    return actual if scale is ScaleMode.ACTUAL else min(actual, fit)


def _draw_centered(painter: QPainter, area: QRectF, img: QImage, factor: float) -> None:
    w, h = img.width() * factor, img.height() * factor
    x = area.x() + (area.width() - w) / 2
    y = area.y() + (area.height() - h) / 2
    painter.drawImage(QRectF(x, y, w, h), img)
