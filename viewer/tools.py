"""Viewer interaction modes (PLAN.md, M18).

The viewer has one active mouse tool at a time. M18 introduces two:

* **SELECT** (default) — left-drag selects text and clicks fill form fields;
* **GRAB** — left-drag pans the page (a hand tool) for the corner cases where select mode gets in
  the way.

This enum is the seam the v0.4.0 annotation tools (highlight / text-box / redact) extend, so the
mode lives here rather than as a bare flag on ``PdfView``.
"""

from __future__ import annotations

from enum import Enum


class InteractionMode(Enum):
    SELECT = "select"
    GRAB = "grab"
