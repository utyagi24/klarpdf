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

# How far a colour may sit from the nearest swatch and still borrow its *name* **unconditionally**,
# under :func:`_perceptual_distance`. Set just under the closest pair of swatches in either palette
# under that same weighting — Yellow to Orange, 0.130 apart — so inside this radius a name is never
# borrowed across a distance greater than the gap between two named colours.
NAME_TOLERANCE = 0.12

# The outer radius, and the margin a colour between the two must clear (M118).
#
# **`NAME_TOLERANCE` alone was answering two questions with one number, and got the second wrong.**
# It has to decide both *is this colour anywhere near our palette* and *is it unambiguously one
# swatch rather than between two* — and calibrating it against the closest swatch pair only really
# addresses the second. The cost was measured: **pure yellow `(1, 1, 0)`** — Acrobat's default
# highlighter and the commonest highlight colour in circulation — sits **0.1214** from our Yellow
# and so fell 1.2% outside a 0.12 ceiling and got no name at all, while `(0.99, 0.99, 0)`, which no
# eye can tell from it, named fine (TC-015). Nothing about pure yellow is ambiguous: its runner-up
# (Orange) is **0.2501** away, so Yellow is 2.06x clearer.
#
# So the two questions are asked separately. Inside `NAME_TOLERANCE` nothing changes. Between it and
# `NAME_MAX_DISTANCE` a colour is named only when the nearest swatch beats the runner-up by
# `NAME_MARGIN` — near enough to be in the neighbourhood, and clearly one of them rather than
# stranded between two. Measured over 200,000 random colours across both palettes and the union,
# this changes **no** colour that already had a name: it is purely additive.
NAME_MAX_DISTANCE = 0.16
NAME_MARGIN = 1.5

# ITU-R BT.709 luma coefficients — the standard sRGB weighting, not a value tuned for this palette.
_LUMA_WEIGHTS = (0.2126, 0.7152, 0.0722)


def _distance(a: tuple, b: tuple) -> float:
    """Plain Euclidean distance in RGB. Not perceptually uniform, and deliberately not: the job is
    to recognise a swatch that round-tripped through a PDF — uniform float noise on every channel,
    where any metric agrees the answer is "basically zero" — not to model human vision. See
    :func:`_perceptual_distance` for the naming question, which is a different one."""
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _perceptual_distance(a: tuple, b: tuple) -> float:
    """Luma-weighted Euclidean distance in RGB — for deciding what a person would *call* ``a``.

    Plain :func:`_distance` weights a blue-channel difference the same as a green one, and gets a
    real case wrong because of it: Edge's default highlight, ``(1, 0.9412, 0.4)``, calls itself
    "yellow" and reads as yellow to a human eye, but sits 0.311 from our Yellow and only 0.243 from
    our Orange in plain RGB distance — nearer the wrong name — because the paleness that separates
    it from our Yellow lives mostly in the blue channel, while the hue split between "yellow" and
    "orange" is carried almost entirely by green. Weighting by the coefficients video luma uses
    (owner-confirmed 2026-08-25: KlarPDF's own highlight default is also named "yellow", which is
    what exposed the mismatch) puts the same colour at 0.106 from our Yellow against 0.189 from our
    Orange — reversed, and now the answer a person gives. :data:`NAME_TOLERANCE` is calibrated
    against this function, not :func:`_distance`.
    """
    return sum(w * (x - y) ** 2 for w, x, y in zip(_LUMA_WEIGHTS, a, b)) ** 0.5


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
    """The palette name nearest ``rgb``, or ``None`` when no swatch is clearly the one it means.

    ``mark_type`` narrows the search to that type's palette; without it both are searched, which is
    what the read side wants — a foreign highlight may be any colour at all, including one only the
    line palette has a name for.

    **Two radii, not one** (M118 — see :data:`NAME_MAX_DISTANCE`). Within
    :data:`NAME_TOLERANCE` the nearest swatch wins outright. Out to
    :data:`NAME_MAX_DISTANCE` it must also be :data:`NAME_MARGIN` times nearer than the
    runner-up, so a colour that is merely *in the region* — a teal between our two greens — stays
    unnamed while one that is plainly a brighter version of a single swatch, like pure yellow
    against our muted marker Yellow, is named. Beyond that, nothing is named at all.
    """
    if mark_type in PALETTES:
        candidates = PALETTES[mark_type]
    else:
        candidates = HIGHLIGHT_COLORS + TEXT_LINE_COLORS
    ranked = sorted(
        (_perceptual_distance(tuple(rgb), value), name) for name, value in candidates
    )
    if not ranked:
        return None
    best, best_name = ranked[0]
    if best <= NAME_TOLERANCE:
        return best_name
    if best > NAME_MAX_DISTANCE:
        return None
    # In the halo: name it only if nothing else is competing. `best == 0` cannot reach here (it
    # would have passed the inner test), so the division is safe.
    runner_up = ranked[1][0] if len(ranked) > 1 else float("inf")
    return best_name if runner_up / best >= NAME_MARGIN else None


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
