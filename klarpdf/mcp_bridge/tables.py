"""M141 — ``get_tables``: rows an agent can read, without ever inventing a grid.

The tool reads a table when the document says where its cells are, and **declines** when it would
have to guess. Which of those applies is not predicted from the document's type — it is decided by
trying to read the page and then testing what came back (see :func:`read_page`). That matters
because real tables are inconsistent in ways no taxonomy anticipates, and a classifier over layouts
would have to be right about a document it has never seen. A test over the *result* only has to be
right about the result.

**Two readers, tried in order, because they fail in opposite places.**

* ``lines_strict`` reads a table whose cells are fully boxed in by drawn lines. Where it fires it is
  exact — measured over 14 documents, 15 tables, none damaged — because the document itself states
  every boundary. It returns nothing for a table that is only partly ruled.
* **Rules for rows, alignment for columns** (``horizontal_strategy="lines"`` with
  ``vertical_strategy="text"``) reads the far commoner filing shape: a rule above and below the
  header, nothing else. Taking the *rows* from the drawn rules is what makes this work — it stops
  the column inference from running away, which is exactly what it does when both axes are guessed:

      rules for rows, alignment for columns   ["Total net sales", "109,417", "94,036", "364,357"]
      both axes inferred                      ["Total net sales 109,417 ", "313,695"]

  (Apple's Q3 2026 10-Q, page 4.) This reader is the reason the tool covers SEC filings at all:
  ``lines_strict`` returns **zero** tables on Apple's 10-Q, NVIDIA's and Cisco's annual reports,
  LLY's proxy and the SpaceX prospectus. Measured: 22 tables, 2 damaged.

**Inferring both axes is deliberately not a third reader.** It was measured and rejected: it adds
~10 tables across the same corpus of which 4 are damaged, and its failure mode is the one an agent
cannot detect — it silently eats leading characters, turning "Beginning balance" into
"eginning balance" and "Total current assets" into "tal current assets". A page it would have read
is declined instead, and ``extract_text`` still returns every value on it (see :func:`ruled_pages`).

**The caller is never left guessing.** Every page in the requested range comes back either in
``tables`` or in ``unread_regions``; a page is never silently absent. So "this table could not be
read" is something the caller is *told* on that page, rather than something they must notice.
"""

from __future__ import annotations

import json
import os
import re

import pymupdf as fitz

from klarpdf.mcp_bridge.queries import _page_of, open_document, resolve_pages

MAX_TABLES = 50
"""How many tables one call returns before it stops and says so.

Its own cap rather than a share of another, for the reason ``DEFAULT_MAX_LINKS`` has one: a table is
a different kind of thing from a link, and the number that makes sense is "more than a real page
range holds". The measured worst case is 4 tables on a page.
"""

MAX_TABLE_CHARS = 60_000
"""How many characters of table JSON one call returns.

Beside :data:`MAX_TABLES`, not instead of it — the lesson ``DEFAULT_MAX_ANNOTATION_CHARS`` records.
A count cap does not bound a reply: one 92-row financial table serialises to over 20,000 characters,
so three of them would exceed what the caller wants while sitting far under the count cap.
"""

MIN_RULES = 3
"""Horizontal rules on a page before the ruled reader is attempted, and the threshold behind
:func:`ruled_pages`.

Measured over 204 pages of nine documents. At this value the scan **never misses** a page where
``get_tables`` returns a grid (recall 100%) while flagging about two pages for every one that has a
table; at 6 it starts missing real tables (recall 93%). Over-flagging is the cheap direction to be
wrong — a caller that checks one page too many loses a call, one that is never told loses the table.
"""

_MIN_WORDS_VERTICAL = 10
"""How many words must line up before a column boundary is believed, for the row-ruled reader.

PyMuPDF's default is 3, which on a justified page finds "columns" in the gaps *inside* sentences and
shatters every label. Measured across the corpus, 10 is the knee: below it labels fragment, above it
nothing further improves and whole tables start disappearing.
"""

_STUFFED_LIMIT = 0.15
"""Fraction of cells that may hold more than one line before a result is rejected.

This is the load-bearing acceptance test. A line break inside a cell means the reader merged rows
that the document keeps apart, and the values are then unusable however plausible the shape looks:

    ['6,016.44\\n1,409.23\\n763.04', '2,374.79\\n662.87\\n769.94', '843.59\\n366.68\\n367.13']

It is what correctly declines a bank statement and a brokerage statement whose printed rules do not
correspond to their rows — neither of which anyone anticipated. Fully ruled tables are held to a
looser bound, since a drawn cell may legitimately wrap its text.
"""

_RULED_STUFFED_LIMIT = 0.5

_MAX_SPLIT_LOSS = 0.35
"""How much of a region's content splitting may discard before the split is disbelieved.

**The guard on :func:`_split_prose_rows`, and it exists because that function deletes rows.** It is
right to: prose between two tables is not data, and dropping it is the point. But the same
signature — a tall row holding line breaks — is also what a *badly read table* produces, and there
the deleted rows hold everything. LLY's proxy page 64 is the measured case: 25 real rows with only
**four** ruled lines to divide them, so each band swallowed five rows into stuffed cells, the split
took those for separators, and what survived was the genuinely empty filler between them. It passed
every other test — not stuffed, not shattered, merely empty — and reported two confident tables
holding almost nothing.

The tell is unmissable once looked for: a real split discards the prose and keeps the table (LLY
page 56 drops **11%** of filled cells), while this drops **84%**, and a region needing no split at
all drops **0%**. So when the discarded share crosses this line the conclusion is not "here are the
pieces" but "the boundaries held the data, so these were never boundaries" — and the region is
dropped, which leaves the page to decline honestly.

The general lesson, which outlives this constant: **split-then-test lets anything the split removed
escape the test.** A test applied only to the residue cannot see what was thrown away, so whenever a
step discards input, something has to account for what it discarded.
"""

_TALL_ROW_FACTOR = 2.5
"""How many times the median row height marks a row as prose rather than data.

A row this tall holding line breaks is not a row — it is the heading and paragraph sitting *between*
two tables that the reader swept into one region. On LLY's proxy page 56 the data rows are 15.0 pt
and the two prose bands are 90.2 pt and 78.2 pt, so the separation is wide rather than marginal.
"""

_MIN_ROWS = 2
_MIN_COLS = 2

_TITLE_MAX_CHARS = 70
_TITLE_MAX_WORDS = 10
_TITLE_MAX_DIGIT_RATIO = 0.25

_TITLE_LEFT_TOLERANCE = 40.0
"""How far right of a table's left edge a caption may start.

Wide enough for the measured captions, which sit a little inside their table's bounding box (the
manual's "Music playback time" begins 17.6 pt right of its table), and narrow enough to reject a
heading sitting over a column rather than over the table.
"""

_TITLE_LOOKBACK = 160.0
"""How far above a table a caption may sit, once the prose between them is stepped over.

A caption is very often **not** the nearest thing above its table: the common report shape is
heading, then an introductory paragraph, then the table. LLY's proxy page 56 does it twice — the
caption sits 62.9 pt and 50.9 pt above its table with a paragraph filling the gap — so a rule that
takes the nearest block returns the paragraph and reports no title at all. The search therefore
steps over anything that does not read as a caption, and this bounds how far, so it cannot wander up
the page and adopt the previous section's heading.
"""

_ORPHAN_GAP = 120.0
"""How far below the last table a stranded title may sit and still be read as one.

Wide enough for the measured case (the manual's orphan sits 17 pt below its table, with the page
number 44 pt further down) and narrow enough that a footnote block halfway down an empty page is not
mistaken for a caption.
"""

_CONTINUATION_TOLERANCE = 2.0
_TOP_OF_PAGE = 0.15

_DECLINE_REASONS = {
    "declined-unruled": (
        "nothing on this page marks where the cells are — no drawn grid and no ruled rows, so the "
        "columns would have to be inferred from spacing, which silently drops characters"
    ),
    "declined-unreliable": (
        "there is ruling here, but the rows it produced did not hold together — values landed "
        "merged or cut, so reporting them as a grid would be worse than not"
    ),
}

_BARE_NUMBER = re.compile(r"^[\d,]+(\.\d+)?$")
_NUMBER_START = re.compile(r"^[\$€£₹]?\s*[\d,]")
_FIGURE = re.compile(r"^[\$€£₹(]?\s*[-–—]?[\d,]+(\.\d+)?\s*[%)]?$")
_BARE_DIGIT = re.compile(r"\d")
_WORD_END = re.compile(r"[A-Za-z]$")
_WORD_START = re.compile(r"^[a-z]")


def horizontal_rules(page: fitz.Page) -> int:
    """Count the near-horizontal ruled segments on ``page``.

    Deliberately cheap: measured at **314 pages/s**, against 158 for ``get_text`` and 5.9 for
    ``find_tables``. That ratio is what lets :func:`ruled_pages` ride along with ``extract_text``
    without changing its cost, and it is why this counts drawings rather than detecting tables.

    A rule is a line (or a rectangle flattened to one) that is level within 1 pt and longer than
    30 pt, which excludes underlined words and the hairlines in a logo.
    """
    found = 0
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                start, end = item[1], item[2]
                if abs(start.y - end.y) < 1 and abs(start.x - end.x) > 30:
                    found += 1
            elif item[0] == "re":
                rect = item[1]
                if rect.height < 2 and rect.width > 30:
                    found += 1
    return found


def _cells_of(rows: list[list[str | None]]) -> list[str]:
    return [c.strip() for row in rows for c in row if c and c.strip()]


def _stuffed_ratio(rows: list[list[str | None]]) -> float:
    """Fraction of *data* cells holding more than one line.

    The header row is excluded, and that is not a convenience: a column heading legitimately wraps
    (``"2025 Annual Stock\\nGrant"``), so counting it conflates "this heading is two lines wide" with
    "these rows were merged" — which cost a real table on LLY's proxy page 56, rejected at 0.167 by
    its own wrapped header while every data row underneath was clean.
    """
    cells = _cells_of(rows[1:] if len(rows) > 1 else rows)
    if not cells:
        return 1.0
    return sum(1 for c in cells if "\n" in c) / len(cells)


_SHATTER_LIMIT = 0.4
"""Fraction of adjacent filled cells that may look like one word cut in two.

The second acceptance test, and it draws a line that matters more than it first appears. Below this
value the damage is **splitting**: a label lands across two cells but every character survives, so
the caller can rejoin them —

    ['Net cash provided by', 'operating act', 'ivities', '$ 14,177 $', '14,193 $']

Above it the reading has stopped tracking the page and characters start **disappearing**, which is
the one failure an agent cannot detect because what arrives still looks like a table —

    ['Statutory federal inco', 'e tax rate', ...]     the 'm' is simply gone

Measured over the 69 tables this tool returns from the corpus, the gap is wide: prose swept into a
grid scores 0.75-1.00, the first result with real character loss scores 0.417, and everything at or
below 0.40 is split-but-complete. So this rejects five prose grids from a brokerage statement and a
pension statement, plus the two lossy readings, and touches nothing else.
"""


MIN_DIGIT_DEFICIT = 3
"""How many digits may go missing from a region before its reading is disbelieved.

Not zero, because a **document** can print a glyph twice: the designed report's page 15 draws `6+`
twice to simulate bold, so one digit is "missing" from a row that is entirely correct. Measured, the
gap is wide — every correct table in the corpus shows a deficit of **0** (one shows 1, the artifact
above) and every damaged one shows **8 to 20**. Three is comfortably between, and the cost of the
floor is stated rather than hidden: a single lost one- or two-digit value would slip under it.
"""


def digits_lost(page: fitz.Page, box: fitz.Rect, rows: list[list[str]]) -> int:
    """How many digits printed inside the region never reached a cell (TC-028).

    **The general form of every "the tool lost a value" defect this milestone has had**, and it
    replaces reasoning about *how* a reading went wrong with checking *what came back*. The invariant
    is one line: every digit printed in the table's region must appear in the returned rows.

    **It exists because :func:`cuts_a_figure` is structurally unable to protect the rows that need
    it.** That check requires two complete numeric cells in the row before it will look, and TC-028
    found the inversion: damage *removes* complete numeric cells, so the worse a row is mangled the
    less likely the guard is to fire. Apple's page 11 header keeps two `$ —` and no parseable figure,
    so it can never qualify — *the loss itself disqualifies the row from the guard meant to catch
    it*. Three Tesla pages lose a label and four figures from their final row and evade it the same
    way. Asking the region what it contained has no such hole: a row stripped to nothing is then the
    easiest case to see rather than the hardest.

    **Counting digits rather than words or figures is what makes it clean**, and each was measured.
    *Words* do not separate: Cisco's page 61 and SpaceX's page 251 legitimately lose 7-10% of theirs
    to split labels and dot leaders while losing no data. *Figures* misfire on a split header —
    LLY's page 56 breaks `2024` into `202` + `4`, which is visible, rejoinable, and deliberately
    tolerated, yet reads as a missing figure. Digits survive being split and do not survive being
    dropped, which is exactly the distinction the tool already draws everywhere else.

    A **deficit**, not a difference: the count may legitimately come out *higher* than the region
    holds, because :func:`recover_edge_row` adds a row from just outside the box. Four correct tables
    do exactly that.
    """
    region: dict[str, int] = {}
    for word in page.get_text("words"):
        centre_y = (word[1] + word[3]) / 2
        centre_x = (word[0] + word[2]) / 2
        if not (box.y0 - 1 <= centre_y <= box.y1 + 1 and box.x0 - 2 <= centre_x <= box.x1 + 2):
            continue
        for character in word[4]:
            if character.isdigit():
                region[character] = region.get(character, 0) + 1
    if not region:
        return 0
    returned: dict[str, int] = {}
    for row in rows:
        for cell in row:
            for character in cell or "":
                if character.isdigit():
                    returned[character] = returned.get(character, 0) + 1
    return sum(max(0, count - returned.get(d, 0)) for d, count in region.items())


def cuts_a_figure(page: fitz.Page, rows: list[list[str | None]]) -> bool:
    """Does a column edge run straight through a number in a row that carries data? (TC-027.)

    The cardinal failure, and the one :func:`_shattered_ratio` cannot see. Amazon's Q2 2026 release,
    page 6, is returned as a confident 8-column grid for a 7-column statement, with the boundary
    drawn through the middle of a value:

        ['Net cash p', 'rovided by (used in) investing activities',
         '(39,424)', '(79,245)', '(69,227) (1', '43,457) (', '123,569) (', '216,775']
                                              ^^^^^^^^^^^^^^^  (143,457) split across two columns

    Three things hid it. ``_WORD_START`` requires a **lowercase** continuation, so the all-caps
    ``EQUIV`` / ``ALENTS`` on the same page is invisible; the ratio never looks at **digits** at all;
    and over a 46x8 table roughly 200 legitimately-unmatched numeric pairs dilute what it does see to
    0.131, comfortably under a 0.4 limit.

    **Asking the page settles all three.** Two adjacent fragments that rejoin into a word the page
    actually contains — while neither fragment is itself such a word — is a cut, whatever its case or
    character class. Restricted to **figures in rows that carry at least two complete numeric
    cells**, because that is precisely where a misplaced edge stops being cosmetic: a split label is
    visible and rejoinable, a split *value* silently misaligns the columns it lands between. Both
    restrictions earn their place — LLY's proxy page 56 splits ``202``/``4`` in its header row and is
    otherwise a good table, and Cisco's 10-K page 61 cuts 26 labels while every figure stays whole.

    Measured over the ten tables this milestone reads correctly plus the one it does not: **Amazon
    page 6 is the only one flagged.**
    """
    vocabulary = {w[4] for w in page.get_text("words")}
    for row in rows:
        if sum(1 for c in row if c and _FIGURE.match(c.strip())) < 2:
            continue
        for left, right in zip(row, row[1:]):
            left = (left or "").strip()
            right = (right or "").strip()
            if not left or not right:
                continue
            tail = left.split()[-1]
            head = right.split()[0]
            if not _BARE_DIGIT.search(tail) or not _BARE_DIGIT.search(head):
                continue
            if tail + head in vocabulary and tail not in vocabulary:
                return True
    return False


def _shattered_ratio(rows: list[list[str | None]]) -> float:
    """How often an adjacent pair of cells reads as a single word broken across a column edge."""
    pairs = cut = 0
    for row in rows:
        for left, right in zip(row, row[1:]):
            left = (left or "").strip()
            right = (right or "").strip()
            if not left or not right:
                continue
            pairs += 1
            if _WORD_END.search(left) and _WORD_START.match(right):
                cut += 1
    return cut / pairs if pairs else 0.0


def _acceptable(rows: list[list[str | None]], stuffed_limit: float) -> bool:
    """Whether a block of rows may be reported as a table.

    Structural only, and that is the honest scope: it catches a result that came apart, not every
    result that is wrong. Two of the 22 tables the row-ruled reader returns across the corpus pass
    this and are still damaged, so a table in the reply means "this parsed cleanly", never "this is
    guaranteed correct".

    **Applied after splitting, never before**, which is not a detail. A region holding three tables
    with their headings and paragraphs between them is *legitimately* full of line breaks — LLY's
    proxy page 56 measures 0.158 against a 0.15 limit — so testing the region whole rejects three
    perfectly readable tables on the strength of the prose separating them. Split first, then judge
    each piece on its own.
    """
    if len(rows) < _MIN_ROWS:
        return False
    if max((len(row) for row in rows), default=0) < _MIN_COLS:
        return False
    if not _cells_of(rows):
        return False
    if not [h for h in rows[0] if h and h.strip()]:
        return False
    if _stuffed_ratio(rows) > stuffed_limit:
        return False
    return _shattered_ratio(rows) <= _SHATTER_LIMIT


def looks_like_title(text: str) -> bool:
    """Whether a free text block reads as a table's caption rather than prose or a stray data row.

    Three tests, each of which earns its place against a real failure of the rule this replaces
    (*"the nearest free text block above the table"*, which PLAN.md §M141 originally specified).
    Left to itself that rule returns a **column header** on SpaceX page 251 (``December 31, 2025
    2024``), a **data row** on Apple page 6 (``Cash and cash equivalents $ 39,544 $ 35,934``) and a
    **sentence** on SpaceX page 274. So a caption must be short, must not read as a sentence, and
    must not be mostly numbers. Measured against 15 real blocks drawn from the corpus: 14 correct.
    """
    # A caption is one line. A *row* is not, and a row is what turns up here when the reader's
    # bounding box stops a line short of the table — the header or the last data row is then loose
    # text sitting immediately above or below it, and it passes every other test easily:
    # "Mode\nOperating time" reads as a plausible caption, and "Codec Hudson\n24 hours" as a
    # plausible stranded one. Both carry a line break because they are several cells in one block.
    if "\n" in text.strip():
        return False
    compact = " ".join(text.split())
    if not compact or len(compact) > _TITLE_MAX_CHARS:
        return False
    if len(compact.split()) > _TITLE_MAX_WORDS:
        return False
    if compact.endswith((".", ";", ":")) and not compact.endswith("..."):
        return False
    # Currency marks and thousands separators count as numeric, not as text. Without them
    # "Cash and cash equivalents $ 39,544 $ 35,934" scores 0.22 on bare digits and passes as a
    # caption — it is a data row whose table bbox stopped one line short of it (Apple 10-Q p6).
    numeric = sum(1 for ch in compact if ch.isdigit() or ch in "$€£₹,")
    return numeric <= _TITLE_MAX_DIGIT_RATIO * len(compact)


def _free_blocks(page: fitz.Page, boxes: list[fitz.Rect]) -> list[tuple[fitz.Rect, str]]:
    """Text blocks on ``page`` that lie outside every detected table region, in reading order.

    "Outside" is judged by majority area rather than by intersection, because a caption often
    overlaps a table's bounding box by a point or two without being part of it.
    """
    free: list[tuple[fitz.Rect, str]] = []
    for block in page.get_text("blocks"):
        rect = fitz.Rect(block[:4])
        text = (block[4] or "").strip()
        if not text:
            continue
        area = rect.get_area()
        if area > 0 and any(
            box.intersects(rect) and (rect & box).get_area() > 0.5 * area for box in boxes
        ):
            continue
        free.append((rect, text))
    free.sort(key=lambda entry: entry[0].y0)
    return free


def _title_above(blocks: list[tuple[fitz.Rect, str]], box: fitz.Rect) -> str | None:
    """The nearest caption-like block above ``box``, starting near its left edge.

    The left-edge test is what separates a caption from a **column heading**. On LLY's proxy page 56
    the nearest block above the first table is ``"Salary"`` — three columns in, at x=238.9 against
    the table's x=54.0 — which passes every text test a caption has to pass and is not one. A
    caption begins at the body's left margin; a column heading sits over its column.

    **Above only** — looking below as well was tried and is wrong, because it collides with the
    stranded-title rule on a real document. The manual's page 29 carries *"Headphone cable connected
    (power is turned on)"* 17 pt below its last table; that line reads exactly like a caption and is
    **not** that table's title, but the title of the table at the top of page 30. A below-lookup
    would attach it to the wrong table. Searching the corpus for caption-style titles (`Table 3:`,
    `Exhibit 2 –`, `Figure 1.`) across six documents found none at all, so a title *below* its table
    is a shape these documents never take and the rule stays where the evidence is.
    """
    for rect, text in reversed(blocks):
        if rect.y1 > box.y0 + 2:
            continue
        if box.y0 - rect.y1 > _TITLE_LOOKBACK:
            return None
        if rect.x0 > box.x0 + _TITLE_LEFT_TOLERANCE:
            continue
        if looks_like_title(text):
            return " ".join(text.split())
    return None


def _orphan_title(blocks: list[tuple[fitz.Rect, str]], last_box: fitz.Rect | None) -> str | None:
    """A caption stranded below the last table on a page, which titles the next page's first table.

    The manual's page 29 ends with *"Headphone cable connected (power is turned on)"* and then runs
    out of page; the table it names is at the top of page 30. This is also what makes the
    continuation flag safe to compute — see :func:`_continues_from`.
    """
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


def _column_edges(table) -> list[float]:
    first = table.rows[0].cells if table.rows else []
    return [round(cell[0], 1) for cell in first if cell]


def _split_prose_rows(table) -> list[tuple[list[list[str | None]], float, float]]:
    """Split one detected region wherever prose sits between two tables.

    A reader that takes its rows from drawn rules will sweep several tables into one region when the
    rules run the width of the page, and the headings and paragraphs *between* those tables become
    rows of it. LLY's proxy page 56 is the measured case — three tables, each introduced by a
    heading and a paragraph, returned as one 19-row region whose rows 5 and 12 hold the prose.

    Those rows are recognisable without reading them: they are **much taller than the table's own
    rows and hold line breaks**. On that page the data rows are 15.0 pt and the two prose bands are
    90.2 pt and 78.2 pt. Splitting there recovers the three tables, and the same boundary tells
    :func:`_read_page` which band to look in for each one's title — so the caller's two problems,
    a merged region and a missing caption, have one answer.
    """
    rows = table.extract()
    geometry = list(table.rows)
    if len(geometry) != len(rows) or len(rows) < 3:
        return [(rows, table.bbox[1], table.bbox[3])]

    heights = sorted(row.bbox[3] - row.bbox[1] for row in geometry)
    median = heights[len(heights) // 2]
    if median <= 0:
        return [(rows, table.bbox[1], table.bbox[3])]

    groups: list[tuple[list[list[str | None]], float, float]] = []
    current: list[tuple[list[str | None], object]] = []

    def flush() -> None:
        if len(current) >= _MIN_ROWS:
            groups.append(
                ([cells for cells, _ in current], current[0][1].bbox[1], current[-1][1].bbox[3])
            )

    for cells, row in zip(rows, geometry):
        tall = (row.bbox[3] - row.bbox[1]) > _TALL_ROW_FACTOR * median
        prose = any(c and "\n" in c for c in cells)
        # A row carrying figures is data, whatever its height (TC-026 HIGH 1). A heading and its
        # paragraph do not have numbers in the numeric columns; a *wrapped* data row does, and it
        # is otherwise indistinguishable — tall, and full of line breaks. Alphabet's 2026 10-K
        # page 51 lost `['Class A, Class B, and Class C stoc', '84,800', '93,126']` exactly this
        # way, which left a balance sheet whose returned components miss their returned total by
        # 93,126. The loss was 3.1% of the region, far under `_MAX_SPLIT_LOSS`, so the guard that
        # catches the gross case could never have caught this one.
        if tall and prose and not any(c and _FIGURE.match(c.strip()) for c in cells):
            flush()
            current = []
            continue
        current.append((cells, row))
    flush()

    if not groups:
        return [(rows, table.bbox[1], table.bbox[3])]
    return groups


def _line_text(words: list, rect: fitz.Rect) -> str:
    """The words inside ``rect``, joined, with the page's own line breaks kept."""
    inside = [
        w
        for w in words
        if fitz.Rect(w[:4]).intersects(rect)
        and (fitz.Rect(w[:4]) & rect).get_area() > 0.4 * fitz.Rect(w[:4]).get_area()
    ]
    lines: dict[float, list[str]] = {}
    for word in sorted(inside, key=lambda w: (round(w[1], 0), w[0])):
        lines.setdefault(round(word[1], 0), []).append(word[4])
    return "\n".join(" ".join(parts) for _, parts in sorted(lines.items()))


def recover_left_margin(page: fitz.Page, table, rows: list[list[str]]) -> list[list[str]]:
    """Restore row labels clipped by the region's left edge (TC-026 HIGH 3).

    A financial statement outdents its section headers, and the first column's left edge is derived
    from the *indented* data rows — so anything starting left of it is cut at a fixed x, mid-word,
    and silently. Measured across two unrelated SEC filers:

        '-current assets:'            was  'Non-current assets:'
        'ent liabilities:'            was  'Current liabilities:'
        'mitments and contingencies'  was  'Commitments and contingencies'
        'nce as of December 31, 2022' was  'Balance as of December 31, 2022'

    This is the harm the tool declines whole pages to avoid — *"the columns would have to be
    inferred from spacing, which silently drops characters"* — happening inside the happy path,
    where nothing flags it.

    **The repair only ever adds a prefix**, and that condition is what makes it safe rather than
    merely better. Re-reading a cell from the page's true left margin can also *re-cut* it on the
    right, because a label may run past its own column into the next one: Cisco's 10-K page 61 turns
    ``'flows from i'`` into ``'Cash flows from'``, gaining a word and losing the ``i``. So a rebuild
    is accepted only when the original survives inside it as a suffix — 9 repairs applied across
    Apple and Alphabet, and all 25 of Cisco's rows correctly left alone.
    """
    box = fitz.Rect(table.bbox)
    words = page.get_text("words")
    margin = min(
        (w[0] for w in words if box.y0 - 2 <= w[1] and w[3] <= box.y1 + 2), default=box.x0
    )
    if margin >= box.x0 - 1:
        return rows

    repaired = [list(row) for row in rows]
    for index, geometry in enumerate(table.rows):
        if index >= len(repaired) or not geometry.cells or not geometry.cells[0]:
            continue
        original = repaired[index][0] if repaired[index] else ""
        if not original.strip():
            continue
        top, bottom = geometry.bbox[1], geometry.bbox[3]
        if not any(w[0] < box.x0 - 1 and top - 1 <= (w[1] + w[3]) / 2 <= bottom + 1 for w in words):
            continue
        widened = _line_text(words, fitz.Rect(margin - 1, top, geometry.cells[0][2], bottom))
        flat, was = " ".join(widened.split()), " ".join(original.split())
        if flat and flat != was and flat.endswith(was):
            repaired[index][0] = widened
    return repaired


_RUN_GAP = 10.0
"""Horizontal gap that separates two column entries on one line.

Within `December 31, 2025` the word gaps are ~2 pt; between `2025` and `June` there are 20.9. So a
run is what belongs to one column, and assigning by run rather than by word is what puts a heading
that straddles a boundary on the correct side of it.
"""


def _runs(line: list) -> list[list]:
    """Split one line's words into runs separated by more than :data:`_RUN_GAP`."""
    grouped: list[list] = []
    current: list = []
    for word in sorted(line, key=lambda w: w[0]):
        if current and word[0] - current[-1][2] > _RUN_GAP:
            grouped.append(current)
            current = []
        current.append(word)
    if current:
        grouped.append(current)
    return grouped


def _column_of(centre: float, columns: list) -> int | None:
    """Index of the column containing ``centre``, else the nearest one.

    Strict containment first and a tolerance nowhere: a ±2 pt slack matched the *label* column for
    a heading whose centre sat 0.25 pt inside the next one, which is how `December 31, 2025` was
    first put back in the wrong place.
    """
    for index, column in enumerate(columns):
        if column[0] <= centre < column[2]:
            return index
    if not columns:
        return None
    return min(
        range(len(columns)),
        key=lambda i: abs(centre - (columns[i][0] + columns[i][2]) / 2),
    )


def recover_edge_row(page: fitz.Page, table, rows: list[list[str]]) -> list[list[str]]:
    """Restore a data row sitting just outside the ruled band (TC-026 HIGH 1).

    The row-ruled reader takes its band from the drawn rules, and a statement's first line often
    sits *above* the first rule. Apple's Q3 2026 10-Q page 6 begins its balance sheet at y=152.6
    while the band starts at y=163.0, so **`Cash and cash equivalents, 39,544 / 35,934` — its
    largest current asset — is simply absent**, and the returned rows miss the returned total by
    exactly that figure.

    Recovered only on the signature of a data row and nothing looser: within **one** median row
    height of the edge, and carrying a **numeric value in every data column**. That is what
    separates it from the things that also sit next to a band — a column header (`Name / 2024 Annual
    Base Salary`), a section label (`Current assets`), a footnote, a rule of underscores. Checked
    against all five: the Apple row is recovered and none of the others is.
    """
    box = fitz.Rect(table.bbox)
    # Column geometry from the widest row, not row 0 — row 0 is very often the damaged header.
    widest = max(table.rows, key=lambda r: sum(1 for c in r.cells if c)) if table.rows else None
    all_columns = [c for c in widest.cells if c] if widest else []
    columns = all_columns[1:]
    if not columns or not rows:
        return rows
    heights = sorted(r.bbox[3] - r.bbox[1] for r in table.rows)
    median = heights[len(heights) // 2]
    if median <= 0:
        return rows

    words = page.get_text("words")
    width = max(len(row) for row in rows)
    for at_top, low, high in (
        (True, box.y0 - median, box.y0 - 1),
        (False, box.y1 + 1, box.y1 + median),
    ):
        line = [w for w in words if low <= (w[1] + w[3]) / 2 <= high]
        if not line:
            continue
        values = []
        for column in columns:
            found = [
                w[4]
                for w in line
                if column[0] - 2 <= (w[0] + w[2]) / 2 <= column[2] + 2 and _FIGURE.match(w[4])
            ]
            values.append(found[0] if found else None)
        if not values or any(value is None for value in values):
            continue
        # Assemble by *run*, not by taking one figure per column, which is how this function
        # itself came to drop data (TC-027): Amazon's page 10 carries `December 31, 2025` and
        # `June 30, 2026` over its two figure columns, and keeping only the first figure in each
        # returned `['December', '31,', '30,']` — both years gone, from the code meant to recover
        # them. A run also lands in the right column where a single word does not: `December 31,
        # 2025` straddles the boundary at x=449 while the run's centre, 449.25, sits inside it.
        recovered = [""] * width
        for run in _runs(line):
            index = _column_of((run[0][0] + run[-1][2]) / 2, all_columns)
            if index is None or index >= width:
                continue
            joined = " ".join(w[4] for w in run)
            recovered[index] = f"{recovered[index]} {joined}".strip()
        if not any(cell for cell in recovered):
            continue
        rows = [recovered, *rows] if at_top else [*rows, recovered]
    return rows


def split_is_credible(whole: list[list[str | None]], groups: list[list[list[str | None]]]) -> bool:
    """Did splitting remove *separators*, or did it remove the table?

    :func:`_split_prose_rows` deletes rows, and rightly — the prose between two tables is not data.
    But the signature it keys on, a tall row holding line breaks, is also what a badly read table
    produces, and there the deleted rows hold everything. Counting what was discarded is the only
    check that can tell those apart, because every other test in this module runs on the survivors
    and therefore cannot see what is missing.

    Measured: a genuine split drops **11%** of filled cells, a region needing no split drops
    **0%**, and the failure this exists for drops **84%**. See :data:`_MAX_SPLIT_LOSS`.
    """
    before = len(_cells_of(whole))
    if not before:
        return False
    after = sum(len(_cells_of(rows)) for rows in groups)
    return (before - after) / before <= _MAX_SPLIT_LOSS


def repair_negative(cell: str) -> str:
    """Restore the closing bracket on an accounting negative that a column boundary split.

    A negative is printed ``(1,234)``. When a column edge falls inside it the closer lands in the
    next cell and the value reads **positive** — a sign error, in a financial statement, that
    nothing downstream can detect. Measured over pages 387-389 of a prospectus: of 36 cells left
    with an unbalanced bracket, **35 keep the opening ``(`` and lose only the ``)``**, so a leading
    bracket with nothing closing it is unambiguously a negative and restoring it is a rule rather
    than a guess.

    The 36th lost its *opening* bracket instead (``1,051.42)``) and still reads positive. That is
    documented in the tool's contract and deliberately not coded around — repairing it would mean
    treating a stray closing bracket as a sign, which is not sound in the other direction.
    """
    text = cell.strip()
    if not text.startswith("(") or ")" in text:
        return cell
    # The content must be a *number*, not merely contain a digit: "(see note 4" opens a bracket and
    # holds a digit, and closing it would invent a value where the document has a footnote marker.
    if not _BARE_NUMBER.match(text[1:]):
        return cell
    return f"{text})"


def rejoin_split_bracket(row: list[str]) -> list[str]:
    """Move an opening bracket that landed at the end of the *previous* cell (TC-026 HIGH 2).

    The other half of :func:`repair_negative`, and the more dangerous half. A column edge falling
    inside ``(103,773)`` can strand the opening bracket on the cell to its left:

        ['Purchases of marketable securities', '(77,858)', '(86,679) (', '103,773']
                                                                     ^^^        ^^^^^^^
                                                        stranded here   reads as POSITIVE

    Measured on Alphabet's 2026 10-K, page 55: two cash-flow lines arrive unsigned, and the printed
    subtotal proves both are negative (``-91,447 -103,773 +83,240 -5,716 +1,367 -1,592 -2,370 =
    -120,291``). Page 54 is full of the same shape. A dropped label is visibly wrong; **a sign flip
    on a cash-flow line is invisible and arithmetically plausible**, which is why this is repaired
    rather than merely disclosed.

    The rule is unambiguous in a way the residue in :func:`repair_negative` is not: a lone ``(`` at
    the *end* of a cell cannot belong to that cell — nothing closes it there — and the only thing it
    can be opening is the value immediately to its right.
    """
    out = list(row)
    for index in range(len(out) - 1):
        left = (out[index] or "").rstrip()
        if not left.endswith("(") or left.count("(") <= left.count(")"):
            continue
        right = (out[index + 1] or "").strip()
        if not right or not _NUMBER_START.match(right) or right.startswith("("):
            continue
        out[index] = left[:-1].rstrip()
        out[index + 1] = f"({right}"
    return out


def _repair_rows(rows: list[list[str | None]]) -> list[list[str]]:
    repaired = []
    for row in rows:
        cells = rejoin_split_bracket([c or "" for c in row])
        repaired.append([repair_negative(c) if c else "" for c in cells])
    return repaired


def read_page(page: fitz.Page) -> tuple[list[dict], str]:
    """Read every table on one page, or decline. Returns ``(tables, mode)``.

    ``mode`` is one of ``ruled`` (a drawn grid), ``row-ruled`` (rules for rows, alignment for
    columns), or one of two declines. *Which reader succeeded* is not reported to the caller — the
    owner's direction, since no post-processing depends on it — but it is what the tests assert
    against, and it is the reason this returns it rather than logging it.

    The two declines **are** distinguished, because they mean different things to a caller holding
    the page. ``declined-unruled`` is "there is nothing here to read a grid from". ``declined-
    unreliable`` is "there is, and it did not come out trustworthy" — which is the more interesting
    answer, and saying the first when the second is true (this page has four ruled lines) would be
    a small lie in the one field whose whole job is to explain a refusal.
    """
    strict = page.find_tables(strategy="lines_strict").tables
    described = _describe(
        page, strict, split=False, stuffed_limit=_RULED_STUFFED_LIMIT, header_known=True
    )
    if described:
        return described, "ruled"

    if horizontal_rules(page) >= MIN_RULES:
        mixed = page.find_tables(
            vertical_strategy="text",
            horizontal_strategy="lines",
            min_words_vertical=_MIN_WORDS_VERTICAL,
        ).tables
        described = _describe(
            page, mixed, split=True, stuffed_limit=_STUFFED_LIMIT, header_known=False
        )
        if described:
            return described, "row-ruled"
        return [], "declined-unreliable"

    return [], "declined-unruled"


def _describe(
    page: fitz.Page, found: list, *, split: bool, stuffed_limit: float, header_known: bool
) -> list[dict]:
    """Split each candidate region, keep the pieces that pass, and give each one its title.

    The ordering is the point: **split, then test, then title**. Testing before the split rejects a
    region for the prose that separates its tables, and titling before the split has nothing to
    attach a caption to but the whole band.

    ``header_known`` decides whether the first row may be *called* a header, and it is false for the
    row-ruled reader on purpose. A reader that takes its rows from drawn rules routinely starts its
    band one row inside the table, so the real header is left outside and ``rows[0]`` is the first
    line of data — Apple's 10-Q page 4 comes back with ``["Products $", "78,678 $", …]`` in that
    position. PyMuPDF's own header detection is no help: it reports ``external=False`` and returns
    the same first row, i.e. it assumes what it is supposed to determine. So the field is reported
    where the drawn grid makes it true and set to null where it would be a guess, and the caller is
    told in ``klarpdf://docs/get_tables`` to look at ``title`` for a header that went missing.
    """
    described: list[dict] = []
    for table in found:
        whole = table.extract()
        groups = _split_prose_rows(table) if split else [(whole, table.bbox[1], table.bbox[3])]

        # What the split threw away has to be accounted for, or it escapes every test below.
        if not split_is_credible(whole, [rows for rows, _, _ in groups]):
            continue

        for rows, top, bottom in groups:
            if not _acceptable(rows, stuffed_limit):
                continue
            # A column edge through a value misaligns every figure it lands between, and unlike a
            # split label it leaves nothing for the caller to notice (TC-027).
            if cuts_a_figure(page, rows):
                continue
            box = fitz.Rect(table.bbox[0], top, table.bbox[2], bottom)
            repaired = _repair_rows(rows)
            # Recover what the region's own boundaries cut off, before anything is reported. Only
            # for the row-ruled reader: a drawn grid states its own edges, so there is nothing
            # outside them that belongs inside (TC-026 HIGH 1, HIGH 3).
            # Digits printed in this region that never reached a cell. Whatever went wrong —
            # a cut, a merge, a row dropped whole — the grid is not what the page says.
            if split and digits_lost(page, box, repaired) >= MIN_DIGIT_DEFICIT:
                continue
            if split:
                repaired = recover_left_margin(page, table, repaired)
                if len(groups) == 1:
                    grown = recover_edge_row(page, table, repaired)
                    if len(grown) != len(repaired):
                        box = fitz.Rect(box.x0, min(box.y0, table.bbox[1]), box.x1, box.y1)
                    repaired = grown
            described.append(
                {
                    "bbox": [round(v, 1) for v in box],
                    "header": repaired[0] if header_known else None,
                    "rows": repaired,
                    "row_count": len(repaired),
                    "col_count": max(len(row) for row in repaired),
                    "column_edges": _column_edges(table),
                }
            )
    described.sort(key=lambda entry: entry["bbox"][1])

    # Titles are resolved against the *split* boxes, never the regions they came from. That is what
    # makes a heading sitting between two tables reachable at all: inside the original region it is
    # not free text, and every table on the page would fall back to whatever preceded the region —
    # which on LLY's proxy page 56 is a column heading three columns in.
    blocks = _free_blocks(page, [fitz.Rect(entry["bbox"]) for entry in described])
    for entry in described:
        entry["title"] = _title_above(blocks, fitz.Rect(entry["bbox"]))
    return described


def _continues_from(entry: dict, previous: dict | None, page_height: float) -> int | None:
    """Whether ``entry`` may be the continuation of the previous page's last table.

    **Flagged, never merged**, and the manual is why. Its page 29 second table and its page 30 table
    have identical column edges ``[36.4, 265.1]`` and identical headers while being different
    tables — 24 hours against 28 hours. Geometry alone would glue them together and the reply would
    be a confident lie. What separates them is the stranded title on page 29, so a table that
    received one is never reported as a continuation.
    """
    if previous is None or entry.get("title_from_previous_page"):
        return None
    if entry["bbox"][1] > _TOP_OF_PAGE * page_height:
        return None
    if entry["col_count"] != previous["col_count"]:
        return None
    mine, theirs = entry["column_edges"], previous["column_edges"]
    if len(mine) != len(theirs):
        return None
    if any(abs(a - b) > _CONTINUATION_TOLERANCE for a, b in zip(mine, theirs)):
        return None
    return previous["page"]


def ruled_pages(
    path: str, pages: list[int] | None = None, password: str | None = None
) -> list[int]:
    """Pages in scope carrying enough ruling that ``get_tables`` may return a grid for them.

    The discovery path for ``get_tables``, and the answer to a real gap: a caller reading a document
    with ``extract_text`` receives every value a table holds but nothing saying it *was* a table, so
    a tool that would hand back the grid goes unused. Detecting tables to find out is not an option
    — ``find_tables`` is 27x the cost of ``get_text`` and would turn a cheap call expensive — but
    counting ruled lines is **half** the cost of reading the text, so this rides along free.

    Over-inclusive on purpose. Measured across 204 pages of nine documents it flags every page where
    ``get_tables`` returns a grid, and roughly two others for each of them; see :data:`MIN_RULES`.
    """
    with open_document(path, password) as vdoc:
        indices = resolve_pages(vdoc, pages)
        return [i + 1 for i in indices if horizontal_rules(_page_of(vdoc, i)) >= MIN_RULES]


def tables(
    path: str,
    pages: list[int],
    *,
    password: str | None = None,
    max_tables: int = MAX_TABLES,
    max_chars: int = MAX_TABLE_CHARS,
    offset: int = 0,
) -> dict:
    """Every table on ``pages`` that can be read without guessing, plus the regions that cannot.

    ``pages`` is required rather than defaulting to the whole document, and the cost is why: reading
    tables runs at **5.9 pages/s** against ``get_text``'s 158, so a 572-page filing would be a
    ninety-second call. An agent narrows with ``get_outline``, ``search`` or ``extract_text``'s
    ``table_pages`` first.

    Every page asked for appears in exactly one of ``tables`` or ``unread_regions``. That is the
    contract the caller relies on: a page with no entry in either would be indistinguishable from a
    page with no table, and the whole point of declining out loud is that the difference is visible.
    """
    if offset < 0:
        raise ValueError(f"offset must be >= 0; got {offset}")
    if not pages:
        raise ValueError(
            "get_tables needs an explicit page range — reading tables is ~27x the cost of reading "
            "text, so a whole-document scan is refused. Narrow with get_outline, search, or "
            "extract_text's `table_pages`."
        )

    with open_document(path, password) as vdoc:
        indices = resolve_pages(vdoc, pages)
        found: list[dict] = []
        unread: list[dict] = []
        carried: str | None = None
        previous: dict | None = None

        for index0 in indices:
            page = _page_of(vdoc, index0)
            described, mode = read_page(page)
            if not described:
                unread.append(
                    {
                        "page": index0 + 1,
                        "bbox": [round(v, 1) for v in page.rect],
                        "reason": _DECLINE_REASONS[mode],
                        "suggestion": (
                            f"extract_text on page {index0 + 1} returns every value in reading "
                            f"order, or render_page to look at it"
                        ),
                    }
                )
                carried, previous = None, None
                continue

            for position, entry in enumerate(described):
                entry["page"] = index0 + 1
                entry["title_from_previous_page"] = False
                if position == 0 and carried and not entry["title"]:
                    entry["title"] = carried
                    entry["title_from_previous_page"] = True
                entry["continues_from"] = (
                    _continues_from(entry, previous, page.rect.height) if position == 0 else None
                )
                found.append(entry)

            boxes = [fitz.Rect(e["bbox"]) for e in described]
            carried = _orphan_title(_free_blocks(page, boxes), boxes[-1] if boxes else None)
            previous = described[-1] | {"page": index0 + 1}

        total = len(found)
        batch: list[dict] = []
        used = 0
        for entry in found[offset:]:
            if len(batch) >= max_tables:
                break
            size = len(json.dumps(entry))
            # Always take one: an empty batch with `more_available` set pages forever.
            if batch and used + size > max_chars:
                break
            batch.append(entry)
            used += size

        for entry in batch:
            entry.pop("column_edges", None)

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
