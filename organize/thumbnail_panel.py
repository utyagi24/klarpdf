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

_DRAG_W = 96    # width of the page image carried under the cursor while dragging
_ACCENT = QColor(0, 120, 215)  # drop-marker + count-badge colour
# Thumbnails scale with the sidebar width (Preview-style): the displayed icon width tracks the bar
# between these bounds, and the bar itself is bounded (min/max width) so widening grows the thumbnail
# up to a cap instead of adding dead space. Pages render once at the MAX width and Qt scales the
# pixmap down, so resizing stays sharp and cheap (no re-render).
_THUMB_MIN_W = 110
_THUMB_MAX_W = 240
_SIDEBAR_W = 210      # default sidebar width (a comfortable mid-size thumbnail)
_SIDEBAR_CHROME = 36  # scrollbar + frame + the 2*spacing margin, added around the thumbnail width
# Custom drag payload so a drop knows the source document + rows even across windows; the plain
# QListWidget item-move MIME can't cross processes/windows and InternalMove can't leave the view.
_PAGES_MIME = "application/x-klarpdf-pages"


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
        self.setIconSize(QSize(_THUMB_MAX_W, round(_THUMB_MAX_W * 1.4)))  # adjusted to the bar on show
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setSpacing(8)
        self.setUniformItemSizes(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Single column; the thumbnails scale with the bar (see _apply_thumb_size), and the bar's
        # width is bounded so widening grows the thumbnail up to a cap rather than leaving dead space.
        self.setMinimumWidth(_THUMB_MIN_W + _SIDEBAR_CHROME)
        self.setMaximumWidth(_THUMB_MAX_W + _SIDEBAR_CHROME)

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

        # Lazy thumbnails: items get a cheap placeholder up front and their real page is rasterised
        # only when scrolled into view, so opening a many-page document doesn't render every page.
        self._rendered: set[int] = set()            # rows whose real thumbnail has been rendered
        self._baked = None                          # edits-applied render doc, kept open for lazy use
        self._layout_key: list = []                 # which page sits in which row (see _carryable_icons)
        self.verticalScrollBar().valueChanged.connect(self._render_visible_thumbs)

        self.currentRowChanged.connect(self._on_row_changed)
        # Repaint the whole viewport on any selection change so every selection marker (drawn in
        # paintEvent) updates — Qt otherwise only invalidates individual changed item rects.
        self.itemSelectionChanged.connect(self.viewport().update)
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
        self._ensure_rendered(rows[0])  # the dragged thumbnail must be the real page, not a placeholder
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
        self._paint_selection_markers(painter)  # clearly mark every selected page
        self._paint_current_marker(painter)  # prominent "you are here" ring (on top of selection)
        self._paint_drop_marker(painter)
        painter.end()

    def _paint_selection_markers(self, painter) -> None:
        """A clear accent fill + border around **every** selected page. Qt's default multi-select
        highlight is too faint to tell which pages are in the selection; this makes it obvious. The
        current page is skipped here — its bolder ring (painted on top) keeps it distinct within a
        multi-page selection."""
        current = self.currentRow()
        fill = QColor(_ACCENT)
        fill.setAlpha(60)
        pen = QPen(_ACCENT)
        pen.setWidth(2)
        for index in self.selectedIndexes():
            row = index.row()
            if row == current:
                continue
            rect = self.visualItemRect(self.item(row)).adjusted(1, 1, -1, -1)
            painter.setBrush(fill)
            painter.setPen(pen)
            painter.drawRoundedRect(QRectF(rect), 5, 5)

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
            or any(r.annotations or r.crop_override is not None for r in self._vdoc.ordered)
            or self._vdoc.has_baked_klarpdf_annotations()
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
                zoom = _THUMB_MAX_W / max(1.0, page.rect.width)
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
        # CropBox, not MediaBox: it is what get_pixmap renders (equal for the normal page; a
        # pre-cropped source otherwise gets scaled to the wrong width). A crop *override* never
        # reaches this fast path — it counts as an edit, so those pages render from the bake.
        cropbox = page.cropbox
        displayed_w = cropbox.height if final_rot % 180 else cropbox.width
        zoom = _THUMB_MAX_W / max(1.0, displayed_w)
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

    def _thumb_dims(self, index: int) -> tuple[int, int]:
        """The displayed thumbnail size (w, h) for page ``index`` — from the mediabox + rotation, so
        it matches what :meth:`_thumbnail` renders **without** rasterising. Lets a placeholder reserve
        the right space so the layout doesn't shift when the real thumbnail arrives."""
        ref = self._vdoc.ordered[index]
        page = self._vdoc.sources[ref.source_id][ref.source_page_index]
        final_rot = page.rotation if ref.rotation_override is None else ref.rotation_override
        if ref.crop_override is not None:  # placeholder matches the cropped page's shape (M48)
            x0, y0, x1, y1 = ref.crop_override
            w, h = x1 - x0, y1 - y0
        else:
            w, h = page.cropbox.width, page.cropbox.height
        disp_w, disp_h = (h, w) if final_rot % 180 else (w, h)
        return _THUMB_MAX_W, max(1, round(_THUMB_MAX_W * disp_h / max(1.0, disp_w)))

    def _placeholder_icon(self, index: int) -> QIcon:
        """A cheap blank-page icon at the correct size, shown until the real thumbnail is rendered."""
        w, h = self._thumb_dims(index)
        pm = QPixmap(w, h)
        pm.fill(QColor(0xEC, 0xEC, 0xEC))
        return QIcon(pm)

    def _render_one(self, row: int) -> None:
        self.item(row).setIcon(self._thumbnail(row, self._baked))
        self._rendered.add(row)

    def _ensure_rendered(self, row: int) -> None:
        """Render row ``row``'s real thumbnail now if it's still a placeholder (used by drag)."""
        if 0 <= row < self.count() and row not in self._rendered:
            self._render_one(row)

    def _render_visible_thumbs(self) -> None:
        """Render the real thumbnail for every item currently in (or near) the viewport that is still
        a placeholder. Driven by scroll / resize / show, so only pages the user actually looks at are
        rasterised — opening a 320-page document renders ~a screenful, not all 320."""
        if len(self._rendered) == self.count():
            return
        view_rect = self.viewport().rect()
        if view_rect.isEmpty():
            return  # not laid out yet (e.g. before first show) — render happens on showEvent
        for row in range(self.count()):
            if row not in self._rendered and view_rect.intersects(self.visualItemRect(self.item(row))):
                self._render_one(row)

    def _page_layout_key(self) -> list:
        """What decides *which* page sits in each row and how it is framed — everything about a
        `PageRef` except its annotations. Two populates with the same key have the same pages in the
        same rows, so a rendered thumbnail still depicts its row's page."""
        return [(ref.source_id, ref.source_page_index, ref.rotation_override, ref.crop_override)
                for ref in self._vdoc.ordered]

    def _carryable_icons(self) -> dict:
        """Already-rendered icons that :meth:`populate` may reuse as each row's starting image.

        Rendering is lazy — only rows in the viewport are rasterised — but ``populate`` runs on
        **every** edit and used to reset every row to a blank grey placeholder. So a single edit
        blanked the whole sidebar and only the handful of rows on screen came back; anything scrolled
        away stayed an empty rectangle until the user happened to scroll to it. Following the edit
        can itself scroll the list (a watermark applied to all pages ends on the last page), so the
        rows the user was looking at were exactly the ones that went blank.

        Carrying the old icons keeps every row showing its page. A carried icon is *stale in its
        annotations* — the edit is not in it yet — which is why the row is still marked unrendered
        and re-rasterises on sight. A **structural** edit gets no carry: the pages have moved, so
        row N's old image is a different page, and an honest placeholder beats a confident lie.
        """
        key = self._page_layout_key()
        if key != self._layout_key or not self._rendered:
            return {}
        return {row: self.item(row).icon() for row in self._rendered if row < self.count()}

    def _close_baked(self) -> None:
        if self._baked is not None:
            self._baked.close()
            self._baked = None

    def populate(self) -> None:
        """(Re)build the thumbnail list from ``ordered[]``. Items get a placeholder up front and are
        rendered **lazily** when scrolled into view (and reflect the page's current edited state via
        the ``_edited_render`` bake, which is kept open for that lazy rendering). Called after every
        edit (``MainWindow._on_doc_changed``).

        Preserves the current-page marker across the rebuild: ``clear()`` would otherwise reset the
        current row to -1, so an edit (which repopulates) would drop the highlight even though the
        page didn't change. We capture the row first and restore it if it still exists.
        """
        current = self.currentRow()
        carried = self._carryable_icons()
        self._syncing = True
        self.clear()
        self._close_baked()
        self._baked = self._edited_render()  # kept open for lazy rendering; closed on next populate/close
        self._rendered = set()
        for i in range(self._vdoc.page_count):
            item = QListWidgetItem(carried.get(i) or self._placeholder_icon(i), str(i + 1))
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.addItem(item)
        self._layout_key = self._page_layout_key()
        if 0 <= current < self.count():
            self.setCurrentRow(current)
        self._syncing = False
        self._render_visible_thumbs()  # render whatever is already on screen (nothing if not yet shown)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_thumb_size()
        self._render_visible_thumbs()

    def sizeHint(self) -> QSize:
        # Snug default width on first show (the bare QListWidget hint is much wider). User-resizable
        # within the min/max bounds; the thumbnail scales to fill whatever width is chosen.
        return QSize(_SIDEBAR_W, super().sizeHint().height())

    def _apply_thumb_size(self) -> None:
        """Scale the displayed thumbnail to the current bar width (Preview-style), within the bounds.
        The pixmap is rendered at _THUMB_MAX_W, so this only resizes the icon — no re-render.

        Size off a scrollbar-*invariant* width: ``contentsRect`` is the inner width that includes the
        vertical scrollbar's slot, and we subtract the scrollbar extent whether or not the bar is
        currently shown (``viewport().width()`` excludes it only while visible). This breaks a flicker
        loop — with a narrow bar the icon tracks the width, so were the size to depend on the scrollbar,
        the bar appearing would shrink the icon → content fits → bar hides → icon grows → content
        overflows → bar reappears, oscillating when the window's bottom edge sits right at the last
        thumbnail. (At max width the icon is clamped to _THUMB_MAX_W and never tracked the width, which
        is why widening the bar hid the loop; reserving the slot fixes it at every width.)"""
        sbw = self.verticalScrollBar().sizeHint().width()
        avail = self.contentsRect().width() - sbw - 2 * self.spacing()
        w = max(_THUMB_MIN_W, min(_THUMB_MAX_W, avail))
        if self.iconSize().width() != w:
            self.setIconSize(QSize(w, round(w * 1.4)))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_thumb_size()  # thumbnails grow/shrink with the bar (Preview-style)
        self._render_visible_thumbs()

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
