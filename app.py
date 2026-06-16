"""PdfApp — the resident QApplication that owns the open document windows.

PLAN.md, Critical files: ``PdfApp`` holds a ``dict[normalized_path -> window]`` (the basis for
"one window per document"), the shared settings store, and later the page clipboard +
``QLocalServer``. In M2 the dict + raise-on-reopen are in place; the single-instance IPC and the
Windows focus shims land in M5 (behind ``platform_integration``).
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from store.settings import Settings
from util.paths import normalize_path


class PdfApp(QApplication):
    def __init__(self, argv: list[str]) -> None:
        super().__init__(argv)
        # Set early: QStandardPaths.AppConfigLocation derives from the application name, so the
        # settings dir resolves to .config/pdfproj (Linux) / %APPDATA%\pdfproj (Windows).
        self.setApplicationName("pdfproj")
        self.setOrganizationName("pdfproj")
        self.settings = Settings()
        self._windows: dict[str, object] = {}
        # Page clipboard for cross-window cut/copy/paste (PLAN.md): each entry is
        # (source_id, source fitz.Document, source_page_index, rotation_override). Holding the
        # source doc lets the paste target register it and splice the PageRef losslessly.
        self.page_clipboard: list[tuple] = []

    def open_document(self, path: str):
        """Open ``path``, or raise its existing window if already open (no duplicate)."""
        key = normalize_path(path)
        existing = self._windows.get(key)
        if existing is not None:
            self._raise(existing)
            return existing

        from main_window import MainWindow  # local import avoids a cycle at module load

        window = MainWindow(self, path, self.settings)
        self._windows[key] = window
        window.show()
        self._raise(window)
        return window

    def forget_window(self, path: str) -> None:
        self._windows.pop(normalize_path(path), None)

    def rename_window(self, old_path: str, new_path: str, window) -> None:
        """Re-key a window after Save As, so one-window-per-document tracks the new identity."""
        self._windows.pop(normalize_path(old_path), None)
        self._windows[normalize_path(new_path)] = window

    @staticmethod
    def _raise(window) -> None:
        # M5 replaces this with platform_integration.activate_window() (Windows focus shims).
        if window.isMinimized():
            window.showNormal()
        window.raise_()
        window.activateWindow()
