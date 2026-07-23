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
    """Enumerate fillable widgets across the document's pages, in current page order.

    Includes fields the user has **placed but not yet saved** (M69 :class:`~model.form_fields.
    NewField`): they only become real widgets at materialise, but the whole point of the milestone is
    that a placed field behaves like any other, so the inline filler has to see it straight away.
    """
    from model.form_fields import NewField, widget_type

    fields: list[FormField] = []
    for page_index in range(vdoc.page_count):
        ref = vdoc.ordered[page_index]
        page = vdoc.sources[ref.source_id][ref.source_page_index]
        for mark in ref.annotations:
            if isinstance(mark, NewField) and mark.name:
                fields.append(
                    FormField(
                        name=mark.name,
                        type=widget_type(mark.kind),
                        type_string=mark.kind,
                        page_index=page_index,
                        rect=mark.rect,
                        choices=mark.options or None,
                        current_value=mark.value,
                    )
                )
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
class InkStroke:
    """A freehand pen mark: one or more point paths, fixed width (M57).

    PDF ink carries no pressure, so the width is uniform by design (PLAN.md M58). ``paths`` is a
    tuple of paths, each a tuple of ``(x, y)`` points in unrotated page points — one descriptor
    holds one drawn gesture (possibly multi-path later; the M58 pen commits one path per stroke).
    """

    paths: tuple[tuple[tuple[float, float], ...], ...]
    color: tuple[float, float, float] = (0.86, 0.10, 0.10)
    width: float = 2.0
    opacity: float = 1.0
    dashed: bool = False   # dashed stroke (a PDF /BS /D border), else solid

    def bounding_rect(self) -> tuple[float, float, float, float]:
        xs = [p[0] for path in self.paths for p in path]
        ys = [p[1] for path in self.paths for p in path]
        return (min(xs), min(ys), max(xs), max(ys))


@dataclass(frozen=True)
class Line:
    """A straight line, optionally arrow-ended (M57). Arrow heads are open arrows — booleans in
    the model (we write one style); any non-plain PDF end style reads back as ``True``. Since M74
    the ends are *style* (Preview's model — set from the picker, restyled in place like colour),
    which is also what made a both-ended line drawable; a pre-R6 Arrow reads back as
    ``Line(arrow_end=True)`` unchanged."""

    start: tuple[float, float]
    end: tuple[float, float]
    color: tuple[float, float, float] = (0.86, 0.10, 0.10)
    width: float = 2.0
    arrow_start: bool = False
    arrow_end: bool = False
    opacity: float = 1.0
    dashed: bool = False   # dashed stroke (a PDF /BS /D border), else solid

    def bounding_rect(self) -> tuple[float, float, float, float]:
        return (
            min(self.start[0], self.end[0]),
            min(self.start[1], self.end[1]),
            max(self.start[0], self.end[0]),
            max(self.start[1], self.end[1]),
        )


@dataclass(frozen=True)
class Shape:
    """A rectangle or ellipse (M57). ``kind`` is ``"rect"`` or ``"ellipse"``; ``fill_color``
    ``None`` leaves the interior transparent (outline only)."""

    kind: str
    rect: tuple[float, float, float, float]
    color: tuple[float, float, float] = (0.86, 0.10, 0.10)
    width: float = 2.0
    fill_color: tuple[float, float, float] | None = None
    # Constant opacity for the whole mark (PDF ``/CA``), 0..1. PDF applies it to outline *and*
    # fill together — there is no fill-only alpha on an annotation — so a translucent fill means
    # a translucent mark. This is the lever for "my filled box hides the text underneath":
    # annotations always paint above the page content, so z-order can never put one behind text.
    opacity: float = 1.0
    dashed: bool = False   # dashed outline (a PDF /BS /D border), else solid

    def bounding_rect(self) -> tuple[float, float, float, float]:
        return self.rect


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


# The descriptors the object clipboard / move tools operate on (M58 move, M59 copy/paste):
# free-placed geometry that stays meaningful anywhere on any page. The text-anchored marks
# (highlight / underline / strikeout) and redactions are deliberately excluded — they belong to
# the text under them; foreign annotations are excluded until M68. The R4 content marks (M61) join
# the list: they are free-placed rects, so move / resize / copy come from the same primitives even
# though they bake into page content rather than staying annotations.
PLACEABLE_TYPES = ("TextBox", "InkStroke", "Line", "Shape", "Stamp", "ImageStamp", "NewField")


def translate_mark(mark, dx: float, dy: float):
    """The same descriptor moved by ``(dx, dy)`` — the one move primitive for every free-placed
    geometry (M58 drag-move, M59 paste-with-offset). Frozen value objects in, new ones out."""
    from dataclasses import replace

    from model.content_marks import CONTENT_MARK_TYPES
    from model.form_fields import NewField

    if isinstance(mark, (TextBox, Shape, NewField) + CONTENT_MARK_TYPES):
        x0, y0, x1, y1 = mark.rect
        return replace(mark, rect=(x0 + dx, y0 + dy, x1 + dx, y1 + dy))
    if isinstance(mark, Line):
        return replace(mark, start=(mark.start[0] + dx, mark.start[1] + dy),
                       end=(mark.end[0] + dx, mark.end[1] + dy))
    if isinstance(mark, InkStroke):
        return replace(mark, paths=tuple(tuple((x + dx, y + dy) for x, y in path)
                                         for path in mark.paths))
    raise TypeError(f"not a movable mark: {type(mark).__name__}")


def mark_bounds(mark) -> tuple:
    """A free-placed mark's bounding rect: a text box's own rect, else ``bounding_rect()``."""
    return mark.rect if isinstance(mark, TextBox) else mark.bounding_rect()


# A page's annotation tuple **is** its z-order: later entries paint on top (both in the viewer
# overlay and in the saved PDF, since :func:`apply_annotations` writes them in order), and the
# hit-tests walk it reversed so the topmost wins. So "bring to front" is just a list reorder —
# no new state, and it moves paint order and click order together, which is what users expect.
Z_ORDER_ACTIONS = ("front", "forward", "backward", "back")


def _mark_positions(annotations: tuple, marks) -> list:
    """Where each of ``marks`` sits in ``annotations`` — identity first, then the first unclaimed
    value-equal entry (frozen value objects can be handed back as equal-but-distinct copies), so a
    reorder never silently skips a mark. Unfound marks are dropped."""
    used: set = set()
    found = []
    for mark in marks:
        position = next((i for i, a in enumerate(annotations) if i not in used and a is mark), -1)
        if position < 0:
            position = next((i for i, a in enumerate(annotations) if i not in used and a == mark), -1)
        if position >= 0:
            used.add(position)
            found.append(position)
    return found


def reorder_marks(annotations: tuple, marks, action: str) -> tuple:
    """``annotations`` with ``marks`` moved in z-order (M59.8).

    ``front`` / ``back`` jump the whole set to the top / bottom, keeping the moved marks in their
    existing relative order; ``forward`` / ``backward`` step each one past its nearest unselected
    neighbour (walking from the far end, so a run of selected marks shifts together instead of
    piling up). Returns the tuple unchanged when there is nothing to do.
    """
    positions = set(_mark_positions(annotations, marks))
    if not positions or action not in Z_ORDER_ACTIONS:
        return annotations
    items = list(annotations)
    if action in ("front", "back"):
        moved = [items[i] for i in sorted(positions)]
        rest = [a for i, a in enumerate(items) if i not in positions]
        return tuple(rest + moved if action == "front" else moved + rest)
    step = 1 if action == "forward" else -1
    # Walk from the end the marks are heading towards, so each moves at most one place and a
    # contiguous run keeps its shape.
    sweep = range(len(items) - 2, -1, -1) if step > 0 else range(1, len(items))
    live = set(positions)
    for i in sweep:
        j = i + step
        if i in live and j not in live:
            items[i], items[j] = items[j], items[i]
            live.discard(i)
            live.add(j)
    return tuple(items)


# ---- text-markup merge (M59.10): one layer per type, per region ----------------
#
# Highlight / underline / strikeout are *paint on text*, not stacked objects: marking a span that
# is already marked must fold into what is there, never add a second descriptor on top of it. Two
# copies looked identical but took two Removes to clear (the hit-test returns the topmost only),
# and a re-colour left the old colour hidden underneath.
#
# The merge is scoped **per type** — a yellow highlight and a red underline on the same words are
# independent layers and stay so — and runs at the granularity the marks are already stored in:
# one unioned bar per text line (see MainWindow._selection_line_bars). So resolving an overlap is
# 1-D interval arithmetic on x *within* a line band, not 2-D clipping.

MARKUP_TYPES = (Highlight, Underline, Strikeout)

# A surviving sliver narrower than this is whitespace, not a mark — dropped rather than left as a
# hairline the user can see but not easily hit.
_MIN_BAR_WIDTH = 1.0
# Float-noise slack for "these bars touch" / "this is the same colour". Colours matter because a
# saved-and-reopened mark comes back through PDF floats (read_klarpdf_annotations), so an exact
# == against a palette tuple would miss and wrongly treat a re-highlight as a colour change.
_TOL = 0.01


def _same_line(a: tuple, b: tuple) -> bool:
    """Do two bars sit on the same text line? Midpoint-containment either way, rather than equal
    y-bounds: a partial selection can omit a tall word and shrink the band it produces."""
    a_mid, b_mid = (a[1] + a[3]) / 2, (b[1] + b[3]) / 2
    return b[1] <= a_mid <= b[3] or a[1] <= b_mid <= a[3]


def _x_overlap(a: tuple, b: tuple) -> bool:
    return _same_line(a, b) and a[0] < b[2] - _TOL and b[0] < a[2] - _TOL


def _same_color(a: tuple, b: tuple) -> bool:
    return len(a) == len(b) and all(abs(x - y) <= _TOL for x, y in zip(a, b))


def _union_bars(bars) -> tuple:
    """Fold bars into the fewest rects: same-line ones that overlap or touch become one, each
    taking the union y-band so a taller word in either pass keeps its coverage. Bars separated by
    a real gap stay separate rects **of the same mark** — the gap is whitespace the user did not
    select, and one mark is all it takes for one Remove to clear the lot."""
    merged: list[list] = []
    for bar in sorted(bars, key=lambda r: (r[1], r[0])):
        for other in merged:
            if _same_line(bar, other) and bar[0] <= other[2] + _TOL and other[0] <= bar[2] + _TOL:
                other[0], other[1] = min(other[0], bar[0]), min(other[1], bar[1])
                other[2], other[3] = max(other[2], bar[2]), max(other[3], bar[3])
                break
        else:
            merged.append(list(bar))
    return tuple(tuple(bar) for bar in merged)


def _subtract_bars(bars, cutters) -> tuple:
    """``bars`` with every ``cutters`` span cut out of it, per line. A cut through the middle
    leaves two runs (the mark splits around the new colour); a fully covered bar disappears."""
    result = []
    for bar in bars:
        pieces = [(bar[0], bar[2])]
        for cutter in cutters:
            if not _same_line(bar, cutter):
                continue
            nxt = []
            for x0, x1 in pieces:
                if cutter[2] <= x0 or cutter[0] >= x1:
                    nxt.append((x0, x1))
                    continue
                if cutter[0] > x0:
                    nxt.append((x0, cutter[0]))
                if cutter[2] < x1:
                    nxt.append((cutter[2], x1))
            pieces = nxt
        result.extend((x0, bar[1], x1, bar[3]) for x0, x1 in pieces if x1 - x0 >= _MIN_BAR_WIDTH)
    return tuple(result)


def merge_markup(annotations: tuple, bars, mark_type, color: tuple) -> tuple:
    """A page's annotations with ``bars`` painted on as ``mark_type`` in ``color`` (M59.10).

    Returns the whole new tuple (pushed as one :class:`SetAnnotationsCommand`), so a pass that
    absorbs, trims and adds is a single undo step. Same-type marks the new bars touch resolve by
    colour:

    * **same colour → absorbed** — the old mark is dropped and its bars folded into the new one,
      so re-marking an already-marked span is a no-op and extending one grows it in place. Chains:
      a pass that bridges two same-colour marks merges all three into one.
    * **different colour → trimmed** — the covered span is cut out of the old mark and the new
      colour takes it. Full coverage removes the old mark outright; a cut through its middle
      splits it, leaving the parts you did *not* select in their original colour.

    The merged mark inherits the earliest absorbed mark's z-position (else it is appended), so
    re-marking never shuffles paint order against a co-located mark of another type. Other types
    and foreign annotations are passed through untouched.
    """
    from dataclasses import replace

    new_bars = tuple(tuple(bar) for bar in bars)
    if not new_bars:
        return annotations
    absorbed = list(new_bars)      # grows as same-colour marks are folded in
    result: list = []
    insert_at = None
    for mark in annotations:
        if not isinstance(mark, mark_type) or not any(
            _x_overlap(bar, other) for bar in mark.rects for other in absorbed
        ):
            result.append(mark)
            continue
        if _same_color(mark.color, color):
            absorbed.extend(mark.rects)
            if insert_at is None:
                insert_at = len(result)          # take over the topmost slot we vacate
            continue
        # Only what the user *just* painted erases — absorbed geometry was already on the page.
        trimmed = _subtract_bars(mark.rects, new_bars)
        if trimmed:
            result.append(replace(mark, rects=trimmed))
    merged = mark_type(_union_bars(absorbed), color=color)
    result.insert(len(result) if insert_at is None else insert_at, merged)
    return tuple(result)


def remove_markup(annotations: tuple, bars, mark_type) -> tuple:
    """A page's annotations with ``mark_type`` **erased over** ``bars`` — the removal half of the
    M59.10 merge, for the M76 markup context menu ("remove one layer leaving the other").

    Covered same-type marks are trimmed by exactly the span the bars cover (the same
    :func:`_subtract_bars` cut merge's different-colour path uses): full coverage drops the mark,
    a cut through the middle splits it. Other types and foreign annotations pass through
    untouched. Returns the whole new tuple (pushed as one ``SetAnnotationsCommand`` → one undo
    step), unchanged (``is``-comparable by equality) when nothing overlapped.
    """
    from dataclasses import replace

    erase = tuple(tuple(bar) for bar in bars)
    if not erase:
        return annotations
    result: list = []
    for mark in annotations:
        if not isinstance(mark, mark_type) or not any(
            _x_overlap(bar, cutter) for bar in mark.rects for cutter in erase
        ):
            result.append(mark)
            continue
        trimmed = _subtract_bars(mark.rects, erase)
        if trimmed:
            result.append(replace(mark, rects=trimmed))
    return tuple(result)


def marks_over(annotations: tuple, bars, mark_type) -> list:
    """The ``mark_type`` marks among ``annotations`` overlapping any of ``bars`` — the existence /
    tick-state query behind the M76 layer toggles (same overlap test the merge machinery uses)."""
    probe = tuple(tuple(bar) for bar in bars)
    return [
        mark for mark in annotations
        if isinstance(mark, mark_type) and any(
            _x_overlap(bar, other) for bar in mark.rects for other in probe
        )
    ]


def scale_mark(mark, sx: float, sy: float, ox: float, oy: float):
    """A mark scaled by ``(sx, sy)`` about the origin ``(ox, oy)`` — the M59.7 resize primitive,
    the geometry twin of :func:`translate_mark`. Frozen value objects in, new ones out.

    A :class:`TextBox` is **repositioned, not resized**: its top-left scales with the group so it
    travels along, but its box keeps its size — a text box hugs its text, so its dimensions are
    really a function of the text + font size (owned by the format bar), and stretching the box
    alone would just detach it from its content. Anything else unrecognised returns ``None``.

    An R4 content mark (:class:`~model.content_marks.Stamp` /
    :class:`~model.content_marks.ImageStamp`) **does** stretch, like a shape: its rect is the box the
    artwork is fitted into, so scaling the rect is exactly how you resize a stamp.
    """
    from dataclasses import replace

    from model.content_marks import CONTENT_MARK_TYPES
    from model.form_fields import NewField

    def point(x: float, y: float) -> tuple:
        return (ox + (x - ox) * sx, oy + (y - oy) * sy)

    if isinstance(mark, (Shape, NewField) + CONTENT_MARK_TYPES):
        x0, y0, x1, y1 = mark.rect
        nx0, ny0 = point(x0, y0)
        nx1, ny1 = point(x1, y1)
        box = (min(nx0, nx1), min(ny0, ny1), max(nx0, nx1), max(ny0, ny1))
        # A stamp at a **pinned** font size is a box shaped *by* its text, so it cannot simply take
        # whatever rectangle a corner-drag produces: stretching one axis would leave the artwork
        # sitting in a box the wrong shape for it, which reads as the stamp distorting. Instead the
        # drag sets the **size** — the smaller axis governs, so it never grows past what was asked
        # — and the box is re-derived from it, keeping the hug exact at every step.
        if getattr(mark, "fontsize", 0.0):
            from model.content_marks import placement_size

            resized = replace(mark, fontsize=mark.fontsize * min(abs(sx), abs(sy)))
            width, height = placement_size(resized)
            # Anchor the corner the drag is pulling *against* (the one at the scale origin), so the
            # stamp grows away from the handle the user is holding rather than sliding under it.
            x0 = box[0] if abs(box[0] - ox) <= abs(box[2] - ox) else box[2] - width
            y0 = box[1] if abs(box[1] - oy) <= abs(box[3] - oy) else box[3] - height
            return replace(resized, rect=(x0, y0, x0 + width, y0 + height))
        return replace(mark, rect=box)
    if isinstance(mark, Line):
        return replace(mark, start=point(*mark.start), end=point(*mark.end))
    if isinstance(mark, InkStroke):
        return replace(mark, paths=tuple(tuple(point(x, y) for x, y in path)
                                         for path in mark.paths))
    if isinstance(mark, TextBox):
        x0, y0, x1, y1 = mark.rect
        nx0, ny0 = point(x0, y0)                      # move with the group, keep the box's size
        return replace(mark, rect=(nx0, ny0, nx0 + (x1 - x0), ny0 + (y1 - y0)))
    return None


def restyle_mark(mark, color: tuple, width: float, fill_color: tuple | None,
                 opacity: float = 1.0, line_ends: tuple[bool, bool] | None = None,
                 dashed: bool | None = None):
    """A drawn mark re-coloured / re-widthed (and, for shapes, re-filled) in place — the M59.5
    "restyle the selected object" primitive, the style twin of :func:`translate_mark`. Only the
    drawn types carry a shared stroke style; a :class:`TextBox` (its own font/fill/border live in
    the format bar) or anything else returns ``None`` — nothing to restyle this way. ``fill_color``
    is ignored for the fill-less :class:`Line` / :class:`InkStroke`; ``line_ends`` (M74 —
    ``(arrow_start, arrow_end)``) applies to a :class:`Line` only; both ``line_ends`` and
    ``dashed`` (the solid/dashed stroke) keep the mark's current value when ``None``."""
    from dataclasses import replace

    dash = mark.dashed if dashed is None else dashed
    if isinstance(mark, Shape):
        return replace(mark, color=color, width=width, fill_color=fill_color, opacity=opacity,
                       dashed=dash)
    if isinstance(mark, Line):
        ends = line_ends if line_ends is not None else (mark.arrow_start, mark.arrow_end)
        return replace(mark, color=color, width=width, opacity=opacity,
                       arrow_start=ends[0], arrow_end=ends[1], dashed=dash)
    if isinstance(mark, InkStroke):
        return replace(mark, color=color, width=width, opacity=opacity, dashed=dash)
    return None


def _dash_array(width: float) -> list[int]:
    """The PDF dash pattern (``/BS /D``) for a dashed stroke of ``width`` points — dash then gap,
    scaled to the width so a thick line dashes as boldly as a thin one dashes finely. **Integer**
    values: PyMuPDF's ``set_border`` silently writes no ``/D`` array for float dashes (verified),
    so a float pattern would round-trip as *solid*. Kept in step with the viewer's Qt dash pattern
    (units of pen width) so the preview matches the baked mark."""
    return [max(2, round(width * 3)), max(2, round(width * 2))]


def _set_stroke_border(annot, width: float, dashed: bool) -> None:
    """Set a drawn mark's border — width, plus a dash pattern (``/BS /D``) when ``dashed``.
    PyMuPDF bakes the dashes into the annotation's border style and reads them back on reopen, so
    the solid/dashed choice round-trips without any extra model state beyond the boolean."""
    if dashed:
        annot.set_border(width=width, dashes=_dash_array(width))
    else:
        annot.set_border(width=width)


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
        elif isinstance(annotation, InkStroke):
            annot = page.add_ink_annot([list(path) for path in annotation.paths])  # seq of (x, y)
            annot.set_colors(stroke=annotation.color)
            _set_stroke_border(annot, annotation.width, annotation.dashed)
            annot.set_opacity(annotation.opacity)
            annot.set_info(title=KLARPDF_AUTHOR)
            annot.update()
        elif isinstance(annotation, Line):
            annot = page.add_line_annot(fitz.Point(annotation.start), fitz.Point(annotation.end))
            if annotation.arrow_start or annotation.arrow_end:
                annot.set_line_ends(
                    fitz.PDF_ANNOT_LE_OPEN_ARROW if annotation.arrow_start else fitz.PDF_ANNOT_LE_NONE,
                    fitz.PDF_ANNOT_LE_OPEN_ARROW if annotation.arrow_end else fitz.PDF_ANNOT_LE_NONE,
                )
            annot.set_colors(stroke=annotation.color)
            _set_stroke_border(annot, annotation.width, annotation.dashed)
            annot.set_opacity(annotation.opacity)
            annot.set_info(title=KLARPDF_AUTHOR)
            annot.update()
        elif isinstance(annotation, Shape):
            add = page.add_rect_annot if annotation.kind == "rect" else page.add_circle_annot
            annot = add(fitz.Rect(annotation.rect))
            annot.set_colors(stroke=annotation.color, fill=annotation.fill_color)
            _set_stroke_border(annot, annotation.width, annotation.dashed)
            annot.set_opacity(annotation.opacity)
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


def _stroke_color(annot, default: tuple) -> tuple:
    """An annot's stroke colour as a plain tuple, or ``default`` when unset (M57 read-back —
    style via ``annot.colors``, no DA parsing)."""
    stroke = annot.colors.get("stroke")
    return tuple(stroke) if stroke else default


def _opacity(annot) -> float:
    """An annot's constant opacity (``/CA``) as 0..1, defaulting to fully opaque. PyMuPDF reports
    an unset ``/CA`` as a negative sentinel, so anything outside the range means "not set"."""
    try:
        value = float(annot.opacity)
    except (TypeError, ValueError):
        return 1.0
    return value if 0.0 <= value <= 1.0 else 1.0


def _border_width(annot) -> float:
    """An annot's border width, defaulting to the drawn-mark default when unset."""
    width = (annot.border or {}).get("width")
    return float(width) if width and width > 0 else 2.0


def _dashed(annot) -> bool:
    """Whether an annot's border carries a dash pattern (``/BS /D``) — the round-trip read of the
    solid/dashed stroke. Presence of any dash array is enough; the exact array is re-derived from
    the width at bake, so only the boolean is modeled."""
    return bool((annot.border or {}).get("dashes"))


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
    return tuple(
        parsed
        for annot in page.annots()
        if annot.info.get("title") == KLARPDF_AUTHOR
        for parsed in (parse_annotation(annot),)
        if parsed is not None
    )


def parse_annotation(annot: fitz.Annot):
    """One annotation → its model descriptor, or ``None`` for a type the model does not represent.

    Split out of :func:`read_klarpdf_annotations` so **M68's adopt-on-edit** can reuse exactly the
    same parsing for a *foreign* annotation. The two callers differ only in which annotations they
    offer it: the round-trip path filters to our own author tag first, adoption passes a foreign one
    deliberately. Keeping one parser is what stops an adopted mark and a round-tripped one drifting
    apart.
    """
    doc = annot.parent.parent
    result: list = []
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
    elif kind == fitz.PDF_ANNOT_INK:
        paths = tuple(tuple((p[0], p[1]) for p in path) for path in annot.vertices)
        result.append(InkStroke(paths, color=_stroke_color(annot, InkStroke.color),
                                width=_border_width(annot), opacity=_opacity(annot),
                                dashed=_dashed(annot)))
    elif kind == fitz.PDF_ANNOT_LINE:
        start, end = annot.vertices[0], annot.vertices[1]
        ends = annot.line_ends or (fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_NONE)
        result.append(
            Line(
                (start[0], start[1]),
                (end[0], end[1]),
                color=_stroke_color(annot, Line.color),
                width=_border_width(annot),
                arrow_start=ends[0] != fitz.PDF_ANNOT_LE_NONE,
                arrow_end=ends[1] != fitz.PDF_ANNOT_LE_NONE,
                opacity=_opacity(annot),
                dashed=_dashed(annot),
            )
        )
    elif kind in (fitz.PDF_ANNOT_SQUARE, fitz.PDF_ANNOT_CIRCLE):
        # The /Rect grows by the border width on each side when the appearance is baked
        # (mirroring the FreeText inset above) — inset to recover the authored shape box, or
        # the shape creeps outward on every save→reopen→save cycle.
        width = _border_width(annot)
        r = annot.rect
        inset = width / 2.0
        rect = (r.x0 + inset, r.y0 + inset, r.x1 - inset, r.y1 - inset)
        fill = annot.colors.get("fill")
        result.append(
            Shape(
                "rect" if kind == fitz.PDF_ANNOT_SQUARE else "ellipse",
                rect,
                color=_stroke_color(annot, Shape.color),
                width=width,
                fill_color=tuple(fill) if fill else None,
                opacity=_opacity(annot),
                dashed=_dashed(annot),
            )
        )
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
    return result[0] if result else None


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
