"""No dead code in shipped code (M169, closes #409).

Nothing stopped dead code building up. ``tests/test_mcp_no_qt.py`` catches an unused *module* in the
core (M147), but nothing caught an unused function, class or constant, and ``PyPdfEngine`` (M156)
showed what that costs: code only tests ever used kept pypdf in the installer through four security
bumps.

**Static, not coverage** (owner, #409). Coverage counts a line as live when a *test* runs it, so the
code it most needs to find looks covered, and a threshold is a tuned number. Vulture reads the
shipped code and answers yes or no, without counting test callers at all.

**Code that only tests call is dead** unless ``tests/dead_code_whitelist.py`` keeps it with a reason.
When this fails on new code, the fix is usually to delete the code, or to have the tests use what
the app runs. Whitelist it only when something vulture cannot see calls it (Qt by name, the MCP SDK)
or when it is a read-only accessor the tests observe state through.

**The files are the ones git tracks** (``_shipped_modules``), never a directory walk: untracked
``build/`` and ``dist/`` folders hold old copies of shipped modules, which a walk would read.
"""

from __future__ import annotations

from pathlib import Path

import vulture

from tests.dead_code_whitelist import KEEP
from tests.test_pypdf_is_dev_only import ROOT, _shipped_modules, needs_git

#: Vulture rates an unused function, class, method or variable 60, and an unused argument 100.
#: 60 is the lowest that reports them, so nothing is skipped by confidence.
MIN_CONFIDENCE = 60
#: MCP tools are called by name over the protocol, not from code.
IGNORE_DECORATORS = ["@server.tool"]


def _findings(paths: list[Path]) -> dict[tuple[str, str], str]:
    """``(path relative to the repo, name)`` -> ``"path:line: unused <kind> 'name'"``."""
    scan = vulture.Vulture(ignore_decorators=IGNORE_DECORATORS)
    scan.scavenge([str(path) for path in paths])
    found = {}
    for item in scan.get_unused_code(min_confidence=MIN_CONFIDENCE):
        path = Path(item.filename).resolve()
        rel = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name
        found[(rel, item.name)] = f"{rel}:{item.first_lineno}: unused {item.typ} {item.name!r}"
    return found


def test_the_check_finds_an_unused_function(tmp_path):
    """A scan that finds nothing looks exactly like a clean one, so give it something to find."""
    module = tmp_path / "planted.py"
    module.write_text(
        "def used():\n    return 1\n\n\ndef never_called():\n    return 2\n\n\nprint(used())\n",
        encoding="utf-8",
    )
    assert set(_findings([module])) == {("planted.py", "never_called")}


@needs_git
def test_the_scan_sees_both_surfaces():
    found = {path.relative_to(ROOT).as_posix() for path in _shipped_modules()}
    assert {"main_window.py", "viewer/pdf_view.py", "klarpdf/mcp_bridge/server.py"} <= found
    assert not any(name.startswith(("tests/", "tools/", "packaging/", "assets/")) for name in found)


@needs_git
def test_no_dead_code_in_shipped_code():
    unlisted = sorted(text for key, text in _findings(_shipped_modules()).items() if key not in KEEP)
    assert unlisted == [], (
        "shipped code that nothing in shipped code uses:\n  " + "\n  ".join(unlisted) + "\n"
        "Delete it, or have its tests use what the app runs. If Qt or the MCP SDK calls it by name, "
        "or it is a read-only accessor the tests observe state through, add it to "
        "tests/dead_code_whitelist.py with the reason."
    )


@needs_git
def test_every_whitelist_entry_is_still_needed():
    stale = sorted(f"{path}: {name}" for path, name in set(KEEP) - set(_findings(_shipped_modules())))
    assert stale == [], (
        "tests/dead_code_whitelist.py keeps names vulture no longer reports, because the code is "
        "gone or now has a caller. Remove the entries:\n  " + "\n  ".join(stale)
    )


def test_every_whitelist_entry_gives_a_reason():
    unexplained = [f"{path}: {name}" for (path, name), reason in KEEP.items() if not reason.strip()]
    assert unexplained == [], f"whitelist entries with no reason: {unexplained}"
