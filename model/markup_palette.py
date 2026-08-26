"""The text-markup colour palettes, and the name ↔ RGB lookups over them (M101).

**Why this is not in** :mod:`viewer.markup_style`, **where it used to live.** The two tuples below
are pure data — five highlight swatches and four line swatches — but that module imports Qt on line
41, and :mod:`mcp_bridge` may never import Qt (``tests/test_mcp_no_qt.py`` asserts it in a fresh
interpreter). So the bridge's ``annotate`` had a choice between duplicating the palette and lifting
it somewhere both sides can reach. Duplicating it would have been the wrong one, and not merely on
tidiness grounds:

**Colour is how a person sorts marks, so a second copy is a correctness bug waiting.** The M101
workflow is "highlight what matters, let a human review it, then act on what they approved" — and
the channel carrying the human's verdict is the colour. If the bridge's orange were a *separate*
constant from the picker's orange, the two would agree until someone adjusted one of them, and then
the same word would name two RGB values: marks made by the agent and marks made by hand in the app
would silently fall into different buckets, and a filter on "orange" would return half the document's
orange. Sharing the tuple makes that impossible by construction rather than by a test that has to be
remembered.

:mod:`viewer.markup_style` re-exports both names, so every existing importer is unaffected.
"""

from __future__ import annotations

# Curated and small on purpose: these are the colours people actually reach for when marking up
# text, and a short list is faster than a colour wheel. Two sets, because the two jobs differ —
# a highlighter lays a translucent wash *behind* the words, while an underline / strikeout draws
# an opaque proofing line *through* them.
HIGHLIGHT_COLORS = (
    ("Yellow", (1.0, 0.86, 0.10)),      # the classic marker, and Highlight's own default
    ("Green", (0.55, 0.92, 0.45)),
    ("Blue", (0.55, 0.80, 1.00)),
    ("Pink", (1.00, 0.65, 0.85)),
    ("Orange", (1.00, 0.72, 0.30)),
)
TEXT_LINE_COLORS = (
    ("Red", (0.86, 0.10, 0.10)),        # redline red — the editing convention
    ("Blue", (0.13, 0.35, 0.85)),
    ("Green", (0.13, 0.60, 0.20)),
    ("Black", (0.0, 0.0, 0.0)),
)

# Which palette a mark type draws from. Underline and strikeout share one: both are proofing lines,
# and the app offers them the same swatches. Note the two palettes are **not** interchangeable —
# there is no orange line and no red highlight — which is why a name is resolved against the type's
# own palette rather than against the union (M101).
PALETTES: dict[str, tuple[tuple[str, tuple[float, float, float]], ...]] = {
    "highlight": HIGHLIGHT_COLORS,
    "underline": TEXT_LINE_COLORS,
    "strikeout": TEXT_LINE_COLORS,
}

# How close two colours must be to count as the same swatch. Matches ``page_edits._TOL``'s intent:
# a value that survived a PDF float round-trip is not bit-identical to the one that was written.
EXACT_TOLERANCE = 0.01

# How far a colour may sit from the nearest swatch and still borrow its *name*. Deliberately loose,
# because on the read side most marks were not made by this app: Acrobat's red is (1, 0, 0), which
# is 0.199 away from our redline red and obviously "red" to anyone looking at it. The ceiling is set
# just under the closest pair of swatches in either palette — Yellow to Orange, 0.244 apart — so a
# name is never borrowed across a distance greater than the gap between two named colours. Anything
# further away (mid-grey, say) gets no name at all rather than a misleading one.
NAME_TOLERANCE = 0.22


def _distance(a: tuple, b: tuple) -> float:
    """Plain Euclidean distance in RGB. Not perceptually uniform, and deliberately not: the job is
    to recognise a swatch that round-tripped through a PDF, not to model human vision."""
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def color_for_name(name: str, mark_type: str) -> tuple[float, float, float] | None:
    """The RGB for a swatch ``name`` in ``mark_type``'s palette, or ``None`` if it has none.

    Case-insensitive, since a caller types ``"orange"`` and the palette says ``"Orange"``. Returning
    ``None`` rather than a default is what lets the caller report *which* names were available —
    M106's rule that an unrecognised input is an error, not a silent fallback.
    """
    for candidate, rgb in PALETTES.get(mark_type, ()):
        if candidate.lower() == name.strip().lower():
            return rgb
    return None


def names_for(mark_type: str) -> tuple[str, ...]:
    """The swatch names ``mark_type`` accepts, in palette order — for an error message."""
    return tuple(name for name, _ in PALETTES.get(mark_type, ()))


def nearest_name(rgb: tuple, mark_type: str | None = None) -> str | None:
    """The palette name nearest ``rgb``, or ``None`` if nothing is within :data:`NAME_TOLERANCE`.

    ``mark_type`` narrows the search to that type's palette; without it both are searched, which is
    what the read side wants — a foreign highlight may be any colour at all, including one only the
    line palette has a name for.
    """
    if mark_type in PALETTES:
        candidates = PALETTES[mark_type]
    else:
        candidates = HIGHLIGHT_COLORS + TEXT_LINE_COLORS
    best_name, best = None, NAME_TOLERANCE
    for name, value in candidates:
        gap = _distance(tuple(rgb), value)
        if gap <= best:
            best_name, best = name, gap
    return best_name


def is_palette_color(rgb: tuple, mark_type: str | None = None) -> bool:
    """Whether ``rgb`` *is* one of the swatches, rather than merely nearest to one.

    The distinction matters on read: a mark this app made carries an exact palette value, so a
    caller filtering on colour can tell "the reviewer picked Orange from the menu" from "something
    orange-ish arrived from Acrobat".
    """
    if mark_type in PALETTES:
        candidates = PALETTES[mark_type]
    else:
        candidates = HIGHLIGHT_COLORS + TEXT_LINE_COLORS
    return any(_distance(tuple(rgb), value) <= EXACT_TOLERANCE for _name, value in candidates)
