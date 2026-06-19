"""Watch the open document's file for external changes (M24, PLAN.md §Next roadmap).

``QFileSystemWatcher`` tells us when the file is touched, but two quirks make a raw watcher
unreliable on its own, so we pair it with a cheap ``(mtime, size)`` signature:

* An atomic save (temp file + ``os.replace`` — our own Save, and most other editors) replaces the
  inode, so the watcher drops the path after a single event. We re-add it on every event.
* Our own Save would otherwise look like an external change. :meth:`record_current` snapshots the
  post-save signature so the self-triggered event is recognised as "no change".

The signature is also the source of truth for the synchronous :meth:`has_changed` check used on
window activation and before an overwriting Save, so detection never depends on a watcher event
actually firing — handy, because ``QFileSystemWatcher`` is flaky headless and on network shares.
This module is the M24 seam; the prompt/Reload policy lives in ``main_window``.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QFileSystemWatcher, QObject, Signal


class FileWatcher(QObject):
    """Emits :attr:`changedOnDisk` when the watched file differs from the last synced signature."""

    changedOnDisk = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._path: str | None = None
        self._sig: tuple | None = None

    def watch(self, path: str | None) -> None:
        """Watch ``path`` (replacing any previous watch), recording its signature as synced."""
        if self._watcher.files():
            self._watcher.removePaths(self._watcher.files())
        self._path = path
        self._sig = self._signature(path)
        self._rearm()

    def record_current(self) -> None:
        """Snapshot the current on-disk signature as the synced state — call after our own Save or
        a reload, so it is not later reported as an external change — and re-arm the watcher."""
        self._sig = self._signature(self._path)
        self._rearm()

    def has_changed(self) -> bool:
        """True if the file on disk differs from the last synced signature (incl. being removed)."""
        if self._path is None:
            return False
        return self._signature(self._path) != self._sig

    def _on_file_changed(self, _path: str) -> None:
        self._rearm()  # an atomic replace drops the watch — re-add so later edits still notify
        if self.has_changed():
            self.changedOnDisk.emit()

    def _rearm(self) -> None:
        if self._path and self._path not in self._watcher.files() and os.path.exists(self._path):
            self._watcher.addPath(self._path)

    @staticmethod
    def _signature(path: str | None):
        if not path:
            return None
        try:
            st = os.stat(path)
        except OSError:
            return None  # missing/unreadable → distinct from any real signature
        return (st.st_mtime_ns, st.st_size)
