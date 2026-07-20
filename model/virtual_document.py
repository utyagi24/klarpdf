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
) -> "tuple[fitz.Document, str]":
    """Authenticate an encrypted ``doc`` (prompting via ``password_provider``), then return a fresh
    **decrypted** in-memory copy plus the password that worked (M32; M54 records the password).

    ``password_provider(path, retry)`` returns a password string, or ``None`` to cancel. The model
    loops on a wrong password (re-calling with ``retry=True``) until it succeeds or the user
    cancels. On success the document is re-serialised with encryption removed, so nothing downstream
    — fresh source copies, materialise, render — ever needs the password again; the password is
    returned so a save can **carry the encryption through** (M54 — it supersedes M32's
    save-unencrypted deferral). Raises :class:`PasswordRequired` when no password is available.

    NB: ``authenticate`` returns a truthy bitfield on success (``needs_pass`` stays set even then),
    and the decrypt ``tobytes`` passes no ``garbage``/``deflate`` — garbage-collecting an AES doc
    mid-decrypt corrupts its content streams; the materialise save cleans the decrypted output later.
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
    decrypted = doc.tobytes(encryption=fitz.PDF_ENCRYPT_NONE)
    doc.close()
    return fitz.open(stream=decrypted, filetype="pdf"), password


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
        self._source_passwords: dict[str, str] = {}
        # Cache: does a registered source carry baked KlarPDF annotations? Keyed by source id;
        # source bytes are immutable, so this never changes for a given source (cleared only when
        # sources are reset in reload_from_file). Lets the viewer / thumbnails keep the fast
        # straight-from-source render for clean documents and switch to the edits-applied copy
        # (our marks stripped, redrawn editable) only for documents that actually have our marks.
        self._source_has_ours: dict[str, bool] = {}
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
        :class:`PasswordRequired` propagates if no password is available.
        """
        source_id = normalize_path(path)
        if source_id not in self.sources:
            doc = fitz.open(stream=Path(path).read_bytes(), filetype="pdf")
            if doc.needs_pass:
                doc, password = _authenticate_and_decrypt(doc, path, password_provider)
                self._source_passwords[source_id] = password
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
        """
        return fitz.open(stream=self.sources[source_id].tobytes(), filetype="pdf")

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
            (self._password, self._permissions),
            self.dirty,
        )

    def restore(self, state: State) -> None:
        ordered, form_values, metadata_override, encryption, dirty = state
        self.ordered = list(ordered)
        self._form_values = dict(form_values)
        self._metadata_override = None if metadata_override is None else dict(metadata_override)
        self._password, self._permissions = encryption
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
        honored by most viewers, not cryptographically enforced — only the password is."""
        return self._permissions

    def set_encryption(self, password: "str | None", permissions: int = -1) -> None:
        """Set / change / remove the password the next Save applies (+ advisory flags).

        Removing the password (``None``) resets the flags too: PDF permission bits live inside
        the encryption dictionary, so restrictions without a password don't exist."""
        self._password = password
        self._permissions = -1 if password is None else int(permissions)
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
        if self._password is None:
            self._permissions = -1
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
