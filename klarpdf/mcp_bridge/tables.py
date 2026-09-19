"""M141 — ``get_tables``: rows an agent can read, checked against the page before they are returned.

**The design in one paragraph.** PyMuPDF finds *where* a table is. It does not decide what the cells
say. For a drawn grid, the drawn cells are the cells. For a table whose rows are ruled or shaded but
whose columns are not drawn — the shape of nearly every financial statement — the rows come from the
drawn lines and the columns come from the page's own whitespace: the vertical gaps that no line of
text crosses. Every cell is built from whole text lines, so no word can be cut. Then, before a table
is returned, it is compared with the page, and a table that disagrees with the page is **declined**
into ``unread_regions`` with the reason, never repaired into something plausible.

**Why the columns are not PyMuPDF's** (the first M141 attempt, PR #348, used them). PyMuPDF infers a
row-ruled table's columns from words that line up, which needs ten aligned words per boundary. That
single inference was behind much of thirteen rounds of text damage: a boundary through Atlassian's
labels (``"Marketable sec"`` + ``"urities"``), through Amazon's ``(69,013)``, and no boundary at all
between two narrow columns of a three-row statement. Measured over a 40-document corpus, only four
row-ruled tables not also found by the grid reader came out consistent with their page when read
that way. With whitespace columns there is nothing to repair, and the corpus check holds the same
statements to fixed expectations.

**Why nothing here repairs content.** The first attempt's repairs each decided what a cell *should*
have said, and each was an assumption about how the damage looks; at least three of the five were
beaten by a later document. What replaced them is a set of checks that compare the table with the
page and give a yes or no — the only kind of rule that never failed in those rounds:

* every word printed inside the table's box is in exactly one cell, once
* no cell holds text the table's own box excludes
* no text runs across a drawn line that is supposed to separate cells
* no cell holds two separate pieces of text side by side (a column that was not found)
* no ruled band holds several rows of text that nothing says how to divide

The table is allowed to be *larger* than PyMuPDF's box, and only on evidence the page states: a text
line crossing its edge, text sitting in the table's own ruled rows, or a row just outside it whose
text fits the table's columns — inside a ruled band, or at the table's own row spacing. Whatever is
added passes the same checks as the rest.
"""

from __future__ import annotations

import json
import os
import threading
from collections import Counter
from dataclasses import dataclass, field

import pymupdf as fitz
from pymupdf import table as _pymupdf_table

from klarpdf.mcp_bridge.queries import _page_of, open_document, resolve_pages

# PyMuPDF prints a one-time suggestion to install `pymupdf_layout` the first time `find_tables` runs,
# with a bare `print` — to stdout, which is this server's protocol stream. The SDK's stdio transport
# diverts stdout while it serves (measured on the pinned SDK), so this is noise rather than
# corruption today; the variable is PyMuPDF's own documented switch for it.
os.environ.setdefault("PYMUPDF_SUGGEST_LAYOUT_ANALYZER", "0")

MAX_TABLES = 50
"""How many tables one call returns before it stops and says so — a mis-call bound, not a working
limit: the most found on one page of the M141 corpus (172 pages, 2026-09-13) is three."""

MAX_TABLE_CHARS = 60_000
"""How many characters of table JSON one call returns. A count cap does not bound a reply, because a
table's size follows its text rather than its rows: the largest in the M141 corpus is three rows long
and serialises to 8,820 characters."""

MIN_RULES = 3
"""Horizontal ruled or shaded edges a page needs before the row-ruled reader is tried, and the
threshold behind ``extract_text``'s ``table_pages`` hint. On the M141 corpus it names all 72 of the
172 pages that return a table, plus 84 that do not; counting thin rules alone, without shaded bands,
misses three. The corpus is chosen for its tables, so the 84 says nothing about ordinary pages."""

MIN_TEXT_SIZE = 1.0
"""Text set smaller than this (in points) is not read into cells.

The owner's decision (2026-09-13), for a measured case: a market report carries a copy of a table's
Total row at **0.1 pt** — a regular-weight duplicate drawn just before the bold 7 pt row a reader
sees, most likely left behind by the layout software. It is ordinary page text, so ``extract_text``
and ``search`` both return it, and it used to land inside the visible row's cells as a second value.
No printed text is legible at a tenth of this size, so this is a physical floor rather than a guess
tuned to documents."""

_MIN_WORDS_VERTICAL = 10
"""Passed to PyMuPDF's row-ruled finder, which still decides *where* a table is. At PyMuPDF's
default of 3 it finds "columns" inside justified prose and reports regions that are paragraphs. Its
column boundaries are not used here, so this no longer decides what a cell contains."""

_SNAP = float(_pymupdf_table.DEFAULT_SNAP_TOLERANCE)
"""How far apart two positions may be and still be the same position — PyMuPDF's own snap
tolerance, the figure its table finder already uses to decide that two edges are one. Reused rather
than invented, wherever this module compares two measured positions."""

_EPS = 0.01
"""Floating-point slack for "touches" versus "overlaps"; far below any printed distance."""

_TITLE_MAX_CHARS = 70
_TITLE_MAX_WORDS = 10
_TITLE_MAX_DIGIT_RATIO = 0.25
_TITLE_LEFT_TOLERANCE = 40.0
_TITLE_LOOKBACK = 160.0
_ORPHAN_GAP = 120.0
_CONTINUATION_TOLERANCE = 2.0
_TOP_OF_PAGE = 0.15

_READ_LOCK = threading.Lock()
"""Serialises ``find_tables`` and the text extraction done beside it.

PyMuPDF's table finder keeps the page's characters and edges in module-level lists that the next
call clears, and it switches a process-wide glyph-height setting on for the duration — as does
:func:`_page_inventory`. Two overlapping calls would read each other's state. The MCP SDK runs each
tool call in a worker thread, so this is not hypothetical."""

_SUGGEST = "extract_text on page {page} returns every value in reading order, or render_page to look at it"
_SUGGEST_IMAGE = "render_page {page} — the page is an image, so there is no text to read"

_REASONS = {
    "no_text": (
        "this page has no text layer — it is an image of a page, so there are no characters to "
        "place in cells"
    ),
    "unruled": (
        "nothing on this page marks where rows are — no drawn grid, and no ruled lines or shaded "
        "bands — so both rows and columns would have to be guessed"
    ),
    "no_region": (
        "this page has ruled lines or shaded bands, but they do not enclose anything table-shaped"
    ),
    "single_runs": (
        "the ruled rows here each hold a single run of text, so any columns exist only as spaces "
        "inside the lines and would have to be guessed"
    ),
    "beside": (
        "text here sits level with the rows of a table read on this page, beside it, and is not part "
        "of that table — it may be labels that table needs, a second table printed alongside, or "
        "something else on the page such as a chart"
    ),
    "reader_failed": "the table finder could not process this page",
    "grid_crossed": (
        "text here runs across the drawn cell borders, so the lines are not separating cells — "
        "this is usually a chart, or a form whose entries overflow their boxes"
    ),
    "overlapping_cells": (
        "the lines drawn here box in areas that overlap one another, so they are not the borders of "
        "cells — this is usually a bar chart, whose bars are drawn over its gridlines"
    ),
    "rule_through_text": (
        "a ruled line runs through text here, so the lines are not separating rows — a chart's "
        "gridlines do this, and so does ruling that belongs to the text rather than to a table"
    ),
    "side_by_side": (
        "two separate pieces of text share one column here, so the columns cannot be told apart "
        "without guessing"
    ),
    "stacked": (
        "several rows of text share one ruled band here, and nothing on the page says which lines "
        "belong to which row"
    ),
    "angled_text": "text inside this region is set at an angle, so it cannot be placed in rows",
    "overlap": (
        "the text here runs into a table already read on this page, and cannot be separated from "
        "it without guessing"
    ),
    "unaccounted": (
        "text inside this region could not be placed in exactly one cell, so the rows would have "
        "been incomplete"
    ),
}


class _Decline(Exception):
    """A region the page contradicts. ``reason`` keys :data:`_REASONS`; ``detail`` names the text
    that disagreed, which is what makes a decline diagnosable rather than a shrug."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


class _NotATable(Exception):
    """A region with too little in it to be a table at all — one row of text, one column. Not a
    failed table, so it is not reported unless nothing else on the page is."""


# ---------------------------------------------------------------------------------------------
# The page's text
# ---------------------------------------------------------------------------------------------


@dataclass
class _Word:
    """One word, with two boxes.

    ``x0 … y1`` is the tight box of the letters' ink, and it is what every geometric test uses: a
    ruled line through text, two lines sharing a visual row. ``outer`` is the ordinary box — the one
    ``search`` and ``get_text`` report, which also spans the font's ascent and descent — and it is
    what the reported ``bbox`` is built from, so a caller checking the table's box against the boxes
    it can get finds every word of the table inside it. Built from the tight boxes, the box could cut
    through a row by a few points: its centre sits above the letters' top.
    """

    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    outer: fitz.Rect


@dataclass(eq=False)
class _Line:
    """One text line as MuPDF assembled it: the unit every cell is built from, and never divided.

    ``top`` and ``baseline`` bound the letters above the baseline — the part a line drawn *through*
    text would cross. The tight glyph boxes let an underscore or a descender reach below the
    baseline and touch a cell border it does not cross (measured: a filename's underscore reaching
    2.5 pt past a clean grid's row line), so the baseline, not the box's bottom, is the limit.
    """

    words: list[_Word]
    baseline: float
    key: tuple[int, int]
    x0: float = field(init=False)
    x1: float = field(init=False)
    top: float = field(init=False)
    bottom: float = field(init=False)

    def __post_init__(self) -> None:
        self.words.sort(key=lambda w: w.x0)
        self.x0 = min(w.x0 for w in self.words)
        self.x1 = max(w.x1 for w in self.words)
        self.top = min(w.y0 for w in self.words)
        self.bottom = max(w.y1 for w in self.words)
        if self.baseline <= self.top + _EPS or self.baseline > self.bottom + _SNAP:
            self.baseline = self.bottom

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def middle(self) -> float:
        return (self.top + self.baseline) / 2

    @property
    def rect(self) -> fitz.Rect:
        return fitz.Rect(self.x0, self.top, self.x1, self.bottom)

    @property
    def outer(self) -> fitz.Rect:
        box = fitz.Rect(self.words[0].outer)
        for word in self.words[1:]:
            box |= word.outer
        return box


@dataclass
class _Inventory:
    lines: list[_Line]
    angled: list[fitz.Rect]


_LEADER_CHARACTERS = frozenset(".·…")


def _is_leader(text: str) -> bool:
    """A run of dots joining a label to its figure, which states nothing a cell should hold.

    Dropped like text under a point, and for the same reason: it is typesetting, not content. It
    has to be dropped from the page's words rather than from the finished cells, so that the
    recount in :func:`_check_accounted` and the cells agree about what the page holds.

    Measured on the SpaceX prospectus p251, a dot-leader balance sheet: 2 of its 134 lines are a
    leader alone, because the leader happened to be set far enough from its label for MuPDF to give
    it a line of its own. Those two shared the label column and the page declined, losing a
    statement that reconciles. Where a leader sits closer it joins the label's line instead, and its
    dots were read into the label's cell.
    """
    return len(text) > 1 and set(text) <= _LEADER_CHARACTERS


def _page_inventory(page: fitz.Page) -> _Inventory:
    """The page's horizontal text lines, in the orientation the page is displayed.

    **The words are the ones ``extract_text`` and ``search`` return, not the table finder's.** The
    finder builds its own text page with extra settings, and on at least one document those break
    words apart — a state education poster comes back as ``'P'``, ``'rcent'``, ``'stud'``, ``'Natio'``
    from the finder's copy and as whole words from an ordinary extraction. A cell built from the
    finder's words would have carried the damage.

    **Coordinates are converted to the displayed orientation**, because that is where the finder
    measures on a page with ``/Rotate``: its cells, edges and boxes describe the page as a reader
    sees it. An ordinary extraction reports the unrotated page, so every word, baseline and text
    direction is carried across once, here.
    """
    matrix = page.rotation_matrix if page.rotation else None
    with _READ_LOCK:
        textpage = page.get_textpage(flags=fitz.TEXTFLAGS_WORDS)
        ordinary = textpage.extractWORDS()
        old = bool(fitz.TOOLS.set_small_glyph_heights())
        fitz.TOOLS.set_small_glyph_heights(True)
        try:
            words = textpage.extractWORDS()
            blocks = textpage.extractDICT()["blocks"]
        finally:
            fitz.TOOLS.set_small_glyph_heights(old)
    if len(ordinary) != len(words):
        ordinary = words

    def shown(x0: float, y0: float, x1: float, y1: float) -> fitz.Rect:
        rect = fitz.Rect(x0, y0, x1, y1)
        if matrix is not None:
            rect = rect * matrix
            rect.normalize()
        return rect

    grouped: dict[tuple[int, int], list[tuple]] = {}
    for word, outer in zip(words, ordinary):
        grouped.setdefault((word[5], word[6]), []).append((word, outer))

    lines: list[_Line] = []
    angled: list[fitz.Rect] = []
    for key, members in grouped.items():
        block_no, line_no = key
        try:
            line = blocks[block_no]["lines"][line_no]
            spans, (dx, dy) = line["spans"], line["dir"]
        except (IndexError, KeyError):
            spans, (dx, dy) = [], (1.0, 0.0)
        if matrix is not None:
            dx, dy = dx * matrix.a + dy * matrix.c, dx * matrix.b + dy * matrix.d
        if abs(dx - 1.0) > _EPS or abs(dy) > _EPS:
            outline = shown(*members[0][1][:4])
            for _, outer in members[1:]:
                outline |= shown(*outer[:4])
            angled.append(outline)
            continue
        kept = []
        for word, outer in members:
            cx, cy = (word[0] + word[2]) / 2, (word[1] + word[3]) / 2
            span = min(
                spans,
                key=lambda s: max(0.0, s["bbox"][0] - cx, cx - s["bbox"][2])
                + max(0.0, s["bbox"][1] - cy, cy - s["bbox"][3]),
                default=None,
            )
            if span is not None and span["size"] < MIN_TEXT_SIZE:
                continue
            if _is_leader(word[4]):
                continue
            tight = shown(*word[:4])
            kept.append(_Word(tight.x0, tight.y0, tight.x1, tight.y1, word[4], shown(*outer[:4])))
        if not kept:
            continue
        sized = [s for s in spans if s["size"] >= MIN_TEXT_SIZE]
        if sized:
            origin = fitz.Point(max(sized, key=lambda s: s["size"])["origin"])
            baseline = (origin * matrix).y if matrix is not None else origin.y
        else:
            baseline = max(w.y1 for w in kept)
        lines.append(_Line(kept, baseline, key))
    return _Inventory(lines, angled)


def horizontal_rules(page: fitz.Page) -> int:
    """Count the page's horizontal ruled lines and shaded-band edges, as the page is displayed.

    Cheap next to reading tables — it counts drawings rather than detecting anything — which is what
    lets ``extract_text`` report ``table_pages``: on the M141 corpus it cost 1.0-1.6x reading the
    page's text, where reading its tables cost 72x. A filled band counts twice,
    once per edge: Cisco's 10-K marks the rows of its results summary with light-blue bands and no
    rules at all. On a page rotated a quarter turn, "horizontal as displayed" is vertical in the
    page's own coordinates, so the axis is swapped rather than the rotated table going unseen.
    """
    swap = page.rotation % 180 == 90
    found = 0
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                start, end = item[1], item[2]
                along, across = (abs(start.y - end.y), abs(start.x - end.x))
                if swap:
                    along, across = across, along
                if along < 1 and across > 30:
                    found += 1
            elif item[0] == "re":
                rect = item[1]
                width, height = (rect.height, rect.width) if swap else (rect.width, rect.height)
                if width > 30:
                    found += 1 if height < 2 else 2
    return found


def _horizontal_edges(page: fitz.Page) -> list[tuple[float, float, float]]:
    """Every horizontal edge the page draws, as ``(top, x0, x1)``, in display orientation.

    A rule, a hairline rectangle and the top or bottom of a shaded band all count: what matters is
    where the page draws a line across, not which kind of drawing object carries it. Read from the
    page's own drawings rather than from a finder's snapshot, because ``lines_strict`` reports none
    of a banded table's band edges — measured on Alphabet's 10-K p54, where its snapshot holds 139
    edges and not one of them is a band's, while the page draws 28 band tops, one per printed row.

    :func:`horizontal_rules` counts rather than collects, and only edges wide enough to be a row
    rule; this keeps every edge, since a band drawn in column-wide pieces is what says a drawn row
    holds more than one of the page's rows.
    """
    matrix = page.rotation_matrix
    edges: list[tuple[float, float, float]] = []
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                start, end = item[1] * matrix, item[2] * matrix
                if abs(start.y - end.y) < 1 and abs(start.x - end.x) > _SNAP:
                    edges.append((min(start.y, end.y), min(start.x, end.x), max(start.x, end.x)))
            elif item[0] == "re":
                rect = fitz.Rect(item[1]) * matrix
                rect.normalize()
                if rect.width > _SNAP:
                    edges.append((rect.y0, rect.x0, rect.x1))
                    edges.append((rect.y1, rect.x0, rect.x1))
    return edges


# ---------------------------------------------------------------------------------------------
# What PyMuPDF found
# ---------------------------------------------------------------------------------------------


@dataclass
class _Found:
    """One region PyMuPDF reported, copied out while its module-level state is still this page's."""

    bbox: fitz.Rect
    rows: list[list[tuple[float, float, float, float] | None]]
    bounds: list[float]


@dataclass
class _Reading:
    found: list[_Found]
    edges: list[tuple[float, float, float]]


def _read_with(page: fitz.Page, **settings) -> _Reading | None:
    """Run one of PyMuPDF's finders and copy out the regions and the horizontal edges it used."""
    with _READ_LOCK:
        finder = page.find_tables(**settings)
        if finder is None:
            return None
        found = []
        for table in finder.tables:
            rows = [list(row.cells) for row in table.rows]
            bounds = sorted({round(c[1], 3) for c in table.cells} | {round(c[3], 3) for c in table.cells})
            found.append(_Found(fitz.Rect(table.bbox), rows, bounds))
        edges = [
            (edge["top"], edge["x0"], edge["x1"])
            for edge in _pymupdf_table.EDGES
            if edge["orientation"] == "h"
        ]
    return _Reading(found, edges)


# ---------------------------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------------------------


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return min(a1, b1) - max(a0, b0)


def _gap(a0: float, a1: float, b0: float, b1: float) -> float:
    """Distance between two intervals; zero when they meet or overlap."""
    return max(0.0, b0 - a1, a0 - b1)


def _union(spans: list[tuple[float, float]]) -> list[list[float]]:
    """Merge x-intervals that overlap; touching is not overlapping."""
    merged: list[list[float]] = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1] - _EPS:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _covered(spans: list[tuple[float, float]]) -> list[list[float]]:
    """The x-ranges a set of drawn segments covers, joining pieces that meet within the snap
    tolerance — a rule drawn as one segment per column is still one rule."""
    merged: list[list[float]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + _SNAP:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _intersect(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    out = []
    for a0, a1 in a:
        for b0, b1 in b:
            lo, hi = max(a0, b0), min(a1, b1)
            if hi - lo > _EPS:
                out.append([lo, hi])
    return out


def _columns_touched(line: _Line, columns: list[list[float]]) -> list[int]:
    return [i for i, (a, b) in enumerate(columns) if _overlap(line.x0, line.x1, a, b) > _EPS]


def _same_row(a: _Line, b: _Line) -> bool:
    """Whether two lines sit side by side: each one's middle lies within the other's letters."""
    return (
        b.top - _EPS <= a.middle <= b.baseline + _EPS
        and a.top - _EPS <= b.middle <= a.baseline + _EPS
    )


def _visual_rows(lines: list[_Line]) -> list[list[_Line]]:
    """Group lines that sit side by side. Two lines share a visual row when each one's middle lies
    within the other's letters — mutual, so a tall line cannot pull two printed rows together."""
    rows: list[list[_Line]] = []
    for line in sorted(lines, key=lambda l: (l.middle, l.x0)):
        for row in rows:
            if all(
                other.top - _EPS <= line.middle <= other.baseline + _EPS
                and line.top - _EPS <= other.middle <= line.baseline + _EPS
                for other in row
            ):
                row.append(line)
                break
        else:
            rows.append([line])
    for row in rows:
        row.sort(key=lambda l: l.x0)
    rows.sort(key=lambda row: min(l.middle for l in row))
    return rows


def _cell_text(lines: list[_Line]) -> str:
    return "\n".join(" ".join(l.text for l in row) for row in _visual_rows(lines))


def _touching(lines: list[_Line], rect: fitz.Rect) -> list[_Line]:
    return [
        l for l in lines
        if _overlap(l.x0, l.x1, rect.x0, rect.x1) > _EPS and _overlap(l.top, l.bottom, rect.y0, rect.y1) > _EPS
    ]


def _inside_any(line: _Line, boxes: list[fitz.Rect]) -> bool:
    cx, cy = (line.x0 + line.x1) / 2, line.middle
    return any(b.x0 <= cx <= b.x1 and b.y0 <= cy <= b.y1 for b in boxes)


def _to_unrotated(page: fitz.Page, rect: fitz.Rect) -> list[float]:
    """The finder works in the page as displayed; every other box this bridge reports (``search``,
    ``get_links``, ``clip``, ``redact_regions``) is unrotated. Converted at the boundary, once."""
    box = fitz.Rect(rect) * page.derotation_matrix if page.rotation else fitz.Rect(rect)
    box.normalize()
    return [round(v, 1) for v in box]


def _quote(text: str) -> str:
    text = " ".join(text.split())
    return repr(text if len(text) <= 60 else text[:57] + "...")


# ---------------------------------------------------------------------------------------------
# A drawn grid
# ---------------------------------------------------------------------------------------------


def _read_grid(found: _Found, inventory: _Inventory, edges: list[tuple[float, float, float]]) -> dict:
    """Cells are the drawn cells; each word goes to the one cell its letters sit in.

    Words rather than lines, because a grid's cells are drawn and a text line may legitimately be
    emitted across two of them. The check is the grid's own claim: **text does not cross a cell
    border**. A chart's plot frame and gridlines look like a grid to the finder, and its axis and
    point labels are what cross them — measured on a statistics annual, where a line chart was
    returned as an 11 × 2 table with ``"76"`` read as ``"6"``.

    The second check asks the opposite question — whether the page marks *more* rows than the grid
    draws. ``edges`` is everything the page draws across (:func:`_horizontal_edges`), and a line
    crossing **every** cell of a drawn row with text on both sides of it says that row holds more
    than one of the page's rows. A banded table shades each row in column-wide pieces, which the
    finder stitches into a grid whose rows are whole blocks: Alphabet's stockholders'-equity
    roll-forward (10-K p54) came back as four rows of five cells, each holding ten values joined by
    newlines, with the label column — printed outside the drawn box — dropped without a word.
    """
    box = found.bbox
    cells = [(r, c, rect) for r, row in enumerate(found.rows) for c, rect in enumerate(row) if rect]
    for rect in inventory.angled:
        if rect.intersects(box):
            raise _Decline("angled_text", "text set at an angle inside the grid")
    for row in found.rows:
        boxes = [rect for rect in row if rect]
        if len(boxes) < 2:
            continue
        top, bottom = min(b[1] for b in boxes), max(b[3] for b in boxes)
        held = [
            line for line in inventory.lines
            if top <= line.baseline <= bottom and boxes[0][0] - _SNAP <= line.x0 and line.x1 <= boxes[-1][2] + _SNAP
        ]
        for height in sorted({round(top_ / _SNAP) * _SNAP for top_, _, _ in edges}):
            if not (top + _SNAP < height < bottom - _SNAP):
                continue
            covered = _covered([(x0, x1) for top_, x0, x1 in edges if abs(top_ - height) <= _SNAP])
            if not all(any(a - _SNAP <= b[0] and b[2] <= z + _SNAP for a, z in covered) for b in boxes):
                continue
            above = [line for line in held if line.baseline < height]
            if above and any(line.baseline > height for line in held):
                nearest = max(above, key=lambda line: line.baseline)
                raise _Decline(
                    "stacked",
                    f"the page draws a line across every cell at y={height:.1f}, under "
                    f"{_quote(nearest.text)} and above more text in the same drawn row",
                )
    placed: dict[tuple[int, int], list[_Line]] = {}
    reported = fitz.Rect(box)
    for line in inventory.lines:
        for word in line.words:
            top, bottom = word.y0, line.baseline if line.baseline > word.y0 + _EPS else word.y1
            if _overlap(word.x0, word.x1, box.x0, box.x1) <= _EPS or _overlap(top, bottom, box.y0, box.y1) <= _EPS:
                continue
            if word.x0 < box.x0 - _EPS or word.x1 > box.x1 + _EPS or top < box.y0 - _EPS or bottom > box.y1 + _EPS:
                raise _Decline("grid_crossed", f"{_quote(word.text)} crosses the grid's outer edge")
            hits = [
                (r, c)
                for r, c, rect in cells
                if _overlap(word.x0, word.x1, rect[0], rect[2]) > _EPS
                and _overlap(top, bottom, rect[1], rect[3]) > _EPS
            ]
            if len(hits) != 1:
                where = "no cell" if not hits else f"{len(hits)} cells"
                raise _Decline("grid_crossed", f"{_quote(word.text)} sits in {where}")
            placed.setdefault(hits[0], []).append(_Line([word], line.baseline, line.key))
            reported |= word.outer
    rows = [
        [_merge_pieces(placed.get((r, c), [])) if rect else "" for c, rect in enumerate(row)]
        for r, row in enumerate(found.rows)
    ]
    rows = [row for row in rows if any(cell for cell in row)]
    columns = sorted({round(rect[0], 1) for _, _, rect in cells})
    if len(rows) < 2 or len(columns) < 2:
        raise _NotATable()
    # The grid's other claim: its cells do not overlap, because every point of a table belongs to one
    # cell. A bar chart drawn over its gridlines breaks it. The band between two gridlines is a cell,
    # and so is each bar top standing inside that band, so the labels above the bars all fall in the
    # band. NADA's dealer report p4 came back as a 2 × 13 table with four bar labels in one cell
    # (#354). Checked last, so a region another check declines keeps that check's reason.
    pair = _overlapping([rect for _, _, rect in cells])
    if pair is not None:
        first, second = (", ".join(f"{v:.1f}" for v in rect) for rect in pair)
        raise _Decline("overlapping_cells", f"the drawn cells [{first}] and [{second}] overlap")
    edges = columns + [box.x1]
    spans = [[a, b] for a, b in zip(edges, edges[1:])]
    return {"bbox": reported, "rows": rows, "header": rows[0], "columns": columns, "spans": spans, "reader": "grid"}


def _overlapping(cells: list[tuple[float, float, float, float]]) -> tuple | None:
    """The first two cells sharing any area, or ``None`` when they tile the region as a grid's do.

    Sorted by left edge, so each cell is compared only with those starting before its right edge:
    a spreadsheet printed as one grid can hold thousands of cells.
    """
    ordered = sorted(cells)
    for index, a in enumerate(ordered):
        for b in ordered[index + 1:]:
            if b[0] >= a[2] - _EPS:
                break
            if _overlap(a[0], a[2], b[0], b[2]) > _EPS and _overlap(a[1], a[3], b[1], b[3]) > _EPS:
                return a, b
    return None


def _merge_pieces(pieces: list[_Line]) -> str:
    """Rejoin a cell's words into their lines, in reading order."""
    by_line: dict[tuple[int, int], list[_Word]] = {}
    baselines: dict[tuple[int, int], float] = {}
    for piece in pieces:
        by_line.setdefault(piece.key, []).extend(piece.words)
        baselines[piece.key] = piece.baseline
    return _cell_text([_Line(words, baselines[key], key) for key, words in by_line.items()])


# ---------------------------------------------------------------------------------------------
# Ruled or shaded rows, whitespace columns
# ---------------------------------------------------------------------------------------------


def _columns(rows: list[list[_Line]]) -> tuple[list[list[float]], set[int]]:
    """Columns from whitespace, and the lines that are spanning cells rather than column evidence.

    Only lines that sit beside another line say anything about where columns are; a line alone on
    its visual row — a section heading, a note — cannot. Of those, a line that holds two columns
    together that every other line keeps apart is a **spanning cell** only when every other line on
    its visual row does the same: a header row of years centred over a ``$`` and its figure. A line
    doing it beside ordinary cells is left in, and :func:`_divide` then decides whether the rows
    still say where the boundary is.
    """
    active = {id(line): line for row in rows if len(row) > 1 for line in row}
    row_of = {id(line): i for i, row in enumerate(rows) for line in row}
    spanning: set[int] = set()

    def bridges(line: _Line) -> bool:
        others = _union([(l.x0, l.x1) for k, l in active.items() if k != id(line)])
        return len(_columns_touched(line, others)) > 1

    changed = True
    while changed:
        changed = False
        for key, line in list(active.items()):
            if not bridges(line):
                continue
            mates = [l for l in rows[row_of[key]] if l is not line and id(l) in active]
            if all(bridges(mate) for mate in mates):
                del active[key]
                spanning.add(key)
                changed = True
                break
    return _union([(l.x0, l.x1) for l in active.values()]), spanning


def _divide(columns: list[list[float]], rows: list[list[_Line]], spanning: set[int]) -> list[list[float]]:
    """Split a whitespace column where the rows themselves say it holds more than one column.

    A column can close up without being one: Apple prints one figure as a single run ``"$ 109,417"``
    where every other row prints ``$`` and the figure apart, and on Salesforce's statement one
    figure's box starts a point inside the neighbouring column's widest figure — two columns that
    never meet on any row, but whose extents touch across rows. In both, the rows where the pieces
    sit side by side are the page stating the boundaries.

    So the rows holding the **most** separate pieces in the column, not overlapping each other,
    define the sub-columns. Every other row must fit them: each of its pieces covers its own run of
    sub-columns, and the runs go left to right without sharing one. A header date centred over a
    ``$`` and its figure covers two sub-columns; a year beside another year covers one each. A row
    that does not fit leaves the column whole, and the side-by-side check declines the table — Apple's
    page 10 holds two tables whose columns sit at different positions in one region, and it declines
    exactly there. Text printed twice to simulate bold overlaps itself and is never divided.
    """
    placement = {id(l): _place(l, columns) for row in rows for l in row}
    divided: list[list[float]] = []
    for index, column in enumerate(columns):
        pieces = [
            sorted((l for l in row if placement[id(l)] == index and id(l) not in spanning), key=lambda l: l.x0)
            for row in rows
        ]
        pieces = [p for p in pieces if p]
        widest = max((len(p) for p in pieces), default=0)
        full = [p for p in pieces if len(p) == widest]
        if widest < 2 or any(p[k].x1 > p[k + 1].x0 + _EPS for p in full for k in range(widest - 1)):
            divided.append(column)
            continue
        subs = [[min(p[k].x0 for p in full), max(p[k].x1 for p in full)] for k in range(widest)]

        def run(line: _Line) -> tuple[int, int]:
            touched = _columns_touched(line, subs)
            if touched:
                return touched[0], touched[-1]
            nearest = min(range(len(subs)), key=lambda i: _gap(line.x0, line.x1, *subs[i]))
            return nearest, nearest

        fits = True
        for p in pieces:
            if len(p) < 2:
                continue
            last = -1
            for line in p:
                first, final = run(line)
                if first <= last:
                    fits = False
                    break
                last = final
            if not fits:
                break
        if fits:
            divided.extend(subs)
        else:
            divided.append(column)
    return divided


def _place(line: _Line, columns: list[list[float]]) -> int:
    touched = _columns_touched(line, columns)
    if touched:
        return max(touched, key=lambda i: _overlap(line.x0, line.x1, *columns[i]))
    return min(range(len(columns)), key=lambda i: _gap(line.x0, line.x1, *columns[i]))


def _fits(row: list[_Line], columns: list[list[float]]) -> bool:
    """Does a visual row belong to a table with these columns?

    A line alone on its row must sit in exactly one column. Beside other lines, each line covers its
    own run of columns, left to right, and no two lines share one: a run of two is a period heading
    centred over a ``$`` and its figure, which is how the header lines just above a statement are set
    — requiring one column there stopped at them and left the figures without their dates. The
    distinction matters: a line alone spanning two columns is a caption, and NADA's two-line chart
    caption joined its table as two rows the moment a lone line was allowed to span."""
    if len(row) == 1:
        return len(_columns_touched(row[0], columns)) == 1
    last = -1
    for line in sorted(row, key=lambda l: l.x0):
        touched = _columns_touched(line, columns)
        if not touched or touched[0] <= last:
            return False
        last = touched[-1]
    return True


def _read_ruled(
    found: _Found,
    inventory: _Inventory,
    edges: list[tuple[float, float, float]],
    claimed: list[fitz.Rect],
    others: list[fitz.Rect],
) -> dict:
    """Read one row-ruled region: rows from the drawn lines, columns from whitespace.

    ``claimed`` are boxes already read or declined on this page; ``others`` are the finder's other
    regions still to come, which an extension must not reach into either.
    """
    free = [line for line in inventory.lines if not _inside_any(line, claimed)]
    box = fitz.Rect(found.bbox)
    for rect in inventory.angled:
        if rect.intersects(box):
            raise _Decline("angled_text", "text set at an angle inside the region")

    # 1. A line crossing the region's edge belongs to it whole — an outdented label, a figure whose
    #    closing bracket overhangs. Grown line by line until nothing crosses.
    mine = _touching(free, box)
    while True:
        grown = fitz.Rect(box)
        for line in mine:
            grown |= line.rect
        if grown == box:
            break
        box = grown
        mine = _touching(free, box)

    # 2. Text in the table's own ruled rows belongs to it, even outside PyMuPDF's box: the drawn
    #    lines at this table's row boundaries say how wide its rows are. That is how a label column
    #    PyMuPDF left out is recovered (its bands run under the labels), and how the two halves of a
    #    list printed two-up become one table. A chart beside a table is not reached: nothing the
    #    table draws runs under it.
    #
    #    Text on one side of the region joins it when **every** line there sits in a row the table's
    #    own ruling runs under — an edge bounding the line's row has a drawn segment beneath the
    #    line that runs on, unbroken, into the table — and no row line crosses any of it. Four simpler
    #    rules each failed on a real page. Taking any segment at any row height stretched a two-up
    #    list across the map printed beside it, whose outline strokes sit within snapping distance of
    #    a few row heights. Requiring the ruling at *every* row height lost a statement's whole label
    #    column: its bands run the full width, but the rules under its subtotals run under the
    #    figures only. Judging each line alone let a couple of a bar chart's labels in, because two
    #    of its gridlines happen to meet a table row's edges. Requiring *both* edges of a row lost
    #    the first label under a column-heading rule, which underlines the figures only. What
    #    separates a label column from a chart beside the table is continuity: the statement's
    #    bands run from its labels straight into its figures, while the chart's gridlines stop short
    #    of the table.
    bounds = found.bounds
    coverage = {b: _covered([(x0, x1) for top, x0, x1 in edges if abs(top - b) <= _SNAP]) for b in bounds}

    def ruled_across(line: _Line) -> bool:
        above = [b for b in bounds if b <= line.baseline + _EPS]
        below = [b for b in bounds if b > line.baseline + _EPS]
        nearest = ([max(above)] if above else []) + ([min(below)] if below else [])
        return any(
            line.x0 >= a - _SNAP and line.x1 <= z + _SNAP and _overlap(a, z, box.x0, box.x1) > _EPS
            for edge in nearest
            for a, z in coverage[edge]
        )

    extra: list[_Line] = []
    for side in ("left", "right"):
        candidates = [
            l for l in free
            if l not in mine
            and not _inside_any(l, others)
            and bounds[0] - _EPS <= l.baseline <= bounds[-1] + _EPS
            and (l.x1 <= box.x0 if side == "left" else l.x0 >= box.x1)
        ]
        if candidates and all(
            ruled_across(l) and not any(l.top + _EPS < b < l.baseline - _EPS for b in bounds)
            for l in candidates
        ):
            extra.extend(candidates)
    if extra:
        for line in extra:
            box |= line.rect
        mine = _touching(free, box)

    # 3. Rows: lines are placed in the ruled band their baseline sits in.
    def layout(lines: list[_Line], frame: fitz.Rect):
        bands = [min(bounds[0], frame.y0), *bounds[1:-1], max(bounds[-1], frame.y1)]
        for line in lines:
            for boundary in bands[1:-1]:
                if line.top + _EPS < boundary < line.baseline - _EPS:
                    raise _Decline(
                        "rule_through_text", f"the rule at y={boundary:.1f} runs through {_quote(line.text)}"
                    )
        banded: list[list[_Line]] = [[] for _ in range(len(bands) - 1)]
        for line in lines:
            index = next(
                (i for i in range(len(bands) - 1) if bands[i] - _EPS <= line.baseline < bands[i + 1] + _EPS),
                None,
            )
            if index is None:
                raise _Decline("unaccounted", f"{_quote(line.text)} sits in no row")
            banded[index].append(line)
        visual = [row for band in banded for row in _visual_rows(band)]
        # 4. Columns, from everything the bands hold. Nothing side by side anywhere means no
        #    columns at all — not a failed table, just not one. Reached since :func:`_recover`
        #    offers the parts it cuts a region into, one of which can be a block of prose: before
        #    that, `_divide` was handed an empty column list and `_place` raised ValueError.
        columns, spanning = _columns(visual)
        if len(columns) < 2:
            raise _NotATable()
        return bands, banded, visual, _divide(columns, visual, spanning), spanning

    bands, banded, visual, columns, spanning = layout(mine, box)

    # 4b. A table whose every row continues beside it. A restated balance sheet rules only under its
    #     figures, so the finder located the three figure columns and left each row's label and note
    #     number beside them — returned as it was, that is 42 rows of numbers with nothing to say what
    #     any of them is, which is worse than declining. When every row of side-by-side text has more
    #     text beside it *on the same line*, that text is part of the rows: it is taken in, and the
    #     whole is laid out and checked again. Here the labels then share ruled bands with section
    #     headings that have no figures, and the table declines as it should. A two-up list whose
    #     halves line up row for row becomes one wider table; a chart or a second list that drifts out
    #     of step does not qualify, and is reported beside the table instead.
    for side in ("left", "right"):
        data_rows = [row for row in visual if len(row) > 1]
        beside = [
            l for l in free
            if l not in mine
            and not _inside_any(l, others)
            and bands[0] - _EPS <= l.baseline <= bands[-1] + _EPS
            and (l.x1 <= box.x0 if side == "left" else l.x0 >= box.x1)
        ]
        if not beside or not data_rows:
            continue
        if not all(any(_same_row(b, l) for b in beside for l in row) for row in data_rows):
            continue
        for line in beside:
            box |= line.rect
        mine = _touching(free, box)
        try:
            bands, banded, visual, columns, spanning = layout(mine, box)
        except _Decline as decline:
            raise _Decline(decline.reason, f"with the text beside every row taken in: {decline.detail}") from None
    if len(columns) < 2:
        raise _NotATable()

    # 5. A row just outside the region joins it only when its text fits these columns, and it sits
    #    either inside a band the table's ruling encloses or no further out than the table's own rows
    #    are spaced. A list ruled *under* each row has no line above its first row; a statement's
    #    first line often sits above the first band the finder used; a total is often set apart by a
    #    gap and a rule of its own.
    pitch = max((b[0].baseline - a[0].baseline for a, b in zip(visual, visual[1:])), default=0.0) + _SNAP
    rules: list[float] = []
    for top in sorted(t for t, x0, x1 in edges if _overlap(x0, x1, box.x0, box.x1) > _EPS):
        if not rules or top - rules[-1] > _SNAP:
            rules.append(top)
    extended_above: list[list[_Line]] = []
    extended_below: list[list[_Line]] = []
    outside = [l for l in free if l not in mine and not _inside_any(l, others)]
    for direction in ("above", "below"):
        edge_row = visual[0] if direction == "above" else visual[-1]
        while True:
            if direction == "above":
                reference = min(l.top for l in edge_row)
                candidates = [l for l in outside if l.baseline < min(l.baseline for l in edge_row) - _EPS]
            else:
                reference = max(l.baseline for l in edge_row)
                candidates = [l for l in outside if l.baseline > reference + _EPS]
            candidates = [l for l in candidates if _overlap(l.x0, l.x1, box.x0, box.x1) > _EPS]
            if not candidates:
                break
            rows_out = _visual_rows(candidates)
            nearest = rows_out[-1] if direction == "above" else rows_out[0]
            top = min(l.top for l in nearest)
            baseline = max(l.baseline for l in nearest)
            if direction == "above":
                ruled = any(baseline - _SNAP <= t < reference for t in rules) and any(t <= top for t in rules)
                distance = min(l.baseline for l in edge_row) - baseline
            else:
                ruled = any(reference < t <= top + _SNAP for t in rules) and any(t >= baseline for t in rules)
                distance = baseline - reference
            if not (ruled or distance <= pitch):
                break
            if not _fits(nearest, columns):
                # A row of several pieces may add a column where a piece sits wholly in whitespace —
                # the same evidence the table's own columns were built from. A statement whose `$`
                # signs appear only on its first and total rows has no `$` column among the rows the
                # finder located, so both rows arrived with each `$` in a gap and were refused, and
                # the first row was lost without a word. A piece bridging two columns still refuses
                # the row, and a line alone on its row never adds a column.
                gaps = [l for l in nearest if not _columns_touched(l, columns)]
                widened = _union([(a, b) for a, b in columns] + [(l.x0, l.x1) for l in gaps])
                if len(nearest) < 2 or not gaps or len(widened) != len(columns) + len(gaps):
                    break
                if not _fits(nearest, widened):
                    break
                columns = widened
            (extended_above if direction == "above" else extended_below).append(nearest)
            outside = [l for l in outside if l not in nearest]
            edge_row = nearest
    # Only side-by-side text anchors an addition. A lone line fits a wide label column just as a
    # centred page title does — Cisco's cash-flow statement took in "CISCO SYSTEMS, INC." and its
    # own heading that way — so lone rows are kept only where a side-by-side row lies beyond them:
    # a statement's "Current assets:" between its year header and its first figures stays. Or where
    # the lone line's letters reach into the table itself: a market report's header cell "SIZE" over
    # "(UNITS)" sits half a line above the rest of its header row, touching it — the same evidence
    # as a label crossing the table's edge, and dropping it left a word of the header inside the
    # table's own box but in no cell.
    #    Touching counts only against the table itself and its side-by-side rows — never against
    #    another lone line, or a two-line caption keeps itself by one line touching the other.
    core = fitz.Rect(mine[0].outer)
    for line in mine[1:]:
        core |= line.outer
    for added in (extended_above, extended_below):
        while added and len({_place(l, columns) for l in added[-1]}) < 2:
            kept = fitz.Rect(core)
            for row in added[:-1]:
                if len({_place(l, columns) for l in row}) > 1:
                    for line in row:
                        kept |= line.outer
            if any(kept.intersects(line.rect) for line in added[-1]):
                break
            added.pop()
    extended_above.reverse()

    # 6. Assemble. A band holding one visual row of side-by-side text, or one plus lines that are
    #    alone (a label wrapped onto a second line), is one row. A band holding several visual rows
    #    each of side-by-side text is several rows only if *every* visual row in it is: one line
    #    alone among them could belong to the row above or the row below, and nothing says which.
    #    And a band is only ever divided when the ruling is shown to mark rows elsewhere in the same
    #    table — some band holds exactly one row. Two columns of prose between a page's header and
    #    footer rules is one band of side-by-side lines; dividing it would return prose as a table.
    table_rows: list[list[list[_Line]]] = []

    def emit(lines: list[_Line]) -> None:
        cells: list[list[_Line]] = [[] for _ in columns]
        for line in lines:
            cells[_place(line, columns)].append(line)
        table_rows.append(cells)

    shapes = []
    for band in banded:
        band_rows = _visual_rows(band) if band else []
        shapes.append((band_rows, [r for r in band_rows if len({_place(l, columns) for l in r}) > 1]))
    rows_are_ruled = any(len(side_by_side) == 1 for _, side_by_side in shapes)

    for row in extended_above:
        emit(row)
    for band, (band_rows, side_by_side) in zip(banded, shapes):
        if not band:
            continue
        if len(side_by_side) <= 1:
            emit(band)
        elif len(side_by_side) == len(band_rows) and rows_are_ruled:
            for r in band_rows:
                emit(r)
        elif not rows_are_ruled:
            raise _Decline(
                "stacked",
                f"no ruled band holds a single row; one holds {len(side_by_side)}, starting {_quote(side_by_side[0][0].text)}",
            )
        else:
            lone = next(r for r in band_rows if r not in side_by_side)
            raise _Decline(
                "stacked",
                f"{len(side_by_side)} rows of side-by-side text share a band with {_quote(lone[0].text)}",
            )
    for row in extended_below:
        emit(row)

    # 7. The checks.
    for cells in table_rows:
        for index, cell in enumerate(cells):
            for visual_row in _visual_rows(cell):
                if len(visual_row) > 1 and not all(id(l) in spanning for l in visual_row):
                    raise _Decline(
                        "side_by_side",
                        " and ".join(_quote(l.text) for l in visual_row[:2]) + f" share column {index + 1}",
                    )
    text_rows = [[_cell_text(cell) for cell in cells] for cells in table_rows]
    text_rows = [row for row in text_rows if any(row)]
    used_columns = sorted({i for row in text_rows for i, cell in enumerate(row) if cell})
    if len(text_rows) < 2 or len(used_columns) < 2:
        raise _NotATable()
    included = [line for cells in table_rows for cell in cells for line in cell]
    reported = fitz.Rect(included[0].outer)
    for line in included[1:]:
        reported |= line.outer
    return {
        "bbox": reported,
        "rows": [[row[i] for i in used_columns] for row in text_rows],
        "header": None,
        "columns": [round(columns[i][0], 1) for i in used_columns],
        "spans": [list(columns[i]) for i in used_columns],
        "reader": "ruled",
    }


_RECOVERABLE = frozenset({"side_by_side", "stacked", "angled_text"})
"""Which failures are worth reading again in parts.

The first two say the region's rows or columns are ambiguous, and the third that it holds something
that is not table text at all — each a reason to look for the boundary the finder missed.
``rule_through_text`` and ``grid_crossed`` are not on the list, and that is the point: they say the
drawn lines run through text, which is a chart's gridlines rather than a table's ruling, and the
parts would be cut along those same lines. A California schools poster (`report_CA_06_california.pdf`
p4) proved it — cut up, one part came back as a table whose first rows were a chart's axis, `← lower`
and `higher →` above a legend's real numbers.
"""


def _recover(
    found: _Found,
    inventory: _Inventory,
    edges: list[tuple[float, float, float]],
    claimed: list[fitz.Rect],
    others: list[fitz.Rect],
) -> tuple[list[dict], list[tuple[fitz.Rect, _Decline]]]:
    """Read a region that failed as a whole in the parts its own prose divides it into.

    One located region often holds several tables: Apple's 10-Q p14 carries three notes, a
    retirement statement carries two, Lilly's proxy p56 three. Read as one, their columns are
    derived across layouts that have nothing to do with each other and the whole page declines —
    three pages that earlier rounds had verified correct came back empty (TC-039).

    **A band breaks the region when it holds more than one row of text and one of those rows is a
    lone line crossing more than one column**: prose, or a caption and the sentence introducing its
    table. A wrapped label's second line stays inside its own column and a section label such as
    ``Current assets:`` sits in one column too, so neither divides a table.

    Every part is read by the same reader and passes the same checks on its own, and a part that
    fails is declined with its own box — the region is re-located, never repaired. Only a region
    that has already failed is offered here, so a page that reads today cannot be split by it.
    """
    inside = [
        line for line in inventory.lines
        if not _inside_any(line, claimed)
        and found.bounds[0] - _EPS <= line.baseline <= found.bounds[-1] + _EPS
        and found.bbox.x0 - _SNAP <= line.x0 and line.x1 <= found.bbox.x1 + _SNAP
    ]
    rough, _ = _columns(_visual_rows(inside))
    if len(rough) < 2:
        return [], []

    runs: list[list[float]] = []
    current: list[float] = []
    for top, bottom in zip(found.bounds, found.bounds[1:]):
        band = [line for line in inside if top - _EPS <= line.baseline <= bottom + _EPS]
        rows = _visual_rows(band)
        prose = len(rows) > 1 and any(
            len(row) == 1 and len(_columns_touched(row[0], rough)) > 1 for row in rows
        )
        if prose:
            if len(current) > 1:
                runs.append(current)
            current = []
        else:
            current = [*current, bottom] if current else [top, bottom]
    if len(current) > 1:
        runs.append(current)
    if len(runs) < 2:
        return [], []

    recovered: list[dict] = []
    refused: list[tuple[fitz.Rect, _Decline]] = []
    for bounds in runs:
        part = _Found(fitz.Rect(found.bbox.x0, bounds[0], found.bbox.x1, bounds[-1]), [], bounds)
        try:
            table = _read_ruled(part, inventory, edges, claimed, others)
            _check_accounted(table, inventory)
        except _NotATable:
            continue
        except _Decline as decline:
            refused.append((fitz.Rect(part.bbox), decline))
        else:
            recovered.append(table)
    if not recovered:
        return recovered, refused

    # Text the parts leave behind. A band that divides the region can hold table text as well as
    # prose — a column heading, a beneficiary's row — and dropping the band dropped that text: it
    # was in no table and in no declined region, on four pages of the corpus. Each side-by-side
    # line left over goes to the part nearest it. A declined part's box grows to name it. A returned
    # part is declined instead, because text pressed against a table that its rows could not take
    # in means the table is not known to be whole: a retirement statement's performance table came
    # back headed "period" where the page prints "For this statement period". A line level with a
    # returned table is not handled here — it sits beside that table, and is reported as such.
    def gap(line: _Line, box: fitz.Rect) -> float:
        return max(0.0, box.y0 - line.baseline, line.top - box.y1)

    placed = [table["bbox"] for table in recovered] + [box for box, _ in refused]
    loose = [
        line
        for row in _visual_rows(inside)
        if len(row) > 1
        for line in row
        if not any(box.contains(fitz.Point((line.x0 + line.x1) / 2, line.middle)) for box in placed)
        and all(gap(line, table["bbox"]) > 0 for table in recovered)
    ]
    grown = [fitz.Rect(box) for box, _ in refused]
    demoted: dict[int, tuple[fitz.Rect, str]] = {}
    for line in loose:
        near_table = min(range(len(recovered)), key=lambda i: gap(line, recovered[i]["bbox"]))
        near_refused = min(range(len(refused)), key=lambda i: gap(line, refused[i][0]), default=None)
        if near_refused is not None and gap(line, refused[near_refused][0]) <= gap(line, recovered[near_table]["bbox"]):
            grown[near_refused] = grown[near_refused] | line.outer
        else:
            box, first = demoted.get(near_table, (fitz.Rect(recovered[near_table]["bbox"]), line.text))
            demoted[near_table] = (box | line.outer, first)
    refused = [(grown[i], decline) for i, (_, decline) in enumerate(refused)]
    for index, (box, first) in demoted.items():
        refused.append((box, _Decline("unaccounted", f"{_quote(first)} is pressed against a table and in none of its rows")))
    recovered = [table for i, table in enumerate(recovered) if i not in demoted]
    return recovered, refused


# ---------------------------------------------------------------------------------------------
# The independent check
# ---------------------------------------------------------------------------------------------


def _check_accounted(table: dict, inventory: _Inventory) -> None:
    """Every word whose letters are centred inside the reported box is in the cells, exactly once —
    and the cells hold nothing else.

    Computed from the page's words and the finished rows, sharing nothing with how the rows were
    built. By construction it cannot fail today; it exists so that a future change to assembly that
    drops, duplicates or imports a word declines the table instead of shipping it (the first
    attempt's regressions were all caught this way, by a script, never by the tool).

    **Letters, not ordinary boxes, decide "inside".** The reported box is drawn around each word's
    ordinary box, which spans the font's full ascent and descent; a line set tight against the
    table — a market report's ``SIZE`` over its ``(UNITS)``, left out as a lone line — then has its
    ordinary box's centre inside the table's, while its letters are plainly outside it."""
    box = table["bbox"]
    printed = Counter(
        word.text
        for line in inventory.lines
        for word in line.words
        if box.x0 - _EPS <= (word.x0 + word.x1) / 2 <= box.x1 + _EPS
        and box.y0 - _EPS <= (word.y0 + word.y1) / 2 <= box.y1 + _EPS
    )
    returned = Counter(token for row in table["rows"] for cell in row for token in cell.split())
    if printed != returned:
        missing = list((printed - returned).elements())[:3]
        extra = list((returned - printed).elements())[:3]
        raise _Decline("unaccounted", f"printed but not in a cell: {missing}; in a cell but not printed: {extra}")


# ---------------------------------------------------------------------------------------------
# One page
# ---------------------------------------------------------------------------------------------


@dataclass
class PageRead:
    tables: list[dict]
    unread: list[dict]
    blocks: list[tuple[fitz.Rect, str]]


def read_page(page: fitz.Page) -> PageRead:
    """Every table on one page that agrees with the page, and every region that does not."""
    inventory = _page_inventory(page)
    if not inventory.lines and not inventory.angled:
        return PageRead([], [_unread(page, page.rect, _Decline("no_text"))], [])
    grid = _read_with(page, strategy="lines_strict")
    if grid is None:
        return PageRead([], [_unread(page, page.rect, _Decline("reader_failed"))], [])

    tables: list[dict] = []
    unread: list[dict] = []
    claimed: list[fitz.Rect] = []
    single_runs: list[fitz.Rect] = []
    deferred: list[tuple[fitz.Rect, dict]] = []
    edges = _horizontal_edges(page)
    for found in grid.found:
        try:
            table = _read_grid(found, inventory, edges)
            _check_accounted(table, inventory)
        except _NotATable:
            pass
        except _Decline as decline:
            # A drawn row the page's own lines cut across is not this reader's to report. The
            # region is left *unclaimed* so the reader that takes its rows from those lines can
            # have it — on a banded block that returns the table whole, labels and all — and the
            # decline is reported below only if nothing else on the page covers the region.
            if decline.reason == "stacked":
                deferred.append((fitz.Rect(found.bbox), _unread(page, found.bbox, decline)))
                continue
            unread.append(_unread(page, found.bbox, decline))
        else:
            tables.append(table)
        claimed.append(found.bbox)

    ruled_page = horizontal_rules(page) >= MIN_RULES
    if ruled_page:
        ruled = _read_with(
            page,
            vertical_strategy="text",
            horizontal_strategy="lines",
            min_words_vertical=_MIN_WORDS_VERTICAL,
        )
        if ruled is None:
            if not tables:
                unread.append(_unread(page, page.rect, _Decline("reader_failed")))
        else:
            for position, found in enumerate(ruled.found):
                free = [l for l in inventory.lines if not _inside_any(l, claimed)]
                inside = _touching(free, found.bbox)
                # A region holding no side-by-side text is not a failed table. Beside a table this
                # page did read, it is what is left of that table's region (a caption, a stray
                # note) and reporting it would send a caller looking for nothing; on a page with
                # no table at all it is the one thing worth saying about the page.
                if not any(len(row) > 1 for row in _visual_rows(inside)):
                    if len(inside) > 1:
                        single_runs.append(found.bbox)
                    continue
                later = [f.bbox for f in ruled.found[position + 1:]]
                try:
                    table = _read_ruled(found, inventory, ruled.edges, claimed, later)
                    if any((table["bbox"] & c).get_area() > 0 for c in claimed if table["bbox"].intersects(c)):
                        raise _Decline("overlap", "grew into a region already read or declined")
                    _check_accounted(table, inventory)
                except _NotATable:
                    claimed.append(found.bbox)
                except _Decline as decline:
                    # Several tables in one located region read as one only by accident. Offer the
                    # region to :func:`_recover`, which reads the parts its own prose divides it
                    # into; the region's own decline stands when that finds nothing, and a failure
                    # that names the drawn lines themselves is never offered (see `_RECOVERABLE`).
                    recovered: list[dict] = []
                    refused: list[tuple[fitz.Rect, _Decline]] = []
                    if decline.reason in _RECOVERABLE:
                        recovered, refused = _recover(found, inventory, ruled.edges, claimed, later)
                    if recovered:
                        for part in recovered:
                            tables.append(part)
                            claimed.append(part["bbox"])
                        for box, refusal in refused:
                            unread.append(_unread(page, box, refusal))
                            claimed.append(box)
                        # The parts and their refusals are claimed, the region itself is not: the
                        # captions that divide it stay free, and each part can be given the one
                        # above it. Claiming the whole region left Apple's p14 notes untitled.
                    else:
                        unread.append(_unread(page, found.bbox, decline))
                        claimed.append(found.bbox)
                else:
                    tables.append(table)
                    claimed.append(table["bbox"])

    # A grid left unclaimed above is reported here, unless something else has taken the region on
    # since: the reader that takes its rows from the drawn lines has had its turn, and either read
    # the block whole or declined it with a reason of its own, which is the better one to give.
    for box, entry in deferred:
        if not any(box.intersects(other) for other in claimed):
            unread.append(entry)
            claimed.append(box)

    # Text level with a returned table's rows, beside it, in no region at all. A statistics annual
    # lists states two-up and the finder located only one half; a restated balance sheet rules under
    # its figures only, so its labels can sit outside the table; a bar chart can stand beside one.
    # The report is deliberately generous — naming a chart costs the caller a look, while a table
    # whose labels were left beside it without a word is the worst reply this tool can give.
    for table in tables:
        if table["reader"] != "ruled":
            continue
        box = table["bbox"]
        members = [l for l in inventory.lines if box.contains(fitz.Point((l.x0 + l.x1) / 2, l.middle))]
        level = [l for l in inventory.lines if not _inside_any(l, claimed) and box.y0 <= l.middle <= box.y1]
        for side in ([l for l in level if l.x1 <= box.x0], [l for l in level if l.x0 >= box.x1]):
            aligned = [l for l in side if any(_same_row(l, m) for m in members)]
            if not aligned:
                continue
            region = fitz.Rect(side[0].outer)
            for line in side[1:]:
                region |= line.outer
            unread.append(_unread(page, region, _Decline("beside", _quote(aligned[0].text))))
            claimed.append(region)

    if not tables and not unread:
        if single_runs:
            unread.extend(_unread(page, box, _Decline("single_runs")) for box in single_runs)
        else:
            # Nothing was located, so there is no region to point at — but the page's own text is
            # narrower than the page, and that is what a caller can act on. Salesforce's p5 handed
            # back the whole 612 x 792 where a sub-page box had been shipped before (TC-039).
            outers = [line.outer for line in inventory.lines]
            where = fitz.Rect(outers[0]) if outers else fitz.Rect(page.rect)
            for outer in outers[1:]:
                where |= outer
            unread.append(_unread(page, where, _Decline("no_region" if ruled_page else "unruled")))

    tables.sort(key=lambda t: (t["bbox"].y0, t["bbox"].x0))
    blocks = _free_blocks(page, claimed)
    for table in tables:
        table["title"] = _title_above(blocks, table["bbox"], table["spans"])
    return PageRead(tables, unread, blocks)


def _unread(page: fitz.Page, rect: fitz.Rect, decline: _Decline) -> dict:
    number = page.number + 1
    suggestion = _SUGGEST_IMAGE if decline.reason == "no_text" else _SUGGEST
    return {
        "page": number,
        "bbox": _to_unrotated(page, rect),
        "reason": _REASONS[decline.reason],
        "suggestion": suggestion.format(page=number),
        "_code": decline.reason,
        "_detail": decline.detail,
    }


# ---------------------------------------------------------------------------------------------
# Titles and continuation — labels for a caller, never table content
# ---------------------------------------------------------------------------------------------


def looks_like_title(text: str) -> bool:
    """Whether a free text block reads as a caption: one line, short, not a sentence, not mostly
    numbers. Measured against the rule it replaced ("the nearest block above"), which returned a
    column header, a data row and a sentence on three real pages."""
    if "\n" in text.strip():
        return False
    compact = " ".join(text.split())
    if not compact or len(compact) > _TITLE_MAX_CHARS or len(compact.split()) > _TITLE_MAX_WORDS:
        return False
    if compact.endswith((".", ";", ":")) and not compact.endswith("..."):
        return False
    # A line wholly in brackets qualifies a title rather than being one — "(in millions)",
    # "(Unaudited)". Taken as the caption it displaced the statement heading above it on QCOM's
    # balance sheet; stepping over it lets the search reach that heading.
    if compact.startswith("(") and compact.endswith(")"):
        return False
    numeric = sum(1 for ch in compact if ch.isdigit() or ch in "$€£₹,")
    return numeric <= _TITLE_MAX_DIGIT_RATIO * len(compact)


def _free_blocks(page: fitz.Page, boxes: list[fitz.Rect]) -> list[tuple[fitz.Rect, str]]:
    """Text blocks outside every table and every declined region, top to bottom — in the page's
    displayed orientation, like the boxes they are compared with.

    **A block a link annotation covers is navigation, not a caption.** Every filing in the corpus
    prints a "Table of Contents" link in its top margin, and it was being handed back as the title
    of the statement below it on Cisco's cash-flow statement, Broadcom's balance sheet and
    Salesforce's income statement alike (TC-039). The page says so itself: the caption of a table is
    text, while that heading is a link to somewhere else.
    """
    links: list[fitz.Rect] = []
    for link in page.get_links():
        rect = fitz.Rect(link["from"])
        if page.rotation:
            rect = rect * page.rotation_matrix
            rect.normalize()
        links.append(rect)
    free: list[tuple[fitz.Rect, str]] = []
    for block in page.get_text("blocks"):
        rect = fitz.Rect(block[:4])
        if page.rotation:
            rect = rect * page.rotation_matrix
            rect.normalize()
        text = (block[4] or "").strip()
        if not text:
            continue
        area = rect.get_area()
        if area > 0 and any(box.intersects(rect) and (rect & box).get_area() > 0.5 * area for box in boxes):
            continue
        if area > 0 and any(link.intersects(rect) and (rect & link).get_area() > 0.5 * area for link in links):
            continue
        free.append((rect, text))
    free.sort(key=lambda entry: entry[0].y0)
    return free


def _title_above(blocks: list[tuple[fitz.Rect, str]], box: fitz.Rect, spans: list[list[float]]) -> str | None:
    """The nearest caption-like block above ``box``, stepping over an introductory paragraph.

    A caption either begins at the table's margin or is centred over the table. What is rejected is
    text **over the figure columns and not over the label column**: a column heading (``Salary``), or
    a piece of a multi-line header left above the table (``Six Months Ended``, ``September 27,``).
    The first version accepted only captions at the margin, which passed over every centred statement
    title and settled on page furniture — Cisco's and Broadcom's statements came back titled "Table of
    Contents". Accepting anything centred then picked those header pieces instead, which cost two real
    captions. ``spans`` are the table's column extents, the label column first.
    """
    for rect, text in reversed(blocks):
        if rect.y1 > box.y0 + 2:
            continue
        if box.y0 - rect.y1 > _TITLE_LOOKBACK:
            return None
        at_margin = rect.x0 <= box.x0 + _TITLE_LEFT_TOLERANCE
        centre = (rect.x0 + rect.x1) / 2
        over_figures = any(_overlap(rect.x0, rect.x1, a, b) > _EPS for a, b in spans[1:])
        over_labels = bool(spans) and _overlap(rect.x0, rect.x1, *spans[0]) > _EPS
        if not at_margin and (not box.x0 <= centre <= box.x1 or (over_figures and not over_labels)):
            continue
        if looks_like_title(text):
            return " ".join(text.split())
    return None


def _orphan_title(blocks: list[tuple[fitz.Rect, str]], last_box: fitz.Rect | None) -> str | None:
    """A caption stranded below the last table on a page, which titles the next page's first
    table — a product manual ends a page with one and starts the table on the next."""
    if last_box is None:
        return None
    for rect, text in blocks:
        if rect.y0 < last_box.y1 - 2:
            continue
        if rect.y0 - last_box.y1 > _ORPHAN_GAP:
            return None
        if looks_like_title(text):
            return " ".join(text.split())
    return None


def titles_after(read: PageRead, before: PageRead | None) -> list[tuple[str | None, bool]]:
    """Each table's title on a page, and whether it came from the foot of the page before.

    ``before`` is the page before this one, read in the same request, or ``None``. It is never the
    previous page *asked for*: a request that skips a page made that an earlier page, and a caption
    stranded two pages up titled a table it has nothing to do with (#366). The corpus checker calls
    this too, so the titles it compares are the ones the tool returns.
    """
    carried = None
    if before is not None and before.tables:
        carried = _orphan_title(before.blocks, before.tables[-1]["bbox"])
    out: list[tuple[str | None, bool]] = []
    for position, table in enumerate(read.tables):
        if position == 0 and carried and not table["title"]:
            out.append((carried, True))
        else:
            out.append((table["title"], False))
    return out


def _continues_from(entry: dict, previous: dict | None, page_height: float) -> int | None:
    """Whether ``entry`` may continue the previous page's last table — flagged, never merged.

    Two different tables can share a header and every column position: a product manual's pages
    29 and 30 do, one reporting 24 hours and the next 28. A table that received a stranded title
    from the previous page is therefore never a continuation — that title says it starts something.
    """
    if previous is None or entry.get("title_from_previous_page"):
        return None
    if entry["_box"].y0 > _TOP_OF_PAGE * page_height:
        return None
    mine, theirs = entry["_columns"], previous["_columns"]
    if len(mine) != len(theirs):
        return None
    if any(abs(a - b) > _CONTINUATION_TOLERANCE for a, b in zip(mine, theirs)):
        return None
    return previous["page"]


# ---------------------------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------------------------


def ruled_pages(path: str, pages: list[int] | None = None, password: str | None = None) -> list[int]:
    """Pages in scope carrying enough ruling or shading that ``get_tables`` may return rows for them."""
    with open_document(path, password) as vdoc:
        indices = resolve_pages(vdoc, pages)
        return [i + 1 for i in indices if horizontal_rules(_page_of(vdoc, i)) >= MIN_RULES]


def _public(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if not k.startswith("_")}


def tables(
    path: str,
    pages: list[int],
    *,
    password: str | None = None,
    max_tables: int = MAX_TABLES,
    max_chars: int = MAX_TABLE_CHARS,
    offset: int = 0,
) -> dict:
    """Every table on ``pages`` that agrees with its page, plus every region that does not.

    Every page asked for appears in ``tables`` or in ``unread_regions`` or both; a page is never
    silently absent, and neither is a region the finder located that held a table's worth of text.
    """
    if offset < 0:
        raise ValueError(f"offset must be >= 0; got {offset}")
    if not pages:
        raise ValueError(
            "get_tables needs an explicit page range — reading tables is tens of times slower than "
            "reading text, so a whole-document scan is refused. Narrow with get_outline, search, or "
            "extract_text's `table_pages`."
        )

    with open_document(path, password) as vdoc:
        indices = resolve_pages(vdoc, pages)
        found: list[dict] = []
        unread: list[dict] = []
        last: tuple[int, PageRead] | None = None

        for index0 in indices:
            page = _page_of(vdoc, index0)
            result = read_page(page)
            unread.extend(_public(u) for u in result.unread)
            # Only the page before can hand this one a caption or a table to continue. The one read
            # before it is that page only when the request did not skip it (#366).
            before = last[1] if last is not None and last[0] == index0 - 1 else None
            previous = found[-1] if before is not None and before.tables else None
            titles = titles_after(result, before)
            for position, (table, (title, carried)) in enumerate(zip(result.tables, titles)):
                entry = {
                    "page": index0 + 1,
                    "bbox": _to_unrotated(page, table["bbox"]),
                    "rows": table["rows"],
                    "header": table["header"],
                    "title": title,
                    "title_from_previous_page": carried,
                    "row_count": len(table["rows"]),
                    "col_count": max(len(row) for row in table["rows"]),
                    "_box": table["bbox"],
                    "_columns": table["columns"],
                }
                entry["continues_from"] = (
                    _continues_from(entry, previous, page.rect.height) if position == 0 else None
                )
                # Checked when there is no page before, or it was read — with or without a table.
                entry["continuation_checked"] = position > 0 or index0 == 0 or before is not None
                found.append(entry)
            last = (index0, result)

        total = len(found)
        batch: list[dict] = []
        used = 0
        for entry in found[offset:]:
            if len(batch) >= max_tables:
                break
            public = _public(entry)
            size = len(json.dumps(public))
            if batch and used + size > max_chars:
                break
            batch.append(public)
            used += size

        more_available = offset + len(batch) < total
        result = {
            "count": len(batch),
            "total_tables": total,
            "offset": offset,
            "tables": batch,
            "unread_regions": unread,
            "pages_scanned": [i + 1 for i in indices],
            "source": os.path.abspath(path),
            "more_available": more_available,
        }
        if more_available:
            result["warnings"] = [
                f"{total} tables in scope; returned {len(batch)} starting at offset {offset}. "
                f"Call again with offset: {offset + len(batch)} for the rest, or narrow `pages`."
            ]
        return result
