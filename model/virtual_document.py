"""Virtual-document / edit-list model (lossless).

PLAN.md, "Key design idea": never mutate the on-disk PDF while editing. A ``VirtualDocument``
holds an ordered list of :class:`PageRef` (``source_id`` + ``source_page_index`` +
``rotation_override``) plus a registry of open read-only source ``fitz.Document`` objects. Every
edit — reorder, delete, merge/insert, rotate, cross-window paste — is a cheap list edit on
``ordered``. Nothing is written until :mod:`model.edit_engine` materialises on Save.

This module is GUI-free and headless-testable (no Qt). The undo/redo wiring lives in
:mod:`model.edit_commands`, which snapshots/restores this object's state.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

import pymupdf as fitz

from util.paths import normalize_path

# A snapshot is the full mutable state captured for undo: the ordered list + dirty flag.
# PageRefs are frozen, so a shallow tuple copy is a safe, cheap point-in-time snapshot.
State = tuple

# Raster image formats we import as a one-page PDF page (M35). PyMuPDF opens each as a 1-page
# document and converts it to PDF, after which it is just another read-only source.
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff"})


class PasswordRequired(Exception):
    """An encrypted source needs a password we don't have — no provider, or the user cancelled.

    Raised out of :meth:`VirtualDocument.open_source` / :meth:`~VirtualDocument.from_path`; the GUI
    catches it and simply doesn't open a window (cancelling the prompt is a normal outcome, not an
    error to surface)."""


def _authenticate_and_decrypt(
    doc: "fitz.Document", path: str, password_provider
) -> "tuple[fitz.Document, str, str, int]":
    """Authenticate an encrypted ``doc`` (prompting via ``password_provider``), then return a fresh
    **decrypted** in-memory copy plus the password that worked (M32; M54 records the password) and
    the two facts the decrypt is about to erase: the algorithm label and the permission flags.

    ``password_provider(path, retry)`` returns a password string, or ``None`` to cancel. The model
    loops on a wrong password (re-calling with ``retry=True``) until it succeeds or the user
    cancels. On success the document is re-serialised with encryption removed, so nothing downstream
    — fresh source copies, materialise, render — ever needs the password again; the password is
    returned so a save can **carry the encryption through** (M54 — it supersedes M32's
    save-unencrypted deferral). Raises :class:`PasswordRequired` when no password is available.

    NB: ``authenticate`` returns a truthy bitfield on success (``needs_pass`` stays set even then),
    and the decrypt ``tobytes`` passes no ``garbage``/``deflate`` — garbage-collecting an AES doc
    mid-decrypt corrupts its content streams; the materialise save cleans the decrypted output later.

    **Both facts are read between the authenticate and the decrypt, and that window is the whole
    reason they are read here at all.** Before authenticating, ``metadata`` is ``None`` and
    ``permissions`` is 0 — the document has not been unlocked. After the ``tobytes``, the copy is a
    plain unencrypted PDF that grants everything. Read a moment too early or too late and a file
    that forbids copying and modification reports the opposite; that was M94's defect, a
    user-password document round-tripping through Save to *fully permitted* (measured: -1052 in,
    -4 out) while M93 had already fixed the owner-password case that never gets decrypted here.
    """
    if password_provider is None:
        doc.close()
        raise PasswordRequired(path)
    retry = False
    while True:
        password = password_provider(path, retry)
        if password is None:  # user cancelled the prompt
            doc.close()
            raise PasswordRequired(path)
        if doc.authenticate(password):
            break
        retry = True
    encryption = (doc.metadata or {}).get("encryption") or ""
    restrictions = _restrictions_of(doc)
    decrypted = doc.tobytes(encryption=fitz.PDF_ENCRYPT_NONE)
    doc.close()
    return fitz.open(stream=decrypted, filetype="pdf"), password, encryption, restrictions


_ALL_PERMISSIONS = (
    fitz.PDF_PERM_PRINT | fitz.PDF_PERM_MODIFY | fitz.PDF_PERM_COPY | fitz.PDF_PERM_ANNOTATE
    | fitz.PDF_PERM_FORM | fitz.PDF_PERM_ACCESSIBILITY | fitz.PDF_PERM_ASSEMBLE
    | fitz.PDF_PERM_PRINT_HQ
)


def _restrictions_of(doc: "fitz.Document") -> int:
    """A document's advisory permission flags, normalised to the model's ``-1 = unrestricted``.

    PyMuPDF answers ``-4`` for a document that restricts nothing — the raw ``/P`` value with only
    the two reserved low bits clear — while this model has always used ``-1`` as its
    "everything allowed" sentinel, and :func:`~model.edit_engine._encryption_args` keys on it to
    decide whether the owner password may equal the user password. Passing ``-4`` straight through
    would quietly retire that rule for every ordinary document, so anything granting the full set
    normalises back to ``-1`` and only a genuinely restricted file keeps its exact bits.
    """
    permissions = doc.permissions
    return -1 if permissions & _ALL_PERMISSIONS == _ALL_PERMISSIONS else permissions


@dataclass(frozen=True, slots=True)
class PageRef:
    """A reference to one source page. Immutable so snapshots are cheap and safe.

    ``rotation_override`` is an **absolute** final angle (0/90/180/270) or ``None`` to inherit
    the source page's own rotation. Rotating produces a *new* PageRef (see ``with_rotation``).

    ``annotations`` is an immutable tuple of page-edit descriptors (``model.page_edits`` —
    highlight / text-box / redaction, v0.4.0) that live **on the page**: because they ride the
    PageRef, they follow the page through reorder / delete / cross-window copy, and are snapshotted
    with ``ordered[]`` for undo/redo. They are applied to the output page at materialize (the
    highlight/text-box overlays non-destructively; a redaction destructively removes its region).

    ``crop_override`` (M48) is an **absolute** visible rect ``(x0, y0, x1, y1)`` in the page's
    unrotated content coordinates (the frame word boxes / annotations live in — the source
    CropBox frame, top-left origin), or ``None`` to inherit the source page's own CropBox. Like
    the rotation override it rides the PageRef (follows reorder / copy, snapshots for undo) and
    is applied at materialize via ``set_cropbox`` — the crop *hides* the rest of the page, it
    does not remove content (Redact does that). A reset ("show the full MediaBox") is an
    explicit override too, which may extend beyond the frame origin (negative coords) when the
    source arrived pre-cropped.
    """

    source_id: str
    source_page_index: int
    rotation_override: int | None = None
    annotations: tuple = ()
    crop_override: tuple | None = None

    def with_rotation(self, angle: int | None) -> "PageRef":
        if angle is not None:
            angle %= 360
            if angle % 90 != 0:
                raise ValueError(f"rotation must be a multiple of 90, got {angle}")
        return replace(self, rotation_override=angle)

    def with_annotations(self, annotations: tuple) -> "PageRef":
        return replace(self, annotations=tuple(annotations))

    def with_crop(self, rect: "tuple | None") -> "PageRef":
        if rect is not None:
            x0, y0, x1, y1 = (float(v) for v in rect)
            if x1 <= x0 or y1 <= y0:
                raise ValueError(f"crop rect must have positive area, got {rect}")
            rect = (x0, y0, x1, y1)
        return replace(self, crop_override=rect)


class VirtualDocument:
    """An ordered list of page references over a set of read-only source documents."""

    def __init__(self) -> None:
        self.sources: dict[str, fitz.Document] = {}
        self.ordered: list[PageRef] = []
        self.path: str | None = None
        self.dirty: bool = False
        # AcroForm field values the user has entered (field name -> value), applied to the output
        # at materialise (model.page_edits). Document-level: AcroForm fields are name-identified
        # across the whole doc. Part of the snapshot so undo/redo restores fills too.
        self._form_values: dict[str, object] = {}
        # The document this virtual doc was opened from. Its outline is the one we rebuild on
        # save (merged-in sources contribute no outline, matching insert_pdf's behaviour).
        self.origin_source_id: str | None = None
        self._origin_toc: list = []
        # Document metadata (M53): the origin's two stores as read (Info dict + raw XMP packet),
        # and the user's override — None = untouched (carry the origin's through at materialise),
        # a dict = edited values (both stores rewritten consistently), {} = removed (both stores
        # cleared). Like _form_values it is document-level and part of the snapshot, so metadata
        # edits ride undo/redo. Merged-in sources contribute no metadata, matching the outline.
        self._origin_info: dict = {}
        self._origin_xmp: str = ""
        self._metadata_override: dict | None = None
        # Document encryption (M54) — a save-path capability: the password a Save applies
        # (AES-256), or None to save unencrypted, plus the advisory permission flags (-1 = all
        # allowed). Held in memory only, never persisted anywhere but the encrypted output
        # itself. Seeded by from_path for an encrypted original (carry-through — supersedes
        # M32's save-unencrypted deferral); _source_passwords records what opened each source.
        self._password: str | None = None
        self._permissions: int = -1
        # Has the user made an explicit encryption decision this session? It is the difference
        # between "no password set" and "password deliberately removed", which are the same
        # ``_password is None`` but must save differently: the first keeps whatever protection the
        # document arrived with, the second is a request to drop it.
        self._encryption_staged: bool = False
        self._source_passwords: dict[str, str] = {}
        # What each source file said about its own protection *on disk*, captured at open (M94).
        # Separate from the two fields above because they answer different questions: those are
        # what the next Save will apply, these are what the file that was opened carries. They
        # cannot be recovered later — a source that needed a password is stored decrypted — and
        # without them a user-password document reports, and saves as, fully permitted.
        self._source_encryption: dict[str, str] = {}    # source id -> algorithm label, "" = none
        self._source_restrictions: dict[str, int] = {}  # source id -> flags (-1 = all allowed)
        # Cache: does a registered source carry baked KlarPDF annotations? Keyed by source id;
        # source bytes are immutable, so this never changes for a given source (cleared only when
        # sources are reset in reload_from_file). Lets the viewer / thumbnails keep the fast
        # straight-from-source render for clean documents and switch to the edits-applied copy
        # (our marks stripped, redrawn editable) only for documents that actually have our marks.
        self._source_has_ours: dict[str, bool] = {}
        # The exact bytes each PDF source's file held on disk when it was opened (M116), and the
        # KlarPDF marks each of its pages arrived carrying. Both are what an **appending** save
        # needs and neither can be recovered later: `tobytes()` re-serialises rather than handing
        # back the original, and the marks in `ordered` are the *edited* set by the time a save
        # asks. The bytes cost nothing to keep — `fitz.open(stream=…)` holds a reference to the
        # same object, so this dict adds a pointer, not a copy. See :meth:`origin_bytes` and
        # :meth:`edits_are_additive`.
        self._source_bytes: dict[str, bytes] = {}
        self._source_marks: dict[str, tuple[tuple, ...]] = {}
        # How to obtain a password for an encrypted source (set by from_path). Stored so a later
        # reload_from_file (Revert of an encrypted original) can re-prompt the same way. GUI-free:
        # callers inject a callable (the GUI's password dialog; a lambda in tests). None = no prompt.
        self._password_provider = None

    # ---- construction / sources -------------------------------------------------

    @classmethod
    def from_path(cls, path: str, password_provider=None) -> "VirtualDocument":
        """Open ``path`` as the origin document and seed ``ordered`` with all its pages.

        ``password_provider`` (``(path, retry) -> str | None``) is consulted if the document is
        encrypted; it raises :class:`PasswordRequired` if no password is supplied (M32)."""
        vd = cls()
        vd._password_provider = password_provider
        source_id = vd.open_source(path, password_provider)
        vd.origin_source_id = source_id
        vd.path = path
        vd._origin_toc = vd.sources[source_id].get_toc(simple=False)
        vd._capture_origin_metadata(source_id)
        # Carry-through (M54): a document opened with a password saves back with that password
        # unless the user changes/removes it. None for an unencrypted original.
        vd._password = vd._source_passwords.get(source_id)
        # …and so do its **restrictions**. Left at the -1 default, "allow everything" was the
        # answer to a question nobody had asked: the password dialog pre-ticks its boxes from
        # `vdoc.permissions` (`ui/encrypt_dialog.py`), so every box arrived ticked whatever the
        # document actually said, and setting a password on a restricted file silently granted
        # copying, modification and assembly. Seeding from the origin makes the dialog show what
        # the document restricts and makes accepting it unchanged a no-op (M93). Read from what
        # was captured at open rather than from the stored source, which is the decrypted copy for
        # a user-password document and grants everything (M94).
        vd._permissions = vd._source_restrictions.get(source_id, -1)
        vd.ordered = vd._seed_ordered(source_id)
        vd.dirty = False
        return vd

    def _capture_origin_metadata(self, source_id: str) -> None:
        """Read the origin's two metadata stores (M53) so materialise can carry them through —
        ``insert_pdf`` copies neither the Info dict nor the XMP packet."""
        from model.metadata import read_info

        src = self.sources[source_id]
        self._origin_info = read_info(src)
        self._origin_xmp = src.get_xml_metadata() or ""

    def _seed_ordered(self, source_id: str) -> list[PageRef]:
        """Build the initial page list for a freshly-opened origin source, seeding each page with
        the KlarPDF annotations baked into it (M31 round-trip read-back) so saved highlights /
        text-boxes reopen as editable model descriptors. Used by ``from_path`` + ``reload_from_file``.
        """
        from model.page_edits import read_klarpdf_annotations

        src = self.sources[source_id]
        refs: list[PageRef] = []
        had_ours = False
        for i in range(src.page_count):
            annotations = read_klarpdf_annotations(src[i])
            had_ours = had_ours or bool(annotations)
            refs.append(PageRef(source_id, i, annotations=annotations))
        # Captured from the same read-back scan, so no second pass over the source pages.
        self._source_has_ours[source_id] = had_ours
        # …and so is the per-page baseline an appending save compares against (M116): what these
        # pages *arrived* carrying, which is the only way to tell "a mark was added" from "a mark
        # was edited or removed" once `ordered` has been through an editing session.
        self._source_marks[source_id] = tuple(ref.annotations for ref in refs)
        return refs

    def source_has_klarpdf_annotations(self, source_id: str) -> bool:
        """Whether ``source_id``'s pages carry baked KlarPDF annotations (cached).

        Pre-populated by :meth:`_seed_ordered` for the origin; computed lazily on miss for sources
        registered later (a cross-window paste), so a pasted page that brought our marks along still
        renders from the stripped copy rather than double-drawing the baked originals.
        """
        cached = self._source_has_ours.get(source_id)
        if cached is None:
            from model.page_edits import page_has_klarpdf_annotations

            src = self.sources[source_id]
            cached = any(page_has_klarpdf_annotations(src[i]) for i in range(src.page_count))
            self._source_has_ours[source_id] = cached
        return cached

    def has_baked_klarpdf_annotations(self) -> bool:
        """True if any registered source carries baked KlarPDF annotations (doc-level)."""
        return any(self.source_has_klarpdf_annotations(sid) for sid in self.sources)

    def open_source(self, path: str, password_provider=None) -> str:
        """Open and register a source by path (idempotent). Returns its source id.

        Opened from an **in-memory copy** of the file, never a live file handle: on Windows an open
        handle blocks the atomic ``os.replace`` used by in-place Save, so holding the file open
        would make saving over the currently-open document fail with "access denied".

        If the document is **encrypted** (``needs_pass``), it is authenticated via
        ``password_provider`` and stored **decrypted** (M32 — see :func:`_authenticate_and_decrypt`);
        :class:`PasswordRequired` propagates if no password is available. What the file on disk
        said about its own protection is recorded first (M94), because the stored copy no longer
        knows: see :meth:`source_encryption`.
        """
        source_id = normalize_path(path)
        if source_id not in self.sources:
            data = Path(path).read_bytes()
            # Kept, not re-read at save time (M116): an appending save writes these bytes out as
            # its starting point, and they have to be the ones this source was *opened* from. The
            # file on disk can have moved on since — an external editor, a sync client — and
            # appending our edits to a document nobody has looked at is how a save quietly ships
            # somebody else's pages. `fitz.open(stream=…)` holds this same object, so keeping it
            # costs nothing.
            self._source_bytes[source_id] = data
            doc = fitz.open(stream=data, filetype="pdf")
            if doc.needs_pass:
                doc, password, encryption, restrictions = _authenticate_and_decrypt(
                    doc, path, password_provider
                )
                self._source_passwords[source_id] = password
            else:
                # Opened without a password — but that does not mean unprotected. An
                # owner-password document opens freely and still restricts copying, and this copy
                # is still the encrypted one, so both facts read straight off it.
                encryption = (doc.metadata or {}).get("encryption") or ""
                restrictions = _restrictions_of(doc)
            self._source_encryption[source_id] = encryption
            self._source_restrictions[source_id] = restrictions
            self.sources[source_id] = doc
        return source_id

    def open_blank_source(self, width: float, height: float) -> str:
        """Register a one-page **blank** in-memory source of ``width`` × ``height`` points (M51
        Insert ▸ Blank Page) and return its source id.

        Built via ``fitz.Document.new_page`` — an empty page, no content stream to inherit. The id
        is synthetic (``blank:WxH`` — never a path), and one source is shared by every blank page
        of the same size, so the insert itself stays a plain ``PageRef`` list edit like any other:
        it rides the undo stack, materialises object-level, and travels cross-window for free.
        Idempotent per size.
        """
        source_id = f"blank:{width:g}x{height:g}"
        if source_id not in self.sources:
            doc = fitz.open()
            doc.new_page(width=width, height=height)
            self.sources[source_id] = doc
        return source_id

    def open_image_source(self, path: str) -> str:
        """Open a raster image as a **one-page PDF** source (M35 image import) and register it.

        PyMuPDF opens the image as a 1-page document; ``convert_to_pdf()`` renders it to PDF bytes,
        which we register exactly like a PDF source (keyed by the image's normalized path) — so the
        image is just another read-only source and flows through reorder / materialize / export
        unchanged. Like :meth:`open_source`, the registered source is in-memory bytes, never a live
        file handle, so the image file isn't held open (no Save-time lock). Idempotent.
        """
        source_id = normalize_path(path)
        if source_id not in self.sources:
            with fitz.open(path) as image:  # opens the raster image as a 1-page document
                pdf_bytes = image.convert_to_pdf()
            self.sources[source_id] = fitz.open(stream=pdf_bytes, filetype="pdf")
        return source_id

    def register_source(self, source_id: str, doc: fitz.Document) -> None:
        """Register an already-open source document (e.g. shared from another window)."""
        self.sources.setdefault(source_id, doc)

    def fresh_source(self, source_id: str) -> fitz.Document:
        """A fresh, independent in-memory copy of a registered source.

        Reusing one ``fitz`` source object across multiple ``insert_pdf`` calls drops its widgets
        after the first call (a PyMuPDF graft-state quirk), which would silently strip form fields
        from a second save and from re-rendered filled pages. A fresh copy resets that state.

        ``PDF_ENCRYPT_KEEP`` because ``tobytes()`` defaults to writing the copy **unencrypted**.
        That did not matter while the copy was only ever a donor for ``insert_pdf``, but the copy is
        now also the *starting point* of an unchanged-page-set save (see
        :meth:`~model.edit_engine.PyMuPDFEngine._build_output`), and a decrypted starting point can
        only ever produce a decrypted output. Measured on an owner-password form: without the flag
        the copy came back ``permissions=-4``, encryption ``None``; with it, ``-1052`` and
        AES-128, matching the source.
        """
        return fitz.open(
            stream=self.sources[source_id].tobytes(encryption=fitz.PDF_ENCRYPT_KEEP),
            filetype="pdf",
        )

    def page_set_unchanged(self) -> bool:
        """Is the output exactly the origin's pages, all of them, in their original order?

        True when nothing structural has happened — no reorder, no delete, no insert, no page from
        a second document. Per-page edits (rotation, crop, annotations, redactions, form fills) do
        **not** affect this: they are applied to a page, whichever document that page ends up in.

        This is the question that decides whether a save has to *rebuild* the document or can edit a
        copy of it. It matters because everything a PDF keeps at the document level rather than on a
        page — the accessibility structure tree, ``/MarkInfo``, Reader-Extensions ``/Perms``, the
        ``/Names`` tree, encryption — is invisible to ``insert_pdf``, which copies pages. Those
        cannot be reconstructed afterwards the way the outline and the metadata are (M33, M53); the
        only way to keep them is not to throw them away.
        """
        origin = self.origin_source_id
        if origin is None or origin not in self.sources:
            return False
        if len(self.ordered) != self.sources[origin].page_count:
            return False
        return all(ref.source_id == origin and ref.source_page_index == i
                   for i, ref in enumerate(self.ordered))

    def edits_are_additive(self) -> bool:
        """Is every edit here the **addition** of a mark to a page the document already had?

        The finer question :meth:`page_set_unchanged` is too coarse to answer, and the one an
        *appending* save turns on (M116). A PDF can be updated by leaving the file alone and
        writing the changed objects onto the end of it, which is what Microsoft Edge does for one
        highlight on a 572-page prospectus: **2,680 bytes appended, the first 9,015,879 untouched**.
        The format offers that for exactly this case — something added, nothing disturbed — and the
        job of this method is to be sure that is what happened.

        **True demands all of the following**, and anything unrecognised answers False:

        * the page set is unchanged (:meth:`page_set_unchanged`) — a reorder, a deletion or a page
          from a second document has to rebuild the document, and there is nothing to append *to*;
        * every mark on every page is an additive kind
          (:data:`~model.page_edits.ADDITIVE_MARK_TYPES` — a whitelist, so tomorrow's descriptor is
          refused until somebody classifies it);
        * every mark the pages **arrived** carrying is still there. Appending cannot take something
          away: the previous revision stays in the file, so a removed mark is still recoverable from
          it and a save that removed one would have lied. Editing a mark is removing one, by the
          same argument — the old wording of a text box would still be in there;
        * no page is rotated or cropped, no form field is filled, the metadata stores are untouched,
          and the user has not staged an encryption change. Each of those rewrites something the
          file already had rather than adding to it.

        Note what this is *not* asked about: whether the origin's pages already carry KlarPDF marks.
        They may — a document annotated last week, opened again, and given one more highlight is
        still purely additive, and refusing that case would send the commonest markup session
        straight back to the full rewrite.

        A :meth:`subset` view answers False by construction: it shares the sources but not the
        per-page baseline, so there is nothing to prove the claim against. That is the right answer
        anyway — an extract is a new document.
        """
        from collections import Counter

        from model.page_edits import is_additive_mark

        if not self.page_set_unchanged():
            return False
        if self._metadata_override is not None or self._form_values or self._encryption_staged:
            return False
        arrived_with = self._source_marks.get(self.origin_source_id)
        if arrived_with is None or len(arrived_with) != len(self.ordered):
            return False
        for ref, arrived in zip(self.ordered, arrived_with):
            if ref.rotation_override is not None or ref.crop_override is not None:
                return False
            if not all(is_additive_mark(mark) for mark in ref.annotations):
                return False
            if not Counter(arrived) <= Counter(ref.annotations):
                return False
        return True

    def origin_bytes(self) -> "bytes | None":
        """The origin's file exactly as it was read from disk — the seed an appending save writes
        out before appending to it (M116). ``None`` when there is no such thing to hand back.

        The bytes are the ones captured in :meth:`open_source`, not a re-read: the file may have
        moved on since (an external editor, a sync client), and appending this session's edits to
        pages nobody has seen is how a save quietly ships somebody else's document.

        ``None`` for a document that needed a **password** to open, and that is the load-bearing
        case rather than a formality. Such a source is stored *decrypted* (M32), so the file's
        bytes and the model's source are two different documents; and a save re-encrypts from the
        decrypted copy (M54), which an append cannot do — MuPDF refuses an incremental write that
        changes encryption at all. Both facts point the same way, so the honest contract for this
        method is "the file the origin source is a view of", which for that document does not
        exist. An owner-password document is the opposite case and works: it opens without a
        password, is never decrypted, and its encryption rides through untouched.
        """
        origin = self.origin_source_id
        if origin is None or origin in self._source_passwords:
            return None
        return self._source_bytes.get(origin)

    def origin_needed_repair(self) -> bool:
        """Did MuPDF have to rebuild the origin's cross-reference table to open it?

        A file that arrived damaged cannot be appended to — measured, MuPDF refuses outright
        (*"Can't do incremental writes on a repaired file"*), and rightly: the offsets an
        incremental update chains onto are the ones it just had to guess. Such a document takes the
        full rewrite, which is also what fixes it.
        """
        origin = self.origin_source_id
        if origin is None or origin not in self.sources:
            return False
        return bool(self.sources[origin].is_repaired)

    def origin_carries_encryption(self) -> bool:
        """Is the origin source still an encrypted document in memory?

        The discriminator for whether a save should preserve encryption it was never given a
        password for. A document that *needed* a password was decrypted at open and its password
        recorded (M54), so it no longer reports encryption here and is re-encrypted from
        ``password`` instead — and a password the user has since removed stays removed. A document
        that opened freely but restricts permissions (an owner password only) was never decrypted,
        still reports its encryption, and is the case M54 does not cover.
        """
        if self._encryption_staged:
            return False        # the user has said what they want; do not second-guess it
        origin = self.origin_source_id
        if origin is None or origin not in self.sources:
            return False
        return bool(self.sources[origin].metadata.get("encryption"))

    def source_encryption(self, source_id: str) -> str | None:
        """The encryption the source **file** carries, e.g. ``'Standard V4 R4 128-bit AES'``.

        ``None`` for an unprotected file. Distinct from :meth:`origin_carries_encryption`, which
        asks a save-path question ("is the copy in memory still encrypted?") and is therefore
        False for a user-password document — decrypted at open, saved back from ``password``. This
        one answers what the file on disk was, for both kinds of protection, which is what a
        *reader* is asking. Captured at open; see :meth:`open_source`.
        """
        return self._source_encryption.get(source_id) or None

    def origin_encryption(self) -> str | None:
        """:meth:`source_encryption` for the document this was opened from."""
        origin = self.origin_source_id
        return None if origin is None else self.source_encryption(origin)

    # ---- queries ----------------------------------------------------------------

    @property
    def page_count(self) -> int:
        return len(self.ordered)

    def ref_at(self, index: int) -> PageRef:
        return self.ordered[index]

    def build_index_map(self) -> dict[int, int]:
        """Map origin page index (0-based) -> output index (0-based) for surviving pages.

        Only pages from the origin source appear (others carry no outline). If a duplicated
        origin page survives more than once, the first occurrence wins — outline targets are
        single-valued.
        """
        index_map: dict[int, int] = {}
        for new_index, ref in enumerate(self.ordered):
            if ref.source_id == self.origin_source_id:
                index_map.setdefault(ref.source_page_index, new_index)
        return index_map

    def remapped_toc(self) -> list:
        from model.toc_remap import remap_toc

        return remap_toc(self._origin_toc, self.build_index_map())

    def subset(self, indices: Iterable[int]) -> "VirtualDocument":
        """A throwaway extract view holding only the pages at ``indices``, in document order (M51
        Export ▸ Selected Pages as PDF…).

        Shares this document's live source objects (so **never** ``close()`` the subset) and copies
        the refs + form fills; the origin outline rides along so ``remapped_toc`` keeps the
        bookmarks whose target pages were extracted and drops the rest. Materialising the subset is
        the ordinary lossless path — object-level page copy, per-page edits, TOC + internal-link
        remap — applied to just these pages.
        """
        sub = VirtualDocument()
        sub.sources = self.sources
        sub.ordered = [self.ordered[i] for i in sorted(set(indices))]
        sub.origin_source_id = self.origin_source_id
        sub._origin_toc = self._origin_toc
        sub._form_values = dict(self._form_values)
        # The document-level metadata state rides along too (M53): the extract carries the
        # origin's stores — or the user's pending edit/removal — like a Save would.
        sub._origin_info = dict(self._origin_info)
        sub._origin_xmp = self._origin_xmp
        sub._metadata_override = self.metadata_override
        return sub

    def page_visible_size(self, index: int) -> tuple:
        """The on-screen ``(width, height)`` of the page at ``index`` — its (cropped) content
        frame with the effective rotation applied. Sizes an inserted blank page to match the page
        it follows (M51)."""
        ref = self.ordered[index]
        if ref.crop_override is not None:
            w = ref.crop_override[2] - ref.crop_override[0]
            h = ref.crop_override[3] - ref.crop_override[1]
        else:
            _x0, _y0, w, h = self.page_base_rect(index)
        native = self.sources[ref.source_id][ref.source_page_index].rotation
        rotation = native if ref.rotation_override is None else ref.rotation_override
        return (h, w) if rotation % 180 else (w, h)

    def has_outline(self) -> bool:
        """Whether the **origin** document carries an outline (M45 — decides if the sidebar grows an
        Outline tab). Keyed to the origin, not the live remap: deleting every bookmarked page leaves
        the tab in place showing an empty tree (undo brings the entries back), rather than tearing
        the switcher down mid-session."""
        return bool(self._origin_toc)

    # ---- snapshot / restore (used by edit_commands for undo/redo) ---------------

    def snapshot(self) -> State:
        override = self._metadata_override
        return (
            tuple(self.ordered),
            dict(self._form_values),
            None if override is None else dict(override),
            (self._password, self._permissions, self._encryption_staged),
            self.dirty,
        )

    def restore(self, state: State) -> None:
        ordered, form_values, metadata_override, encryption, dirty = state
        self.ordered = list(ordered)
        self._form_values = dict(form_values)
        self._metadata_override = None if metadata_override is None else dict(metadata_override)
        self._password, self._permissions, self._encryption_staged = encryption
        self.dirty = dirty

    # ---- list edits (each marks the document dirty) -----------------------------

    def move_page(self, from_index: int, to_index: int) -> None:
        """Move the page at ``from_index`` so it lands at ``to_index`` in the new order."""
        ref = self.ordered.pop(from_index)
        self.ordered.insert(to_index, ref)
        self.dirty = True

    def move_pages(self, src_indices: Iterable[int], before_index: int) -> None:
        """Move pages ``src_indices`` so they sit just before ``before_index`` in the new order.

        ``before_index`` is a position in the *current* list (0..page_count). Handles
        non-contiguous multi-selection; moved pages keep their relative order.
        """
        src = sorted(set(src_indices))
        if not src:
            return
        refs = [self.ordered[i] for i in src]
        shift = sum(1 for i in src if i < before_index)
        remaining = [r for i, r in enumerate(self.ordered) if i not in set(src)]
        pos = max(0, min(before_index - shift, len(remaining)))
        remaining[pos:pos] = refs
        self.ordered = remaining
        self.dirty = True

    def delete_page(self, index: int) -> None:
        del self.ordered[index]
        self.dirty = True

    def delete_pages(self, indices: Iterable[int]) -> None:
        for i in sorted(set(indices), reverse=True):
            del self.ordered[i]
        self.dirty = True

    def insert_pages(self, at_index: int, refs: Iterable[PageRef]) -> None:
        """Splice ``refs`` into ``ordered`` at ``at_index`` (merge / insert / paste)."""
        refs = list(refs)
        for r in refs:
            if r.source_id not in self.sources:
                raise KeyError(f"source {r.source_id!r} not registered; register it first")
        self.ordered[at_index:at_index] = refs
        self.dirty = True

    def append_pages(self, refs: Iterable[PageRef]) -> None:
        self.insert_pages(self.page_count, refs)

    def set_rotation(self, index: int, angle: int | None) -> None:
        """Set the **absolute** rotation override for the page at ``index``."""
        self.ordered[index] = self.ordered[index].with_rotation(angle)
        self.dirty = True

    def rotate_pages(self, indices: Iterable[int], delta: int) -> None:
        """Rotate each page in ``indices`` by ``delta`` degrees from its current angle.

        The current angle is the page's existing override, or — when it has none — its source
        page's own ``/Rotate``; the result is stored as a new **absolute** override. ``delta`` is
        a relative quarter-turn (±90, 180, …); ``with_rotation`` enforces the multiple-of-90 rule.
        """
        for i in indices:
            ref = self.ordered[i]
            native = self.sources[ref.source_id][ref.source_page_index].rotation
            current = native if ref.rotation_override is None else ref.rotation_override
            self.ordered[i] = ref.with_rotation((current + delta) % 360)
        self.dirty = True

    # ---- crop (M48; rides the PageRef like rotation, applied at materialise) ----

    def page_base_rect(self, index: int) -> tuple:
        """The page's full visible frame ``(0, 0, w, h)`` in unrotated content coordinates — the
        space word boxes, annotations, and ``crop_override`` live in (the source CropBox frame)."""
        ref = self.ordered[index]
        cropbox = self.sources[ref.source_id][ref.source_page_index].cropbox
        return (0.0, 0.0, float(cropbox.width), float(cropbox.height))

    def set_crop(self, indices: Iterable[int], rect: "tuple | None") -> None:
        """Set (or clear, with ``None``) the absolute crop on each page, clamped to that page's
        frame. A page where the clamped rect has no area (the drag lies wholly off that page's
        frame — possible when one rect is applied to differently-sized pages) is left unchanged."""
        for i in indices:
            clamped = rect
            if rect is not None:
                base = self.page_base_rect(i)
                clamped = (max(rect[0], 0.0), max(rect[1], 0.0),
                           min(rect[2], base[2]), min(rect[3], base[3]))
                if clamped[2] <= clamped[0] or clamped[3] <= clamped[1]:
                    continue
            self.ordered[i] = self.ordered[i].with_crop(clamped)
        self.dirty = True

    def reset_crop(self, indices: Iterable[int]) -> None:
        """Restore each page to its **full MediaBox** — undoes our override *and* un-hides a crop
        the source arrived with (the MediaBox expressed in content coordinates reaches beyond the
        frame origin for a pre-cropped source, hence the possibly-negative rect)."""
        for i in indices:
            ref = self.ordered[i]
            page = self.sources[ref.source_id][ref.source_page_index]
            cx, cy = page.cropbox_position
            mediabox = page.mediabox
            if cx or cy or page.cropbox.width != mediabox.width or page.cropbox.height != mediabox.height:
                full = (-cx, -cy, mediabox.width - cx, mediabox.height - cy)
            else:
                full = None  # source is already full-page — clearing the override is the reset
            self.ordered[i] = self.ordered[i].with_crop(full)
        self.dirty = True

    def page_is_cropped(self, index: int) -> bool:
        """Whether the page has an explicit crop override or a source CropBox smaller than its
        MediaBox — i.e. whether Remove Crop has anything to act on. (A reset override counts;
        resetting again is harmlessly idempotent.)"""
        ref = self.ordered[index]
        if ref.crop_override is not None:
            return True
        page = self.sources[ref.source_id][ref.source_page_index]
        return tuple(page.cropbox) != tuple(page.mediabox)

    # ---- document metadata (M53; document-level, applied at materialise) --------

    @property
    def origin_metadata(self) -> dict:
        """The origin file's Info-dict fields as read (what an untouched save carries through)."""
        return dict(self._origin_info)

    @property
    def origin_xmp(self) -> str:
        """The origin file's raw XMP packet as read (``""`` when it has none)."""
        return self._origin_xmp

    @property
    def metadata_override(self) -> "dict | None":
        """The user's metadata verb: ``None`` untouched, a dict = edited, ``{}`` = removed."""
        override = self._metadata_override
        return None if override is None else dict(override)

    def effective_metadata(self) -> dict:
        """What the Properties dialog shows and a Save writes — the override when one is set,
        else the origin's fields."""
        if self._metadata_override is not None:
            return dict(self._metadata_override)
        return dict(self._origin_info)

    def set_metadata_override(self, values: "dict | None") -> None:
        """Set the metadata verb: a dict of Info fields (edit), ``{}`` (remove all — both stores
        cleared at materialise), or ``None`` (revert to the origin's)."""
        self._metadata_override = None if values is None else dict(values)
        self.dirty = True

    def metadata_is_removed(self) -> bool:
        return self._metadata_override == {}

    # ---- document encryption (M54; a save-path capability) ----------------------

    @property
    def password(self) -> "str | None":
        """The password a Save applies (AES-256), or ``None`` to save unencrypted. In memory
        only — never persisted anywhere but the encrypted output itself."""
        return self._password

    @property
    def permissions(self) -> int:
        """The advisory permission flags a Save writes (-1 = everything allowed). Advisory:
        honored by most viewers, not cryptographically enforced — only the password is.

        Seeded from the origin at open, so a document that arrived restricted stays restricted
        unless the user says otherwise. PyMuPDF reports these in the same encoding ``save`` takes,
        so the value round-trips exactly (verified: -1052 in, -1052 out)."""
        return self._permissions

    def set_encryption(self, password: "str | None", permissions: int = -1) -> None:
        """Set / change / remove the password the next Save applies (+ advisory flags).

        Removing the password (``None``) resets the flags too: PDF permission bits live inside
        the encryption dictionary, so restrictions without a password don't exist."""
        self._password = password
        self._permissions = -1 if password is None else int(permissions)
        self._encryption_staged = True
        self.dirty = True

    # ---- form field values (document-level; applied at materialise) -------------

    @property
    def form_values(self) -> dict[str, object]:
        """Current AcroForm fills (field name -> value)."""
        return dict(self._form_values)

    def field_value(self, name: str):
        """The user-entered value for ``name``, or ``None`` if unset."""
        return self._form_values.get(name)

    def set_field_value(self, name: str, value: object) -> None:
        """Set (or clear, when ``value`` is None) an AcroForm field value."""
        if value is None:
            self._form_values.pop(name, None)
        else:
            self._form_values[name] = value
        self.dirty = True

    # ---- per-page annotations (ride the PageRef; applied at materialise) ---------

    def page_annotations(self, index: int) -> tuple:
        """The annotation descriptors on the page at ``index``."""
        return self.ordered[index].annotations

    # Annotation edits match the target by **identity** first: descriptors are frozen value
    # objects, so two separately-built copies of "the same" mark compare equal, and identity is
    # what tells genuine duplicates apart (a paste clamped back onto its original, say).
    #
    # But identity alone makes a stale-but-equal handle fail *silently* — the edit just doesn't
    # happen, which reads to the user as a broken tool (it did: a moved object's re-selection once
    # held a distinct copy, so the next resize no-opped). So each edit falls back to the first
    # value-equal match. That is safe precisely because the descriptors are value objects: equal
    # ones are interchangeable, so whichever is chosen the resulting page is identical.

    @staticmethod
    def _first_equal_index(annotations: tuple, target) -> int:
        for i, annotation in enumerate(annotations):
            if annotation == target:
                return i
        return -1

    def add_annotation(self, index: int, annotation) -> None:
        """Append an annotation descriptor to the page at ``index``."""
        ref = self.ordered[index]
        self.ordered[index] = ref.with_annotations(ref.annotations + (annotation,))
        self.dirty = True

    def set_annotations(self, index: int, annotations: tuple) -> None:
        """Replace the page's whole annotation tuple — used by the z-order reorder (M59.8), where
        the *order* is the edit. Same contents in a new order still counts as dirty."""
        ref = self.ordered[index]
        if tuple(annotations) != ref.annotations:
            self.ordered[index] = ref.with_annotations(tuple(annotations))
            self.dirty = True

    def clear_annotations(self, index: int) -> None:
        """Remove all annotations from the page at ``index``."""
        ref = self.ordered[index]
        if ref.annotations:
            self.ordered[index] = ref.with_annotations(())
            self.dirty = True

    def remove_annotation(self, index: int, annotation) -> None:
        """Remove one specific annotation instance from the page at ``index``."""
        ref = self.ordered[index]
        remaining = tuple(a for a in ref.annotations if a is not annotation)
        if len(remaining) == len(ref.annotations):     # identity missed → fall back to value
            i = self._first_equal_index(ref.annotations, annotation)
            if i >= 0:
                remaining = ref.annotations[:i] + ref.annotations[i + 1:]
        if len(remaining) != len(ref.annotations):
            self.ordered[index] = ref.with_annotations(remaining)
            self.dirty = True

    def replace_annotation(self, index: int, old, new) -> None:
        """Swap ``old`` for ``new`` **in place** on the page at ``index`` (preserving z-order).

        Used when an annotation is mutated rather than added/removed — moving a text box or
        re-editing its text replaces the (immutable) descriptor with an updated one without
        disturbing the stacking order, so it reads as one undoable edit.
        """
        ref = self.ordered[index]
        annotations = tuple(new if a is old else a for a in ref.annotations)
        if annotations == ref.annotations:             # identity missed → fall back to value
            i = self._first_equal_index(ref.annotations, old)
            if i >= 0:
                annotations = ref.annotations[:i] + (new,) + ref.annotations[i + 1:]
        if annotations != ref.annotations:
            self.ordered[index] = ref.with_annotations(annotations)
            self.dirty = True

    # ---- cross-window move / copy -----------------------------------------------

    def import_pages(
        self, at_index: int, other: "VirtualDocument", indices: Iterable[int]
    ) -> list[PageRef]:
        """Copy pages ``indices`` from another virtual document in at ``at_index``.

        Registers the other document's source(s) here (cross-window paste), then splices the
        same PageRefs — the lossless object-level copy happens later, at materialize. Returns
        the inserted refs so the caller (a move) can delete the originals from ``other``.
        """
        refs = [other.ordered[i] for i in indices]
        for r in refs:
            self.register_source(r.source_id, other.sources[r.source_id])
        self.insert_pages(at_index, refs)
        return refs

    # ---- reload (point-of-no-return after a destructive save) -------------------

    def reload_from_file(self, path: str) -> None:
        """Re-seed this document **in place** from a freshly-saved file (the redaction commit).

        After a save that applied redactions, the on-disk file is clean but the in-memory sources
        still hold the original (un-redacted) bytes — so an undo + re-save could resurrect the
        removed content. Reloading from the clean output drops those bytes from memory and resets
        ``ordered`` to the saved page set; the caller then clears the undo stack, making the
        redaction a true point of no return (the secret is gone from disk *and* RAM).

        Mutates this same object (so the view / thumbnails / overlays keep their reference). The old
        ``sources`` dict is **dropped, not closed**: some entries may be shared with other windows
        (cross-window paste registers another window's source), and closing those would corrupt them.
        """
        self.sources = {}
        self._source_has_ours = {}  # new file's bytes → recompute whether our marks are baked in
        self._source_passwords = {}
        self._source_encryption = {}
        self._source_restrictions = {}
        # …and the saved file is the new baseline for an appending save (M116): its bytes are what
        # a further edit would append to, and the marks it carries are what those pages now
        # "arrived" with. Re-seeded by open_source / _seed_ordered below.
        self._source_bytes = {}
        self._source_marks = {}

        def known_then_prompt(path_, retry):
            # A carry-through save (M54) wrote the file with the password we hold, so try it
            # silently first — a redaction commit / Revert on an encrypted document must not
            # re-prompt for a password we know. Fall back to the stored provider (an external
            # program may have re-encrypted the file with a different password).
            if not retry and self._password is not None:
                return self._password
            if self._password_provider is not None:
                return self._password_provider(path_, retry)
            return None

        source_id = self.open_source(path, known_then_prompt)
        # Re-baseline the carry-through from what actually opened the file: the fallback may
        # have collected a different password, and a now-unencrypted file clears it.
        self._password = self._source_passwords.get(source_id)
        # …and re-baseline the restrictions from the reloaded file too. Zeroing them whenever no
        # password came back was right for the case it was written for (a file that is simply no
        # longer encrypted) and wrong for the other one: an owner-password document opens without
        # a password and still forbids copying, so a redaction commit made `permissions` read
        # "everything allowed" for a document that allowed nothing — M93's defect, re-entered
        # through the reload door (M94).
        self._permissions = self._source_restrictions.get(source_id, -1)
        self.origin_source_id = source_id
        self.path = path
        self._origin_toc = self.sources[source_id].get_toc(simple=False)
        self._capture_origin_metadata(source_id)  # the saved file's stores are the new baseline
        self._metadata_override = None
        self.ordered = self._seed_ordered(source_id)  # re-read our annotations from the clean file
        self._form_values = {}
        self.dirty = False

    def has_redactions(self) -> bool:
        """True if any page carries a redaction (so a save must commit it irreversibly)."""
        from model.page_edits import Redaction

        return any(isinstance(a, Redaction) for ref in self.ordered for a in ref.annotations)

    def has_content_marks(self) -> bool:
        """True if any page carries a stamp / signature / watermark (M61).

        Content marks bake into the page's **content stream**, so — unlike our annotations — they
        leave nothing author-tagged to read back, and the model's copy would re-bake a second one on
        the next save. A save that writes one is therefore committed the same way a redaction is:
        confirm, write, then reload from the clean file so the model no longer holds a mark that is
        already in the page.
        """
        from model.content_marks import is_content_mark

        return any(is_content_mark(a) for ref in self.ordered for a in ref.annotations)

    # ---- dirty tracking ---------------------------------------------------------

    def mark_clean(self) -> None:
        self.dirty = False

    def close(self) -> None:
        for doc in self.sources.values():
            doc.close()
        self.sources.clear()
        # The captured source bytes are a *reference* to what each ``fitz.open(stream=…)`` already
        # held (M116), so they cost nothing while a document is open and everything after it is
        # closed: without this, closing a 75 MB PDF would free the MuPDF side and leave 75 MB of
        # Python bytes alive for as long as anything still points at this object — and a
        # ``MainWindow`` reference cycle outliving its window is a thing this project has measured
        # (M89). The per-page marks go with them; both are re-seeded by ``open_source`` /
        # ``_seed_ordered`` if this document is ever reloaded.
        self._source_bytes.clear()
        self._source_marks.clear()
