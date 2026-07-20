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

# Author tag stamped on every annotation KlarPDF bakes in. It is the hook a future "round-trip"
# milestone (re-open + edit/remove saved annotations — PLAN.md §Future enhancements) needs to tell
# *our* annotations from ones other tools wrote, so it can read only ours back into the model and
# strip-then-re-add them at save without duplicating. Costs nothing today.
KLARPDF_AUTHOR = "klarpdf"


@dataclass(frozen=True)
class Highlight:
    """A text highlight over one or more word boxes (non-destructive — the text stays intact)."""

    rects: tuple[tuple[float, float, float, float], ...]
    color: tuple[float, float, float] = (1.0, 0.86, 0.10)  # marker yellow


@dataclass(frozen=True)
class Underline:
    """A text underline over one or more line bars (M56 — same text-quad path as Highlight)."""

    rects: tuple[tuple[float, float, float, float], ...]
    color: tuple[float, float, float] = (0.86, 0.10, 0.10)  # redline red


@dataclass(frozen=True)
class Strikeout:
    """A text strike-through over one or more line bars (M56 — the Highlight quad path)."""

    rects: tuple[tuple[float, float, float, float], ...]
    color: tuple[float, float, float] = (0.86, 0.10, 0.10)


@dataclass(frozen=True)
class TextBox:
    """A free-text note box, with optional styling baked at materialise (M27 — styled text boxes).

    Every style field rides the descriptor (not hard-coded at materialise), so it snapshots for
    undo/redo and carries across windows exactly like the geometry:

    * ``fontname`` — a PyMuPDF base-14 **family** selector: ``helv`` (Helvetica, the default),
      ``tiro`` (Times), ``cour`` (Courier). On the FreeText *appearance* path a base-14 name encodes
      only the family: PyMuPDF collapses the bold/italic variant names (``hebo`` / ``heit`` …) onto
      the same ``/Helv`` font in the DA string and renders them identically, so weight/slant cannot
      be carried this way (it would need the richtext path — out of scope for M27, see PLAN.md §M27).
    * ``fontsize`` / ``color`` — point size + RGB text colour (baked into the annot's DA string).
    * ``fill_color`` — the box's background fill (annot ``/C``); ``None`` = no fill (transparent).
    * ``border_width`` — the box outline thickness in points (annot ``/BS /W``); ``0`` = no outline.
      A drawn outline is black — the simple FreeText path carries no separate border colour (setting
      ``border_color`` there raises; that, too, needs richtext).
    """

    rect: tuple[float, float, float, float]
    text: str
    fontsize: float = 11.0
    color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    fontname: str = "helv"
    fill_color: tuple[float, float, float] | None = None
    border_width: float = 0.0


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
    :mod:`model.edit_engine`). Each baked annotation is tagged with :data:`KLARPDF_AUTHOR`.
    """
    for annotation in annotations:
        if isinstance(annotation, Highlight):
            annot = page.add_highlight_annot([fitz.Rect(r) for r in annotation.rects])
            annot.set_colors(stroke=annotation.color)
            annot.set_info(title=KLARPDF_AUTHOR)
            annot.update()
        elif isinstance(annotation, (Underline, Strikeout)):
            add = (
                page.add_underline_annot
                if isinstance(annotation, Underline)
                else page.add_strikeout_annot
            )
            annot = add([fitz.Rect(r) for r in annotation.rects])
            annot.set_colors(stroke=annotation.color)
            annot.set_info(title=KLARPDF_AUTHOR)
            annot.update()
        elif isinstance(annotation, TextBox):
            annot = page.add_freetext_annot(
                fitz.Rect(annotation.rect),
                annotation.text,
                fontsize=annotation.fontsize,
                fontname=annotation.fontname,
                text_color=annotation.color,
                fill_color=annotation.fill_color,      # None → no background fill
                border_width=annotation.border_width,  # 0 → no outline (black when > 0)
            )
            annot.set_info(title=KLARPDF_AUTHOR)
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


# ---- round-trip read-back (M31): reconstruct our own annotations from a saved page ----
#
# The inverse of :func:`apply_annotations`: re-parse the highlights / text-boxes a previous KlarPDF
# save baked in, back into the descriptors above, so a reopened document's annotations become
# movable / re-editable / removable again. Only *our* annotations round-trip (matched by the
# :data:`KLARPDF_AUTHOR` title); foreign annotations and consumed redactions are left alone.
# Geometry comes back in unrotated page points — the space the model + viewer overlays use.

# A base-14 DA font name (``/Helv`` / ``/TiRo`` / ``/Cour``) → our :class:`TextBox` ``fontname``.
_DA_FONT_TO_NAME = {"helv": "helv", "tiro": "tiro", "cour": "cour"}


def _parse_freetext_da(da: str) -> tuple[float, tuple[float, float, float], str]:
    """Parse a FreeText DA string → ``(fontsize, text_color, fontname)``.

    A simple-appearance DA looks like ``1 0 0 rg /TiRo 14.0 Tf`` (RGB text) or ``0 g /Helv 11 Tf``
    (grey). Anything we can't read falls back to the :class:`TextBox` defaults (11 pt, black, helv),
    so a hand-edited or foreign-but-tagged DA never raises.
    """
    fontsize, color, fontname = 11.0, (0.0, 0.0, 0.0), "helv"
    tokens = da.split()
    for i, tok in enumerate(tokens):
        if tok == "rg" and i >= 3:
            try:
                color = (float(tokens[i - 3]), float(tokens[i - 2]), float(tokens[i - 1]))
            except ValueError:
                pass
        elif tok == "g" and i >= 1:
            try:
                gray = float(tokens[i - 1])
                color = (gray, gray, gray)
            except ValueError:
                pass
        elif tok == "Tf" and i >= 2:
            try:
                fontsize = float(tokens[i - 1])
            except ValueError:
                pass
            fontname = _DA_FONT_TO_NAME.get(tokens[i - 2].lstrip("/").lower(), "helv")
    return fontsize, color, fontname


def _quads_to_rects(vertices) -> tuple[tuple[float, float, float, float], ...]:
    """Group a highlight's quad-point list (4 points per marked span) into per-span bounding rects."""
    rects = []
    for i in range(0, len(vertices) - 3, 4):
        quad = vertices[i : i + 4]
        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        rects.append((min(xs), min(ys), max(xs), max(ys)))
    return tuple(rects)


def read_klarpdf_annotations(page: fitz.Page) -> tuple:
    """Re-parse this page's KlarPDF-authored highlights / text-boxes into model descriptors.

    The inverse of :func:`apply_annotations`. Matches annotations by the :data:`KLARPDF_AUTHOR`
    title, so only marks KlarPDF itself wrote come back into the editable model; foreign
    annotations are ignored (and copied through verbatim at materialise). Redactions are
    *destructive* and leave nothing tagged to read, so they never round-trip — a redacted save
    stays a point of no return.
    """
    doc = page.parent
    result: list = []
    for annot in page.annots():
        if annot.info.get("title") != KLARPDF_AUTHOR:
            continue
        kind = annot.type[0]
        if kind == fitz.PDF_ANNOT_HIGHLIGHT:
            stroke = annot.colors.get("stroke")
            color = tuple(stroke) if stroke else Highlight.color
            result.append(Highlight(_quads_to_rects(annot.vertices), color=color))
        elif kind in (fitz.PDF_ANNOT_UNDERLINE, fitz.PDF_ANNOT_STRIKE_OUT):
            cls = Underline if kind == fitz.PDF_ANNOT_UNDERLINE else Strikeout
            stroke = annot.colors.get("stroke")
            color = tuple(stroke) if stroke else cls.color
            result.append(cls(_quads_to_rects(annot.vertices), color=color))
        elif kind == fitz.PDF_ANNOT_FREE_TEXT:
            # PyMuPDF grows a FreeText /Rect by border_width/2 on each side when it bakes the
            # outline (RD stays zero), so inset by that to recover the authored box — otherwise the
            # box would creep outward by half the border on every save→reopen→save round-trip.
            border_width = (annot.border or {}).get("width") or 0.0
            inset = border_width / 2.0
            r = annot.rect
            rect = (r.x0 + inset, r.y0 + inset, r.x1 - inset, r.y1 - inset)
            da = doc.xref_get_key(annot.xref, "DA")
            fontsize, color, fontname = _parse_freetext_da(da[1] if da[0] == "string" else "")
            # A FreeText's /C (the box fill) surfaces as the 'stroke' colour in PyMuPDF.
            fill = annot.colors.get("stroke")
            result.append(
                TextBox(
                    rect,
                    annot.info.get("content", ""),
                    fontsize=fontsize,
                    color=color,
                    fontname=fontname,
                    fill_color=tuple(fill) if fill else None,
                    border_width=border_width,
                )
            )
    return tuple(result)


def page_has_klarpdf_annotations(page: fitz.Page) -> bool:
    """True if the page carries any baked KlarPDF-authored (:data:`KLARPDF_AUTHOR`-tagged) mark.

    The viewer / thumbnails use this to decide whether a page must render from an edits-applied
    copy — our baked annotations stripped and redrawn from the (editable) model — instead of
    straight from the shared source, which would otherwise show the original mark twice (the baked
    one pinned under the editable overlay).
    """
    return any(annot.info.get("title") == KLARPDF_AUTHOR for annot in page.annots())


def strip_klarpdf_annotations(page: fitz.Page) -> None:
    """Delete this page's KlarPDF-authored annotations (matched by :data:`KLARPDF_AUTHOR`).

    Used at materialise: ``insert_pdf(annots=True)`` copies every source annotation — *including*
    the KlarPDF marks a prior save baked in — so before re-adding them from the model (which now
    owns them, with any move / edit / removal applied) we strip the copies, leaving the model the
    single source of truth. Foreign annotations are preserved. Uses the documented
    delete-while-iterating idiom (:meth:`Page.delete_annot` returns the next annot), and is a no-op
    on a page with no KlarPDF annotations (so clean pages are never rewritten).
    """
    annot = page.first_annot
    while annot:
        if annot.info.get("title") == KLARPDF_AUTHOR:
            annot = page.delete_annot(annot)
        else:
            annot = annot.next


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
