"""Foreign annotations — the ones *other* tools wrote (PLAN.md §R5, M66).

Everything before this milestone treated a PDF's existing annotations as untouchable scenery: they
are copied through by ``insert_pdf(annots=True)`` and otherwise ignored, because only annotations
carrying the :data:`~model.page_edits.KLARPDF_AUTHOR` tag round-trip into the editable model. That
is the right default — it is what guarantees a document edited here comes back byte-identical
wherever we did not touch it — but it also means a stray comment from Acrobat cannot be removed.

This module is the shared infrastructure for reaching them, built once and consumed by three verbs
across R5: **delete** (M66), **move** (M67), and **adopt-on-edit** (M68).

**Identity is the hard part, and it is why this is the keystone.** The obvious handle — the object's
``xref`` — is worthless to us: ``insert_pdf`` renumbers every object as it copies, so the xref a
foreign annotation has in the *source* is not the one it has in the *output*, and a descriptor
holding one would silently target the wrong annotation (or nothing) at materialise. Annotations also
have no required unique key. So a foreign annotation is identified by a **fingerprint**:

* its ``/NM`` name when the writing tool set one (the PDF spec's own optional identifier), else
* a hash of the things that do survive a copy: annotation **type**, **rect** (rounded, because a
  round-trip through PDF floats is not bit-exact), and **contents**.

The fallback is not guaranteed unique — two identical empty squares at the same spot fingerprint the
same — so matching is deliberately **positional within a page**: the *n*-th annotation with a given
fingerprint maps to the *n*-th in the output. Identical twins then resolve to themselves in order,
which is the best available answer and never deletes the wrong *kind* of thing.

**Zero fidelity risk.** A deletion removes an annotation; it never rewrites one. Every annotation not
named by a :class:`ForeignDeletion` is copied through exactly as before, so this works for **every**
annotation type — including ones the model has no idea how to draw — and costs nothing in fidelity.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pymupdf as fitz

from model.page_edits import KLARPDF_AUTHOR

# Rect rounding for the fallback fingerprint. Coordinates survive a copy but not bit-exactly (they
# go out and back through PDF's decimal floats), so the hash must agree to within a hair. A tenth of
# a point is far below anything a user could place by hand and far above the round-trip noise.
_RECT_PRECISION = 1


def _rect_key(rect) -> tuple:
    return tuple(round(value, _RECT_PRECISION) for value in (rect.x0, rect.y0, rect.x1, rect.y1))


def annot_name(annot: "fitz.Annot") -> str:
    """The annotation's ``/NM`` name, or ``""``.

    Read from the object dictionary, **not** from ``annot.info``: PyMuPDF's ``info`` dict exposes a
    ``"name"`` key that is always empty for this purpose (it reports the icon name for a few types,
    never ``/NM``), so trusting it would silently disable the preferred identity path and send every
    annotation down the hash fallback.
    """
    try:
        kind, value = annot.parent.parent.xref_get_key(annot.xref, "NM")
    except Exception:
        return ""
    return value.strip() if kind == "string" and value else ""


def fingerprint(annot: "fitz.Annot") -> str:
    """A stable identity for a foreign annotation, surviving ``insert_pdf``'s renumbering.

    Prefers the annotation's own ``/NM`` name; falls back to a hash of type + rect + contents. See
    the module docstring for why the xref cannot be used.
    """
    name = annot_name(annot)
    if name:
        return f"nm:{name}"
    info = annot.info or {}
    payload = repr((annot.type[0], _rect_key(annot.rect), info.get("content", "")))
    return "fp:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def is_foreign(annot: "fitz.Annot") -> bool:
    """True for an annotation KlarPDF did not write.

    Our own marks are matched by author tag and round-trip into editable descriptors (M31), so they
    are emphatically *not* foreign — they already have a better handle than a fingerprint.
    """
    return (annot.info or {}).get("title") != KLARPDF_AUTHOR


@dataclass(frozen=True)
class ForeignAnnot:
    """A read-only view of one foreign annotation, for hit-testing and the UI.

    Not an editable descriptor — it never rides a ``PageRef``. It is what the viewer enumerates to
    draw a selection outline and offer a verb; the editable state is the :class:`ForeignDeletion`
    that a verb produces.
    """

    fingerprint: str
    kind: int                                    # fitz.PDF_ANNOT_*
    kind_name: str
    rect: tuple[float, float, float, float]
    contents: str
    author: str

    def bounding_rect(self) -> tuple[float, float, float, float]:
        """Its box — so ``mark_bounds`` and the viewer's outline helper work on it unchanged."""
        return self.rect

    @property
    def label(self) -> str:
        """What to call it in a menu — the type, plus the author when the file names one."""
        return f"{self.kind_name}{f' by {self.author}' if self.author else ''}"


@dataclass(frozen=True)
class ForeignDeletion:
    """A foreign annotation marked for removal at materialise (M66).

    Rides the ``PageRef`` like every other page edit, so it snapshots for undo/redo and follows its
    page through a reorder. Holds only the fingerprint — nothing about the annotation's content — so
    it stays a small frozen value.
    """

    fingerprint: str
    label: str = ""          # what the UI called it, for a readable undo entry


@dataclass(frozen=True)
class ForeignMove:
    """A foreign annotation translated by ``(dx, dy)``, applied at materialise (M67).

    Deltas are in **fitz page coordinates** (y grows downward, the frame every other descriptor in
    this codebase uses); the conversion to PDF's y-up array values happens at apply time.

    Moves for one annotation **combine rather than stack**: dragging a mark twice replaces its
    descriptor with the summed delta. That matters for more than tidiness — a hash fingerprint is
    computed from the annotation's *rect*, so a second descriptor keyed on the moved position would
    no longer match the annotation as it arrives at materialise. One descriptor per mark, always
    holding the original fingerprint, keeps identity stable.
    """

    fingerprint: str
    dx: float
    dy: float
    label: str = ""


# Annotation dictionary keys holding page geometry as flat number arrays (x y x y …). All of them
# have to travel with a move, or the drawn appearance and the annotation's own geometry desync —
# a highlight whose /Rect moved but whose /QuadPoints did not would be re-derived back to its old
# position by any viewer that regenerates appearances from the quads.
_GEOMETRY_KEYS = ("Rect", "QuadPoints", "Vertices", "L", "CL", "InkList")

_NUMBER = __import__("re").compile(r"-?\d+(?:\.\d+)?")


def _shift_array(value: str, dx: float, dy: float) -> str:
    """Translate every ``x y`` pair in a PDF number array, preserving the string's structure.

    Rewritten match-by-match rather than parsed and rebuilt, so nested arrays (``/InkList`` is an
    array *of* arrays) keep their brackets exactly. Alternating parity survives nesting because every
    sub-array holds whole coordinate pairs.

    **``dy`` is subtracted**: these arrays are in PDF user space, whose y axis grows *upward*, while
    the callers' deltas are in fitz page space, whose y grows downward.
    """
    index = 0

    def replace(match) -> str:
        nonlocal index
        shifted = float(match.group()) + (dx if index % 2 == 0 else -dy)
        index += 1
        return f"{shifted:g}"

    return _NUMBER.sub(replace, value)


def _translate_annot(annot: "fitz.Annot", dx: float, dy: float) -> None:
    """Move one annotation by editing its geometry keys directly.

    Deliberately **not** ``Annot.set_rect``: that raises outright on the quad-based text markup types
    ("Highlight annotations have no Rect property"), so it cannot be the one path for "every
    annotation type". Editing the dictionary works for all of them and never goes near the
    appearance stream, which is what makes the "appearance preserved verbatim" guarantee literal —
    a rich callout box with a custom appearance moves with exactly zero degradation, because nothing
    re-renders it. The PDF spec maps an appearance's ``/BBox`` into ``/Rect``, so moving the rect
    moves what is drawn.
    """
    doc = annot.parent.parent
    for key in _GEOMETRY_KEYS:
        kind, value = doc.xref_get_key(annot.xref, key)
        if kind == "array" and value:
            doc.xref_set_key(annot.xref, key, _shift_array(value, dx, dy))


def read_foreign_annotations(page: "fitz.Page") -> tuple[ForeignAnnot, ...]:
    """Every foreign annotation on ``page``, in the page's annotation order."""
    found = []
    for annot in page.annots():
        if not is_foreign(annot):
            continue
        info = annot.info or {}
        rect = annot.rect
        found.append(
            ForeignAnnot(
                fingerprint=fingerprint(annot),
                kind=annot.type[0],
                kind_name=annot.type[1] or "Annotation",
                rect=(rect.x0, rect.y0, rect.x1, rect.y1),
                contents=info.get("content", ""),
                author=(info.get("title") or "").strip(),
            )
        )
    return tuple(found)


def page_has_foreign_annotations(page: "fitz.Page") -> bool:
    """Cheap check used to decide whether a page needs the foreign-annotation machinery at all."""
    return any(is_foreign(annot) for annot in page.annots())


def apply_foreign_edits(page: "fitz.Page", annotations: tuple) -> tuple[int, int]:
    """Apply this page's foreign-annotation deletions and moves. Returns ``(deleted, moved)``.

    Runs on the **materialised output page**, never a shared source.

    **Fingerprints are resolved once, up front**, before anything is applied — a hash fingerprint is
    computed from the annotation's rect, so moving an annotation changes its fingerprint, and
    matching descriptor-by-descriptor as we went would make a move invalidate every later descriptor
    aimed at the same mark. Resolving first means every descriptor is matched against the page as it
    arrived.

    Matching is positional within the page: the *n*-th annotation with a fingerprint satisfies the
    *n*-th descriptor carrying it, so identical twins resolve to themselves in order rather than one
    descriptor claiming both.

    A descriptor whose target is not found is silently a no-op — the annotation may have been
    removed by an earlier redaction pass, or the page may have arrived from another document by
    drag/paste. Failing the save over a missing annotation would be much worse.
    """
    deletions: dict[str, int] = {}
    moves: dict[str, list] = {}
    for mark in annotations:
        if isinstance(mark, ForeignDeletion):
            deletions[mark.fingerprint] = deletions.get(mark.fingerprint, 0) + 1
        elif isinstance(mark, ForeignMove):
            moves.setdefault(mark.fingerprint, []).append(mark)
    if not deletions and not moves:
        return (0, 0)

    resolved = [(annot, fingerprint(annot)) for annot in page.annots() if is_foreign(annot)]
    to_delete, to_move = [], []
    for annot, key in resolved:
        # Deletion wins over a move for the same mark: dragging something and then deleting it
        # should delete it, and a deleted annotation has no position worth computing.
        if deletions.get(key):
            deletions[key] -= 1
            to_delete.append(annot)
        elif moves.get(key):
            to_move.append((annot, moves[key].pop(0)))

    for annot, mark in to_move:
        _translate_annot(annot, mark.dx, mark.dy)
    for annot in to_delete:
        page.delete_annot(annot)
    return (len(to_delete), len(to_move))


def apply_foreign_deletions(page: "fitz.Page", annotations: tuple) -> int:
    """Deletions only — :func:`apply_foreign_edits` restricted to :class:`ForeignDeletion`."""
    return apply_foreign_edits(
        page, tuple(a for a in annotations if isinstance(a, ForeignDeletion))
    )[0]
