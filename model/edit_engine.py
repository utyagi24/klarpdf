"""Edit engines — materialize-on-save (the only write).

PLAN.md, "Materialize-on-Save": iterate the virtual document's ordered list and copy contiguous
same-source runs object-level (never rasterise/flatten), apply absolute rotation overrides,
rebuild the outline, then save. Object-level copies preserve the OCR text layer, annotations,
and form fields by construction.

Two engines behind one interface:
 * :class:`PyMuPDFEngine` — the default/authoritative engine (``fitz.insert_pdf``).
 * :class:`PyPdfEngine` — a pure-Python fallback (pypdf). Best-effort: handles page
   order/rotation/outline; PyMuPDF is authoritative for duplicate form-field handling.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pymupdf as fitz

from model.virtual_document import VirtualDocument


def _contiguous_runs(ordered) -> list[list]:
    """Collapse ``ordered`` into ``[source_id, from_page, to_page]`` runs of consecutive pages.

    A run extends while the next ref is the same source and exactly one page later, so each run
    becomes a single ``insert_pdf`` range copy.
    """
    runs: list[list] = []
    for ref in ordered:
        if (
            runs
            and ref.source_id == runs[-1][0]
            and ref.source_page_index == runs[-1][2] + 1
        ):
            runs[-1][2] = ref.source_page_index
        else:
            runs.append([ref.source_id, ref.source_page_index, ref.source_page_index])
    return runs


class EditEngine(ABC):
    """Common interface so the viewer/Save path is engine-agnostic."""

    @abstractmethod
    def materialize(self, vdoc: VirtualDocument, out_path: str) -> None:
        """Write ``vdoc``'s current ordered list to ``out_path`` as a new PDF."""


class PyMuPDFEngine(EditEngine):
    """Default engine. Lossless object-level page copy + outline rebuild via PyMuPDF."""

    def materialize(self, vdoc: VirtualDocument, out_path: str) -> None:
        out = fitz.open()
        try:
            runs = _contiguous_runs(vdoc.ordered)
            for i, (source_id, start, end) in enumerate(runs):
                src = vdoc.sources[source_id]
                # final=True only on the last copy, so per-source graft maps survive across
                # multiple runs from the same source (PLAN.md / PyMuPDF docs).
                out.insert_pdf(
                    src,
                    from_page=start,
                    to_page=end,
                    start_at=-1,
                    links=True,
                    annots=True,
                    widgets=True,
                    final=(i == len(runs) - 1),
                )

            # Apply absolute rotation overrides (output page i == ordered[i]).
            for i, ref in enumerate(vdoc.ordered):
                if ref.rotation_override is not None:
                    out[i].set_rotation(ref.rotation_override)

            out.set_toc(vdoc.remapped_toc())
            out.save(out_path, garbage=4, deflate=True, clean=True)
        finally:
            out.close()


class PyPdfEngine(EditEngine):
    """Pure-Python fallback (pypdf). Best-effort; PyMuPDF is the authoritative engine."""

    def materialize(self, vdoc: VirtualDocument, out_path: str) -> None:
        from pypdf import PdfReader, PdfWriter

        readers: dict[str, PdfReader] = {}

        def reader_for(source_id: str) -> "PdfReader":
            # Fallback reopens sources from their identity path (a real file path).
            if source_id not in readers:
                readers[source_id] = PdfReader(source_id)
            return readers[source_id]

        writer = PdfWriter()
        for ref in vdoc.ordered:
            page = reader_for(ref.source_id).pages[ref.source_page_index]
            added = writer.add_page(page)
            if ref.rotation_override is not None:
                added.rotation = ref.rotation_override  # absolute

        # Rebuild outline with proper nesting from the remapped, level-repaired TOC.
        parents: dict[int, object] = {}
        for entry in vdoc.remapped_toc():
            level, title, page = entry[0], entry[1], entry[2]
            parent = parents.get(level - 1)
            item = writer.add_outline_item(title, page - 1, parent=parent)
            parents[level] = item

        with open(out_path, "wb") as fh:
            writer.write(fh)
