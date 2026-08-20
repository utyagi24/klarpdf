"""Writing and reading text markup — highlights, underlines, strike-throughs, and their notes (M101).

Two functions behind two tools. :func:`annotate` writes marks onto a copy of a document;
:func:`get_annotations` reads back every annotation a document holds, whoever made it.

**This module takes boxes, not queries** (owner, 2026-08-20 — PLAN.md §M101). It does not search,
it does not know what a name or an account number looks like, and it will not go and find one. The
caller locates what it cares about with ``search`` / ``extract_text`` and hands over coordinates.
That is the same seam :mod:`mcp_bridge.redaction` draws when its variant scan *reports* rather than
*matches* — "whether two spellings denote one value is a fact about the document that only the
caller has" — applied to a different question: whether a given line is the termination clause is not
something a PDF engine can know, so it must not pretend to.

**Almost nothing here is new behaviour.** ``Highlight`` / ``Underline`` / ``Strikeout`` already
carry rects, an RGB colour and a note (M81); ``apply_annotations`` already bakes them with the
KlarPDF author tag; ``merge_markup`` already resolves a new mark against the ones already on the
page; ``parse_annotation`` already reads a foreign mark the same way it reads ours (M68). What this
module adds is the boundary: validating a caller's JSON into descriptors, and shaping annotations
back into JSON.

**Why the read side does not go through** :func:`~model.page_edits.parse_annotation` **alone.**
That function returns ``None`` for every type the model cannot draw — a sticky note among them
(§M83: "an Edge sticky note is an unmodeled type"). A reviewer who leaves their comments as sticky
notes rather than as notes on highlights would then be *invisible* to a caller asking what the
document says, which is the one failure this tool must not have. So the read walks the raw
annotations and reports all of them, using ``parse_annotation`` only to answer a narrower question:
whether this mark would round-trip as editable in the app.
"""

from __future__ import annotations

import os

import pymupdf as fitz

from model.markup_palette import (
    color_for_name,
    is_palette_color,
    names_for,
    nearest_name,
)
from model.page_edits import (
    KLARPDF_AUTHOR,
    Highlight,
    Strikeout,
    Underline,
    merge_markup,
    parse_annotation,
)
from mcp_bridge.queries import _page_of, open_document, resolve_pages
from mcp_bridge.transforms import _resolve_out, _write

# The three mark types this tool writes, by the name a caller uses. The model has six more —
# ink, line, rect, ellipse, text box, stamp — and they are deliberately not here: they are drawing
# rather than markup, they take a different geometry (a path, two endpoints, a fill), and a tool
# that offered all nine would spend its whole description cap listing them. The description says so
# explicitly, because a model that guesses `"type": "rect"` should get an error naming the three
# rather than a silent no-op (M106).
MARK_TYPES: dict[str, type] = {
    "highlight": Highlight,
    "underline": Underline,
    "strikeout": Strikeout,
}

# Annotation types that are not markup and would only be noise in a listing. Widgets are form
# fields — `get_form_fields` reports them properly, with their values, flags and export states,
# which is far more than a rect and a colour. Popups are the floating window a sticky note opens
# into, not a mark of their own: reporting one would double-count its parent.
_NOT_MARKUP = {fitz.PDF_ANNOT_WIDGET, fitz.PDF_ANNOT_POPUP}

# Text markup stores its geometry as **quad points**, one quad per line covered, and its `/Rect` is
# a padded bounding box around them — measured, ~5pt wider on each side than the quads it contains.
# Reporting the rect would hand a caller boxes that are visibly too big and, fed to
# `redact_regions`, would delete a strip of whatever sits alongside. So markup is always read from
# its quads; only the types that have no quads fall back to the rect.
_QUAD_TYPES = {
    fitz.PDF_ANNOT_HIGHLIGHT,
    fitz.PDF_ANNOT_UNDERLINE,
    fitz.PDF_ANNOT_STRIKE_OUT,
    fitz.PDF_ANNOT_SQUIGGLY,
}

# How many annotations one `get_annotations` reply may carry. A review copy of a long contract can
# hold hundreds, and an uncapped listing is the failure mode the server's other bulk tools already
# avoid: the reply degrades legibly rather than blowing out the caller's context.
MAX_ANNOTATIONS = 500


def _quads_to_boxes(vertices) -> list[list[float]]:
    """Quad points → one box per quad, in the order the annotation stores them.

    A quad is four ``(x, y)`` corners; ``vertices`` is a flat run of them. This mirrors
    ``page_edits._quads_to_rects`` and stays independent of it only because that one returns tuples
    for the model while this returns JSON lists.
    """
    boxes = []
    for i in range(0, len(vertices) - 3, 4):
        quad = vertices[i:i + 4]
        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        boxes.append([min(xs), min(ys), max(xs), max(ys)])
    return boxes


def _describe(annot, page1: int) -> dict:
    """One raw annotation → the JSON an agent reads.

    ``boxes`` is the field that has to be right: it is in the same **unrotated page-point space**
    that ``search`` reports and ``redact_regions`` consumes, so a caller can filter this list and
    pass what survives straight into a redaction without touching the numbers. That space is not a
    conversion this function performs — PyMuPDF stores annotation geometry unrotated and reports it
    unrotated, at every ``/Rotate`` (measured across 0/90/180/270), exactly as ``search_for`` does.
    It is pinned by a test rather than left as a happy accident.
    """
    kind, kind_name = annot.type[0], annot.type[1]
    name = kind_name.lower()          # 'StrikeOut' → 'strikeout', 'FreeText' → 'freetext'
    if kind in _QUAD_TYPES and annot.vertices:
        boxes = _quads_to_boxes(annot.vertices)
    else:
        r = annot.rect
        boxes = [[r.x0, r.y0, r.x1, r.y1]]
    stroke = (annot.colors or {}).get("stroke") or None
    color = [round(float(c), 4) for c in stroke] if stroke else None
    info = annot.info or {}
    author = info.get("title") or ""
    described = {
        "page": page1,
        "type": name,
        "boxes": [[round(v, 2) for v in box] for box in boxes],
        "color": color,
        # Advisory: the nearest swatch name, so a caller can filter on "orange" without doing colour
        # arithmetic. `color_exact` is the honest half — true only for a value that *is* a swatch,
        # which is what a mark made in this app carries and a mark made in Acrobat generally does
        # not. Both are null/false when the annotation stores no colour at all.
        "color_name": nearest_name(color, name) if color else None,
        "color_exact": bool(color) and is_palette_color(color, name),
        "note": info.get("content") or "",
        "author": author,
        # `mine` is the author tag this app writes; `editable` is whether the model can represent
        # the mark at all. They differ in both directions, which is why both are reported: a
        # foreign highlight is editable-but-not-mine (the app adopts it on double-click, M68),
        # while a sticky note is neither.
        "mine": author == KLARPDF_AUTHOR,
        "editable": parse_annotation(annot) is not None,
    }
    return described


def get_annotations(
    path: str,
    pages: list[int] | None = None,
    *,
    password: str | None = None,
    max_annotations: int = MAX_ANNOTATIONS,
) -> dict:
    """Every annotation on ``pages`` (default: all), in document order.

    Reports foreign marks alongside ours — a reviewer does not care who wrote a comment, and the
    caller of this tool is usually trying to read someone else's review. ``mine`` and ``editable``
    say which is which.
    """
    with open_document(path, password) as vdoc:
        indices = resolve_pages(vdoc, pages)
        found: list[dict] = []
        truncated = False
        for index0 in indices:
            page = _page_of(vdoc, index0)
            for annot in page.annots():
                if annot.type[0] in _NOT_MARKUP:
                    continue
                if len(found) >= max_annotations:
                    truncated = True
                    break
                found.append(_describe(annot, index0 + 1))
            if truncated:
                break
        result = {
            "count": len(found),
            "annotations": found,
            "pages_scanned": [i + 1 for i in indices],
            "source": os.path.abspath(path),
        }
        if truncated:
            result["truncated"] = True
            result["warnings"] = [
                f"more than {max_annotations} annotations; the listing stops there. "
                "Pass `pages` to read the document a part at a time."
            ]
        return result


def _resolve_color(raw, mark_type: str) -> tuple[float, float, float] | None:
    """A caller's ``color`` → RGB, or ``None`` to take the descriptor's own default.

    Accepts a swatch **name** from that type's palette, or a raw ``[r, g, b]`` triple of floats in
    0..1. A name outside the palette is an error naming the ones that exist rather than a silent
    fallback to the default — a caller who asked for an orange underline and got a red one has
    been told the wrong thing about their own document (M106's rule).
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        rgb = color_for_name(raw, mark_type)
        if rgb is None:
            available = ", ".join(names_for(mark_type))
            raise ValueError(
                f"{raw!r} is not a {mark_type} colour; this palette has {available}. "
                "Pass [r, g, b] floats in 0..1 for anything else."
            )
        return rgb
    if isinstance(raw, (list, tuple)):
        if len(raw) != 3:
            raise ValueError(f"color must be a name or [r, g, b]; got {list(raw)!r}")
        try:
            rgb = tuple(float(c) for c in raw)
        except (TypeError, ValueError):
            raise ValueError(f"color components must be numbers; got {list(raw)!r}") from None
        if any(c < 0.0 or c > 1.0 for c in rgb):
            raise ValueError(
                f"color components are 0..1, not 0..255; got {list(raw)!r}"
            )
        return rgb
    raise ValueError(f"color must be a palette name or [r, g, b]; got {raw!r}")


def _mark_boxes(mark: dict) -> list[tuple]:
    """The boxes of one requested mark, validated. Accepts ``box`` or ``boxes``, as
    ``redact_regions`` does — a ``search`` hit carries ``boxes`` and goes back in whole."""
    if "box" in mark and "boxes" in mark:
        raise ValueError(f"a mark takes 'box' or 'boxes', not both; got {mark!r}")
    if "box" not in mark and "boxes" not in mark:
        raise ValueError(f"each mark needs 'box' or 'boxes'; got {mark!r}")
    raw = [mark["box"]] if "box" in mark else list(mark["boxes"])
    if not raw:
        raise ValueError(f"mark {mark!r} carries no boxes")
    boxes = []
    for entry in raw:
        try:
            box = tuple(float(v) for v in entry)
        except (TypeError, ValueError):
            raise ValueError(f"box must be four numbers; got {entry!r}") from None
        if len(box) != 4:
            raise ValueError(f"box must be [x0, y0, x1, y1]; got {list(entry)!r}")
        if box[0] >= box[2] or box[1] >= box[3]:
            raise ValueError(f"box {list(box)} is empty or inverted")
        boxes.append(box)
    return boxes


def annotate(
    path: str,
    marks: list[dict],
    out: str,
    *,
    password: str | None = None,
    overwrite: bool = False,
) -> dict:
    """Write text markup onto a copy of ``path``.

    Each mark is ``{"type", "page", "boxes"}`` plus an optional ``color`` and ``note``. Every mark
    for the document goes in **one call**, because every call writes a file: three calls to lay
    three kinds of mark would leave two intermediates, which is the sprawl ``redact_text``'s
    ``queries`` exists to avoid.

    **A mark merges with what is already on the page rather than stacking on it.** This is
    ``merge_markup``, the same function the app's own markup tools call, so a mark written here and
    one drawn by hand behave identically: re-marking a span in the same colour is a no-op instead of
    a second overlapping annotation, extending one grows it in place, and a different colour takes
    the span over. Without this an agent that retried a call would leave two highlights where a
    reader sees one — and a caller filtering `get_annotations` by colour would then act on both.
    Notes survive the fold (M81.2): an absorbed mark's note is carried onto the survivor rather
    than destroyed by a call that deleted nothing.
    """
    if not isinstance(marks, list) or not marks:
        raise ValueError("no marks given — annotate must write something")
    target = _resolve_out(out, sources=[path], overwrite=overwrite)
    with open_document(path, password) as vdoc:
        touched: set[int] = set()
        before = {}
        for mark in marks:
            if not isinstance(mark, dict):
                raise ValueError(f"each mark must be an object; got {mark!r}")
            if "page" not in mark:
                raise ValueError(f"each mark needs 'page'; got {mark!r}")
            # A note with no type gets a Highlight to carry it — the app's own rule for a note
            # dropped on unmarked text (`resolve_note_host`), where attaching is the primary act
            # and only plain text creates anything.
            kind = str(mark.get("type") or "highlight").strip().lower()
            if kind not in MARK_TYPES:
                raise ValueError(
                    f"{kind!r} is not a mark type; this tool writes "
                    f"{', '.join(sorted(MARK_TYPES))}."
                )
            (index0,) = resolve_pages(vdoc, [mark["page"]])
            boxes = _mark_boxes(mark)
            mark_class = MARK_TYPES[kind]
            color = _resolve_color(mark.get("color"), kind)
            if color is None:
                color = mark_class.color            # the descriptor's own default
            note = str(mark.get("note") or "")
            before.setdefault(index0, len(vdoc.page_annotations(index0)))
            merged = merge_markup(vdoc.page_annotations(index0), boxes, mark_class, color)
            # `merge_markup` builds the survivor with no note, since it only ever inherits from the
            # marks it absorbed. A note asked for here is applied to that survivor afterwards, and
            # joins any it inherited rather than replacing them — the same "no note is lost unless
            # its mark was deleted" rule the app follows.
            if note:
                merged = _attach_note(merged, mark_class, boxes, note)
            vdoc.set_annotations(index0, merged)
            touched.add(index0)
        _write(vdoc, target)
        after = {i: len(vdoc.page_annotations(i)) for i in sorted(touched)}
        written = get_annotations(target, [i + 1 for i in sorted(touched)], password=password)
        return {
            "out": target,
            "source": os.path.abspath(path),
            "source_unchanged": True,
            "pages": vdoc.page_count,
            "bytes": os.path.getsize(target),
            "marks_requested": len(marks),
            "pages_annotated": [i + 1 for i in sorted(touched)],
            # The net change per page, which is what tells a caller a merge happened: ask for three
            # marks over one already-highlighted paragraph and this reports +1, not +3.
            "marks_added": sum(after[i] - before[i] for i in sorted(touched)),
            "annotations": written["annotations"],
        }


def _attach_note(annotations: tuple, mark_class, boxes, note: str) -> tuple:
    """``annotations`` with ``note`` set on the mark that ``merge_markup`` just produced.

    The survivor is the last mark of its type overlapping ``boxes`` — ``merge_markup`` inserts at
    the vacated slot of the topmost mark it absorbed, so identity is not enough to find it and
    geometry is. Joining rather than replacing an inherited note keeps M81's rule: a call that
    deleted nothing must not destroy typed text.
    """
    from dataclasses import replace

    from model.page_edits import _NOTE_JOIN, _x_overlap

    result = list(annotations)
    for i in range(len(result) - 1, -1, -1):
        mark = result[i]
        if isinstance(mark, mark_class) and any(
            _x_overlap(tuple(box), other) for box in boxes for other in mark.rects
        ):
            joined = _NOTE_JOIN.join([n for n in (mark.note, note) if n])
            result[i] = replace(mark, note=joined)
            break
    return tuple(result)
