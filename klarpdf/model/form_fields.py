"""Form-field creation — place new AcroForm fields (PLAN.md §R5, M69).

Everything before this milestone could only *fill* fields a document already had (M14). This adds
the three field types worth creating by hand — **text · checkbox · dropdown** — as page edits that
materialise into ordinary ``page.add_widget`` widgets.

**The output is not a KlarPDF construct.** A field placed here is an AcroForm field like any other,
so everything already built for form documents works on it for free and by construction: inline
filling (M14), lossless value save, edits-aware printing (M25), and flatten (M31.5). There is no
"KlarPDF field" concept to keep working — which is also why nothing here needs an author tag.

**Radio-button groups are deliberately absent** — rejected by the owner (2026-07-18, `PLAN.md`
§Future enhancements): a radio group is several widgets sharing one field name and one export value
each, so it needs group-aware placement and editing UI that the other three do not, for a control
a checkbox usually replaces.

Like every page edit, a :class:`NewField` rides the ``PageRef``: it snapshots for undo/redo, follows
its page through a reorder, and is applied only to the materialised output.
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf as fitz

# The three creatable types, in the order the UI offers them. Keyed by a short stable token so a
# descriptor never stores a PyMuPDF integer constant (which would bake a library detail into
# something that snapshots and round-trips).
FIELD_KINDS = ("text", "checkbox", "dropdown")

_WIDGET_TYPES = {
    "text": fitz.PDF_WIDGET_TYPE_TEXT,
    "checkbox": fitz.PDF_WIDGET_TYPE_CHECKBOX,
    "dropdown": fitz.PDF_WIDGET_TYPE_COMBOBOX,
}

_KIND_LABELS = {"text": "Text Field", "checkbox": "Checkbox", "dropdown": "Dropdown"}


def widget_type(kind: str) -> int:
    return _WIDGET_TYPES[kind]


def kind_label(kind: str) -> str:
    return _KIND_LABELS.get(kind, kind.title())


@dataclass(frozen=True)
class NewField:
    """An AcroForm field to create on this page at materialise.

    ``options`` is only meaningful for ``dropdown`` (the choice list); ``value`` is the field's
    initial value — for a checkbox, truthy means ticked.
    """

    rect: tuple[float, float, float, float]
    name: str
    kind: str = "text"
    value: str = ""
    options: tuple[str, ...] = ()

    def bounding_rect(self) -> tuple[float, float, float, float]:
        """So the viewer's shared hit-test / outline helpers work on it unchanged."""
        return self.rect

    @property
    def label(self) -> str:
        return f"{kind_label(self.kind)} “{self.name}”" if self.name else kind_label(self.kind)


def apply_new_fields(page: "fitz.Page", annotations: tuple) -> int:
    """Create this page's :class:`NewField` widgets on a materialised output page. Returns how many.

    Runs **before** :func:`model.page_edits.apply_form_values`, so a value the user typed into a
    field they created in the same session lands on the widget like any other fill — the field is a
    real AcroForm field by the time the fill pass walks the document.
    """
    made = 0
    for mark in annotations:
        if not isinstance(mark, NewField):
            continue
        widget = fitz.Widget()
        widget.field_name = mark.name or f"field_{made + 1}"
        widget.field_type = widget_type(mark.kind)
        widget.rect = fitz.Rect(mark.rect)
        if mark.kind == "dropdown":
            widget.choice_values = list(mark.options)
            widget.field_value = mark.value or (mark.options[0] if mark.options else "")
        elif mark.kind == "checkbox":
            widget.field_value = bool(mark.value)
        else:
            widget.field_value = mark.value or ""
        page.add_widget(widget)
        made += 1
    return made
