"""Per-document view state — remember last page/zoom/scroll/geometry across sessions.

PLAN.md, Viewer: "Remember last page/zoom/scroll per document in a small local JSON under the
QStandardPaths app-config dir (%LOCALAPPDATA%\\klarpdf on Windows, ~/.config/klarpdf on Linux),
keyed by identity path." Using ``AppConfigLocation`` (not a literal path) is Portability hedge #1 —
Qt resolves it per-OS, so the same code is correct on Windows and Linux. Note it resolves to
**Local** AppData on Windows, not Roaming (%APPDATA%); ``packaging/installer.iss`` must match.

Offline, auditable, human-readable JSON. The identity key is :func:`util.paths.normalize_path`,
the single chokepoint, so this store never disagrees with the single-instance "already open?"
lookup.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from util.paths import normalize_path

_STATE_FILENAME = "view_state.json"
_APP_DIR_NAME = "klarpdf"
_MAX_RECENT = 10  # cap the File ▸ Open Recent list


def _config_dir() -> Path:
    """The app-config directory, guaranteed to end in a ``klarpdf`` leaf.

    ``AppConfigLocation`` already includes the application name once the QApplication sets it,
    but tests and early startup may query before that, so we defensively ensure the leaf.
    """
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
    path = Path(base) if base else Path.home() / ".config"
    if path.name.lower() != _APP_DIR_NAME:
        path = path / _APP_DIR_NAME
    return path


class Settings:
    """Load/save a map of ``normalized_path -> view-state dict``."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (_config_dir() / _STATE_FILENAME)
        self._docs: dict[str, dict] = {}
        self._recent: list[str] = []  # most-recent-first, original-case paths
        self._prefs: dict = {}        # app-global preferences (e.g. sidebar visibility)
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return  # tolerate a missing/corrupt/old file rather than crash on open
        if not isinstance(raw, dict):
            return
        self._docs = {k: v for k, v in raw.get("documents", {}).items() if isinstance(v, dict)}
        self._recent = [p for p in raw.get("recent", []) if isinstance(p, str)]
        prefs = raw.get("preferences", {})
        self._prefs = dict(prefs) if isinstance(prefs, dict) else {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "documents": self._docs,
            "recent": self._recent,
            "preferences": self._prefs,
        }
        # Atomic-ish write: temp then replace, so a crash mid-write can't truncate the file.
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        # Windows: an AV scanner / indexer can briefly hold the destination, making the replace
        # fail with PermissionError. Retry a few times before giving up (no-op on POSIX).
        last_error: PermissionError | None = None
        for delay in (0.0, 0.02, 0.05, 0.1, 0.2):
            if delay:
                time.sleep(delay)
            try:
                tmp.replace(self._path)
                return
            except PermissionError as exc:
                last_error = exc
        raise last_error

    def get_doc_state(self, doc_path: str) -> dict:
        """Return the saved view state for ``doc_path`` (empty dict if none)."""
        return dict(self._docs.get(normalize_path(doc_path), {}))

    def set_doc_state(self, doc_path: str, state: dict) -> None:
        """Persist the view state for ``doc_path`` immediately."""
        self._docs[normalize_path(doc_path)] = dict(state)
        self._save()

    # ---- recent documents (MRU) -------------------------------------------------

    def add_recent(self, doc_path: str) -> None:
        """Record ``doc_path`` as the most recently opened (deduped by identity, capped)."""
        key = normalize_path(doc_path)
        # Drop any prior entry for the same file (case-insensitive identity), keep original case.
        updated = [doc_path] + [p for p in self._recent if normalize_path(p) != key]
        del updated[_MAX_RECENT:]
        if updated == self._recent:
            return  # reopening the already-most-recent file changes nothing — skip the write
        self._recent = updated
        self._save()

    def recent_files(self) -> list[str]:
        """Recent paths, most-recent-first, with vanished files pruned (and persisted if so)."""
        present = [p for p in self._recent if os.path.exists(p)]
        if present != self._recent:
            self._recent = present
            self._save()
        return list(present)

    def clear_recent(self) -> None:
        self._recent = []
        self._save()

    # ---- app-global preferences -------------------------------------------------

    def get_pref(self, key: str, default=None):
        """An app-global preference (shared by all windows), e.g. ``"sidebar_visible"``."""
        return self._prefs.get(key, default)

    def set_pref(self, key: str, value) -> None:
        if self._prefs.get(key) == value:
            return  # unchanged — skip the write
        self._prefs[key] = value
        self._save()
