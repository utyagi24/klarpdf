"""Every in-page link in the bridge README lands on a heading.

The bridge README is also the PyPI page (``pyproject.toml`` ``readme``), and it opens with a
contents list, so renaming a heading silently breaks the links to it on both sites. GitHub and
PyPI's renderer (``readme_renderer`` with comrak) derive a heading's anchor the same way, which
``_slug`` copies: lowercase, drop punctuation other than ``-`` and ``_``, and turn spaces into
hyphens. A hand-written ``<a id>`` is not a substitute. PyPI rewrites the link to
``#user-content-…`` but not the anchor, so the link dies there (measured with
``readme_renderer`` 46.0).
"""

from __future__ import annotations

import re
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "klarpdf" / "mcp_bridge" / "README.md"


def _slug(heading: str) -> str:
    text = heading.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _headings(markdown: str) -> list[str]:
    """ATX headings outside fenced code blocks (a ``# comment`` in a shell block is not one)."""
    found, fenced = [], False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and (m := re.match(r"#{1,6} (.+)", line)):
            found.append(m.group(1))
    return found


def test_the_slug_rule_matches_the_renderers():
    assert _slug("Reading a tool's full contract") == "reading-a-tools-full-contract"
    assert _slug("If those say `command not found`") == "if-those-say-command-not-found"
    assert _slug("Marking up, and the review hand-off") == "marking-up-and-the-review-hand-off"


def test_every_in_page_link_lands_on_a_heading():
    text = README.read_text(encoding="utf-8")
    anchors = {_slug(h) for h in _headings(text)}
    links = re.findall(r"\]\(#([^)]+)\)", text)
    assert len(links) > 20, "the contents list was not found; did the link syntax change?"
    dangling = sorted(set(links) - anchors)
    assert dangling == [], f"links to headings that do not exist: {dangling}"
