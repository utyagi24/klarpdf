"""Keep `install.py`'s baked-in constants in step with the repo (M136).

    python packaging/mcp/installer/sync_installer.py            # rewrite the block
    python packaging/mcp/installer/sync_installer.py --check    # exit 1 if stale (what CI runs)

**Why they are baked in rather than read.** `install.py` is downloaded and run on a machine with no
clone, so it cannot read `pyproject.toml` or `klarpdf/version.py` — there is no repo around it. It
therefore carries the version it installs and the Python window it accepts as literals, which makes
it a *release artifact*: the file you checksummed determines exactly what lands, and there is no
`--version` flag to make it install something else (`PLAN.md` §M133–M136, decision 5).

**Why that needs a generator and not a careful human.** M132's whole lesson: a constraint restated
in a second file is not one fact, it is two facts that happen to agree today, and the copy nobody
reads is the one that rots. The Python window already lives in `pyproject.toml`, the `.mcpb`
manifest and `uv.lock`; `install.py` would be the fourth. Generating it means the count of
hand-maintained copies does not go up.

Mirrors `packaging/mcp/pypi/sync_pins.py`, deliberately — same sentinel shape, same `--check`, so
there is one thing to learn rather than two.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent  # packaging/mcp/installer/ -> repo root
INSTALLER = HERE / "install.py"
PYPROJECT = ROOT / "pyproject.toml"
VERSION_PY = ROOT / "klarpdf" / "version.py"

BEGIN = "# BEGIN GENERATED — packaging/mcp/installer/sync_installer.py, from klarpdf/version.py + pyproject.toml"
END = "# END GENERATED"


def read_version() -> str:
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', VERSION_PY.read_text("utf-8"), re.M)
    if not match:
        raise SystemExit(f"could not read __version__ from {VERSION_PY}")
    return match.group(1)


def read_window() -> tuple[tuple[int, int], tuple[int, int]]:
    """`requires-python` is a half-open range; `install.py` compares against inclusive bounds.

    `>=3.11,<3.15` means the newest *accepted* minor is 3.14, so the ceiling is decremented here
    rather than restated by hand — the off-by-one is exactly the kind of thing a second copy gets
    wrong once and then keeps.
    """
    text = PYPROJECT.read_text("utf-8")
    match = re.search(r'^requires-python\s*=\s*">=(\d+)\.(\d+),<(\d+)\.(\d+)"', text, re.M)
    if not match:
        raise SystemExit(f"could not parse requires-python from {PYPROJECT}")
    lo_major, lo_minor, hi_major, hi_minor = (int(g) for g in match.groups())
    if hi_minor == 0:
        raise SystemExit(
            f"requires-python's ceiling is {hi_major}.{hi_minor}, which this script cannot "
            f"decrement across a major version. Widen it by hand and adjust here."
        )
    return (lo_major, lo_minor), (hi_major, hi_minor - 1)


def render_block() -> str:
    version = read_version()
    (lo, hi) = read_window()
    return (
        f"{BEGIN}\n"
        f'KLARPDF_VERSION = "{version}"\n'
        f"PYTHON_MIN = {lo}\n"
        f"PYTHON_MAX = {hi}\n"
        f"{END}"
    )


def current_block(text: str) -> str:
    start, end = text.find(BEGIN), text.find(END)
    if start == -1 or end == -1 or end < start:
        raise SystemExit(
            f"{INSTALLER} has no generated block. Expected these two lines:\n  {BEGIN}\n  {END}"
        )
    return text[start : end + len(END)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report staleness instead of fixing it")
    args = parser.parse_args()

    text = INSTALLER.read_text("utf-8")
    have, want = current_block(text), render_block()

    if have == want:
        print(f"install.py is in step with version.py and pyproject.toml")
        return 0
    if args.check:
        print("install.py's baked-in constants are stale.", file=sys.stderr)
        print(f"  it says:\n{have}\n  the repo says:\n{want}", file=sys.stderr)
        print("  fix: python packaging/mcp/installer/sync_installer.py", file=sys.stderr)
        return 1

    INSTALLER.write_text(text.replace(have, want), encoding="utf-8")
    print("install.py updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
