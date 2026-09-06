"""Every local link in the root `README.md` must resolve — it is the repo's shop window.

The README carries a nav strip standing in for tabs, because GitHub's own tab row is generated from
recognised filenames (`CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md`, `LICENSE`, `README.md`)
and cannot be extended: the two MCP documents can never appear there. A hand-built strip gets the
effect and inherits the obligation — nothing keeps it in step with the files it names.

That obligation is not hypothetical here. M134 moved `mcp_bridge/` under `klarpdf/` and M133 moved
six files under `packaging/`; each would have silently broken a link like these. The shipped-artifact
version of this check lives in `tests/test_packaging_layout.py` and covers absolute `blob/main/…`
URLs inside the wheel and the `.mcpb`. This one covers the repo-local half: relative links and image
sources in the file a visitor reads first.

A broken link here embarrasses rather than breaks, which is exactly why nothing else would catch it.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)")
HTML_HREF = re.compile(r'<a\s[^>]*href="([^"]+)"', re.I)
HTML_SRC = re.compile(r'<(?:img|source)\s[^>]*(?:src|srcset)="([^"]+)"', re.I)

def _local_targets(text: str) -> set[str]:
    """Paths the README points at inside this repo — not the web, not in-page anchors."""
    found: set[str] = set()
    for pattern in (MARKDOWN_LINK, HTML_HREF, HTML_SRC):
        for target in pattern.findall(text):
            target = target.split("#", 1)[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            found.add(target.lstrip("./"))
    return found


def test_every_local_link_in_the_readme_resolves():
    targets = _local_targets(README.read_text(encoding="utf-8"))
    missing = sorted(t for t in targets if not (ROOT / t).exists())
    assert not missing, (
        f"README.md links to {missing}, which do not exist. This is the file a visitor reads first, "
        f"and a move is what breaks it — M133 moved six files under packaging/, M134 moved "
        f"mcp_bridge/ under klarpdf/. Update the link or restore the path."
    )


def test_the_check_is_actually_looking_at_something():
    """Guard the guard: if the patterns stop matching, the test above passes vacuously and the
    README could rot freely. The nav strip alone supplies six of these."""
    targets = _local_targets(README.read_text(encoding="utf-8"))
    assert len(targets) >= 8, (
        f"only found {len(targets)} local targets in README.md — either the file changed shape or "
        f"the link patterns in this test have drifted from it. Both need a human."
    )


def test_the_nav_strip_names_the_documents_it_promises():
    """The strip exists to reach the two MCP documents, which GitHub's real tab row cannot show."""
    text = README.read_text(encoding="utf-8")
    for target in ("klarpdf/mcp_bridge/QUICKSTART.md", "klarpdf/mcp_bridge/README.md"):
        assert target in text, f"the README no longer links to {target}"
        assert (ROOT / target).exists()
