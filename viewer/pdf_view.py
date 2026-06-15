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
from PySide6.QtCore import QRectF, Qt, Signal
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

    def __init__(self, vdoc: VirtualDocument, parent=None) -> None:
        super().__init__(parent)
        self._vdoc = vdoc
        self._zoom = 1.0
        self._rotation = 0  # view rotation in degrees: 0/90/180/270
        self._cache: "OrderedDict[tuple, QPixmap]" = OrderedDict()
        self._pages: list[dict] = []   # per page: {bg, pix, x, y, w, h}
        self._current = 0

        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QBrush(QColor(0x30, 0x30, 0x30)))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)

        self._build_scene()
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)

    # ---- natural geometry -------------------------------------------------------

    def _natural_size(self, index: int) -> tuple[float, float]:
        """Unscaled page size in points, with view rotation's axis swap applied."""
        ref = self._vdoc.ordered[index]
        rect = self._vdoc.sources[ref.source_id][ref.source_page_index].rect
        w, h = rect.width, rect.height
        return (h, w) if self._rotation in (90, 270) else (w, h)

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
            bg = QGraphicsRectItem(QRectF(x, y, w, h))
            bg.setPen(page_pen)
            bg.setBrush(page_brush)
            scene.addItem(bg)
            pix = QGraphicsPixmapItem(bg)  # child of bg → shares position
            pix.setPos(0, 0)
            self._pages.append({"bg": bg, "pix": pix, "x": x, "y": y, "w": w, "h": h})
            y += h + _PAGE_GAP

        scene.setSceneRect(0, 0, widest + 2 * _PAGE_GAP, y)
        self._render_visible()

    # ---- rendering --------------------------------------------------------------

    def _render_pixmap(self, index: int) -> QPixmap | None:
        key = (index, round(self._zoom, 4), self._rotation)
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            return hit
        ref = self._vdoc.ordered[index]
        try:
            page = self._vdoc.sources[ref.source_id][ref.source_page_index]
            pm = page.get_pixmap(matrix=fitz.Matrix(self._zoom, self._zoom), alpha=False)
            img = QImage(pm.samples, pm.width, pm.height, pm.stride, QImage.Format.Format_RGB888)
            pixmap = QPixmap.fromImage(img.copy())  # copy: detach from pm.samples buffer
            if self._rotation:
                pixmap = pixmap.transformed(QTransform().rotate(self._rotation))
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

    def zoom_in(self) -> None:
        self.set_zoom(self._zoom * _ZOOM_STEP)

    def zoom_out(self) -> None:
        self.set_zoom(self._zoom / _ZOOM_STEP)

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
