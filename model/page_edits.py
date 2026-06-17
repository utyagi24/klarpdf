"""Page-content edits layer (PLAN.md §Next-release roadmap, M14).

The virtual-document model so far only *reorders* read-only source pages. This module is the home
for edits to page **content** — starting with **AcroForm field values** (M14) and extended with
annotations / redactions in v0.3.0 (M16–M17).

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
