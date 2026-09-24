"""Continuous-scroll PDF viewer (PLAN.md, Viewer — Option B).

A ``QGraphicsView``/``QGraphicsScene`` lays every page of the ``VirtualDocument`` out in a
single vertical strip. Page *geometry* is cheap (from ``page.rect``, no rendering), so the whole
strip is laid out up front; page *pixels* are rendered lazily — only pages intersecting the
viewport (plus a small prefetch) get a PyMuPDF pixmap, cached in a bounded LRU keyed by
``(index, device_scale, rotation)``. Zoom (incl. fit-width/fit-page) and 90° view rotation are
scalar re-layouts.

**Three scales, not one** (M88). ``zoom`` is the magnification the reader asks for, where 1.0 means
true physical size; ``scale`` (= ``zoom × logicalDpi/72``) is what the *layout* uses, scene units
per PDF point; ``device_scale`` (= ``scale × devicePixelRatio``) is what the *rasteriser* uses. The
three collapsed into one before M88, which is why 100% drew a Letter page 6.375" wide and a 1.75×
laptop panel showed an upscaled, blurry page.

This is the M2 view surface: render / scroll / zoom / fit / rotate / current-page tracking.
Text selection (M3) and drag-reorder (M4) build on the same scene later.
"""

from __future__ import annotations

import math
import time
from bisect import bisect_left, bisect_right
from contextlib import contextmanager

import pymupdf as fitz
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QImage, QPainter, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QToolTip,
)

from klarpdf.model.virtual_document import VirtualDocument
from klarpdf.model.form_fields import NewField
from viewer.pieces import TILE, Sizer, Tiles
from viewer.pixmap_cache import pixmap_cache
from viewer.resize_handles import cursor_for
from viewer.tools import ArmedTool, InteractionMode
from viewer.reveal import is_settled

_PAGE_GAP = 14          # px between pages in the strip
_PREFETCH = 2           # pages to render above/below the viewport, at ordinary page sizes
# What one direction of prefetch may spend (M87.1). Prefetch is *speculative* — pages the reader
# has not asked for and at high zoom cannot reach without several more scrolls — so it gets a byte
# allowance rather than a page count. 48 MB is ~26 Letter pages at 100%, far more than the 2 the
# band ever wants, so ordinary reading is untouched; it falls to 1 page once a page passes 48 MB
# and to 0 past 96 MB, which is where prefetching a page costs more than the whole visible band.
_PREFETCH_BYTES = 48 * 1024 * 1024
# How often the deferred prefetch drains one page (M92.4). One frame at 60 Hz: often enough that the
# band is ready again within a couple of frames of a gesture ending, slow enough that the event loop
# gets a turn between two pages that may cost 91 ms each. See :meth:`PdfView._drain_prefetch`.
_PREFETCH_TICK_MS = 16
# How long drawing the pages on screen may take before a resize stops redrawing them at every step
# (M152.1). About two screen refreshes. The view times every drawing, so this is the only number:
# nothing looks at what a document contains. When the pages on screen last took longer than this,
# a resize stretches their pictures and redraws them once the edge rests. A quicker page is redrawn
# at every step, as before. Checked against the corpus in `PLAN.md` §M152.
_DRAW_BUDGET_S = 0.030
# How long a window edge must rest before a stretched page is redrawn (M152.1). A drag sends a
# resize for every mouse move, so a pause this long means the reader has stopped.
_RESIZE_SETTLE_MS = 200
# Each piece is drawn this many device pixels larger on every side, and the margin is cut off
# (M152.2). MuPDF smooths an edge a little differently where a clip cuts through it: on the NADA
# cover one row of a shape's edge came out lighter on a 33-pixel stretch at a piece's side. With
# the cut 2 px away, the pieces match the whole page there (measured in `PLAN.md` §M152).
_PIECE_MARGIN = 2
# A clip is shrunk by this much, in device pixels, so that MuPDF's rounding of it outward cannot
# add a row or a column to a piece.
_CLIP_INSET = 1e-3
# The magnification a reader may *ask* for, 25%–500% (M88.6). Sequenced after M88.1 on purpose:
# the DPI correction shifts every number, so choosing bounds before it meant choosing twice. The old
# floor of 10% drew a Letter page 62x80 px — a thumbnail, not a view — and the old 800% ceiling was
# set when a page there cost 118 MB; at physical scale on a 1.75x panel the same ceiling would cost
# ~670 MB for one page. Note these bound the *ask*, not every possible zoom: see _clamp_zoom, which
# lets a Fit go below the floor, because a fit that does not fit is not a fit. Only a Fit — until
# M149 the same lowered floor reached the buttons and the zoom field, so a small enough window let
# a reader ask for 3% (#377).
_MIN_ZOOM, _MAX_ZOOM = 0.25, 5.0
_ZOOM_STEP = 1.25
# One PDF point is 1/72", so a page is drawn at true physical size when one point maps to
# ``logicalDpi / 72`` of the screen's logical pixels — 1.333 at the 96 logical DPI Windows reports
# for every screen (M88.1). Before M88 a point was one logical pixel, so we drew a 612 pt Letter
# page 612 px = 6.375" wide and called it 100%: three-quarters of physical size. Browsers and
# Acrobat all define 100% as physical, which is why the same document looked smaller here than in
# Edge at the same percentage. 96 is the fallback for a screen we cannot ask (see _refresh_display).
_FALLBACK_LOGICAL_DPI = 96.0
_WHEEL_NOTCH = 120      # one mouse-wheel detent, in eighths of a degree (Qt's unit)
_WHEEL_QUIET_MS = 250   # gap that ends a wheel gesture (a flywheel wheel coasts well past the hand)
# The longest the M91.4 coast-mute may keep the wheel silent, however many events keep arriving
# (M92.3). **This is the number that makes the mute non-renewable**, and without it the wheel could
# be dead indefinitely — owner-reported 2026-07-31 as *"the mouse wheel becomes unavailable to resume
# scrolling again for a long duration; I have to click around before it becomes responsive"*, and
# reproduced at **200 consecutive events over 4 seconds, every one swallowed**, recovering only after
# a 300 ms pause. The cause is that a swallowed event still refreshed ``_last_wheel_ts``, so the
# quiet window could never elapse while events kept coming — and the reader's instinct on finding
# that scrolling has stopped is to scroll *more*, which is exactly what held it open. Clicking around
# "fixed" it only because it is time spent not touching the wheel.
#
# **800 ms is measured, not picked.** A coast probe on the owner's hardware (2026-07-31) recorded the
# decelerating tail after a hard spin at **~660 ms in discrete mode and ~720 ms in free-spin** — and
# note that discrete/ratchet mode coasts too, which contradicted the guess that it would not. The
# ceiling has to cover that tail or M91.4's defect returns; 800 ms clears both with a little room and
# bounds the worst case to a hiccup rather than a fault. It is the one number here that trades the
# owner's two reports against each other, so it is deliberately easy to move.
_WHEEL_MUTE_MAX_MS = 800.0
# What one *line* of a mouse-wheel detent moves, in logical px at 100% zoom (M92.1). Multiplied by
# Windows' "lines to scroll" setting (``QApplication.wheelScrollLines()``, default 3) and by the
# zoom, so a detent is 96 px at 100% and always moves the same amount of *document*.
#
# What it replaces was not a considered choice at all: Qt's ``QGraphicsView`` sets the vertical
# ``singleStep`` to **viewportHeight / 20** (measured: viewport 846 -> 42, viewport 832 -> 41), so a
# detent was 15% of the *window height* and nothing else — unrelated to the document, the text or the
# zoom, and worse the more screen the window was given. `_place_window` opens at the full available
# screen height by design, which put that derivation at its maximum: on the owner's 2560x1440 display
# the viewport is 1246 px tall, so one detent moved **183 px — 19% of a page, ten lines of body
# text** (measured 2026-07-30).
#
# **32 is measured against Edge, not borrowed from it.** This shipped at **40** — the constant
# Chromium and Gecko share for the *web* — and the owner's side-by-side then put us at **10 lines**
# per detent against Edge's **8** in the same document (2026-07-31). 40 x 0.8 = 32. Two independent
# observations agree on the target: "Edge moves about half" of the old 183 px is ~91 px, and 8/10 of
# the 40-constant's 109 px is ~87 px. The likely reason the web constant was the wrong one to borrow
# is that Edge renders PDFs through **PDFium**, not the generic web scroll path, so its viewer never
# used the 40 px/line figure in the first place. Recorded because the temptation on any future tune
# is to reach back for the "standard" number: the standard is for web pages, and this is a PDF
# viewer. The reader's own control remains the Windows lines-to-scroll slider.
_WHEEL_LINE_PX = 32.0

# How long a wheel detent's glide lasts, in ms (M92.2). **Owner's pick, made with the wheel in
# hand** against a live toggle (`[`/`]` in the throwaway demo), and it sits at the outer edge of the
# two bounds the benchmark drew rather than comfortably inside them — recorded so the edge is a
# choice and not an oversight:
#
# * **lag** — how far the page trails where the wheel has already asked it to be, during a sustained
#   5-detent/second spin: 60 px at 130 ms, 66 px at 170 ms, **88 px at 200 ms**. One detent is 87 px,
#   so at 200 ms the page runs about one whole click behind the hand.
# * **duty cycle** — the share of wall time a glide is in flight during that spin: 70% at 130 ms,
#   86% at 170 ms, and 100% at 200 ms *as the bound was originally measured*. At 100% the page never
#   rests where it was asked to be, and every page-boundary rasterise (4-48 ms, synchronous — see
#   `_render_pixmap`) is *certain* to land mid-glide, where it reads as a stutter rather than being
#   invisible inside a jump.
#
# **That second bound no longer binds at 200 ms, because `_glide_tick` ends on the pixels rather
# than on the clock.** An ease-out asymptotes, so a detent's motion is complete at t = 0.80 — 160 ms,
# measured — and stopping there cuts duty at 5 detents/second from **100% to 80%**, and at 3/second
# from 60% to 48%. The lag bound is the one that still argues for less: 88 px against a detent of 87.
#
# 170 ms is the largest value inside both bounds as first drawn, if this ever wants walking back.
# What 200 ms buys is measured too: the worst single-frame content jump falls from **87 px to 20 px**
# (19 px on the real display), against 12 distinct positions where there used to be one.
_WHEEL_EASE_MS = 200.0

_ZOOM_COALESCE_MS = 16  # one frame at 60 Hz — the Ctrl+wheel accumulator's flush interval (M86.2)

# Arrow key → unit (dx, dy) direction for nudging an object selection (M78.2); page-y grows down.
_NUDGE_KEYS = {
    Qt.Key.Key_Left: (-1.0, 0.0),
    Qt.Key.Key_Right: (1.0, 0.0),
    Qt.Key.Key_Up: (0.0, -1.0),
    Qt.Key.Key_Down: (0.0, 1.0),
}


class _Wip:
    """One page being drawn in pieces at one size (M152.2).

    ``page``, ``clip`` and ``total`` are what :meth:`PdfView._render_target` returned.
    ``origin`` is the top-left of the whole picture in PyMuPDF's device pixels, and ``rendered``
    its width and height before the extra spin. ``pieces`` are the pieces on screen, each with its
    scene item. ``seconds`` and ``pixels`` add up what the pieces cost. ``stand_in`` is the store
    key of the picture standing in below them.
    """

    __slots__ = ("key", "tiles", "sizer", "page", "clip", "total", "origin", "rendered", "pieces",
                 "seconds", "pixels", "stand_in")

    def __init__(self, key, tiles, sizer, page, clip, total, origin, rendered) -> None:
        self.key, self.tiles, self.sizer = key, tiles, sizer
        self.page, self.clip, self.total = page, clip, total
        self.origin, self.rendered = origin, rendered
        self.pieces: list = []
        self.seconds = 0.0
        self.pixels = 0
        self.stand_in = None               # the store key of the picture standing in, to pin


class _Carried:
    """A picture of what a page showed, kept to stand in while the page is drawn again (M152.2).

    ``rect`` is the part of the page it shows, in points of the page as displayed, so it can be
    placed at any zoom. ``total`` is the spin it was drawn with: after a rotation it no longer fits.
    """

    __slots__ = ("pixmap", "rect", "total")

    def __init__(self, pixmap: QPixmap, rect: QRectF, total: int) -> None:
        self.pixmap, self.rect, self.total = pixmap, rect, total


class PdfView(QGraphicsView):
    """Vertical continuous-scroll renderer over a VirtualDocument."""

    currentPageChanged = Signal(int)
    zoomChanged = Signal(float)  # emitted whenever the zoom factor changes (1.0 == 100%)
    armedChanged = Signal(object)  # the armed ArmedTool, or None — so the toolbar can light a button
    applyTextTool = Signal(object)  # an ArmedTool (HIGHLIGHT/REDACT_TEXT) fired on a drag-over-text release
    cropDragged = Signal(int, tuple)  # an armed CROP drag finished: (page_index, content box) — M48
    foreignMoved = Signal(int, object, float, float)  # a foreign annotation was dragged — M67
    foreignAdopt = Signal(int, object)  # a foreign annotation was double-clicked — M68
    noteGlyphClicked = Signal(int, object)  # an on-page note badge was clicked: (page, mark) — M90.2
    externalLinkClicked = Signal(str)  # a web link was clicked — MainWindow hands it to the browser (M149)

    def __init__(self, vdoc: VirtualDocument, parent=None) -> None:
        super().__init__(parent)
        self._vdoc = vdoc
        # **Three numbers, not one** (M88). ``_zoom`` is the magnification the *reader* asks for and
        # the % indicator shows; the two derived scales below are what the code actually draws with.
        # Before M88 all three were this one field, which is why 100% was neither physical size nor
        # native resolution. See :meth:`scale` / :meth:`device_scale` for what each one means.
        self._zoom = 1.0
        self._logical_dpi = _FALLBACK_LOGICAL_DPI  # this screen's logical DPI (M88.1)
        self._dpr = 1.0                            # this screen's devicePixelRatio (M88.2)
        self._rotation = 0  # view rotation in degrees: 0/90/180/270
        # This view's handle on the **process-global** pixmap store (M87.2). Formerly a private
        # OrderedDict bounded at 48 entries — which bounded pages, not bytes, and so let one
        # Ctrl+wheel sweep reach 4.3 GB. See viewer/pixmap_cache.py for the budgets and why there
        # are two of them. Shared means a closed window must hand its entries back — closeEvent
        # does, and the handle releases on collection as a backstop.
        self._cache = pixmap_cache.owner()
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
        # The scale the pages were laid out at. A zoom, or a screen with another DPI, changes
        # :attr:`scale` before the scene is built again, and the old layout is read before that.
        self._layout_scale = self.scale
        # Two indexes that keep the render pass O(visible band) instead of O(document length),
        # both maintained by _build_scene / _render_visible (M87.3):
        self._page_tops: list[float] = []   # each page's scene y, non-decreasing — binary-searched
        self._painted: set[int] = set()     # the pages currently holding a pixmap
        # Prefetch runs off the scroll's critical path (M92.4): _render_visible queues the margin
        # here and this timer rasterises one page per tick, never while a glide is animating.
        # ``_scroll_dir`` (+1 down, -1 up, 0 unknown) orders the queue towards where the reader is
        # going. Parented to the view, so it dies with it.
        self._prefetch_queue: list[int] = []
        self._scroll_dir = 0
        self._last_scroll_value = 0
        self._prefetch_timer = QTimer(self)
        self._prefetch_timer.setInterval(_PREFETCH_TICK_MS)
        self._prefetch_timer.timeout.connect(self._drain_prefetch)
        # A slow page is stretched while a window edge moves, and drawn once it rests (M152.1).
        # ``_draw_rate`` is how long each page's last drawing took, in seconds per device pixel,
        # which is what decides "slow" (see _DRAW_BUDGET_S). ``_stand_ins`` are the painted pages
        # showing a picture drawn for another size. The timer is the wait for the edge to rest;
        # while it runs, nothing is drawn. Parented to the view, so it dies with it.
        self._draw_rate: dict[int, float] = {}
        self._stand_ins: set[int] = set()
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.timeout.connect(self._render_visible)
        # A slow page is drawn in pieces (M152.2). ``_wip`` holds each page being drawn that way at
        # the current size: its tile grid, its piece sizer and the pieces on screen.
        # ``_dlists`` keeps each such page's display list, so a piece does not read the page
        # again: on the NADA cover that cost 45 ms a piece, against 3–4 ms from the list.
        # ``_carried`` is a picture of what a page showed before a zoom, an edit or a minimize,
        # kept to stand in while the page is drawn again. The timer runs the turns.
        self._wip: dict[int, _Wip] = {}
        self._dlists: dict[int, "fitz.DisplayList"] = {}
        self._carried: dict[int, _Carried] = {}
        self._piece_timer = QTimer(self)
        self._piece_timer.setSingleShot(True)
        self._piece_timer.timeout.connect(self._turn)
        self._current = 0
        # Overlay controllers (set by MainWindow): text selection + search (M3), form fill (M14),
        # annotations (M20). They own their items and expose repaint(), called after every rebuild.
        self.selection = None
        self.search = None
        self.form = None
        self.annotations = None
        self.links = None  # internal-link navigation (M33), set by MainWindow
        # The sticky Highlight colour, as an (r,g,b) tuple, kept in sync by MainWindow (M76.2) so
        # an armed Highlight previews the *chosen* colour under the drag — not a fixed yellow that
        # only "converts" to the real colour on release (owner report). None → the default yellow.
        self.highlight_preview_color = None
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
        # True while the armed concrete redact tool was resolved from the combined REDACT slot
        # (M72) — a release that commits nothing then restores REDACT, so a stray click can't
        # leave the *resolved* gesture locked in for the next press.
        self._redact_combined = False
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
        # Page layout (M78): "single" = the vertical strip; "facing" = two-page rows (1|2, 3|4 …).
        # View-only — a pure re-layout of the same pages, like zoom and rotation.
        self._page_layout = "single"
        # Slideshow (M78): while True the view narrows to one-page reading — left-click, the
        # arrow/space/page keys and the wheel step whole *rows* instead of scrolling/selecting;
        # Esc (which this view leaves unconsumed when nothing is armed) bubbles to MainWindow,
        # which exits. ``_slide_row`` is the projected row (an index into :meth:`_layout_rows`) —
        # the mode's own position, so a step never has to re-derive where it is from the scroll
        # offset. ``_wheel_accum`` quantises a hi-res wheel to whole detents.
        # ``_wheel_muted`` parks a coasting wheel after a click / key step (see wheelEvent).
        self._slideshow = False
        self._slide_row = 0
        self._wheel_accum = 0
        self._wheel_muted = False
        self._last_wheel_ts = 0
        # The M92.3 ceiling: when the mute was armed (this view's monotonic clock), and the
        # direction of the coast it is swallowing (0 until the first swallowed event). Together
        # these make the mute non-renewable — see :data:`_WHEEL_MUTE_MAX_MS`.
        self._wheel_mute_start = 0.0
        self._wheel_mute_dir = 0
        # True only while inside wheelEvent, so a move the *wheel* made does not park the wheel
        # (M91.4) — the slideshow steps via goto_page, which arms the mute for everyone else.
        self._wheel_driving = False
        # Sub-pixel carry for the M92.1 wheel step: the scrollbar takes whole pixels, so the
        # fraction each detent leaves over is kept and spent on the next one. Without it a wheel
        # whose step lands just under a pixel boundary loses ground on every event.
        self._scroll_remainder = 0.0
        # The M92.2 glide: a detent moves a *target*, and this timer walks the bar to it on an
        # ease-out curve. ``_glide_target is None`` means nothing is in flight. Parented to the
        # view, so it dies with it (the repo rule for view-owned timers).
        self._smooth_scrolling = True
        self._glide_target: float | None = None
        self._glide_origin = 0.0
        self._glide_start = 0.0
        self._glide_timer = QTimer(self)
        self._glide_timer.timeout.connect(self._glide_tick)
        # One geometry change = one rasterise (M86.1). ``_render_held`` counts open
        # :meth:`_hold_render` blocks; while any is open, _render_visible defers.
        self._render_held = 0
        # Ctrl+wheel deltas accumulate here and apply once a frame (M86.2), so a burst is one
        # scene rebuild instead of N. ``_zoom_anchor`` is the pointer the flush anchors on.
        self._zoom_pending = 0.0
        self._zoom_anchor = None
        # Where the view was last sent (M151): per axis, the scene coordinate meant for the
        # viewport's top-left corner, the scroll value the view was left at, and whether an end of
        # the document stopped it short. See :meth:`_scroll_to`.
        self._sent: dict[str, tuple[float, int, bool]] = {}
        self._sent_page = 0
        # ...and the content point that placement held, with the spot it held it at ("centre" for
        # the zoom buttons, "top" for a resize with a fit on), so the next step of the same kind
        # starts from exactly that point. See :meth:`_kept_anchor`.
        self._sent_axes: frozenset[str] = frozenset()
        self._sent_anchor: "tuple[tuple[int, float, float], str] | None" = None
        self._zoom_timer = QTimer(self)
        self._zoom_timer.setSingleShot(True)
        self._zoom_timer.timeout.connect(self._flush_wheel_zoom)
        # A screen change (M88.3) answers on the event loop, not inside the Qt callback that
        # reported it — see :meth:`event`. Parented to this view, so it dies with it.
        self._display_pending = ""
        self._display_timer = QTimer(self)
        self._display_timer.setSingleShot(True)
        self._display_timer.timeout.connect(self._apply_display_change)
        self._refresh_display()   # logical DPI + DPR, before the first layout is computed
        self._build_scene()
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        # Grabbing the scrollbar is a deliberate move; a glide still running would fight the thumb
        # (M92.2). The keyboard and goto_page routes are covered by _park_coasting_wheel instead.
        self.verticalScrollBar().sliderPressed.connect(self.stop_glide)

    # ---- display metrics: what a PDF point is worth on *this* screen (M88) ---------

    @property
    def scale(self) -> float:
        """**Scene units per PDF point** — the layout scale, ``zoom × logicalDpi/72`` (M88.1).

        Everything that maps page points to scene coordinates uses this, not :attr:`zoom`: the page
        rects in :meth:`_build_scene`, every hit-test, and the annotation overlay. A scene unit is a
        *logical* pixel (the view transform is identity — zoom rebuilds the scene rather than scaling
        the view), so at ``zoom = 1.0`` a 612 pt Letter page is 816 logical px = 8.5 real inches.
        """
        return self._zoom * self._logical_dpi / 72.0

    @property
    def device_scale(self) -> float:
        """**Device pixels per PDF point** — ``scale × devicePixelRatio`` (M88.2).

        The only thing this is for is rasterising: the PyMuPDF matrix and the cache key. Handing Qt
        a pixmap rendered at :attr:`scale` on a 1.75× panel made the compositor upscale it 1.75×,
        which is why text was blurry on the *higher*-resolution of the owner's two screens. Render
        here and set the same ratio on the pixmap and the geometry is untouched — Qt lays a DPR'd
        pixmap out at its ``deviceIndependentSize()``, i.e. back at :attr:`scale`.
        """
        return self.scale * self._dpr

    def _refresh_display(self) -> str:
        """Re-read this view's screen metrics; name what changed (M88.3).

        Returns ``""`` (nothing moved), ``"render"`` (only the device pixel ratio — the layout is
        in *logical* units and is therefore unaffected, but every cached pixmap is now the wrong
        resolution) or ``"layout"`` (the logical DPI moved, so the page rects themselves are stale).
        On Windows the logical DPI is 96 on every screen and the scaling rides entirely in the DPR,
        so dragging between monitors is the cheap ``"render"`` case in practice.

        Not one-shot setup: the owner's machine pairs a 1.75× laptop panel with a 1.0× external
        Dell, so dragging the window across changes DPR *live* — a value sampled once is correct
        only on the screen the window opened on. ``QWidget.screen()`` falls back to the primary
        screen before the window is mapped, and to nothing at all under some offscreen platforms,
        which is what the 96 DPI default covers.
        """
        screen = self.screen() or QGuiApplication.primaryScreen()
        dpi = screen.logicalDotsPerInch() if screen is not None else _FALLBACK_LOGICAL_DPI
        dpr = self.devicePixelRatioF()
        if not dpi:                      # a screen reporting 0 would collapse the layout to nothing
            dpi = _FALLBACK_LOGICAL_DPI
        if dpi == self._logical_dpi and dpr == self._dpr:
            return ""
        kind = "layout" if dpi != self._logical_dpi else "render"
        self._logical_dpi, self._dpr = dpi, dpr
        return kind

    def event(self, ev) -> bool:
        """Notice a screen change through Qt's own **widget** events (M88.3).

        Deliberately not a ``QWindow.screenChanged`` connection, which is what this first shipped as
        and which **segfaulted CI on Linux**. Two lifetime hazards, both removed by asking the
        widget instead of a foreign object:

        * the sender is the *window's* ``QWindow``, not this view, so the connection outlives a
          destroyed view — and a slot invoked on freed memory is a crash, not an exception. These
          events are delivered to the widget and die with it.
        * the old handler rebuilt the scene **inside** the callback, so ``scene.clear()`` destroyed
          every item while Qt was mid-show. The response is now deferred to the event loop (see
          ``_display_timer``), which also collapses a burst of events into one rebuild.
        """
        if ev.type() in (QEvent.Type.DevicePixelRatioChange, QEvent.Type.ScreenChangeInternal):
            self._sync_display()
        return super().event(ev)

    def showEvent(self, event) -> None:
        """Re-read the metrics once the widget is mapped — the construction-time read hit the
        primary screen, which is not necessarily the one this window opens on. It only *schedules*
        a response; the first build is :meth:`open_at`'s job and already uses the fresh numbers."""
        super().showEvent(event)
        self._sync_display()

    def _sync_display(self) -> None:
        """Re-read the metrics and schedule the response on the event loop, never inline.

        The timer is parented to this view, so it is cancelled when the view dies and can never
        fire into a half-destroyed object — the property the old signal connection lacked.
        """
        kind = self._refresh_display()
        if not kind:
            return
        # "layout" wins over a pending "render": it is the strictly larger response.
        if kind == "layout" or self._display_pending != "layout":
            self._display_pending = kind
        self._display_timer.start(0)

    def _apply_display_change(self, kind: "str | None" = None) -> None:
        """Re-lay-out and/or re-rasterise for the screen this view is now on.

        A DPR change needs only a re-render: the layout is in logical units, and the cache key
        carries ``device_scale``, so the stale-resolution pixmaps are not reachable — they are
        simply re-rendered at the new ratio. A logical-DPI change moves the page rects too.
        """
        kind = kind or self._display_pending
        self._display_pending = ""
        if not self._pages or not kind:
            return
        if kind == "layout":
            anchor = self._current
            with self._hold_render():
                self._build_scene()
                self.goto_page(anchor)
        else:
            self._render_visible()

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

    def _layout_rows(self) -> list[tuple[int, ...]]:
        """Page indices grouped into layout rows: singletons for the vertical strip, pairs
        (1|2, 3|4 …, a trailing odd page alone) for the facing layout (M78)."""
        count = self._vdoc.page_count
        if self._page_layout == "facing" and count > 1:
            return [tuple(i for i in (row, row + 1) if i < count) for row in range(0, count, 2)]
        return [(i,) for i in range(count)]

    def _build_scene(self) -> None:
        scene = self.scene()
        # Keep a picture of what the pages on screen show, to stand in while they are drawn again
        # at the new size (M152.2). Taken now, while the items still exist.
        self._carry_visible()
        scene.clear()
        self._wip.clear()       # their pieces died with the scene
        # A new layout makes a remembered placement meaningless; every caller places the view
        # again once the scene is built, and that placement is remembered afresh (M151).
        self._sent, self._sent_anchor = {}, None
        self._pages.clear()
        self._page_tops.clear()
        self._painted.clear()   # scene.clear() destroyed the items, so nothing holds a pixmap
        self._stand_ins.clear()
        # ...and the queued prefetch indices refer to pages that no longer exist (M92.4). A rebuild
        # can change the page *count* (an edit, a two-page toggle), so a stale index is not merely
        # wrong, it is out of range; the next render pass queues afresh against the new layout.
        self._prefetch_queue.clear()
        self._prefetch_timer.stop()
        page_pen = QPen(QColor(0x80, 0x80, 0x80))
        # Night mode paints the not-yet-rendered page black — the inverse of the white page —
        # so a page scrolling into view doesn't flash bright before its pixmap lands.
        page_brush = QBrush(QColor(0, 0, 0) if self._night else QColor(0xFF, 0xFF, 0xFF))

        # Lay out by row: the single layout is one page per row; the facing layout (M78) sits a
        # pair side by side, the row taking the taller page's height. ``widest`` spans the widest
        # row, so pairs centre as a unit exactly as single pages centred alone.
        rows = self._layout_rows()
        z = self.scale   # scene units per point — zoom × logicalDpi/72, not the bare zoom (M88.1)
        self._layout_scale = z
        sizes = {i: self._natural_size(i) for row in rows for i in row}
        row_width = {
            row: sum(sizes[i][0] for i in row) * z + (len(row) - 1) * _PAGE_GAP for row in rows
        }
        widest = max(row_width.values(), default=1.0)
        y = float(_PAGE_GAP)
        placed: dict[int, tuple] = {}
        for row in rows:
            # Centre each row within the scene's content band. The band is inset by _PAGE_GAP on
            # both sides (the sceneRect below is widest + 2*_PAGE_GAP), so the left inset must be
            # added here too — without it the widest row sat flush at scene-x 0 and the whole strip
            # rendered ~_PAGE_GAP px left of centre in the window.
            x = _PAGE_GAP + (widest - row_width[row]) / 2.0
            row_h = 0.0
            for i in row:
                w, h = sizes[i][0] * z, sizes[i][1] * z
                placed[i] = (x, y, w, h)
                x += w + _PAGE_GAP
                row_h = max(row_h, h)
            y += row_h + _PAGE_GAP
        for i in range(self._vdoc.page_count):
            x, py, w, h = placed[i]
            # Geometry goes in the item POSITION (local rect at origin), so the child pixmap —
            # placed at the parent's (0,0) — inherits the page's scene position. Encoding x/y in
            # the rect instead leaves the item at (0,0) and piles every pixmap at the origin.
            bg = QGraphicsRectItem(QRectF(0, 0, w, h))
            bg.setPos(x, py)
            bg.setPen(page_pen)
            bg.setBrush(page_brush)
            scene.addItem(bg)
            pix = QGraphicsPixmapItem(bg)  # child of bg → shares its position
            pix.setPos(0, 0)
            # ``total`` is the spin its pictures are drawn with. A carried picture is checked
            # against it after an edit has already changed the document (M152.2).
            self._pages.append({"bg": bg, "pix": pix, "x": x, "y": py, "w": w, "h": h,
                                "total": (self._page_extra(i) + self._rotation) % 360})
            self._page_tops.append(py)  # non-decreasing by construction — see _visible_range

        scene.setSceneRect(0, 0, widest + 2 * _PAGE_GAP, y)
        self._render_visible()
        # scene.clear() above discarded any overlay items; repaint them from logical state.
        for overlay in (self.annotations, self.form, self.selection, self.search):
            if overlay is not None:
                overlay.repaint()
        self._reposition_overlay_editors()  # an open inline editor follows the zoom

    def _reposition_overlay_editors(self) -> None:
        """Move any open inline editor (form field / text box, and the M90 note popup the
        annotation overlay carries) back onto its target after the view geometry changes (zoom or
        scroll), so it doesn't get left behind."""
        for overlay in (self.form, self.annotations):
            if overlay is not None:
                overlay.reposition_editor()

    # ---- overlay geometry helpers (used by selection + search) ------------------

    def page_and_local_at(self, scene_pt) -> tuple[int | None, "QPointF | None"]:
        """Map a scene point to ``(page_index, point in unrotated page points)``.

        Returns the page whose vertical band contains the point; when the facing layout (M78) puts
        two pages in that band, the one whose **x-range** contains the point wins, else the
        nearest by x — so a hit on the right-hand page maps to it, and a margin click still maps
        to the adjacent page (the single-layout behaviour, which never x-checked). The local point
        is mapped back through any per-page rotation so it lands in the source coordinate space
        (where word boxes and widget rects live). ``(None, None)`` when the point falls in a gap
        above/below all pages.
        """
        candidates = [
            (i, p) for i, p in enumerate(self._pages)
            if p["y"] <= scene_pt.y() <= p["y"] + p["h"]
        ]
        if not candidates:
            return None, None
        chosen = next(
            ((i, p) for i, p in candidates if p["x"] <= scene_pt.x() <= p["x"] + p["w"]),
            min(candidates,
                key=lambda c: min(abs(scene_pt.x() - c[1]["x"]),
                                  abs(scene_pt.x() - (c[1]["x"] + c[1]["w"])))),
        )
        i, p = chosen
        dx = (scene_pt.x() - p["x"]) / self.scale
        dy = (scene_pt.y() - p["y"]) / self.scale
        w, h = self._unrotated_size(i)
        lx, ly = self._point_to_source(w, h, self._display_rotation(i), dx, dy)
        ox, oy = self._crop_origin(i)  # displayed frame → content coords
        return i, QPointF(lx + ox, ly + oy)

    def scene_rect_for_box(self, page_index: int, box: tuple) -> QRectF:
        """Map a box in unrotated page points (x0,y0,x1,y1) to its scene rect, accounting for any
        per-page rotation so overlays align with the displayed (spun) page. ``box`` is in content
        coords; a crop override shifts the displayed frame, so its origin is subtracted first."""
        p = self._pages[page_index]
        z = self.scale
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
        z = self.scale
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
        tr.scale(self.scale, self.scale)
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
        dx = (scene_pt.x() - p["x"]) / self.scale
        dy = (scene_pt.y() - p["y"]) / self.scale
        w, h = self._unrotated_size(page_index)
        lx, ly = self._point_to_source(w, h, self._display_rotation(page_index), dx, dy)
        ox, oy = self._crop_origin(page_index)
        return QPointF(lx + ox, ly + oy)

    def ensure_box_visible(self, page_index: int, box: tuple) -> None:
        """Bring ``box`` into view and leave it somewhere a reader will actually look.

        Deliberately **not** ``ensureVisible``, which scrolls the *minimum* distance and so parks
        the box hard against whichever edge it travelled towards. Stepping forward through search
        results left every match ~60 px above the bottom of the window; stepping back left it ~60 px
        below the top. Same call, same margin — the asymmetry is the direction of travel, which is
        why Previous felt right and Next felt broken (owner-reported 2026-08-13; measured over 14
        consecutive presses each way on a 320-page prospectus, bottom margin 59.6–60.4 px on every
        Next). The taller the window, the worse it reads: on a maximised 1440p screen a hit lands in
        the bottom 5% of the page, which is nobody's idea of "revealed".

        So the rule is the one Preview and the browsers use. A box already sitting comfortably in
        view is **left alone** — stepping between two matches on the same screen must not shove the
        page around — and anything else is **centred**, which makes the two directions symmetric and
        stops the result depending on window height or zoom.

        Horizontal position is held unless the box is actually off to one side, since a document
        that fits the window sideways should not slide about while the reader pages through hits.
        """
        rect = self.scene_rect_for_box(page_index, box)
        visible = self.mapToScene(self.viewport().rect()).boundingRect()
        settled = is_settled(rect.top(), rect.bottom(), visible.top(), visible.bottom())
        off_to_the_side = rect.left() < visible.left() or rect.right() > visible.right()
        if settled and not off_to_the_side:
            return
        # A hit near either end of the document lands as close to centred as the document allows,
        # and the view remembers the rest (M151), so the page marked current is the hit's page.
        centre = self._centre()
        with self._hold_render():
            self._scroll_to(rect.center().x() - centre.x() if off_to_the_side else None,
                            rect.center().y() - centre.y() if not settled else None, page_index)

    # ---- mouse → text selection -------------------------------------------------

    def mousePressEvent(self, event) -> None:
        # Slideshow (M78): a left click advances one slide — the projector gesture — and nothing
        # else in the view (selection, forms, links) is reachable until Esc exits.
        if self.slideshow and event.button() == Qt.MouseButton.LeftButton:
            self._deliberate_step(1)
            event.accept()
            return
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
            if self._armed is ArmedTool.REDACT:
                # One Redact slot, two gestures (M72): the press point's text hit decides — a
                # drag starting on a word runs the text-flow redaction, a drag starting in
                # margin/image space rubber-bands a block. Resolved *at press* to the concrete
                # tool, then falls through to that tool's own branch below, so the armed-
                # selection tint, the release path and the one-shot disarm are exactly the
                # explicit tools' — nothing downstream knows the combined slot exists. A press
                # off any page stays armed and unresolved (a mis-click neither wastes the arm
                # nor locks in a gesture); a rotated view resolves to the block gesture, since
                # text selection is disabled there (see TextSelection).
                page_index, _local = self.page_and_local_at(scene_pt)
                if page_index is None:
                    event.accept()
                    return
                on_text = (self._rotation == 0 and self.selection is not None
                           and self.selection.has_word_at(scene_pt))
                resolved = ArmedTool.REDACT_TEXT if on_text else ArmedTool.REDACT_REGION
                self._armed = resolved
                self._redact_combined = True  # release restores REDACT if nothing commits
                self.armedChanged.emit(resolved)
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
            # A note glyph (M90.2) is the next most specific target, and likewise mode-independent:
            # it is a 15 px badge the user aimed at, and nothing else lives where it sits. Below
            # the armed tools above, so arming a tool still wins the press.
            if self.annotations is not None:
                noted = self.annotations.note_glyph_at(scene_pt)
                if noted is not None:
                    self.noteGlyphClicked.emit(*noted)
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
                # Click a web link → the window hands it to the system browser (M149, #333). Emitted
                # rather than opened here: `viewer/` imports nothing from `ui/`, and the one function
                # that hands a URL to a browser stays `ui.about._open_url`. Only the schemes
                # `links.openable_uri_at` allows get this far, so a `file:` link still just selects.
                if self.links is not None:
                    uri = self.links.openable_uri_at(scene_pt)
                    if uri is not None:
                        self.externalLinkClicked.emit(uri)
                        event.accept()
                        return
                if self.selection is not None and self.selection.begin(scene_pt):
                    event.accept()
                    return
        # GRAB (and any unhandled click) → QGraphicsView; ScrollHandDrag pans in GRAB mode.
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:
        if self.slideshow:
            event.accept()  # chrome-free reading (M78): no menus until Esc exits
            return
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
        # Clicking on impatiently turns every second press into a double-click, which the press
        # handler never sees — so half a fast click sequence used to vanish. In the slideshow a
        # double-click is simply the next slide (M78).
        if self.slideshow and event.button() == Qt.MouseButton.LeftButton:
            self._deliberate_step(1)
            event.accept()
            return
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
            # One-shot draw tools revert to SELECT after the gesture commits; Pen is sticky
            # (M73) — stroke after stroke on one arm, exited via Esc / the lit button / arming
            # another tool.
            if self._armed is not None and self._armed.draws and not self._armed.sticky:
                self.disarm()
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
            # An armed drag-over-text tool applies to what was just selected — then the sticky
            # markup trio (M73) stays armed for the next passage while text-redact disarms
            # (one-shot: destructive). A stray click that selected nothing leaves the tool armed
            # either way (no wasted arm). If the tool was resolved from the combined Redact slot
            # (M72), the no-commit click restores REDACT — otherwise the resolved text gesture
            # would stay locked in and the next press on a margin would drag-select instead of
            # rubber-banding.
            if self._armed is not None and self._armed.drags_text:
                if self.selection.selected_words():
                    self.applyTextTool.emit(self._armed)
                    if self._armed is not None and not self._armed.sticky:
                        self.disarm()
                elif self._redact_combined:
                    self._armed = ArmedTool.REDACT
                    self._redact_combined = False
                    self.armedChanged.emit(ArmedTool.REDACT)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        # Slideshow (M78): whole-slide steps instead of smooth scrolling — arrows / Space /
        # PgUp/PgDn move one slide. Esc is deliberately NOT consumed here: with nothing armed it
        # falls through the QGraphicsView chain to MainWindow, which exits the mode.
        if self.slideshow:
            key = event.key()
            if key in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_Space, Qt.Key.Key_PageDown):
                self._deliberate_step(1)
                event.accept()
                return
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp):
                self._deliberate_step(-1)
                event.accept()
                return
            if key in (Qt.Key.Key_Home, Qt.Key.Key_End):
                self._deliberate_step(-len(self._layout_rows()) if key == Qt.Key.Key_Home
                                     else len(self._layout_rows()))
                event.accept()
                return
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
            # Arrow keys nudge the selection (M78.2): 1 pt/press, Shift = 10 pt; a held key's
            # auto-repeat coalesces to one undo step. With nothing selected the arrows fall through
            # to the view's normal scrolling (below).
            nudge = _NUDGE_KEYS.get(event.key())
            if nudge is not None:
                step = 10.0 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
                if self.annotations.nudge_selection(nudge[0] * step, nudge[1] * step,
                                                     auto_repeat=event.isAutoRepeat()):
                    event.accept()
                    return
        # Ctrl+A selects every word in the **whole document** (M89.6) — what Edge and Brave do, and
        # the only non-arbitrary answer in a viewer that scrolls continuously. It lives here, not on
        # a window-level QAction, for exactly the reason :meth:`_navigation_key` sets out below: a
        # focused inline editor must keep its own select-all.
        if event.key() == Qt.Key.Key_A and event.modifiers() == Qt.KeyboardModifier.ControlModifier \
                and self.selection is not None:
            self.selection.select_all()
            event.accept()
            return
        if self._navigation_key(event):     # M89.1 / M89.2
            event.accept()
            return
        super().keyPressEvent(event)

    def reading_key(self, event) -> bool:
        """Handle a paging key that reached the **window** because nothing with focus claimed it.

        The sidebar panels hand ``Space`` over rather than swallowing it (M91.4), and it arrives
        here by ordinary key propagation — see :meth:`main_window.MainWindow.keyPressEvent`, which
        is the one caller. Returns whether the key was ours.

        The slideshow keeps its own keys: there the reader is stepping *slides*, and a scroll would
        be the wrong verb. Nothing routes here in that mode anyway — M78 hides the sidebar — but the
        guard is what makes that a decision rather than an accident.
        """
        return False if self._slideshow else self._navigation_key(event)

    def _navigation_key(self, event) -> bool:
        """``Home``/``End`` jump to the document's start/end (M89.1); ``Space``/``Shift+Space`` and
        ``PgDn``/``PgUp`` page down/up (M89.2, corrected by M91.4). Returns whether the key was ours.

        Qt left all five dead in this view: ``QAbstractScrollArea`` binds ``Home``/``End`` only on
        macOS, and ``Space`` nowhere — so the two gestures a reader reaches for most in a long
        document did nothing, while ``PgUp``/``PgDn`` worked.

        **All four of Home / End / Ctrl+Home / Ctrl+End are one verb** (owner call): a continuous
        strip has no "line" for the bare form to go to the start of, to a reader the Ctrl'd and bare
        forms are the same gesture, and Preview, Edge and Chrome bind them alike in a PDF. Both are
        the scrollbar's minimum/maximum — the literal reading, and it lets :meth:`_on_scroll` update
        the current page for free.

        ``PgDn``/``PgUp`` are **taken off Qt** here (M91.4) rather than left to the base class: they
        page by the same wrong distance ``Space`` did, and M89.2's own promise is that the two keys
        are one verb. Only the bare form; anything modified falls through untouched.

        **These live here rather than as window-level ``QAction`` shortcuts on purpose** — as does
        the ``Ctrl+A`` above, which points at this paragraph. A window shortcut fires wherever focus
        is, so ``Home`` / ``Space`` / ``Ctrl+A`` bound that way would hijack those keys from the
        inline text-box editor and the form-field editors — children of this viewport — where they
        mean line-start, a literal space, and select-all-in-this-field. Routed through the view, a
        focused editor consumes them first and never sees ours; the same reasoning that put the
        clipboard verbs behind ``_edit_copy``'s focus router (M59).

        The slideshow's own ``Home``/``End``/``Space`` bindings (M78) are untouched: that branch
        returns long before this one.
        """
        key, mods = event.key(), event.modifiers()
        none, ctrl = Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ControlModifier
        shift = Qt.KeyboardModifier.ShiftModifier
        # The keypad's Home/End carry KeypadModifier, so an exact match on the modifier set would
        # accept the main keyboard's key and quietly ignore the numeric keypad's — a difference no
        # reader means. Drop it before comparing; it says which key was pressed, not what was meant.
        if mods & Qt.KeyboardModifier.KeypadModifier:
            mods = mods ^ Qt.KeyboardModifier.KeypadModifier
        vbar = self.verticalScrollBar()
        if key in (Qt.Key.Key_Home, Qt.Key.Key_End) and mods in (none, ctrl):
            self._park_coasting_wheel()
            vbar.setValue(vbar.minimum() if key == Qt.Key.Key_Home else vbar.maximum())
            return True
        if key == Qt.Key.Key_Space and mods in (none, shift):
            self._page_scroll(forward=mods != shift)
            return True
        if key in (Qt.Key.Key_PageDown, Qt.Key.Key_PageUp) and mods == none:
            self._page_scroll(forward=key == Qt.Key.Key_PageDown)
            return True
        return False

    def _reading_stops(self, near: int, reach: int) -> list[int]:
        """The scroll offsets a paging key may land on within ``reach`` of offset ``near``,
        ascending: **the top of each page**, plus — for a page taller than the viewport — that page
        cut into the fewest **equal** steps that each still fit a screenful.

        This list is the whole of M91.4. ``Space`` used to be the scrollbar's own ``SliderPageStep``,
        which advances by the **viewport height**; the strip advances by the **page pitch**, which at
        Fit Page is one ``_PAGE_GAP`` *less* (the fit leaves ``2 * _PAGE_GAP`` of margin and the
        layout puts one gap back between the pages). So every press overshot by exactly 14 px and the
        error accumulated: measured 126 px into the screen by page 10 and past half a screen by page
        ~27, at which point the page counter reads one ahead of the page filling the top of the
        window — the owner's "bottom half of page 9 and top half of page 10 while it says 10".

        Landing on a stop instead of a raw screenful fixes that by construction and is one rule for
        three cases: **a page that fits** advances exactly one page; **several pages that fit at
        once** (zoomed out) advance as many whole pages as the screen holds, still aligned; **a page
        taller than the screen** takes equal steps whose last one lands exactly on the next page's
        top. That last part is why the subdivision is *equal* rather than "a screenful, then the
        remainder": the remainder can be a handful of pixels, i.e. a press that visibly does nothing,
        and — since every page of a document is usually the same height — it would do nothing once
        per page, for ever.

        Consecutive stops are at most a screenful apart by construction, so :meth:`_page_scroll`
        always finds one within reach; a reader who wheeled to an arbitrary offset is put back on the
        page's own grid by their next press.

        **Bounded by the viewport, not the document.** Only the pages a single step could reach are
        walked — ``_page_tops`` is non-decreasing, so two bisections find them — which keeps this the
        same work in a 5000-page document as in a five-page one. Building the whole list would cost a
        few milliseconds per press and be invisible next to the rasterise a page turn already pays,
        but O(document) in an input path is the trap M87.3 and M78.8 were spent closing, and it does
        not get to come back through the keyboard.
        """
        vbar = self.verticalScrollBar()
        screen = max(1, vbar.pageStep())
        if not self._page_tops:
            return []
        # ``_page_tops`` holds each page's scene y; the offset that puts its top edge under the
        # viewport's is that minus the gap above it — goto_page's arithmetic, so a paged step and a
        # clicked thumbnail land on the same pixel. Hence the +_PAGE_GAP converting the wanted band
        # of *offsets* back into the scene y the bisections search.
        first = max(0, bisect_right(self._page_tops, near - reach + _PAGE_GAP) - 1)
        last = min(len(self._page_tops) - 1,
                   bisect_right(self._page_tops, near + reach + _PAGE_GAP))
        edges = sorted({int(y) - _PAGE_GAP for y in self._page_tops[first:last + 1]})
        # The edge that *closes* the last page's segment: the top of the next page, or the end of
        # the document when there is none. Found by value, not by ``last + 1`` — a facing row's two
        # pages share a y (M78), and the page after `last` can be its partner rather than the next
        # row, which would close the segment against itself and leave the row unsteppable.
        after = bisect_right(self._page_tops, self._page_tops[last])
        edges.append(int(self._page_tops[after]) - _PAGE_GAP if after < len(self._page_tops)
                     else max(vbar.maximum(), edges[-1]))
        stops: list[int] = []
        for lo, hi in zip(edges, edges[1:]):
            span = hi - lo
            if span <= 0:
                stops.append(lo)                # a facing row's second page: same top, no segment
                continue
            n = -(-span // screen)          # ceil: the fewest equal steps that each fit a screenful
            stops.extend(lo + round(j * span / n) for j in range(n))
        stops.append(edges[-1])
        # Zoomed out far enough, the strip is barely longer than the viewport and the last pages'
        # tops sit past where the bar can scroll to. A stop the bar cannot reach is not a stop.
        return sorted({min(max(s, vbar.minimum()), vbar.maximum()) for s in stops})

    def _page_scroll(self, forward: bool) -> None:
        """One paging step — the **furthest reading stop within one screenful**.

        Furthest, not nearest, so a zoomed-out view showing five pages still advances five. With no
        stop in reach (only at the very end of the document, where the last stop is behind us) it
        falls back to the plain screenful and the bar clamps it, so the key still means "onwards".

        Parks a coasting wheel first, or the step is undone by events the hand stopped asking for
        seconds ago — see :meth:`wheelEvent`.
        """
        self._park_coasting_wheel()
        vbar = self.verticalScrollBar()
        value, screen = vbar.value(), vbar.pageStep()
        stops = self._reading_stops(value, screen)
        if forward:
            reachable = [s for s in stops if value < s <= value + screen]
            vbar.setValue(max(reachable) if reachable else value + screen)
        else:
            reachable = [s for s in stops if value - screen <= s < value]
            vbar.setValue(min(reachable) if reachable else value - screen)

    def wheelEvent(self, event) -> None:
        """**Ctrl+wheel zooms** (M80), **Shift+wheel pans horizontally** (M89.3); in the slideshow
        the wheel steps whole slides (M78); everywhere else it scrolls.

        Free-scrolling a mode that shows *one page per screen* is what let the view come to rest
        straddling two pages — and from a straddle the projected page and the page under the
        viewport centre disagree, so the next click appeared to jump to the wrong page. One detent,
        one slide; a hi-res wheel's fractional deltas accumulate to a detent first.

        A **coasting** wheel is parked — **in every mode since M91.4**, not just here. A flywheel
        wheel (and Windows' smooth scrolling) keeps emitting long after the hand has left it, so a
        click or key pressed during the coast-down is immediately undone by the events still
        arriving. M78 met this as "clicked eight times and the first slide never moved"; the owner
        met the *same* wheel again in ordinary reading (2026-07-30): spin hard back to page 1, press
        ``Space``, and the page "flickers and stays on page 1" — then the next press "moves only
        half a page". Both are the coast eating the step, which is why it reproduced **100% of the
        time when spun fast and never when scrolled slowly**, and why the count of dead presses grew
        with the flick: a harder spin coasts longer. Scrolling *up* at offset 0 is a no-op, so the
        coast is invisible until a deliberate step gives it somewhere to go — which is exactly why
        it looked like the key was broken rather than the wheel still running.

        The mute is armed by any deliberate navigation (:meth:`_page_scroll`, Home/End,
        :meth:`goto_page`, the slideshow's :meth:`_deliberate_step`) and lifts once the wheel has
        actually gone quiet (:data:`_WHEEL_QUIET_MS` with no wheel event) — which is also how a
        *reader* tells the two apart: a fresh scroll comes after a pause, a coast doesn't.
        """
        # Qt fills the timestamp from the platform message; a plugin that leaves it at 0 falls back
        # to our own clock, so an unstamped wheel still un-mutes (fail-open: at worst the wheel
        # behaves as it did before this guard, never dead).
        ts = event.timestamp() or int(time.monotonic() * 1000)
        # ``0 <=`` matters as much as the upper bound: a *backwards* step cannot be the same gesture,
        # it means the clock under us changed — which is reachable, because the fallback above is a
        # different clock from the platform's and the two are not comparable. Before M91.4 only the
        # slideshow kept this timestamp, so the mixed-source case could not arise; now that every
        # wheel event updates it, an unstamped event followed by a stamped one would otherwise leave
        # the wheel muted for ever. Fail open, always: a mute that cannot lift is a dead wheel.
        elapsed = ts - self._last_wheel_ts
        if self._wheel_muted:
            if self._mute_still_applies(event, elapsed):
                self._last_wheel_ts = ts        # same gesture, still coasting — swallow it
                event.accept()
                return
            self._wheel_muted = False           # the wheel stopped; this is a new gesture
        self._last_wheel_ts = ts
        # **A wheel-driven move must not park the wheel that drove it.** The slideshow steps by
        # calling ``goto_page``, which arms the mute for every other caller — so without this the
        # wheel muted itself after one detent and a four-detent flick moved one slide (caught by
        # M78's own tests). The flag says "we are inside the wheel handler"; nothing else reads it.
        self._wheel_driving = True
        try:
            self._apply_wheel(event)
        finally:
            self._wheel_driving = False

    def _mute_still_applies(self, event, elapsed: int) -> bool:
        """Whether this wheel event is still part of the coast the mute was armed against (M92.3).

        Three ways out, and **the mute can never renew itself through any of them** — which is the
        whole of M92.3. Before it, a swallowed event refreshed ``_last_wheel_ts``, so the quiet
        window could not elapse while events kept arriving; the wheel then stayed dead for as long as
        the reader kept scrolling, and scrolling is exactly what a reader does when scrolling stops
        working (reproduced: 200 events over 4 s, every one swallowed).

        * **The gap test** (M91.4, unchanged): a pause of :data:`_WHEEL_QUIET_MS` means the wheel
          genuinely stopped, so what follows is a new gesture. This handles the ordinary case and is
          why the ceiling below is rarely reached.
        * **The ceiling** (M92.3): :data:`_WHEEL_MUTE_MAX_MS` after arming, the mute lifts whatever
          is still arriving. Timed on :meth:`_now_ms` rather than the event timestamp, so the two
          clocks are never compared with one another.
        * **A reversal** (M92.3): a coast runs one way — it is the wheel losing speed, not changing
          its mind. Scrolling the *other* way is unambiguously a fresh decision, and recovering
          instantly is worth more here than anywhere else, because "go back" is precisely what a
          reader wants after a deliberate step landed somewhere they did not expect.
        """
        if not 0 <= elapsed < _WHEEL_QUIET_MS:
            return False                        # the wheel went quiet — M91.4's original test
        if self._now_ms() - self._wheel_mute_start >= _WHEEL_MUTE_MAX_MS:
            return False                        # aged out: a mute may not outlive its own coast
        dy = event.angleDelta().y()
        sign = (dy > 0) - (dy < 0)
        if sign:
            if not self._wheel_mute_dir:
                self._wheel_mute_dir = sign     # first swallowed event names the coast's direction
            elif sign != self._wheel_mute_dir:
                return False                    # turned round — a hand, not a flywheel
        return True

    def _apply_wheel(self, event) -> None:
        """The wheel's actual effect, once :meth:`wheelEvent` has decided it is not a coast."""
        if not self.slideshow:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._wheel_zoom(event)
                return
            if self._wheel_pan(event):      # Shift+wheel → horizontal (M89.3)
                return
            if self._wheel_scroll(event):   # a mouse detent moves a defined distance (M92.1)
                return
            super().wheelEvent(event)
            return
        delta = event.angleDelta().y()
        if delta and (delta > 0) != (self._wheel_accum > 0):
            self._wheel_accum = 0                    # a reversal starts counting afresh
        self._wheel_accum += delta
        while self._wheel_accum >= _WHEEL_NOTCH:     # wheel up = back through the deck
            self._wheel_accum -= _WHEEL_NOTCH
            self.step_slide(-1)
        while self._wheel_accum <= -_WHEEL_NOTCH:
            self._wheel_accum += _WHEEL_NOTCH
            self.step_slide(1)
        event.accept()

    def _is_mouse_detent(self, event) -> bool:
        """Whether this wheel event is a **discrete mouse-wheel detent** rather than a precision
        device (a touchpad, or a free-spinning hi-res wheel).

        The distinction exists because M92.1 deliberately changes only the *mouse*. Touchpad
        scrolling was declared out of scope by the owner (2026-07-30: *"though not perfect I am
        satisfied with it for now"*), and a precision device's whole point is that it reports how far
        the fingers moved — imposing a per-detent step on it would replace a measured distance with a
        quantised guess. Anything that fails this test falls through to ``super()``, i.e. to exactly
        the behaviour shipped before M92.1.

        Two tests, because the platforms differ:

        * **``pixelDelta`` is set** — the device told us the distance in pixels. Qt fills this on
          macOS and Wayland; it is null on Windows for every device, so this arm is the portable one,
          not the one that fires here.
        * **``angleDelta`` is a whole multiple of a detent.** This is the Windows discriminator: a
          notched wheel reports exactly ±120 per click, while a precision touchpad reports the fine
          fractions of 120 that make its scrolling smooth in the first place.

        **Granularity is not a heuristic standing in for a better test — on Windows it is the only
        test there is.** Measured with ``tools/probe_wheel.py`` on the owner's hardware (2026-07-31),
        across three devices:

        =================  ======  ==========================  ====================
        device             events  ``angleDelta.y``            whole multiples of 120
        =================  ======  ==========================  ====================
        wheel, discrete       50   ±120 only                   50 / 50
        wheel, free-spin     160   ±120 only                   160 / 160
        touchpad             376   -44 … -31 (and the rest)    1 / 376
        =================  ======  ==========================  ====================

        Three findings, each of which closes a question this docstring used to leave open:

        * **``event.device()`` cannot tell them apart.** All three report ``DeviceType.Mouse``, named
          ``"core pointer"`` — Qt's Windows plugin does not distinguish a touchpad from a mouse. The
          earlier note here proposing ``device().type()`` as the honest fix pointed at a dead end.
        * **Free-spin is mechanical, not hi-res.** It disengages the ratchet so the wheel coasts
          longer and emits *more* detents (160 against 50 for comparable hand motion); the encoder
          resolution is unchanged, and every event is still a whole ±120. So the "hi-res wheel keeps
          the old step" gap this once warned about **does not exist on this hardware**, and the
          87 px lattice a detented wheel imposes is not reachable by any software change.
        * **``phase()`` is ``NoScrollPhase`` for every device, touchpad included** — so the deferred
          touchpad-inertia work (`PLAN.md` §Future enhancements) must infer gesture end from a quiet
          gap, as M91.4's coast-mute already does. That was an open question; it is now answered.

        Accuracy on that sample: **210/210** wheel events classified as mouse, **375/376** touchpad
        events as precision. The single stray was a touchpad report that happened to land on exactly
        120; it costs one frame of a gesture moving 87 px where Qt would have moved ~183 — a momentary
        slowing inside a stream of hundreds, not a jump.
        """
        if not event.pixelDelta().isNull():
            return False
        dy = event.angleDelta().y()
        return dy != 0 and dy % _WHEEL_NOTCH == 0

    def _wheel_scroll(self, event) -> bool:
        """**One mouse-wheel detent moves a defined distance** (M92.1) — ``wheelScrollLines ×
        _WHEEL_LINE_PX × zoom``. Returns whether the gesture was ours.

        This replaces the ``super().wheelEvent()`` delegation, and with it Qt's ``singleStep`` of
        ``viewportHeight / 20`` — a step derived from the *window* rather than the document, which on
        the owner's full-height window threw the page 183 px (ten lines of body text) per click. See
        :data:`_WHEEL_LINE_PX` for the measurement and the replacement constant.

        **Scaled by zoom**, so a detent always moves the same amount of *document*: zoom to 200% and
        the text is twice as tall, so the same pixel distance would carry you half as far through the
        page. This is the property Qt's rule lacked in the other direction — its step ignored zoom
        entirely and tracked window height instead, which is neither of the two things a reader means.

        **Proportional to the raw delta**, not quantised to whole detents like the slideshow's
        stepper: a wheel that reports 240 in one event (a fast spin coalesced by the driver) moves two
        detents' worth, and the sub-pixel remainder each event leaves over is carried in
        :attr:`_scroll_remainder` rather than rounded away.

        **A horizontal-dominant wheel is left to** ``super()`` — that is a tilt wheel, and
        ``QAbstractScrollArea`` already routes the larger axis to the matching scrollbar. Only the
        vertical axis is taken over, the same division of labour :meth:`_wheel_pan` draws.
        """
        if not self._is_mouse_detent(event):
            return False
        dy, dx = event.angleDelta().y(), event.angleDelta().x()
        if abs(dx) > abs(dy):
            return False                    # a tilt wheel — Qt already routes it to the h-bar
        step = QApplication.wheelScrollLines() * _WHEEL_LINE_PX * self.zoom
        # Wheel *up* (positive delta) means scroll *back*, i.e. a smaller scrollbar value.
        self._scroll_by(-dy / _WHEEL_NOTCH * step)
        event.accept()
        return True

    # ---- the glide (M92.2) -------------------------------------------------------

    @property
    def smooth_scrolling(self) -> bool:
        """Whether a wheel step is eased (M92.2) or written straight to the bar (M92.1)."""
        return self._smooth_scrolling

    @smooth_scrolling.setter
    def smooth_scrolling(self, on: bool) -> None:
        self._smooth_scrolling = bool(on)
        if not self._smooth_scrolling:
            self.stop_glide()               # never leave a glide running with the feature off

    def _now_ms(self) -> float:
        """This view's own monotonic clock, in ms. A method so tests can drive time without
        sleeping — and deliberately ``monotonic``, which no clock change can run backwards.

        Used by the glide (M92.2) *and* by the coast-mute ceiling (M92.3). Note that the mute's
        other test — the gap between events — uses ``QWheelEvent.timestamp()`` instead, which comes
        from the platform message and is a **different clock**. The two are never compared with each
        other: each test is internally consistent, which is what keeps the mixed-clock trap
        :meth:`wheelEvent` documents from reappearing in the ceiling.
        """
        return time.monotonic() * 1000.0

    def _glide_interval_ms(self) -> int:
        """One tick, sized to **this screen's refresh rate** rather than a hardcoded 16 ms.

        Not for phase — a plain ``QTimer`` cannot lock to vblank, and on a raster ``QWidget`` Qt
        does not vsync-lock painting on Windows anyway (measured: this display refreshes at
        59.95 Hz, i.e. every 16.68 ms, and no integer millisecond divides that). It is for **rate**:
        a fixed 16 ms would produce 62 updates a second on a 144 Hz panel, which is needlessly
        coarser than the panel can show.

        Truncated rather than rounded, so we always produce *at least* one update per display
        frame. Rounding 16.68 up to 17 ms yields 58.8 Hz — under-sampling, where the display shows
        the same position twice and the motion micro-stalls. Over-sampling merely computes a frame
        nobody sees, which costs ~1 ms and is the safer error.

        Sampled per glide, not cached: the owner's machine pairs a 1.75× laptop panel with a 1.0×
        external Dell, and a window dragged between screens can change refresh rate as well as DPR.
        """
        screen = self.screen() or QGuiApplication.primaryScreen()
        hz = screen.refreshRate() if screen is not None else 0.0
        if not 24.0 <= hz <= 400.0:         # a screen that cannot answer, or answers absurdly
            return 16
        return max(4, int(1000.0 / hz))

    def stop_glide(self) -> None:
        """Abandon any glide in flight, leaving the view exactly where it has got to.

        Called by :meth:`_park_coasting_wheel` — so every deliberate navigation (the paging keys,
        Home/End, :meth:`goto_page`, a slideshow step) ends the glide rather than fighting it — and
        by the scrollbar's ``sliderPressed``. This is the M91.4 lesson applied to our own motion:
        there, a *flywheel* kept emitting events that undid a keypress, and the fix had to infer the
        coast from a quiet gap. A glide we own needs no inference; it can simply be stopped.
        """
        self._glide_timer.stop()
        self._glide_target = None

    def _scroll_by(self, px: float) -> None:
        """Move the view ``px`` down (negative = up) — eased when smooth scrolling is on (M92.2),
        written straight to the bar when it is off (M92.1 behaviour, byte for byte).

        **Target-based, and a mid-glide detent extends the target** rather than restarting from
        rest: the new distance is added to where the glide was already heading, and the curve is
        re-timed from where the view actually *is*. That is what makes a held spin read as one
        continuous motion instead of a train of separate lurches — the single biggest thing M92.2
        buys, since a lone detent at 87 px is not far enough for easing to matter much.

        **A reversal collapses the outstanding distance** instead of unwinding it. Flick back and
        the target becomes ``current + px``, not ``old_target + px``, so the view turns round
        immediately rather than first travelling the rest of the way to a place the reader has
        already changed their mind about.
        """
        vbar = self.verticalScrollBar()
        if not self._smooth_scrolling:
            moved = self._scroll_remainder + px
            whole = int(moved)              # truncates toward zero; the rest funds the next event
            self._scroll_remainder = moved - whole
            if whole:
                vbar.setValue(vbar.value() + whole)
            return
        current = float(vbar.value())
        base = current
        if self._glide_target is not None and (self._glide_target - current) * px > 0:
            base = self._glide_target       # same direction — extend what is already in flight
        target = max(float(vbar.minimum()), min(float(vbar.maximum()), base + px))
        if round(target) == round(current):
            self.stop_glide()               # nothing to travel (clamped at an end) — don't animate
            return
        if self._glide_target is not None and round(target) == round(self._glide_target):
            # **The target did not move, so do not restart the curve** (M92.5). This happens only at
            # the document's ends, where the clamp pins the target while detents keep arriving — and
            # restarting there is what made the landing jerky. Each restart re-enters the ease-out's
            # *fast opening*, so velocity snaps up, decays, snaps up: a sawtooth. Mid-document it is
            # masked because the target keeps advancing; against a pinned target the same shrinking
            # distance is re-traversed and the sawtooth is all there is. Measured arriving at page 1
            # (owner-reported 2026-08-01), the frames ran 129, 66, **84**, 43, **54**, 30, 35 px —
            # visibly speeding up twice while "stopping" — and then took **240 ms to crawl the last
            # 23 px**, because 23% of a tiny remainder is a pixel at a time.
            #
            # Letting the curve finish instead means the boundary gets one clean ease-out that
            # decelerates into it, which is what the reader is asking for: the arrival is the part
            # they are watching.
            return
        self._glide_origin = current
        self._glide_target = target
        self._glide_start = self._now_ms()
        if not self._glide_timer.isActive():
            self._glide_timer.setInterval(self._glide_interval_ms())
            self._glide_timer.start()

    def _glide_tick(self) -> None:
        """One frame of the glide: an **ease-out** from origin to target over :data:`_WHEEL_EASE_MS`.

        **Position comes from the clock, not from a per-frame increment.** A frame delayed by a page
        rasterise (4-48 ms, synchronous — :meth:`_render_pixmap`) therefore costs smoothness for a
        frame or two and nothing else; the motion still lands on the right pixel at the right time.
        An increment-driven animator would stretch by exactly the time it lost, and a burst of them
        would drift the landing point somewhere the reader never asked for.

        Ease-out (``1 - (1-t)³``) rather than ease-in-out: the motion has to begin on the same frame
        as the click or the wheel feels laggy — this curve moves 20 px of an 87 px step in the first
        frame, where ease-in-out moves 0.2 px and reads as the app hesitating. The cost, measured, is
        that ease-out keeps the largest single-frame jump of the three candidates (20 px against a
        uniform 7.3 px for linear); linear, though, stops dead, which is the abruptness this
        milestone exists to remove.
        """
        if self._glide_target is None:
            self._glide_timer.stop()
            return
        vbar = self.verticalScrollBar()
        t = (self._now_ms() - self._glide_start) / _WHEEL_EASE_MS
        if t >= 1.0:
            vbar.setValue(round(self._glide_target))
            self.stop_glide()
            return
        eased = 1.0 - (1.0 - max(t, 0.0)) ** 3
        pos = round(self._glide_origin + (self._glide_target - self._glide_origin) * eased)
        vbar.setValue(pos)
        # **End on the pixels, not on the clock.** An ease-out asymptotes, so the tail of the curve
        # moves less than half a pixel per frame and the reader sees nothing at all: measured on the
        # owner's display, a detent's motion finished at 161 ms of a 200 ms glide, leaving ~2.5
        # frames of timer that changed nothing. Stopping when the rounded position reaches the
        # rounded target lands on exactly the same pixel, and it matters beyond the saved frames —
        # duty cycle is the share of time a glide is in flight, and a glide that outlives its own
        # motion inflates the one number that argued against 200 ms (see :data:`_WHEEL_EASE_MS`).
        if pos == round(self._glide_target):
            self.stop_glide()

    def _wheel_pan(self, event) -> bool:
        """``Shift+wheel`` pans **horizontally** across a page wider than the viewport (M89.3).
        Returns whether the gesture was ours.

        This is an *override*, not a gap. Qt's own ``Shift+wheel`` scrolls this view **vertically**
        (measured, with the h-bar at full range), so a page zoomed wider than the window had no
        wheel gesture that could cross it — the reader had to reach for the scrollbar. Every
        browser and Acrobat read the shifted wheel as the horizontal axis.

        A wheel with a **genuine horizontal component** — a tilt wheel, most precision touchpads —
        is left to ``super()``, which already routes it correctly; only the vertical axis Shift is
        decorating gets reinterpreted. And the gesture is consumed even when the page fits across
        the viewport, so a shifted wheel is *inert* there rather than silently scrolling down: it
        means one thing everywhere, which is how it behaves in a browser.

        One detent moves what ``wheelScrollLines`` × the bar's single step is worth — the same
        arithmetic Qt applies to the vertical axis, so the two feel alike.
        """
        if not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            return False
        if event.angleDelta().x():
            return False                    # a real horizontal wheel — Qt already handles it
        delta = event.angleDelta().y()
        if delta:
            hbar = self.horizontalScrollBar()
            lines = QApplication.wheelScrollLines()
            hbar.setValue(hbar.value()
                          - round(delta / _WHEEL_NOTCH * lines * hbar.singleStep()))
        event.accept()
        return True

    def _wheel_zoom(self, event) -> None:
        """Ctrl+wheel zoom, anchored on the pointer (M80) — the gesture every browser, Acrobat and
        Explorer share, and the one thing a reader reaches for that this view did not have.

        **One detent is exactly one Ctrl+± step**: the factor is ``_ZOOM_STEP ** (delta /
        _WHEEL_NOTCH)``, continuous rather than quantised to whole detents like the slideshow's
        stepper. That is deliberate — a precision touchpad sends small fractional deltas, and
        rounding them to detents would either swallow them or make two-finger zoom lurch; a
        proportional factor makes the same code smooth there and exact on a notched mouse wheel.

        **Anchored on the pointer**, unlike every other zoom entry point (which holds the viewport
        centre — see :meth:`_anchor_at`): that is what makes it a *pointing* gesture. The word under
        the cursor stays under the cursor, so zooming into a figure in the page's corner needs no
        follow-up scroll to find it again.

        The event is accepted unconditionally — including at the zoom limits and on a stray
        horizontal-only delta — so a Ctrl+wheel can never fall through to ``super()`` and **scroll**,
        which is precisely what it did before this existed (Ctrl held, page jumps: the worst
        outcome, since the user asked for zoom and got motion).

        Not reached in the slideshow: that mode's contract is one page per screen at Fit Page
        (M78), so the wheel there keeps stepping slides whatever the modifier.

        **Deltas are coalesced to one zoom per frame** (M86.2). The gesture can deliver 10–60
        events a second — a precision touchpad sends a fine delta every few milliseconds — and each
        one rebuilt the scene and rasterised the visible band. Accumulating them and applying once
        per frame makes a burst cost one rebuild where it cost N, and it is **exact, not an
        approximation**: the factor is ``_ZOOM_STEP ** (delta / _WHEEL_NOTCH)``, so multiplying the
        per-event factors and exponentiating the summed delta are the same number
        (``Π s^(dᵢ/n) == s^(Σdᵢ/n)``) — one power is if anything the more accurate of the two.
        """
        delta = event.angleDelta().y()
        if delta:
            self._zoom_pending += delta
            self._zoom_anchor = event.position().toPoint()  # the pointer as of the latest event
            if not self._zoom_timer.isActive():
                # Throttle, not debounce: the timer is started by the first event of a frame and
                # left to run. Restarting it on every event would push the flush back for as long
                # as the gesture continued, so a sustained touchpad zoom would show nothing at all
                # until the fingers stopped — the opposite of smooth.
                self._zoom_timer.start(_ZOOM_COALESCE_MS)
        event.accept()

    def _flush_wheel_zoom(self) -> None:
        """Apply one frame's worth of accumulated Ctrl+wheel delta (M86.2).

        Deltas that reverse inside a single frame cancel, which is the right answer for a hand that
        genuinely moved both ways — the one case it differs from applying each event separately is
        a reversal that also hits a zoom limit mid-frame, and no hand reverses a wheel in 16 ms.
        """
        delta, anchor = self._zoom_pending, self._zoom_anchor
        self._zoom_pending, self._zoom_anchor = 0.0, None
        if delta:
            self.set_zoom(self._zoom * (_ZOOM_STEP ** (delta / _WHEEL_NOTCH)), anchor_pos=anchor,
                          step=True)

    def viewportEvent(self, event) -> bool:
        """Offer a hovered web link's URL as a tooltip — after the scene's items have had theirs.

        This has to live here rather than in :meth:`_update_hover_cursor`, and the reason is a trap
        worth naming: **a ``QGraphicsView``'s viewport tooltip is never shown.**
        ``QGraphicsView.viewportEvent`` intercepts ``QEvent.ToolTip``, turns it into a
        ``GraphicsSceneHelp`` event sent to the *scene*, and returns — so ``QWidget``'s handler,
        the only thing that reads the viewport's ``toolTip`` property, never runs. Measured: with
        ``viewport().setToolTip(url)`` set, a real ``QHelpEvent`` left ``QToolTip.isVisible()``
        False. That is how M149 first shipped the URL-on-hover the owner had asked for, and how a
        test asserting ``viewport().toolTip() == url`` passed while a reader saw nothing: the
        property was set, and nothing ever read it.

        The scene goes **first**, deliberately. A note badge carries its note as a
        *``QGraphicsItem``* tooltip (``annotations.py`` — the one hover text in this view that has
        always worked, because items are exactly what the scene offers the event to), and mouse
        presses prefer an annotation over a link, so hover text should agree. ``isAccepted()`` after
        the base call is Qt's own answer to "did an item take it": ``QGraphicsScene.helpEvent``
        accepts only when it showed an item's tooltip.
        """
        handled = super().viewportEvent(event)
        if event.type() == QEvent.Type.ToolTip and not event.isAccepted():
            self._show_link_tooltip(event)
            return True
        return handled

    def _show_link_tooltip(self, event) -> None:
        """Show the URL under the pointer, or nothing. Gated on the same state as the pointing-hand
        cursor: a link that this mode would not follow must not advertise itself either."""
        uri = None
        if self._armed is None and self._mode == InteractionMode.SELECT and self.links is not None:
            uri = self.links.openable_uri_at(self.mapToScene(event.pos()))
        if uri is None:
            QToolTip.hideText()
        else:
            QToolTip.showText(event.globalPos(), uri, self.viewport())

    def _update_hover_cursor(self, scene_pt) -> None:
        """Show a pointing-hand over an internal link (SELECT — it's clickable) and a move cursor
        over a draggable mark — but never while a box is being edited (you're typing, not arranging),
        so the move cursor isn't left showing on the viewport, which the inline editor / formatting
        bar would inherit. In OBJECT mode (M59.6) the move cursor covers any drawn mark or text box,
        since dragging one moves it / the group; links are inert there.

        The URL a web link points at is shown on hover too (M149), but **not from here** — see
        :meth:`viewportEvent`, because on a ``QGraphicsView`` the viewport's own ``toolTip`` property
        is never read."""
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
        if self._mode == InteractionMode.SELECT and self.links is not None:
            # A web link gets the same pointing hand as an internal one (M149).
            uri = self.links.openable_uri_at(scene_pt)
            if uri is not None or self.links.link_at(scene_pt) is not None:
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
        from klarpdf.model.foreign_annots import ForeignDeletion, ForeignMove, apply_foreign_edits

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
        from klarpdf.model.page_edits import apply_form_values, strip_klarpdf_annotations

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

    def _pixmap_key(self, index: int) -> tuple:
        """What identifies a rendered page in the store: the page and everything that changes its
        pixels. ``_page_extra`` is the per-page rotation override, ``_rotation`` the view spin.

        Keyed on :attr:`device_scale`, not :attr:`zoom` (M88.2): two screens of different DPR render
        the *same* zoom at different resolutions, and the store is process-global (M87.2), so a zoom
        would hand the 1.0× Dell's pixmap to the 1.75× laptop — the exact blur this milestone is
        fixing, cached.
        """
        return (index, round(self.device_scale, 4),
                (self._page_extra(index) + self._rotation) % 360)

    def _render_target(self, index: int) -> "tuple | None":
        """What drawing page ``index`` means: ``(page, clip, total)``, or ``None`` when its crop
        lies wholly outside what can be drawn.

        ``page`` is the page object to draw: an edited copy when the page needs one (M14, M31,
        M66), else the shared source. ``clip`` is the displayed part in the page's rotated space,
        or ``None`` for the whole page. ``total`` is the extra spin applied after drawing: the
        rotation override and the view rotation. Whole pages and pieces are drawn from the same
        three (M152.2).
        """
        ref = self._vdoc.ordered[index]
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
        return page, clip, (self._page_extra(index) + self._rotation) % 360

    def _to_pixmap(self, img: QImage, total: int) -> QPixmap:
        """A drawn picture, ready to show: night mode, the device pixel ratio and the extra spin."""
        if self._night:
            img.invertPixels()  # M49: view-only — save/print/export render elsewhere
        # Tell Qt these are device pixels *before* the QPixmap conversion, which inherits the
        # ratio. Without it Qt reads the extra pixels as extra size and the page lays out 1.75×
        # too big; with it the item occupies deviceIndependentSize() — exactly `scale` units,
        # so the layout is byte-for-byte what it would be at DPR 1.0, only sharper.
        img.setDevicePixelRatio(self._dpr)
        pixmap = QPixmap.fromImage(img)
        if total:
            pixmap = pixmap.transformed(QTransform().rotate(total))  # preserves the ratio
        return pixmap

    def _render_pixmap(self, index: int) -> QPixmap | None:
        key = self._pixmap_key(index)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        try:
            target = self._render_target(index)
            if target is None:
                return None
            page, clip, total = target
            # A page drawn in pieces keeps its display list, and a whole drawing from it skips
            # reading the page again (M152.2).
            source = self._dlists.get(index, page)
            # Rasterise at **device** resolution (M88.2) — zoom × logicalDpi/72 × devicePixelRatio.
            ds = self.device_scale
            # Timed from here, not from the top: building the render copy above happens once per
            # source, and a redraw at another size costs only what follows (M152.1).
            started = time.perf_counter()
            pm = source.get_pixmap(matrix=fitz.Matrix(ds, ds), clip=clip, alpha=False)
            img = QImage(pm.samples, pm.width, pm.height, pm.stride, QImage.Format.Format_RGB888)
            pixmap = self._to_pixmap(img.copy(), total)  # the copy detaches from pm.samples
            self._draw_rate[index] = (time.perf_counter() - started) / max(1, pm.width * pm.height)
        except Exception:
            return None
        self._cache.put(key, pixmap)
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
        return QPointF(d[0] * self.scale, d[1] * self.scale)

    def _visible_range(self) -> tuple[int, int]:
        """The pages intersecting the viewport — see :meth:`_pages_in`."""
        return self._pages_in(self.mapToScene(self.viewport().rect()).boundingRect())

    def _pages_in(self, view_rect: QRectF) -> tuple[int, int]:
        """The pages intersecting ``view_rect``, found by **binary search** (M87.3).

        This used to scan every page in the document to find the handful on screen, which made a
        render pass cost O(document length) however few pages were visible — measured at ~6 ms per
        pass on 320 pages, so ~246 ms of a 40-step zoom sweep went on pages nowhere near the
        viewport (the follow-up M86.1's benchmark surfaced).

        ``_page_tops`` is non-decreasing by construction: rows are laid out top to bottom, and the
        two pages of a facing row share a y. So ``bisect_right`` lands on the first row that can
        reach ``top``, and the forward scan below stops at the first page past ``bottom`` — both
        bounded by the number of pages actually on screen.

        **A page counts only when at least one whole pixel of it is in view** (M152.1, #360). The
        test used to be ``y + h >= top``, so a page that ended exactly at the top of the view
        counted, and so did a sliver thinner than a pixel. Both happen on every move to the next
        page: at Fit Page the page above ends exactly at the top, and at Fit Width the scroll bar
        rounds the page's top down and leaves up to a pixel of the page above. On the NADA report
        that drew the whole cover, 0.4–1.0 s, to show 0 px or 0.18 px of it. The scroll bar moves
        in whole pixels, so a part thinner than one is what rounding left, not what the reader
        scrolled to. The white page behind the picture shows it the same until the page is drawn.
        """
        top, bottom = view_rect.top(), view_rect.bottom()
        # The last page starting at or above `top` is the only one that can reach down into the
        # viewport from above — a preceding row ends a full gap before this row starts. Its facing
        # partner shares its y and needs the step back.
        start = max(0, bisect_right(self._page_tops, top) - 1)
        while start > 0 and self._page_tops[start - 1] == self._page_tops[start]:
            start -= 1
        first, last = None, None
        for i in range(start, len(self._pages)):
            page = self._pages[i]
            if page["y"] > bottom - 1:
                break
            if page["y"] + page["h"] >= top + 1:
                first = i if first is None else first
                last = i
        if first is None:  # nothing intersects (e.g. between renders) — fall back to current
            return self._current, self._current
        return first, last

    def _page_bytes(self, index: int) -> int:
        """What page ``index`` will cost as a pixmap at the current zoom.

        The layout already holds the page's on-screen size (``sizes[i] * scale``), so this needs no
        rendering — which is the point: the band has to be decided *before* anything is rasterised.

        **Scaled by ``dpr²``** (M88.2): the layout is in logical units but the pixmap is rasterised
        at device resolution, so on the owner's 1.75× panel a page costs 3.06× what its rect
        suggests. Without this the M87.1 prefetch allowance and the ceiling it feeds would both
        under-count by that factor on exactly the machine that can least afford it.
        """
        page = self._pages[index]
        dpr2 = self._dpr * self._dpr
        return int(int(page["w"]) * int(page["h"]) * dpr2) * (QPixmap.defaultDepth() // 8)

    def _prefetch(self, first: int, last: int) -> int:
        """How many pages either side of the viewport are worth rendering ahead (M87.1).

        ``_PREFETCH`` was a **fixed 2** — sound when a page was 1.85 MB, actively harmful once it is
        264 MB. Measured 2026-07-28 (PR #207): at zoom >= 2 the visible band is 2 pages and the
        render band 6, so **67% of everything rendered is prefetch** — 237 MB visible against 473 MB
        prefetched at 8x, and 57% waste even at 1.0x. Those are pages the reader cannot reach
        without several more scrolls, rendered at the exact zoom where a page is most expensive.

        Scaled by the **heaviest page in view**, not the average: in a mixed-size document one A0
        sheet is what would blow the band, and it is the one the reader is most likely looking at.
        """
        heaviest = max((self._page_bytes(i) for i in range(first, last + 1)), default=0)
        if heaviest <= 0:
            return _PREFETCH
        return int(min(_PREFETCH, _PREFETCH_BYTES // heaviest))

    def content_band(self) -> tuple[int, int] | None:
        """The page range worth rasterising for, or ``None`` before the first show (paint it all).

        The same band :meth:`_render_visible` uses for page pixmaps, exposed so the annotation
        overlay's rasterised content marks can be just as lazy as the pages they sit on — including
        the same adaptive prefetch, or a heavy page's content marks would out-live its pixmap.
        """
        if not self._pages or not self._shown_once:
            return None
        first, last = self._visible_range()
        prefetch = self._prefetch(first, last)
        return max(0, first - prefetch), min(len(self._pages) - 1, last + prefetch)

    @contextmanager
    def _hold_render(self):
        """Defer rasterising until the whole geometry change has landed, then do it **once**.

        A single ``set_zoom`` used to rasterise the visible band three times (M86.1, profiler:
        ``ncalls=3``): :meth:`_build_scene` renders at the end of its rebuild, the anchor restore
        renders again once it has scrolled, and the scrollbar write fires :meth:`_on_scroll`, which
        renders a third time. Only the last pass could be right — the first two rasterise a band the
        scroll is about to move off screen — so two-thirds of the most expensive work in the app was
        computed and thrown away.

        **Pre-existing, not M80's**: the old ``centerOn`` path did the same. Ctrl+wheel merely made
        it matter, by driving zoom 10–60× a second where a toolbar click drove it once. Holding it
        here rather than at each call site is what makes every geometry change — zoom, fit, rotate,
        two-page toggle, reload, reopen — cost one pass instead of three or four.

        Nested blocks collapse into the outermost (``set_page_layout`` rebuilds and then re-fits,
        which zooms). On an exception nothing is rendered and the flag still unwinds: the geometry
        is half-built, so painting it would only put a corrupt frame on screen ahead of the
        traceback, and the next scroll renders anyway.
        """
        self._render_held += 1
        try:
            yield
        finally:
            self._render_held -= 1
        if not self._render_held:
            self._render_visible()

    def overlay_band(self) -> tuple[int, int]:
        """The pages an **overlay** should paint: those intersecting the viewport, plus the
        ``_PREFETCH`` margin so a scroll of a page or two finds the marks already there.

        Public, and deliberately not the byte-budgeted band :meth:`_render_visible` computes for
        pixmaps: an overlay item is a handful of rects, not megabytes, so the adaptive shrink M87.1
        applies to rasterising has nothing to weigh here — and this is called on every scroll and
        every drag update, where the flat constant costs a binary search and the budgeted one costs
        a page-weight scan.

        Used by :class:`~viewer.text_selection.TextSelection` to bound the scene item count by
        viewport size instead of document length (M89.6).
        """
        first, last = self._visible_range()
        return max(0, first - _PREFETCH), min(len(self._pages) - 1, last + _PREFETCH)

    def _paint_page(self, index: int) -> None:
        """Rasterise page ``index`` (or take the cache hit) and hang it on its scene item."""
        pixmap = self._render_pixmap(index)
        if pixmap is not None:
            self._hang(index, pixmap)

    def _hang(self, index: int, pixmap: QPixmap, stretch: "float | None" = None) -> None:
        """Put ``pixmap`` on page ``index``'s scene item. ``stretch`` is given for a picture drawn
        for another size, and is how much to stretch it (M152.1). A picture drawn for this size
        finishes the page, so its pieces and any carried picture go (M152.2)."""
        item = self._pages[index]["pix"]
        item.setPixmap(pixmap)
        item.setPos(self._pixmap_offset(index))  # inset only in the un-crop edge case
        item.setScale(1.0 if stretch is None else stretch)
        # Smooth only while stretched: a picture drawn for its own size maps pixel for pixel, and
        # filtering it would only cost time.
        item.setTransformationMode(Qt.TransformationMode.FastTransformation if stretch is None
                                   else Qt.TransformationMode.SmoothTransformation)
        self._painted.add(index)
        if stretch is None:
            self._stand_ins.discard(index)
            self._drop_pieces(index)
            self._drop_carried(index)
        else:
            self._stand_ins.add(index)

    def _best_stored(self, index: int, key: tuple) -> "tuple | None":
        """The key of the store's best picture of page ``index`` drawn for another size, or
        ``None``. Only a picture with the same rotation will do: everything else that changes a
        page's pixels empties the store (an edit, night mode). The smallest picture at least as
        sharp as this size is taken, and failing that the sharpest. Stretching down loses less
        than stretching up, and a picture near the right size loses the least."""
        others = [k for k in self._cache.keys() if k[0] == index and k[2] == key[2] and k != key]
        if not others:
            return None
        sharp_enough = [k for k in others if k[1] >= key[1]]
        return (min(sharp_enough, key=lambda k: k[1]) if sharp_enough
                else max(others, key=lambda k: k[1]))

    def _show_stand_in(self, index: int) -> "tuple | None":
        """Show page ``index`` without drawing it (M152.1, M152.2). Returns the store key of the
        picture shown, to pin, or ``None``.

        The page's picture for this size, if the store has one. Otherwise up to two pictures stand
        in, one above the other. Below, the store's best picture of the whole page drawn for
        another size, stretched. Above it, the picture carried from before a zoom, an edit or a
        minimize, when that one is sharper: it may cover only the part of the page that was on
        screen. With neither, the white page shows until pieces arrive.
        """
        key = self._pixmap_key(index)
        exact = self._cache.get(key)
        if exact is not None:
            self._hang(index, exact)
            return key
        chosen = self._best_stored(index, key)
        if chosen is not None:
            pixmap = self._cache.get(chosen)
            # The picture lays out at chosen[1] / its own pixel ratio scene units per point, and
            # the page is now self.scale. Not the ratio of the two keys: a picture from the other
            # screen carries that screen's ratio.
            self._hang(index, pixmap, self.scale * pixmap.devicePixelRatio() / chosen[1])
        carried = self._carried.get(index)
        if carried is not None and carried.total == key[2]:
            sharpness = carried.pixmap.width() / max(1e-9, carried.rect.width())  # px per point
            if chosen is None or sharpness > chosen[1]:
                self._show_carried(index, carried)
        return chosen

    def _show_carried(self, index: int, carried: _Carried) -> None:
        page = self._pages[index]
        item = page.get("carry")
        if item is None:
            item = page["carry"] = QGraphicsPixmapItem(page["bg"])
            item.setZValue(1)          # above the store's stand-in, below the pieces
            item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        item.setPixmap(carried.pixmap)
        item.setPos(carried.rect.x() * self.scale, carried.rect.y() * self.scale)
        # Its size without its pixel ratio: a page's own picture has the screen's, where a picture
        # painted from the pieces has none. Counted as its pixels, it showed at half size on a
        # screen with two pixels to a point.
        width = carried.pixmap.deviceIndependentSize().width()
        item.setScale(carried.rect.width() * self.scale / max(1e-9, width))
        self._painted.add(index)
        self._stand_ins.add(index)

    def _drop_carried(self, index: int) -> None:
        self._carried.pop(index, None)
        if 0 <= index < len(self._pages):
            item = self._pages[index].get("carry")
            if item is not None:
                item.setPixmap(QPixmap())

    def _needs_drawing(self, index: int) -> bool:
        """Whether page ``index`` lacks a picture drawn for this size — blank, or stretched."""
        return index not in self._painted or index in self._stand_ins

    def _page_px(self, index: int) -> int:
        """How many device pixels page ``index`` covers at this size."""
        page = self._pages[index]
        return int(int(page["w"]) * int(page["h"]) * self._dpr * self._dpr)

    def _visible_cost(self) -> float:
        """How long the pages on screen would take to draw at this size, from how long each took
        per pixel the last time it was drawn (M152.1). A page not drawn yet counts as quick: the
        first resize step draws and times it."""
        first, last = self._visible_range()
        return sum(self._draw_rate.get(i, 0.0) * self._page_px(i) for i in range(first, last + 1))

    def _is_slow(self, index: int) -> bool:
        """Whether page ``index`` would take longer than the budget to draw whole (M152.2).

        From how long it took per pixel last time. A page not drawn yet is taken as quick when it
        is no bigger than the window, which is every page at Fit Page, and as slow when it is
        bigger. So opening a document draws as before, and a page first met while zoomed in is
        drawn in pieces until its first pieces are timed: drawn whole, the NADA cover at 375%
        took 10 s.
        """
        rate = self._draw_rate.get(index)
        if rate is not None:
            cost = rate * self._page_px(index)
        else:
            window = self.viewport().width() * self.viewport().height() * self._dpr * self._dpr
            cost = _DRAW_BUDGET_S if self._page_px(index) <= window else float("inf")
        return cost > _DRAW_BUDGET_S

    # ---- drawing a slow page in pieces (M152.2) ------------------------------------

    def _display_list(self, index: int, page) -> "fitz.DisplayList | None":
        dlist = self._dlists.get(index)
        if dlist is None:
            try:
                dlist = self._dlists[index] = page.get_displaylist()
            except Exception:
                return None
        return dlist

    def _start_pieces(self, index: int) -> "_Wip | None":
        """Begin drawing page ``index`` in pieces at this size, or carry on if already begun."""
        key = self._pixmap_key(index)
        wip = self._wip.get(index)
        if wip is not None and wip.key == key:
            return wip
        if wip is not None:                 # pieces of another size: the screen moved (M88.3)
            self._carry(index)
            self._drop_pieces(index)
        try:
            target = self._render_target(index)
        except Exception:                   # as _render_pixmap: a page that cannot be drawn stays blank
            return None
        if target is None:
            return None
        page, clip, total = target
        if self._display_list(index, page) is None:
            return None
        rect = (clip if clip is not None else page.rect) * fitz.Matrix(self.device_scale, self.device_scale)
        whole = rect.irect                  # exactly the picture get_pixmap would make
        wide, high = whole.width, whole.height
        shown = (high, wide) if total in (90, 270) else (wide, high)
        # A page never drawn is drawn a window's worth at first, as much as a page no bigger than
        # the window is drawn in one go. From that piece its cost is known, and a quick page is
        # then drawn whole. A smaller first piece would be mostly the fixed part of a piece's cost,
        # and would make a quick page look slow.
        window = self.viewport().width() * self.viewport().height() * self._dpr * self._dpr
        sizer = Sizer(_DRAW_BUDGET_S, self._draw_rate.get(index), first=max(1, window // (TILE * TILE)))
        wip = self._wip[index] = _Wip(key, Tiles(*shown), sizer, page, clip, total,
                                      (whole.x0, whole.y0), (wide, high))
        return wip

    def _drop_pieces(self, index: int) -> None:
        wip = self._wip.pop(index, None)
        if wip is None:
            return
        for _piece, item in wip.pieces:
            if item.scene() is not None:
                item.scene().removeItem(item)

    def _local_rect(self, index: int, scene_rect: QRectF) -> "tuple | None":
        """``scene_rect`` in device pixels of page ``index``'s picture, from its top-left."""
        page = self._pages[index]
        shown = scene_rect.intersected(QRectF(page["x"], page["y"], page["w"], page["h"]))
        if shown.isEmpty():
            return None
        off = self._pixmap_offset(index)
        x, y, d = page["x"] + off.x(), page["y"] + off.y(), self._dpr
        return ((shown.left() - x) * d, (shown.top() - y) * d,
                (shown.right() - x) * d, (shown.bottom() - y) * d)

    def _local_point(self, index: int, point: QPointF) -> tuple[float, float]:
        page = self._pages[index]
        off = self._pixmap_offset(index)
        return ((point.x() - page["x"] - off.x()) * self._dpr,
                (point.y() - page["y"] - off.y()) * self._dpr)

    def _ahead_rect(self, view: QRectF) -> QRectF:
        """What is drawn ahead of a slow page: one window height above and below the window."""
        return view.adjusted(0, -view.height(), 0, view.height())

    def _next_piece(self, visible_only: bool, grow: bool = True) -> "tuple | None":
        """The next piece to draw: ``(page, piece, ahead)``, or ``None`` when there is none.

        Pieces on screen come first, nearest the middle of the window (the owner's decision 2).
        Then pieces within a window height above and below, nearest first, with ``ahead`` set.
        """
        view = self.mapToScene(self.viewport().rect()).boundingRect()
        centre = view.center()
        for region, ahead in ((view, False), (self._ahead_rect(view), True)):
            if ahead and visible_only:
                break
            best = None
            for index, wip in self._wip.items():
                local = self._local_rect(index, region)
                if local is None:
                    continue
                wanted = wip.tiles.tiles_in(local)
                point = self._local_point(index, centre)
                hit = wip.tiles.nearest(wanted, point)
                if hit is not None and (best is None or hit[1] < best[0]):
                    best = (hit[1], index, hit[0], wanted, point)
            if best is not None:
                _d, index, seed, wanted, point = best
                wip = self._wip[index]
                piece = wip.tiles.grow(seed, wanted, point, wip.sizer.tiles()) if grow else None
                return index, piece, ahead
        return None

    def _draw_piece(self, index: int, piece) -> None:
        """Draw one piece of page ``index`` from its display list and put it on screen."""
        wip = self._wip[index]
        x0, y0, x1, y1 = wip.tiles.box(piece)
        wide, high = wip.rendered
        # The piece in the page as drawn, before the extra spin, then in PyMuPDF's pixels.
        corners = [self._point_to_source(wide, high, wip.total, x, y) for x, y in ((x0, y0), (x1, y1))]
        ox, oy = wip.origin
        ax0, ax1 = (ox + round(v) for v in sorted(c[0] for c in corners))
        ay0, ay1 = (oy + round(v) for v in sorted(c[1] for c in corners))
        # Drawn with a margin that is cut off again: see _PIECE_MARGIN.
        mx0, my0 = max(ox, ax0 - _PIECE_MARGIN), max(oy, ay0 - _PIECE_MARGIN)
        mx1, my1 = min(ox + wide, ax1 + _PIECE_MARGIN), min(oy + high, ay1 + _PIECE_MARGIN)
        ds = self.device_scale
        clip = fitz.Rect((mx0 + _CLIP_INSET) / ds, (my0 + _CLIP_INSET) / ds,
                         (mx1 - _CLIP_INSET) / ds, (my1 - _CLIP_INSET) / ds)
        started = time.perf_counter()
        try:
            pm = self._dlists[index].get_pixmap(matrix=fitz.Matrix(ds, ds), clip=clip, alpha=False)
            img = QImage(pm.samples, pm.width, pm.height, pm.stride, QImage.Format.Format_RGB888)
            pixmap = self._to_pixmap(img.copy(ax0 - pm.x, ay0 - pm.y, ax1 - ax0, ay1 - ay0), wip.total)
        except Exception:
            self._drop_pieces(index)
            return
        seconds = time.perf_counter() - started
        pixels = (x1 - x0) * (y1 - y0)
        wip.sizer.record(pixels, seconds)
        wip.seconds += seconds
        wip.pixels += pixels
        self._draw_rate[index] = wip.seconds / wip.pixels
        item = QGraphicsPixmapItem(pixmap, self._pages[index]["bg"])
        off = self._pixmap_offset(index)
        item.setPos(off.x() + x0 / self._dpr, off.y() + y0 / self._dpr)
        item.setZValue(2)                  # above both stand-ins
        wip.pieces.append((piece, item))
        wip.tiles.mark(piece)
        if wip.tiles.complete():
            self._finish_pieces(index)

    def _finish_pieces(self, index: int) -> None:
        """Every tile is drawn: join the pieces into one picture of the page, for the store."""
        wip = self._wip[index]
        whole = QPixmap(wip.tiles.width, wip.tiles.height)
        whole.setDevicePixelRatio(self._dpr)
        painter = QPainter(whole)
        for piece, item in wip.pieces:
            x0, y0, _x1, _y1 = wip.tiles.box(piece)
            painter.drawPixmap(QPointF(x0 / self._dpr, y0 / self._dpr), item.pixmap())
        painter.end()
        self._cache.put(wip.key, whole)
        self._hang(index, whole)           # drops the pieces and any carried picture

    def _trim_pieces(self, lo: int, hi: int) -> None:
        """Drop the pieces no longer needed: those of pages outside the band, and those further
        than a window height from the window (M152.2)."""
        region = self._ahead_rect(self.mapToScene(self.viewport().rect()).boundingRect())
        for index in list(self._wip):
            if not lo <= index <= hi:
                self._drop_pieces(index)
                self._dlists.pop(index, None)
                continue
            wip = self._wip[index]
            local = self._local_rect(index, region)
            keep = []
            for piece, item in wip.pieces:
                x0, y0, x1, y1 = wip.tiles.box(piece)
                if local is not None and x0 < local[2] and local[0] < x1 and y0 < local[3] and local[1] < y1:
                    keep.append((piece, item))
                else:
                    wip.tiles.mark(piece, drawn=False)
                    if item.scene() is not None:
                        item.scene().removeItem(item)
            wip.pieces = keep
        for index in [i for i in self._dlists if not lo <= i <= hi]:
            del self._dlists[index]

    def _turn(self, visible_only: bool = False) -> None:
        """Draw pieces until the budget is spent, then let the window handle what is waiting.

        A piece in view is drawn at once, and the next turn follows as soon as the window has
        handled its events. A piece ahead waits for a wheel glide to end, like the pages drawn
        ahead (M92.4), and nothing is drawn while a window edge moves (M152.1).
        """
        if self._settle_timer.isActive() or not self._wip or not self._shown_once:
            return
        started = time.perf_counter()
        while True:                         # at least one piece a turn, then until the budget
            job = self._next_piece(visible_only)
            if job is None:
                break
            index, piece, ahead = job
            if ahead and self._glide_timer.isActive():
                break
            self._draw_piece(index, piece)
            # A page taken as slow before it was timed may turn out quick: then draw it whole.
            if index in self._wip and not self._is_slow(index):
                self._paint_page(index)
            if time.perf_counter() - started >= _DRAW_BUDGET_S:
                break
        if not visible_only:
            self._schedule_turn()

    def _schedule_turn(self) -> None:
        if self._settle_timer.isActive():
            return                          # the end of the wait draws, and schedules again
        job = self._next_piece(visible_only=False, grow=False)
        if job is None:
            self._piece_timer.stop()
        else:
            self._piece_timer.start(_PREFETCH_TICK_MS if job[2] else 0)

    def _pieces_on_screen_pending(self) -> bool:
        return self._piece_timer.isActive() and self._piece_timer.interval() == 0

    def _draw_low_res(self, index: int) -> "tuple | None":
        """A quick, low-resolution picture of the whole page, to stand in when nothing else can.

        Drawn at the smallest zoom a reader can ask for, 25%. Only for a page timed before: its
        images are then already unpacked, and the picture takes a small share of the page's time.
        Not when that is hardly smaller than the page itself. Returns the store key, to pin.
        """
        if index not in self._draw_rate:
            return None
        low = round(_MIN_ZOOM * self._logical_dpi / 72.0 * self._dpr, 4)
        if low * 2 > self.device_scale:
            return None
        key = (index, low, self._pixmap_key(index)[2])
        pixmap = self._cache.get(key)
        if pixmap is None:
            try:
                target = self._render_target(index)
                if target is None:
                    return None
                page, clip, total = target
                source = self._dlists.get(index, page)
                pm = source.get_pixmap(matrix=fitz.Matrix(low, low), clip=clip, alpha=False)
                img = QImage(pm.samples, pm.width, pm.height, pm.stride, QImage.Format.Format_RGB888)
                pixmap = self._to_pixmap(img.copy(), total)
            except Exception:
                return None
            self._cache.put(key, pixmap)
        self._hang(index, pixmap, self.scale * pixmap.devicePixelRatio() / low)
        return key

    def _carry(self, index: int, factor: float = 1.0) -> None:
        """Keep a picture of the part of page ``index`` on screen, to stand in while it is drawn
        again (M152.2). ``factor`` shrinks it: a minimized window keeps a quarter of the width and
        height (the owner's decision 5).

        A page showing its own picture for this size, whole, is kept as it is. A page showing only
        stand-ins needs nothing: the store and the carried picture still hold them, and painting
        them again at every step of a drag would cost time for nothing. Otherwise what the page
        shows is painted into one picture: the stand-ins and the pieces.
        """
        page = self._pages[index]
        base = page["pix"]
        total = page["total"]   # from the layout, which may be older than the document (reload)
        # The layout's scale, not the one it is about to change to: a zoom has already set that
        # when it rebuilds the scene, and measured with it, the picture came back at its old size.
        s = self._layout_scale
        wip = self._wip.get(index)
        carry = page.get("carry")
        if factor == 1.0 and (wip is None or not wip.pieces):
            if index not in self._stand_ins and not base.pixmap().isNull():
                rect = base.mapRectToParent(base.boundingRect())
                self._carried[index] = _Carried(base.pixmap(), QRectF(
                    rect.x() / s, rect.y() / s, rect.width() / s, rect.height() / s), total)
            return
        view = self.mapToScene(self.viewport().rect()).boundingRect()
        shown = view.intersected(QRectF(page["x"], page["y"], page["w"], page["h"]))
        if shown.isEmpty():
            return
        shown.translate(-page["x"], -page["y"])
        layers = [base, carry] + ([item for _piece, item in wip.pieces] if wip is not None else [])
        layers = [item for item in layers if item is not None and not item.pixmap().isNull()]
        if not layers:
            return
        d = self._dpr * factor
        picture = QPixmap(max(1, math.ceil(shown.width() * d)), max(1, math.ceil(shown.height() * d)))
        picture.fill(QColor(0, 0, 0) if self._night else QColor(0xFF, 0xFF, 0xFF))
        painter = QPainter(picture)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.scale(picture.width() / shown.width(), picture.height() / shown.height())
        painter.translate(-shown.x(), -shown.y())
        for item in layers:
            pm = item.pixmap()
            painter.drawPixmap(item.mapRectToParent(item.boundingRect()), pm,
                               QRectF(0, 0, pm.width(), pm.height()))
        painter.end()
        self._carried[index] = _Carried(picture, QRectF(shown.x() / s, shown.y() / s,
                                                        shown.width() / s, shown.height() / s), total)

    def _carry_visible(self, factor: float = 1.0) -> None:
        if not self._pages or not self._shown_once:
            return
        first, last = self._visible_range()
        for index in range(first, last + 1):
            if index in self._painted or index in self._wip:
                self._carry(index, factor)

    def _begin_slow(self, index: int, on_screen: bool = True) -> "tuple | None":
        """Show a slow page's stand-in and begin drawing it in pieces (M152.2). Returns the store
        key of the stand-in, to pin.

        A page on screen with no stand-in at all gets a quick low-resolution picture first. Pieces
        begun at another size, after a move to another screen, are kept as a carried picture.
        """
        key = self._pixmap_key(index)
        wip = self._wip.get(index)
        if wip is not None:
            if wip.key == key:
                return wip.stand_in         # already begun at this size, stand-in on screen
            self._carry(index)
            self._drop_pieces(index)
        shown = self._show_stand_in(index)
        carried = self._carried.get(index)
        if shown is None and on_screen and (carried is None or carried.total != key[2]):
            shown = self._draw_low_res(index)
        wip = self._start_pieces(index)
        if wip is not None:
            wip.stand_in = shown
        return shown

    def _queue_prefetch(self, first: int, last: int, lo: int, hi: int) -> None:
        """Hand the prefetch margin to :meth:`_drain_prefetch` instead of rasterising it now (M92.4).

        **Prefetch was the whole of the stall.** The owner reported (2026-08-01) that with smooth
        scrolling on, *"pages with images slow down and then pick speed again when pages with text
        come in"*. Measured on a 40-page document alternating text and full-page images, scrolling
        one glide-frame at a time across six pages:

        =====  ===================  ===========  =====================  ====================
        zoom   frames over 60 Hz    worst frame  **visible** page work  **prefetch** work
        =====  ===================  ===========  =====================  ====================
        0.91   0                    14.7 ms      0 ms                   48 ms
        1.50   3                    26.8 ms      0 ms                   101 ms
        2.00   6                    45.9 ms      0 ms                   166 ms
        3.00   6                    91.1 ms      0 ms                   356 ms
        =====  ===================  ===========  =====================  ====================

        **The reader never waits for a page they are looking at** — visible-page rendering is 0 ms at
        every zoom, because prefetch had already cached it. 100% of the stalling is speculative work
        for pages one or two ahead that are not on screen yet, and it was being paid synchronously
        inside the scroll handler: on the one thread that also has to animate, at the one moment that
        cannot afford it. A worst frame of 46 ms at 2x is three dropped frames; 91 ms at 3x is five.
        That is the "slow down, then pick up speed" exactly — it happens as each image page enters the
        *prefetch* band, one or two pages **before** the reader sees it.

        So the fix is not to render less, or to render it differently — it is to render it **later**,
        in the gaps between gestures. The queue is ordered **direction of travel first, nearest
        first**, so the page being scrolled towards is ready before the one being left behind. The
        band itself is unchanged (still symmetric), which keeps a small back-and-forth scroll free:
        trimming the margin behind would halve the work but blank the page above on every reversal.
        """
        after = list(range(last + 1, hi + 1))            # nearest-first, ahead of the viewport
        before = list(range(first - 1, lo - 1, -1))      # nearest-first, behind it
        order = before + after if self._scroll_dir < 0 else after + before
        self._prefetch_queue = [i for i in order if self._needs_drawing(i) and not self._is_slow(i)]
        if self._prefetch_queue and not self._prefetch_timer.isActive():
            self._prefetch_timer.start()

    def _drain_prefetch(self) -> None:
        """Rasterise **one** queued prefetch page per tick, and never while a glide is running.

        One page per tick because a single page is 4 ms of text and up to 91 ms of image at 3x zoom:
        draining the queue in a loop would simply move the stall rather than remove it. Yielding to
        the event loop between pages keeps the window responsive even mid-drain.

        Skipping while :attr:`_glide_timer` is active is what actually buys the smooth glide —
        prefetch is speculative, so it can always wait for the animation to finish. The tick keeps
        firing meanwhile and finds its moment as soon as the motion settles. It waits for a
        window edge to rest the same way (M152.1): the pages on screen are drawn first.

        **The honest limit** (recorded so it is not mistaken for a bug): a reader who scrolls fast
        enough to *outrun* the queue reaches a page it has not reached yet, and that page must be
        drawn by :meth:`_render_visible` at once. Since M152.2 a page timed as slow is drawn there in
        pieces, a turn at a time. A page met for the first time is still drawn whole,
        or a window's worth of it — a stall like the old one, but only on genuinely outpacing
        prefetch, and only once. The owner accepted that when Item E was called done (`PLAN.md`
        §M152, *M152.2 as built*, *Its limits*).
        """
        if (self._glide_timer.isActive() or self._settle_timer.isActive()
                or self._pieces_on_screen_pending()):
            return                          # the animation, a resize or the page on screen first
        while self._prefetch_queue:
            index = self._prefetch_queue.pop(0)
            if (0 <= index < len(self._pages) and self._needs_drawing(index)
                    and not self._is_slow(index)):
                self._paint_page(index)
                break                       # exactly one page per tick
        if not self._prefetch_queue:
            self._prefetch_timer.stop()     # emptied — don't spend a tick discovering that

    def _render_visible(self) -> None:
        if not self._pages or not self._shown_once:
            return
        if self._render_held:
            return          # inside _hold_render — one pass runs when the outermost block closes
        first, last = self._visible_range()
        prefetch = self._prefetch(first, last)   # shrinks as the pages get heavier (M87.1)
        lo, hi = max(0, first - prefetch), min(len(self._pages) - 1, last + prefetch)
        # Pin the band **before** rendering it (M87.2). The plan asks for the visible pages; this
        # pins the whole band the pass is about to populate, which is a superset and is what makes
        # "no thrash while scrolling" true by construction rather than by picking a large enough
        # budget: nothing rendered in this pass can be evicted by a later page of the same pass.
        band = [self._pixmap_key(i) for i in range(lo, hi + 1)]
        if self._settle_timer.isActive():
            # **A slow page while a window edge moves** (M152.1): show what the store has,
            # stretched, and draw nothing. The timer runs this pass again once the edge rests, and
            # that pass draws. The stretched pictures are pinned with the band, or drawing in
            # another window could evict the picture on screen.
            shown = [self._show_stand_in(i) for i in range(first, last + 1)]
            self._cache.pin(band + [key for key in shown if key is not None])
        else:
            self._cache.pin(band)
            # **Only the pages the reader can actually see are rasterised here** (M92.4). The
            # prefetch margin is queued instead — see :meth:`_queue_prefetch` for the measurement
            # that moved it. **A slow page is drawn in pieces** (M152.2): it shows a stand-in at
            # once, and the pieces follow in turns, so the window keeps handling events.
            shown = []
            for i in range(first, last + 1):
                if self._cache.get(band[i - lo]) is not None or not self._is_slow(i):
                    self._paint_page(i)
                else:
                    shown.append(self._begin_slow(i))
            # A slow page in the band near the window is begun too, so the pieces within a window
            # height of the window are drawn ahead, as a quick page is drawn whole from the queue.
            ahead = self._ahead_rect(self.mapToScene(self.viewport().rect()).boundingRect())
            for i in list(range(lo, first)) + list(range(last + 1, hi + 1)):
                if (self._needs_drawing(i) and self._is_slow(i)
                        and self._local_rect(i, ahead) is not None):
                    shown.append(self._begin_slow(i, on_screen=False))
            self._cache.pin(band + [key for key in shown if key is not None])
            self._queue_prefetch(first, last, lo, hi)
            self._trim_pieces(lo, hi)
            self._turn(visible_only=True)   # the first turn now, so a quick page is never blank
            self._schedule_turn()
        # Drop what scrolled off. This used to be the `else` arm of a loop over **every page in the
        # document**, asking each one whether it held a pixmap; tracking the answer costs a set
        # membership and makes the pass proportional to what is actually painted (M87.3).
        for i in [i for i in self._painted if not lo <= i <= hi]:
            self._pages[i]["pix"].setPixmap(QPixmap())
            self._painted.discard(i)
            self._stand_ins.discard(i)
            self._drop_carried(i)
        # Content marks ride the same band as the pixmaps, so a stamp scrolls in with its page.
        if self.annotations is not None:
            self.annotations._paint_visible_content()
        self._update_current()

    # ---- giving pixels back when nobody is reading this window (M87.2) ------------

    def release_pixmaps(self, *, keep_visible: bool = True) -> None:
        """Hand this view's rendered pixels back to the shared store.

        Two tiers, because "background window" means two different things:

        * ``keep_visible=True`` — the window is no longer *focused* but may still be on screen
          beside the one that is. The scrollback goes (that is the bulk of it, and after a zoom
          sweep it can be most of the process); the band the reader can still see stays, so nothing
          blanks and coming back is free. This is the deviation from the plan's flat "background
          windows drop their pixmaps": on Windows, windows tile, and painting a visible window's
          pages white on deactivation would be a defect traded for memory nobody asked to trade.
        * ``keep_visible=False`` — the window is minimised or hidden, so there is nothing to blank.
          Everything goes, including the scene items' own references, which the store cannot reach.
          :meth:`restore_pixmaps` puts it back at ~6 ms/page for text.
        """
        if not keep_visible:
            # A small blurry copy of what the window shows, so a restore has something to show at
            # once (M152.2, the owner's decision 5): a quarter of the width and height.
            self._carry_visible(0.25)
        self._cache.clear(keep_pinned=keep_visible)
        if not keep_visible:
            for i in self._painted:
                self._pages[i]["pix"].setPixmap(QPixmap())
                item = self._pages[i].get("carry")
                if item is not None:
                    item.setPixmap(QPixmap())
            self._painted.clear()
            self._stand_ins.clear()
            for i in list(self._wip):
                self._drop_pieces(i)
            self._dlists.clear()
            self._piece_timer.stop()
            # A resize waiting to redraw would draw into a minimized window; the restore draws.
            self._settle_timer.stop()
        else:
            # The pieces of pages no longer on screen go with the scrollback.
            first, last = self._visible_range()
            for i in [i for i in self._wip if not first <= i <= last]:
                self._drop_pieces(i)
                self._dlists.pop(i, None)
        # Whatever tier, stop speculating: this window is not the one being read, and a queue left
        # running would rasterise pages straight back into the store we just handed back (M92.4).
        self._prefetch_queue.clear()
        self._prefetch_timer.stop()

    def restore_pixmaps(self) -> None:
        """Re-render the band after a :meth:`release_pixmaps` that dropped the visible pages."""
        self._render_visible()

    def _update_current(self) -> None:
        """The current page is the one **occupying the most of the viewport**.

        Not the page under the viewport *centre* (M85): that names the page you are reading only
        while pages are taller than half the viewport. A 16:9 slide at Fit Width in a tall window
        was 403 px in a 966 px viewport, so the centre landed 1.2 pages down — jump to page 0 and
        the centre already sits inside page 1. Ordinary A4 documents never reach it, which is how
        it survived to M84. Largest visible area is right for short pages, tall pages and the
        facing spread alike, and is what other viewers do.

        Ties go to the **earlier** page, so a fully-visible facing spread resolves to its left-hand
        page, and landing a short page exactly at the viewport top keeps that page current rather
        than the equally-visible one below it. The epsilon is what makes that tie *stable*: the two
        areas are computed from different scene coordinates and can differ by an ulp, which without
        it would let the later page win at random.

        **The viewport measured is the one the view was sent to** (M151), which is the one on screen
        except near an end of the document, where the view stops short. There the page on screen
        and the page the reader was sent to can differ: zoomed out on the last page, the window
        shows the last three pages whole and the tie went to the first of them, and a Fit Width
        window narrowed on page 10 of 23 ended on page 1 once every page fitted (#359). Both are
        the page the next zoom or resize starts from, so both have to be the reader's page.

        Measuring the view as sent is not enough on its own: zoomed out on the last page, the
        sent view still holds the page before it whole. So while the view is stopped short, a tie
        goes to the page it was sent to rather than the earlier one. Anywhere else the earlier
        page still wins, exactly as above.
        """
        actual = self.mapToScene(self.viewport().rect()).boundingRect()
        view_rect = actual.translated(self._intended_origin() - actual.topLeft())
        first, last = self._pages_in(view_rect)
        short = any(stopped for _want, _value, stopped in self._sent.values())
        sent = self._sent_page if short else None
        current, most = first, -1.0
        for i in range(first, last + 1):
            p = self._pages[i]
            shown = view_rect.intersected(QRectF(p["x"], p["y"], p["w"], p["h"]))
            area = shown.width() * shown.height()
            # 1 px² — far below any difference a reader could mean. A later page that only ties
            # wins when it is the page the view was sent to and stopped short of.
            if area > most + 1.0 or (i == sent and area > most - 1.0):
                current, most = i, area
        if current != self._current:
            self._current = current
            self.currentPageChanged.emit(current)

    def _on_scroll(self, _value: int) -> None:
        # Which way the reader is going, so prefetch queues towards it first (M92.4). Read before
        # _render_visible, which is what consumes it; unchanged position leaves the last direction
        # standing rather than resetting to "unknown", so a glide's final frame does not forget.
        if _value != self._last_scroll_value:
            self._scroll_dir = 1 if _value > self._last_scroll_value else -1
            self._last_scroll_value = _value
        self._render_visible()
        self._reposition_overlay_editors()  # keep an open inline editor on its field while scrolling
        if self.selection is not None:
            # The selection's rects now live only for the pages on screen (M89.6), so a scroll can
            # uncover selected text that has nothing painted over it. Cheap: it returns immediately
            # unless the band actually moved.
            self.selection.repaint_for_scroll()
        if self._slideshow:
            # A scroll from anywhere else (the scrollbar, a pan) re-seats the projected row, so the
            # next step continues from what is on screen. Steps of our own re-assert it afterwards.
            self._slide_row = self._row_of(self._current)

    def resizeEvent(self, event) -> None:
        if self._fit_mode is None or not self._pages:
            super().resizeEvent(event)
            return
        # A sticky Fit Width/Page follows the new viewport (e.g. a Pages-sidebar toggle), and the
        # line at the top of the window stays there while the page is resized around it (M151).
        # Read it **before** ``super()``, which is where Qt re-clamps the scrollbars to the new
        # size: a clamp is not the reader moving, and reading after it would lose the place the
        # view was sent to. The hold spans both, so a drag-resize costs one rasterise per step.
        top = self._top_line_anchor()
        # **A slow page is stretched, not redrawn, until the edge rests** (M152.1, #360). A drag
        # sends a resize for every mouse move, and with a fit on each one changes the zoom. On the
        # NADA cover each step drew the page again, about 1 s, and on Windows the drag was lost
        # while it did. So when the pages on screen took longer than the budget to draw last time,
        # this step and every step until the edge has rested show the pictures already drawn,
        # stretched to the new size. Each step restarts the wait.
        if self._settle_timer.isActive() or self._visible_cost() > _DRAW_BUDGET_S:
            self._settle_timer.start(_RESIZE_SETTLE_MS)
        with self._hold_render():
            super().resizeEvent(event)
            self._reapply_fit(top)

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
        self._redact_combined = False  # an explicit arm is never the resolved combined slot
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        self.armedChanged.emit(tool)

    def disarm(self) -> None:
        """Cancel any armed one-shot tool and return to plain SELECT behaviour."""
        self._redact_combined = False
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
        self._settle_timer.stop()   # the store is empty, so there is nothing to stretch (M152.1)
        # Every picture on screen was drawn in the other palette too (M152.2). A quick page was
        # always drawn again at once below, but a slow one would show its old picture stretched
        # until its pieces covered it, so everything goes.
        for i in list(self._wip):
            self._drop_pieces(i)
        for i in list(self._carried):
            self._drop_carried(i)
        for i in self._painted:
            self._pages[i]["pix"].setPixmap(QPixmap())
        self._painted.clear()
        self._stand_ins.clear()
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

    def _anchor_at(self, view_pos=None) -> "tuple[int, float, float] | None":
        """The content point under viewport point ``view_pos`` as ``(page_index, fx, fy)``, where
        fx/fy are fractions of that page's scene rect. The rect scales uniformly with zoom, so the
        fractions are zoom-invariant — a handle that lets a zoom hold a chosen point fixed instead
        of snapping to a page top / left edge. ``None`` before any page exists.

        ``view_pos`` of ``None`` means the viewport centre — the right anchor for every zoom with
        no pointer behind it (menu, toolbar, typed percentage, Ctrl+±); the pointer case is the
        Ctrl+wheel gesture (M80).

        **The two read different views** (M151, owner's calls, 2026-09-21). The centre is read
        from the view as it was **sent**, so zooming out with the buttons and back in returns to
        where you were even when an end of the document stopped the view short: on page 1 the
        middle of a zoomed-out window sits on page 2, and before M151 the next zoom in went there
        (#357). The pointer is read from the view **on screen**, because the reader can see what
        they are pointing at: zoomed out from page 1 until page 2 is under the mouse, a wheel zoom
        in goes to page 2, whether or not the mouse moved.
        """
        if not self._pages:
            return None
        if view_pos is None:
            return (self._kept_anchor("centre")
                    or self._content_at(self._intended_origin() + self._centre()))
        return self._content_at(self.mapToScene(view_pos))

    def _top_line_anchor(self) -> "tuple[int, float, float]":
        """The content at the **top line** of the view it was sent to — see :meth:`_top_line`."""
        return (self._kept_anchor("top")
                or self._content_at(self._intended_origin() + self._top_line()))

    def _kept_anchor(self, spot: str) -> "tuple[int, float, float] | None":
        """The content point the last placement held at ``spot``, while the reader has not scrolled
        since — or ``None``, and the caller reads the view as it was sent.

        Reading the point back from the view is not exact even then, because **the window changes
        size under a placement**: a page grown wider than the window brings in a horizontal
        scrollbar, the window loses its height, and its middle moves up by half of it. The first
        build of M151 read the view back and so returned from 500% 10 px away from where it left.
        Kept, the point is the one the reader was on, whatever the scrollbars did since.
        """
        self._intended_origin()     # forgets whichever axis the reader has scrolled
        if self._sent_anchor is None or set(self._sent) != self._sent_axes:
            return None
        anchor, kept_at = self._sent_anchor
        return anchor if kept_at == spot else None

    def _content_at(self, pt: QPointF) -> "tuple[int, float, float]":
        """Scene point ``pt`` as ``(page_index, fx, fy)`` — see :meth:`_anchor_at`. In a facing
        row the point is measured against the row's first page, with ``fx`` past 1 on the right.

        **A point in the grey gap between two rows is the top of the page below it** (M151). The
        gap stays ``_PAGE_GAP`` pixels at every zoom while a page fraction grows with it, so a
        fraction measured into the gap lands somewhere else after the zoom. And the page it was
        measured against used to be the current page, which is itself worked out from the view:
        narrowing a Fit Width window put the top line in the gap above page 10, the current page
        turned to 11, and the next step measured from page 11 and drifted. A point past either end
        of the document keeps its fraction — the first or last page, measured beyond its edge — as
        that is where a placement stopped short meant it to be.
        """
        tops = self._page_tops
        row = bisect_right(tops, pt.y()) - 1          # the last row starting at or above pt
        if row < 0:
            pi, snap = 0, False                         # above the first page
        else:
            pi = bisect_left(tops, tops[row])           # that row's first page
            nxt = bisect_right(tops, tops[row])         # the next row's first page
            height = max(self._pages[i]["h"] for i in range(pi, nxt))   # a row is its tallest page
            below = pt.y() > self._pages[pi]["y"] + height
            snap = below and nxt < len(self._pages)
            if snap:
                pi = nxt
        p = self._pages[pi]
        fx = (pt.x() - p["x"]) / p["w"] if p["w"] else 0.5
        fy = 0.0 if snap else ((pt.y() - p["y"]) / p["h"] if p["h"] else 0.5)
        return pi, fx, fy

    def _centre(self) -> QPointF:
        """The viewport's centre, as the one point both :meth:`_anchor_at` and
        :meth:`_restore_anchor` use, so a zoom puts back exactly the point it read."""
        return QPointF(self.viewport().width() / 2.0, self.viewport().height() / 2.0)

    def _top_line(self) -> QPointF:
        """The point a resize holds still with a fit on (M151, owner's call): the reader's top
        line. **Narrowing the window zooms out and shows more below it; widening zooms in and shows
        less; the line stays where it is.**

        It sits ``_PAGE_GAP`` below the window's top edge because that is where :meth:`goto_page`
        puts a page's top edge, so right after a jump the top line is the page's first line. The
        window's very top edge would instead sit in the grey gap above the page, and the gap does
        not grow with the zoom, so the page would creep a few pixels at every step.
        """
        return QPointF(self.viewport().width() / 2.0, float(_PAGE_GAP))

    def _restore_anchor(self, anchor: "tuple[int, float, float]", view_pos=None) -> None:
        """Scroll so the ``(page_index, fx, fy)`` from :meth:`_anchor_at` sits back under
        ``view_pos`` — or under the viewport centre when it is ``None``.

        The position is set directly rather than by ``centerOn``. That is a pixel delta because the
        view transform stays identity — zoom rebuilds the scene at the new page size rather than
        scaling the view (see :meth:`_build_scene`). The scrollbars clamp on their own, and the
        view alignment re-centres a page that now fits without scrollbars, which is the wanted
        behaviour at the edges: as close to the anchor as the document allows — and
        :meth:`_scroll_to` remembers the rest (M151).
        """
        pi, fx, fy = anchor
        if not (0 <= pi < len(self._pages)):
            return
        p = self._pages[pi]
        at = self._centre() if view_pos is None else QPointF(view_pos)
        self._scroll_to(p["x"] + fx * p["w"] - at.x(), p["y"] + fy * p["h"] - at.y(), pi,
                        (anchor, "centre" if view_pos is None else "pointer"))
        self._render_visible()

    def _put_on_top_line(self, anchor: "tuple[int, float, float]") -> None:
        """Scroll so ``anchor`` sits on :meth:`_top_line` again, moving up and down only (M151)."""
        pi, _fx, fy = anchor
        p = self._pages[pi]
        self._scroll_to(None, p["y"] + fy * p["h"] - self._top_line().y(), pi, (anchor, "top"))

    def _scroll_to(self, x: "float | None", y: "float | None", page: int,
                   anchor: "tuple[tuple[int, float, float], str] | None" = None) -> None:
        """Scroll so scene point ``(x, y)`` sits at the viewport's top-left corner, and **remember
        it when the view cannot get there** (M151, #357, #359). ``None`` leaves that axis alone.
        ``page`` is the page the placement is for, which :meth:`_update_current` prefers in a tie,
        and ``anchor`` the content point it holds and where, for :meth:`_kept_anchor`.

        Every placement goes through here: a page jump, a link, a search hit, a zoom, a resize.
        Near an end of the document the view stops short — it cannot scroll above the first page
        or below the last, and a document smaller than the window is centred in it. Before M151 the
        next zoom or resize read the reading position back from what the window showed, which was
        no longer where the reader had been sent, so each step moved them: the zoom buttons walked
        from page 1 to page 2 (#357), and narrowing a Fit Width window walked from page 10 to page 1
        (#359). So the place asked for is kept here, and :meth:`_intended_origin` hands it back
        until the reader scrolls.

        **The exact place is kept for every placement, not only one stopped short.** The scroll
        value is a whole pixel, so the view misses by up to half a pixel even in the middle of the
        document, and a zoom or resize that read the position back from the screen carried that
        rounding into the next step. A drag-resize is hundreds of steps: the first build of M151
        crept the top line down 3.5 px in fifteen. Read from here, each step starts from exactly
        the point the last one placed. Whether the view *stopped short* — missed by more than that
        half pixel — is kept beside it, because only then does :meth:`_update_current` prefer
        ``page`` in a tie.

        The scroll value is the scene coordinate of the viewport's edge because the scene starts
        at (0, 0) and the view transform is identity (:meth:`_build_scene`).
        """
        self._sent, self._sent_page, self._sent_anchor = {}, page, anchor
        self._sent_axes = frozenset(a for a, v in (("x", x), ("y", y)) if v is not None)
        for axis, want, bar in (("x", x, self.horizontalScrollBar()),
                                ("y", y, self.verticalScrollBar())):
            if want is None:
                continue
            bar.setValue(round(want))
            origin = self.mapToScene(QPoint(0, 0))
            miss = want - (origin.x() if axis == "x" else origin.y())
            self._sent[axis] = (want, bar.value(), abs(miss) > 0.5)

    def _intended_origin(self) -> QPointF:
        """The scene point at the viewport's top-left corner in the view as it was **sent** — see
        :meth:`_scroll_to`. It is the view on screen, to the half pixel, except where an end of the
        document stopped a placement short; and only until the reader moves the view: any scroll
        since then changes the scroll value, and what the reader sees is then where they are."""
        origin = QPointF(self.mapToScene(QPoint(0, 0)))
        for axis, bar in (("x", self.horizontalScrollBar()), ("y", self.verticalScrollBar())):
            sent = self._sent.get(axis)
            if sent is None:
                continue
            want, value, _short = sent
            if bar.value() != value:
                del self._sent[axis]
            elif axis == "x":
                origin.setX(want)
            else:
                origin.setY(want)
        return origin

    def _clamp_zoom(self, zoom: float, fit: bool = False, step: bool = False) -> float:
        """Hold ``zoom`` inside 25%–500% (M88.6) — with **one** way out of the bottom: a fit.

        Three floors, because the three kinds of request mean different things (M149, #377; owner's
        call). ``fit`` is a Fit Page / Fit Width zoom, ``step`` is a *relative* one — the zoom
        buttons, Ctrl+±, Ctrl+wheel — and everything else is a reader naming a magnification: a
        typed percentage or a preset.

        * **A fit may go as small as the page needs.** A hard 25% floor breaks Fit Page on a
          large-format sheet: an A0 in a 1100x850 window wants ~17%, and clamping that to 25%
          overshoots the viewport in both portrait and landscape (measured for M88.6). A "Fit Page"
          that does not fit the page is simply broken.
        * **A step outward never moves the reader in.** Below 25% — where only a fit can have put
          them — zooming out is a no-op rather than a jump back up to the floor, because a control
          labelled "zoom out" must never make the page bigger.
        * **Everything else lands inside 25%–500%.** A step *inward* from below the floor arrives
          exactly at 25% rather than at 1.25 x wherever the fit left off, and a typed 10% becomes
          25% — the number the toolbar's dropdown has always advertised as the minimum.

        This is what #377 got wrong: the floor was ``min(_MIN_ZOOM, fit_page_zoom)`` for *every*
        path, and the Fit Page zoom falls with the **window** as well as rising with the **page**.
        So a small window silently lowered the floor for the buttons and the zoom field too — at
        400x300 an A4 fits at 19.2%, and typing 10% landed there. The exception was always meant to
        be about a fit that has to fit, never about a licence to ask for less.
        """
        if fit:
            floor = min(_MIN_ZOOM, self._fit_zoom(fit_height=True))
        elif step and zoom < self._zoom:
            floor = min(_MIN_ZOOM, self._zoom)   # already below it → stay, don't zoom them in
        else:
            floor = _MIN_ZOOM
        return max(floor, min(_MAX_ZOOM, zoom))

    def set_zoom(self, zoom: float, keep_page: bool = True, fit: "str | None" = None,
                 anchor_pos=None, step: bool = False) -> None:
        # ``fit`` records the sticky fit-mode this zoom represents ("width" / "page"); it is re-applied
        # on a viewport resize so the fit follows the window (e.g. a Pages-sidebar toggle). A manual
        # zoom passes None, which cancels any sticky fit.
        # ``anchor_pos`` is the viewport point to hold fixed — the pointer, for the Ctrl+wheel
        # gesture (M80). None means the viewport centre, which is right for every zoom that has no
        # pointer behind it (menu, toolbar, typed percentage, Ctrl+±).
        # ``step`` marks a *relative* zoom (the buttons, Ctrl+±, Ctrl+wheel) as against a named
        # magnification, because the floor differs between them — see :meth:`_clamp_zoom`.
        self._fit_mode = fit
        zoom = self._clamp_zoom(zoom, fit=fit is not None, step=step)
        if abs(zoom - self._zoom) < 1e-6:
            return
        page_anchor = self._current
        # A manual zoom (no sticky fit) holds the content under the anchor point fixed, so the view
        # zooms *into* what you're looking at rather than drifting toward a corner. A fit zoom
        # re-lands on the current page's top — its own contract (see fit_width/_center_horizontally).
        held = self._anchor_at(anchor_pos) if fit is None else None
        self._zoom = zoom
        with self._hold_render():   # rebuild + scroll, then rasterise once (M86.1)
            self._build_scene()
            if keep_page:
                if held is not None:
                    self._restore_anchor(held, anchor_pos)
                else:
                    self.goto_page(page_anchor)
        self.zoomChanged.emit(self._zoom)

    def zoom_in(self) -> None:
        self.set_zoom(self._zoom * _ZOOM_STEP, step=True)

    def zoom_out(self) -> None:
        self.set_zoom(self._zoom / _ZOOM_STEP, step=True)

    def actual_size(self) -> None:
        """Reset to 100% — the page at **true physical size** (M88.4).

        Ctrl+0 used to mean "1 PDF point per logical pixel", which drew a Letter page 6.375" wide
        and so made the menu item's name a lie. It now means what it says: hold a ruler to the
        screen and an 8.5" page measures 8.5". Nothing here changed — the re-basing is in
        :attr:`scale`, which is the point of routing every magnification through one definition.
        """
        self.set_zoom(1.0)

    def _fit_dims(self) -> tuple[float, float, float]:
        """The fit target's ``(width_pt, height_pt, extra_px)``: the current page — or, in the
        facing layout (M78), its whole row, so Fit Width/Page frame the spread. ``extra_px`` is
        the inter-page gap, which zoom does not scale."""
        row = next((r for r in self._layout_rows() if self._current in r), (self._current,))
        sizes = [self._natural_size(i) for i in row]
        return (sum(w for w, _h in sizes), max(h for _w, h in sizes),
                float((len(row) - 1) * _PAGE_GAP))

    def _fit_zoom(self, fit_height: bool) -> float:
        """The **zoom** that fits the current row to the viewport.

        The division yields scene units per point; dividing by the DPI factor converts that back to
        a magnification, because :attr:`scale` multiplies it straight back in when the scene is
        built (M88.1). Skip that and Fit Page would overshoot the viewport by 1.333×.
        """
        margin = 2 * _PAGE_GAP
        w_pt, h_pt, extra = self._fit_dims()
        avail_w = max(1, self.viewport().width() - margin - extra)
        scale = avail_w / w_pt
        if fit_height:
            avail_h = max(1, self.viewport().height() - margin)
            scale = min(scale, avail_h / h_pt)
        return scale / (self._logical_dpi / 72.0)

    def fit_width(self) -> None:
        self.set_zoom(self._fit_zoom(fit_height=False), fit="width")
        self._center_horizontally()

    def fit_page(self) -> None:
        self.set_zoom(self._fit_zoom(fit_height=True), fit="page")
        self._center_horizontally()

    def _reapply_fit(self, top: "tuple[int, float, float] | None" = None) -> None:
        """Re-run the active sticky fit against the current viewport.

        A resize passes ``top``, the content on the top line from :meth:`_top_line_anchor`, and
        the page is re-fitted around it so that line stays where it is (M151, owner's call). This
        runs even when the fit zoom has not changed: a window made taller at the end of the
        document has to show more above, and made shorter again it should put the top line back.
        Without ``top`` — a layout switch — the view lands on the current page's top, as a fit
        always did before M151.
        """
        if self._fit_mode is None:
            return
        self.set_zoom(self._fit_zoom(fit_height=self._fit_mode == "page"), fit=self._fit_mode,
                      keep_page=top is None)
        if top is not None:
            self._put_on_top_line(top)
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
        with self._hold_render():
            self._build_scene()
            self.goto_page(anchor)

    # ---- page layout (M78) ------------------------------------------------------

    @property
    def page_layout(self) -> str:
        return self._page_layout

    def set_page_layout(self, layout: str) -> None:
        """Switch between the single vertical strip and the facing two-page layout (M78) —
        view-only, a pure re-layout like zoom/rotation. A sticky Fit Width/Page re-fits against
        the new row dimensions (a facing fit frames the spread); a manual zoom is kept as-is."""
        if layout not in ("single", "facing") or layout == self._page_layout:
            return
        self._page_layout = layout
        anchor = self._current
        with self._hold_render():    # the re-fit's own hold nests into this one
            self._build_scene()      # unconditionally: set_zoom below no-ops on an equal zoom
            if self._fit_mode is not None:
                self._reapply_fit()  # rebuilds again only if the re-fitted zoom differs
            self.goto_page(anchor)
        self._slide_row = self._row_of(self._current)

    # ---- slideshow (M78) --------------------------------------------------------

    @property
    def slideshow(self) -> bool:
        return self._slideshow

    @slideshow.setter
    def slideshow(self, on: bool) -> None:
        """Enter/leave the projected reading mode. Entering adopts the page being read as the
        starting slide, so the first step goes where the reader expects."""
        self._slideshow = bool(on)
        self._wheel_accum = 0
        if self._slideshow:
            self._slide_row = self._row_of(self._current)

    def _row_of(self, page_index: int) -> int:
        """The layout row holding ``page_index`` — the unit a slideshow step moves by. In the
        facing layout (M78) a row is the 1|2 spread, so a step is a spread, not a page."""
        for i, row in enumerate(self._layout_rows()):
            if page_index in row:
                return i
        return 0

    def step_slide(self, delta: int) -> None:
        """Move the slideshow ``delta`` rows (clamped at both ends) and land on that row's top.

        Steps move **rows**, not page indices: in the facing layout pages 1 and 2 share a row —
        stepping by index landed on the same scroll offset and the mode looked dead (the click and
        the forward keys did nothing). And the destination is recorded rather than re-derived from
        the scroll offset afterwards: a free scroll can leave the viewport centre over a *different*
        page than the one on screen, which made the next click jump somewhere unexpected.
        """
        rows = self._layout_rows()
        if not rows:
            return
        target = max(0, min(self._slide_row + delta, len(rows) - 1))
        if target == self._slide_row and self._current == rows[target][0]:
            return      # already there (a burst running into either end) — no scroll, no render
        first = rows[target][0]
        self.goto_page(first)
        # goto_page re-derives "current" from the viewport centre, which near the end of the
        # document (where the scroll clamps) or on a short page can name a different page than the
        # one just projected. The projected row is the truth here — otherwise the next step
        # counts from a page the reader isn't looking at. Assigned *after* the scroll, whose
        # _on_scroll resync would otherwise overwrite it.
        self.set_current_page(first)
        self._slide_row = target

    def _deliberate_step(self, delta: int) -> None:
        """A step the reader asked for by hand — a click or a key, never the wheel. It parks a
        coasting wheel (see :meth:`wheelEvent`) so the slide it lands on stays put."""
        self._park_coasting_wheel()
        self._wheel_accum = 0
        self.step_slide(delta)

    def _park_coasting_wheel(self) -> None:
        """Mute the wheel until it has actually gone quiet, so a deliberate move survives the
        events a flywheel wheel is still emitting (see :meth:`wheelEvent` for the full account).

        Called by every navigation the *reader* asked for — the paging keys, Home/End, a slideshow
        step, and :meth:`goto_page`, which is where the thumbnail, the outline, the page counter,
        Ctrl+G and internal links all arrive. Arming it when no wheel is spinning costs nothing: the
        next wheel event is then more than :data:`_WHEEL_QUIET_MS` from the last one, so it lifts
        the mute and scrolls, which is why the internal callers of ``goto_page`` (rebuilds, zoom,
        reopen) can share the entry point without a special case.

        The one caller that must **not** arm it is the wheel itself — see :meth:`wheelEvent`.

        Since M92.2 this also ends any **glide** in flight, for the same reason and with none of the
        guesswork: a hardware coast has to be waited out, but our own animation can simply be
        stopped. Without it, ``Space`` pressed mid-glide would be undone by our easing — M91.4's
        defect re-created by the very milestone that had the means to avoid it.
        """
        self.stop_glide()
        if not self._wheel_driving:
            self._wheel_muted = True
            # Start the M92.3 ceiling running, and forget any previous coast's direction — this is
            # a fresh mute, and inheriting the last one's direction would let a reversal that had
            # already been used lift it on its first event.
            self._wheel_mute_start = self._now_ms()
            self._wheel_mute_dir = 0

    def reload(self) -> bool:
        """Rebuild after the ordered list changed (edit). Page indices remap, so the pixmap
        cache (keyed by ordered index) is dropped to avoid showing stale pages; the render
        copies are dropped too so a changed field value / annotation re-renders, and the text
        selection's word cache is invalidated (same reasons — remapped indices, stripped marks).

        Returns ``True`` when the edit was **structural** (page count / order / geometry changed),
        ``False`` for a content-only edit — the same distinction the scroll-anchor logic below draws,
        exposed so a caller can tell whether page-index-keyed state (e.g. search hits) is still valid."""
        self._cache.clear()
        # The store is empty, so a resize waiting to redraw has nothing to stretch: draw now
        # (M152.1). The display lists were read from the old pages (M152.2).
        self._settle_timer.stop()
        self._dlists.clear()
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
        # whichever fills most of the viewport (M85), which may not be the page they edited.
        # A **structural** edit (insert / delete / reorder / rotate / crop) remaps the layout, so
        # there the current-page anchor is the only sensible place to land.
        layout = self._layout_signature()
        offset = self.verticalScrollBar().value()
        sent = (self._sent, self._sent_anchor)
        with self._hold_render():
            self._build_scene()
            structural = self._layout_signature() != layout
            if not structural:
                self.verticalScrollBar().setValue(offset)
                # The same layout, so where the view was sent still holds (M151).
                self._sent, self._sent_anchor = sent
            else:
                # The pages may have moved, so the pictures carried from before the edit and the
                # drawing times belong to other pages now (M152.2). After an edit that moves no
                # page, the carried pictures stand in while the pages are drawn again.
                for i in list(self._carried):
                    self._drop_carried(i)
                self._draw_rate.clear()
                self.goto_page(self._current)
        return structural

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
        # Every "take me to this page" gesture lands here — the thumbnail, the outline, the page
        # counter, Ctrl+G, an internal link — so this is the one place that has to park a coasting
        # wheel for all of them (M91.4).
        self._park_coasting_wheel()
        p = self._pages[index]
        self._scroll_to(None, int(p["y"]) - _PAGE_GAP, index)
        self._render_visible()

    def goto_destination(self, index: int, top: float | None) -> None:
        """Go to page ``index`` and land on the spot its bookmark or link points at (M150, #362).

        **A bookmark or a link moves the page up and down only — never sideways** (owner's rule,
        2026-09-20). A destination can name a left edge as well as a height, and the first version
        of this honoured it. That was wrong twice over. It threw a page that already fitted the
        window off to one side, which is what the owner hit on
        `SpaceX-EUProspectus-outlined.pdf` at Fit Width. And even corrected to "only when the spot
        is off screen", it still moved the page sideways under a reader who had not asked for it.
        So the left is read from the file and **not acted on**: clicking a contents entry changes
        how far down the document you are, and nothing else.

        ``top`` is how far down page ``index`` to land, in the page's own points, measured from the
        top of the visible page — the same numbers ``get_heading_candidates`` and ``search`` use
        for a box, and what :func:`~model.destinations.content_point` returns. ``None`` means the
        destination names no height at all (``/Fit``, or an ``/XYZ`` whose height is left blank),
        and this is then exactly :meth:`goto_page`: the top of the page, which is what the app did
        for every link before this milestone.

        The spot goes at the **top of the window**. That is what a PDF destination means and what
        other viewers do — and it is why Cisco's contents entries used to land a page early: each
        one points at the foot of the page *before* its section, so using the spot shows that
        page's bottom margin and then the section, while ignoring it shows the wrong page.

        **The magnification a destination may also ask for is ignored** (owner's call, 2026-09-20).
        Some destinations say "go here at 200%", and some imply a zoom of their own. Obeying any of
        them would drop a reader out of the Fit Width or Fit Page they chose, and arriving where
        you were going without being resized on the way is the point of the feature.

        The spot is mapped through :meth:`page_transform`, so a rotated page — or one the reader
        has spun — lands correctly without this method knowing anything about rotation. On a page
        turned a quarter turn, a line that runs across the paper runs *down* the screen, so "how
        far down" stops having one answer: both ends of the line are mapped and the higher one on
        screen wins. Reading one fixed end instead would be right at 90° and wrong at 270°, where
        that same end is the one nearest the bottom.

        **A spot outside the page is pulled back onto it**, which is what a reader means by "go
        there". This is not a corner case: **283** of the corpus's 4,617 destinations that name a
        height name one the page does not contain, three ways — Cisco's 10-K aims at the corner of
        the *paper* on pages whose printed area starts 24 pt inside it (100), the SpaceX prospectus
        overshoots the top by 36.75 pt (21), and a Javadoc set points 130 pt *below* the bottom
        (37). Unclamped, each scrolls past the page into the grey around it. A fourth group is not
        out of bounds but reads as though it were: the Sony manual's 591 links aim at 841.92 on an
        841.92 pt page, landing a hair above the top edge and leaving a sliver of grey showing.

        The bounds are the page's own displayed box, so there is no number to tune, and a crop the
        reader applied is honoured because :meth:`_crop_origin` and :meth:`_unrotated_size`
        describe *that* frame rather than the file's. The clamp lives here and not in
        :func:`~model.destinations.content_point`, which stays honest about what the file says:
        where a destination points and where a viewer can go are two different questions, and the
        bridge's reading tools want the first.
        """
        if not 0 <= index < len(self._pages):
            return
        if top is None:
            self.goto_page(index)
            return
        self._park_coasting_wheel()
        width, height = self._unrotated_size(index)
        ox, oy = self._crop_origin(index)
        top = min(max(top, oy), oy + height)
        transform = self.page_transform(index)
        scene_y = min(transform.map(QPointF(x, top)).y() for x in (ox, ox + width))
        self._scroll_to(None, int(scene_y) - _PAGE_GAP, index)
        self._render_visible()

    # ---- persistence ------------------------------------------------------------

    def view_state(self) -> dict:
        """The per-document state saved on close. **Page and rotation resume; zoom does not.**

        ``zoom`` is written on purpose even though nothing reads it back. A document opens at
        **Fit Page** — an owner decision from v0.9.1 (PR #61: "Default zoom is Fit Page — the whole
        page is visible — instead of Fit Width… Per-document page + rotation still resume on
        reopen"), taken because a remembered magnification kept reopening documents too large for
        the window. Keeping the value in the file costs one float and leaves the option open should
        we ever decide to restore it; dropping it would mean every existing state file lost the
        magnification the day we changed our minds.

        So a reader reporting "the zoom isn't remembered" is describing the design, not a bug — see
        :meth:`open_at`, and `PLAN.md` §Future enhancements for what restoring it would take.
        """
        return {"page": self._current, "zoom": self._zoom, "rotation": self._rotation}

    def apply_state(self, state: dict) -> None:
        """Restore page + rotation + **zoom** into an already-shown view.

        The one path that honours a saved zoom — and the app does not currently call it: opening is
        :meth:`open_at`'s job, and that lands at Fit Page by the decision recorded in
        :meth:`view_state`. Kept as the seam a future "restore my last magnification" would use
        (and exercised by the state round-trip tests), not dead-by-accident.
        """
        if not state:
            return
        self._fit_mode = None  # a restored, explicit zoom is manual — not a sticky fit
        rotation = int(state.get("rotation", 0)) % 360
        if rotation in (0, 90, 180, 270):
            self._rotation = rotation
        zoom = state.get("zoom")
        if isinstance(zoom, (int, float)) and _MIN_ZOOM <= zoom <= _MAX_ZOOM:
            self._zoom = float(zoom)
        with self._hold_render():
            self._build_scene()
            self.goto_page(int(state.get("page", 0)))
        # apply_state sets _zoom directly (bypassing set_zoom), so announce it for the indicator.
        self.zoomChanged.emit(self._zoom)

    def open_at(self, state: dict) -> None:
        """First show: restore the remembered page + rotation, open at **Fit Page**, and do the
        first pixmap render — once, at the now-final viewport size. Rendering was suppressed until
        here (``_shown_once``), so the page paints exactly once at the fit zoom — no zoom-1.0 frame,
        no re-render after a remembered zoom, no flicker.

        **Fit Page is deliberate, not an oversight**: the saved ``zoom`` is read past on purpose
        (v0.9.1, PR #61) because a remembered magnification kept reopening documents too large for
        the window — the rationale, and why the value is still saved, is in :meth:`view_state`.
        """
        self._shown_once = True
        state = state or {}
        rotation = int(state.get("rotation", 0)) % 360
        if rotation in (0, 90, 180, 270):
            self._rotation = rotation
        # Hold the remembered page in a LOCAL across the rebuild. `_build_scene` renders at the end
        # of its work, and that render re-derives the current page from the viewport — which is
        # still scrolled to the top, so it resets `_current` to 0. Passing `self._current` to
        # `goto_page` below therefore asked for page 0 and the remembered page was silently never
        # restored (measured: a document saved on page 3 reopened on page 1). `rotate_view` and
        # `set_page_layout` already take a local `anchor` across their rebuilds for this exact
        # reason; this is the one rebuild that read the field back out afterwards.
        page = max(0, min(int(state.get("page", 0)), self._vdoc.page_count - 1))
        self._current = page                          # _fit_zoom sizes against *this* page's row
        self._zoom = self._fit_zoom(fit_height=True)  # Fit Page, computed against the final viewport
        self._fit_mode = "page"                       # default view tracks Fit Page (re-fits on resize)
        with self._hold_render():                     # geometry + the first (and only) render
            self._build_scene()
            self.goto_page(page)                      # resume the remembered page — the LOCAL, not
            self._center_horizontally()               # `self._current`: the hold happens to keep the
                                                      # field intact here, but the restore must not
                                                      # depend on a render optimisation (see above).
        # **Announce the restored page** (M91.4). ``_current`` was assigned directly above, because
        # the fit has to be sized against that page's row before a scene exists to derive it from —
        # so by the time ``goto_page`` scrolls there, :meth:`_update_current` finds the page it
        # already holds and stays silent. Every indicator bound to this signal therefore opened
        # reading **page 1 while the view sat on page 10** (owner report, 2026-07-30). The sidebar
        # had a private workaround for exactly this (``MainWindow.showEvent`` → ``mark_open_page``),
        # which is why it was the one indicator that looked right and why the next one would have
        # been wrong too. Announced at the source, no consumer needs to know.
        self.currentPageChanged.emit(self._current)
        self.zoomChanged.emit(self._zoom)
