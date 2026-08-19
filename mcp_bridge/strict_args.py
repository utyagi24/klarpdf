"""Reject an argument name the tool does not accept, instead of dropping it in silence (M106).

Every other check this bridge performs is *downstream of parameter binding*. `residual_matches`,
`cross_engine_verified`, `verified_text` and the rest all describe what the server **did**; none of
them can describe what it was **asked** to do, because pydantic discarded that before any of them
ran. So a one-character typo — `querys` for `queries` — produced an unqualified success on a file
that still held the PII the caller asked to remove (TC-009, PLAN.md §M106).

The logic lives here rather than in :mod:`mcp_bridge.server` for the same reason the PDF helpers do:
it imports no SDK, so it can be tested as the string-in/string-out function it is. The server owns
only the seam that feeds it.

**Reject rather than warn.** For a tool that deletes content an unrecognised key is far more likely
to be a mistake than something safe to ignore, and rejection fails closed: it costs the caller one
corrected call instead of a file they may already have shipped.
"""

from __future__ import annotations

import difflib

SUGGESTION_CUTOFF = 0.7
"""How close a name must be to be offered as a did-you-mean.

Tuned against the five typos TC-009 actually found plus the near misses either side of them. At
0.6 an `out_path` is answered with `path` — the **input** file, the one argument the caller least
wants to be nudged towards when they meant `out`. At 0.7 it is answered with nothing, which is the
honest reply; the accepted list is printed regardless, so a missing suggestion still leaves the
caller a complete answer.
"""

MAX_SUGGESTIONS = 3
"""Offer every close name, not just the closest.

`querys` scores higher against `query` than against `queries` — the shorter word is the smaller
edit — but a caller who wrote a plural almost certainly wanted the list. Naming both costs a few
characters and removes the round trip that guessing wrong would have added.
"""


def unknown_parameters(accepted: list[str], given: list[str]) -> list[str]:
    """The names in ``given`` that ``accepted`` does not contain, in the order they were sent."""
    known = set(accepted)
    return [name for name in given if name not in known]


def rejection_message(tool: str, accepted: list[str], unknown: list[str]) -> str:
    """Explain the rejection to an agent that has to fix the call and try again.

    Names the offending parameter first (that is the one thing the caller must change), then a
    did-you-mean, then the full accepted list so a second wrong guess is not necessary, and finally
    what did *not* happen — because the failure this replaces was one that reported success.

    ``accepted`` stays in the tool's own declaration order rather than being sorted: it mirrors the
    signature the agent read in the tool description, so the required arguments lead.
    """
    named = ", ".join(repr(name) for name in unknown)
    plural = "parameters" if len(unknown) > 1 else "parameter"
    lines = [f"unknown {plural} {named} for tool '{tool}'."]

    for name in unknown:
        close = difflib.get_close_matches(name, accepted, n=MAX_SUGGESTIONS, cutoff=SUGGESTION_CUTOFF)
        if close:
            options = " or ".join(repr(match) for match in close)
            lines.append(f"Did you mean {options} instead of {name!r}?")

    lines.append(f"'{tool}' accepts: {', '.join(accepted) if accepted else '(no parameters)'}.")
    lines.append("The call was rejected before it ran: nothing was read, and nothing was written.")
    return " ".join(lines)
