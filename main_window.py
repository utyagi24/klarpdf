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

from PySide6.QtCore import QEvent, QRect, QSize, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QUndoStack
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from model.edit_commands import (
    AddAnnotationCommand,
    DeleteCommand,
    InsertCommand,
    MovePagesCommand,
    RemoveAnnotationCommand,
    ReplaceAnnotationCommand,
    RotatePagesCommand,
    SetFieldValueCommand,
)
from model.page_edits import Highlight, Redaction
from model.edit_engine import PyMuPDFEngine
from model.virtual_document import IMAGE_EXTENSIONS, PageRef, VirtualDocument
from organize.thumbnail_panel import ThumbnailPanel
from store.file_watch import FileWatcher
from store.settings import Settings
from ui import icons
from util.paths import normalize_path
from viewer.annotations import AnnotationOverlay
from viewer.form_fill import FormFiller
from viewer.links import LinkNavigator
from viewer.pdf_view import PdfView
from viewer.search import FindBar, SearchController
from viewer.text_selection import TextSelection
from viewer.tools import ArmedTool, InteractionMode
from viewer.zoom_widget import ZoomWidget


def _ask_pdf_password(path: str, retry: bool) -> str | None:
    """Password provider for an encrypted PDF (M32): a masked input dialog, looped by the model on a
    wrong password (``retry=True`` shows a "try again" message). Returns the entered text, or
    ``None`` if the user cancelled (the open is then quietly abandoned). Module-level so tests can
    monkeypatch it."""
    name = os.path.basename(path)
    message = (
        f"Incorrect password. Try again for “{name}”:"
        if retry
        else f"“{name}” is password-protected.\n\nEnter password:"
    )
    text, ok = QInputDialog.getText(None, "Password required", message, QLineEdit.EchoMode.Password)
    return text if ok else None


class MainWindow(QMainWindow):
    def __init__(self, app, path: str, settings: Settings) -> None:
        super().__init__()
        self._app = app
        self._settings = settings
        self.path = path
        self._initialized = False

        # from_path may raise PasswordRequired for an encrypted file (the prompt was cancelled);
        # app.open_document constructs MainWindow in a try/except and simply opens no window then.
        self.vdoc = VirtualDocument.from_path(path, password_provider=_ask_pdf_password)
        self.view = PdfView(self.vdoc)
        self.undo_stack = QUndoStack(self)

        # Text selection + search overlays live on the view (M3); form-fill overlay (M14).
        self.view.selection = TextSelection(self.view)
        self.view.search = SearchController(self.view)
        self.view.form = FormFiller(self.view, self._set_field_value)
        self.view.links = LinkNavigator(self.view)  # click internal links to jump (M33)
        self.view.annotations = AnnotationOverlay(
            self.view, self._add_annotation, self._remove_annotation, self._replace_annotation
        )
        # PdfView built its scene in __init__ (before this overlay existed), and the first show's
        # fit/restore can early-return without a rebuild — so paint any annotations the document was
        # opened with (round-tripped from a prior save, M31) now that the overlay is wired.
        self.view.annotations.repaint()
        self.view.armedChanged.connect(self._on_armed_changed)
        self.view.applyTextTool.connect(self._apply_text_tool)
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
        self.pages_dock = QDockWidget("Pages", self)
        self.pages_dock.setWidget(self.thumbs)
        # Closable (hide/show via View ▸ Pages Sidebar) but NOT floatable or movable — it must stay
        # docked, never tear off into a separate window the user can lose (the inherited M2 bug).
        self.pages_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.pages_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.pages_dock)

        self.view.currentPageChanged.connect(self.thumbs.set_current)
        self.thumbs.pageActivated.connect(self.view.goto_page)
        self.thumbs.pagesDropped.connect(self._on_pages_dropped)
        self.thumbs.filesDropped.connect(self._on_files_dropped)
        self.thumbs.deleteRequested.connect(self._delete_rows)
        self.thumbs.customContextMenuRequested.connect(self._page_context_menu)

        # Any edit (push/undo/redo) refreshes the surfaces; clean state drives the title's *.
        # Bound methods (not lambdas) so Qt auto-disconnects when the window is destroyed.
        self.undo_stack.indexChanged.connect(self._on_doc_changed)
        self.undo_stack.cleanChanged.connect(self._on_clean_changed)

        # M24: warn when the open file is changed on disk by another program. Watches self.path;
        # the prompt / Reload policy lives in the handlers below.
        self._external_prompt_open = False
        self._watcher = FileWatcher(self)
        self._watcher.watch(self.path)
        self._watcher.changedOnDisk.connect(self._check_external_change)

        self._build_actions()
        self.setWindowTitle(f"{os.path.basename(path)} — pdfproj[*]")
        self.setWindowIcon(icons.app_icon())
        self.resize(1000, 800)

    # ---- actions / menus --------------------------------------------------------

    def _build_actions(self) -> None:
        bar = self.addToolBar("Main")
        bar.setMovable(False)
        # Icon-only toolbar (the user's ask: icons instead of text). Each QAction's text becomes
        # the button tooltip, so the labels stay discoverable on hover and in the menus.
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        bar.setIconSize(QSize(20, 20))
        # Press/hover feedback + spacing between functional groups. Translucent grey reads on both
        # light and dark themes, so this needs no per-theme rebuild. The separator gains margin so
        # grouped buttons sit close while groups are clearly divided.
        bar.setStyleSheet(
            "QToolBar { spacing: 1px; padding: 2px; }"
            "QToolButton { border: 1px solid transparent; border-radius: 4px; padding: 3px; }"
            "QToolButton:hover { background-color: rgba(128, 128, 128, 46); }"
            "QToolButton:pressed { background-color: rgba(128, 128, 128, 100); }"
            "QToolButton:checked { background-color: rgba(128, 128, 128, 72); }"
            "QToolBar::separator { width: 1px; margin: 5px 8px;"
            " background-color: rgba(128, 128, 128, 90); }"
        )
        menu = self.menuBar()
        file_menu = menu.addMenu("&File")
        edit_menu = menu.addMenu("&Edit")
        view_menu = menu.addMenu("&View")

        def act(text, slot, shortcut=None, icon=None, to_menu=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(shortcut if isinstance(shortcut, QKeySequence) else QKeySequence(shortcut))
            if icon:
                a.setIcon(icons.icon(icon))
                a.setProperty("iconName", icon)  # so _retint_icons can re-tint on theme change
            a.triggered.connect(slot)
            if to_menu is not None:
                to_menu.addAction(a)
            return a

        # File
        a_open = act("Open…", self._open_dialog, QKeySequence.StandardKey.Open, icon="open", to_menu=file_menu)
        # Rebuilt each time it opens (aboutToShow), so it reflects MRU changes from other windows.
        self._recent_menu = file_menu.addMenu("Open &Recent")
        self._recent_menu.aboutToShow.connect(self._populate_recent_menu)
        a_save = act("Save", self.save, QKeySequence.StandardKey.Save, icon="save", to_menu=file_menu)
        act("Save As…", self.save_as, QKeySequence.StandardKey.SaveAs, to_menu=file_menu)
        # Export → a *derived* copy (Save stays editable; Export locks). One format today
        # (flattened PDF); the submenu grows an image format in M36.
        export_menu = file_menu.addMenu("&Export")
        act("Flattened PDF…", self._export_flattened_pdf, to_menu=export_menu)
        act("Image…", self._export_images, to_menu=export_menu)
        # Revert: discard all edits and reload from disk. Enabled only while there are unsaved
        # changes (a clean doc has nothing to revert) — toggled in _on_clean_changed.
        self._a_revert = act("Revert to Saved", self.revert, to_menu=file_menu)
        self._a_revert.setEnabled(False)
        file_menu.addSeparator()
        a_print = act("Print…", self._print, QKeySequence.StandardKey.Print, icon="print", to_menu=file_menu)
        file_menu.addSeparator()
        act("Close", self.close, QKeySequence.StandardKey.Close, to_menu=file_menu)

        # Undo/Redo: QUndoStack supplies labelled actions ("Undo Move 2 pages") for free.
        undo = self.undo_stack.createUndoAction(self, "&Undo")
        undo.setShortcut(QKeySequence.StandardKey.Undo)
        undo.setIcon(icons.icon("undo"))
        undo.setProperty("iconName", "undo")
        redo = self.undo_stack.createRedoAction(self, "&Redo")
        redo.setShortcut(QKeySequence.StandardKey.Redo)
        redo.setIcon(icons.icon("redo"))
        redo.setProperty("iconName", "redo")
        edit_menu.addAction(undo)
        edit_menu.addAction(redo)
        edit_menu.addSeparator()
        # Page ops act on the thumbnail selection. No accelerators here: Ctrl+C is text Copy and
        # Delete is handled inside the panel; the toolbar + context menu are the discoverable paths.
        a_cut = act("Cut Pages", lambda: self._cut_pages(), icon="cut", to_menu=edit_menu)
        a_copy_pg = act("Copy Pages", lambda: self._copy_pages(), icon="copy", to_menu=edit_menu)
        a_paste = act("Paste Pages", lambda: self._paste_pages(), icon="paste", to_menu=edit_menu)
        a_delete = act("Delete Pages", lambda: self._delete_rows(self.thumbs.selected_rows()), icon="delete", to_menu=edit_menu)
        a_insert = act("Insert Pages from File…", self._insert_from_file, icon="insert", to_menu=edit_menu)
        edit_menu.addSeparator()
        act("Copy", self._copy_selection, QKeySequence.StandardKey.Copy, to_menu=edit_menu)
        a_find = act("Find…", self._show_find, QKeySequence.StandardKey.Find, icon="find", to_menu=edit_menu)
        act("Find Next", self.find_bar.find_next, QKeySequence.StandardKey.FindNext, to_menu=edit_menu)
        act("Find Previous", self.find_bar.find_prev, QKeySequence.StandardKey.FindPrevious, to_menu=edit_menu)

        # View
        a_zout = act("Zoom Out", self.view.zoom_out, QKeySequence.StandardKey.ZoomOut, icon="zoom-out", to_menu=view_menu)
        a_zin = act("Zoom In", self.view.zoom_in, QKeySequence.StandardKey.ZoomIn, icon="zoom-in", to_menu=view_menu)
        # Live magnification indicator + preset/typed zoom (1.0 == 100%).
        self.zoom_widget = ZoomWidget(self.view)
        act("Actual Size", self.view.actual_size, "Ctrl+0", to_menu=view_menu)  # reset to 100%
        a_fitw = act("Fit Width", self.view.fit_width, "Ctrl+1", icon="fit-width", to_menu=view_menu)
        a_fitp = act("Fit Page", self.view.fit_page, "Ctrl+2", icon="fit-page", to_menu=view_menu)
        view_menu.addSeparator()
        # Rotate the current/selected page(s) only — a real per-page edit (undoable, saved),
        # not a whole-view spin. PdfView.rotate_view still exists for view-only rotation.
        a_rotl = act("Rotate Left", lambda: self._rotate_pages(-90), "Ctrl+L", icon="rotate-left", to_menu=view_menu)
        a_rotr = act("Rotate Right", lambda: self._rotate_pages(90), "Ctrl+R", icon="rotate-right", to_menu=view_menu)
        view_menu.addSeparator()
        # Persistent interaction mode: Select (default — text/forms/move) vs Grab (hand-pan).
        # Mutually exclusive; the checked toolbar button shows the active tool.
        mode_group = QActionGroup(self)
        mode_group.setExclusive(True)
        a_select = act("Select", lambda: self.view.set_mode(InteractionMode.SELECT), icon="select", to_menu=view_menu)
        a_grab = act("Grab", lambda: self.view.set_mode(InteractionMode.GRAB), icon="grab", to_menu=view_menu)
        for a in (a_select, a_grab):
            a.setCheckable(True)
            mode_group.addAction(a)
        a_select.setChecked(True)
        self._a_select = a_select
        view_menu.addSeparator()
        # One-shot armed annotate/redact tools: click to arm (button lights), do one gesture, then
        # it reverts to Select. Checkable only to reflect the armed state — NOT in the mode group.
        # All four are consistent — arm, then a single gesture: TextBox = click; Highlight /
        # Redact Text = drag over text; Redact Block = drag a rectangle.
        a_textbox = act("Add Text Box", lambda: self._arm_tool(ArmedTool.TEXTBOX), icon="textbox", to_menu=view_menu)
        a_textbox.setToolTip("Add Text Box — click a spot, then type (drag to move, double-click to edit)")
        a_highlight = act("Highlight", lambda: self._arm_tool(ArmedTool.HIGHLIGHT), "Ctrl+H", icon="highlight", to_menu=view_menu)
        a_highlight.setToolTip("Highlight — drag over text to highlight it")
        a_redact_text = act("Redact Text", lambda: self._arm_tool(ArmedTool.REDACT_TEXT), "Ctrl+Shift+R", icon="redact-text", to_menu=view_menu)
        a_redact_text.setToolTip("Redact Text — drag over text to permanently remove it at save")
        a_redact_block = act("Redact Block", lambda: self._arm_tool(ArmedTool.REDACT_REGION), icon="redact", to_menu=view_menu)
        a_redact_block.setToolTip("Redact Block — drag a box to permanently remove its contents at save")
        self._armed_actions = {
            ArmedTool.TEXTBOX: a_textbox,
            ArmedTool.HIGHLIGHT: a_highlight,
            ArmedTool.REDACT_TEXT: a_redact_text,
            ArmedTool.REDACT_REGION: a_redact_block,
        }
        for a in self._armed_actions.values():
            a.setCheckable(True)
        view_menu.addSeparator()
        # Checkable show/hide for the Pages sidebar — menu item + a dedicated toolbar button (its
        # checked state mirrors the panel's visibility, with the :checked toolbar styling).
        pages_toggle = self.pages_dock.toggleViewAction()
        pages_toggle.setText("&Pages Sidebar")
        pages_toggle.setIcon(icons.icon("sidebar"))
        pages_toggle.setToolTip("Show/Hide the Pages sidebar")
        pages_toggle.setProperty("iconName", "sidebar")  # re-tinted on theme change
        view_menu.addAction(pages_toggle)

        # Toolbar: built explicitly (order independent of menu wiring), grouped functionally with
        # separators — file · history · page edits · zoom/fit · rotate · search.
        groups = (
            [pages_toggle],
            [a_open, a_save, a_print],
            [a_select, a_grab],
            [a_zout, self.zoom_widget, a_zin, a_fitw, a_fitp],
            [undo, redo],
            [a_cut, a_copy_pg, a_paste, a_delete, a_insert],
            [a_textbox, a_highlight, a_redact_text, a_redact_block],
            [a_rotl, a_rotr],
            [a_find],
        )
        for gi, group in enumerate(groups):
            if gi:
                bar.addSeparator()
            for item in group:
                if isinstance(item, QAction):
                    bar.addAction(item)
                else:
                    bar.addWidget(item)  # e.g. the zoom % combo

    def _retint_icons(self) -> None:
        """Re-fetch every action's icon so it matches the current theme's text colour."""
        for a in self.findChildren(QAction):
            name = a.property("iconName")
            if name:
                a.setIcon(icons.icon(name))

    def changeEvent(self, event) -> None:
        # A runtime OS light/dark switch reaches a window as a PaletteChange (its *effective* palette
        # changed) — the application also broadcasts ApplicationPaletteChange. Either way, re-tint the
        # toolbar glyphs so they don't vanish against the new background. (Qt delivers PaletteChange,
        # not ApplicationPaletteChange, to changeEvent, so the latter alone never fired — M29.)
        if event.type() in (QEvent.Type.ApplicationPaletteChange, QEvent.Type.PaletteChange):
            icons.refresh_for_theme()
            self._retint_icons()
        elif event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            # Returning to this window is when we surface an external change noticed in the
            # background (and re-check, since a watcher event can be missed on some filesystems).
            self._check_external_change()
        super().changeEvent(event)

    def _open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF files (*.pdf)")
        if path:
            self._app.open_document(path)

    def _populate_recent_menu(self) -> None:
        """(Re)build the Open Recent submenu from the shared MRU list."""
        self._recent_menu.clear()
        recents = self._settings.recent_files()
        if not recents:
            empty = self._recent_menu.addAction("(No recent documents)")
            empty.setEnabled(False)
            return
        for i, path in enumerate(recents):
            label = os.path.basename(path)
            text = f"&{i + 1}  {label}" if i < 9 else label  # 1–9 get keyboard accelerators
            action = self._recent_menu.addAction(text)
            action.setToolTip(path)
            action.triggered.connect(lambda checked=False, p=path: self._app.open_document(p))
        self._recent_menu.addSeparator()
        self._recent_menu.addAction("Clear Recent", self._settings.clear_recent)

    def _print(self) -> None:
        from viewer.printing import print_document

        if self.view.form is not None:
            self.view.form.commit_pending()  # print what's on screen, incl. a pending fill
        print_document(self.vdoc, self.view.current_page, self)

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
        self._a_revert.setEnabled(not clean)

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
                # Carry the page's unsaved per-page edits with it (annotations, redactions,
                # rotation) — what's on the page travels to the destination. A carried redaction
                # is the safe default: otherwise a redacted page could be dragged out and saved
                # un-redacted (a leak). Form-field fills are document-level and stay behind.
                refs.append(
                    PageRef(ref.source_id, ref.source_page_index, ref.rotation_override, ref.annotations)
                )
        if refs:
            self.undo_stack.push(InsertCommand(self.vdoc, before_index, refs, text="Drag pages in"))

    def _rotate_pages(self, delta: int) -> None:
        """Rotate the selected pages — or the current page if none are selected — by ``delta``."""
        rows = self.thumbs.selected_rows() or [self.view.current_page]
        rows = [r for r in rows if 0 <= r < self.vdoc.page_count]
        if rows:
            self.undo_stack.push(RotatePagesCommand(self.vdoc, rows, delta))

    def _set_field_value(self, name: str, value) -> None:
        """Fill an AcroForm field (from the inline editor) as an undoable command."""
        self.undo_stack.push(SetFieldValueCommand(self.vdoc, name, value))

    def _arm_tool(self, tool: ArmedTool) -> None:
        """Toolbar: arm a one-shot annotate/redact tool, or disarm it if already armed (toggle)."""
        if self.view.armed is tool:
            self.view.disarm()
        else:
            self.view.arm(tool)
            self._a_select.setChecked(True)  # arming forces the SELECT base mode

    def _on_armed_changed(self, tool) -> None:
        """Light the matching tool button while it's armed (None → all off)."""
        for armed_tool, action in self._armed_actions.items():
            action.setChecked(armed_tool is tool)

    def _apply_text_tool(self, tool) -> None:
        """A drag-over-text armed tool was released on a selection → apply it (one undo)."""
        if tool is ArmedTool.HIGHLIGHT:
            self._highlight_selection()
        elif tool is ArmedTool.REDACT_TEXT:
            self._redact_selection()

    def _add_annotation(self, index: int, annotation) -> None:
        """Add an annotation to a page (from the text-box / redact tools) as an undoable command."""
        self.undo_stack.push(AddAnnotationCommand(self.vdoc, index, annotation))

    def _remove_annotation(self, index: int, annotation) -> None:
        """Remove an annotation (from the right-click menu) as an undoable command."""
        self.undo_stack.push(RemoveAnnotationCommand(self.vdoc, index, annotation))

    def _replace_annotation(self, index: int, old, new, text=None) -> None:
        """Swap an annotation for an updated one (moving / re-editing a text box) — one undo step."""
        self.undo_stack.push(ReplaceAnnotationCommand(self.vdoc, index, old, new, text))

    def _selection_line_bars(self) -> dict[int, list[tuple]]:
        """Group the current text selection's word boxes into one unioned bar **per line, per page**.

        A continuous strip (no inter-word gaps) is the natural look for a highlighter and, for
        redaction, hides word boundaries/lengths (a de-anonymisation leak). Shared by highlight and
        redaction so both behave identically; a mid-paragraph start/end covers exactly the selected
        span across the wrapped lines. Returns ``{page_index: [line_rect, …]}`` (empty if nothing
        selected)."""
        if self.view.selection is None:
            return {}
        bars: dict[tuple, list] = {}
        for page_index, _i, word in self.view.selection.selected_words():
            key = (page_index, word[5], word[6])  # (page, block_no, line_no)
            x0, y0, x1, y1 = word[:4]
            if key in bars:
                b = bars[key]
                b[0], b[1], b[2], b[3] = min(b[0], x0), min(b[1], y0), max(b[2], x1), max(b[3], y1)
            else:
                bars[key] = [x0, y0, x1, y1]
        by_page: dict[int, list[tuple]] = {}
        for (page_index, _b, _l), rect in bars.items():
            by_page.setdefault(page_index, []).append(tuple(rect))
        return by_page

    def _highlight_selection(self) -> None:
        """Highlight the current text selection — one continuous bar per line (per page, one undo)."""
        by_page = self._selection_line_bars()
        if not by_page:
            return
        self.view.selection.clear()  # so the yellow bar shows, not the blue selection over it
        self.undo_stack.beginMacro("Highlight")
        for page_index, rects in by_page.items():
            self.undo_stack.push(AddAnnotationCommand(self.vdoc, page_index, Highlight(tuple(rects))))
        self.undo_stack.endMacro()

    def _redact_selection(self) -> None:
        """Redact the current text selection — one continuous bar per line (per page, one undo)."""
        by_page = self._selection_line_bars()
        if not by_page:
            return
        self.view.selection.clear()
        self.undo_stack.beginMacro("Redact selection")
        for page_index, rects in by_page.items():
            self.undo_stack.push(AddAnnotationCommand(self.vdoc, page_index, Redaction(tuple(rects))))
        self.undo_stack.endMacro()

    def _delete_rows(self, rows) -> None:
        if rows:
            self.undo_stack.push(DeleteCommand(self.vdoc, rows))

    def _copy_pages(self, rows=None) -> None:
        rows = rows if rows else self.thumbs.selected_rows()
        clipboard = []
        for i in rows:
            ref = self.vdoc.ordered[i]
            # Carry the page's per-page edits (rotation + annotations/redactions) on the clipboard
            # so a paste into another document keeps what's on the page.
            clipboard.append((ref.source_id, self.vdoc.sources[ref.source_id],
                              ref.source_page_index, ref.rotation_override, ref.annotations))
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
        for source_id, doc, page_index, rotation, annotations in payloads:
            self.vdoc.register_source(source_id, doc)
            refs.append(PageRef(source_id, page_index, rotation, annotations))
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

    def _on_files_dropped(self, paths, before_index: int) -> None:
        """PDF(s) and/or image(s) dragged from Explorer onto the Pages panel — insert at the drop
        slot. An image (M35) is converted to a one-page PDF source; a PDF opens as-is."""
        refs = []
        for path in paths:
            try:
                if os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS:
                    source_id = self.vdoc.open_image_source(path)  # raster image → 1-page PDF source
                else:
                    source_id = self.vdoc.open_source(path)  # raises on a non-PDF / unreadable file
            except Exception as exc:  # skip the bad file, keep going with the rest
                QMessageBox.warning(self, "Insert file", f"Could not open {os.path.basename(path)}:\n{exc}")
                continue
            doc = self.vdoc.sources[source_id]
            refs.extend(PageRef(source_id, i) for i in range(doc.page_count))
        if refs:
            label = "Insert dropped file" if len(paths) == 1 else f"Insert {len(paths)} dropped files"
            self.undo_stack.push(InsertCommand(self.vdoc, before_index, refs, text=label))

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
        if self.view.form is not None:
            self.view.form.commit_pending()  # flush an open inline field editor (toolbar Save)
        if self.vdoc.path is None:
            return self.save_as()
        if self._watcher.has_changed():  # the file changed on disk since we opened/last synced it
            choice = self._confirm_overwrite_external()
            if choice == "reload":
                self._reload_external()
                return False  # reloaded from disk; the in-place Save is superseded
            if choice != "overwrite":
                return False  # cancelled
        if self._write_to(self.vdoc.path):
            self._watcher.record_current()  # our own write is the new synced state, not a change
            return True
        return False

    def save_as(self) -> bool:
        if self.view.form is not None:
            self.view.form.commit_pending()
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
        self._watcher.watch(path)  # follow the new file; its signature is now the synced state
        self.setWindowTitle(f"{os.path.basename(path)} — pdfproj[*]")
        return True

    def _write_to(self, target_path: str) -> bool:
        """Materialize to a temp file in the same directory, then atomically os.replace it in.

        A save that applies **any** redaction is a *point of no return*: the redaction is destructive
        in the output, but the in-memory sources still hold the original bytes, so removing the
        redaction (right-click Remove, or undo) and re-saving could otherwise resurrect the removed
        content — and a redaction can be removed in any document that holds it, including one that
        received the page by drag/paste. We confirm first, and after writing reload from the clean
        file + clear the undo history, so the secret is gone from disk *and* memory."""
        committing = self.vdoc.has_redactions()
        if committing and not self._confirm_redaction():
            return False
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
        if committing:
            # Drop the un-redacted bytes from memory + clear undo so nothing can be brought back
            # into a leak. Same reload-in-place path as Revert.
            self._reset_to_file(target_path)
        return True

    # ---- export (a derived copy, not the editable document) ---------------------

    def _export_flattened_pdf(self) -> None:
        """Export → Flattened PDF (M31.5): write a locked copy whose annotations + form fields are
        baked into page content (text preserved). A *derived* artifact like Print — it does not
        touch the working document's path, dirty state, undo history, or file watcher; a pending
        redaction is applied in the exported file without committing it in the open document."""
        if self.view.form is not None:
            self.view.form.commit_pending()  # flush an open inline field editor first
        default = self.vdoc.path or ""
        if default:
            base, _ext = os.path.splitext(default)
            default = f"{base} (flattened).pdf"
        path, _ = QFileDialog.getSaveFileName(self, "Export Flattened PDF", default, "PDF files (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        # Write to a temp file in the same directory, then atomically replace — never leave a partial
        # export (and never clobber the target if the bake/save fails).
        directory = os.path.dirname(os.path.abspath(path)) or "."
        fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=directory)
        os.close(fd)
        try:
            from model.export import export_flattened_pdf

            export_flattened_pdf(self.vdoc, tmp)
            os.replace(tmp, path)
        except Exception as exc:  # surface, don't crash; leave any existing target intact
            if os.path.exists(tmp):
                os.remove(tmp)
            QMessageBox.critical(self, "Export failed", str(exc))

    def _export_images(self) -> None:
        """Export → Image (M36): rasterise the selected page(s) — or the current page when nothing
        is selected — to PNG / JPEG at a chosen DPI, one file per page. Edits-aware like the flatten
        export (each image shows the annotations / fills / redactions a Save would write); a side
        artifact that leaves the working document untouched. To export every page, select all in the
        Pages sidebar (Ctrl+A) first."""
        if self.view.form is not None:
            self.view.form.commit_pending()
        indices = self.thumbs.selected_rows() or [self.view.current_page]
        dpi, ok = QInputDialog.getInt(self, "Export Image", "Resolution (DPI):", 150, 36, 600, 1)
        if not ok:
            return
        default = ""
        if self.vdoc.path:
            default = os.path.splitext(self.vdoc.path)[0] + ".png"
        path, selected = QFileDialog.getSaveFileName(
            self, "Export Image", default, "PNG image (*.png);;JPEG image (*.jpg)"
        )
        if not path:
            return
        # Make the extension agree with the chosen filter, so the format is unambiguous.
        jpeg = "jpg" in selected.lower() or path.lower().endswith((".jpg", ".jpeg"))
        if jpeg and not path.lower().endswith((".jpg", ".jpeg")):
            path += ".jpg"
        elif not jpeg and not path.lower().endswith(".png"):
            path += ".png"
        try:
            from model.export import export_page_images

            written = export_page_images(self.vdoc, indices, path, dpi=dpi)
        except Exception as exc:  # surface, don't crash
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        if len(written) > 1:
            QMessageBox.information(
                self, "Export Image", f"Exported {len(written)} pages to image files."
            )

    def _confirm_redaction(self) -> bool:
        return (
            QMessageBox.warning(
                self,
                "Apply redactions?",
                "Saving permanently removes the redacted content and cannot be undone.\n\nContinue?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            == QMessageBox.StandardButton.Save
        )

    def _reset_to_file(self, path: str) -> None:
        """Reload the document from ``path`` in place, dropping all in-memory edits and the undo
        history, then refresh every surface. Shared by the redaction commit (reload from the clean
        output) and Revert (reload from the on-disk original); afterwards the document is clean."""
        self.vdoc.reload_from_file(path)
        self.undo_stack.clear()        # empties history; an empty stack is clean → title * clears
        self.view.selection.clear()
        self.view.search.clear()
        self.view.reload()
        self.thumbs.populate()

    def revert(self) -> None:
        """Discard all in-memory edits and reload the document from its on-disk file (M23).

        Behind a confirm because it throws away unsaved changes and clears the undo history (it
        cannot itself be undone). No-op for an untitled or already-clean document — the menu action
        is disabled in both of those cases."""
        if self.vdoc.path is None or self.undo_stack.isClean():
            return
        if self._confirm_revert():
            self._reset_to_file(self.vdoc.path)

    def _confirm_revert(self) -> bool:
        return (
            QMessageBox.warning(
                self,
                "Revert to saved",
                f"Discard all changes to {os.path.basename(self.path)} and reload the saved "
                f"file?\n\nThis cannot be undone.",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            == QMessageBox.StandardButton.Discard
        )

    # ---- external on-disk change (M24) ------------------------------------------

    def _check_external_change(self) -> None:
        """Watcher signal / window-activation entry point. Prompt only while we are the active
        window — a change noticed in the background waits until the user returns to this window."""
        if self.isActiveWindow():
            self._prompt_external_change()

    def _prompt_external_change(self) -> None:
        if self._external_prompt_open or not self._watcher.has_changed():
            return
        self._external_prompt_open = True  # guard: the watcher and activation can both fire
        try:
            if self._confirm_external_reload():
                self._reload_external()
            else:
                self._watcher.record_current()  # Keep: acknowledge so we stop nagging
        finally:
            self._external_prompt_open = False

    def _reload_external(self) -> None:
        if not os.path.exists(self.path):
            QMessageBox.warning(
                self, "File changed on disk",
                f"{os.path.basename(self.path)} no longer exists on disk. Keeping your version.",
            )
            self._watcher.record_current()  # acknowledge the removal so we stop nagging
            return
        self._reset_to_file(self.path)
        self._watcher.record_current()

    def _confirm_external_reload(self) -> bool:
        """Reload from disk (True) vs Keep my version (False) when the file changed under us."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("File changed on disk")
        text = f"{os.path.basename(self.path)} has been modified by another program."
        if not self.undo_stack.isClean():
            text += "\n\nReloading will discard your unsaved changes."
        box.setText(text + "\n\nReload it from disk?")
        reload_btn = box.addButton("Reload", QMessageBox.ButtonRole.AcceptRole)
        keep_btn = box.addButton("Keep My Version", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(keep_btn if not self.undo_stack.isClean() else reload_btn)
        box.exec()
        return box.clickedButton() is reload_btn

    def _confirm_overwrite_external(self) -> str:
        """Before an in-place Save when the file changed on disk: 'overwrite' / 'reload' / 'cancel'."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("File changed on disk")
        box.setText(
            f"{os.path.basename(self.path)} has been modified by another program since you opened "
            "it.\n\nSaving now will overwrite those changes."
        )
        overwrite_btn = box.addButton("Overwrite", QMessageBox.ButtonRole.DestructiveRole)
        reload_btn = box.addButton("Reload", QMessageBox.ButtonRole.AcceptRole)
        cancel_btn = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is overwrite_btn:
            return "overwrite"
        if clicked is reload_btn:
            return "reload"
        return "cancel"

    # ---- view-state persistence + close prompt ----------------------------------

    @staticmethod
    def _centered_on(avail: QRect, width: int, height: int, margin: int = 40) -> QRect:
        """A window rect of ``width`` × ``height`` clamped to fit ``avail`` (the screen's available
        area, minus ``margin``) and centred within it — so the window never opens larger than the
        screen and always lands centred, not at the OS's default offset."""
        w = max(400, min(width, avail.width() - margin))
        h = max(300, min(height, avail.height() - margin))
        x = avail.x() + (avail.width() - w) // 2
        y = avail.y() + (avail.height() - h) // 2
        return QRect(x, y, w, h)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._initialized:
            return
        self._initialized = True
        # Centre the window on the screen (clamped to fit) rather than leaving it at the OS default
        # offset / a size that can exceed the display.
        screen = self.screen()
        if screen is not None:
            self.setGeometry(self._centered_on(screen.availableGeometry(), self.width(), self.height()))
        # Resume the last page (+ rotation) for this document, but always open at Fit Page so the
        # whole page is visible — the preferred default rather than a remembered zoom.
        self.view.apply_state(self._settings.get_doc_state(self.path))
        self.view.fit_page()

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
        # Persist the per-document page / rotation (to resume on reopen). Window size + position are
        # intentionally not remembered — every launch opens centred at Fit Page (see showEvent).
        self._settings.set_doc_state(self.path, self.view.view_state())
        self.view._drop_render_docs()  # release the fresh-opened render copies' file handles
        self._app.forget_window(self.path)
        super().closeEvent(event)
