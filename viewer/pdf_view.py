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
from viewer.tools import ArmedTool, InteractionMode

_PAGE_GAP = 14          # px between pages in the strip
_PREFETCH = 2           # pages to render above/below the viewport
_CACHE_LIMIT = 48       # max rendered pixmaps held at once
_MIN_ZOOM, _MAX_ZOOM = 0.1, 8.0
_ZOOM_STEP = 1.25


class PdfView(QGraphicsView):
    """Vertical continuous-scroll renderer over a VirtualDocument."""

    currentPageChanged = Signal(int)
    zoomChanged = Signal(float)  # emitted whenever the zoom factor changes (1.0 == 100%)
    armedChanged = Signal(object)  # the armed ArmedTool, or None — so the toolbar can light a button
    applyTextTool = Signal(object)  # an ArmedTool (HIGHLIGHT/REDACT_TEXT) fired on a drag-over-text release

    def __init__(self, vdoc: VirtualDocument, parent=None) -> None:
        super().__init__(parent)
        self._vdoc = vdoc
        self._zoom = 1.0
        self._rotation = 0  # view rotation in degrees: 0/90/180/270
        self._cache: "OrderedDict[tuple, QPixmap]" = OrderedDict()
        # Per-source fresh copies used to render edited pages: form fills applied (M14) and our
        # baked annotations stripped (M31 — they redraw as editable overlays). Keyed by source id;
        # rebuilt after each edit (reload clears them). Kept separate from the shared read-only
        # sources, and NOT built via insert_pdf — repeated insert_pdf from one source drops widgets
        # after the first call, so we use a fresh source copy and apply values / strip on it.
        self._render_docs: dict[str, "fitz.Document"] = {}
        self._pages: list[dict] = []   # per page: {bg, pix, x, y, w, h}
        self._current = 0
        # Overlay controllers (set by MainWindow): text selection + search (M3), form fill (M14),
        # annotations (M20). They own their items and expose repaint(), called after every rebuild.
        self.selection = None
        self.search = None
        self.form = None
        self.annotations = None
        self.links = None  # internal-link navigation (M33), set by MainWindow
        # Builds the right-click menu for a scene point (M46), set by MainWindow; None falls
        # through to the default QGraphicsView handling.
        self.context_menu_provider = None

        self._mode = InteractionMode.SELECT  # SELECT (text/forms/move) vs GRAB (hand-pan) — M18
        # A one-shot armed insert tool (ArmedTool.TEXTBOX / .REDACT) — fires once then auto-reverts
        # to SELECT (M21). None means no tool is armed.
        self._armed: "ArmedTool | None" = None
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QBrush(QColor(0x30, 0x30, 0x30)))
        # SELECT mode: left-drag selects text (M3), so no hand-drag panning; scroll via wheel/bars.
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        # Centre the scene in the viewport. Qt only applies the alignment when the *whole* scene
        # fits with no scrollbars — i.e. a short / zoomed-out page — so this centres that page both
        # ways; a taller multi-page doc gets scrollbars and scrolls normally from the top, unaffected.
        self.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)

        # Suppress page rasterisation until the window is first shown (open_at). The scene geometry
        # is still built so overlays can place themselves, but no pixmap is rendered — and therefore
        # never painted — at the construction zoom (1.0) or during the pre-show resizes. So the first
        # frame the user sees is rendered once, at Fit Page, with no zoom-1.0 / resize flicker.
        self._shown_once = False
        # Sticky fit mode ("width" / "page" / None): re-applied on every viewport resize so a chosen
        # Fit Width / Fit Page follows the window — e.g. it re-fits when the Pages sidebar is toggled.
        self._fit_mode: "str | None" = None
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

    def _unrotated_size(self, index: int) -> tuple[float, float]:
        """Unrotated **MediaBox** size in points — the space word boxes + widget rects live in,
        even for a page with a baked-in ``/Rotate`` (PyMuPDF reports those coords un-rotated, while
        ``page.rect`` and ``get_pixmap`` are rotated)."""
        ref = self._vdoc.ordered[index]
        mediabox = self._vdoc.sources[ref.source_id][ref.source_page_index].mediabox
        return mediabox.width, mediabox.height

    def _display_rotation(self, index: int) -> int:
        """**Absolute** rotation of the displayed page vs its MediaBox: the per-page override if
        set, else the source page's own ``/Rotate`` — plus the view rotation. Overlays rotate
        boxes by this so they align whether the rotation is an in-session override or baked in."""
        ref = self._vdoc.ordered[index]
        if ref.rotation_override is not None:
            base = ref.rotation_override
        else:
            base = self._vdoc.sources[ref.source_id][ref.source_page_index].rotation
        return (base + self._rotation) % 360

    def _natural_size(self, index: int) -> tuple[float, float]:
        """Unscaled displayed page size in points (MediaBox with rotation axis swaps)."""
        w, h = self._unrotated_size(index)
        return (h, w) if self._display_rotation(index) in (90, 270) else (w, h)

    @staticmethod
    def _box_to_display(W: float, H: float, total: int, box: tuple) -> tuple:
        """Rotate a box (in unrotated WxH page points) into the displayed (spun) page space."""
        x0, y0, x1, y1 = box
        if total == 90:      # source (x,y) -> display (H - y, x)
            pts = (H - y0, x0, H - y1, x1)
        elif total == 180:
            pts = (W - x0, H - y0, W - x1, H - y1)
        elif total == 270:   # source (x,y) -> display (y, W - x)
            pts = (y0, W - x0, y1, W - x1)
        else:
            pts = (x0, y0, x1, y1)
        ax0, ay0, ax1, ay1 = pts
        return (min(ax0, ax1), min(ay0, ay1), max(ax0, ax1), max(ay0, ay1))

    @staticmethod
    def _point_to_source(W: float, H: float, total: int, dx: float, dy: float) -> tuple:
        """Inverse of :meth:`_box_to_display` for a single display-space point."""
        if total == 90:
            return dy, H - dx
        if total == 180:
            return W - dx, H - dy
        if total == 270:
            return W - dy, dx
        return dx, dy

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
            # Centre each page within the scene's content band. The band is inset by _PAGE_GAP on
            # both sides (the sceneRect below is widest + 2*_PAGE_GAP), so the left inset must be
            # added here too — without it the widest page sat flush at scene-x 0 and the whole strip
            # rendered ~_PAGE_GAP px left of centre in the window.
            x = _PAGE_GAP + (widest - w) / 2.0
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
        for overlay in (self.annotations, self.form, self.selection, self.search):
            if overlay is not None:
                overlay.repaint()
        self._reposition_overlay_editors()  # an open inline editor follows the zoom

    def _reposition_overlay_editors(self) -> None:
        """Move any open inline editor (form field / text box) back onto its target after the view
        geometry changes (zoom or scroll), so it doesn't get left behind."""
        for overlay in (self.form, self.annotations):
            if overlay is not None:
                overlay.reposition_editor()

    # ---- overlay geometry helpers (used by selection + search) ------------------

    def page_and_local_at(self, scene_pt) -> tuple[int | None, "QPointF | None"]:
        """Map a scene point to ``(page_index, point in unrotated page points)``.

        Returns the page whose vertical band contains the point; the local point is mapped back
        through any per-page rotation so it lands in the source coordinate space (where word boxes
        and widget rects live). ``(None, None)`` when the point falls in a gap above/below all pages.
        """
        for i, p in enumerate(self._pages):
            if p["y"] <= scene_pt.y() <= p["y"] + p["h"]:
                dx = (scene_pt.x() - p["x"]) / self._zoom
                dy = (scene_pt.y() - p["y"]) / self._zoom
                w, h = self._unrotated_size(i)
                lx, ly = self._point_to_source(w, h, self._display_rotation(i), dx, dy)
                return i, QPointF(lx, ly)
        return None, None

    def scene_rect_for_box(self, page_index: int, box: tuple) -> QRectF:
        """Map a box in unrotated page points (x0,y0,x1,y1) to its scene rect, accounting for any
        per-page rotation so overlays align with the displayed (spun) page."""
        p = self._pages[page_index]
        z = self._zoom
        w, h = self._unrotated_size(page_index)
        dx0, dy0, dx1, dy1 = self._box_to_display(w, h, self._display_rotation(page_index), box)
        return QRectF(p["x"] + dx0 * z, p["y"] + dy0 * z, (dx1 - dx0) * z, (dy1 - dy0) * z)

    def local_box_from_scene_rect(self, page_index: int, scene_rect) -> tuple:
        """Inverse of :meth:`scene_rect_for_box`: map a scene rect back to an unrotated page-local
        box ``(x0,y0,x1,y1)``, clamped to the page. Used by the redaction rubber-band to record the
        marked region in the coordinate space the materialise pass redacts in."""
        p = self._pages[page_index]
        z = self._zoom
        w, h = self._unrotated_size(page_index)
        rot = self._display_rotation(page_index)
        corners = (
            (scene_rect.left(), scene_rect.top()),
            (scene_rect.right(), scene_rect.bottom()),
        )
        pts = [
            self._point_to_source(w, h, rot, (sx - p["x"]) / z, (sy - p["y"]) / z)
            for sx, sy in corners
        ]
        xs, ys = [pt[0] for pt in pts], [pt[1] for pt in pts]
        return (max(0.0, min(xs)), max(0.0, min(ys)), min(w, max(xs)), min(h, max(ys)))

    def page_transform(self, page_index: int) -> QTransform:
        """The affine that maps a page's **unrotated** point coords → scene coords (origin offset +
        zoom + per-page/view rotation). Lets an overlay item authored in page points (a text box and
        its text) render rotated *with* the page, instead of axis-aligned in scene space."""
        p = self._pages[page_index]
        w, h = self._unrotated_size(page_index)
        total = self._display_rotation(page_index)
        tr = QTransform()
        tr.translate(p["x"], p["y"])
        tr.scale(self._zoom, self._zoom)
        # Compose the same unrotated→display mapping as _box_to_display (Qt applies the last-added
        # op to the point first), so a point (x,y) lands exactly where scene_rect_for_box puts it.
        if total == 90:
            tr.translate(h, 0)
            tr.rotate(90)
        elif total == 180:
            tr.translate(w, h)
            tr.rotate(180)
        elif total == 270:
            tr.translate(0, w)
            tr.rotate(270)
        return tr

    def local_point_on_page(self, page_index: int, scene_pt) -> QPointF:
        """Map a scene point to a specific page's **unrotated** point coords (rotation-aware, not
        clamped). Unlike :meth:`page_and_local_at` it targets a fixed page, so a drag that strays
        past the page edge still maps to that page's frame — used by the text-box move."""
        p = self._pages[page_index]
        dx = (scene_pt.x() - p["x"]) / self._zoom
        dy = (scene_pt.y() - p["y"]) / self._zoom
        w, h = self._unrotated_size(page_index)
        lx, ly = self._point_to_source(w, h, self._display_rotation(page_index), dx, dy)
        return QPointF(lx, ly)

    def ensure_box_visible(self, page_index: int, box: tuple) -> None:
        self.ensureVisible(self.scene_rect_for_box(page_index, box), 60, 60)

    # ---- mouse → text selection -------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            scene_pt = self.mapToScene(event.position().toPoint())
            # An armed one-shot tool takes the click first. TEXTBOX disarms once a box is placed;
            # a click off any page leaves it armed (a mis-click doesn't waste the arm). REDACT
            # disarms on release (after the drag commits).
            if self._armed is ArmedTool.TEXTBOX and self.annotations is not None:
                if self.annotations.place_textbox(scene_pt):
                    self.disarm()
                event.accept()
                return
            if self._armed is ArmedTool.REDACT_REGION and self.annotations is not None:
                self.annotations.begin_redaction(scene_pt)  # no-op off-page; stays armed
                event.accept()
                return
            if self._armed is not None and self._armed.drags_text and self.selection is not None:
                self.selection.begin(scene_pt)  # drag over text; applied (highlight/redact) on release
                event.accept()
                return
            if self._mode == InteractionMode.SELECT:
                # Priority: fill a form field → move an existing text box → begin a text selection.
                if self.form is not None and self.form.handle_press(scene_pt):
                    event.accept()
                    return
                if self.annotations is not None and self.annotations.begin_move(scene_pt):
                    event.accept()
                    return
                # Click an internal link → jump to its target page (before text selection, so a click
                # on a link navigates rather than starting a selection).
                if self.links is not None and self.links.navigate_at(scene_pt):
                    event.accept()
                    return
                if self.selection is not None and self.selection.begin(scene_pt):
                    event.accept()
                    return
        # GRAB (and any unhandled click) → QGraphicsView; ScrollHandDrag pans in GRAB mode.
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:
        # The menu is built by MainWindow (context_menu_provider) from the hit state under the
        # cursor — annotation / text selection / link / bare page (M46) — because the verbs it
        # routes (copy, highlight/redact, fit modes, Go to Page…) live on the window, not the view.
        if self.context_menu_provider is not None:
            menu = self.context_menu_provider(self.mapToScene(event.pos()))
            if menu is not None:
                menu.exec(event.globalPos())
                event.accept()
                return
        super().contextMenuEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._armed is None:
            scene_pt = self.mapToScene(event.position().toPoint())
            # Double-click an existing text box → re-edit its text; otherwise select the word.
            if self.annotations is not None and self.annotations.edit_textbox_at(scene_pt):
                event.accept()
                return
            if self.selection is not None and self.selection.select_word_at(scene_pt):
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:
        scene_pt = self.mapToScene(event.position().toPoint())
        if self.annotations is not None and self.annotations.redacting:
            self.annotations.update_redaction(scene_pt)
            event.accept()
            return
        if self.annotations is not None and self.annotations.moving:
            self.annotations.update_move(scene_pt)
            event.accept()
            return
        if self.selection is not None and self.selection.active:
            self.selection.update_to(scene_pt)
            event.accept()
            return
        self._update_hover_cursor(scene_pt)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.annotations is not None and self.annotations.redacting:
            self.annotations.finish_redaction()
            if self._armed is ArmedTool.REDACT_REGION:
                self.disarm()  # one-shot: revert to SELECT after the drag commits
            event.accept()
            return
        if self.annotations is not None and self.annotations.moving:
            self.annotations.finish_move()
            event.accept()
            return
        if self.selection is not None and self.selection.active:
            self.selection.finish()
            # An armed drag-over-text tool applies to what was just selected, then disarms — but a
            # stray click that selected nothing leaves the tool armed (no wasted arm).
            if self._armed is not None and self._armed.drags_text:
                if self.selection.selected_words():
                    self.applyTextTool.emit(self._armed)
                    self.disarm()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        # Esc cancels an armed one-shot tool (back to plain Select).
        if event.key() == Qt.Key.Key_Escape and self._armed is not None:
            self.disarm()
            event.accept()
            return
        super().keyPressEvent(event)

    def _update_hover_cursor(self, scene_pt) -> None:
        """In SELECT mode, show a pointing-hand over an internal link (it's clickable) and a move
        cursor over a text box (it's draggable) — but never while a box is being edited (you're
        typing, not arranging), so the move cursor isn't left showing on the viewport, which the
        inline editor / formatting bar would inherit."""
        if self._armed is not None or self._mode != InteractionMode.SELECT:
            return
        if self.annotations is not None and getattr(self.annotations, "editing", False):
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            return
        if self.links is not None and self.links.link_at(scene_pt) is not None:
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            return
        over_box = self.annotations is not None and self.annotations.textbox_at(scene_pt) is not None
        self.viewport().setCursor(Qt.CursorShape.SizeAllCursor if over_box else Qt.CursorShape.ArrowCursor)

    # ---- rendering --------------------------------------------------------------

    def _render_source_page(self, ref):
        """The source page to render for ``ref`` — a fresh per-source copy when this source needs a
        display fixup, else ``None`` (caller renders the shared source directly, the fast path).

        Two fixups, both on a fresh in-memory copy (``VirtualDocument.fresh_source``) so the shared
        source — possibly referenced by another window — is never mutated:

        * **form fills** — ``apply_form_values`` applied directly (no ``insert_pdf``, dodging
          PyMuPDF's repeated-graft widget loss) so entered values render;
        * **our baked annotations stripped** — a round-tripped highlight / text-box is baked into
          the source page, so ``get_pixmap`` would render it *and* the editable overlay would draw
          the model's copy on top: the original would show twice and a move / delete would only
          shift / hide the overlay, leaving the baked one pinned. Stripping our marks here (foreign
          annotations stay) makes the rendered pixmap the clean page and the overlay the single
          source of truth — the same strip-then-redraw materialise does at save.

        The copy is built (and the build-or-fast-path decision made) **once per source**, cached,
        and dropped by :meth:`reload` after every edit — so per-page renders are a dict lookup, not
        a re-scan. A cached ``None`` means "no copy needed; render the shared source".
        """
        source_id = ref.source_id
        if source_id not in self._render_docs:
            self._render_docs[source_id] = self._build_render_doc(source_id)
        doc = self._render_docs[source_id]
        return None if doc is None else doc[ref.source_page_index]

    def _build_render_doc(self, source_id: str):
        """Build the per-source render copy, or ``None`` when the source needs no fixup (the fast
        path). See :meth:`_render_source_page` for what the copy carries."""
        values = self._vdoc.form_values
        has_fills = bool(values) and any(
            w.field_name in values
            for page in self._vdoc.sources[source_id]
            for w in (page.widgets() or [])
        )
        has_ours = self._vdoc.source_has_klarpdf_annotations(source_id)
        if not has_fills and not has_ours:
            return None
        from model.page_edits import apply_form_values, strip_klarpdf_annotations

        doc = self._vdoc.fresh_source(source_id)  # fresh copy keeps widgets (graft quirk)
        if has_fills:
            apply_form_values(doc, values)
        if has_ours:
            for page in doc:
                strip_klarpdf_annotations(page)
        return doc

    def _drop_render_docs(self) -> None:
        for doc in self._render_docs.values():
            if doc is not None:  # a cached None means "fast path", nothing to close
                doc.close()
        self._render_docs.clear()

    def _render_pixmap(self, index: int) -> QPixmap | None:
        total = (self._page_extra(index) + self._rotation) % 360  # per-page override + view spin
        key = (index, round(self._zoom, 4), total)
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            return hit
        ref = self._vdoc.ordered[index]
        try:
            render_page = self._render_source_page(ref)  # None → render the shared source (fast path)
            page = render_page if render_page is not None else self._vdoc.sources[ref.source_id][ref.source_page_index]
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
        if not self._pages or not self._shown_once:
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
        self._reposition_overlay_editors()  # keep an open inline editor on its field while scrolling

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reapply_fit()  # a sticky Fit Width/Page follows the new viewport (e.g. sidebar toggle)
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

    @property
    def mode(self) -> InteractionMode:
        return self._mode

    @property
    def armed(self) -> "ArmedTool | None":
        return self._armed

    def set_mode(self, mode: InteractionMode) -> None:
        """Switch the persistent mouse tool: SELECT (text/forms/move) or GRAB (hand-pan).
        Switching modes also disarms any one-shot insert tool."""
        self.disarm()
        if mode == self._mode:
            return
        self._mode = mode
        if mode == InteractionMode.GRAB:
            if self.selection is not None:
                self.selection.clear()  # drop any in-progress selection when grabbing
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)  # Qt shows the hand cursor
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)

    def arm(self, tool: "ArmedTool") -> None:
        """Arm a one-shot insert tool (Add Text Box / Redact Region). Forces SELECT as the base
        mode, shows a crosshair, and announces the change so the toolbar can light the button."""
        self.set_mode(InteractionMode.SELECT)  # NB: set_mode disarms first; we set _armed after
        self._armed = tool
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        self.armedChanged.emit(tool)

    def disarm(self) -> None:
        """Cancel any armed one-shot tool and return to plain SELECT behaviour."""
        if self._armed is None:
            return
        self._armed = None
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        self.armedChanged.emit(None)

    def set_zoom(self, zoom: float, keep_page: bool = True, fit: "str | None" = None) -> None:
        # ``fit`` records the sticky fit-mode this zoom represents ("width" / "page"); it is re-applied
        # on a viewport resize so the fit follows the window (e.g. a Pages-sidebar toggle). A manual
        # zoom passes None, which cancels any sticky fit.
        self._fit_mode = fit
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
        self.set_zoom(self._fit_zoom(fit_height=False), fit="width")
        self._center_horizontally()

    def fit_page(self) -> None:
        self.set_zoom(self._fit_zoom(fit_height=True), fit="page")
        self._center_horizontally()

    def _reapply_fit(self) -> None:
        """Re-run the active sticky fit against the current viewport (called on resize)."""
        if self._fit_mode == "width":
            self.set_zoom(self._fit_zoom(fit_height=False), fit="width")
            self._center_horizontally()
        elif self._fit_mode == "page":
            self.set_zoom(self._fit_zoom(fit_height=True), fit="page")
            self._center_horizontally()

    def _center_horizontally(self) -> None:
        """Centre the viewport on the scene's horizontal midline. Pages are laid out centred in the
        scene's widest column, so this centres the **current** page — needed when a wider (e.g. a
        90°/270°-rotated) page makes the scene exceed the viewport width, where Qt's AlignHCenter no
        longer applies. Fit Width/Page on the current page then stays centred + fitting while the
        wider page overflows symmetrically (h-scrollable), instead of shoving the current page off to
        one side (where it fit neither page)."""
        hbar = self.horizontalScrollBar()
        hbar.setValue((hbar.minimum() + hbar.maximum()) // 2)

    def rotate_view(self, delta: int) -> None:
        """Rotate the whole view by ``delta`` degrees (a multiple of 90)."""
        self._rotation = (self._rotation + delta) % 360
        anchor = self._current
        self._build_scene()
        self.goto_page(anchor)

    def reload(self) -> None:
        """Rebuild after the ordered list changed (edit). Page indices remap, so the pixmap
        cache (keyed by ordered index) is dropped to avoid showing stale pages; the render
        copies are dropped too so a changed field value / annotation re-renders, and the text
        selection's word cache is invalidated (same reasons — remapped indices, stripped marks)."""
        self._cache.clear()
        self._drop_render_docs()
        if self.selection is not None:
            self.selection.invalidate()
        if self.links is not None:
            self.links.invalidate()
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
        self._fit_mode = None  # a restored, explicit zoom is manual — not a sticky fit
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

    def open_at(self, state: dict) -> None:
        """First show: restore the remembered page + rotation, open at **Fit Page**, and do the
        first pixmap render — once, at the now-final viewport size. Rendering was suppressed until
        here (``_shown_once``), so the page paints exactly once at the fit zoom — no zoom-1.0 frame,
        no re-render after a remembered zoom, no flicker."""
        self._shown_once = True
        state = state or {}
        rotation = int(state.get("rotation", 0)) % 360
        if rotation in (0, 90, 180, 270):
            self._rotation = rotation
        self._current = max(0, min(int(state.get("page", 0)), self._vdoc.page_count - 1))
        self._zoom = self._fit_zoom(fit_height=True)  # Fit Page, computed against the final viewport
        self._fit_mode = "page"                       # default view tracks Fit Page (re-fits on resize)
        self._build_scene()                           # geometry + the first (and only) render
        self.goto_page(self._current)                 # resume the remembered page
        self._center_horizontally()                   # centre even if a rotated page widens the scene
        self.zoomChanged.emit(self._zoom)
