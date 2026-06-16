"""MainWindow — one window per document (PLAN.md, Critical files).

Owns the :class:`~model.virtual_document.VirtualDocument`, the :class:`PdfView`, the thumbnail
panel, and (M4) the ``QUndoStack`` that drives every page edit. All edits go through
:mod:`model.edit_commands` so undo/redo and the dirty flag come for free; a structural change
refreshes the view + thumbnails and clears stale selection/search overlays. Save materialises the
edit list losslessly (:class:`~model.edit_engine.PyMuPDFEngine`) with an atomic temp+replace;
closing a dirty document prompts Save / Discard / Cancel.

Cross-window move/copy is two independent commands on two stacks (delete in the source, insert in
the target) — a documented limitation: undoing the paste in the target does not restore the page
in the source.
"""

from __future__ import annotations

import os
import tempfile

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QUndoStack
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from model.edit_commands import (
    DeleteCommand,
    InsertCommand,
    MovePagesCommand,
    RotatePagesCommand,
)
from model.edit_engine import PyMuPDFEngine
from model.virtual_document import PageRef, VirtualDocument
from organize.thumbnail_panel import ThumbnailPanel
from store.settings import Settings
from util.paths import normalize_path
from viewer.pdf_view import PdfView
from viewer.search import FindBar, SearchController
from viewer.text_selection import TextSelection


class MainWindow(QMainWindow):
    def __init__(self, app, path: str, settings: Settings) -> None:
        super().__init__()
        self._app = app
        self._settings = settings
        self.path = path
        self._initialized = False

        self.vdoc = VirtualDocument.from_path(path)
        self.view = PdfView(self.vdoc)
        self.undo_stack = QUndoStack(self)

        # Text selection + search overlays live on the view (M3).
        self.view.selection = TextSelection(self.view)
        self.view.search = SearchController(self.view)
        self.find_bar = FindBar(self.view)  # hidden until Ctrl+F

        # Central column: find bar above the view. (A QToolBar host collapses to zero height
        # while the bar is hidden and won't re-expand on show(), so use a real layout.)
        central = QWidget()
        col = QVBoxLayout(central)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self.find_bar)
        col.addWidget(self.view, 1)
        self.setCentralWidget(central)

        self.thumbs = ThumbnailPanel(self.vdoc)
        self.thumbs.source_key = normalize_path(path)  # identity for cross-window drag/drop
        dock = QDockWidget("Pages", self)
        dock.setWidget(self.thumbs)
        dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

        self.view.currentPageChanged.connect(self.thumbs.set_current)
        self.thumbs.pageActivated.connect(self.view.goto_page)
        self.thumbs.pagesDropped.connect(self._on_pages_dropped)
        self.thumbs.deleteRequested.connect(self._delete_rows)
        self.thumbs.customContextMenuRequested.connect(self._page_context_menu)

        # Any edit (push/undo/redo) refreshes the surfaces; clean state drives the title's *.
        # Bound methods (not lambdas) so Qt auto-disconnects when the window is destroyed.
        self.undo_stack.indexChanged.connect(self._on_doc_changed)
        self.undo_stack.cleanChanged.connect(self._on_clean_changed)

        self._build_actions()
        self.setWindowTitle(f"{os.path.basename(path)} — pdfproj[*]")
        self.resize(1000, 800)

    # ---- actions / menus --------------------------------------------------------

    def _build_actions(self) -> None:
        bar = self.addToolBar("Main")
        menu = self.menuBar()
        file_menu = menu.addMenu("&File")
        edit_menu = menu.addMenu("&Edit")
        view_menu = menu.addMenu("&View")

        def act(text, slot, shortcut=None, to_bar=True, to_menu=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(shortcut if isinstance(shortcut, QKeySequence) else QKeySequence(shortcut))
            a.triggered.connect(slot)
            if to_bar:
                bar.addAction(a)
            if to_menu is not None:
                to_menu.addAction(a)
            return a

        act("Open…", self._open_dialog, QKeySequence.StandardKey.Open, to_bar=False, to_menu=file_menu)
        act("Save", self.save, QKeySequence.StandardKey.Save, to_bar=False, to_menu=file_menu)
        act("Save As…", self.save_as, QKeySequence.StandardKey.SaveAs, to_bar=False, to_menu=file_menu)
        file_menu.addSeparator()
        act("Close", self.close, QKeySequence.StandardKey.Close, to_bar=False, to_menu=file_menu)

        # Undo/Redo: QUndoStack supplies labelled actions ("Undo Move 2 pages") for free.
        undo = self.undo_stack.createUndoAction(self, "&Undo")
        undo.setShortcut(QKeySequence.StandardKey.Undo)
        redo = self.undo_stack.createRedoAction(self, "&Redo")
        redo.setShortcut(QKeySequence.StandardKey.Redo)
        edit_menu.addAction(undo)
        edit_menu.addAction(redo)
        bar.addAction(undo)
        bar.addAction(redo)
        edit_menu.addSeparator()
        # Page ops act on the thumbnail selection. No accelerators here: Ctrl+C is text Copy and
        # Delete is handled inside the panel; the context menu is the primary discoverable path.
        act("Cut Pages", lambda: self._cut_pages(), to_bar=False, to_menu=edit_menu)
        act("Copy Pages", lambda: self._copy_pages(), to_bar=False, to_menu=edit_menu)
        act("Paste Pages", lambda: self._paste_pages(), to_bar=False, to_menu=edit_menu)
        act("Delete Pages", lambda: self._delete_rows(self.thumbs.selected_rows()), to_bar=False, to_menu=edit_menu)
        act("Insert Pages from File…", self._insert_from_file, to_bar=False, to_menu=edit_menu)
        edit_menu.addSeparator()
        act("Copy", self._copy_selection, QKeySequence.StandardKey.Copy, to_bar=False, to_menu=edit_menu)
        act("Find…", self._show_find, QKeySequence.StandardKey.Find, to_bar=False, to_menu=edit_menu)
        act("Find Next", self.find_bar.find_next, QKeySequence.StandardKey.FindNext, to_bar=False, to_menu=edit_menu)
        act("Find Previous", self.find_bar.find_prev, QKeySequence.StandardKey.FindPrevious, to_bar=False, to_menu=edit_menu)

        act("Zoom In", self.view.zoom_in, QKeySequence.StandardKey.ZoomIn, to_menu=view_menu)
        act("Zoom Out", self.view.zoom_out, QKeySequence.StandardKey.ZoomOut, to_menu=view_menu)
        act("Fit Width", self.view.fit_width, "Ctrl+1", to_menu=view_menu)
        act("Fit Page", self.view.fit_page, "Ctrl+2", to_menu=view_menu)
        view_menu.addSeparator()
        # Rotate the current/selected page(s) only — a real per-page edit (undoable, saved),
        # not a whole-view spin. PdfView.rotate_view still exists for view-only rotation.
        act("Rotate Left", lambda: self._rotate_pages(-90), "Ctrl+L", to_menu=view_menu)
        act("Rotate Right", lambda: self._rotate_pages(90), "Ctrl+R", to_menu=view_menu)

    def _open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF files (*.pdf)")
        if path:
            self._app.open_document(path)

    def _copy_selection(self) -> None:
        self.view.selection.copy()

    def _show_find(self) -> None:
        self.find_bar.show_bar()

    # ---- page edits (all via the undo stack) ------------------------------------

    def _on_doc_changed(self, _index: int) -> None:
        # A structural edit invalidates page indices, so drop stale overlays and rebuild.
        self.view.selection.clear()
        self.view.search.clear()
        self.view.reload()
        self.thumbs.populate()

    def _on_clean_changed(self, clean: bool) -> None:
        self.setWindowModified(not clean)

    def _insertion_index(self) -> int:
        """Where Paste / Insert land: after the last selected page, else at the end."""
        rows = self.thumbs.selected_rows()
        return rows[-1] + 1 if rows else self.vdoc.page_count

    def _reorder(self, rows, before_index: int) -> None:
        self.undo_stack.push(MovePagesCommand(self.vdoc, rows, before_index))

    def _on_pages_dropped(self, source_key, rows, before_index: int) -> None:
        """A drag dropped onto this window's Pages panel.

        Same document → reorder (move). Another window → copy those pages in (a cross-window
        *move* would need two undo stacks like cut/paste, so drag defaults to copy and leaves the
        source intact). Refs splice in losslessly; the object-level copy happens at save.
        """
        rows = sorted({int(r) for r in rows})
        if not rows:
            return
        if source_key == normalize_path(self.path):
            self._reorder(rows, before_index)
            return
        src = self._app.window_for_key(source_key) if source_key else None
        if src is None or src is self:
            return
        refs = []
        for i in rows:
            if 0 <= i < src.vdoc.page_count:
                ref = src.vdoc.ordered[i]
                self.vdoc.register_source(ref.source_id, src.vdoc.sources[ref.source_id])
                refs.append(PageRef(ref.source_id, ref.source_page_index, ref.rotation_override))
        if refs:
            self.undo_stack.push(InsertCommand(self.vdoc, before_index, refs, text="Drag pages in"))

    def _rotate_pages(self, delta: int) -> None:
        """Rotate the selected pages — or the current page if none are selected — by ``delta``."""
        rows = self.thumbs.selected_rows() or [self.view.current_page]
        rows = [r for r in rows if 0 <= r < self.vdoc.page_count]
        if rows:
            self.undo_stack.push(RotatePagesCommand(self.vdoc, rows, delta))

    def _delete_rows(self, rows) -> None:
        if rows:
            self.undo_stack.push(DeleteCommand(self.vdoc, rows))

    def _copy_pages(self, rows=None) -> None:
        rows = rows if rows else self.thumbs.selected_rows()
        clipboard = []
        for i in rows:
            ref = self.vdoc.ordered[i]
            clipboard.append((ref.source_id, self.vdoc.sources[ref.source_id],
                              ref.source_page_index, ref.rotation_override))
        if clipboard:
            self._app.page_clipboard = clipboard

    def _cut_pages(self, rows=None) -> None:
        rows = rows if rows else self.thumbs.selected_rows()
        if not rows:
            return
        self._copy_pages(rows)
        self.undo_stack.push(DeleteCommand(self.vdoc, rows))

    def _paste_pages(self, before_index=None) -> None:
        payloads = self._app.page_clipboard
        if not payloads:
            return
        before = self._insertion_index() if before_index is None else before_index
        refs = []
        for source_id, doc, page_index, rotation in payloads:
            self.vdoc.register_source(source_id, doc)
            refs.append(PageRef(source_id, page_index, rotation))
        self.undo_stack.push(InsertCommand(self.vdoc, before, refs, text="Paste pages"))

    def _insert_from_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Insert Pages from PDF", "", "PDF files (*.pdf)")
        if not path:
            return
        source_id = self.vdoc.open_source(path)
        doc = self.vdoc.sources[source_id]
        refs = [PageRef(source_id, i) for i in range(doc.page_count)]
        self.undo_stack.push(InsertCommand(self.vdoc, self._insertion_index(), refs,
                                           text="Insert pages from file"))

    def _page_context_menu(self, pos) -> None:
        rows = self.thumbs.selected_rows()
        menu = QMenu(self)
        a_cut = menu.addAction("Cut")
        a_copy = menu.addAction("Copy")
        a_paste = menu.addAction("Paste")
        a_delete = menu.addAction("Delete")
        menu.addSeparator()
        a_insert = menu.addAction("Insert Pages from File…")
        for a in (a_cut, a_copy, a_delete):
            a.setEnabled(bool(rows))
        a_paste.setEnabled(bool(self._app.page_clipboard))
        chosen = menu.exec(self.thumbs.mapToGlobal(pos))
        if chosen is a_cut:
            self._cut_pages(rows)
        elif chosen is a_copy:
            self._copy_pages(rows)
        elif chosen is a_paste:
            self._paste_pages(rows[-1] + 1 if rows else None)
        elif chosen is a_delete:
            self._delete_rows(rows)
        elif chosen is a_insert:
            self._insert_from_file()

    # ---- save (materialize-on-save) ---------------------------------------------

    def save(self) -> bool:
        if self.vdoc.path is None:
            return self.save_as()
        return self._write_to(self.vdoc.path)

    def save_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, "Save PDF As", self.vdoc.path or "", "PDF files (*.pdf)")
        if not path:
            return False
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        if not self._write_to(path):
            return False
        old = self.path
        self.vdoc.path = self.path = path
        self._app.rename_window(old, path, self)
        self.thumbs.source_key = normalize_path(path)  # re-key for cross-window drag/drop
        self.setWindowTitle(f"{os.path.basename(path)} — pdfproj[*]")
        return True

    def _write_to(self, target_path: str) -> bool:
        """Materialize to a temp file in the same directory, then atomically os.replace it in."""
        directory = os.path.dirname(os.path.abspath(target_path)) or "."
        fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=directory)
        os.close(fd)
        try:
            PyMuPDFEngine().materialize(self.vdoc, tmp)
            os.replace(tmp, target_path)
        except Exception as exc:  # surface, don't crash; leave the original file intact
            if os.path.exists(tmp):
                os.remove(tmp)
            QMessageBox.critical(self, "Save failed", str(exc))
            return False
        self.undo_stack.setClean()
        self.vdoc.mark_clean()
        return True

    # ---- view-state persistence + close prompt ----------------------------------

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

    def _confirm_discard(self):
        return QMessageBox.question(
            self,
            "Unsaved changes",
            f"Save changes to {os.path.basename(self.path)} before closing?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )

    def closeEvent(self, event) -> None:
        if not self.undo_stack.isClean():
            choice = self._confirm_discard()
            if choice == QMessageBox.StandardButton.Save:
                if not self.save():
                    event.ignore()
                    return
            elif choice == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            # Discard → fall through and close without saving.
        state = self.view.view_state()
        state.update({"win_w": self.width(), "win_h": self.height()})
        self._settings.set_doc_state(self.path, state)
        self._app.forget_window(self.path)
        super().closeEvent(event)
