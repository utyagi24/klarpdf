"""MainWindow — one window per document (PLAN.md, Critical files).

M2 scope: View mode only — central :class:`~viewer.pdf_view.PdfView`, a thumbnail dock for
jump-to-page, and a toolbar/menu for zoom / fit / rotate. View state (page/zoom/rotation/size)
is restored on open and saved on close via :class:`~store.settings.Settings`.

Organize mode, the QUndoStack edits, Save/Save As, and the dirty-close prompt arrive in M4; the
seams (it owns the VirtualDocument; close persists state) are placed so they slot in cleanly.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QDockWidget, QFileDialog, QMainWindow

from model.virtual_document import VirtualDocument
from organize.thumbnail_panel import ThumbnailPanel
from store.settings import Settings
from viewer.pdf_view import PdfView


class MainWindow(QMainWindow):
    def __init__(self, app, path: str, settings: Settings) -> None:
        super().__init__()
        self._app = app
        self._settings = settings
        self.path = path
        self._initialized = False

        self.vdoc = VirtualDocument.from_path(path)
        self.view = PdfView(self.vdoc)
        self.setCentralWidget(self.view)

        self.thumbs = ThumbnailPanel(self.vdoc)
        dock = QDockWidget("Pages", self)
        dock.setWidget(self.thumbs)
        dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

        self.view.currentPageChanged.connect(self.thumbs.set_current)
        self.thumbs.pageActivated.connect(self.view.goto_page)

        self._build_actions()
        self.setWindowTitle(f"{os.path.basename(path)} — pdfproj")
        self.resize(1000, 800)

    # ---- actions / menus --------------------------------------------------------

    def _build_actions(self) -> None:
        bar = self.addToolBar("Main")
        menu = self.menuBar()
        file_menu = menu.addMenu("&File")
        view_menu = menu.addMenu("&View")

        def act(text, slot, shortcut=None, to_bar=True, to_menu=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(slot)
            if to_bar:
                bar.addAction(a)
            if to_menu is not None:
                to_menu.addAction(a)
            return a

        act("Open…", self._open_dialog, QKeySequence.StandardKey.Open, to_bar=False, to_menu=file_menu)
        act("Close", self.close, QKeySequence.StandardKey.Close, to_bar=False, to_menu=file_menu)

        act("Zoom In", self.view.zoom_in, QKeySequence.StandardKey.ZoomIn, to_menu=view_menu)
        act("Zoom Out", self.view.zoom_out, QKeySequence.StandardKey.ZoomOut, to_menu=view_menu)
        act("Fit Width", self.view.fit_width, "Ctrl+1", to_menu=view_menu)
        act("Fit Page", self.view.fit_page, "Ctrl+2", to_menu=view_menu)
        view_menu.addSeparator()
        act("Rotate Left", lambda: self.view.rotate_view(-90), "Ctrl+L", to_menu=view_menu)
        act("Rotate Right", lambda: self.view.rotate_view(90), "Ctrl+R", to_menu=view_menu)

    def _open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF files (*.pdf)")
        if path:
            self._app.open_document(path)

    # ---- view-state persistence -------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._initialized:
            return
        self._initialized = True
        state = self._settings.get_doc_state(self.path)
        if state.get("win_w") and state.get("win_h"):
            self.resize(int(state["win_w"]), int(state["win_h"]))
        if state:
            self.view.apply_state(state)
        else:
            self.view.fit_width()  # sensible default for a first open

    def closeEvent(self, event) -> None:
        # M4 adds the dirty Save/Discard/Cancel prompt here; M2 just remembers where we were.
        state = self.view.view_state()
        state.update({"win_w": self.width(), "win_h": self.height()})
        self._settings.set_doc_state(self.path, state)
        self._app.forget_window(self.path)
        super().closeEvent(event)
