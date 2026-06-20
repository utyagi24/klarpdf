"""Export — write a *derived* copy of the document in a chosen format (PLAN.md, M31.5 / M36).

Distinct from Save / Save As, which write the **editable** document (annotations stay annotations,
our marks round-trip on reopen — M31). Export writes a locked / derived artifact:

* **Flattened PDF** (M31.5): every annotation and form widget is baked into page **content** via
  PyMuPDF ``Document.bake()`` — the text layer is **preserved** (body text *and* the baked
  annotation text stay real, searchable text; nothing is rasterised), but the marks are now page
  content, not editable annotations, so they can't be moved / removed / re-edited in any tool. It is
  the opt-out counterpart to M31's round-trip: Save As stays editable, Export → PDF locks.
* **Images** (M36, planned): each page → PNG / JPEG at a chosen DPI.

Every format shares the edits-applied render (:meth:`PyMuPDFEngine.render_output`), so an export
reflects the same page order / rotation / redactions / annotations / fills a Save would write — and
a pending (unsaved) redaction is applied destructively in the *exported* copy without committing it
in the working document (the throwaway render keeps the redaction point-of-no-return tied to Save).

Headless and GUI-free; the ``File ▸ Export`` menu wiring lives in ``main_window``.
"""

from __future__ import annotations

from model.edit_engine import PyMuPDFEngine
from model.virtual_document import VirtualDocument


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
