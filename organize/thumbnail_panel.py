"""Thumbnail sidebar bound to the VirtualDocument's ``ordered[]`` (PLAN.md, Viewer).

In M2 (View mode) it is a jump-to-page strip that also highlights the page currently in view.
In M4 the same panel grows Organize-mode behaviours (drag-reorder, cross-window drag, delete);
keeping it bound to ``ordered[]`` now means both modes read one model.
"""

from __future__ import annotations

import pymupdf as fitz
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import QListWidget, QListWidgetItem

from model.virtual_document import VirtualDocument

_THUMB_W = 140  # target thumbnail width in px


class ThumbnailPanel(QListWidget):
    """A vertical list of page thumbnails; click to jump, reflects the current page."""

    pageActivated = Signal(int)

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

        self.currentRowChanged.connect(self._on_row_changed)
        self.populate()

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
