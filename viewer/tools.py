"""Viewer interaction modes + one-shot tools (PLAN.md, M18 / M21).

Two layers:

* **Persistent modes** (:class:`InteractionMode`) — the standing behaviour of a left-drag:
  - **SELECT** (default) — drag selects text; click fills a form field; an existing text box can be
    dragged to move it or double-clicked to re-edit. Highlight / Redact-Selection are
    select-then-act actions in this mode.
  - **GRAB** — drag pans the page (a hand tool).

* **One-shot armed tools** (:class:`ArmedTool`) — an annotate/redact action that fires once then
  reverts to SELECT, instead of being a sticky mode (the user's model: click the toolbar button
  each time, then do the gesture). While armed the view shows a crosshair and the button stays lit.
  All four are consistent — arm, then a single gesture:
  - **TEXTBOX** — click a spot to place a free-text note box (M20);
  - **HIGHLIGHT** — drag over text to highlight it (one continuous bar per line);
  - **REDACT_TEXT** — drag over text to redact it (text-flow, one bar per line);
  - **REDACT_REGION** — drag a rectangle to destructively remove a block/image (M21);
  - **CROP** — drag the rectangle to keep; the rest of the page is hidden, not removed (M48).
"""

from __future__ import annotations

from enum import Enum


class InteractionMode(Enum):
    SELECT = "select"
    GRAB = "grab"


class ArmedTool(Enum):
    TEXTBOX = "textbox"
    HIGHLIGHT = "highlight"
    REDACT_TEXT = "redact_text"
    REDACT_REGION = "redact_region"
    CROP = "crop"

    @property
    def drags_text(self) -> bool:
        """True for tools driven by a drag-over-text selection (highlight / text-redact)."""
        return self in (ArmedTool.HIGHLIGHT, ArmedTool.REDACT_TEXT)
