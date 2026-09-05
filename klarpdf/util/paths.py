"""Path identity — the SINGLE chokepoint for deciding whether two paths name the same file.

Per PLAN.md (Portability hedge #4): keeping case-sensitivity in one function means the
Windows (case-fold) vs Linux (case-sensitive) switch is a one-line change. ``os.path.normcase``
already does the right thing per-OS (identity on Linux, lower-case on Windows), so this file
is portable as written — the chokepoint exists so it *stays* that way.
"""

from __future__ import annotations

import os


def normalize_path(path: str | os.PathLike[str]) -> str:
    """Return the canonical identity key for ``path``.

    Resolves symlinks and ``..`` (``realpath``), normalises separators
    (``normpath``), and folds case where the OS is case-insensitive
    (``normcase``: no-op on Linux, lower-case on Windows). Used both as the
    single-instance "is this file already open?" key and as a source id in the
    virtual document, so the two never disagree.
    """
    return os.path.normcase(os.path.normpath(os.path.realpath(os.fspath(path))))
