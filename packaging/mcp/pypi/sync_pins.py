"""Write `requirements-mcp.txt`'s 29 pins into the root `pyproject.toml` (M135).

    python packaging/mcp/pypi/sync_pins.py            # rewrite the block
    python packaging/mcp/pypi/sync_pins.py --check    # exit 1 if it is stale (what CI runs)

**Why the published metadata pins rather than floors.** `pyproject.toml`'s `dependencies` become
`Requires-Dist` in the wheel, and that is what `pip`, `pipx`, `uvx` and `install.py` all resolve
against. Floors would give a `uvx` install and a lock install *different dependency sets from one
package*, which is the drift already recorded against `pipx install .` in `PROGRESS.md`. The
"floors, never pins" convention is a **library's** convention: it exists so a caller can co-install
you with something else. This distribution is an application — installed by `uvx`/`pipx` into an
environment of its own, where there is nothing to co-install with — so pinning is the correct shape
and the audited set becomes what every route installs. `pip-audit` already scans
`requirements-mcp.txt`, so that one scan now covers users too (PLAN.md §M133–M136, decision 2).

**Why generated rather than hand-maintained.** Twenty-nine exact pins restated by hand in a second
file is the drift this repo keeps being bitten by. The pins are read through `build_mcpb.py`'s own
`read_pins()` rather than a second parser, so the bundle and the wheel cannot disagree about what
the lock says — they are the same function reading the same file.

**What stays hand-written.** Only the `dependencies` array is generated, between two sentinels;
every comment, floor rationale and the rest of `[project]` is untouched. The array is replaced
wholesale, so a pin removed from the lock disappears here too.

**The residue, stated rather than discovered.** `requirements-mcp.txt` is deliberately
platform-marker-free (see its `.in` header), so it structurally cannot name `colorama` or
`pywin32`. Those arrive through their parents' own metadata, unpinned, on the platforms that need
them. It is the same two-package gap the `.mcpb` carries, for the same reason.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent  # packaging/mcp/pypi/ -> repo root
PYPROJECT = ROOT / "pyproject.toml"

BEGIN = "    # BEGIN GENERATED PINS — packaging/mcp/pypi/sync_pins.py, from requirements-mcp.txt"
END = "    # END GENERATED PINS"


def _read_pins() -> list[str]:
    """Reuse the bundle builder's reader so the two cannot disagree about the lock."""
    path = ROOT / "packaging" / "mcp" / "mcpb" / "build_mcpb.py"
    spec = importlib.util.spec_from_file_location("_klarpdf_build_mcpb", path)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        raise SystemExit(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.read_pins()


def render_block(pins: list[str]) -> str:
    body = "\n".join(f'    "{pin}",' for pin in pins)
    return f"{BEGIN}\n{body}\n{END}"


def current_block(text: str) -> str:
    """The sentinel-delimited region as it stands, or a message naming what is wrong."""
    start = text.find(BEGIN)
    end = text.find(END)
    if start == -1 or end == -1 or end < start:
        raise SystemExit(
            f"{PYPROJECT} has no generated-pins block. Expected these two lines inside "
            f"`dependencies = [ ... ]`:\n  {BEGIN}\n  {END}"
        )
    return text[start : end + len(END)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report staleness instead of fixing it")
    args = parser.parse_args()

    text = PYPROJECT.read_text(encoding="utf-8")
    have = current_block(text)
    want = render_block(_read_pins())

    if have == want:
        print(f"pyproject.toml is in step with requirements-mcp.txt ({len(_read_pins())} pins)")
        return 0

    if args.check:
        have_names = set(re.findall(r'"([^"]+)"', have))
        want_names = set(re.findall(r'"([^"]+)"', want))
        added, dropped = sorted(want_names - have_names), sorted(have_names - want_names)
        print("pyproject.toml's dependencies are stale against requirements-mcp.txt.", file=sys.stderr)
        if added:
            print(f"  the lock has, the metadata does not: {added}", file=sys.stderr)
        if dropped:
            print(f"  the metadata has, the lock does not: {dropped}", file=sys.stderr)
        print("  fix: python packaging/mcp/pypi/sync_pins.py", file=sys.stderr)
        return 1

    PYPROJECT.write_text(text.replace(have, want), encoding="utf-8")
    print(f"pyproject.toml dependencies rewritten from requirements-mcp.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
