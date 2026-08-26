"""M113.7 — naming a foreign mark's colour needs a perceptual distance, not a plain one.

Plain Euclidean RGB distance weights a blue-channel difference the same as a green one, and gets a
real, common case wrong: Edge's default highlight calls itself "yellow" — and so does KlarPDF's own
default highlight, which is what exposed the mismatch (owner, 2026-08-25) — but plain distance calls
it nearer our Orange. :func:`~model.markup_palette._perceptual_distance` fixes this with the
standard BT.709 luma weighting, not a value tuned to make one test pass; :data:`NAME_TOLERANCE` is
recalibrated to match it. See ``PLAN.md`` §M113.7.
"""

from __future__ import annotations

from model.markup_palette import (
    HIGHLIGHT_COLORS,
    NAME_TOLERANCE,
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
