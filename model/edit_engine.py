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
        # Fresh per-source copies: reusing a live source across insert_pdf calls (or across save
        # attempts) drops its widgets after the first graft, which would strip form fields from the
        # output. One fresh copy per source, reused across that source's runs, with final= freeing
        # the graft maps on the last copy.
        fresh: dict[str, fitz.Document] = {}

        def source_copy(source_id: str) -> fitz.Document:
            if source_id not in fresh:
                fresh[source_id] = vdoc.fresh_source(source_id)
            return fresh[source_id]

        try:
            runs = _contiguous_runs(vdoc.ordered)
            for i, (source_id, start, end) in enumerate(runs):
                out.insert_pdf(
                    source_copy(source_id),
                    from_page=start,
                    to_page=end,
                    start_at=-1,
                    links=True,
                    annots=True,
                    widgets=True,
                    final=(i == len(runs) - 1),
                )

            # Apply absolute rotation overrides + per-page edits (output page i == ordered[i]).
            # Redactions run first as a destructive pass (apply_redactions rewrites the page and
            # would otherwise strip overlapping annotations); the non-destructive highlight/text-box
            # overlays go on top afterwards.
            from model.page_edits import apply_annotations, apply_redactions

            for i, ref in enumerate(vdoc.ordered):
                if ref.rotation_override is not None:
                    out[i].set_rotation(ref.rotation_override)
                if ref.annotations:
                    apply_redactions(out[i], ref.annotations)
                    apply_annotations(out[i], ref.annotations)

            # Apply AcroForm fills onto the copied widgets (M14). Done here, on the output, so the
            # shared read-only sources are never touched.
            from model.page_edits import apply_form_values

            apply_form_values(out, vdoc.form_values)

            out.set_toc(vdoc.remapped_toc())
            out.save(out_path, garbage=4, deflate=True, clean=True)
        finally:
            for doc in fresh.values():
                doc.close()
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
