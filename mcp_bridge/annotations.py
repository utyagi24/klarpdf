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

**M113 (the TC-012/TC-013 follow-ups)** fixed six things here without changing the shape above: a
re-run no longer duplicates its own note (:func:`_attach_note`); ``get_annotations`` paginates with
``offset`` instead of only capping, since a mark's JSON is 213-613 characters and a count alone let
a real reply reach 139,288 characters; a document whose permissions ask not to be annotated gets a
warning rather than silent compliance; each mark reports the text its boxes actually cover, so a box
built against the wrong coordinate origin (this module is top-left, y-down — see ``get_annotations``)
is visibly wrong instead of silently wrong; a mark overlapping one this app did not write says so
instead of claiming a merge that cannot happen; and ``annotate``'s echo is narrowed to the marks a
call actually touched rather than every mark already on the page.
"""

from __future__ import annotations

import json
import os
from collections import Counter

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
    _NOTE_JOIN,
    _x_overlap,
    merge_markup,
    parse_annotation,
)
from model.page_text import PageText
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

# How many characters of JSON a `get_annotations` reply may carry, beside the count cap (M113.2).
# `MAX_ANNOTATIONS` alone does not bound the reply: one mark's JSON runs 213-613 characters
# depending on its note, so 500 marks is anywhere from 107 KB to 300 KB. A real review copy hit
# 139,288 characters over 406 marks — under the count cap, over what the client receiving it could
# take. 60,000 is comfortably under that failure point while still holding a hundred-plus ordinary
# marks.
MAX_ANNOTATION_CHARS = 60_000

# Effectively unbounded. `annotate`'s own echo (see `annotate`) reads its target back through
# `get_annotations` and then narrows the result to the marks the call actually touched — so the
# caller-facing caps above must not clip that internal read first, or a page already holding a few
# hundred marks could crowd out the one or two this call just added.
_NO_CAP = 1_000_000

# The share of a batch's character budget one mark's note may take before it is cut (M118). A batch
# must always yield at least one mark or a correctly-paging caller never terminates — and M113 took
# that to mean a single mark could set an unbounded *floor* on reply size, which put the whole reply
# back over what a client accepts: **120,624 characters from one annotation**, the very harm the
# character budget was added to prevent, now reachable without needing 406 marks (TC-015). Whole
# marks are still never trimmed. What gets cut is the **note**, which is the only field that grows
# without bound, and the cut is disclosed per mark so a caller can go and read the rest.
#
# Half, so a cut mark still leaves room for others rather than monopolising the batch it sits in.
_NOTE_BUDGET_SHARE = 0.5

# What a truncated note ends with, so a reader sees the cut rather than a sentence stopping dead.
_NOTE_ELLIPSIS = " […]"


def _fit(entry: dict, budget: int) -> dict:
    """``entry`` with its ``note`` cut down if the entry alone would blow ``budget`` (M118).

    Returns the entry unchanged when it fits, which is the overwhelmingly common case. When it does
    not, the note — and only the note — is cut, because everything else a caller filters on is
    small and bounded: ``boxes``, ``color``, ``color_name``, ``page``, ``type``, the flags. A mark
    whose note was cut carries ``note_truncated: true`` and ``note_length``, the original character
    count, so the reply says plainly that there is more and how much.
    """
    if len(json.dumps(entry)) <= budget or not entry.get("note"):
        return entry
    original = entry["note"]
    # Budget the note against what the rest of the entry costs, so the arithmetic holds however the
    # other fields grow later.
    without_note = len(json.dumps({**entry, "note": ""}))
    room = int(budget * _NOTE_BUDGET_SHARE) - without_note - len(_NOTE_ELLIPSIS)
    kept = original[:max(room, 0)]
    return {
        **entry,
        "note": (kept + _NOTE_ELLIPSIS) if kept else _NOTE_ELLIPSIS.strip(),
        "note_truncated": True,
        "note_length": len(original),
    }


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


def _lines_of(boxes: list[tuple]) -> list[tuple]:
    """``boxes`` folded to one union box per text line (M118).

    Only for building a mark's ``snippet``. A mark's boxes are usually one per line, but they need
    not be: a highlight laid over a `search` hit's individual word boxes carries one quad *per word*,
    and snippetting each of thirteen word-boxes separately returned thirteen overlapping windows of
    the same sentence — **465 characters to describe 59** (TC-015), spending the very budget
    :func:`_fit` exists to defend. Merging to lines first gives one window per line, which is what
    the field was always meant to be.

    Same midpoint-containment rule as ``page_edits._same_line``, restated rather than imported
    because that one takes a pair and this needs to group a list.
    """
    lines: list[list[float]] = []
    for box in sorted(boxes, key=lambda b: (b[1], b[0])):
        mid = (box[1] + box[3]) / 2
        for line in lines:
            line_mid = (line[1] + line[3]) / 2
            if line[1] <= mid <= line[3] or box[1] <= line_mid <= box[3]:
                line[0], line[1] = min(line[0], box[0]), min(line[1], box[1])
                line[2], line[3] = max(line[2], box[2]), max(line[3], box[3])
                break
        else:
            lines.append(list(box))
    return [tuple(line) for line in lines]


def _describe(annot, page1: int, text: PageText) -> dict:
    """One raw annotation → the JSON an agent reads.

    ``boxes`` is the field that has to be right: it is in the same **unrotated page-point space**
    that ``search`` reports and ``redact_regions`` consumes, so a caller can filter this list and
    pass what survives straight into a redaction without touching the numbers. That space is not a
    conversion this function performs — PyMuPDF stores annotation geometry unrotated and reports it
    unrotated, at every ``/Rotate`` (measured across 0/90/180/270), exactly as ``search_for`` does.
    It is pinned by a test rather than left as a happy accident.

    That space also measures from the **top-left**, y increasing downward — the flip PyMuPDF applies
    on the way in and out of the PDF format's own bottom-left, y-up convention (``page.
    transformation_matrix``), applied uniformly enough that nothing here has ever needed to touch it.
    A box built against the wrong origin — by a caller reading the raw PDF, or another library — lands
    **mirrored about the page's horizontal axis**: valid, on the page, no error, wrong line (M113.4).
    ``snippet`` and ``text_length`` (below) exist to make that self-revealing instead of silent.
    """
    kind, kind_name = annot.type[0], annot.type[1]
    name = kind_name.lower()          # 'StrikeOut' → 'strikeout', 'FreeText' → 'freetext'
    if kind in _QUAD_TYPES and annot.vertices:
        boxes = _quads_to_boxes(annot.vertices)
    else:
        r = annot.rect
        boxes = [[r.x0, r.y0, r.x1, r.y1]]
    box_tuples = [tuple(box) for box in boxes]
    stroke = (annot.colors or {}).get("stroke") or None
    color = [round(float(c), 4) for c in stroke] if stroke else None
    info = annot.info or {}
    author = info.get("title") or ""
    described = {
        "page": page1,
        "type": name,
        "boxes": [[round(v, 2) for v in box] for box in boxes],
        # The text the mark's own boxes actually cover (M113.4) — `snippet` windowed for
        # readability the way `search`'s hits are, `text_length` the *un*windowed count, so a box
        # that is plausible-looking but covers three paragraphs instead of one line still shows up.
        # Both are empty/zero on a page with no text layer, which then reads the same as a wrong
        # box — a limit to know about, not a bug. Snippetting is per *line* rather than per box
        # (M118): a mark carrying one quad per word would otherwise repeat its own sentence once
        # per word.
        "snippet": text.snippet_for(_lines_of(box_tuples)),
        "text_length": len(text.text_under_all(box_tuples)),
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
    max_chars: int = MAX_ANNOTATION_CHARS,
    offset: int = 0,
) -> dict:
    """Every annotation on ``pages`` (default: all), in document order.

    Reports foreign marks alongside ours — a reviewer does not care who wrote a comment, and the
    caller of this tool is usually trying to read someone else's review. ``mine`` and ``editable``
    say which is which.

    **Paginated, not merely capped (M113.2).** A reply is bounded by both `max_annotations` and
    `max_chars` — a mark's JSON runs 213-613 characters depending on its note, so a count alone let
    a real 406-mark reply reach 139,288 characters. Whichever bound is hit first, whole marks are
    dropped rather than trimmed and `more_available` is set: call again with `offset` set to this
    reply's `offset + count` for the rest. `total_annotations` is the number in scope regardless of
    either cap, so a caller knows how many rounds to expect before starting. Unlike every other
    capped tool here, narrowing `pages` will not help when the marks that overflow the budget are
    on one page — pagination is the only lever, which is why this is the one tool that has it.
    """
    if offset < 0:
        raise ValueError(f"offset must be >= 0; got {offset}")
    with open_document(path, password) as vdoc:
        indices = resolve_pages(vdoc, pages)
        # Every annotation in scope is described before either cap is applied — `total_annotations`
        # has to be an honest count of what is actually there, not merely "more than the cap".
        all_found: list[dict] = []
        for index0 in indices:
            page = _page_of(vdoc, index0)
            text = None            # built lazily: most pages in a large scan carry no markup at all
            for annot in page.annots():
                if annot.type[0] in _NOT_MARKUP:
                    continue
                if text is None:
                    text = PageText(page)
                all_found.append(_describe(annot, index0 + 1, text))
        total = len(all_found)
        found: list[dict] = []
        used = 0
        for entry in all_found[offset:]:
            if len(found) >= max_annotations:
                break
            # A mark whose own note would blow the whole budget has the note cut rather than the
            # mark dropped (M118) — the batch must always yield at least one mark, and before this
            # that guarantee let one annotation set an unbounded floor on the reply's size.
            entry = _fit(entry, max_chars)
            size = len(json.dumps(entry))
            # Always take at least one entry: an empty batch with `more_available: true` is a
            # caller that pages forever.
            if found and used + size > max_chars:
                break
            found.append(entry)
            used += size
        more_available = offset + len(found) < total
        result = {
            "count": len(found),
            "total_annotations": total,
            "offset": offset,
            "annotations": found,
            "pages_scanned": [i + 1 for i in indices],
            "source": os.path.abspath(path),
            "more_available": more_available,
        }
        if more_available:
            result["warnings"] = [
                f"{total} annotations in scope; returned {len(found)} starting at offset {offset}. "
                f"Call again with offset: {offset + len(found)} for the rest. Narrowing `pages` "
                "will not help when this many are on one page — offset is the only lever."
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
            article = "an" if mark_type[0] in "aeiou" else "a"
            raise ValueError(
                f"{raw!r} is not {article} {mark_type} colour; this palette has {available}. "
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


_KIND_TO_PDF_TYPE = {
    "highlight": fitz.PDF_ANNOT_HIGHLIGHT,
    "underline": fitz.PDF_ANNOT_UNDERLINE,
    "strikeout": fitz.PDF_ANNOT_STRIKE_OUT,
}


def _permission_warning(vdoc) -> str | None:
    """A warning naming the document's own no-annotate preference, or ``None`` (M113.3).

    The flag is advisory — enforced by nothing but the file's own encryption, which has nothing to
    do with whether this call may run — so it changes nothing about *whether* marks get written,
    only whether the caller is told. `permissions == -1` is the model's own "unrestricted" value
    (`model/edit_engine.py`), which an ordinary unencrypted document also reports, so that case must
    not trip this.
    """
    if vdoc.permissions == -1 or vdoc.permissions & fitz.PDF_PERM_ANNOTATE:
        return None
    return (
        "this document's permissions ask readers not to annotate it (the ANNOTATE bit is unset). "
        "That restriction is advisory and unrelated to the encryption password, so the marks were "
        "written anyway — tell the user before they share the output."
    )


def _foreign_authors_over(page, kind: str, boxes) -> list[str]:
    """Authors of existing non-KlarPDF marks of ``kind`` overlapping ``boxes`` on ``page`` (M113.5).

    A foreign mark never enters ``vdoc.page_annotations()`` — the model's own editable set is
    filtered to this app's author tag when a document is opened — so ``merge_markup`` cannot see it
    and a new mark laid over one is neither absorbed nor trimmed. That is correct: merging deletes a
    mark, and silently deleting a reviewer's annotation to reattribute its span would be worse than
    a duplicate. The caller still deserves to know what is now sitting on top of what.
    """
    wanted = _KIND_TO_PDF_TYPE[kind]
    found = []
    for annot in page.annots():
        if annot.type[0] != wanted or not annot.vertices:
            continue
        info = annot.info or {}
        if (info.get("title") or "") == KLARPDF_AUTHOR:
            continue
        existing = _quads_to_boxes(annot.vertices)
        if any(_x_overlap(tuple(box), tuple(other)) for box in boxes for other in existing):
            found.append(info.get("title") or "unknown")
    return found


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
    one drawn by hand **in KlarPDF** behave identically: re-marking a span in the same colour is a
    no-op instead of a second overlapping annotation, extending one grows it in place, and a
    different colour takes the span over. Without this an agent that retried a call would leave two
    highlights where a reader sees one — and a caller filtering `get_annotations` by colour would
    then act on both. Notes survive the fold (M81.2): an absorbed mark's note is carried onto the
    survivor rather than destroyed by a call that deleted nothing, and a note passed to a call whose
    mark merges is never attached twice, even across a retried call (M113.1).

    **Merging never crosses authorship (M113.5).** A mark made in Acrobat, Edge or Preview is never
    absorbed or trimmed — merging deletes a mark, and deleting a reviewer's would be worse than a
    duplicate — so a new mark laid over one is simply added beside it, and the reply's `warnings`
    says so when it happens. `annotations` in the reply is narrowed to the marks *this call* wrote
    or merged into, not every mark already on the pages touched (M113.2).
    """
    if not isinstance(marks, list) or not marks:
        raise ValueError("no marks given — annotate must write something")
    target = _resolve_out(out, sources=[path], overwrite=overwrite)
    with open_document(path, password) as vdoc:
        touched: set[int] = set()
        before = {}
        requested_boxes: dict[tuple[int, str], list] = {}
        foreign_overlaps: list[str] = []
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
            foreign_overlaps.extend(_foreign_authors_over(_page_of(vdoc, index0), kind, boxes))
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
            requested_boxes.setdefault((index0, kind), []).extend(boxes)
        permission_warning = _permission_warning(vdoc)
        _write(vdoc, target)
        after = {i: len(vdoc.page_annotations(i)) for i in sorted(touched)}
        # Read the written file back uncapped, then narrow to the marks this call actually touched
        # (M113.2) — otherwise a page that already held eighty marks would echo all eighty-one back
        # for one mark added, rather than the one that changed.
        full = get_annotations(
            target, [i + 1 for i in sorted(touched)], password=password,
            max_annotations=_NO_CAP, max_chars=_NO_CAP,
        )["annotations"]
        touched_annotations = [
            a for a in full
            if any(
                _x_overlap(tuple(box), tuple(other))
                for box in requested_boxes.get((a["page"] - 1, a["type"]), [])
                for other in a["boxes"]
            )
        ]
        result = {
            "out": target,
            "source": os.path.abspath(path),
            "source_unchanged": True,
            "pages": vdoc.page_count,
            "bytes": os.path.getsize(target),
            "marks_requested": len(marks),
            "pages_annotated": [i + 1 for i in sorted(touched)],
            # The net change per page, which is what tells a caller a merge happened: ask for three
            # marks over one already-highlighted paragraph and this reports +1, not +3. It can also
            # be negative — a mark bridging two existing ones into one net removes an annotation.
            "marks_added": sum(after[i] - before[i] for i in sorted(touched)),
            "annotations": touched_annotations,
        }
        warnings = []
        if permission_warning:
            warnings.append(permission_warning)
        if foreign_overlaps:
            counts = Counter(foreign_overlaps)
            names = ", ".join(f"{author} x{n}" if n > 1 else author for author, n in counts.items())
            warnings.append(
                f"{len(foreign_overlaps)} of the requested marks overlap an existing mark not made "
                f"by KlarPDF ({names}). Merging only ever folds together marks this app wrote, so "
                "both are now on the page rather than one replacing the other."
            )
        if warnings:
            result["warnings"] = warnings
        return result


def _already_present(existing: str, note: str) -> bool:
    """Does ``existing`` already carry ``note``, as whole segments? (M113.1, corrected M118.)

    Notes are joined with :data:`_NOTE_JOIN`, so both sides are read as **lists of segments** and
    the question is whether ``note``'s segments appear as a **contiguous run** of ``existing``'s.

    That framing is the fix for the case M113.1 could not express. Testing membership of a single
    segment — ``note in existing.split(_NOTE_JOIN)`` — is exact only while a note *is* one segment;
    a note that itself contains a blank line splits into several, matches nothing, and was appended
    again on every re-run, growing the note without bound while ``marks_added: 0`` reported that
    nothing had changed (TC-015). A multi-paragraph review comment is an ordinary thing to write.

    Comparing runs of segments keeps the property that made the original right: ``"check"`` against
    an existing ``"check the totals"`` is one segment against another and does **not** match, so it
    is appended rather than swallowed as a substring.
    """
    if not note:
        return True                       # nothing to add
    have, want = existing.split(_NOTE_JOIN), note.split(_NOTE_JOIN)
    return any(
        have[i:i + len(want)] == want for i in range(len(have) - len(want) + 1)
    )


def _attach_note(annotations: tuple, mark_class, boxes, note: str) -> tuple:
    """``annotations`` with ``note`` set on the mark that ``merge_markup`` just produced.

    The survivor is the last mark of its type overlapping ``boxes`` — ``merge_markup`` inserts at
    the vacated slot of the topmost mark it absorbed, so identity is not enough to find it and
    geometry is. Joining rather than replacing an inherited note keeps M81's rule: a call that
    deleted nothing must not destroy typed text.

    **A note already present is not joined again (M113.1).** ``merge_markup`` carries an absorbed
    mark's note onto the survivor, so without this a re-run of the same call would see its own note
    twice: once inherited from the merge, once attached fresh right here. See
    :func:`_already_present` for how "already there" is decided, and why it is decided over segment
    *runs* rather than over one segment.
    """
    from dataclasses import replace

    result = list(annotations)
    for i in range(len(result) - 1, -1, -1):
        mark = result[i]
        if isinstance(mark, mark_class) and any(
            _x_overlap(tuple(box), other) for box in boxes for other in mark.rects
        ):
            if _already_present(mark.note, note):
                break
            joined = _NOTE_JOIN.join([n for n in (mark.note, note) if n])
            result[i] = replace(mark, note=joined)
            break
    return tuple(result)
