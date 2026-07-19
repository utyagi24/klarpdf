"""Export — write a *derived* copy of the document in a chosen format (PLAN.md, M31.5 / M36).

Distinct from Save / Save As, which write the **editable** document (annotations stay annotations,
our marks round-trip on reopen — M31). Export writes a locked / derived artifact:

* **Flattened PDF** (M31.5): every annotation and form widget is baked into page **content** via
  PyMuPDF ``Document.bake()`` — the text layer is **preserved** (body text *and* the baked
  annotation text stay real, searchable text; nothing is rasterised), but the marks are now page
  content, not editable annotations, so they can't be moved / removed / re-edited in any tool. It is
  the opt-out counterpart to M31's round-trip: Save As stays editable, Export → PDF locks.
* **Images** (M36): the selected page(s) → PNG / JPEG at a chosen DPI, one file per page.
* **Selected pages as PDF** (M51): the selected page(s) → a new PDF, extracted **object-level**
  through the ordinary materialise path — so unlike the other formats it stays *editable* (a
  Save-like artifact of a page subset): the text layer, form fields, and our round-trippable
  annotations all carry, and the origin bookmarks / internal links whose targets were extracted
  are remapped to the new page numbers (the rest are dropped).

Every format shares the edits-applied render (:meth:`PyMuPDFEngine.render_output`), so an export
reflects the same page order / rotation / redactions / annotations / fills a Save would write — and
a pending (unsaved) redaction is applied destructively in the *exported* copy without committing it
in the working document (the throwaway render keeps the redaction point-of-no-return tied to Save).

Headless and GUI-free; the ``File ▸ Export`` menu wiring lives in ``main_window``.
"""

from __future__ import annotations

import os

import pymupdf as fitz

from model.edit_engine import PyMuPDFEngine
from model.virtual_document import VirtualDocument

_JPEG_EXTS = (".jpg", ".jpeg")


def export_flattened_pdf(vdoc: VirtualDocument, out_path: str) -> None:
    """Write ``vdoc`` to ``out_path`` as a **flattened** PDF.

    Builds the edits-applied output (exactly what a Save would write), then ``Document.bake()``
    turns every annotation and form widget into permanent page content — text-preserving, not
    rasterised — and saves. The result is a locked copy: the marks are page content, no longer
    editable annotations.
    """
    out = PyMuPDFEngine().render_output(vdoc)
    try:
        out.bake()  # annotations + form widgets → permanent page content (text layer preserved)
        out.save(out_path, garbage=4, deflate=True, clean=True)
    finally:
        out.close()


def export_selected_pages(vdoc: VirtualDocument, page_indices, out_path: str) -> None:
    """Write the pages at ``page_indices`` (indices into the live order, deduped, document order)
    to ``out_path`` as a new PDF — the object-level extract (M51).

    Materialises a :meth:`VirtualDocument.subset`, so the output is exactly what a Save would
    write for those pages: text layer / forms / annotations carried, rotation + crop + fills
    applied, a *pending* redaction applied destructively **in the extracted copy only** (the
    working document keeps it, still undoable — same side-artifact rule as the other exports),
    and the origin bookmarks + internal links remapped to the extracted page numbers.
    """
    indices = sorted(set(page_indices))
    if not indices:
        return
    PyMuPDFEngine().materialize(vdoc.subset(indices), out_path)


def export_page_images(
    vdoc: VirtualDocument,
    page_indices,
    base_path: str,
    dpi: int = 150,
    jpg_quality: int = 90,
) -> list[str]:
    """Export pages of the **edits-applied** output to image files — one file per page (M36).

    ``page_indices`` are indices into the live page order (``ordered[]``), the same indices the
    viewer / thumbnails use. The image **format** comes from ``base_path``'s extension
    (``.png`` / ``.jpg`` / ``.jpeg``). A single page writes ``base_path`` verbatim; with more than
    one, the document page number is appended, zero-padded — ``report.png`` → ``report-01.png`` …
    Returns the written paths in order.

    Rasterised from :meth:`PyMuPDFEngine.render_output` at ``dpi`` (1 pt = dpi/72 px), so each image
    reflects the page order / rotation / annotations / fills / redactions a Save would write — and a
    *pending* redaction exports as removed without committing it (the render copy is a throwaway).
    """
    indices = list(page_indices)
    if not indices:
        return []
    root, ext = os.path.splitext(base_path)
    is_jpeg = ext.lower() in _JPEG_EXTS
    single = len(indices) == 1
    pad = len(str(max(i + 1 for i in indices)))  # widest page number → consistent zero-padding
    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)

    out = PyMuPDFEngine().render_output(vdoc)
    written: list[str] = []
    try:
        for index in indices:
            target = base_path if single else f"{root}-{index + 1:0{pad}d}{ext}"
            pix = out[index].get_pixmap(matrix=matrix, alpha=False)
            if is_jpeg:
                pix.save(target, jpg_quality=jpg_quality)
            else:
                pix.save(target)
            written.append(target)
    finally:
        out.close()
    return written
