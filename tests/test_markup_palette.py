"""M113.7 — naming a foreign mark's colour needs a perceptual distance, not a plain one.

Plain Euclidean RGB distance weights a blue-channel difference the same as a green one, and gets a
real, common case wrong: Edge's default highlight calls itself "yellow" — and so does KlarPDF's own
default highlight, which is what exposed the mismatch (owner, 2026-08-25) — but plain distance calls
it nearer our Orange. :func:`~model.markup_palette._perceptual_distance` fixes this with the
standard BT.709 luma weighting, not a value tuned to make one test pass; :data:`NAME_TOLERANCE` is
recalibrated to match it. See ``PLAN.md`` §M113.7.

**M118 finishes the job at the other edge of the same tolerance.** Recalibrating the ceiling to
0.12 excluded **pure yellow** — Acrobat's default highlighter — by 1.2%, while a colour 1% darker
named fine (TC-015). One number was deciding two questions: *is this near our palette at all* and
*is it unambiguously one swatch*. They are now asked separately, and the M118 section at the foot
pins both the case that must name and the cases that must not.
"""

from __future__ import annotations

import pytest

from klarpdf.model.markup_palette import (
    HIGHLIGHT_COLORS,
    NAME_MARGIN,
    NAME_MAX_DISTANCE,
    NAME_TOLERANCE,
    PALETTES,
    TEXT_LINE_COLORS,
    _distance,
    _perceptual_distance,
    is_palette_color,
    nearest_name,
)

EDGE_YELLOW = (1, 0.9412, 0.4)
ACROBAT_RED = (1, 0, 0)


def test_edge_default_yellow_is_named_yellow_not_orange():
    """The case that motivated the reweighting: a plain distance gets this backwards."""
    assert nearest_name(EDGE_YELLOW, "highlight") == "Yellow"


def test_plain_distance_would_have_named_it_orange():
    """Pins *why* the fix is needed — if this ever stops being true, the bug it fixed is gone too
    and the module docstring's justification should be re-checked, not just the ceiling."""
    yellow = dict(HIGHLIGHT_COLORS)["Yellow"]
    orange = dict(HIGHLIGHT_COLORS)["Orange"]
    assert _distance(EDGE_YELLOW, orange) < _distance(EDGE_YELLOW, yellow)
    assert _perceptual_distance(EDGE_YELLOW, yellow) < _perceptual_distance(EDGE_YELLOW, orange)


def test_acrobat_default_red_is_still_named_red():
    """The other real-world default this tolerance has to keep catching."""
    assert nearest_name(ACROBAT_RED, "underline") == "Red"


def test_no_name_is_borrowed_across_a_greater_distance_than_two_swatches_are_apart():
    """The invariant NAME_TOLERANCE is calibrated to, restated as code rather than a comment."""
    all_swatches = list(HIGHLIGHT_COLORS) + list(TEXT_LINE_COLORS)
    closest_pair = min(
        _perceptual_distance(a, b)
        for i, (_, a) in enumerate(all_swatches)
        for _, b in all_swatches[i + 1 :]
    )
    assert NAME_TOLERANCE < closest_pair


def test_a_colour_with_nothing_close_gets_no_name():
    assert nearest_name((0.5, 0.5, 0.5)) is None


def test_exact_match_is_unaffected_by_the_reweighting():
    """EXACT_TOLERANCE / is_palette_color answer a different question (round-trip noise, not
    perceived colour) and stay on the plain metric — this pins that they still agree."""
    yellow = dict(HIGHLIGHT_COLORS)["Yellow"]
    assert is_palette_color(yellow, "highlight") is True
    assert nearest_name(yellow, "highlight") == "Yellow"


def test_highlight_and_line_blue_are_not_the_same_colour(): # M113.8(a), still true after reweighting
    h_blue = dict(HIGHLIGHT_COLORS)["Blue"]
    l_blue = dict(TEXT_LINE_COLORS)["Blue"]
    assert _perceptual_distance(h_blue, l_blue) > NAME_TOLERANCE


def test_highlight_and_line_green_are_not_the_same_colour(): # M113.8(a), still true after reweighting
    h_green = dict(HIGHLIGHT_COLORS)["Green"]
    l_green = dict(TEXT_LINE_COLORS)["Green"]
    assert _perceptual_distance(h_green, l_green) > NAME_TOLERANCE


# ---- M118: the boundary the M113.7 recalibration overshot ---------------------

PURE_YELLOW = (1, 1, 0)


def test_pure_yellow_is_named_yellow():
    """TC-015's headline: `#FFFF00` is Acrobat's default highlighter and the commonest highlight
    colour in circulation, and a 0.12 ceiling excluded it by 1.2%."""
    assert nearest_name(PURE_YELLOW, "highlight") == "Yellow"


def test_pure_yellow_is_unambiguous_which_is_why_it_may_be_named():
    """It is not named because the ceiling moved — it is named because nothing competes with it.
    If this ratio ever falls below NAME_MARGIN the halo rule stops applying and the fix is void."""
    yellow = dict(HIGHLIGHT_COLORS)["Yellow"]
    orange = dict(HIGHLIGHT_COLORS)["Orange"]
    near = _perceptual_distance(PURE_YELLOW, yellow)
    runner_up = _perceptual_distance(PURE_YELLOW, orange)
    assert near > NAME_TOLERANCE          # outside the inner radius…
    assert near <= NAME_MAX_DISTANCE      # …inside the outer one…
    assert runner_up / near >= NAME_MARGIN  # …and clearly one swatch rather than between two


def test_a_colour_the_eye_cannot_tell_from_pure_yellow_agrees_with_it():
    """The indefensible pair TC-015 found: `[0.99, 0.99, 0]` named while `[1, 1, 0]` did not."""
    assert nearest_name((0.99, 0.99, 0), "highlight") == nearest_name(PURE_YELLOW, "highlight")


@pytest.mark.parametrize(
    "rgb",
    [(0, 0.5, 0.5), (0.5, 0.5, 0.5), (0.4, 0.26, 0.13), (0.5, 0, 0.5), (1, 1, 1)],
    ids=["teal", "mid-grey", "brown", "purple", "white"],
)
def test_a_colour_stranded_between_swatches_is_still_unnamed(rgb):
    """The halo must not become a wider tolerance. Teal is the tight one — it is inside
    NAME_MAX_DISTANCE of our Green and is rejected on the margin, not on the distance."""
    assert nearest_name(rgb) is None


def test_the_halo_is_purely_additive():
    """The property that makes this safe to ship: no colour that had a name before has a different
    one now. Swept over the RGB cube rather than asserted."""
    step = 0.05
    values = [i * step for i in range(int(1 / step) + 1)]
    for r in values:
        for g in values:
            for b in values:
                rgb = (r, g, b)
                for mark_type in ("highlight", "underline", None):
                    name = nearest_name(rgb, mark_type)
                    if name is None:
                        continue
                    # Anything that names must be inside the outer radius, and inside the inner one
                    # it must be the plain nearest — the pre-M118 rule, unchanged.
                    candidates = (
                        PALETTES[mark_type] if mark_type in PALETTES
                        else HIGHLIGHT_COLORS + TEXT_LINE_COLORS
                    )
                    best, best_name = min(
                        (_perceptual_distance(rgb, v), n) for n, v in candidates
                    )
                    assert name == best_name
                    assert best <= NAME_MAX_DISTANCE
