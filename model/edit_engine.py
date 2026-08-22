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


#: Object cleanup for the route that **copies the origin** — an unchanged page set (M110).
#: Drop objects nothing references and compact the xref, and stop there. Linear in the size of the
#: document, so it costs nothing on any file.
#:
#: **A Save is not an optimiser.** The levels above this one hunt for duplicate objects and streams
#: across the whole graph, which on this route is the *origin's* graph — a document somebody else
#: wrote, arriving packed as they packed it. Shrinking it is not this operation's job:
#: **Reduced-Size PDF** is where a user asks for a smaller file, and a Save should hand back
#: roughly what it was given. Measured, that is what this does — every corpus document still saves
#: *smaller than the file it came from* at this level (`ssa-1-bk.pdf` 233,320 → 224,075,
#: `f8949.pdf` 150,240 → 81,352), and re-saving an output at this level reproduces its size
#: exactly, so nothing ratchets upward across saves.
#:
#: The cost of not stopping here is the milestone: the duplicate hunt is **quadratic** in object
#: count where this is linear — 0.11 s against 0.02 s at 2,000 objects, but 360 s against 1.08 s at
#: 80,000 — and a 572-page, 48,877-object prospectus took **289 s** to save.
GARBAGE_COPY = 2

#: Object cleanup for the route that **grafts pages** into a fresh document (M110).
#: Here the duplicates are ours: a duplicated page carries a second reference to every image on it,
#: and only level 4 merges identical *streams*. Measured on an image-heavy page duplicated 20
#: times, level 4 writes **1.9 MB** where levels 1–3 write **39.5 MB**, and it is 4× faster doing
#: it, because detecting that twenty images are identical costs less than compressing twenty
#: copies. So the expensive level is used exactly when we were the ones who did the copying —
#: cleaning up after ourselves rather than tidying somebody else's file.
#:
#: It stays affordable here for a structural reason rather than a lucky one: ``insert_pdf``
#: collapses the object graph on the way through, so the graft has already reduced 48,877 objects
#: to 2,178 before the hunt begins. Measured on that same prospectus with one page deleted:
#: **2.08 s**.
GARBAGE_GRAFT = 4


def write_options(garbage: int) -> dict:
    """The ``Document.save`` keywords every PDF this project writes shares, at ``garbage`` level.

    **One named set on purpose.** These options were four copies of a literal across ``materialize``
    and the three export writes, and when M93 added ``use_objstms=1`` it landed on one of them —
    leaving Reduced-Size PDF (the feature whose entire job is a smaller file) writing 146 KB *more*
    than a plain Save on one document (M111). A change to how this project writes a PDF now has one
    place to land.

    ``use_objstms=1`` packs objects into object streams and writes a compressed cross-reference
    (PDF 1.5, 2003 — universally supported). Without it every object is written as a plain
    uncompressed dictionary, which is what the save used to do and what made keeping the document's
    own structure look expensive: a 9-page tagged form came back at 316 KB against a 233 KB input.
    With it the same save is **151 KB — smaller than the input** while keeping everything. Most real
    PDFs arrive using object streams (that form had 26 of them), so this is closer to preserving how
    the file was written than to compressing it further.

    ``clean=True`` sanitises content streams; it is **not** what made the save slow (measured at
    ~1.9 s on the pathological file, and dropping it makes ``garbage=4`` *slower*, not faster).

    ``garbage`` is the caller's, because it is the one option that depends on **who did the
    copying** rather than on what a PDF ought to look like — see :data:`GARBAGE_COPY` /
    :data:`GARBAGE_GRAFT` and :meth:`PyMuPDFEngine.save_options`.
    """
    return {"garbage": garbage, "deflate": True, "clean": True, "use_objstms": 1}


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

    **Two kinds of encrypted document, and M54 only covered one.** A document that *needs* a
    password to open is decrypted at open and its password recorded, which is the branch below. A
    document that opens freely but restricts what you may do with it — an owner password only, the
    common shape for a published form — was never decrypted and has no password to record, so this
    returned ``{}`` and the save produced a file with no encryption and every permission granted.
    Nobody was told (TC-002, 2026-08-13). That case is now carried by keeping the encryption the
    output copy already has, which only the unchanged-page-set route can do — see
    :func:`_keep_encryption`. A rebuild genuinely cannot: it is a new document, and reproducing the
    original's encryption would need the owner password, which we do not have and must not need.
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


def _keep_encryption(vdoc: VirtualDocument) -> dict:
    """``{"encryption": PDF_ENCRYPT_KEEP}`` when the output should retain the encryption it has.

    Only meaningful on the unchanged-page-set route, where the output *is* a copy of the origin and
    therefore still carries its encryption dictionary. It applies exactly when the origin was
    encrypted but never decrypted — i.e. it opened without a password. A document the user opened
    with a password, or whose password they have since removed, was decrypted at open and no longer
    reports encryption here, so a removed password stays removed.
    """
    if vdoc.password is None and vdoc.origin_carries_encryption():
        return {"encryption": fitz.PDF_ENCRYPT_KEEP}
    return {}


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

    def save_options(self, vdoc: VirtualDocument) -> dict:
        """The exact ``Document.save`` keywords :meth:`materialize` writes ``vdoc`` with (M110).

        Public because ``export_reduced_pdf`` reports a "before" size it promises is *what a plain
        Save would write*, and a promise like that has to be measured with the real thing rather
        than a second copy of the literal that drifts away from it (M111).

        The cleanup level follows the **route**, which is the split :meth:`_build_output` already
        makes: the graft cleans up after its own page-copying (:data:`GARBAGE_GRAFT`), while a copy
        of the origin is left as packed as it arrived (:data:`GARBAGE_COPY`) — a Save was not asked
        to optimise anybody's file, and Reduced-Size PDF is where that is asked for.

        **The floor matters more than the ceiling.** Level 1 is what deletes an image a redaction
        detached from its page — below it the orphaned object stays in the file, recoverable by
        anything that walks objects rather than pages. :data:`GARBAGE_COPY` sits above that floor,
        and ``tests/test_redaction_orphans.py`` pins it, because the redaction verification itself
        structurally cannot see this: it re-reads the output and checks the *text* with two engines,
        and an orphaned picture of a secret is not text to either.
        """
        return write_options(GARBAGE_COPY if vdoc.page_set_unchanged() else GARBAGE_GRAFT)

    def materialize(self, vdoc: VirtualDocument, out_path: str) -> None:
        """Write ``vdoc``'s current state to ``out_path``.

        ``_encryption_args`` wins when the user has set a password; otherwise an unchanged page set
        keeps whatever encryption its copy of the origin already carries. A rebuild has nothing to
        keep, so it saves as it always did.

        The write keywords come from :meth:`save_options` — see :func:`write_options` for what they
        are and :data:`GARBAGE_COPY` / :data:`GARBAGE_GRAFT` for why the cleanup level is a
        property of the route rather than of the file.
        """
        keep = _keep_encryption(vdoc) if vdoc.page_set_unchanged() else {}
        out = self._build_output(vdoc)
        try:
            out.save(out_path, **self.save_options(vdoc), **(_encryption_args(vdoc) or keep))
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
        """Build the materialised output document (open, unsaved). Shared by save + render.

        Two routes, chosen by :meth:`~model.virtual_document.VirtualDocument.page_set_unchanged`:

        * **The page set is unchanged** — every page of the origin, in order. Start from a *copy of
          the origin* and edit it. Output page ``i`` is still ``ordered[i]``, so every pass below is
          unchanged; what differs is that the document arrives with its own furniture intact.
        * **Anything structural happened** — reorder, delete, insert, a second document. Then a new
          document genuinely has to be assembled, and the graft below is the only way to do it.

        The first route exists because ``insert_pdf`` copies **pages**, and a PDF keeps a great deal
        at the *document* level: the accessibility structure tree and ``/MarkInfo``, Reader
        Extensions ``/Perms``, the ``/Names`` tree, and encryption. None of it rides along with a
        page, so grafting into an empty document silently dropped all of it — a tagged, AES-encrypted
        federal form came back untagged, unencrypted and with every permission granted, and the two
        hyperlinks in it were rewritten into ``/Launch`` actions pointing at local files that do not
        exist (TC-002, 2026-08-13).

        The pattern was already visible here: the outline and internal links get rebuilt (M33) and
        the metadata stores get carried across (M53), each pass added after a save was found to be
        dropping something ``insert_pdf`` had never copied. The structure tree is the next item on
        that list and the one that **cannot** have such a pass written for it — it is a tree of
        references into page content, not a store that can be copied over afterwards. So the answer
        for the unchanged case is to stop discarding it.

        This costs output size: nothing is being thrown away any more. The same form saved 379
        objects rather than 97, and the 97-object version was smaller than its own input precisely
        because of what was missing from it.
        """
        if vdoc.page_set_unchanged():
            return self._edit_origin_copy(vdoc)
        return self._graft_output(vdoc)

    def _edit_origin_copy(self, vdoc: VirtualDocument) -> fitz.Document:
        """The unchanged-page-set route: apply the per-page edits to a copy of the origin.

        ``remap_internal_links`` and ``set_toc`` are deliberately **not** run here. Both exist to
        repair what the graft breaks, and neither has anything to repair when no page has moved —
        running them anyway would flatten named destinations into direct GoTos and rewrite a rich
        outline (colours, open state, named targets) through ``set_toc``'s simple triples, losing
        fidelity to fix nothing. The metadata pass stays: it is how a user's *edit* to the Info dict
        or XMP is applied, which has nothing to do with grafting.
        """
        out = vdoc.fresh_source(vdoc.origin_source_id)
        try:
            self._apply_page_edits(out, vdoc)
            from model.metadata import apply_metadata

            apply_metadata(out, vdoc)
        except Exception:
            out.close()
            raise
        return out

    def _apply_page_edits(self, out: fitz.Document, vdoc: VirtualDocument) -> None:
        """Apply every per-page edit to ``out``, where output page ``i`` is ``ordered[i]``.

        Shared by both routes: these act on a page and do not care which document it sits in, which
        is exactly why the unchanged-page-set route can skip the graft and still be correct.
        """
        # Round-trip (M31): a copied page carries every source annotation, including the KlarPDF
        # marks a prior save baked in. The model now owns those (read back on open, with any move /
        # edit / removal applied), so strip the copies and re-add from the model — the model is the
        # single source of truth. Stripping runs on *every* page (even one with no model
        # annotations) so a removed mark is actually dropped; foreign annotations are preserved.
        # Then redactions run first as a destructive pass (apply_redactions rewrites the page and
        # would otherwise strip overlapping annotations); the non-destructive highlight/text-box
        # overlays go on top afterwards.
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
            # Foreign-annotation deletions (M66) run next, while the copied annotations are still
            # exactly as they arrived — fingerprints are computed against that state. Everything
            # not named here passes through untouched, which is what keeps this
            # zero-fidelity-risk for annotation types the model cannot draw.
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

        # Create any new AcroForm fields (M69) **before** the fill pass, so a value typed into a
        # field created in this same session lands on it like any other fill.
        from model.form_fields import apply_new_fields

        for i, ref in enumerate(vdoc.ordered):
            if ref.annotations:
                apply_new_fields(out[i], ref.annotations)

        # Apply AcroForm fills onto the widgets (M14). Done on the output, so the shared read-only
        # sources are never touched.
        from model.page_edits import apply_form_values

        apply_form_values(out, vdoc.form_values)

    def _graft_output(self, vdoc: VirtualDocument) -> fitz.Document:
        """The structural route: assemble a new document out of the ordered pages."""
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

            self._apply_page_edits(out, vdoc)

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
