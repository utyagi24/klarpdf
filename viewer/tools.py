"""Viewer interaction modes + one-shot tools (PLAN.md, M18 / M21).

Two layers:

* **Persistent modes** (:class:`InteractionMode`) — the standing behaviour of a left-drag:
  - **SELECT** (default) — drag selects text; click fills a form field; an existing text box can be
    dragged to move it or double-clicked to re-edit. Highlight / Redact-Selection are
    select-then-act actions in this mode.
  - **GRAB** — drag pans the page (a hand tool).

* **One-shot armed tools** (:class:`ArmedTool`) — an *insert* that fires once then reverts to
  SELECT, instead of being a sticky mode (the user's model: click the toolbar button each time you
  want to add one). While armed the view shows a crosshair and the button stays lit:
  - **TEXTBOX** — the next click on a page places a free-text note box (M20);
  - **REDACT** — the next left-drag marks a rectangle to destructively remove at save (M21).
"""

from __future__ import annotations

from enum import Enum


class InteractionMode(Enum):
    SELECT = "select"
    GRAB = "grab"


class ArmedTool(Enum):
    TEXTBOX = "textbox"
    REDACT = "redact"
