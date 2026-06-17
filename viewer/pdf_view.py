"""Continuous-scroll PDF viewer (PLAN.md, Viewer — Option B).

A ``QGraphicsView``/``QGraphicsScene`` lays every page of the ``VirtualDocument`` out in a
single vertical strip. Page *geometry* is cheap (from ``page.rect``, no rendering), so the whole
strip is laid out up front; page *pixels* are rendered lazily — only pages intersecting the
viewport (plus a small prefetch) get a PyMuPDF pixmap, cached in a bounded LRU keyed by
``(index, zoom, rotation)``. Zoom (incl. fit-width/fit-page) and 90° view rotation are scalar
re-layouts.

This is the M2 view surface: render / scroll / zoom / fit / rotate / current-page tracking.
Text selection (M3) and drag-reorder (M4) build on the same scene later.
"""

from __future__ import annotations

from collections import OrderedDict

import pymupdf as fitz
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView

from model.virtual_document import VirtualDocument

_PAGE_GAP = 14          # px between pages in the strip
_PREFETCH = 2           # pages to render above/below the viewport
_CACHE_LIMIT = 48       # max rendered pixmaps held at once
_MIN_ZOOM, _MAX_ZOOM = 0.1, 8.0
_ZOOM_STEP = 1.25


class PdfView(QGraphicsView):
    """Vertical continuous-scroll renderer over a VirtualDocument."""

    currentPageChanged = Signal(int)
    zoomChanged = Signal(float)  # emitted whenever the zoom factor changes (1.0 == 100%)

    def __init__(self, vdoc: VirtualDocument, parent=None) -> None:
        super().__init__(parent)
        self._vdoc = vdoc
        self._zoom = 1.0
        self._rotation = 0  # view rotation in degrees: 0/90/180/270
        self._cache: "OrderedDict[tuple, QPixmap]" = OrderedDict()
        # Per-source fresh copies with form fills applied, for rendering filled pages (M14). Keyed
        # by source id; rebuilt after each edit (reload clears them). Kept separate from the shared
        # read-only sources, and NOT built via insert_pdf — repeated insert_pdf from one source
        # drops widgets after the first call, so we use a fresh source copy and apply values to it.
        self._fill_docs: dict[str, "fitz.Document"] = {}
        self._pages: list[dict] = []   # per page: {bg, pix, x, y, w, h}
        self._current = 0
        # Overlay controllers (set by MainWindow): text selection + search (M3) + form fill (M14).
        # They own their highlight items and expose repaint(), called after every scene rebuild.
        self.selection = None
        self.search = None
        self.form = None

        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QBrush(QColor(0x30, 0x30, 0x30)))
        # Left-drag selects text (M3), so no hand-drag panning; scroll via wheel/scrollbars.
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)

        self._build_scene()
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)

    # ---- natural geometry -------------------------------------------------------

    def _page_extra(self, index: int) -> int:
        """Extra degrees to spin the natively-rendered page to reach its override (0 if none).

        ``get_pixmap`` already renders a page at its own ``page.rotation``; a per-page override is
        an *absolute* target angle, so the extra spin on top is ``override - native``.
        """
        ref = self._vdoc.ordered[index]
        if ref.rotation_override is None:
            return 0
        native = self._vdoc.sources[ref.source_id][ref.source_page_index].rotation
        return (ref.rotation_override - native) % 360

    def _natural_size(self, index: int) -> tuple[float, float]:
        """Unscaled page size in points, with per-page rotation + view rotation axis swaps."""
        ref = self._vdoc.ordered[index]
        rect = self._vdoc.sources[ref.source_id][ref.source_page_index].rect
        w, h = rect.width, rect.height
        total = (self._page_extra(index) + self._rotation) % 360
        return (h, w) if total in (90, 270) else (w, h)

    def _build_scene(self) -> None:
        scene = self.scene()
        scene.clear()
        self._pages.clear()
        page_pen = QPen(QColor(0x80, 0x80, 0x80))
        page_brush = QBrush(QColor(0xFF, 0xFF, 0xFF))

        widest = max((self._natural_size(i)[0] for i in range(self._vdoc.page_count)), default=1.0)
        widest *= self._zoom
        y = float(_PAGE_GAP)
        for i in range(self._vdoc.page_count):
            w_pt, h_pt = self._natural_size(i)
            w, h = w_pt * self._zoom, h_pt * self._zoom
            x = (widest - w) / 2.0
            # Geometry goes in the item POSITION (local rect at origin), so the child pixmap —
            # placed at the parent's (0,0) — inherits the page's scene position. Encoding x/y in
            # the rect instead leaves the item at (0,0) and piles every pixmap at the origin.
            bg = QGraphicsRectItem(QRectF(0, 0, w, h))
            bg.setPos(x, y)
            bg.setPen(page_pen)
            bg.setBrush(page_brush)
            scene.addItem(bg)
            pix = QGraphicsPixmapItem(bg)  # child of bg → shares its position
            pix.setPos(0, 0)
            self._pages.append({"bg": bg, "pix": pix, "x": x, "y": y, "w": w, "h": h})
            y += h + _PAGE_GAP

        scene.setSceneRect(0, 0, widest + 2 * _PAGE_GAP, y)
        self._render_visible()
        # scene.clear() above discarded any overlay items; repaint them from logical state.
        for overlay in (self.form, self.selection, self.search):
            if overlay is not None:
                overlay.repaint()

    # ---- overlay geometry helpers (used by selection + search) ------------------

    def page_and_local_at(self, scene_pt) -> tuple[int | None, "QPointF | None"]:
        """Map a scene point to ``(page_index, point-in-page-points)``.

        Returns the page whose vertical band contains the point (x is not constrained, so a
        drag that strays past a page's edge still resolves to a word on the nearest line).
        ``(None, None)`` when the point falls in a gap above/below all pages. Only valid at
        rotation 0 — callers gate on it.
        """
        for i, p in enumerate(self._pages):
            if p["y"] <= scene_pt.y() <= p["y"] + p["h"]:
                local = QPointF((scene_pt.x() - p["x"]) / self._zoom, (scene_pt.y() - p["y"]) / self._zoom)
                return i, local
        return None, None

    def scene_rect_for_box(self, page_index: int, box: tuple) -> QRectF:
        """Map a page-space box (points, x0,y0,x1,y1) to a scene rect (rotation 0)."""
        p = self._pages[page_index]
        x0, y0, x1, y1 = box
        z = self._zoom
        return QRectF(p["x"] + x0 * z, p["y"] + y0 * z, (x1 - x0) * z, (y1 - y0) * z)

    def ensure_box_visible(self, page_index: int, box: tuple) -> None:
        self.ensureVisible(self.scene_rect_for_box(page_index, box), 60, 60)

    # ---- mouse → text selection -------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            scene_pt = self.mapToScene(event.position().toPoint())
            # A click on a form field starts filling it; otherwise it begins a text selection.
            if self.form is not None and self.form.handle_press(scene_pt):
                event.accept()
                return
            if self.selection is not None and self.selection.begin(scene_pt):
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self.selection is not None and event.button() == Qt.MouseButton.LeftButton:
            if self.selection.select_word_at(self.mapToScene(event.position().toPoint())):
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.selection is not None and self.selection.active:
            self.selection.update_to(self.mapToScene(event.position().toPoint()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.selection is not None and self.selection.active:
            self.selection.finish()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ---- rendering --------------------------------------------------------------

    def _filled_source_page(self, ref):
        """The source page to render for ``ref`` — a fills-applied fresh copy if this page has a
        filled field, else None (caller uses the shared source page).

        A fresh in-memory source copy (``VirtualDocument.fresh_source``) with ``apply_form_values``
        applied directly (no ``insert_pdf``) renders the entered values without mutating the shared
        source AND avoids PyMuPDF's repeated-``insert_pdf`` widget loss. The fresh doc is cached per
        source and dropped by :meth:`reload` after every edit.
        """
        values = self._vdoc.form_values
        if not values:
            return None
        src = self._vdoc.sources[ref.source_id]
        page = src[ref.source_page_index]
        if not any(w.field_name in values for w in (page.widgets() or [])):
            return None
        doc = self._fill_docs.get(ref.source_id)
        if doc is None:
            from model.page_edits import apply_form_values

            doc = self._vdoc.fresh_source(ref.source_id)  # fresh copy keeps widgets (graft quirk)
            apply_form_values(doc, values)
            self._fill_docs[ref.source_id] = doc
        return doc[ref.source_page_index]

    def _drop_fill_docs(self) -> None:
        for doc in self._fill_docs.values():
            doc.close()
        self._fill_docs.clear()

    def _render_pixmap(self, index: int) -> QPixmap | None:
        total = (self._page_extra(index) + self._rotation) % 360  # per-page override + view spin
        key = (index, round(self._zoom, 4), total)
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            return hit
        ref = self._vdoc.ordered[index]
        try:
            filled = self._filled_source_page(ref)  # None unless this page has a filled field
            page = filled if filled is not None else self._vdoc.sources[ref.source_id][ref.source_page_index]
            pm = page.get_pixmap(matrix=fitz.Matrix(self._zoom, self._zoom), alpha=False)
            img = QImage(pm.samples, pm.width, pm.height, pm.stride, QImage.Format.Format_RGB888)
            pixmap = QPixmap.fromImage(img.copy())  # copy: detach from pm.samples buffer
            if total:
                pixmap = pixmap.transformed(QTransform().rotate(total))
        except Exception:
            return None
        self._cache[key] = pixmap
        self._cache.move_to_end(key)
        while len(self._cache) > _CACHE_LIMIT:
            self._cache.popitem(last=False)
        return pixmap

    def _visible_range(self) -> tuple[int, int]:
        view_rect = self.mapToScene(self.viewport().rect()).boundingRect()
        top, bottom = view_rect.top(), view_rect.bottom()
        first, last = None, None
        for i, p in enumerate(self._pages):
            if p["y"] + p["h"] >= top and p["y"] <= bottom:
                first = i if first is None else first
                last = i
        if first is None:  # nothing intersects (e.g. between renders) — fall back to current
            return self._current, self._current
        return first, last

    def _render_visible(self) -> None:
        if not self._pages:
            return
        first, last = self._visible_range()
        lo, hi = max(0, first - _PREFETCH), min(len(self._pages) - 1, last + _PREFETCH)
        for i, p in enumerate(self._pages):
            if lo <= i <= hi:
                pixmap = self._render_pixmap(i)
                if pixmap is not None:
                    p["pix"].setPixmap(pixmap)
            elif not p["pix"].pixmap().isNull():
                p["pix"].setPixmap(QPixmap())  # drop offscreen pixels to bound memory
        self._update_current(first, last)

    def _update_current(self, first: int, last: int) -> None:
        center = self.mapToScene(self.viewport().rect().center()).y()
        current = first
        for i in range(first, last + 1):
            p = self._pages[i]
            if p["y"] <= center <= p["y"] + p["h"]:
                current = i
                break
        if current != self._current:
            self._current = current
            self.currentPageChanged.emit(current)

    def _on_scroll(self, _value: int) -> None:
        self._render_visible()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render_visible()

    # ---- public API: zoom / fit / rotate / navigation ---------------------------

    @property
    def zoom(self) -> float:
        return self._zoom

    @property
    def rotation(self) -> int:
        return self._rotation

    @property
    def current_page(self) -> int:
        return self._current

    def set_zoom(self, zoom: float, keep_page: bool = True) -> None:
        zoom = max(_MIN_ZOOM, min(_MAX_ZOOM, zoom))
        if abs(zoom - self._zoom) < 1e-6:
            return
        anchor = self._current
        self._zoom = zoom
        self._build_scene()
        if keep_page:
            self.goto_page(anchor)
        self.zoomChanged.emit(self._zoom)

    def zoom_in(self) -> None:
        self.set_zoom(self._zoom * _ZOOM_STEP)

    def zoom_out(self) -> None:
        self.set_zoom(self._zoom / _ZOOM_STEP)

    def actual_size(self) -> None:
        """Reset to 100% — 1 PDF point per pixel (no scaling)."""
        self.set_zoom(1.0)

    def _fit_zoom(self, fit_height: bool) -> float:
        margin = 2 * _PAGE_GAP
        avail_w = max(1, self.viewport().width() - margin)
        w_pt, h_pt = self._natural_size(self._current)
        zoom = avail_w / w_pt
        if fit_height:
            avail_h = max(1, self.viewport().height() - margin)
            zoom = min(zoom, avail_h / h_pt)
        return zoom

    def fit_width(self) -> None:
        self.set_zoom(self._fit_zoom(fit_height=False))

    def fit_page(self) -> None:
        self.set_zoom(self._fit_zoom(fit_height=True))

    def rotate_view(self, delta: int) -> None:
        """Rotate the whole view by ``delta`` degrees (a multiple of 90)."""
        self._rotation = (self._rotation + delta) % 360
        anchor = self._current
        self._build_scene()
        self.goto_page(anchor)

    def reload(self) -> None:
        """Rebuild after the ordered list changed (edit). Page indices remap, so the pixmap
        cache (keyed by ordered index) is dropped to avoid showing stale pages; the form-fill
        copies are dropped too so a changed field value re-renders."""
        self._cache.clear()
        self._drop_fill_docs()
        if self._current >= self._vdoc.page_count:
            self._current = max(0, self._vdoc.page_count - 1)
        self._build_scene()
        self.goto_page(self._current)

    def goto_page(self, index: int) -> None:
        if not (0 <= index < len(self._pages)):
            return
        p = self._pages[index]
        self.verticalScrollBar().setValue(int(p["y"]) - _PAGE_GAP)
        self._render_visible()

    # ---- persistence ------------------------------------------------------------

    def view_state(self) -> dict:
        return {"page": self._current, "zoom": self._zoom, "rotation": self._rotation}

    def apply_state(self, state: dict) -> None:
        if not state:
            return
        rotation = int(state.get("rotation", 0)) % 360
        if rotation in (0, 90, 180, 270):
            self._rotation = rotation
        zoom = state.get("zoom")
        if isinstance(zoom, (int, float)) and _MIN_ZOOM <= zoom <= _MAX_ZOOM:
            self._zoom = float(zoom)
        self._build_scene()
        self.goto_page(int(state.get("page", 0)))
        # apply_state sets _zoom directly (bypassing set_zoom), so announce it for the indicator.
        self.zoomChanged.emit(self._zoom)
