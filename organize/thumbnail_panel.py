"""Thumbnail sidebar bound to the VirtualDocument's ``ordered[]`` (PLAN.md, Viewer).

In M2 (View mode) it is a jump-to-page strip that also highlights the page currently in view.
In M4 the same panel grows Organize-mode behaviours (drag-reorder, cross-window drag, delete);
keeping it bound to ``ordered[]`` now means both modes read one model.
"""

from __future__ import annotations

import json
import os

import pymupdf as fitz
from PySide6.QtCore import QByteArray, QMimeData, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QDrag,
    QFont,
    QIcon,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QTransform,
)
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem

from model.edit_engine import PyMuPDFEngine
from model.virtual_document import IMAGE_EXTENSIONS, VirtualDocument

_THUMB_W = 140  # target thumbnail width in px
_DRAG_W = 96    # width of the page image carried under the cursor while dragging
_ACCENT = QColor(0, 120, 215)  # drop-marker + count-badge colour
# Custom drag payload so a drop knows the source document + rows even across windows; the plain
# QListWidget item-move MIME can't cross processes/windows and InternalMove can't leave the view.
_PAGES_MIME = "application/x-pdfproj-pages"


class ThumbnailPanel(QListWidget):
    """Page thumbnails bound to ordered[]: click jumps (View), drag reorders + multi-select
    delete (Organize). Both modes read the one model."""

    pageActivated = Signal(int)
    pagesDropped = Signal(object, object, int)  # (source_key | None, rows, before-index)
    filesDropped = Signal(object, int)          # (list[pdf/image paths], before-index) — from Explorer
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
        # We paint our own insertion marker (see paintEvent) — clearer than the default hairline,
        # and the built-in one is unreliable for our custom-MIME drag anyway.
        self.setDropIndicatorShown(False)
        self._drop_row: int | None = None  # slot the marker is drawn before, while a drag is over
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

    def _drag_pixmap(self, rows: list[int]) -> QPixmap:
        """A page image to carry under the cursor: the first grabbed page, with a stacked-page hint
        and an "N" count badge for a multi-page drag, so it's obvious a page is being dragged."""
        size = QSize(_DRAG_W, int(_DRAG_W * 1.4))
        thumb = self.item(rows[0]).icon().pixmap(size)
        if thumb.isNull():
            thumb = QPixmap(size)
            thumb.fill(QColor("white"))
        n = len(rows)
        offset = 6 if n > 1 else 0  # a single offset page behind hints at a stack
        pad = 2
        canvas = QPixmap(thumb.width() + offset + pad * 2, thumb.height() + offset + pad * 2)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        border = QPen(QColor(110, 110, 110))
        if offset:
            painter.setBrush(QColor(245, 245, 245))
            painter.setPen(border)
            painter.drawRect(QRectF(pad + offset, pad + offset, thumb.width(), thumb.height()))
        painter.drawPixmap(pad, pad, thumb)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(border)
        painter.drawRect(QRectF(pad, pad, thumb.width(), thumb.height()))
        if n > 1:
            d = 18
            bx, by = canvas.width() - d - 1, 1
            badge = QRectF(bx, by, d, d)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_ACCENT)
            painter.drawEllipse(badge)
            painter.setPen(QColor("white"))
            font = QFont()
            font.setBold(True)
            font.setPointSize(9)
            painter.setFont(font)
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(n))
        painter.end()
        return canvas

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
        pixmap = self._drag_pixmap(rows)
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, 12))  # page hangs just below the cursor
        drag.exec(Qt.DropAction.MoveAction | Qt.DropAction.CopyAction, Qt.DropAction.MoveAction)

    def _dropped_file_paths(self, mime) -> list[str]:
        """Local **PDF or image** files in a drag's URLs (e.g. dragged from Explorer), in URL order,
        else ``[]``. Images (M35) insert as a page just like a dropped PDF (converted at open)."""
        paths = []
        for url in mime.urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                ext = os.path.splitext(path)[1].lower()
                if (ext == ".pdf" or ext in IMAGE_EXTENSIONS) and os.path.isfile(path):
                    paths.append(path)
        return paths

    def _accepts(self, mime) -> bool:
        return mime.hasFormat(_PAGES_MIME) or bool(self._dropped_file_paths(mime))

    def dragEnterEvent(self, event) -> None:
        if self._accepts(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        # Explicitly accept our payload (internal page drag OR an Explorer .pdf) on every move so
        # the cursor reads "droppable" instead of the blocked icon (the default view logic rejects
        # a non-item-model MIME), and track the slot so paintEvent shows where it will land.
        if self._accepts(event.mimeData()):
            self._set_drop_row(self._drop_before_index(event))
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dragLeaveEvent(self, event) -> None:
        self._set_drop_row(None)
        super().dragLeaveEvent(event)

    def _set_drop_row(self, row: int | None) -> None:
        if row != self._drop_row:
            self._drop_row = row
            self.viewport().update()  # repaint the insertion marker

    def dropEvent(self, event) -> None:
        md = event.mimeData()
        before = self._drop_before_index(event)
        self._set_drop_row(None)

        if md.hasFormat(_PAGES_MIME):  # internal page reorder / cross-window page drag
            try:
                payload = json.loads(bytes(md.data(_PAGES_MIME).data()).decode("utf-8"))
                rows = [int(r) for r in payload.get("rows", [])]
            except (ValueError, TypeError, UnicodeDecodeError):
                return
            if rows:
                self.pagesDropped.emit(payload.get("source"), rows, before)
            # The model command + repopulate apply the change; mark the drop a Copy so Qt's own
            # move machinery never removes rows behind our back.
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            return

        files = self._dropped_file_paths(md)  # PDF(s) / image(s) dragged in from Explorer
        if files:
            self.filesDropped.emit(files, before)
            event.acceptProposedAction()
            return

        super().dropEvent(event)

    # ---- insertion marker -------------------------------------------------------

    def _drop_marker_line(self) -> tuple[int, int, int] | None:
        """``(y, x0, x1)`` for the insertion line at the pending drop slot, or None."""
        if self._drop_row is None or self.count() == 0:
            return None
        gap = self.spacing() // 2
        if self._drop_row >= self.count():
            rect = self.visualItemRect(self.item(self.count() - 1))
            y = rect.bottom() + gap
        else:
            rect = self.visualItemRect(self.item(self._drop_row))
            y = rect.top() - gap
        return y, rect.left(), rect.right()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)  # items first
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_current_marker(painter)  # prominent "you are here" ring
        self._paint_drop_marker(painter)
        painter.end()

    def _paint_current_marker(self, painter) -> None:
        """A bold accent ring + tinted fill around the current page — the default item-selection
        highlight is too faint to read at a glance in the sidebar."""
        row = self.currentRow()
        if not (0 <= row < self.count()):
            return
        rect = self.visualItemRect(self.item(row)).adjusted(1, 1, -1, -1)
        fill = QColor(_ACCENT)
        fill.setAlpha(38)
        painter.setBrush(fill)
        pen = QPen(_ACCENT)
        pen.setWidth(3)
        painter.setPen(pen)
        painter.drawRoundedRect(QRectF(rect), 5, 5)

    def _paint_drop_marker(self, painter) -> None:
        line = self._drop_marker_line()
        if line is None:
            return
        y, x0, x1 = line
        pen = QPen(_ACCENT)
        pen.setWidth(3)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(x0, y, x1, y)
        painter.drawLine(x0, y - 4, x0, y + 4)  # end ticks for visibility
        painter.drawLine(x1, y - 4, x1, y + 4)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            rows = self.selected_rows()
            if rows:
                self.deleteRequested.emit(rows)
                event.accept()
                return
        super().keyPressEvent(event)

    def _edited_render(self) -> "fitz.Document | None":
        """The edits-applied output document (rotation + redactions + annotations + form fills baked
        in) when the document has **any** page edit, else ``None`` so clean pages render straight from
        the source — the fast path. Shares the ``render_output`` path that print / save use (M25), so
        a thumbnail shows exactly what a Save would write. The caller owns it and must close it.
        A malformed edit returns ``None`` rather than blanking the sidebar — the source render shows.
        """
        # The third term keeps a document on the edits-applied path even after its last model
        # annotation is removed: our marks are still baked into the source bytes, so the fast source
        # render would show a just-deleted highlight / text-box until the next save. render_output
        # strips our baked marks and re-adds only what the model still holds (M31).
        has_edits = (
            bool(self._vdoc.form_values)
            or any(r.annotations for r in self._vdoc.ordered)
            or self._vdoc.has_baked_pdfproj_annotations()
        )
        if not has_edits:
            return None
        try:
            return PyMuPDFEngine().render_output(self._vdoc)
        except Exception:
            return None

    def _thumbnail(self, index: int, baked=None) -> QIcon:
        """Render page ``index`` to a thumbnail icon. ``baked`` is the edits-applied
        :meth:`_edited_render` document to take the (already rotation/edit-baked) page from; ``None``
        renders the raw source page, applying any rotation override via transform (the fast path)."""
        ref = self._vdoc.ordered[index]
        if baked is not None:
            try:
                page = baked[index]  # page i == ordered[i], with rotation + every edit baked in
                zoom = _THUMB_W / max(1.0, page.rect.width)
                pm = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                img = QImage(pm.samples, pm.width, pm.height, pm.stride, QImage.Format.Format_RGB888)
                return QIcon(QPixmap.fromImage(img.copy()))
            except Exception:
                pass  # fall through to a plain source render
        page = self._vdoc.sources[ref.source_id][ref.source_page_index]
        # Size by the FINAL displayed width (after any rotation override), not the page's native
        # rotated width: the override is applied as a pixmap rotation below, so a page whose override
        # orientation differs from its baked-in /Rotate — e.g. a page saved rotated then rotated back
        # to portrait — would otherwise be scaled to the wrong width and render narrower than its
        # neighbours. (The baked path above is already correct: render_output bakes /Rotate, so the
        # baked page.rect is the displayed size.)
        final_rot = page.rotation if ref.rotation_override is None else ref.rotation_override
        mediabox = page.mediabox
        displayed_w = mediabox.height if final_rot % 180 else mediabox.width
        zoom = _THUMB_W / max(1.0, displayed_w)
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
        """(Re)build the thumbnail list from ``ordered[]``, each thumbnail reflecting the page's
        **current edited state** (annotations / redactions / fills), so the sidebar matches the page
        and the saved output. Called after every edit (``MainWindow._on_doc_changed``).

        Preserves the current-page marker across the rebuild: ``clear()`` would otherwise reset the
        current row to -1, so an edit (which repopulates) would drop the highlight even though the
        page didn't change. We capture the row first and restore it if it still exists.
        """
        current = self.currentRow()
        self._syncing = True
        self.clear()
        baked = self._edited_render()  # built once per rebuild; None when the doc has no edits
        try:
            for i in range(self._vdoc.page_count):
                item = QListWidgetItem(self._thumbnail(i, baked), str(i + 1))
                item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
                self.addItem(item)
        finally:
            if baked is not None:
                baked.close()
        if 0 <= current < self.count():
            self.setCurrentRow(current)
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
