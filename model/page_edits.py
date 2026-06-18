"""Page-content edits layer (PLAN.md §Next-release roadmap, M14).

The virtual-document model so far only *reorders* read-only source pages. This module is the home
for edits to page **content** — starting with **AcroForm field values** (M14) and extended with
annotations (highlight / text-box, M20) and **destructive redaction** (M21) in v0.4.0.

The hard constraint: source ``fitz.Document``s are shared across windows, so we never mutate them.
Instead the edit state lives in :class:`~model.virtual_document.VirtualDocument` (snapshotted for
undo/redo) and is applied only at **materialise**, on the freshly written *output* copy.

Form-field values are **document-level**: an AcroForm field is identified by name across the whole
document (the same field can have widgets on several pages, sharing one value), so values are keyed
by field name — not by page. This module provides:

* :func:`read_form_fields` — enumerate the fillable widgets (name/type/geometry/page) for the UI;
* :func:`apply_form_values` — write the stored values onto a materialised output document.

Both are GUI-free and headless-testable.
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf as fitz

# Widget types we treat as fillable (text, the button family, and the choice family).
FILLABLE_TYPES = frozenset(
    {
        fitz.PDF_WIDGET_TYPE_TEXT,
        fitz.PDF_WIDGET_TYPE_CHECKBOX,
        fitz.PDF_WIDGET_TYPE_RADIOBUTTON,
        fitz.PDF_WIDGET_TYPE_COMBOBOX,
        fitz.PDF_WIDGET_TYPE_LISTBOX,
    }
)


@dataclass(frozen=True)
class FormField:
    """One fillable widget occurrence, located in the *current* page order.

    A field may appear on more than one page (same ``name`` → shared value); each occurrence is a
    separate ``FormField`` so the UI can place an editor over every widget.
    """

    name: str
    type: int                       # fitz.PDF_WIDGET_TYPE_*
    type_string: str
    page_index: int                 # index into VirtualDocument.ordered (live page order)
    rect: tuple[float, float, float, float]  # widget box in unrotated page points
    choices: tuple[str, ...] | None  # options for combo/list, else None
    current_value: object            # the source widget's existing value


def read_form_fields(vdoc) -> list[FormField]:
    """Enumerate fillable widgets across the document's pages, in current page order."""
    fields: list[FormField] = []
    for page_index in range(vdoc.page_count):
        ref = vdoc.ordered[page_index]
        page = vdoc.sources[ref.source_id][ref.source_page_index]
        for widget in page.widgets() or []:
            if widget.field_type not in FILLABLE_TYPES or not widget.field_name:
                continue
            r = widget.rect
            choices = tuple(widget.choice_values) if widget.choice_values else None
            fields.append(
                FormField(
                    name=widget.field_name,
                    type=widget.field_type,
                    type_string=widget.field_type_string,
                    page_index=page_index,
                    rect=(r.x0, r.y0, r.x1, r.y1),
                    choices=choices,
                    current_value=widget.field_value,
                )
            )
    return fields


# Text-like fields whose "empty" means cleared (vs a checkbox's Off / a radio's deselect).
_TEXTLIKE = frozenset(
    {fitz.PDF_WIDGET_TYPE_TEXT, fitz.PDF_WIDGET_TYPE_COMBOBOX, fitz.PDF_WIDGET_TYPE_LISTBOX}
)


# ---- annotations (per-page; ride the PageRef, applied at materialise — v0.4.0) ----
#
# Frozen + hashable so they can live inside a frozen PageRef and be snapshotted for undo/redo.
# Geometry is in unrotated page points (the same space text selection + the viewer overlays use).

# Author tag stamped on every annotation pdfproj bakes in. It is the hook a future "round-trip"
# milestone (re-open + edit/remove saved annotations — PLAN.md §Future enhancements) needs to tell
# *our* annotations from ones other tools wrote, so it can read only ours back into the model and
# strip-then-re-add them at save without duplicating. Costs nothing today.
PDFPROJ_AUTHOR = "pdfproj"


@dataclass(frozen=True)
class Highlight:
    """A text highlight over one or more word boxes (non-destructive — the text stays intact)."""

    rects: tuple[tuple[float, float, float, float], ...]
    color: tuple[float, float, float] = (1.0, 0.86, 0.10)  # marker yellow


@dataclass(frozen=True)
class TextBox:
    """A free-text note box.

    ``fontname`` / ``fontsize`` / ``color`` are stored on the descriptor (not hard-coded at
    materialise) so a future font/size/colour picker is pure UI wiring — the render + materialise
    paths already honour whatever the descriptor carries. ``fontname`` is a PyMuPDF base-14 name
    (``helv``, ``tiro``, ``cour``…); ``helv`` (Helvetica) is the default.
    """

    rect: tuple[float, float, float, float]
    text: str
    fontsize: float = 11.0
    color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    fontname: str = "helv"


@dataclass(frozen=True)
class Redaction:
    """One or more regions to **destructively** remove (M21).

    Unlike :class:`Highlight` / :class:`TextBox` (which merely overlay), a redaction deletes the
    text / images / vector graphics under its rects at materialise via PyMuPDF's
    ``apply_redactions`` — the content is *gone* from the output, not just covered. Each rect is
    painted with an opaque ``fill`` (black by default).

    ``rects`` is a tuple (mirroring :class:`Highlight`): a region drag is a single rect, while a
    text-flow "Redact Selection" is one rect **per line** — unioned into a continuous bar so the
    redaction reveals neither word boundaries nor word lengths (a known de-anonymisation leak).
    Rides the PageRef like the others, so it follows the page through reorder and snapshots for
    undo/redo.
    """

    rects: tuple[tuple[float, float, float, float], ...]
    fill: tuple[float, float, float] = (0.0, 0.0, 0.0)


def apply_annotations(page: fitz.Page, annotations: tuple) -> None:
    """Write a page's *non-destructive* annotation descriptors onto a materialised output page.

    Redaction descriptors are deliberately skipped here — they are handled separately by
    :func:`apply_redactions`, which must run as its own destructive pass (see
    :mod:`model.edit_engine`). Each baked annotation is tagged with :data:`PDFPROJ_AUTHOR`.
    """
    for annotation in annotations:
        if isinstance(annotation, Highlight):
            annot = page.add_highlight_annot([fitz.Rect(r) for r in annotation.rects])
            annot.set_colors(stroke=annotation.color)
            annot.set_info(title=PDFPROJ_AUTHOR)
            annot.update()
        elif isinstance(annotation, TextBox):
            annot = page.add_freetext_annot(
                fitz.Rect(annotation.rect),
                annotation.text,
                fontsize=annotation.fontsize,
                fontname=annotation.fontname,
                text_color=annotation.color,
            )
            annot.set_info(title=PDFPROJ_AUTHOR)
            annot.update()


def apply_redactions(page: fitz.Page, annotations: tuple) -> None:
    """Destructively remove every :class:`Redaction`'s regions from a materialised output page.

    Adds a redaction annotation per rect (across all of the page's redactions), then commits them
    in one ``page.apply_redactions()`` — which physically deletes the overlapped text/images/
    graphics and leaves an opaque ``fill`` box. ``cross_out=False`` keeps each box a clean fill (no
    crossing lines). A no-op when the page has no redactions, so it never rewrites an unredacted
    page's content streams. (The redaction annotations are *consumed* by apply_redactions — nothing
    to author-tag, and nothing left in the output that could be deleted to reveal the content.)
    """
    rects = [
        rect
        for annotation in annotations
        if isinstance(annotation, Redaction)
        for rect in annotation.rects
    ]
    if not rects:
        return
    for annotation in annotations:
        if isinstance(annotation, Redaction):
            for rect in annotation.rects:
                page.add_redact_annot(fitz.Rect(rect), fill=annotation.fill, cross_out=False)
    page.apply_redactions()


def apply_form_values(out_doc: fitz.Document, values: dict[str, object]) -> None:
    """Write ``values`` (field name → value) onto matching widgets in a materialised output.

    Called during materialise after ``insert_pdf(widgets=True)`` has copied the widgets. A value
    whose field no longer exists in the output (its page was deleted) is simply skipped.

    Clearing a text-like field is special: PyMuPDF silently ignores ``field_value = ""`` (the
    assignment doesn't persist), so an already-filled field could never be emptied. We instead
    reset ``/V`` to an empty string and drop the stale ``/AP`` appearance via the xref, and do NOT
    call ``update()`` afterwards (it would rewrite the old value back).
    """
    if not values:
        return
    for page in out_doc:
        for widget in page.widgets() or []:
            name = widget.field_name
            if name not in values:
                continue
            value = values[name]
            if widget.field_type in _TEXTLIKE and (value is None or value == ""):
                out_doc.xref_set_key(widget.xref, "V", "()")     # empty PDF string
                out_doc.xref_set_key(widget.xref, "AP", "null")  # force a blank appearance
            else:
                widget.field_value = value
                widget.update()
