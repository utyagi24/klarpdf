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
from PySide6.QtGui import QAction, QActionGroup, QCursor, QGuiApplication, QKeySequence, QUndoStack
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from model.edit_commands import (
    AddAnnotationCommand,
    CropPagesCommand,
    DeleteCommand,
    InsertCommand,
    MovePagesCommand,
    RemoveAnnotationCommand,
    ReplaceAnnotationCommand,
    ResetCropCommand,
    RotatePagesCommand,
    SetEncryptionCommand,
    SetFieldValueCommand,
    SetMetadataCommand,
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
from viewer.search import FindBar, SearchController, SearchResultsPanel
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
        self.view.cropDragged.connect(self._on_crop_dragged)
        self.find_bar = FindBar(self.view)  # hidden until Ctrl+F
        # Doc-wide search hit list (M47): a band under the find bar, hidden until List All.
        self.search_results = SearchResultsPanel(self.view)
        self.find_bar.results_panel = self.search_results

        # Central column: find bar (+ results list) above the view. (A QToolBar host collapses to
        # zero height while the bar is hidden and won't re-expand on show(), so use a real layout.)
        central = QWidget()
        col = QVBoxLayout(central)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self.find_bar)
        col.addWidget(self.search_results)
        col.addWidget(self.view, 1)
        self.setCentralWidget(central)

        self.thumbs = ThumbnailPanel(self.vdoc)
        self.thumbs.source_key = normalize_path(path)  # identity for cross-window drag/drop
        self.outline = None  # OutlinePanel — exists only while the document has an outline (M45)
        self.pages_dock = QDockWidget("Pages", self)
        # Closable (hide/show via View ▸ Sidebar) but NOT floatable or movable — it must stay
        # docked, never tear off into a separate window the user can lose (the inherited M2 bug).
        self.pages_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.pages_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.pages_dock)
        self._mount_sidebar()
        # Hidden by default (a clean, fast, flicker-free open — no thumbnails rendered until the
        # sidebar is shown); the choice is remembered app-wide, so once you open it to organise pages
        # it stays open on the next launch. Toggle via View ▸ Sidebar.
        self.pages_dock.setVisible(bool(self._settings.get_pref("sidebar_visible", False)))

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
        # Right-click menus by hit state (M46) — set after _build_actions: the bare-page menu
        # routes the QActions built there.
        self.view.context_menu_provider = self._view_context_menu
        self.setWindowTitle(f"{os.path.basename(path)} — KlarPDF[*]")
        self.setWindowIcon(icons.app_icon())
        self._place_window()  # final size + position *before* show() → no post-show resize jump

    # ---- sidebar (Pages + optional Outline, M45) --------------------------------

    def _mount_sidebar(self) -> None:
        """(Re)build the dock's contents for the current document: a Pages | Outline tab switcher
        when the document has an outline, else the bare Pages panel — no tab and no tab bar
        (owner rule: a TOC-less document keeps the plain pre-M45 sidebar; inapplicable chrome is
        invisible, not greyed out). Re-run by ``_reset_to_file`` (Revert / redaction commit /
        external reload), where the freshly-read file may have gained or lost its outline."""
        if self.outline is not None:
            self.view.currentPageChanged.disconnect(self.outline.set_current)
            self.outline = None
        old = self.pages_dock.widget()  # None at construction
        if self.vdoc.has_outline():
            from organize.outline_panel import OutlinePanel  # lazy — TOC-less docs never pay it

            self.outline = OutlinePanel(self.vdoc)
            self.outline.entryActivated.connect(self.view.goto_page)
            self.view.currentPageChanged.connect(self.outline.set_current)
            self.outline.set_current(self.view.current_page)
            tabs = QTabWidget()
            tabs.setDocumentMode(True)  # flat sidebar-style tab bar, no page frame
            tabs.addTab(self.thumbs, "Pages")  # reparents thumbs out of any retired container
            tabs.addTab(self.outline, "Outline")
            # The dock's resize bounds come from its content widget, and a QTabWidget does not
            # inherit its children's constraints — without this the sidebar becomes freely
            # resizable the moment the switcher mounts (dead space beside the capped thumbnails).
            # Mirror the Pages panel's bounds: one surface, one set of limits, either document kind.
            tabs.setMinimumWidth(self.thumbs.minimumWidth())
            tabs.setMaximumWidth(self.thumbs.maximumWidth())
            if isinstance(old, QTabWidget):
                tabs.setCurrentIndex(old.currentIndex())  # a reload keeps the active tab
            self.pages_dock.setWindowTitle("Sidebar")  # the tab labels carry the specifics
            self.pages_dock.setWidget(tabs)
        else:
            self.pages_dock.setWindowTitle("Pages")
            self.pages_dock.setWidget(self.thumbs)
        # QDockWidget.setWidget does not show a widget added while the dock is visible (Qt docs) —
        # relevant on a re-mount; at construction the dock isn't visible yet and this is a no-op.
        self.pages_dock.widget().show()
        if old is not None and old is not self.pages_dock.widget() and old is not self.thumbs:
            old.deleteLater()  # retired tab container (thumbs was already reparented out of it)

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
        # Tools — the tranche's one budgeted top-level menu (PLAN.md §GUI feature roadmap, UI
        # budget; owner call during the R1 review): interaction *modes* live here — the cursor
        # changes and a gesture follows — while Edit holds one-shot document operations and View
        # holds what never touches the file. R3's Markup/Draw and R4's Stamp land here too.
        tools_menu = menu.addMenu("&Tools")

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
        # Export → a *derived* copy that never touches the working document. Flatten locks the
        # marks into content; Selected Pages (M51) extracts a page subset through the ordinary
        # materialise path (that one stays editable — a Save-like artifact); Image rasterises.
        export_menu = file_menu.addMenu("&Export")
        act("Flattened PDF…", self._export_flattened_pdf, to_menu=export_menu)
        act("Selected Pages as PDF…", self._export_selected_pages, to_menu=export_menu)
        act("Reduced Size PDF…", self._export_reduced_pdf, to_menu=export_menu)
        act("Image…", self._export_images, to_menu=export_menu)
        # Revert: discard all edits and reload from disk. Enabled only while there are unsaved
        # changes (a clean doc has nothing to revert) — toggled in _on_clean_changed.
        self._a_revert = act("Revert to Saved", self.revert, to_menu=file_menu)
        self._a_revert.setEnabled(False)
        file_menu.addSeparator()
        # Properties (M53): view / edit / remove-all over the document metadata — one dialog,
        # menu + dialog only (one-shot command, stays off the toolbar). Ctrl+D as in Acrobat.
        act("Properties…", self._show_properties, "Ctrl+D", to_menu=file_menu)
        # Password protection (M54): Set / Change / Remove the AES-256 save password. Same
        # placement logic — a document-level dialog beside Properties, never on the toolbar.
        act("Password Protection…", self._password_protection, to_menu=file_menu)
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
        # Rotate sits with the other page operations (owner call, R1 review): it is a real,
        # undoable, *saved* edit on the selected/current page — its old View placement read as a
        # view-only spin that wouldn't touch the file. PdfView.rotate_view remains the view-only one.
        a_rotl = self._a_rotl = act("Rotate Left", lambda: self._rotate_pages(-90), "Ctrl+L", icon="rotate-left", to_menu=edit_menu)
        a_rotr = self._a_rotr = act("Rotate Right", lambda: self._rotate_pages(90), "Ctrl+R", icon="rotate-right", to_menu=edit_menu)
        a_insert = act("Insert Pages from File…", self._insert_from_file, icon="insert", to_menu=edit_menu)
        # M51: a fresh empty page / copies of the selection — both plain PageRef inserts on the
        # undo stack, grouped with the other page operations. Menu-only (one-shot commands stay
        # off the toolbar — PLAN.md §GUI feature roadmap, UI budget).
        act("Insert Blank Page", self._insert_blank_page, to_menu=edit_menu)
        act("Duplicate Pages", lambda: self._duplicate_pages(), to_menu=edit_menu)
        edit_menu.addSeparator()
        self._a_copy_text = act("Copy", self._copy_selection, QKeySequence.StandardKey.Copy, to_menu=edit_menu)
        a_find = act("Find…", self._show_find, QKeySequence.StandardKey.Find, icon="find", to_menu=edit_menu)
        act("Find Next", self.find_bar.find_next, QKeySequence.StandardKey.FindNext, to_menu=edit_menu)
        act("Find Previous", self.find_bar.find_prev, QKeySequence.StandardKey.FindPrevious, to_menu=edit_menu)

        # View
        a_zout = act("Zoom Out", self.view.zoom_out, QKeySequence.StandardKey.ZoomOut, icon="zoom-out", to_menu=view_menu)
        a_zin = act("Zoom In", self.view.zoom_in, QKeySequence.StandardKey.ZoomIn, icon="zoom-in", to_menu=view_menu)
        # Live magnification indicator + preset/typed zoom (1.0 == 100%).
        self.zoom_widget = ZoomWidget(self.view)
        # self._a_* refs: these actions are also routed into the view's context menu (M46) — the
        # same QAction objects, so labels/shortcuts/enabled-state stay single-sourced.
        self._a_actual = act("Actual Size", self.view.actual_size, "Ctrl+0", to_menu=view_menu)  # reset to 100%
        a_fitw = self._a_fitw = act("Fit Width", self.view.fit_width, "Ctrl+1", icon="fit-width", to_menu=view_menu)
        a_fitp = self._a_fitp = act("Fit Page", self.view.fit_page, "Ctrl+2", icon="fit-page", to_menu=view_menu)
        view_menu.addSeparator()
        # Jump to an absolute page (M45). Menu + dialog only — one-shot commands stay off the
        # toolbar (PLAN.md §GUI feature roadmap, UI budget).
        self._a_goto = act("Go to &Page…", self._goto_page_dialog, "Ctrl+G", to_menu=view_menu)
        view_menu.addSeparator()

        # Tools — persistent interaction mode first: Select (default — text/forms/move) vs Grab
        # (hand-pan). Mutually exclusive; the checked toolbar button shows the active tool.
        mode_group = QActionGroup(self)
        mode_group.setExclusive(True)
        a_select = act("Select", lambda: self.view.set_mode(InteractionMode.SELECT), icon="select", to_menu=tools_menu)
        a_grab = act("Grab", lambda: self.view.set_mode(InteractionMode.GRAB), icon="grab", to_menu=tools_menu)
        for a in (a_select, a_grab):
            a.setCheckable(True)
            mode_group.addAction(a)
        a_select.setChecked(True)
        self._a_select = a_select
        tools_menu.addSeparator()
        # One-shot armed annotate/redact tools: click to arm (button lights), do one gesture, then
        # it reverts to Select. Checkable only to reflect the armed state — NOT in the mode group.
        # All four are consistent — arm, then a single gesture: TextBox = click; Highlight /
        # Redact Text = drag over text; Redact Block = drag a rectangle.
        a_textbox = act("Add Text Box", lambda: self._arm_tool(ArmedTool.TEXTBOX), icon="textbox", to_menu=tools_menu)
        a_textbox.setToolTip("Add Text Box — click a spot, then type (drag to move, double-click to edit)")
        a_highlight = act("Highlight", lambda: self._arm_tool(ArmedTool.HIGHLIGHT), "Ctrl+H", icon="highlight", to_menu=tools_menu)
        a_highlight.setToolTip("Highlight — drag over text to highlight it")
        a_redact_text = act("Redact Text", lambda: self._arm_tool(ArmedTool.REDACT_TEXT), "Ctrl+Shift+R", icon="redact-text", to_menu=tools_menu)
        a_redact_text.setToolTip("Redact Text — drag over text to permanently remove it at save")
        a_redact_block = act("Redact Block", lambda: self._arm_tool(ArmedTool.REDACT_REGION), icon="redact", to_menu=tools_menu)
        a_redact_block.setToolTip("Redact Block — drag a box to permanently remove its contents at save")
        tools_menu.addSeparator()
        # Crop (M48): menu-only (no free toolbar slot needed for a one-shot); same armed pattern.
        a_crop = act("Crop Pages", lambda: self._arm_tool(ArmedTool.CROP), to_menu=tools_menu)
        a_crop.setToolTip("Crop Pages — drag the area to keep; the rest is hidden, not removed")
        act("Remove Crop", self._remove_crop, to_menu=tools_menu)
        self._armed_actions = {
            ArmedTool.TEXTBOX: a_textbox,
            ArmedTool.HIGHLIGHT: a_highlight,
            ArmedTool.REDACT_TEXT: a_redact_text,
            ArmedTool.REDACT_REGION: a_redact_block,
            ArmedTool.CROP: a_crop,
        }
        for a in self._armed_actions.values():
            a.setCheckable(True)
        # Night reading mode (M49): view-only page inversion, independent of the OS theme the
        # chrome follows. Remembered app-wide, like the sidebar choice; the file, print, export,
        # and thumbnails stay daylight — only what the eyes read at night inverts.
        self._a_night = act("Night Reading Mode", self._toggle_night_mode, to_menu=view_menu)
        self._a_night.setCheckable(True)
        if bool(self._settings.get_pref("night_mode", False)):
            self._a_night.setChecked(True)
            self.view.set_night_mode(True)  # pre-show: nothing rendered yet, so no flash
        view_menu.addSeparator()
        # Checkable show/hide for the sidebar — menu item + a dedicated toolbar button (its
        # checked state mirrors the panel's visibility, with the :checked toolbar styling).
        # "Sidebar", not "Pages Sidebar": since M45 the dock can also hold the Outline tab, and one
        # stable label that is right for both document kinds beats a per-document rename.
        pages_toggle = self.pages_dock.toggleViewAction()
        pages_toggle.setText("&Sidebar")
        pages_toggle.setIcon(icons.icon("sidebar"))
        pages_toggle.setToolTip("Show/Hide the sidebar")
        pages_toggle.setProperty("iconName", "sidebar")  # re-tinted on theme change
        # Remember the user's show/hide choice app-wide (triggered fires only on an explicit toggle,
        # not the programmatic setVisible above), so it persists to the next launch / new windows.
        pages_toggle.triggered.connect(
            lambda checked: self._settings.set_pref("sidebar_visible", checked)
        )
        view_menu.addAction(pages_toggle)

        # Help — an AGPL release owes the user the licence and the corresponding source (G4).
        # Deliberately not on the toolbar: discoverable where users look for it, not in the way.
        help_menu = menu.addMenu("&Help")
        act("About KlarPDF", self._show_about, icon="about", to_menu=help_menu)
        act("Open-Source Licenses", self._show_licenses, to_menu=help_menu)
        help_menu.addSeparator()
        act("View Source", self._open_source_url, to_menu=help_menu)
        # Donate (G6) — last, and deliberately in the same group as View Source: the separator splits
        # "opens a dialog" from "hands a URL to your browser", which is the distinction that actually
        # matters to the user. Menu-only, like the rest of Help — asking for money should be findable,
        # never in the way of the work. Voluntary: no feature is gated on it.
        act("Donate…", self._open_donate_url, icon="donate", to_menu=help_menu)

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

    # --- Help (G4) -------------------------------------------------------------------------
    # Imported lazily: the About/Licenses dialogs read the bundled licence texts off disk, and no
    # window should pay that on open. Mirrors the lazy `viewer.printing` import below.

    def _show_about(self) -> None:
        from ui.about import AboutDialog

        AboutDialog(self).exec()

    def _show_licenses(self) -> None:
        from ui.about import LicensesDialog

        LicensesDialog(self).exec()

    def _open_source_url(self) -> None:
        """Open the repo in the system browser. User-initiated; the app opens no socket itself."""
        from ui.about import SOURCE_URL, _open_url

        _open_url(SOURCE_URL)

    def _open_donate_url(self) -> None:
        """Open the sponsors page in the system browser (G6). Same policy as View Source: the browser
        is handed a URL only because the user clicked, so the offline / no-telemetry guarantee holds."""
        from ui.about import DONATE_URL, _open_url

        _open_url(DONATE_URL)

    def _print(self) -> None:
        from viewer.printing import print_document

        if self.view.form is not None:
            self.view.form.commit_pending()  # print what's on screen, incl. a pending fill
        print_document(self.vdoc, self.view.current_page, self)

    def _copy_selection(self) -> None:
        self.view.selection.copy()

    def _show_find(self) -> None:
        self.find_bar.show_bar()

    def _toggle_night_mode(self, checked: bool) -> None:
        """View ▸ Night Reading Mode (M49): invert the page pixels, view-only; remembered."""
        self.view.set_night_mode(checked)
        self._settings.set_pref("night_mode", checked)

    def _show_properties(self) -> None:
        """File ▸ Properties… (M53): one dialog, three verbs — view (fields + provenance + file
        facts), edit (title/author/subject/keywords), remove all. The dialog stages; anything it
        staged becomes one undoable command here, so a metadata edit is an ordinary document edit
        (dirty flag, undo/redo, written to **both stores** at the next save)."""
        from ui.properties_dialog import PropertiesDialog

        dialog = PropertiesDialog(self.vdoc, self)
        if not dialog.exec():
            return
        override = dialog.staged_override()
        if override is not None:
            self.undo_stack.push(SetMetadataCommand(self.vdoc, override))

    def _password_protection(self) -> None:
        """File ▸ Password Protection… (M54): one save-path capability, four verbs — Set / Change
        / Remove the AES-256 password (+ advisory restriction flags), then carry-through on every
        following save. The dialog owns the type-twice + unrecoverable warning and requires the
        current password for Change/Remove; whatever it stages becomes one undoable command (the
        password is only ever written into the encrypted output itself)."""
        from ui.encrypt_dialog import PasswordDialog

        dialog = PasswordDialog(self.vdoc, self)
        if not dialog.exec():
            return
        staged = dialog.staged()
        if staged is not None:
            password, permissions = staged
            self.undo_stack.push(SetEncryptionCommand(self.vdoc, password, permissions))

    def _goto_page_dialog(self) -> None:
        """View ▸ Go to Page… (Ctrl+G, M45): jump straight to an absolute page number."""
        count = self.vdoc.page_count
        page, ok = QInputDialog.getInt(
            self, "Go to Page", f"Page (1–{count}):", self.view.current_page + 1, 1, count, 1
        )
        if ok:
            self.view.goto_page(page - 1)

    # ---- page edits (all via the undo stack) ------------------------------------

    def _on_doc_changed(self, _index: int) -> None:
        # A structural edit invalidates page indices, so drop stale overlays and rebuild.
        self.view.selection.clear()
        self.view.search.clear()
        if self.search_results.isVisible():
            self.search_results.refresh()  # the hits died with the edit — no stale rows
        self.view.reload()
        self.thumbs.populate()
        if self.outline is not None:
            self.outline.populate()  # live remapped_toc: the tree shows what a Save would write

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
                # rotation, crop) — what's on the page travels to the destination. A carried
                # redaction is the safe default: otherwise a redacted page could be dragged out and
                # saved un-redacted (a leak). Form-field fills are document-level and stay behind.
                refs.append(
                    PageRef(ref.source_id, ref.source_page_index, ref.rotation_override,
                            ref.annotations, ref.crop_override)
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
        """Toolbar: arm a one-shot annotate/redact tool, or disarm it if already armed (toggle).

        A drag-over-text tool clicked while a text selection is **live** applies to that selection
        immediately instead of arming (Preview-style, and identical to the context menu's
        Highlight/Redact Selection) — otherwise "select, then click Highlight" would leave the
        selection untouched and silently wait for a second drag, while the context menu acted at
        once (owner call on the M46 review). With no selection the arm-then-drag flow is unchanged.
        """
        if self.view.armed is tool:
            self.view.disarm()
            return
        if tool.drags_text and self.view.selection is not None and self.view.selection.selected_words():
            self._apply_text_tool(tool)  # clears the selection as it applies — one undo step
            return
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

    def _on_crop_dragged(self, page_index: int, rect: tuple) -> None:
        """An armed-CROP drag finished (M48): ask the scope, then crop as one undo step."""
        indices = self._ask_crop_scope(page_index)
        if indices:
            self.undo_stack.push(CropPagesCommand(self.vdoc, indices, rect))

    def _ask_crop_scope(self, page_index: int) -> list[int]:
        """Which pages the dragged crop applies to — this page / the sidebar selection / all —
        with the honesty wording (crop hides; Redact removes). Returns ``[]`` on cancel. A
        separate seam so tests drive the choice without the modal."""
        selection = self.thumbs.selected_rows()
        others = [r for r in selection if r != page_index]
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Crop pages")
        box.setText(
            "Crop to the dragged area?\n\nEverything outside it is hidden, not removed — use "
            "Redact to remove content permanently. Remove Crop restores the full page any time."
        )
        this_btn = box.addButton("This Page", QMessageBox.ButtonRole.AcceptRole)
        sel_btn = (
            box.addButton(f"Selected Pages ({len(others) + 1})", QMessageBox.ButtonRole.AcceptRole)
            if others
            else None
        )
        all_btn = box.addButton("All Pages", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(this_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is this_btn:
            return [page_index]
        if sel_btn is not None and clicked is sel_btn:
            return sorted({page_index, *selection})
        if clicked is all_btn:
            return list(range(self.vdoc.page_count))
        return []

    def _remove_crop(self) -> None:
        """View ▸ Remove Crop: restore the selected pages — or the current page — to the full
        MediaBox (also un-hides a crop the file arrived with). No-op on uncropped pages."""
        rows = self.thumbs.selected_rows() or [self.view.current_page]
        rows = [r for r in rows if 0 <= r < self.vdoc.page_count and self.vdoc.page_is_cropped(r)]
        if rows:
            self.undo_stack.push(ResetCropCommand(self.vdoc, rows))

    def _delete_rows(self, rows) -> None:
        if rows:
            self.undo_stack.push(DeleteCommand(self.vdoc, rows))

    def _copy_pages(self, rows=None) -> None:
        rows = rows if rows else self.thumbs.selected_rows()
        clipboard = []
        for i in rows:
            ref = self.vdoc.ordered[i]
            # Carry the page's per-page edits (rotation + annotations/redactions + crop) on the
            # clipboard so a paste into another document keeps what's on the page.
            clipboard.append((ref.source_id, self.vdoc.sources[ref.source_id],
                              ref.source_page_index, ref.rotation_override, ref.annotations,
                              ref.crop_override))
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
        for source_id, doc, page_index, rotation, annotations, crop in payloads:
            self.vdoc.register_source(source_id, doc)
            refs.append(PageRef(source_id, page_index, rotation, annotations, crop))
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

    def _insert_blank_page(self) -> None:
        """Edit ▸ Insert Blank Page (M51): one empty page after the selection — or after the
        current page — sized to match the page it follows (its visible, rotated frame), so a
        landscape scan gets a landscape blank. Undoable like any insert."""
        rows = self.thumbs.selected_rows()
        at = rows[-1] + 1 if rows else min(self.view.current_page + 1, self.vdoc.page_count)
        if self.vdoc.page_count:
            width, height = self.vdoc.page_visible_size(max(0, at - 1))
        else:
            width, height = 612.0, 792.0  # empty document — US Letter
        source_id = self.vdoc.open_blank_source(width, height)
        self.undo_stack.push(
            InsertCommand(self.vdoc, at, [PageRef(source_id, 0)], text="Insert blank page")
        )

    def _duplicate_pages(self, rows=None) -> None:
        """Edit ▸ Duplicate Pages (M51): copies of the selected pages — or the current page —
        spliced in right after the last of them. The copies are the same frozen PageRefs, so they
        carry the pages' per-page edits (rotation / crop / annotations); the object-level copy
        happens at materialise, like every insert. One undo step."""
        rows = rows if rows else (self.thumbs.selected_rows() or [self.view.current_page])
        rows = sorted({r for r in rows if 0 <= r < self.vdoc.page_count})
        if not rows:
            return
        refs = [self.vdoc.ordered[i] for i in rows]
        label = "Duplicate page" if len(refs) == 1 else f"Duplicate {len(refs)} pages"
        self.undo_stack.push(InsertCommand(self.vdoc, rows[-1] + 1, refs, text=label))

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

    def _view_context_menu(self, scene_pt) -> "QMenu | None":
        """Build the view's right-click menu from the hit state under the cursor (M46): the verbs
        for our annotation / the live text selection / the link at ``scene_pt``, else the bare-page
        navigation set. Exec'd by ``PdfView.contextMenuEvent``; returned unexec'd so tests can
        assert contents. Later milestones hang their situational verbs here (paste object M59,
        extract M51)."""
        menu = QMenu(self)
        # Our annotation under the cursor → Remove (pre-M46 behaviour, now routed here). First:
        # the most specific hit wins, and Remove must stay reachable over a selection.
        hit = self.view.annotations.annotation_at(scene_pt) if self.view.annotations else None
        if hit is not None:
            page_index, annot = hit
            label = {
                "Highlight": "Remove highlight",
                "TextBox": "Remove text box",
                "Redaction": "Remove redaction",
            }.get(type(annot).__name__, "Remove annotation")
            menu.addAction(label, lambda: self.view.annotations.remove(page_index, annot))
            return menu
        # A live text selection → its verbs. Highlight/Redact Selection apply now to what is
        # selected — unlike the toolbar's armed one-shot tools, which select-then-apply.
        if self.view.selection is not None and self.view.selection.selected_words():
            menu.addAction(self._a_copy_text)
            menu.addSeparator()
            menu.addAction("Highlight Selection", self._highlight_selection)
            menu.addAction("Redact Selection", self._redact_selection)
            return menu
        if self.view.links is not None:
            dest = self.view.links.link_at(scene_pt)
            if dest is not None:
                menu.addAction(f"Go to Page {dest + 1}", lambda: self.view.goto_page(dest))
                return menu
            uri = self.view.links.uri_at(scene_pt)
            if uri is not None:
                # External links are never click-navigable (offline app) — copy is the one verb.
                menu.addAction("Copy Link Address",
                               lambda: QGuiApplication.clipboard().setText(uri))
                return menu
        # Bare page (or the gap between pages — these verbs are view-level, so no dead zone):
        # routes the same QAction objects as the View menu, so shortcuts show alongside.
        for action in (self._a_fitw, self._a_fitp, self._a_actual):
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction(self._a_rotl)
        menu.addAction(self._a_rotr)
        menu.addSeparator()
        menu.addAction(self._a_goto)
        return menu

    def _build_page_context_menu(self, rows) -> QMenu:
        """The Pages-sidebar right-click menu for the selected ``rows`` (M46; M51 adds Duplicate +
        the extract). Built unexec'd so tests can assert contents and trigger actions."""
        menu = QMenu(self)
        a_cut = menu.addAction("Cut", lambda: self._cut_pages(rows))
        a_copy = menu.addAction("Copy", lambda: self._copy_pages(rows))
        a_paste = menu.addAction("Paste", lambda: self._paste_pages(rows[-1] + 1 if rows else None))
        a_delete = menu.addAction("Delete", lambda: self._delete_rows(rows))
        a_dup = menu.addAction("Duplicate", lambda: self._duplicate_pages(rows))
        menu.addSeparator()
        # Same undoable per-page rotation as View ▸ Rotate; rows == the sidebar selection, which
        # is exactly what _rotate_pages acts on.
        a_rotl = menu.addAction("Rotate Left", lambda: self._rotate_pages(-90))
        a_rotr = menu.addAction("Rotate Right", lambda: self._rotate_pages(90))
        menu.addSeparator()
        menu.addAction("Insert Pages from File…", self._insert_from_file)
        menu.addAction("Insert Blank Page", self._insert_blank_page)
        # The selection-scoped extract (M51) — same handler as File ▸ Export ▸ Selected Pages
        # (it reads the sidebar selection, which is exactly these rows).
        a_extract = menu.addAction("Export as PDF…", self._export_selected_pages)
        for a in (a_cut, a_copy, a_delete, a_dup, a_rotl, a_rotr, a_extract):
            a.setEnabled(bool(rows))
        a_paste.setEnabled(bool(self._app.page_clipboard))
        return menu

    def _page_context_menu(self, pos) -> None:
        self._build_page_context_menu(self.thumbs.selected_rows()).exec(self.thumbs.mapToGlobal(pos))

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
        self.setWindowTitle(f"{os.path.basename(path)} — KlarPDF[*]")
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

    def _export_pdf(self, title: str, default_tag: str, write, confirm=None):
        """Shared plumbing for the PDF-writing exports: flush a pending inline form edit, ask for
        the target (default name = the document's, tagged ``default_tag``), then ``write(vdoc,
        tmp_path)`` to a same-directory temp file and atomically replace — never a partial export,
        and the target is left intact if the write fails. Every export is a *derived* artifact
        like Print: the working document's path, dirty state, undo history, and file watcher are
        untouched.

        ``confirm(path)``, when given, runs after the target is chosen and vetoes the export by
        returning False (the reduced export's overwrite-the-original guard). Returns ``(path,
        write's result)`` on success, else ``None`` — so a caller can report on the written file."""
        if self.view.form is not None:
            self.view.form.commit_pending()  # flush an open inline field editor first
        default = self.vdoc.path or ""
        if default:
            base, _ext = os.path.splitext(default)
            default = f"{base} ({default_tag}).pdf"
        path, _ = QFileDialog.getSaveFileName(self, title, default, "PDF files (*.pdf)")
        if not path:
            return None
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        if confirm is not None and not confirm(path):
            return None
        directory = os.path.dirname(os.path.abspath(path)) or "."
        fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=directory)
        os.close(fd)
        try:
            result = write(self.vdoc, tmp)
            os.replace(tmp, path)
        except Exception as exc:  # surface, don't crash; leave any existing target intact
            if os.path.exists(tmp):
                os.remove(tmp)
            QMessageBox.critical(self, "Export failed", str(exc))
            return None
        return path, result

    def _export_flattened_pdf(self) -> None:
        """Export → Flattened PDF (M31.5): write a locked copy whose annotations + form fields are
        baked into page content (text preserved); a pending redaction is applied in the exported
        file without committing it in the open document."""
        from model.export import export_flattened_pdf

        self._export_pdf("Export Flattened PDF", "flattened", export_flattened_pdf)

    def _export_selected_pages(self) -> None:
        """Export → Selected Pages as PDF… (M51): the selected pages — or the current page when
        nothing is selected — extracted object-level into a new, still-editable PDF (text layer /
        forms / our annotations carried; origin bookmarks + internal links remapped)."""
        from model.export import export_selected_pages

        indices = self.thumbs.selected_rows() or [self.view.current_page]
        self._export_pdf(
            "Export Selected Pages",
            "extracted",
            lambda vdoc, tmp: export_selected_pages(vdoc, indices, tmp),
        )

    def _export_reduced_pdf(self) -> None:
        """Export → Reduced Size PDF… (M52): the lossy tier — images downsampled + re-encoded
        JPEG at the chosen preset/custom dpi & quality, fonts subset — reporting the **actual**
        before → after sizes. Overwriting the original goes through the guard below with
        permanent-quality-loss wording; by default the original is untouched."""
        from model.export import export_reduced_pdf
        from ui.reduce_dialog import ReduceSizeDialog, human_size

        dialog = ReduceSizeDialog(self)
        if not dialog.exec():
            return
        dpi, quality = dialog.chosen()
        outcome = self._export_pdf(
            "Export Reduced Size PDF",
            "reduced",
            lambda vdoc, tmp: export_reduced_pdf(vdoc, tmp, dpi, quality),
            confirm=self._confirm_reduce_overwrite,
        )
        if outcome is None:
            return
        _path, (before, after) = outcome
        if after < before:
            percent = round(100 * (before - after) / before)
            message = f"Reduced from {human_size(before)} to {human_size(after)} (−{percent}%)."
        else:  # honesty over cheerleading: recompression can lose nothing worth having
            message = (
                f"The reduced copy is {human_size(after)} — no smaller than a plain save "
                f"({human_size(before)}). This document has little image data to recompress."
            )
        QMessageBox.information(self, "Reduced Size PDF", message)

    def _confirm_reduce_overwrite(self, path: str) -> bool:
        """The reduced export chose the **original file** as its target: confirm with the
        permanent-quality-loss wording (any other target needs no guard — the original is
        untouched by default)."""
        if self.vdoc.path is None or normalize_path(path) != normalize_path(self.vdoc.path):
            return True
        return (
            QMessageBox.warning(
                self,
                "Replace the original?",
                "This will overwrite the original file with the reduced copy. The removed image "
                "detail is permanently lost — the full-quality original will be gone.\n\n"
                "Replace it?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            == QMessageBox.StandardButton.Save
        )

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
        # After a reload-in-place, memory matches disk by definition — resync the watcher here, in
        # the one shared place. Revert previously skipped this: reverting while the file had also
        # changed on disk loaded the current bytes yet kept nagging "changed on disk".
        self._watcher.record_current()
        self.view.selection.clear()
        self.view.search.clear()
        if self.search_results.isVisible():
            self.search_results.refresh()  # cleared with the search — no stale rows
        self.view.reload()
        self.thumbs.populate()
        self._mount_sidebar()  # the freshly-read file may have gained or lost its outline

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
        """Watcher signal / window-activation entry point. Prompt only while we are the visible,
        active window — a change noticed in the background waits until the user returns to this
        window. The visibility check matters: ``close()`` hides a window without destroying it, and
        a lingering closed window must never raise this prompt (nobody can "return" to it — and
        offscreen, a stray activation event on one turned the modal into a test-suite deadlock)."""
        if self.isVisible() and self.isActiveWindow():
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
        self._reset_to_file(self.path)  # resyncs the watcher too

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
    def _open_geometry(avail: QRect, width: int, frame_w: int, frame_h: int, title_bar: int) -> QRect:
        """Window content rect: the **full available height** of the screen, ``width`` clamped to the
        screen, **centred horizontally**, anchored so the window *frame* fills the height and stays
        on-screen. ``frame_w`` / ``frame_h`` / ``title_bar`` are the window-decoration sizes — the
        content is shortened by the frame and dropped by the title-bar height, so the title bar sits
        at the top of the available area and the bottom border at the bottom."""
        w = max(400, min(width, avail.width() - frame_w))
        h = max(300, avail.height() - frame_h)
        x = avail.x() + (avail.width() - w) // 2  # symmetric side borders → centring content centres the frame
        y = avail.y() + title_bar
        return QRect(x, y, w, h)

    def _place_window(self) -> None:
        """Size + position the window at full screen height, default width, centred horizontally —
        done **before** the window is shown, so it maps directly at its final geometry (no post-show
        resize jump / flicker). The native frame isn't realised yet, so use a typical title-bar /
        border allowance for the decoration sizes.

        Open on the screen **under the cursor** — where the user just double-clicked in Explorer, or
        is working — so a launch from a second monitor opens there, not on the primary. (Before the
        window is shown ``self.screen()`` only ever reports the primary screen, which is why the
        placement was being forced onto it.)"""
        screen = (
            QGuiApplication.screenAt(QCursor.pos())
            or self.screen()
            or QGuiApplication.primaryScreen()
        )
        if screen is None:
            self.resize(1000, 800)
            return
        self.setGeometry(self._open_geometry(screen.availableGeometry(), 1000, 16, 39, 31))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._initialized:
            return
        self._initialized = True
        # The window is already at its final geometry (set pre-show); now do the first render once,
        # at Fit Page, resuming the remembered page/rotation. No fit/resize happens after the first
        # paint, so there's no flicker.
        self.view.open_at(self._settings.get_doc_state(self.path))

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
        self.thumbs._close_baked()     # release the thumbnails' kept-open edits render, if any
        self._app.forget_window(self.path)
        super().closeEvent(event)
