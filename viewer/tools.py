"""Viewer interaction modes (PLAN.md, M18).

The viewer has one active mouse tool at a time:

* **SELECT** (default) — left-drag selects text and clicks fill form fields. Highlighting is a
  *select-then-highlight* action in this mode (not a separate tool);
* **GRAB** — left-drag pans the page (a hand tool) for the corner cases where select mode gets in
  the way (M18);
* **TEXTBOX** — left-click places a free-text note box (M20).

Redaction (M21) will add another mode here.
"""

from __future__ import annotations

from enum import Enum


class InteractionMode(Enum):
    SELECT = "select"
    GRAB = "grab"
    TEXTBOX = "textbox"
