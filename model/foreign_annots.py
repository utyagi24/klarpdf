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


def apply_foreign_deletions(page: "fitz.Page", annotations: tuple) -> int:
    """Delete the foreign annotations named by ``annotations``' deletions. Returns how many went.

    Runs on the **materialised output page**, never a shared source. Matching is positional within
    the page: the *n*-th annotation with a fingerprint satisfies the *n*-th deletion carrying it, so
    two identical annotations resolve to themselves in order rather than both being removed by one
    descriptor.

    A deletion whose target is not found is silently a no-op — the annotation may have been removed
    by an earlier redaction pass, or the page may have arrived from another document by drag/paste.
    Failing the save over a missing annotation would be much worse than not deleting it.
    """
    wanted: dict[str, int] = {}
    for mark in annotations:
        if isinstance(mark, ForeignDeletion):
            wanted[mark.fingerprint] = wanted.get(mark.fingerprint, 0) + 1
    if not wanted:
        return 0
    removed = 0
    annot = page.first_annot
    while annot:
        key = fingerprint(annot) if is_foreign(annot) else None
        if key is not None and wanted.get(key):
            wanted[key] -= 1
            removed += 1
            annot = page.delete_annot(annot)      # returns the next annot (the documented idiom)
        else:
            annot = annot.next
    return removed
