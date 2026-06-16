"""Thumbnail sidebar bound to the VirtualDocument's ``ordered[]`` (PLAN.md, Viewer).

In M2 (View mode) it is a jump-to-page strip that also highlights the page currently in view.
In M4 the same panel grows Organize-mode behaviours (drag-reorder, cross-window drag, delete);
keeping it bound to ``ordered[]`` now means both modes read one model.
"""

from __future__ import annotations

import json

import pymupdf as fitz
from PySide6.QtCore import QByteArray, QMimeData, QSize, Qt, Signal
from PySide6.QtGui import QDrag, QIcon, QImage, QPixmap, QTransform
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem

from model.virtual_document import VirtualDocument

_THUMB_W = 140  # target thumbnail width in px
# Custom drag payload so a drop knows the source document + rows even across windows; the plain
# QListWidget item-move MIME can't cross processes/windows and InternalMove can't leave the view.
_PAGES_MIME = "application/x-pdfproj-pages"


class ThumbnailPanel(QListWidget):
    """Page thumbnails bound to ordered[]: click jumps (View), drag reorders + multi-select
    delete (Organize). Both modes read the one model."""

    pageActivated = Signal(int)
    pagesDropped = Signal(object, object, int)  # (source_key | None, rows, before-index)
    deleteRequested = Signal(object)            # (sorted rows)

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

        # Organize: multi-select + drag-and-drop. DragDrop (not InternalMove) so pages can be
        # dragged across windows too; we carry a custom MIME payload and drive the model via a
        # command + repopulate, so Qt never moves/removes items itself (see startDrag/dropEvent).
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        # Item-view drag events are delivered to the VIEWPORT, not the view widget. setAcceptDrops
        # above (and setDragDropMode) only flag the view, so the viewport silently rejects drops
        # and the cursor stays "blocked" — enable drops on the viewport explicitly.
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        # Identity of this panel's document (set by MainWindow); lets a drop distinguish an
        # internal reorder from a cross-window copy. None until assigned.
        self.source_key: str | None = None

        self.currentRowChanged.connect(self._on_row_changed)
        self.populate()

    def selected_rows(self) -> list[int]:
        return sorted(i.row() for i in self.selectedIndexes())

    def _drop_before_index(self, event) -> int:
        """Translate a drop position into an 'insert before this row' index.

        Thumbnails stack vertically, so the slot is chosen by the drop's *y*: insert before the
        first page whose vertical centre sits below the cursor. A drop in the gap between two
        pages (or above the very first page) then lands exactly there, and a drop below the last
        page appends. The earlier horizontal-midpoint test was unintuitive in a single column —
        it let the cursor's left/right half, not its height, pick the slot.
        """
        y = event.position().toPoint().y()
        for row in range(self.count()):
            if y < self.visualItemRect(self.item(row)).center().y():
                return row
        return self.count()

    def startDrag(self, supported_actions) -> None:
        # Build our own drag carrying (source_key, rows). We never call super().startDrag, so
        # QAbstractItemView never moves/removes items — the model command + repopulate own the
        # result. DragDrop mode + this explicit MIME also let the drop land in another window.
        rows = self.selected_rows()
        if not rows:
            return
        mime = QMimeData()
        payload = json.dumps({"source": self.source_key, "rows": rows}).encode("utf-8")
        mime.setData(_PAGES_MIME, QByteArray(payload))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction | Qt.DropAction.CopyAction, Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(_PAGES_MIME):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        # Explicitly accept our payload on every move so the cursor reads "droppable" instead of
        # the blocked icon (the default view logic rejects a non-item-model MIME like ours).
        if event.mimeData().hasFormat(_PAGES_MIME):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        md = event.mimeData()
        if not md.hasFormat(_PAGES_MIME):
            super().dropEvent(event)
            return
        try:
            payload = json.loads(bytes(md.data(_PAGES_MIME).data()).decode("utf-8"))
            rows = [int(r) for r in payload.get("rows", [])]
        except (ValueError, TypeError, UnicodeDecodeError):
            return
        before = self._drop_before_index(event)
        if rows:
            self.pagesDropped.emit(payload.get("source"), rows, before)
        # The model command + repopulate apply the change; mark the drop a Copy so Qt's own move
        # machinery never removes rows behind our back.
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

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
            pixmap = QPixmap.fromImage(img.copy())
            if ref.rotation_override is not None:
                extra = (ref.rotation_override - page.rotation) % 360
                if extra:
                    pixmap = pixmap.transformed(QTransform().rotate(extra))
            return QIcon(pixmap)
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
