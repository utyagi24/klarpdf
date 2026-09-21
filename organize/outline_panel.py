"""Outline (bookmark) sidebar tree bound to the VirtualDocument's live ``remapped_toc()`` (M45).

Shown as an **Outline** tab beside Pages — only for documents whose origin carries an outline
(``VirtualDocument.has_outline()``); a TOC-less document gets no tab and no tab bar at all
(owner rule: inapplicable UI is invisible, not greyed out). The tree is rebuilt from
``remapped_toc()`` after every edit, so it always shows what a Save would write: entries follow
reorders to their page's new position and entries whose target page was deleted disappear (and
come back on undo). Clicking an entry jumps the view — **to the spot on the page the bookmark
names, since M150 (#362)**, not merely to the page's top, and only ever up or down; scrolling the
view highlights the entry of the page in view. Display-only — outline *editing* is a deferred enhancement (PLAN.md).
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from klarpdf.model.destinations import RAW_TAIL_KEY, Destination, content_point
from klarpdf.model.virtual_document import VirtualDocument
from organize.thumbnail_panel import _SIDEBAR_W  # one default width for both sidebar tabs

_PAGE_ROLE = Qt.ItemDataRole.UserRole  # item data slot holding the 0-based target page index
#: Item data slot holding how far **down** its page the bookmark points, in that page's own
#: points from the top of its visible area — ``None`` when the bookmark names no height
#: (M150, #362). Only the height is kept: a click never moves the page sideways.
_SPOT_ROLE = Qt.ItemDataRole.UserRole + 1


class OutlinePanel(QTreeWidget):
    """The outline tree: click / keyboard-select an entry to jump, tracks the visible page."""

    #: 0-based target page, and how far down it to land (``None`` when the bookmark names no
    #: height). ``MainWindow`` hands both to ``PdfView.goto_destination``.
    entryActivated = Signal(int, object)

    def __init__(self, vdoc: VirtualDocument, parent=None) -> None:
        super().__init__(parent)
        self._vdoc = vdoc
        self._syncing = False   # guard against jump→highlight→jump feedback (as ThumbnailPanel)
        self._current_page = 0  # last page reported by the view; re-highlighted after a rebuild
        # (page0, item) in outline order — set_current scans this instead of walking the tree.
        self._entries: list[tuple[int, QTreeWidgetItem]] = []

        self.setHeaderHidden(True)
        self.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.currentItemChanged.connect(self._on_current_item_changed)
        # currentItemChanged alone misses a click on the already-current entry (no change fires),
        # and re-jumping to the section you scrolled away from is the natural outline gesture.
        self.itemClicked.connect(self._on_item_clicked)
        self.populate()

    def sizeHint(self) -> QSize:
        return QSize(_SIDEBAR_W, super().sizeHint().height())

    # ---- build ------------------------------------------------------------------

    def populate(self) -> None:
        """(Re)build the tree from the live ``remapped_toc()``. Called after every edit
        (``MainWindow._on_doc_changed``), so the outline tracks deletes/reorders as they happen.
        User-collapsed branches stay collapsed across the rebuild (keyed by title path — ambiguous
        only for identically-titled siblings, where re-expanding is a harmless default)."""
        collapsed = self._collapsed_paths()
        self._syncing = True
        self.clear()
        self._entries = []
        # remap_toc repairs levels (start at 1, never jump by more than +1), so a simple ancestor
        # stack rebuilds the nesting: stack[i] is the last item seen at level i+1.
        stack: list[QTreeWidgetItem] = []
        for entry in self._vdoc.remapped_toc():
            level, title, page = entry[0], entry[1], entry[2]
            item = QTreeWidgetItem([str(title)])
            item.setData(0, _PAGE_ROLE, page - 1)
            item.setData(0, _SPOT_ROLE, self._spot_of(entry, page - 1))
            item.setToolTip(0, f"Page {page}")
            del stack[level - 1 :]
            if stack:
                stack[-1].addChild(item)
            else:
                self.addTopLevelItem(item)
            stack.append(item)
            self._entries.append((page - 1, item))
        self.expandAll()
        self._apply_collapsed_paths(collapsed)
        self._syncing = False
        self.set_current(self._current_page)  # restore the you-are-here highlight

    def _spot_of(self, entry, page0: int) -> "float | None":
        """How far down page ``page0`` this entry points, or ``None`` when it names no height.

        The measurement is in that page's own points, from the top of its visible area. A bookmark
        may also name a left edge; it is read and discarded, because a click must not move the page
        sideways (owner's rule, 2026-09-20).

        The bookmark's destination rides in the entry under
        :data:`~model.destinations.RAW_TAIL_KEY`, put there by the same remap that renumbered the
        page, so the spot and the page can never disagree about which bookmark they describe.
        """
        dest = entry[3] if len(entry) > 3 else None
        tail = dest.get(RAW_TAIL_KEY) if isinstance(dest, dict) else None
        if tail is None or not 0 <= page0 < len(self._vdoc.ordered):
            return None
        ref = self._vdoc.ordered[page0]
        page = self._vdoc.sources[ref.source_id][ref.source_page_index]
        return content_point(page, Destination(page0, tail))[1]

    def _collapsed_paths(self) -> set[tuple]:
        collapsed = set()
        for _page, item in self._entries:
            if item.childCount() and not item.isExpanded():
                collapsed.add(self._path_of(item))
        return collapsed

    def _apply_collapsed_paths(self, collapsed: set[tuple]) -> None:
        if not collapsed:
            return
        for _page, item in self._entries:
            if self._path_of(item) in collapsed:
                item.setExpanded(False)

    @staticmethod
    def _path_of(item: QTreeWidgetItem) -> tuple:
        path = []
        while item is not None:
            path.append(item.text(0))
            item = item.parent()
        return tuple(reversed(path))

    # ---- navigation -------------------------------------------------------------

    def keyPressEvent(self, event) -> None:
        # `Space` pages the document, from this tab as much as from the Pages tab (M91.4) — the
        # rule and the reason live in MainWindow.keyPressEvent; leaving it unaccepted is what
        # carries it there. Everything else is the tree's own.
        if event.key() == Qt.Key.Key_Space:
            event.ignore()
            return
        super().keyPressEvent(event)

    def _on_current_item_changed(self, item, _previous) -> None:
        # Fires for clicks *and* keyboard moves; suppressed while we highlight programmatically.
        if not self._syncing and item is not None:
            self._activate(item)

    def _on_item_clicked(self, item, _column: int) -> None:
        self._activate(item)

    def _activate(self, item) -> None:
        self.entryActivated.emit(item.data(0, _PAGE_ROLE), item.data(0, _SPOT_ROLE))

    def set_current(self, page_index: int) -> None:
        """Highlight the entry the visible page falls under — the nearest entry at or before
        ``page_index`` (last one wins on ties) — without triggering a jump back. No entry at or
        before it (or an emptied outline) clears the highlight. A reorder can leave the remapped
        entries non-monotonic in page, hence nearest-by-page rather than last-in-reading-order."""
        self._current_page = page_index
        best = None
        best_page = -1
        for page0, item in self._entries:
            if best_page <= page0 <= page_index:
                best, best_page = item, page0
        current = self.currentItem()
        if best is not None and current is not None and current.data(0, _PAGE_ROLE) == best_page:
            return  # the highlighted entry (e.g. just clicked) already matches — don't reshuffle ties
        self._syncing = True
        self.setCurrentItem(best)  # None clears the selection
        if best is not None:
            self.scrollToItem(best)
        self._syncing = False
