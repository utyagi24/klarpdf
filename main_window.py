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

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCursor, QGuiApplication, QKeySequence, QUndoStack
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QDialog,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QTabWidget,
    QToolButton,
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
    SetAnnotationsCommand,
    SetEncryptionCommand,
    SetFieldValueCommand,
    SetMetadataCommand,
)
from model.page_edits import (
    Highlight,
    Redaction,
    Strikeout,
    TextBox,
    Underline,
    mark_bounds,
    merge_markup,
    reorder_marks,
    translate_mark,
)
from model.edit_engine import PyMuPDFEngine
from model.virtual_document import IMAGE_EXTENSIONS, PageRef, VirtualDocument
from organize.thumbnail_panel import ThumbnailPanel
from store.file_watch import FileWatcher
from store.settings import Settings
from ui import icons
from util.paths import normalize_path
from model.content_marks import ImageStamp, is_content_mark
from model.form_fields import FIELD_KINDS, kind_label
from viewer.annotations import OBJECT_TYPES, AnnotationOverlay, mark_noun
from viewer.form_fill import FormFiller
from viewer.markup_style import (
    HIGHLIGHT_COLORS,
    TEXT_LINE_COLORS,
    MarkupStyle,
    MarkupStyleButton,
    swatch_icon,
)
from viewer.links import LinkNavigator
from viewer.pdf_view import PdfView
from viewer.search import FindBar, SearchController, SearchResultsPanel
from viewer.text_selection import TextSelection
from viewer.tools import ArmedTool, InteractionMode
from viewer.zoom_widget import ZoomWidget

# Preference key for the sticky mark style. Style only — never the page range, whose stale value
# would silently re-scope the next mark to a whole document (see ui.mark_dialog).
_MARK_STYLE_PREF = "mark_style"


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
        self._edited_page: int | None = None  # the page the in-flight edit lands on (M59.9)
        # Text-markup colours (M59.9), curated + separate from the pen/shapes stroke picker:
        # highlight keeps its own (a translucent wash), underline + strikeout share one (an opaque
        # proofing line). Sticky for the session, like the other last-used styles.
        self._highlight_color = HIGHLIGHT_COLORS[0][1]
        self._markup_line_color = TEXT_LINE_COLORS[0][1]

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
            self.view, self._add_annotation, self._remove_annotation, self._replace_annotation,
            self._on_object_selected, self._replace_annotations_batch, self._remove_annotations_batch,
        )
        # PdfView built its scene in __init__ (before this overlay existed), and the first show's
        # fit/restore can early-return without a rebuild — so paint any annotations the document was
        # opened with (round-tripped from a prior save, M31) now that the overlay is wired.
        self.view.annotations.repaint()
        self.view.armedChanged.connect(self._on_armed_changed)
        self.view.applyTextTool.connect(self._apply_text_tool)
        self.view.cropDragged.connect(self._on_crop_dragged)
        self.view.foreignMoved.connect(self._move_foreign_annotation)
        self.view.foreignAdopt.connect(self._adopt_foreign_annotation)
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
        # The dropdown arrow on a menu-carrying button (M59.13). Qt places it differently per popup
        # mode: **MenuButtonPopup** (the Markup ▾ / Draw ▾ split buttons) draws a *raised sub-panel*
        # on the right with the arrow centred in it, which crowds the icon; **InstantPopup** (the
        # pen-&-shapes style swatch) tucks a small indicator into the **bottom-right corner**, over
        # the swatch. Same toolbar, two different arrow positions — and both cramped. These rules
        # give every menu button the same treatment: reserve room on the right (so the arrow never
        # touches the icon), drop the sub-panel frame, and pin the arrow to the **vertical centre**
        # whichever mode drew it. popupMode 1 = MenuButtonPopup, 2 = InstantPopup.
        bar.setStyleSheet(
            "QToolBar { spacing: 1px; padding: 2px; }"
            "QToolButton { border: 1px solid transparent; border-radius: 4px; padding: 3px; }"
            "QToolButton:hover { background-color: rgba(128, 128, 128, 46); }"
            "QToolButton:pressed { background-color: rgba(128, 128, 128, 100); }"
            "QToolButton:checked { background-color: rgba(128, 128, 128, 72); }"
            'QToolButton[popupMode="1"], QToolButton[popupMode="2"] { padding-right: 16px; }'
            "QToolButton::menu-button { border: none; background: transparent; width: 14px; }"
            "QToolButton::menu-arrow, QToolButton::menu-indicator {"
            " subcontrol-origin: padding; subcontrol-position: center right; right: 4px; }"
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
        # Focus-routed clipboard (M59): one Ctrl+C/X/V dispatcher decides text vs pages vs object
        # by focus + state (see _edit_copy/_edit_cut/_edit_paste). The menu's Copy is the router
        # too, so the shortcut shown beside it is the one that actually fires. Cut/Paste have no
        # menu row of their own (pages keep their explicit entries; object cut/paste live on the
        # context menus) — window-level actions carry their shortcuts.
        self._a_copy_text = act("Copy", self._edit_copy, QKeySequence.StandardKey.Copy, to_menu=edit_menu)
        a_cut_sc = act("Cut", self._edit_cut, QKeySequence.StandardKey.Cut)
        a_paste_sc = act("Paste", self._edit_paste, QKeySequence.StandardKey.Paste)
        self.addAction(a_cut_sc)   # not in a menu — added to the window so the shortcut is live
        self.addAction(a_paste_sc)
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
        # Objects mode (M59.6): drag a box to select drawn marks, Ctrl+click to add/remove one, drag
        # a member to move the whole group — a colour/width/fill change then restyles the group.
        a_objects = act("Objects", lambda: self.view.set_mode(InteractionMode.OBJECT), icon="objects", to_menu=tools_menu)
        a_objects.setToolTip("Objects — drag a box to select drawn marks; Ctrl-click adds/removes; "
                             "drag to move the group; the style picker restyles it")
        for a in (a_select, a_grab, a_objects):
            a.setCheckable(True)
            mode_group.addAction(a)
        a_select.setChecked(True)
        self._a_select = a_select
        self._a_objects = a_objects
        tools_menu.addSeparator()
        # One-shot armed annotate/redact tools: click to arm (button lights), do one gesture, then
        # it reverts to Select. Checkable only to reflect the armed state — NOT in the mode group.
        # All four are consistent — arm, then a single gesture: TextBox = click; Highlight /
        # Redact Text = drag over text; Redact Block = drag a rectangle.
        a_textbox = act("Add Text Box", lambda: self._arm_tool(ArmedTool.TEXTBOX), icon="textbox", to_menu=tools_menu)
        a_textbox.setToolTip("Add Text Box — click a spot, then type (drag to move, double-click to edit)")
        a_highlight = act("Highlight", lambda: self._arm_tool(ArmedTool.HIGHLIGHT), "Ctrl+H", icon="highlight", to_menu=tools_menu)
        a_highlight.setToolTip("Highlight — drag over text to highlight it")
        # Underline / strikeout (M56): the same drag-over-text gesture and line-bar path as
        # Highlight; the three markup verbs share one toolbar slot (the Markup ▾ split-button).
        a_underline = act("Underline", lambda: self._arm_tool(ArmedTool.UNDERLINE), "Ctrl+U", icon="underline", to_menu=tools_menu)
        a_underline.setToolTip("Underline — drag over text to underline it")
        a_strikeout = act("Strike Out", lambda: self._arm_tool(ArmedTool.STRIKEOUT), icon="strikeout", to_menu=tools_menu)
        a_strikeout.setToolTip("Strike Out — drag over text to strike it through")
        # Draw tools (M58): pen path capture + line/arrow/rect/ellipse press-drag-release, all
        # one-shot armed like the rest (Shift constrains: square / circle / 45° line). Fixed-width
        # red ink — markup/redlining framing, not CAD (PLAN.md M58).
        a_pen = act("Pen", lambda: self._arm_tool(ArmedTool.PEN), icon="pen", to_menu=tools_menu)
        a_pen.setToolTip("Pen — draw a freehand stroke (fixed width)")
        a_line = act("Line", lambda: self._arm_tool(ArmedTool.LINE), icon="line", to_menu=tools_menu)
        a_line.setToolTip("Line — drag from start to end (Shift snaps to 45°)")
        a_arrow = act("Arrow", lambda: self._arm_tool(ArmedTool.ARROW), icon="arrow", to_menu=tools_menu)
        a_arrow.setToolTip("Arrow — drag from tail to head (Shift snaps to 45°)")
        a_rect = act("Rectangle", lambda: self._arm_tool(ArmedTool.RECT), icon="rect", to_menu=tools_menu)
        a_rect.setToolTip("Rectangle — drag a box (Shift for a square)")
        a_ellipse = act("Ellipse", lambda: self._arm_tool(ArmedTool.ELLIPSE), icon="ellipse", to_menu=tools_menu)
        a_ellipse.setToolTip("Ellipse — drag a box (Shift for a circle)")
        a_redact_text = act("Redact Text", lambda: self._arm_tool(ArmedTool.REDACT_TEXT), "Ctrl+Shift+R", icon="redact-text", to_menu=tools_menu)
        a_redact_text.setToolTip("Redact Text — drag over text to permanently remove it at save")
        a_redact_block = act("Redact Block", lambda: self._arm_tool(ArmedTool.REDACT_REGION), icon="redact", to_menu=tools_menu)
        a_redact_block.setToolTip("Redact Block — drag a box to permanently remove its contents at save")
        # Search & redact (M64): the one redaction verb that is not a gesture, so it is a dialog —
        # find every occurrence, review the hits, mark the ones you meant. No toolbar slot: it is a
        # one-shot command, and §Design budgets keeps the toolbar to modes.
        a_redact_find = act("Find and Redact…", self._redact_matches, to_menu=tools_menu)
        a_redact_find.setToolTip("Find and Redact — redact every occurrence of a word or phrase")
        tools_menu.addSeparator()
        # Text marks + signature (M62, merged at M69.3). Both are the one R4 content-draw engine
        # (M61), and a stamp and a watermark are now **one** entry: they were never two features —
        # a watermark is a Stamp with `under=True`, and the only real difference (drag it somewhere
        # vs cover whole pages) is a control inside the dialog. They share one toolbar slot via the
        # Stamp ▾ split-button.
        a_stamp = act("Stamp / Watermark…", self._add_mark, icon="stamp", to_menu=tools_menu)
        a_stamp.setToolTip("Compose a text mark — drag it where you want it, or cover whole pages")
        a_signature = act("Signature / Image…", self._add_image_stamp, icon="signature",
                          to_menu=tools_menu)
        a_signature.setToolTip("Signature — place a scanned signature, seal or logo")
        self._stamp_actions = (a_stamp, a_signature)
        tools_menu.addSeparator()
        # Form fields (M69): compose, then drag the box — M62's placement gesture again. Menu-only;
        # creating a field is a one-shot command, and §Design budgets keeps the toolbar to modes.
        # Radio groups are deliberately absent (owner, 2026-07-18) — see PLAN.md §Future enhancements.
        field_menu = tools_menu.addMenu("Add Form Field")
        for kind in FIELD_KINDS:
            field_menu.addAction(
                f"{kind_label(kind)}…", lambda _checked=False, k=kind: self._add_form_field(k)
            )
        tools_menu.addSeparator()
        # Crop (M48): menu-only (no free toolbar slot needed for a one-shot); same armed pattern.
        a_crop = act("Crop Pages", lambda: self._arm_tool(ArmedTool.CROP), to_menu=tools_menu)
        a_crop.setToolTip("Crop Pages — drag the area to keep; the rest is hidden, not removed")
        act("Remove Crop", self._remove_crop, to_menu=tools_menu)
        # Object z-order (M59.8). Window-level actions so the shortcuts work wherever focus is,
        # but deliberately *not* in a menubar menu: they only ever apply to a selected object, so a
        # permanent menu group would sit greyed out most of the time. They surface in the view's
        # right-click menu on an object — where the object is — carrying their shortcut hints.
        self._a_z_actions = {}
        for key, label, keys in (
            ("front", "Bring to Front", "Ctrl+Shift+]"),
            ("forward", "Bring Forward", "Ctrl+]"),
            ("backward", "Send Backward", "Ctrl+["),
            ("back", "Send to Back", "Ctrl+Shift+["),
        ):
            a = act(label, lambda _checked=False, k=key: self._reorder_objects(k), keys)
            self.addAction(a)          # register on the window so the shortcut fires
            self._a_z_actions[key] = a
        # The page range a composed stamp is waiting to be applied across (M62); None = just
        # the page it is dropped on.
        self._pending_stamp_pages = None
        self._armed_actions = {
            ArmedTool.TEXTBOX: a_textbox,
            ArmedTool.HIGHLIGHT: a_highlight,
            ArmedTool.UNDERLINE: a_underline,
            ArmedTool.STRIKEOUT: a_strikeout,
            ArmedTool.PEN: a_pen,
            ArmedTool.LINE: a_line,
            ArmedTool.ARROW: a_arrow,
            ArmedTool.RECT: a_rect,
            ArmedTool.ELLIPSE: a_ellipse,
            ArmedTool.REDACT_TEXT: a_redact_text,
            ArmedTool.REDACT_REGION: a_redact_block,
            ArmedTool.CROP: a_crop,
            # STAMP arms only *after* its dialog composes a mark, so the menu entry above opens the
            # dialog rather than arming; this entry is what lights the button while placing.
            ArmedTool.STAMP: a_stamp,
        }
        for a in self._armed_actions.values():
            a.setCheckable(True)

        def split_button(actions) -> QToolButton:
            """A grouped split-button (PLAN.md §Design budgets — they keep the toolbar at ~10
            slots): the face is the sticky last-used tool, the arrow opens the group. The same
            QActions as the Tools menu, so shortcuts / checked-state stay single-sourced."""
            button = QToolButton()
            button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            menu = QMenu(button)
            for a in actions:
                menu.addAction(a)
            button.setMenu(menu)
            button.setDefaultAction(actions[0])
            # Sticky last-used face — but only for the *tools*. QMenu.triggered also fires for
            # sub-menu entries, so without this guard picking a colour from the Markup ▾ palettes
            # (M59.9) would make "Yellow" the button's tool.
            menu.triggered.connect(
                lambda chosen: button.setDefaultAction(chosen) if chosen in actions else None
            )
            return button

        # Markup ▾ (M56): highlight / underline / strikeout. Draw ▾ (M58): the five draw tools.
        self._markup_button = split_button((a_highlight, a_underline, a_strikeout))
        # The text-markup colours live here (M59.9), with the verbs they colour — not on the
        # pen/shapes style button, and not in a new toolbar slot.
        markup_menu = self._markup_button.menu()
        markup_menu.addSeparator()
        self._highlight_color_actions = self._add_color_submenu(
            markup_menu, "Highlight Colour", HIGHLIGHT_COLORS,
            self._set_highlight_color, self._highlight_color)
        self._line_color_actions = self._add_color_submenu(
            markup_menu, "Underline / Strike Colour", TEXT_LINE_COLORS,
            self._set_markup_line_color, self._markup_line_color)
        self._draw_button = split_button((a_pen, a_line, a_arrow, a_rect, a_ellipse))
        # Stamp ▾ (M62): the text mark · signature in one slot, the slot §Design budgets reserved
        # for R4. Each opens its dialog rather than arming directly — the mark has to be composed
        # before there is anything to place.
        self._stamp_button = split_button(self._stamp_actions)
        # Recent Signatures (M63) hangs off the same dropdown: re-placing last week's signature is
        # the common case, and going through the dialog again to pick the same file is the friction
        # the "two clicks on the second use" target is about. Hidden until there is one.
        self._signature_menu = self._stamp_button.menu().addMenu("Recent Signatures")
        self._rebuild_signature_menu()
        # Markup style (M59.5): one slot — the shared colour · width · fill the underline /
        # strikeout + draw tools stamp on the next mark. Seeds the overlay's sticky style and
        # tracks it thereafter (the overlay is the single source of truth, created above).
        self._markup_style_button = MarkupStyleButton()
        self.view.annotations.set_markup_style(self._markup_style_button.style())
        self._markup_style_button.styleChanged.connect(self._on_markup_style_changed)
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
            [a_select, a_grab, a_objects],
            [a_zout, self.zoom_widget, a_zin, a_fitw, a_fitp],
            [undo, redo],
            [a_cut, a_copy_pg, a_paste, a_delete, a_insert],
            [a_textbox, self._markup_button, self._draw_button, self._markup_style_button,
             self._stamp_button, a_redact_text, a_redact_block],
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

    # ---- focus-routed clipboard (M59): text vs pages vs object -------------------

    def _focused_text_editor(self):
        """The focused inline text-input widget, if any — a form-fill editor or the text-box
        editor. Their own clipboard must win, or Ctrl+V while typing would paste an *object*."""
        focus = QApplication.focusWidget()
        return focus if isinstance(focus, (QLineEdit, QPlainTextEdit)) else None

    def _edit_copy(self) -> None:
        """Ctrl+C, routed: an inline editor's own copy → the Pages sidebar's selection (when the
        sidebar has focus) → the live text selection → the selected object."""
        editor = self._focused_text_editor()
        if editor is not None:
            editor.copy()
        elif self.thumbs.hasFocus():
            self._copy_pages()
        elif self.view.selection is not None and self.view.selection.selected_words():
            self._copy_selection()
        else:
            self._copy_object()

    def _edit_cut(self) -> None:
        """Ctrl+X, routed like copy. Page text can't be cut (it isn't ours to remove without a
        redaction), so with neither sidebar focus nor a selected object this is a no-op."""
        editor = self._focused_text_editor()
        if editor is not None:
            editor.cut()
        elif self.thumbs.hasFocus():
            self._cut_pages()
        else:
            self._cut_object()

    def _edit_paste(self) -> None:
        """Ctrl+V, routed: inline editor → pages (sidebar focus) → the object clipboard."""
        editor = self._focused_text_editor()
        if editor is not None:
            editor.paste()
        elif self.thumbs.hasFocus():
            self._paste_pages()
        elif self._app.object_clipboard:
            self._paste_object()

    def _clipboard_target(self, hit=None):
        """The ``(page_index, [marks])`` the object-clipboard verbs act on, else ``None`` (M59.12).

        A **group** copies and cuts as a unit: if ``hit`` is part of the current multi-selection —
        or no hit was given (the keyboard path) — the whole selection is the target, mirroring how
        move / restyle / Delete already treat a group. A hit *outside* the selection targets just
        that mark, which is what right-clicking an unselected mark means. A selection is always on
        one page (M59.6), so a single page index covers the set.
        """
        selected = self.view.annotations.selected_objects if self.view.annotations else []
        if hit is not None and not any(mark is hit[1] for _p, mark in selected):
            return hit[0], [hit[1]]
        if not selected:
            return None
        return selected[0][0], [mark for _p, mark in selected]

    @staticmethod
    def _object_label(verb: str, count: int) -> str:
        """"Copy Object" / "Copy 3 Objects" — the menu never claims to act on more than it will."""
        return f"{verb} Object" if count == 1 else f"{verb} {count} Objects"

    def _copy_object(self, hit=None) -> bool:
        """Copy free-placed marks — the given ``(page, mark)`` hit, or the whole selected group —
        onto the app-wide object clipboard. Any text boxes among them also land on the system
        clipboard as plain text (joined, in selection order). Returns True if anything was copied."""
        target = self._clipboard_target(hit)
        if target is None:
            return False
        _page_index, marks = target
        self._app.object_clipboard = list(marks)
        texts = [mark.text for mark in marks if isinstance(mark, TextBox)]
        if texts:
            QGuiApplication.clipboard().setText("\n".join(texts))  # text/plain rides along
        return True

    def _cut_object(self, hit=None) -> None:
        """Cut = copy + undoable remove, the whole group in **one** undo step (M59.12)."""
        target = self._clipboard_target(hit)
        if target is None or not self._copy_object(hit):
            return
        page_index, marks = target
        self.view.annotations.clear_object_selection()
        self._remove_annotations_batch(page_index, marks, self._object_label("Cut", len(marks)))

    def _paste_object(self, page_index: int | None = None, at_point: tuple | None = None) -> None:
        """Paste the object clipboard as **one** undoable step: at ``at_point`` (page coords — the
        context menu's click spot, centred there), else onto the current page with a small offset
        from the original position.

        A group pastes as a group (M59.12): the offset/centring/clamping is computed once from the
        set's **union bounds** and the same delta is applied to every mark, so the arrangement is
        preserved exactly — the same bounding-box principle as the M59.7 group resize. Clamping the
        union (not each mark) is what stops a group from collapsing onto itself near a page edge.
        """
        marks = self._app.object_clipboard
        if not marks or self.vdoc.page_count == 0:
            return
        page = self.view.current_page if page_index is None else page_index
        boxes = [mark_bounds(mark) for mark in marks]
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        w, h = x1 - x0, y1 - y0
        if at_point is not None:
            tx, ty = at_point[0] - w / 2.0, at_point[1] - h / 2.0  # centred on the click
        else:
            tx, ty = x0 + 12.0, y0 + 12.0  # the classic paste offset
        _fx, _fy, pw, ph = self.vdoc.page_base_rect(page)
        tx = min(max(0.0, tx), max(0.0, pw - w))
        ty = min(max(0.0, ty), max(0.0, ph - h))
        dx, dy = tx - x0, ty - y0                      # one delta for the set → arrangement kept
        pasted = [translate_mark(mark, dx, dy) for mark in marks]
        self._note_edit_on(page)
        if len(pasted) == 1:
            self.undo_stack.push(AddAnnotationCommand(self.vdoc, page, pasted[0]))
        else:
            self.undo_stack.beginMacro(self._object_label("Paste", len(pasted)))
            for mark in pasted:
                self.undo_stack.push(AddAnnotationCommand(self.vdoc, page, mark))
            self.undo_stack.endMacro()
        # Select what was just pasted, so it is immediately ready to restyle / move / delete — and
        # so its resize handles are up (M59.7). Without this the first drag on a pasted mark lands
        # on its body and *moves* it instead of resizing, which only *looks* like a broken resize.
        # After the push, not before: the add reloads the view, which clears any selection.
        if self.view.annotations is not None:
            self.view.annotations.select_objects(page, pasted)

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

    def _note_edit_on(self, index: int) -> None:
        """Remember which page an edit is about to land on. Set *before* pushing the command —
        ``_on_doc_changed`` runs synchronously inside the push."""
        self._edited_page = index

    def _on_doc_changed(self, _index: int) -> None:
        # A structural edit invalidates page indices, so drop stale overlays and rebuild.
        self.view.selection.clear()
        self.view.search.clear()
        if self.search_results.isVisible():
            self.search_results.refresh()  # the hits died with the edit — no stale rows
        self.view.reload()
        # Follow the edit: marking up a page that isn't the one under the viewport centre should
        # move the sidebar highlight onto it, without scrolling. Consumed once, so an undo/redo
        # (which records nothing) leaves the current page where the reader put it.
        if self._edited_page is not None:
            self.view.set_current_page(self._edited_page)
            self._edited_page = None
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

    # ---- text marks + signatures (M62; the M61 content-draw engine) -------------
    #
    # Two shapes of flow over one engine, and the difference is only where the mark goes:
    #   * a stamp / signature is *placed* — compose it, then drag the box it lands in;
    #   * a watermark covers whole pages — compose it and it applies at once, no drag.

    def _arm_content_mark(self, mark, pages=None) -> None:
        """Hand a composed content mark to the placement gesture (M62).

        ``pages`` (more than one) makes the next placement apply to that whole range — the drag
        still happens on one page, because you have to point at *something* to say how big.
        """
        self._pending_stamp_pages = list(pages) if pages else None
        self.view.annotations.pending_content_mark = mark
        self.view.arm(ArmedTool.STAMP)
        self._a_select.setChecked(True)

    def _add_mark(self) -> None:
        """Tools ▸ Stamp / Watermark… — compose one text mark, placed either way (M69.3).

        **One entry point, because they were never two features**: a watermark is a
        :class:`~model.content_marks.Stamp` with ``under=True``, and the only structural difference
        is how it is placed — dragged onto a spot, or applied full-page across a range. The dialog
        surfaces exactly that as its Place control, so this method just routes on it.

        The composed **style** is sticky across sessions (text, colour, size, angle, opacity, frame,
        behind-content): a mark is configured once and applied for months, so retyping it every
        launch is the kind of friction that sends people back to whatever they used before. The page
        range is deliberately *not* remembered — see :mod:`ui.mark_dialog`.
        """
        from ui.mark_dialog import MarkDialog

        dialog = MarkDialog(self, self.vdoc.page_count, self.view.current_page)
        dialog.restore(self._settings.get_pref(_MARK_STYLE_PREF, {}))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        pages = dialog.selected_pages()
        if pages is None:
            return
        self._settings.set_pref(_MARK_STYLE_PREF, dialog.style_state())
        if dialog.covers_page:
            self._apply_page_mark(dialog, pages)
        else:
            self._arm_content_mark(dialog.mark(), pages)

    def _apply_page_mark(self, dialog, pages: list[int]) -> None:
        """Apply a whole-page mark across ``pages`` as one undo step — the watermark flow.

        Each page gets the mark sized to **its own** page box, so a document mixing page sizes is
        marked correctly rather than inheriting the current page's rect.
        """
        if not pages:
            return
        # **Follow the edit only when the edit is somewhere** (M69.5). `_note_edit_on` exists to move
        # the current page onto the page a mark landed on, which is right for a mark that landed on
        # *a* page. A range mark did not land anywhere in particular, so there is no page to follow —
        # and following one yanks the reader (and the sidebar's current row) off the page they were
        # reading to the start or the end of the document for no reason they can see. Marking every
        # page changes nothing about where the reader is, so nothing should move.
        if len(pages) == 1:
            self._note_edit_on(pages[0])
        self.undo_stack.beginMacro(f"Add mark to {len(pages)} pages")
        for page_index in pages:
            width, height = self.view._unrotated_size(page_index)
            self.undo_stack.push(
                AddAnnotationCommand(self.vdoc, page_index,
                                     dialog.mark((0.0, 0.0, width, height)))
            )
        self.undo_stack.endMacro()

    def _add_image_stamp(self) -> None:
        """Tools ▸ Signature / Image… — choose + tune the image, then arm the placement drag (M63).

        Only the **path** is remembered, never the pixels: a signature is the most sensitive thing
        this app touches, and a convenience copy would be one the user did not ask for and cannot
        find to delete. Moving or deleting the file revokes it.
        """
        from ui.signature_dialog import SignatureDialog

        dialog = SignatureDialog(self, self._settings.recent_signatures())
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.path():
            return
        self._settings.add_recent_signature(dialog.path())
        self._rebuild_signature_menu()
        self._arm_content_mark(dialog.image_stamp())

    def _rebuild_signature_menu(self) -> None:
        """Refresh the Stamp ▾ ▸ Recent Signatures submenu from the store.

        This is what makes the *second* use two clicks — pick the signature, drag its box — with no
        dialog in the way. Hidden entirely when there is nothing to list (owner rule: no dead
        chrome), so a first-time user never sees an empty submenu.
        """
        menu = self._signature_menu
        menu.clear()
        recent = self._settings.recent_signatures()
        menu.menuAction().setVisible(bool(recent))
        for path in recent:
            action = menu.addAction(os.path.basename(path))
            action.setToolTip(path)
            action.triggered.connect(lambda _checked=False, p=path: self._place_recent_signature(p))
        if recent:
            menu.addSeparator()
            menu.addAction("Clear List", self._clear_recent_signatures)

    def _rebuild_signature_menu_later(self) -> None:
        """Rebuild the Recent Signatures submenu **after** the current signal finishes delivering.

        :meth:`_rebuild_signature_menu` calls ``menu.clear()``, which destroys the submenu's
        ``QAction`` objects. Calling it from one of those actions' own ``triggered`` handlers
        therefore deletes the action that is still mid-emission — undefined behaviour that crashed
        the app on Windows and surfaces as "Internal C++ object already deleted" under PySide
        (owner-reported: picking a recent signature from the dropdown). Deferring by a zero-delay
        timer lets the signal unwind first, so the action is destroyed when nothing is using it.
        """
        QTimer.singleShot(0, self._rebuild_signature_menu)

    def _place_recent_signature(self, path: str) -> None:
        """Arm a previously used signature straight from the menu — no dialog (M63)."""
        if not os.path.exists(path):
            # It vanished; drop it rather than fail mysteriously. Deferred — see the helper: we are
            # inside the triggered handler of the very action the rebuild would destroy.
            self._rebuild_signature_menu_later()
            return
        self._settings.add_recent_signature(path)
        self._rebuild_signature_menu_later()
        self._arm_content_mark(ImageStamp(rect=(0.0, 0.0, 1.0, 1.0), image_path=path))

    def _clear_recent_signatures(self) -> None:
        # Same hazard: "Clear List" lives in the menu the rebuild empties.
        self._settings.clear_recent_signatures()
        self._rebuild_signature_menu_later()

    def _add_form_field(self, kind: str) -> None:
        """Tools ▸ Add Form Field ▸ … — compose the field, then drag its box (M69).

        The result is an ordinary AcroForm field at save, so filling, printing and flattening work
        on it by construction rather than by anything written here.
        """
        from ui.field_dialog import FieldDialog

        from model.page_edits import read_form_fields

        existing = {field.name for field in read_form_fields(self.vdoc)}
        dialog = FieldDialog(self, kind, existing)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.view.annotations.pending_field = dialog.field()
        self.view.arm(ArmedTool.FIELD)
        self._a_select.setChecked(True)

    def _redact_matches(self) -> None:
        """Tools ▸ Find and Redact… — mark every checked occurrence for redaction (M64).

        The dialog drives the real search controller, so the hits highlight on the page while they
        are reviewed. What comes back is ordinary :class:`Redaction` descriptors — **nothing is
        destroyed here**: they stay editable and undoable until the existing confirmed Save applies
        them, which keeps one destructive path in the app instead of a second one.

        One :class:`Redaction` per page holding that page's boxes, all inside one macro, so a
        hundred hits across twenty pages is a single undo step.
        """
        from ui.redact_matches_dialog import RedactMatchesDialog

        dialog = RedactMatchesDialog(self, self.view)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        hits = dialog.checked_hits() if accepted else []
        self.view.search.clear()          # drop the dialog's highlights either way
        if self.find_bar.isVisible():
            self.find_bar.hide_bar()      # the find bar's own query is stale now
        if not hits:
            return
        by_page: dict[int, list[tuple]] = {}
        for page_index, box in hits:
            by_page.setdefault(page_index, []).append(box)
        label = f"Redact {len(hits)} match{'es' if len(hits) != 1 else ''}"
        self.undo_stack.beginMacro(label)
        for page_index, boxes in sorted(by_page.items()):
            self._note_edit_on(page_index)
            self.undo_stack.push(
                AddAnnotationCommand(self.vdoc, page_index, Redaction(tuple(boxes)))
            )
        self.undo_stack.endMacro()

    def _on_armed_changed(self, tool) -> None:
        """Light the matching tool button while it's armed (None → all off)."""
        for armed_tool, action in self._armed_actions.items():
            action.setChecked(armed_tool is tool)

    def _apply_text_tool(self, tool) -> None:
        """A drag-over-text armed tool was released on a selection → apply it (one undo)."""
        handler = {
            ArmedTool.HIGHLIGHT: self._highlight_selection,
            ArmedTool.UNDERLINE: self._underline_selection,
            ArmedTool.STRIKEOUT: self._strikeout_selection,
            ArmedTool.REDACT_TEXT: self._redact_selection,
        }.get(tool)
        if handler is not None:
            handler()

    def _add_annotation(self, index: int, annotation) -> None:
        """Add an annotation to a page (from the text-box / redact tools) as an undoable command.

        A stamp placed while a **page range** is pending (M62's "initials on every page") lands on
        every page in the range instead — the placement drag happened on one page, but the range is
        what the user asked for. One macro, so the whole application is a single undo step.
        """
        pages, self._pending_stamp_pages = self._pending_stamp_pages, None
        if pages and is_content_mark(annotation) and len(pages) > 1:
            self._apply_to_pages(annotation, pages)
            return
        self._note_edit_on(index)
        self.undo_stack.push(AddAnnotationCommand(self.vdoc, index, annotation))

    def _apply_to_pages(self, mark, pages) -> None:
        """Add one content mark to each of ``pages`` as a single undo step (M62).

        The same descriptor object on every page: it is a frozen value, and the placement rect is in
        page points, so a stamp lands in the same spot on each — which is what "initials on every
        page" means. Pages of differing size keep the rect, not a proportion; the mark is clamped by
        nothing here because the user placed it on a real page and pages in one document rarely
        differ. The range itself is the UI's, never model state (PLAN.md §R4, M61).
        """
        pages = [p for p in pages if 0 <= p < self.vdoc.page_count]
        if not pages:
            return
        self.undo_stack.beginMacro(f"Add {mark_noun(mark)} to {len(pages)} pages")
        for page_index in pages:
            self._note_edit_on(page_index)
            self.undo_stack.push(AddAnnotationCommand(self.vdoc, page_index, mark))
        self.undo_stack.endMacro()

    def _remove_annotation(self, index: int, annotation) -> None:
        """Remove an annotation (from the right-click menu) as an undoable command."""
        self._note_edit_on(index)
        self.undo_stack.push(RemoveAnnotationCommand(self.vdoc, index, annotation))

    def _replace_annotation(self, index: int, old, new, text=None) -> None:
        """Swap an annotation for an updated one (moving / re-editing a text box) — one undo step."""
        self._note_edit_on(index)
        self.undo_stack.push(ReplaceAnnotationCommand(self.vdoc, index, old, new, text))

    def _replace_annotations_batch(self, index: int, pairs, text: str) -> None:
        """Swap several annotations on a page as **one** undo step (M59.6 group restyle / move)."""
        self._note_edit_on(index)
        self.undo_stack.beginMacro(text)
        for old, new in pairs:
            self.undo_stack.push(ReplaceAnnotationCommand(self.vdoc, index, old, new))
        self.undo_stack.endMacro()

    def _remove_annotations_batch(self, index: int, marks, text: str) -> None:
        """Remove several annotations on a page as **one** undo step (M59.6 group delete)."""
        self._note_edit_on(index)
        self.undo_stack.beginMacro(text)
        for mark in marks:
            self.undo_stack.push(RemoveAnnotationCommand(self.vdoc, index, mark))
        self.undo_stack.endMacro()

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

    def _apply_selection_bars(self, make, label: str) -> None:
        """Apply a line-bar mark to the current text selection — one continuous bar per line, one
        undo step across the touched pages. ``make(rects)`` builds the descriptor.

        Text redaction only, since M59.10: the three markup verbs went to :meth:`_apply_markup`,
        which merges with what is already on the page. Redactions deliberately stay on this plain
        add path — they are destructive and colourless, and overlapping rects already union
        harmlessly in ``apply_redactions``, so there is nothing for a merge to fix."""
        by_page = self._selection_line_bars()
        if not by_page:
            return
        self.view.selection.clear()  # so the mark shows, not the blue selection over it
        self._note_edit_on(min(by_page))   # a selection can span pages — follow the first marked
        self.undo_stack.beginMacro(label)
        for page_index, rects in by_page.items():
            self.undo_stack.push(AddAnnotationCommand(self.vdoc, page_index, make(tuple(rects))))
        self.undo_stack.endMacro()

    def _apply_markup(self, mark_type, color: tuple, label: str) -> None:
        """Paint the current text selection with a markup mark, **merging** with what is already
        there (M59.10) instead of stacking a second descriptor on top: same colour folds in, a
        different colour takes over the span it covers. See :func:`merge_markup` for the rules.

        One :class:`SetAnnotationsCommand` per touched page inside one macro — so a pass that
        absorbs, trims and adds across a multi-page selection is a single undo step."""
        by_page = self._selection_line_bars()
        if not by_page:
            return
        self.view.selection.clear()  # so the mark shows, not the blue selection over it
        updates = []
        for page_index, rects in by_page.items():
            current = self.vdoc.ordered[page_index].annotations
            merged = merge_markup(current, tuple(rects), mark_type, color)
            if merged != current:
                updates.append((page_index, merged))
        if not updates:
            return                   # re-marking an identical span in the same colour: nothing to do
        self._note_edit_on(min(page_index for page_index, _ in updates))
        self.undo_stack.beginMacro(label)
        for page_index, merged in updates:
            self.undo_stack.push(SetAnnotationsCommand(self.vdoc, page_index, merged, label))
        self.undo_stack.endMacro()

    def _add_color_submenu(self, menu, title: str, palette, setter, current) -> dict:
        """A curated colour sub-menu of swatches (M59.9), ticked at ``current``. Returns
        ``{rgb: action}`` so tests and later syncing can reach the entries."""
        sub = menu.addMenu(title)
        group = QActionGroup(sub)
        actions = {}
        for label, rgb in palette:
            action = sub.addAction(swatch_icon(rgb), label)
            action.setCheckable(True)
            action.setProperty("colorSwatch", True)  # a semantic chip — must NOT theme-retint
            group.addAction(action)
            action.triggered.connect(lambda _checked=False, c=rgb: setter(c))
            action.setChecked(rgb == current)
            actions[rgb] = action
        return actions

    def _set_highlight_color(self, color) -> None:
        self._highlight_color = color

    def _set_markup_line_color(self, color) -> None:
        self._markup_line_color = color

    def _on_markup_style_changed(self, style) -> None:
        """The picker changed → update the sticky style for the next mark, and — mirroring the
        text-markup 'apply to the current selection' rule — restyle the selected drawn object(s) in
        place (undoable) if any are selected (a whole group in one undo step, M59.6)."""
        self.view.annotations.set_markup_style(style)
        self.view.annotations.restyle_selected_objects(style)

    def _reorder_objects(self, action: str) -> bool:
        """Move the selected object(s) in z-order (M59.8) as one undo step.

        Acts on the whole selection, so a group raises together. The page's annotation tuple *is*
        the z-order, so this is a pure reorder — it moves what paints on top in the saved PDF and
        what a click hits, in step. The marks survive the edit, so the selection is restored onto
        them afterwards."""
        selection = self.view.annotations.selected_objects if self.view.annotations else []
        if not selection:
            return False
        page_index = selection[0][0]
        marks = [mark for _p, mark in selection]
        current = self.vdoc.ordered[page_index].annotations
        reordered = reorder_marks(current, marks, action)
        if reordered == current:
            return False                                # already at that end — nothing to undo
        label = {"front": "Bring to front", "forward": "Bring forward",
                 "backward": "Send backward", "back": "Send to back"}[action]
        self._note_edit_on(page_index)
        self.undo_stack.push(SetAnnotationsCommand(self.vdoc, page_index, reordered, label))
        if self.view.annotations is not None:
            self.view.annotations.select_objects(page_index, marks)  # the reload cleared it
        return True

    def _on_object_selected(self, mark) -> None:
        """A free-placed mark was click-selected (M59.5): load a drawn mark's colour/width/fill into
        the picker (so a follow-up tweak edits *that* mark), like double-clicking a text box loads
        its style into the format bar. A text box keeps its own format bar, so it leaves the picker
        untouched."""
        style = MarkupStyle.from_mark(mark)
        if style is not None:
            self._markup_style_button.set_style(style)      # reflect it in the button (no emit)
            self.view.annotations.set_markup_style(style)   # …and as the sticky default

    def _highlight_selection(self) -> None:
        self._apply_markup(Highlight, self._highlight_color, "Highlight")  # curated wash (M59.9)

    def _underline_selection(self) -> None:
        # The line colour is shared with strikeout — the proofing-line colour.
        self._apply_markup(Underline, self._markup_line_color, "Underline")

    def _strikeout_selection(self) -> None:
        self._apply_markup(Strikeout, self._markup_line_color, "Strike out")

    def _redact_selection(self) -> None:
        self._apply_selection_bars(Redaction, "Redact selection")

    def _delete_foreign_annotation(self, page_index: int, mark) -> None:
        """Mark a foreign annotation for removal at save (M66) — undoable like any page edit.

        Nothing is removed from the shared source: a :class:`ForeignDeletion` rides the PageRef and
        is applied to the materialised copy, so undo restores the annotation exactly and every other
        annotation on the page still passes through untouched.
        """
        from model.foreign_annots import ForeignDeletion

        self._note_edit_on(page_index)
        self.undo_stack.push(
            AddAnnotationCommand(self.vdoc, page_index,
                                 ForeignDeletion(mark.fingerprint, mark.label),
                                 text=f"Delete {mark.label}")
        )

    def _adopt_foreign_annotation(self, page_index: int, mark) -> bool:
        """Double-click a foreign mark → make it an editable KlarPDF mark (M68).

        Adoption re-creates the annotation from our own descriptor, so anything the descriptor
        cannot carry is **lost**. That has to be said before the edit, with a way out, rather than
        discovered afterwards when the original is gone — hence the degrade warning, which fires
        exactly when something would actually be dropped.

        The mechanism is entirely M66's: a :class:`ForeignDeletion` of the original plus the parsed
        descriptor, in one macro. At materialise the original is stripped and ours is re-added
        author-tagged, so from then on it round-trips exactly like a mark we drew.
        """
        from model.foreign_annots import (
            ForeignDeletion,
            adopt_annotation,
            degradations,
            find_annotation,
        )

        ref = self.vdoc.ordered[page_index]
        source = self.vdoc.sources[ref.source_id][ref.source_page_index]
        annot = find_annotation(source, mark.fingerprint)
        if annot is None:
            return False
        adopted = adopt_annotation(annot)
        if adopted is None:
            QMessageBox.information(
                self, "Edit annotation",
                f"KlarPDF can't edit a {mark.kind_name} annotation.\n\n"
                "You can still move it or delete it.",
            )
            return False
        lost = degradations(annot)
        if lost and not self._confirm_degrade(mark, lost):
            return False
        # Carry the pending move (M67) onto the adopted descriptor, so adopting a mark you have
        # already dragged keeps it where you put it rather than snapping back.
        from model.foreign_annots import ForeignMove
        from model.page_edits import translate_mark

        shift = next((a for a in ref.annotations
                      if isinstance(a, ForeignMove) and a.fingerprint == mark.fingerprint), None)
        if shift is not None:
            try:
                adopted = translate_mark(adopted, shift.dx, shift.dy)
            except TypeError:
                pass                      # a text-anchored mark: its quads are already where it sits
        self._note_edit_on(page_index)
        self.undo_stack.beginMacro(f"Edit {mark.label}")
        if shift is not None:
            self.undo_stack.push(RemoveAnnotationCommand(self.vdoc, page_index, shift))
        self.undo_stack.push(
            AddAnnotationCommand(self.vdoc, page_index, ForeignDeletion(mark.fingerprint,
                                                                       mark.label))
        )
        self.undo_stack.push(AddAnnotationCommand(self.vdoc, page_index, adopted))
        self.undo_stack.endMacro()
        return True

    def _confirm_degrade(self, mark, lost) -> bool:
        """Warn that editing will simplify this annotation. A separate seam so tests drive it."""
        listed = "\n".join(f"  • {item}" for item in lost)
        return (
            QMessageBox.warning(
                self, "Editing will simplify this annotation",
                f"This {mark.kind_name} uses features KlarPDF can't reproduce. Editing it will "
                f"lose:\n\n{listed}\n\nEdit it anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            == QMessageBox.StandardButton.Yes
        )

    def _move_foreign_annotation(self, page_index: int, mark, dx: float, dy: float) -> None:
        """A foreign annotation was dragged (M67) — record the translation, undoably.

        Moves **combine rather than stack**: dragging the same mark twice replaces its descriptor
        with the summed delta. Beyond tidiness that is required for correctness — a hash fingerprint
        is derived from the annotation's rect, so a second descriptor keyed on the moved position
        would no longer match the annotation as it arrives at materialise. One descriptor per mark,
        always holding the fingerprint the page arrived with.
        """
        from model.foreign_annots import ForeignMove

        self._note_edit_on(page_index)
        existing = next(
            (a for a in self.vdoc.page_annotations(page_index)
             if isinstance(a, ForeignMove) and a.fingerprint == mark.fingerprint),
            None,
        )
        label = f"Move {mark.label}"
        if existing is not None:
            self.undo_stack.push(ReplaceAnnotationCommand(
                self.vdoc, page_index, existing,
                ForeignMove(mark.fingerprint, existing.dx + dx, existing.dy + dy, mark.label),
                label,
            ))
        else:
            self.undo_stack.push(AddAnnotationCommand(
                self.vdoc, page_index, ForeignMove(mark.fingerprint, dx, dy, mark.label), text=label
            ))

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
            # Free-placed marks are copyable (M59); the text-anchored marks + redactions are not
            # (they belong to the text under them). The R4 content marks (M62) are free-placed too
            # — they already select, move, resize and Ctrl+C/X/V through the same object code — so
            # they list the same verbs here. Sourced from the overlay's own object-type tuple rather
            # than a second hand-written list, which is what let stamps fall off this menu while
            # working everywhere else.
            # …except a page-blanketing mark (a watermark), which is not a free-placed object: it
            # has nowhere to be moved to and is deliberately not grabbable (see `covers_page`), so
            # offering Copy / Cut / z-order on it would be chrome for verbs that do nothing. Its
            # right-click menu is just Remove — which is also its only removal path, the click that
            # would select it having been given back to text selection.
            page_wide = self.view.annotations is not None and \
                self.view.annotations.covers_page(page_index, annot)
            if isinstance(annot, (TextBox,) + OBJECT_TYPES) and not page_wide:
                # Right-clicking a free-placed mark selects it, so the verbs below (and the
                # z-order shortcuts) have an unambiguous target and you can *see* what you're
                # acting on. Right-clicking a member of a group leaves the group intact — that's
                # how you raise several marks at once.
                if self.view.annotations is not None and not any(
                    mark is annot for _p, mark in self.view.annotations.selected_objects
                ):
                    self.view.annotations.select_object(page_index, annot)
                # Copy/Cut act on the whole group when the clicked mark is part of it (M59.12),
                # so the labels count what will actually move.
                target = self._clipboard_target(hit)
                n = len(target[1]) if target else 1
                menu.addAction(self._object_label("Copy", n), lambda: self._copy_object(hit))
                menu.addAction(self._object_label("Cut", n), lambda: self._cut_object(hit))
                menu.addSeparator()
                # Z-order (M59.8) — the shared window actions, so their shortcuts show here (this
                # menu is their discovery path). Each is disabled at the end it's already at, so
                # the menu only offers what would actually change something.
                current = self.vdoc.ordered[page_index].annotations
                selected = [mark for _p, mark in self.view.annotations.selected_objects] \
                    if self.view.annotations else [annot]
                for key in ("front", "forward", "backward", "back"):
                    action = self._a_z_actions[key]
                    action.setEnabled(reorder_marks(current, selected, key) != current)
                    menu.addAction(action)
                menu.addSeparator()
            # `mark_noun` already names every free-placed mark for the undo labels; deferring to it
            # keeps one vocabulary in the app and means a new descriptor gets a real name here
            # instead of the generic fallback (which is what a stamp used to get).
            # A mark covering the whole page is a watermark whichever side of the content it is on
            # — that is what the user called for and what they will look for to remove. Keying this
            # on `under` broke the moment `under` stopped being the whole-page default (M69.5).
            noun = "watermark" if page_wide else mark_noun(annot)
            label = {
                "Highlight": "Remove highlight",
                "Underline": "Remove underline",
                "Strikeout": "Remove strikeout",
                "Redaction": "Remove redaction",
            }.get(type(annot).__name__) or f"Remove {noun}"
            menu.addAction(label, lambda: self.view.annotations.remove(page_index, annot))
            return menu
        # A **foreign** annotation — one another tool wrote (M66). Checked after our own marks, so
        # an editable mark the user just placed wins over a foreign one it happens to sit on, and
        # before the text-selection verbs, keeping the stated "most specific hit wins" rule.
        foreign = (self.view.annotations.foreign_annotation_at(scene_pt)
                   if self.view.annotations else None)
        if foreign is not None:
            page_index, mark = foreign
            self.view.annotations.outline_foreign(page_index, mark)  # show what will be deleted
            if mark.contents:
                menu.addAction("Copy Comment Text",
                               lambda: QGuiApplication.clipboard().setText(mark.contents))
            menu.addAction(f"Delete {mark.label}",
                           lambda: self._delete_foreign_annotation(page_index, mark))
            return menu
        # A live text selection → its verbs. Highlight/Redact Selection apply now to what is
        # selected — unlike the toolbar's armed one-shot tools, which select-then-apply.
        if self.view.selection is not None and self.view.selection.selected_words():
            menu.addAction(self._a_copy_text)
            menu.addSeparator()
            menu.addAction("Highlight Selection", self._highlight_selection)
            menu.addAction("Underline Selection", self._underline_selection)
            menu.addAction("Strike Out Selection", self._strikeout_selection)
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
        # routes the same QAction objects as the View menu, so shortcuts show alongside. Paste
        # Object (M59) leads — the one verb that acts *at the clicked spot* (the pasted mark is
        # centred on it); disabled while the object clipboard is empty.
        page_hit = self.view.page_and_local_at(scene_pt)
        if page_hit[0] is not None:
            page_index, local = page_hit
            a_paste_obj = menu.addAction(
                self._object_label("Paste", max(1, len(self._app.object_clipboard))),
                lambda: self._paste_object(page_index, (local.x(), local.y())),
            )
            a_paste_obj.setEnabled(bool(self._app.object_clipboard))
            menu.addSeparator()
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
        file + clear the undo history, so the secret is gone from disk *and* memory.

        An R4 **content mark** (stamp / signature / watermark, M61) commits on the same path for a
        different reason: it bakes into the page content stream and leaves nothing author-tagged to
        read back, so a model copy surviving the save would bake a *second* mark on the next one."""
        committing = self.vdoc.has_redactions() or self.vdoc.has_content_marks()
        if committing and not self._confirm_commit():
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

    def _confirm_commit(self) -> bool:
        """Confirm a save that is a point of no return, naming what is actually being committed.

        Two kinds, and a save can carry both: a **redaction** destroys content, and a **content
        mark** (stamp / signature / watermark) becomes part of the page. Either way the edit stops
        being undoable at Save, which is the thing the user has to agree to — so the wording says
        that plainly rather than hiding behind "are you sure?" (PLAN.md §Design budgets, honesty).
        """
        redacting, stamping = self.vdoc.has_redactions(), self.vdoc.has_content_marks()
        if redacting and stamping:
            title = "Apply redactions and stamps?"
            body = ("Saving permanently removes the redacted content and bakes the stamps, "
                    "signatures and watermarks into the pages. Neither can be undone afterwards.")
        elif redacting:
            title = "Apply redactions?"
            body = "Saving permanently removes the redacted content and cannot be undone."
        else:
            title = "Bake stamps into the pages?"
            body = ("Saving draws the stamps, signatures and watermarks into the page content. "
                    "They can no longer be moved or removed afterwards.")
        return (
            QMessageBox.warning(
                self,
                title,
                f"{body}\n\nContinue?",
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
