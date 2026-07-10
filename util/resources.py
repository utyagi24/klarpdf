"""Read-only data files that ship with the app, resolved for source runs *and* the frozen build.

The single chokepoint for "where does a bundled data file live at runtime?" (PLAN.md,
§Public-release readiness / G4). PyInstaller unpacks ``datas`` into a temp dir and points
``sys._MEIPASS`` at it; a source checkout has them next to the repo root. Everything else in the app
asks here rather than reaching for ``__file__`` and guessing — the same reason
``util.paths.normalize_path`` exists.

This mirrors :func:`ui.icons.icons_dir`, which solves the identical problem for the SVGs. Two copies
of the ``_MEIPASS`` dance is one too many; if a third appears, fold ``ui.icons`` into this module.

**The failure mode this guards against:** the headless suite only ever exercises the source path, so
a resolver that works under ``pytest`` and breaks inside the installer is invisible to CI. Hence
:func:`resource_path` never raises on a missing file — callers decide — and
:func:`read_text_resource` degrades to a readable placeholder rather than crashing a dialog in the
one build we cannot test here.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Data files bundled at the *root* of the frozen tree (see packaging/klarpdf.spec `datas`).
# Kept as a tuple so a caller can enumerate what the Open-Source Licenses dialog should offer.
LICENSE_FILES: tuple[str, ...] = ("LICENSE", "THIRD_PARTY_LICENSES")


def resource_root() -> Path:
    """Directory holding bundled read-only data — the repo root, or PyInstaller's unpack dir."""
    meipass = getattr(sys, "_MEIPASS", None)  # set by PyInstaller at runtime; absent from source
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent  # util/ -> repo root


def resource_path(*parts: str) -> Path:
    """Absolute path to a bundled data file. Does **not** assert existence."""
    return resource_root().joinpath(*parts)


def read_text_resource(name: str) -> str:
    """Text of a bundled data file, or an explanatory placeholder if it is missing.

    A missing license text must not take down the About dialog: the frozen build is the only place
    this can realistically fail (a spec `datas` regression), and it is the one build the headless
    suite never runs. Fail visibly, not fatally.
    """
    path = resource_path(name)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        return (
            f"{name} could not be read from the application bundle.\n\n"
            f"Tried: {path}\nReason: {exc}\n\n"
            "This is a packaging fault, not a licensing one. The full text is always available at "
            "https://github.com/utyagi24/klarpdf"
        )
