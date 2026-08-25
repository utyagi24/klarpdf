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

import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

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

#: The same level named for **what it does** rather than for who asks. `Export ▸ Reduced Size PDF`
#: writes with it whichever route the document took (M111): it is the one operation the caller
#: reaches for *because* they want a smaller file, and the one that creates duplicate streams of its
#: own — re-encoding every image to one JPEG quality can turn two different images into identical
#: ones. One definition, two reasons; not a second copy of the number.
GARBAGE_DEDUP = GARBAGE_GRAFT


#: Sanitise content streams on the way out — re-parse every stream and re-emit every operator.
#: **Only for a write that rewrote page content itself** (M114): `apply_redactions` rewrites a page,
#: `bake()` draws annotations into one, `rewrite_images` re-encodes what a page draws. There the
#: cost buys something — the output is our own construction, and cleaning it is tidying up after
#: ourselves.
#:
#: **Never for a write that only copied a page through.** It has been in the save since M1 with no
#: recorded reason, and measured across the 56-document corpus it is a straight loss there:
#: content streams left byte-identical to the source go from **324 / 1,315 pages to 1,315 / 1,315**
#: without it, the corpus saves 70% faster (10.9 s → 3.3 s), and the "a save does not grow the file
#: it was given" promise gets *stronger* — files ending up larger than their source drop from 3 to 1.
#:
#: It is not merely wasteful, it is **lossy in a way that matters**: it re-emits operators in a
#: different order, and on three corpus documents Poppler then extracts the text in a different
#: reading order than from the source — `Invoice-6KNSJA3E-0001.pdf` moves "Subtotal / Total /
#: Amount due" thirteen lines up. Anything consuming extracted text (a search index, a screen
#: reader, a diff, an agent reading the PDF) sees content the source did not have in that order.
#: Dropping it makes all three byte-identical again. TC-012 reported exactly this and it is the half
#: §M114 first failed to reproduce — the retest's document was not one of the three.
CLEAN_REWRITTEN = True

#: Leave content streams exactly as they arrived — the copy route's default (M114). See
#: :data:`CLEAN_REWRITTEN` for the measurements.
CLEAN_COPIED = False


#: Object cleanup for the route that **appends** to the file it was given (M116). Not a choice:
#: MuPDF refuses an incremental write with any collection at all (*"Can't do incremental writes
#: with garbage collection"*, measured at levels 1 and 2), and it is the right answer anyway —
#: collecting means renumbering, and an append that renumbered the objects it did not write would
#: invalidate every offset in the revision below it.
#:
#: **This sits below the orphan floor** that :data:`GARBAGE_COPY` and
#: ``tests/test_redaction_orphans.py`` exist to hold: level 1 is what deletes an image a redaction
#: detached from its page, and at level 0 that picture is still in the file. The floor is not
#: lowered — the append route is simply never taken by a save that redacts anything, which
#: :meth:`~model.virtual_document.VirtualDocument.edits_are_additive` refuses on the separate and
#: stronger ground that an append leaves the *whole* previous revision recoverable. Redaction is
#: therefore excluded twice over, and the two exclusions are independent on purpose.
GARBAGE_APPEND = 0


def write_options(garbage: int, clean: bool) -> dict:
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

    ``garbage`` and ``clean`` are both the caller's, and **both are required**, because they are the
    two options that depend on *what this particular write is doing* rather than on what a PDF ought
    to look like. Defaulting either one is how M111 happened — four copies of a literal, and
    ``use_objstms`` reached exactly one of them. Making them explicit forces every call site to say
    what it means. See :data:`GARBAGE_COPY` / :data:`GARBAGE_GRAFT` and
    :meth:`PyMuPDFEngine.save_options`.
    """
    return {"garbage": garbage, "deflate": True, "clean": clean, "use_objstms": 1}


def append_options() -> dict:
    """The ``Document.save`` keywords for a write that **appends to the file it was given** (M116).

    :func:`write_options` with the only cleanup level an incremental write allows
    (:data:`GARBAGE_APPEND`), plus the two keywords that make it one.

    ``encryption=PDF_ENCRYPT_KEEP`` is passed even for a document that carries no encryption, and
    that is not belt-and-braces: PyMuPDF's default is ``PDF_ENCRYPT_NONE``, which MuPDF reads as a
    *request to change* the encryption and refuses outright — *"Can't do incremental writes when
    changing encryption"*, on a plain unprotected PDF. ``KEEP`` is how you say "leave it as it is",
    which is also exactly the promise this route makes about everything else in the file.

    ``clean`` stays :data:`CLEAN_COPIED` for the reason it is off on the copy route — this write
    did not rewrite any page's content — and here there is a second one: measured, ``clean=True``
    is *accepted* on an incremental write and makes it write 33% more, which is the entire point of
    the route spent on sanitising streams nobody touched.
    """
    return {
        **write_options(GARBAGE_APPEND, clean=CLEAN_COPIED),
        "incremental": True,
        "encryption": fitz.PDF_ENCRYPT_KEEP,
    }


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

        **``clean`` follows a different question, asked of the same model** (M114). The route says
        *who copied the objects*; sanitising content streams turns on *whether this write rewrote
        page content at all* — and those are not the same split, which is why this is a second
        decision rather than a second use of the first. A redaction rewrites a page
        (``apply_redactions``) and an R4 content mark appends a stream to one, so those writes clean
        up after themselves; a save that merely copies pages through does not, and cleaning there
        costs time, costs bytes and reorders the text a second engine extracts
        (:data:`CLEAN_REWRITTEN`). Both sub-questions are already answered by the model — the same
        ``has_redactions`` / ``has_content_marks`` pair that decides whether a save is a
        point of no return in ``MainWindow._write_to``.
        """
        rewrites_content = vdoc.has_redactions() or vdoc.has_content_marks()
        return write_options(
            GARBAGE_COPY if vdoc.page_set_unchanged() else GARBAGE_GRAFT,
            clean=CLEAN_REWRITTEN if rewrites_content else CLEAN_COPIED,
        )

    def save_keywords(self, vdoc: VirtualDocument) -> dict:
        """**Everything** :meth:`materialize` hands to ``Document.save`` — the option set above plus
        the encryption choice (M111).

        Two halves, one question. ``export_reduced_pdf`` reports a baseline it calls *what a plain
        Save would write*, and a Save also carries the document's encryption: an AES-128 SSA form
        writes 2,231 B more than the same content unencrypted, so a baseline measured without it
        understates the starting size exactly the way the missing ``use_objstms`` did.
        """
        keep = _keep_encryption(vdoc) if vdoc.page_set_unchanged() else {}
        return {**self.save_options(vdoc), **(_encryption_args(vdoc) or keep)}

    def appends(self, vdoc: VirtualDocument) -> bool:
        """Can this save be written by **appending** to the file the document was opened from?

        The third route (M116), and the second fork on the axis §M110 opened: the first asks who
        copied the objects, this one asks whether anything in the file needs to change at all.

        Three conditions, each refusing for its own reason:

        * :meth:`~model.virtual_document.VirtualDocument.edits_are_additive` — the model's half.
          Every edit adds a mark and nothing was removed, moved, rotated, cropped, filled or
          rewritten. This is the whitelist, and it is where a redaction is refused.
        * :meth:`~model.virtual_document.VirtualDocument.origin_bytes` — there has to be a file to
          append *to*, and the in-memory source has to be a view of it. ``None`` for a document
          that needed a password (stored decrypted) and for a subset view.
        * :meth:`~model.virtual_document.VirtualDocument.origin_needed_repair` — MuPDF will not
          append to a file whose cross-reference table it had to rebuild.

        Plus the encryption question, asked as *"would this save write different encryption?"*
        rather than as a fact about the document: ``_encryption_args`` is non-empty exactly when
        the save must (re-)encrypt from a password the model holds, and MuPDF refuses an
        incremental write that changes encryption. An owner-password document is unaffected — it
        keeps the encryption its own bytes carry, which is what :func:`_keep_encryption` already
        asks a plain save to do and what ``PDF_ENCRYPT_KEEP`` does here.

        **Nothing falls back.** Every one of these is decidable before the write, and the four ways
        MuPDF can refuse an incremental save — collection, an encryption change, a stream-opened
        document, a repaired file — are each closed above or by construction in
        :meth:`_append_to_origin`. A fallback would turn a defect in this predicate into a silent
        return to the full rewrite: correct output, no error, and nothing but a byte count to
        notice it by. The measurement that stands in for it is the corpus, run through this same
        method rather than a copy of it.
        """
        return (
            vdoc.edits_are_additive()
            and vdoc.origin_bytes() is not None
            and not vdoc.origin_needed_repair()
            and not _encryption_args(vdoc)
        )

    def save_size(self, vdoc: VirtualDocument, built: "fitz.Document") -> int:
        """How many bytes a plain Save of ``vdoc`` would write — measured by writing one (M116).

        ``built`` is the caller's already-materialised output (:meth:`render_output`), used on the
        full-rewrite routes so the document is not assembled twice; the append route ignores it and
        writes a throwaway probe, because what a Save writes there is the origin's own file with a
        revision on the end and no output document exists to measure.

        Public and asked *this* way for M111's reason. ``export_reduced_pdf`` reports a "before"
        size it calls **what a plain Save would write**, and a promise like that has to be measured
        with the real thing: it was once computed from a second copy of the save keywords, which
        drifted, and it overstated the saving by 143,143 B on a 7 MB prospectus. M116 moved the
        thing being described again — a Save that only adds a mark now writes the file it was given
        plus a couple of kilobytes — and the number followed rather than quietly meaning something
        else. ``tests/test_reduce.py`` compares it against a real ``materialize``, which is what
        caught it.
        """
        if self.appends(vdoc):
            with tempfile.TemporaryDirectory() as probe_dir:
                probe = os.path.join(probe_dir, "save.pdf")
                self._append_to_origin(vdoc, probe)
                return os.path.getsize(probe)
        return len(built.tobytes(**self.save_keywords(vdoc)))

    def materialize(self, vdoc: VirtualDocument, out_path: str) -> None:
        """Write ``vdoc``'s current state to ``out_path``.

        ``_encryption_args`` wins when the user has set a password; otherwise an unchanged page set
        keeps whatever encryption its copy of the origin already carries. A rebuild has nothing to
        keep, so it saves as it always did.

        The write keywords come from :meth:`save_keywords` — see :func:`write_options` for what
        they are and :data:`GARBAGE_COPY` / :data:`GARBAGE_GRAFT` for why the cleanup level is
        a property of the route rather than of the file.

        A save that only *adds* marks skips all of that and appends instead — see :meth:`appends`.
        """
        if self.appends(vdoc):
            self._append_to_origin(vdoc, out_path)
            return
        out = self._build_output(vdoc)
        try:
            out.save(out_path, **self.save_keywords(vdoc))
        finally:
            out.close()

    def _append_to_origin(self, vdoc: VirtualDocument, out_path: str) -> None:
        """Seed ``out_path`` with the origin's own bytes, apply the edits, append what changed.

        **Why the seed rather than an in-place write.** MuPDF appends only to the file a document
        was opened *from* (``ValueError: incremental needs original file`` for anything opened from
        a stream, or saved to a second path), and this project never writes to the file it opened:
        both surfaces materialise into a fresh temp beside the target and rename it in —
        ``MainWindow._write_to`` and ``mcp_bridge/transforms._write``, deliberately the same shape
        (M38.5), with the bridge additionally refusing to write over its input at all. Writing the
        origin's bytes into that temp first satisfies MuPDF without disturbing any of it: the
        rename is still atomic, the bridge still never touches its input, and ``Save``'s in-place
        semantics are unchanged. Measured on a 9 MB document, the seed costs 18 ms of the save's
        100.

        The output is therefore the source file with a new revision on the end: every byte of the
        original is still where it was, every page that was not marked is byte-identical **by
        construction** rather than by inspection, and only the pages that gained a mark appear in
        the appended section. This is the ordinary PDF incremental update — what Edge writes for
        the same edit, and what :meth:`appends` exists to be sure is honest.

        ``_apply_page_edits`` is the same pass both other routes run, unchanged: the predicate has
        already refused everything in it that would do more than add an annotation, and it is
        measurably scoped — a page with no mark of ours is never touched, so a document opened and
        saved with no edits at all appends **0 bytes** and comes back byte-identical.
        """
        Path(out_path).write_bytes(vdoc.origin_bytes())
        out = fitz.open(out_path)
        try:
            self._apply_page_edits(out, vdoc)
            out.save(out_path, **append_options())
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
            # ...and only when the user actually edited the metadata (M114). `apply_metadata`'s
            # untouched branch copies the origin's Info dict and XMP packet onto the output — a pass
            # written for the *graft*, where `insert_pdf` copies neither store and they would
            # otherwise vanish. This route starts from a copy of the origin, which already carries
            # both: measured, `fresh_source()`'s output has the Info title and a byte-identical XMP
            # packet before this runs, where `insert_pdf` into a fresh document has an empty title
            # and no packet at all. So here it rewrites what is already there — a no-op in meaning
            # and not in bytes, since PyMuPDF then marks both objects changed and re-serialises them.
            # Free while the save rewrites everything anyway; on a document with a 3,093-byte XMP
            # packet it turns a 901 B incremental append into 4,249 B, which is what makes it worth
            # skipping now rather than when incremental writing arrives.
            if vdoc.metadata_override is not None:
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
