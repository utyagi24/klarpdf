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

    # ---- recent signatures (M63) ------------------------------------------------
    #
    # **Paths only, never pixels.** A signature image is the most sensitive thing this app is likely
    # to touch, and a convenience cache of it would be a copy the user did not ask for, does not know
    # about, and cannot find to delete. So the list is exactly what the Open Recent list is: file
    # paths the user chose, which they can move or delete to revoke. A vanished file drops out on the
    # next read rather than lingering as a dead menu entry.

    _SIGNATURE_KEY = "recent_signatures"
    _SIGNATURE_SETTINGS_KEY = "signature_settings"
    _MAX_SIGNATURES = 6

    def add_recent_signature(self, path: str, white_to_alpha: "bool | None" = None,
                             white_threshold: "float | None" = None) -> None:
        """Record ``path`` as the most recently used signature / stamp image.

        Given the transparency settings, they are remembered **for that image** (M63.1): how much
        paper to drop out of a photographed signature is a property of the scan, not of the day, so
        re-placing it should not mean re-tuning it — and the Recent Signatures / Images menu, which
        places with no dialog at all, has no other way to know. Stored beside the list, not in it,
        so the list stays exactly what it says it is: paths, never pixels. The keys are paths the
        list already holds, so this adds no information about the user's files.
        """
        key = normalize_path(path)
        current = list(self.get_pref(self._SIGNATURE_KEY, []) or [])
        updated = [path] + [p for p in current if normalize_path(p) != key]
        del updated[self._MAX_SIGNATURES:]
        self.set_pref(self._SIGNATURE_KEY, updated)
        tuned = dict(self.get_pref(self._SIGNATURE_SETTINGS_KEY, {}) or {})
        if white_to_alpha is not None and white_threshold is not None:
            tuned[key] = {"white_to_alpha": bool(white_to_alpha),
                          "white_threshold": float(white_threshold)}
        self._prune_signature_settings(updated, tuned)

    def signature_settings(self, path: str) -> "dict | None":
        """The transparency settings ``path`` was last placed with, or ``None`` if it has none."""
        entry = (self.get_pref(self._SIGNATURE_SETTINGS_KEY, {}) or {}).get(normalize_path(path))
        if not isinstance(entry, dict) or not isinstance(entry.get("white_threshold"), (int, float)):
            return None
        return {"white_to_alpha": bool(entry.get("white_to_alpha", False)),
                "white_threshold": float(entry["white_threshold"])}

    def recent_signatures(self) -> list[str]:
        """Recent signature paths, most-recent-first, with vanished files pruned."""
        current = list(self.get_pref(self._SIGNATURE_KEY, []) or [])
        present = [p for p in current if os.path.exists(p)]
        if present != current:
            self.set_pref(self._SIGNATURE_KEY, present)
            self._prune_signature_settings(present)
        return present

    def _prune_signature_settings(self, kept_paths: list, tuned: "dict | None" = None) -> None:
        """Drop remembered settings for images no longer on the list — a setting must not outlive
        the entry it belongs to (a file the user deleted to revoke it least of all)."""
        if tuned is None:
            tuned = dict(self.get_pref(self._SIGNATURE_SETTINGS_KEY, {}) or {})
        keys = {normalize_path(p) for p in kept_paths}
        self.set_pref(self._SIGNATURE_SETTINGS_KEY,
                      {k: v for k, v in tuned.items() if k in keys})

    def clear_recent_signatures(self) -> None:
        self.set_pref(self._SIGNATURE_KEY, [])
        self.set_pref(self._SIGNATURE_SETTINGS_KEY, {})

    # ---- app-global preferences -------------------------------------------------

    def get_pref(self, key: str, default=None):
        """An app-global preference (shared by all windows), e.g. ``"sidebar_visible"``."""
        return self._prefs.get(key, default)

    def set_pref(self, key: str, value) -> None:
        if self._prefs.get(key) == value:
            return  # unchanged — skip the write
        self._prefs[key] = value
        self._save()
