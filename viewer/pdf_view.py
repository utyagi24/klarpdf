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
from model.form_fields import NewField
from viewer.resize_handles import cursor_for
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
    cropDragged = Signal(int, tuple)  # an armed CROP drag finished: (page_index, content box) — M48
    foreignMoved = Signal(int, object, float, float)  # a foreign annotation was dragged — M67
    foreignAdopt = Signal(int, object)  # a foreign annotation was double-clicked — M68

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
        # Per-*ordered-page* render copies carrying that page's pending foreign-annotation
        # deletions (M66). Keyed by page index, not source id: the deletion rides the PageRef, so
        # two copies of one source page can differ. Value is (page, owning doc).
        self._foreign_docs: dict[int, tuple] = {}
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
        # In-progress armed-CROP drag (M48): the anchor point, its page, and the dashed band item.
        self._crop_anchor = None
        self._crop_page: "int | None" = None
        self._crop_item = None
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
        # Night reading mode (M49): view-only pixel inversion, independent of the OS theme the
        # chrome follows. The file, print, export, and thumbnails are untouched.
        self._night = False
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
        """Unrotated size in points of the page area being **displayed**: the crop override's
        dims when one is set (M48), else the source **CropBox** (== MediaBox for the normal,
        uncropped page — and the frame PyMuPDF's word boxes / widget rects / ``get_pixmap`` are
        all relative to, so a source that arrives pre-cropped lays out consistently too)."""
        ref = self._vdoc.ordered[index]
        if ref.crop_override is not None:
            x0, y0, x1, y1 = ref.crop_override
            return x1 - x0, y1 - y0
        cropbox = self._vdoc.sources[ref.source_id][ref.source_page_index].cropbox
        return cropbox.width, cropbox.height

    def _crop_origin(self, index: int) -> tuple[float, float]:
        """Top-left of the displayed area within the page's content frame — ``(0, 0)`` unless a
        crop override shifts it (M48). Content coords (words, annotations, links) subtract this
        to land in the displayed (cropped) frame; the inverse mappings add it back."""
        crop = self._vdoc.ordered[index].crop_override
        return (crop[0], crop[1]) if crop is not None else (0.0, 0.0)

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
        # Night mode paints the not-yet-rendered page black — the inverse of the white page —
        # so a page scrolling into view doesn't flash bright before its pixmap lands.
        page_brush = QBrush(QColor(0, 0, 0) if self._night else QColor(0xFF, 0xFF, 0xFF))

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
                ox, oy = self._crop_origin(i)  # displayed frame → content coords
                return i, QPointF(lx + ox, ly + oy)
        return None, None

    def scene_rect_for_box(self, page_index: int, box: tuple) -> QRectF:
        """Map a box in unrotated page points (x0,y0,x1,y1) to its scene rect, accounting for any
        per-page rotation so overlays align with the displayed (spun) page. ``box`` is in content
        coords; a crop override shifts the displayed frame, so its origin is subtracted first."""
        p = self._pages[page_index]
        z = self._zoom
        w, h = self._unrotated_size(page_index)
        ox, oy = self._crop_origin(page_index)
        box = (box[0] - ox, box[1] - oy, box[2] - ox, box[3] - oy)
        dx0, dy0, dx1, dy1 = self._box_to_display(w, h, self._display_rotation(page_index), box)
        return QRectF(p["x"] + dx0 * z, p["y"] + dy0 * z, (dx1 - dx0) * z, (dy1 - dy0) * z)

    def local_box_from_scene_rect(self, page_index: int, scene_rect) -> tuple:
        """Inverse of :meth:`scene_rect_for_box`: map a scene rect back to an unrotated page-local
        content box ``(x0,y0,x1,y1)``, clamped to the displayed (possibly cropped) frame. Used by
        the redaction rubber-band and the crop drag to record the marked region in the coordinate
        space the materialise pass works in."""
        p = self._pages[page_index]
        z = self._zoom
        w, h = self._unrotated_size(page_index)
        rot = self._display_rotation(page_index)
        ox, oy = self._crop_origin(page_index)
        corners = (
            (scene_rect.left(), scene_rect.top()),
            (scene_rect.right(), scene_rect.bottom()),
        )
        pts = [
            self._point_to_source(w, h, rot, (sx - p["x"]) / z, (sy - p["y"]) / z)
            for sx, sy in corners
        ]
        xs = [pt[0] + ox for pt in pts]
        ys = [pt[1] + oy for pt in pts]
        return (max(ox, min(xs)), max(oy, min(ys)), min(ox + w, max(xs)), min(oy + h, max(ys)))

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
        ox, oy = self._crop_origin(page_index)
        tr.translate(-ox, -oy)  # last-added runs first: content coords → the displayed crop frame
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
        ox, oy = self._crop_origin(page_index)
        return QPointF(lx + ox, ly + oy)

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
            if self._armed is ArmedTool.CROP:
                self.begin_crop_drag(scene_pt)  # no-op off-page; stays armed
                event.accept()
                return
            if self._armed is not None and self._armed.draws and self.annotations is not None:
                self.annotations.begin_draw(self._armed, scene_pt)  # no-op off-page; stays armed
                event.accept()
                return
            if self._armed is not None and self._armed.drags_text and self.selection is not None:
                self.selection.begin(scene_pt)  # drag over text; applied (highlight/redact) on release
                event.accept()
                return
            # A press on a resize handle wins over everything below (it's the most specific
            # target) — in any mode, since the handles only exist while something is selected.
            if self.annotations is not None:
                handle = self.annotations.handle_at(scene_pt)
                if handle is not None and self.annotations.begin_resize(handle, scene_pt):
                    event.accept()
                    return
            if self._mode == InteractionMode.OBJECT and self.annotations is not None:
                # Object mode (M59.6): Ctrl toggles a mark in/out of the group (or additively
                # marquees empty space); a plain press moves the hit mark / group, or marquees.
                ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                hit = self.annotations.textbox_at(scene_pt) or self.annotations.drawn_mark_at(scene_pt)
                if ctrl and hit is not None:
                    self.annotations.toggle_object(*hit)
                elif hit is not None:
                    self.annotations.begin_move(scene_pt)  # group move if a member, else this mark
                else:
                    self.annotations.begin_marquee(scene_pt, add=ctrl)
                event.accept()
                return
            if self._mode == InteractionMode.SELECT:
                # Priority: drag an already-selected object → fill a form field → move an existing
                # text box → begin a text selection.
                #
                # The selected-object case leads (M69.15) so a mark that *is* a form field can still
                # be dragged here. Without it the form overlay claimed every press on a field, and a
                # field you had just drawn could be resized (handles are tested earlier still) but
                # never moved without switching to Objects mode. Gated on the mark already being
                # selected, so a click on an *unselected* field still means "fill this in" — which
                # is what Select mode is for, and what M69 made work for a field made this session.
                if self.annotations is not None and self._grabs_before_form(scene_pt)                         and self.annotations.begin_move(scene_pt):
                    event.accept()
                    return
                if self.form is not None and self.form.handle_press(scene_pt):
                    event.accept()
                    return
                if self.annotations is not None and self.annotations.begin_move(scene_pt):
                    event.accept()
                    return
                # A foreign annotation (M67) — tried after our own marks, so an editable mark
                # always wins a spot they share.
                if self.annotations is not None and self.annotations.begin_foreign_move(scene_pt):
                    event.accept()
                    return
                # The press wasn't on a free-placed mark → drop a lingering object selection
                # (M59); a press that *did* grab one re-selects on its zero-drag release.
                if self.annotations is not None:
                    self.annotations.clear_object_selection()
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

    def _grabs_before_form(self, scene_pt) -> bool:
        """Whether a Select-mode press on ``scene_pt`` should **move a mark** rather than reach the
        form overlay (M69.16).

        Two cases, and the distinction is who owns the thing under the cursor:

        * a **field this session created** (:class:`~model.form_fields.NewField`) — you are still
          authoring it, so a press moves it and a *double*-click types into it, exactly the contract
          a text box has had since M20. Before this, a press anywhere on a field went to the form
          overlay, so the only way to grab one in Select mode was to hit its border precisely —
          "hit and miss most of the times" (owner). A **document's own** form fields are untouched:
          single-click still fills them, which is what filling in a form requires.
        * anything **already selected** — having selected a mark, dragging it should move it rather
          than fall through to whatever sits underneath.
        """
        hit = self.annotations.drawn_mark_at(scene_pt)
        if hit is None:
            return False
        if isinstance(hit[1], NewField):
            return True
        return any(mark is hit[1] for _p, mark in self.annotations.selected_objects)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._armed is None:
            scene_pt = self.mapToScene(event.position().toPoint())
            # Double-click a field you created → type into it. The press moves it (see
            # `_grabs_before_form`), so this is how its value is reached — the same drag-to-move /
            # double-click-to-edit split a text box has.
            if self.annotations is not None and self.form is not None:
                hit = self.annotations.drawn_mark_at(scene_pt)
                if hit is not None and isinstance(hit[1], NewField)                         and self.form.handle_press(scene_pt):
                    event.accept()
                    return
            # Double-click an existing text box → re-edit its text; otherwise select the word.
            if self.annotations is not None and self.annotations.edit_textbox_at(scene_pt):
                event.accept()
                return
            # Double-click a *foreign* mark → offer to adopt it into the editable model (M68).
            # After our own text boxes, so re-editing one still wins.
            if self.annotations is not None:
                hit = self.annotations.foreign_annotation_at(scene_pt)
                if hit is not None:
                    self.foreignAdopt.emit(*hit)
                    event.accept()
                    return
            if self.selection is not None and self.selection.select_word_at(scene_pt):
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:
        scene_pt = self.mapToScene(event.position().toPoint())
        if self.cropping:
            self.update_crop_drag(scene_pt)
            event.accept()
            return
        if self.annotations is not None and self.annotations.redacting:
            self.annotations.update_redaction(scene_pt)
            event.accept()
            return
        if self.annotations is not None and self.annotations.drawing:
            self.annotations.update_draw(scene_pt, event.modifiers())  # Shift constrains
            event.accept()
            return
        if self.annotations is not None and self.annotations.resizing:
            self.annotations.update_resize(scene_pt, event.modifiers())  # Shift keeps proportions
            event.accept()
            return
        if self.annotations is not None and self.annotations.moving:
            self.annotations.update_move(scene_pt)
            event.accept()
            return
        if self.annotations is not None and self.annotations.marqueeing:
            self.annotations.update_marquee(scene_pt)
            event.accept()
            return
        if self.selection is not None and self.selection.active:
            self.selection.update_to(scene_pt)
            event.accept()
            return
        self._update_hover_cursor(scene_pt)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.cropping:
            self.finish_crop_drag()
            if self._armed is ArmedTool.CROP:
                self.disarm()  # one-shot: revert to SELECT after the drag commits
            event.accept()
            return
        if self.annotations is not None and self.annotations.redacting:
            self.annotations.finish_redaction()
            if self._armed is ArmedTool.REDACT_REGION:
                self.disarm()  # one-shot: revert to SELECT after the drag commits
            event.accept()
            return
        if self.annotations is not None and self.annotations.drawing:
            self.annotations.finish_draw()
            if self._armed is not None and self._armed.draws:
                self.disarm()  # one-shot: revert to SELECT after the gesture commits
            event.accept()
            return
        if self.annotations is not None and self.annotations.resizing:
            self.annotations.finish_resize()
            event.accept()
            return
        if self.annotations is not None and self.annotations.moving_foreign:
            moved = self.annotations.finish_foreign_move()
            if moved is not None:
                self.foreignMoved.emit(*moved)
            event.accept()
            return
        if self.annotations is not None and self.annotations.moving:
            self.annotations.finish_move()
            event.accept()
            return
        if self.annotations is not None and self.annotations.marqueeing:
            self.annotations.finish_marquee()
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
        # Esc cancels an armed one-shot tool (back to plain Select) — else a lingering object
        # selection (M59). Delete/Backspace removes the selected object (undoable).
        if event.key() == Qt.Key.Key_Escape and self.annotations is not None \
                and self.annotations.resizing:
            self.annotations.cancel_resize()   # drop an in-flight resize before anything else
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self._armed is not None:
            self.disarm()
            event.accept()
            return
        if self.annotations is not None and self.annotations.selected_objects:
            if event.key() == Qt.Key.Key_Escape:
                self.annotations.clear_object_selection()
                event.accept()
                return
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self.annotations.remove_selected_objects()  # whole group, one undo (M59.6)
                event.accept()
                return
        super().keyPressEvent(event)

    def _update_hover_cursor(self, scene_pt) -> None:
        """Show a pointing-hand over an internal link (SELECT — it's clickable) and a move cursor
        over a draggable mark — but never while a box is being edited (you're typing, not arranging),
        so the move cursor isn't left showing on the viewport, which the inline editor / formatting
        bar would inherit. In OBJECT mode (M59.6) the move cursor covers any drawn mark or text box,
        since dragging one moves it / the group; links are inert there."""
        if self._armed is not None or self._mode not in (InteractionMode.SELECT, InteractionMode.OBJECT):
            return
        if self.annotations is not None and getattr(self.annotations, "editing", False):
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            return
        if self.annotations is not None:                      # a resize handle names its own cursor
            handle = self.annotations.handle_at(scene_pt)
            if handle is not None:
                self.viewport().setCursor(cursor_for(handle))
                return
        if self._mode == InteractionMode.SELECT and self.links is not None \
                and self.links.link_at(scene_pt) is not None:
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            return
        over_mark = self.annotations is not None and (
            self.annotations.textbox_at(scene_pt) is not None
            or (self._mode == InteractionMode.OBJECT
                and self.annotations.drawn_mark_at(scene_pt) is not None)
        )
        self.viewport().setCursor(Qt.CursorShape.SizeAllCursor if over_mark else Qt.CursorShape.ArrowCursor)

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

    def _deleted_foreign_page(self, index: int, ref):
        """A one-page render copy with this page's pending foreign deletions applied (M66), or
        ``None`` when the page has none.

        A deleted foreign annotation is *in the source page's pixmap*, so unlike our own marks it
        cannot be hidden by an overlay — the render has to lose it. It is cached **per ordered page,
        not per source**, because the deletion rides the ``PageRef``: duplicate a page (M51) and
        delete a comment on one copy, and the two must render differently despite sharing a source
        page. Dropped with the rest on :meth:`reload`.
        """
        from model.foreign_annots import ForeignDeletion, ForeignMove, apply_foreign_edits

        if not any(isinstance(a, (ForeignDeletion, ForeignMove)) for a in ref.annotations):
            return None
        if index in self._foreign_docs:
            return self._foreign_docs[index][0]
        base = self._render_source_page(ref)
        source = base if base is not None else self._vdoc.sources[ref.source_id][ref.source_page_index]
        doc = fitz.open()
        doc.insert_pdf(source.parent, from_page=source.number, to_page=source.number,
                       annots=True, widgets=True)
        apply_foreign_edits(doc[0], ref.annotations)
        self._foreign_docs[index] = (doc[0], doc)
        return doc[0]

    def _drop_foreign_docs(self) -> None:
        for _page, doc in self._foreign_docs.values():
            doc.close()
        self._foreign_docs.clear()

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
        self._drop_foreign_docs()

    def _render_pixmap(self, index: int) -> QPixmap | None:
        total = (self._page_extra(index) + self._rotation) % 360  # per-page override + view spin
        key = (index, round(self._zoom, 4), total)
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            return hit
        ref = self._vdoc.ordered[index]
        try:
            # A pending foreign deletion needs the annotation gone from the *pixmap*, so it takes
            # precedence over the shared per-source copy (M66).
            render_page = self._deleted_foreign_page(index, ref) or self._render_source_page(ref)
            page = render_page if render_page is not None else self._vdoc.sources[ref.source_id][ref.source_page_index]
            clip = None
            if ref.crop_override is not None:
                visible = self._renderable_crop(index)
                if visible is None:
                    return None  # the crop lies wholly outside the renderable area
                # get_pixmap's clip is in the page's *rotated* space — spin the content-frame rect.
                cropbox = page.cropbox
                clip = fitz.Rect(
                    self._box_to_display(cropbox.width, cropbox.height, page.rotation, visible)
                )
            pm = page.get_pixmap(matrix=fitz.Matrix(self._zoom, self._zoom), clip=clip, alpha=False)
            img = QImage(pm.samples, pm.width, pm.height, pm.stride, QImage.Format.Format_RGB888)
            img = img.copy()  # detach from pm.samples buffer
            if self._night:
                img.invertPixels()  # M49: view-only — save/print/export render elsewhere
            pixmap = QPixmap.fromImage(img)
            if total:
                pixmap = pixmap.transformed(QTransform().rotate(total))
        except Exception:
            return None
        self._cache[key] = pixmap
        self._cache.move_to_end(key)
        while len(self._cache) > _CACHE_LIMIT:
            self._cache.popitem(last=False)
        return pixmap

    def _renderable_crop(self, index: int) -> tuple | None:
        """The part of the page's crop override the renderer can actually produce — the override
        intersected with the source CropBox frame — or ``None`` when nothing overlaps. Only a
        Remove Crop on a pre-cropped source extends beyond the frame (the model clamps drags);
        the uncovered border shows as blank page until a save re-bases the frame."""
        crop = self._vdoc.ordered[index].crop_override
        if crop is None:
            return None
        ref = self._vdoc.ordered[index]
        cropbox = self._vdoc.sources[ref.source_id][ref.source_page_index].cropbox
        visible = (max(crop[0], 0.0), max(crop[1], 0.0),
                   min(crop[2], cropbox.width), min(crop[3], cropbox.height))
        if visible[2] <= visible[0] or visible[3] <= visible[1]:
            return None
        return visible

    def _pixmap_offset(self, index: int) -> QPointF:
        """Where the rendered pixmap sits inside the page's display rect — ``(0, 0)`` except when
        the crop override extends beyond the renderable area (see :meth:`_renderable_crop`): the
        rendered part then lands inset in the grown frame."""
        crop = self._vdoc.ordered[index].crop_override
        if crop is None:
            return QPointF(0, 0)
        visible = self._renderable_crop(index)
        if visible is None or visible == crop:
            return QPointF(0, 0)
        w, h = self._unrotated_size(index)
        shifted = (visible[0] - crop[0], visible[1] - crop[1],
                   visible[2] - crop[0], visible[3] - crop[1])
        d = self._box_to_display(w, h, self._display_rotation(index), shifted)
        return QPointF(d[0] * self._zoom, d[1] * self._zoom)

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

    def content_band(self) -> tuple[int, int] | None:
        """The page range worth rasterising for, or ``None`` before the first show (paint it all).

        The same band :meth:`_render_visible` uses for page pixmaps, exposed so the annotation
        overlay's rasterised content marks can be just as lazy as the pages they sit on.
        """
        if not self._pages or not self._shown_once:
            return None
        first, last = self._visible_range()
        return max(0, first - _PREFETCH), min(len(self._pages) - 1, last + _PREFETCH)

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
                    p["pix"].setPos(self._pixmap_offset(i))  # inset only in the un-crop edge case
            elif not p["pix"].pixmap().isNull():
                p["pix"].setPixmap(QPixmap())  # drop offscreen pixels to bound memory
        # Content marks ride the same band as the pixmaps, so a stamp scrolls in with its page.
        if self.annotations is not None:
            self.annotations._paint_visible_content()
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
        """Switch the persistent mouse tool: SELECT (text/forms/move), GRAB (hand-pan), or OBJECT
        (marquee/group-select drawn marks — M59.6). Switching modes also disarms any one-shot
        insert tool."""
        self.disarm()
        if mode == self._mode:
            return
        self._mode = mode
        if mode == InteractionMode.GRAB:
            if self.selection is not None:
                self.selection.clear()  # drop any in-progress selection when grabbing
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)  # Qt shows the hand cursor
        else:
            if mode == InteractionMode.OBJECT and self.selection is not None:
                self.selection.clear()  # text selection is inert in object mode
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
        if self.cropping:  # Esc mid-drag: drop the band without emitting (nothing committed)
            if self._crop_item.scene() is self.scene():
                self.scene().removeItem(self._crop_item)
            self._crop_item = self._crop_anchor = self._crop_page = None
        if self.annotations is not None and self.annotations.drawing:
            self.annotations.cancel_draw()  # Esc mid-gesture: drop the preview, commit nothing
        self._armed = None
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        self.armedChanged.emit(None)

    # ---- night reading mode (M49) -----------------------------------------------

    @property
    def night_mode(self) -> bool:
        return self._night

    def set_night_mode(self, on: bool) -> None:
        """Toggle the view-only pixel inversion. Restyles the page backgrounds (the pre-render
        placeholder must match the inverted page) and re-renders what's visible; the cache is
        dropped because its pixmaps were produced under the other palette."""
        if on == self._night:
            return
        self._night = on
        self._cache.clear()
        brush = QBrush(QColor(0, 0, 0) if on else QColor(0xFF, 0xFF, 0xFF))
        for p in self._pages:
            p["bg"].setBrush(brush)
        self._render_visible()

    # ---- armed-CROP drag (M48) --------------------------------------------------

    @property
    def cropping(self) -> bool:
        return self._crop_item is not None

    def begin_crop_drag(self, scene_pt) -> bool:
        """Anchor a crop drag on the page under ``scene_pt`` (False off-page — the tool stays
        armed, a mis-click doesn't waste the arm). Shows a dashed keep-this-area band."""
        page_index, _local = self.page_and_local_at(scene_pt)
        if page_index is None:
            return False
        self._crop_page = page_index
        self._crop_anchor = scene_pt
        item = QGraphicsRectItem(QRectF(scene_pt, scene_pt))
        pen = QPen(QColor(0, 120, 215))
        pen.setWidth(2)
        pen.setStyle(Qt.PenStyle.DashLine)
        item.setPen(pen)
        item.setBrush(QBrush(QColor(0, 120, 215, 30)))
        item.setZValue(11)
        self.scene().addItem(item)
        self._crop_item = item
        return True

    def update_crop_drag(self, scene_pt) -> None:
        if self._crop_item is not None:
            self._crop_item.setRect(QRectF(self._crop_anchor, scene_pt).normalized())

    def finish_crop_drag(self) -> None:
        """End the drag: drop the band and emit ``cropDragged`` with the kept area in content
        coords (clamped to the page by ``local_box_from_scene_rect``). A sub-8-point drag is
        discarded as accidental — an 8pt-tall page is not a plausible crop."""
        if self._crop_item is None:
            return
        page_index, rect = self._crop_page, self._crop_item.rect()
        if self._crop_item.scene() is self.scene():
            self.scene().removeItem(self._crop_item)
        self._crop_item = None
        self._crop_anchor = None
        self._crop_page = None
        box = self.local_box_from_scene_rect(page_index, rect)
        if box[2] - box[0] >= 8 and box[3] - box[1] >= 8:
            self.cropDragged.emit(page_index, box)

    def _center_anchor(self) -> "tuple[int, float, float] | None":
        """The content point under the viewport centre as ``(page_index, fx, fy)``, where fx/fy are
        fractions of that page's scene rect. The rect scales uniformly with zoom, so the fractions
        are zoom-invariant — a handle that lets a zoom hold the centre fixed instead of snapping to
        a page top / left edge. ``None`` before any page exists."""
        if not self._pages:
            return None
        center = self.mapToScene(self.viewport().rect().center())
        pi = self._current
        for i, p in enumerate(self._pages):
            if p["y"] <= center.y() <= p["y"] + p["h"]:
                pi = i
                break
        p = self._pages[pi]
        fx = (center.x() - p["x"]) / p["w"] if p["w"] else 0.5
        fy = (center.y() - p["y"]) / p["h"] if p["h"] else 0.5
        return pi, fx, fy

    def _restore_center_anchor(self, anchor: "tuple[int, float, float]") -> None:
        """Scroll so the ``(page_index, fx, fy)`` from :meth:`_center_anchor` sits back under the
        viewport centre. ``centerOn`` clamps to the scene bounds, and the view alignment re-centres
        a page that now fits without scrollbars — so both zoom-out-to-fit and zoom-in-past-edge land
        where the eye expects."""
        pi, fx, fy = anchor
        if not (0 <= pi < len(self._pages)):
            return
        p = self._pages[pi]
        self.centerOn(p["x"] + fx * p["w"], p["y"] + fy * p["h"])
        self._render_visible()

    def set_zoom(self, zoom: float, keep_page: bool = True, fit: "str | None" = None) -> None:
        # ``fit`` records the sticky fit-mode this zoom represents ("width" / "page"); it is re-applied
        # on a viewport resize so the fit follows the window (e.g. a Pages-sidebar toggle). A manual
        # zoom passes None, which cancels any sticky fit.
        self._fit_mode = fit
        zoom = max(_MIN_ZOOM, min(_MAX_ZOOM, zoom))
        if abs(zoom - self._zoom) < 1e-6:
            return
        anchor = self._current
        # A manual zoom (no sticky fit) holds the content under the viewport centre fixed, so the
        # view zooms *into* what you're looking at rather than drifting toward a corner. A fit zoom
        # re-lands on the current page's top — its own contract (see fit_width/_center_horizontally).
        center = self._center_anchor() if fit is None else None
        self._zoom = zoom
        self._build_scene()
        if keep_page:
            if center is not None:
                self._restore_center_anchor(center)
            else:
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
        # A **content-only** edit (annotation, form fill) leaves every page's geometry alone, so the
        # exact scroll offset is still meaningful — keep it. Snapping to the current page's top
        # would yank the reader away from the spot they just marked up, and the "current" page is
        # whichever owns the viewport centre, which may not even be the page they edited.
        # A **structural** edit (insert / delete / reorder / rotate / crop) remaps the layout, so
        # there the current-page anchor is the only sensible place to land.
        layout = self._layout_signature()
        offset = self.verticalScrollBar().value()
        self._build_scene()
        if self._layout_signature() == layout:
            self.verticalScrollBar().setValue(offset)
            self._render_visible()
        else:
            self.goto_page(self._current)

    def _layout_signature(self) -> tuple:
        """The page geometry the scroll offset is meaningful against — unchanged by a content edit."""
        return tuple((p["x"], p["y"], p["w"], p["h"]) for p in self._pages)

    def set_current_page(self, index: int) -> None:
        """Mark ``index`` as the current page **without scrolling to it**.

        Used after an edit lands on a page that isn't the one under the viewport centre: the
        sidebar highlight should follow the work you just did, not where the scroll happens to sit.
        Scrolling here would defeat the whole point (see :meth:`reload`)."""
        if 0 <= index < len(self._pages) and index != self._current:
            self._current = index
            self.currentPageChanged.emit(index)

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
