"""Per-document view state — remember last page/zoom/scroll/geometry across sessions.

PLAN.md, Viewer: "Remember last page/zoom/scroll per document in a small local JSON under the
QStandardPaths app-config dir (%APPDATA%\\pdfproj on Windows, ~/.config/pdfproj on Linux),
keyed by identity path." Using ``AppConfigLocation`` (not a literal ``%APPDATA%``) is Portability
hedge #1 — Qt resolves it per-OS, so the same code is correct on Windows and Linux.

Offline, auditable, human-readable JSON. The identity key is :func:`util.paths.normalize_path`,
the single chokepoint, so this store never disagrees with the single-instance "already open?"
lookup.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from util.paths import normalize_path

_STATE_FILENAME = "view_state.json"
_APP_DIR_NAME = "pdfproj"


def _config_dir() -> Path:
    """The app-config directory, guaranteed to end in a ``pdfproj`` leaf.

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
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            # Be liberal: tolerate a corrupt/old file rather than crash on open.
            if isinstance(raw, dict):
                self._docs = {k: v for k, v in raw.get("documents", {}).items() if isinstance(v, dict)}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self._docs = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "documents": self._docs}
        # Atomic-ish write: temp then replace, so a crash mid-write can't truncate the file.
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._path)

    def get_doc_state(self, doc_path: str) -> dict:
        """Return the saved view state for ``doc_path`` (empty dict if none)."""
        return dict(self._docs.get(normalize_path(doc_path), {}))

    def set_doc_state(self, doc_path: str, state: dict) -> None:
        """Persist the view state for ``doc_path`` immediately."""
        self._docs[normalize_path(doc_path)] = dict(state)
        self._save()
