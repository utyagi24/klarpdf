"""Atomic file replacement that survives a transient lock on the freshly written temp file.

Every write in this app is temp-file-then-``os.replace``: materialize into a sibling temp in the
target's own directory, then rename it over the target in one indivisible step, so a crash or a
failed write can never leave a half-written PDF where the original was.

The rename is the fragile part on Windows. ``os.replace`` needs exclusive access to *both* paths,
and a file we finished writing microseconds ago is exactly what an on-access antivirus scanner
opens to inspect. While that handle is up the rename fails with ``ERROR_ACCESS_DENIED`` (WinError
5) or ``ERROR_SHARING_VIOLATION`` (WinError 32) — Python raises ``PermissionError`` for both — and
the app reported a "Save failed" modal for a save that was, a few hundred milliseconds later,
perfectly possible. It surfaced as a flaky test twice while preparing v0.14.0 (PROGRESS.md §Open
follow-ups), but the test was only the messenger: any user with real-time scanning can hit it.

So: retry a bounded number of times with a short backoff, and only for the error that lock
contention actually raises. A real permission problem (read-only target, no write access to the
directory) raises the same ``PermissionError`` and cannot be told apart from the transient one
without guessing, so it pays the full retry budget before failing — the cost of being wrong in
that direction is a fraction of a second on a path that is about to show an error dialog anyway.
Everything else (``FileNotFoundError``, a cross-device ``OSError``) is not contention and
propagates on the first try.

Kept out of ``util/paths.py`` on purpose: that file is the path-*identity* chokepoint and has no
business doing I/O.
"""

from __future__ import annotations

import os
import time

# Five attempts at 50/100/200/400 ms ≈ 0.75 s of waiting in the worst case. Long enough to outlast
# an antivirus scan of a PDF-sized file, short enough that a genuine failure still feels immediate.
# The wait happens on the GUI thread, like the materialize that precedes it.
_BACKOFF_SECONDS = (0.05, 0.10, 0.20, 0.40)


def atomic_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
    """``os.replace(src, dst)``, retried through a transient lock on either path.

    Raises the last ``PermissionError`` if every attempt loses the race, so callers keep their
    existing "leave the original intact and report it" error handling unchanged.
    """
    for delay in _BACKOFF_SECONDS:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            time.sleep(delay)
    os.replace(src, dst)  # last attempt: let the error propagate to the caller
