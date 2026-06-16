"""Thumbnail sidebar bound to the VirtualDocument's ``ordered[]`` (PLAN.md, Viewer).

In M2 (View mode) it is a jump-to-page strip that also highlights the page currently in view.
In M4 the same panel grows Organize-mode behaviours (drag-reorder, cross-window drag, delete);
keeping it bound to ``ordered[]`` now means both modes read one model.
"""

from __future__ import annotations

import pymupdf as fitz
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem

from model.virtual_document import VirtualDocument

_THUMB_W = 140  # target thumbnail width in px


class ThumbnailPanel(QListWidget):
    """Page thumbnails bound to ordered[]: click jumps (View), drag reorders + multi-select
    delete (Organize). Both modes read the one model."""

    pageActivated = Signal(int)
    reorderRequested = Signal(object, int)  # (sorted source rows, before-index)
    deleteRequested = Signal(object)        # (sorted rows)

    def __init__(self, vdoc: VirtualDocument, parent=None) -> None:
        super().__init__(parent)
        self._vdoc = vdoc
        self._syncing = False  # guard against current→highlight→jump feedback loops

        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setIconSize(QSize(_THUMB_W, int(_THUMB_W * 1.4)))
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setSpacing(8)
        self.setUniformItemSizes(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # Organize: multi-select + drag-reorder. We intercept the drop and drive the model via
        # a command (then repopulate), so Qt's own item move is suppressed.
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        self.currentRowChanged.connect(self._on_row_changed)
        self.populate()

    def selected_rows(self) -> list[int]:
        return sorted(i.row() for i in self.selectedIndexes())

    def _drop_before_index(self, event) -> int:
        """Translate a drop position into a 'before this row' insertion index."""
        pos = event.position().toPoint()
        item = self.itemAt(pos)
        if item is None:
            return self.count()
        row = self.row(item)
        rect = self.visualItemRect(item)
        # Past the item's horizontal midpoint → insert after it (grid flows left-to-right).
        if pos.x() > rect.center().x():
            row += 1
        return row

    def startDrag(self, supported_actions) -> None:
        # Force CopyAction so QAbstractItemView never auto-removes the dragged rows after the
        # drop; the model command + repopulate are the single source of truth for the order.
        super().startDrag(Qt.DropAction.CopyAction)

    def dropEvent(self, event) -> None:
        rows = self.selected_rows()
        before = self._drop_before_index(event)
        if rows:
            self.reorderRequested.emit(rows, before)
        event.accept()  # model + repopulate handle the actual move; suppress Qt's item shuffle

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            rows = self.selected_rows()
            if rows:
                self.deleteRequested.emit(rows)
                event.accept()
                return
        super().keyPressEvent(event)

    def _thumbnail(self, index: int) -> QIcon:
        ref = self._vdoc.ordered[index]
        page = self._vdoc.sources[ref.source_id][ref.source_page_index]
        zoom = _THUMB_W / max(1.0, page.rect.width)
        try:
            pm = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            img = QImage(pm.samples, pm.width, pm.height, pm.stride, QImage.Format.Format_RGB888)
            return QIcon(QPixmap.fromImage(img.copy()))
        except Exception:
            return QIcon()

    def populate(self) -> None:
        """(Re)build the thumbnail list from ``ordered[]``."""
        self._syncing = True
        self.clear()
        for i in range(self._vdoc.page_count):
            item = QListWidgetItem(self._thumbnail(i), str(i + 1))
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.addItem(item)
        self._syncing = False

    def set_current(self, index: int) -> None:
        """Highlight ``index`` without triggering a jump back to the view."""
        if 0 <= index < self.count() and index != self.currentRow():
            self._syncing = True
            self.setCurrentRow(index)
            self.scrollToItem(self.item(index))
            self._syncing = False

    def _on_row_changed(self, row: int) -> None:
        if not self._syncing and row >= 0:
            self.pageActivated.emit(row)
