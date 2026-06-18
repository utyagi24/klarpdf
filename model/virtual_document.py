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


@dataclass(frozen=True, slots=True)
class PageRef:
    """A reference to one source page. Immutable so snapshots are cheap and safe.

    ``rotation_override`` is an **absolute** final angle (0/90/180/270) or ``None`` to inherit
    the source page's own rotation. Rotating produces a *new* PageRef (see ``with_rotation``).

    ``annotations`` is an immutable tuple of annotation descriptors (``model.page_edits`` —
    highlight / text-box, v0.4.0) that live **on the page**: because they ride the PageRef, they
    follow the page through reorder / delete / cross-window copy, and are snapshotted with
    ``ordered[]`` for undo/redo. They are applied to the output page at materialize.
    """

    source_id: str
    source_page_index: int
    rotation_override: int | None = None
    annotations: tuple = ()

    def with_rotation(self, angle: int | None) -> "PageRef":
        if angle is not None:
            angle %= 360
            if angle % 90 != 0:
                raise ValueError(f"rotation must be a multiple of 90, got {angle}")
        return replace(self, rotation_override=angle)

    def with_annotations(self, annotations: tuple) -> "PageRef":
        return replace(self, annotations=tuple(annotations))


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

    # ---- construction / sources -------------------------------------------------

    @classmethod
    def from_path(cls, path: str) -> "VirtualDocument":
        """Open ``path`` as the origin document and seed ``ordered`` with all its pages."""
        vd = cls()
        source_id = vd.open_source(path)
        vd.origin_source_id = source_id
        vd.path = path
        vd._origin_toc = vd.sources[source_id].get_toc(simple=False)
        vd.ordered = [PageRef(source_id, i) for i in range(vd.sources[source_id].page_count)]
        vd.dirty = False
        return vd

    def open_source(self, path: str) -> str:
        """Open and register a source by path (idempotent). Returns its source id.

        Opened from an **in-memory copy** of the file, never a live file handle: on Windows an open
        handle blocks the atomic ``os.replace`` used by in-place Save, so holding the file open
        would make saving over the currently-open document fail with "access denied".
        """
        source_id = normalize_path(path)
        if source_id not in self.sources:
            self.sources[source_id] = fitz.open(stream=Path(path).read_bytes(), filetype="pdf")
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

    # ---- snapshot / restore (used by edit_commands for undo/redo) ---------------

    def snapshot(self) -> State:
        return (tuple(self.ordered), dict(self._form_values), self.dirty)

    def restore(self, state: State) -> None:
        ordered, form_values, dirty = state
        self.ordered = list(ordered)
        self._form_values = dict(form_values)
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

    def add_annotation(self, index: int, annotation) -> None:
        """Append an annotation descriptor to the page at ``index``."""
        ref = self.ordered[index]
        self.ordered[index] = ref.with_annotations(ref.annotations + (annotation,))
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
        if len(remaining) != len(ref.annotations):
            self.ordered[index] = ref.with_annotations(remaining)
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

    # ---- dirty tracking ---------------------------------------------------------

    def mark_clean(self) -> None:
        self.dirty = False

    def close(self) -> None:
        for doc in self.sources.values():
            doc.close()
        self.sources.clear()
