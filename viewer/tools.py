"""Viewer interaction modes + one-shot tools (PLAN.md, M18 / M21).

Two layers:

* **Persistent modes** (:class:`InteractionMode`) — the standing behaviour of a left-drag:
  - **SELECT** (default) — drag selects text; click fills a form field; an existing text box can be
    dragged to move it or double-clicked to re-edit. Highlight / Redact-Selection are
    select-then-act actions in this mode.
  - **GRAB** — drag pans the page (a hand tool).
  - **OBJECT** (M59.6) — drag an empty area to marquee-select drawn objects, Ctrl+click to add /
    remove one, drag a member to move the whole group; text selection & form-fill are inert here.

* **One-shot armed tools** (:class:`ArmedTool`) — an annotate/redact action that fires once then
  reverts to SELECT, instead of being a sticky mode (the user's model: click the toolbar button
  each time, then do the gesture). While armed the view shows a crosshair and the button stays lit.
  All four are consistent — arm, then a single gesture:
  - **TEXTBOX** — click a spot to place a free-text note box (M20);
  - **HIGHLIGHT** — drag over text to highlight it (one continuous bar per line);
  - **UNDERLINE** / **STRIKEOUT** — drag over text to underline / strike it (M56; same
    line-bar path as highlight);
  - **REDACT_TEXT** — drag over text to redact it (text-flow, one bar per line);
  - **REDACT_REGION** — drag a rectangle to destructively remove a block/image (M21);
  - **CROP** — drag the rectangle to keep; the rest of the page is hidden, not removed (M48).
"""

from __future__ import annotations

from enum import Enum


class InteractionMode(Enum):
    SELECT = "select"
    GRAB = "grab"
    OBJECT = "object"   # M59.6 — marquee/Ctrl-click to select drawn objects; drag to move the group


class ArmedTool(Enum):
    TEXTBOX = "textbox"
    HIGHLIGHT = "highlight"
    UNDERLINE = "underline"
    STRIKEOUT = "strikeout"
    REDACT_TEXT = "redact_text"
    REDACT_REGION = "redact_region"
    CROP = "crop"
    PEN = "pen"
    LINE = "line"
    ARROW = "arrow"
    RECT = "rect"
    ELLIPSE = "ellipse"

    @property
    def drags_text(self) -> bool:
        """True for tools driven by a drag-over-text selection (the markup trio + text-redact)."""
        return self in (
            ArmedTool.HIGHLIGHT,
            ArmedTool.UNDERLINE,
            ArmedTool.STRIKEOUT,
            ArmedTool.REDACT_TEXT,
        )

    @property
    def draws(self) -> bool:
        """True for the free-draw tools (M58): pen path capture / line / arrow / rect / ellipse —
        a press-drag-release gesture on the page, committed as a drawn-mark descriptor."""
        return self in (
            ArmedTool.PEN,
            ArmedTool.LINE,
            ArmedTool.ARROW,
            ArmedTool.RECT,
            ArmedTool.ELLIPSE,
        )
