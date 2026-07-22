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


def _apply_crop(page: "fitz.Page", rect: tuple) -> None:
    """Apply a ``PageRef.crop_override`` to an output page via ``set_cropbox`` (M48).

    ``rect`` is in the page's unrotated **content** frame (origin = the current CropBox top-left,
    the space word boxes live in); ``set_cropbox`` wants the unrotated **MediaBox** frame — shift
    by the CropBox origin, then clamp to the MediaBox (a reset rect is exactly the MediaBox; a
    dragged rect was already clamped by the model). Crop *hides* the area outside the rect — the
    content stays in the file (Redact removes)."""
    cx, cy = page.cropbox_position
    target = fitz.Rect(rect[0] + cx, rect[1] + cy, rect[2] + cx, rect[3] + cy) & page.mediabox
    page.set_cropbox(target)


def _encryption_args(vdoc: VirtualDocument) -> dict:
    """The ``Document.save`` keywords that carry the document's encryption (M54); ``{}`` when it
    saves unencrypted.

    AES-256 only — the one real-cryptography tier. With no restriction flags the owner password
    equals the user password (one password, one secret). With flags set, the owner password is a
    fresh random secret held nowhere: PDF permissions bind only readers authenticated as *user*,
    so a shared owner/user password would authenticate every reader as owner and silently void
    the flags. Losing that owner secret costs nothing — we re-encrypt from the decrypted
    in-memory sources on every save, never by re-authenticating as owner.
    """
    if vdoc.password is None:
        return {}
    if vdoc.permissions == -1:
        owner_pw = vdoc.password
    else:
        import secrets

        owner_pw = secrets.token_urlsafe(30)  # 40 chars — MuPDF's password length ceiling
    return {
        "encryption": fitz.PDF_ENCRYPT_AES_256,
        "user_pw": vdoc.password,
        "owner_pw": owner_pw,
        "permissions": vdoc.permissions,
    }


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
        out = self._build_output(vdoc)
        try:
            out.save(out_path, garbage=4, deflate=True, clean=True, **_encryption_args(vdoc))
        finally:
            out.close()

    def render_output(self, vdoc: VirtualDocument) -> fitz.Document:
        """The edits-applied output document, built **in memory and not saved** — page ``i``
        corresponds to ``ordered[i]`` with rotation / redactions / annotations / form fills already
        applied. Same build as :meth:`materialize`, so what gets rendered (print / preview /
        print-to-PDF) matches exactly what a Save would write. The caller owns the returned document
        and must close it.

        Rendering off this throwaway copy keeps the destructive ``apply_redactions`` away from the
        shared sources and the undo stack: printing a *pending* redaction shows it removed without
        turning the print into a point of no return.
        """
        return self._build_output(vdoc)

    def _build_output(self, vdoc: VirtualDocument) -> fitz.Document:
        """Build the materialised output document (open, unsaved). Shared by save + render."""
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
            # Round-trip (M31): insert_pdf(annots=True) copied every source annotation, including
            # the KlarPDF marks a prior save baked in. The model now owns those (read back on open,
            # with any move / edit / removal applied), so strip the copies and re-add from the
            # model — the model is the single source of truth. Stripping runs on *every* page (even
            # one with no model annotations) so a removed mark is actually dropped; foreign
            # annotations are preserved. Then redactions run first as a destructive pass
            # (apply_redactions rewrites the page and would otherwise strip overlapping annotations);
            # the non-destructive highlight/text-box overlays go on top afterwards.
            from model.content_marks import apply_content_marks
            from model.foreign_annots import apply_foreign_edits
            from model.page_edits import (
                apply_annotations,
                apply_redactions,
                strip_klarpdf_annotations,
            )

            for i, ref in enumerate(vdoc.ordered):
                if ref.rotation_override is not None:
                    out[i].set_rotation(ref.rotation_override)
                if ref.crop_override is not None:
                    _apply_crop(out[i], ref.crop_override)  # set_cropbox takes unrotated coords
                strip_klarpdf_annotations(out[i])
                # Foreign-annotation deletions (M66) run next, while the copied annotations are
                # still exactly as `insert_pdf` brought them across — fingerprints are computed
                # against that state. Everything not named here passes through untouched, which is
                # what keeps this zero-fidelity-risk for annotation types the model cannot draw.
                if ref.annotations:
                    apply_foreign_edits(out[i], ref.annotations)
                if ref.annotations:
                    apply_redactions(out[i], ref.annotations)
                    # R4 content marks sit between the two annotation passes: after redaction (which
                    # rewrites the content stream and would erase a stamp drawn under it) and before
                    # the overlays (which stay annotations, so they float above page content — a
                    # stamp included, exactly as they do above the page's own ink).
                    apply_content_marks(out[i], ref.annotations)
                    apply_annotations(out[i], ref.annotations)

            # Apply AcroForm fills onto the copied widgets (M14). Done here, on the output, so the
            # shared read-only sources are never touched.
            from model.page_edits import apply_form_values

            apply_form_values(out, vdoc.form_values)

            # Rebuild internal GoTo links + the outline against the new page order (M33 / M1):
            # insert_pdf drops cross-run internal links and never copies the outline, so both are
            # remapped here — surviving targets repointed to their new index, deleted ones dropped.
            from model.links_remap import remap_internal_links

            remap_internal_links(out, vdoc)
            out.set_toc(vdoc.remapped_toc())

            # Document metadata (M53): carry the origin's Info dict + XMP packet through (or the
            # user's edit / removal) — insert_pdf copies neither store, so without this every
            # save silently stripped them.
            from model.metadata import apply_metadata

            apply_metadata(out, vdoc)
        except Exception:
            out.close()
            raise
        finally:
            for doc in fresh.values():
                doc.close()
        return out


class PyPdfEngine(EditEngine):
    """Pure-Python fallback (pypdf). Best-effort; PyMuPDF is the authoritative engine."""

    def materialize(self, vdoc: VirtualDocument, out_path: str) -> None:
        # M54: pypdf can't write AES without a dev-only `cryptography` extra (PLAN.md), and a
        # weaker cipher or a silent unencrypted write would both betray the password promise.
        if vdoc.password is not None:
            raise NotImplementedError(
                "PyPdfEngine cannot write AES-256 encryption; PyMuPDF is the ship engine"
            )
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
