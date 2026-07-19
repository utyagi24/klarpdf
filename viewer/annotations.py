"""On-screen annotation preview + the text-box & redaction tools (PLAN.md, M20 / M21).

The rendered page pixmap does not include annotations (they only bake in at materialise), so this
controller paints them as scene overlay items — translucent rects for highlights, a bordered box +
text for text-boxes, opaque fill bars for redactions — the same pattern as ``text_selection`` /
``search``. It also drives the interactive tools, all routed from ``PdfView``:

* **Place text box** (one-shot armed): a click on a page opens an inline editor that **auto-grows in
  width and height** as you type; committing adds a ``TextBox`` (undoable). Placement is clamped to
  the page.
* **Move text box** (SELECT mode): press-drag an existing box to reposition it (clamped to the
  page) — committed as an in-place descriptor swap so it is one undo step.
* **Re-edit text box** (SELECT double-click): reopen the editor on an existing box; emptying it
  removes the box.
* **Redaction** (one-shot armed): a left-drag rubber-bands a rectangle, committed as a
  single-rect ``Redaction``.

Rotation-0 only, like the other overlays.
"""

from __future__ import annotations

import math
from dataclasses import replace

from PySide6.QtCore import QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFontMetricsF,
    QGuiApplication,
    QPainterPath,
    QPen,
    QTransform,
)
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsSimpleTextItem,
    QPlainTextEdit,
)

from model.page_edits import (
    Highlight,
    InkStroke,
    Line,
    Redaction,
    Shape,
    Strikeout,
    TextBox,
    Underline,
    mark_bounds,
    restyle_mark,
    translate_mark,
)
from viewer.markup_style import MarkupStyle
from viewer.tools import ArmedTool
from viewer.text_format_bar import TextBoxStyle, TextFormatBar, qt_font

_TEXTBOX_DEFAULT = (200.0, 56.0)  # starting size for a new box, in page points (then auto-grows)
_MIN_BOX_W, _MIN_BOX_H = 40.0, 20.0  # a text box never shrinks below this (page points)
_MIN_REDACT = 3.0                 # ignore a redaction drag smaller than this (a stray click)
_MIN_DRAW = 2.0                   # ignore a draw drag smaller than this (page points)
_HIT_PAD = 3.0                    # grab slack around thin drawn marks (page points)

# The markup / draw style (colour · width · fill) is picked from the shared, sticky
# :class:`~viewer.markup_style.MarkupStyle` (M59.5) — its defaults are the old M58 fixed
# redline-red-at-2pt, so an untouched picker draws exactly as before.

_DRAWN_TYPES = (InkStroke, Line, Shape)


def _line_path(start: tuple, end: tuple, arrow_start: bool, arrow_end: bool,
               width: float) -> QPainterPath:
    """A line's painter path in page points, with open-arrow heads where flagged — the same
    geometry for the live preview and the baked-mark overlay, so WYSIWYG holds."""
    path = QPainterPath()
    path.moveTo(*start)
    path.lineTo(*end)
    length = math.hypot(end[0] - start[0], end[1] - start[1])
    if length < 1e-6:
        return path
    head = max(6.0, width * 4.0)
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    spread = math.radians(25)
    if arrow_end:
        for side in (-spread, spread):
            path.moveTo(*end)
            path.lineTo(end[0] - head * math.cos(angle + side),
                        end[1] - head * math.sin(angle + side))
    if arrow_start:
        back = angle + math.pi
        for side in (-spread, spread):
            path.moveTo(*start)
            path.lineTo(start[0] - head * math.cos(back + side),
                        start[1] - head * math.sin(back + side))
    return path




class _TextBoxEditor(QPlainTextEdit):
    committed = Signal()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.committed.emit()


class AnnotationOverlay:
    def __init__(self, view, on_add, on_remove=None, on_replace=None, on_select=None,
                 on_replace_many=None, on_remove_many=None) -> None:
        self._view = view
        self._on_add = on_add               # on_add(page_index, annotation) — pushes Add command
        self._on_remove = on_remove         # on_remove(page_index, annotation)
        self._on_replace = on_replace       # on_replace(page_index, old, new) — move / re-edit
        self._on_select = on_select         # on_select(mark) — an object was click-selected (M59.5)
        # Batch (one-undo-macro) callbacks for group ops (M59.6): restyle / move / delete a group.
        self._on_replace_many = on_replace_many   # on_replace_many(page, [(old, new), …], text)
        self._on_remove_many = on_remove_many     # on_remove_many(page, [mark, …], text)
        self._items: list[QGraphicsRectItem] = []
        # Inline editor (placing a new box, or re-editing an existing one) + its formatting bar.
        self._editor: _TextBoxEditor | None = None
        self._editor_page = 0
        self._editor_rect: tuple = (0, 0, 0, 0)
        self._editing: tuple | None = None  # (page_index, existing TextBox) when re-editing, else None
        # The style stamped on the next new box — sticky across boxes (the last-used font/colour/etc.
        # carries forward), and loaded from a box when it's re-edited. Edited via the format bar.
        self._style = TextBoxStyle()
        self._format_bar: TextFormatBar | None = None
        # Suspends the editor's focus-out commit while a modal colour picker is up (the dialog steals
        # focus, which would otherwise commit + close the box before the colour is applied).
        self._suppress_commit = False
        # Live redaction rubber-band: a scene rect item dragged out over a page.
        self._redact_band: QGraphicsRectItem | None = None
        self._redact_page = 0
        self._redact_anchor = None          # the scene point where the drag started
        # The shared, sticky markup / draw style (M59.5): colour · width · fill, stamped onto the
        # next drawn mark (and read by main_window for underline / strikeout colour). Edited via the
        # toolbar MarkupStyleButton — the last-used style carries forward, like the text-box style.
        self._markup_style = MarkupStyle()
        # In-progress draw gesture (M58): the armed draw tool, its page, the anchor + live end
        # point (page points), captured pen points, and the preview path item.
        self._draw_tool = None
        self._draw_page = 0
        self._draw_anchor: tuple | None = None
        self._draw_end: tuple | None = None
        self._draw_points: list = []
        self._draw_item: QGraphicsPathItem | None = None
        # The object selection (M59 single → M59.6 group): a list of (page_index, mark), all on one
        # page — what restyle / move / Delete act on — plus one dashed outline item per mark.
        self._selection: list = []          # [(page_index, mark), …]
        self._selection_items: list = []    # the dashed outlines, parallel to _selection
        self._editor_fontsize = 11.0        # the box font the editor mirrors (WYSIWYG sizing)
        # Live object move (M58 single → M59.6 group): the page, the grab anchor (unrotated page
        # coords), the grabbed mark, one entry per moved mark, and the shared group-clamped delta.
        self._move_page = 0
        self._move_anchor_local = None      # grab point in unrotated page coords (rotation-safe)
        self._move_grabbed = None           # the mark actually under the press (for a no-drag click)
        self._move_items: list = []         # [(mark, (x0, y0, x1, y1), ghost), …]
        self._move_delta: tuple = (0.0, 0.0)
        # Live marquee (M59.6 Objects mode): a rubber-band rect, its page, anchor, and whether Ctrl
        # extends the current group.
        self._marquee_item: QGraphicsRectItem | None = None
        self._marquee_page = 0
        self._marquee_anchor = None
        self._marquee_add = False

    # ---- preview painting -------------------------------------------------------

    def _clear_items(self) -> None:
        scene = self._view.scene()
        for item in self._items:
            try:
                if item.scene() is scene:
                    scene.removeItem(item)
            except RuntimeError:
                pass  # already dropped by scene.clear() during a rebuild
        self._items.clear()

    def repaint(self) -> None:
        """Redraw every page's annotations from the model (also after zoom / scene rebuild)."""
        self._clear_items()
        # A structural change / scene rebuild orphans the selection (its items died with the
        # scene, and the marks themselves may be gone) — selection never survives a repaint.
        self._selection = []
        self._selection_items = []
        if self._view.rotation != 0:
            return
        scene = self._view.scene()
        vdoc = self._view._vdoc
        for page_index in range(vdoc.page_count):
            for annot in vdoc.ordered[page_index].annotations:
                if isinstance(annot, Highlight):
                    fill = QColor.fromRgbF(*annot.color)
                    fill.setAlpha(110)
                    brush = QBrush(fill)
                    for box in annot.rects:
                        item = QGraphicsRectItem(self._view.scene_rect_for_box(page_index, box))
                        item.setBrush(brush)
                        item.setPen(QColor(0, 0, 0, 0))
                        item.setZValue(6)
                        scene.addItem(item)
                        self._items.append(item)
                elif isinstance(annot, (Underline, Strikeout)):
                    self._paint_text_line(scene, page_index, annot)
                elif isinstance(annot, _DRAWN_TYPES):
                    self._paint_drawn(scene, page_index, annot)
                elif isinstance(annot, TextBox):
                    self._paint_textbox(scene, page_index, annot)
                elif isinstance(annot, Redaction):
                    self._paint_redaction(scene, page_index, annot)

    def _paint_text_line(self, scene, page_index: int, annot) -> None:
        """Underline / strikeout preview (M56): an opaque thin bar per line rect — along the
        bottom for an underline, through the vertical middle for a strikeout — matching where
        MuPDF draws the baked annotation, so the preview is WYSIWYG with the saved page."""
        colour = QColor.fromRgbF(*annot.color)
        for box in annot.rects:
            x0, y0, x1, y1 = box
            thickness = max(0.9, (y1 - y0) * 0.06)  # ~MuPDF's stroke share of the line height
            if isinstance(annot, Underline):
                bar = (x0, y1 - thickness, x1, y1)
            else:
                mid = (y0 + y1) / 2.0
                bar = (x0, mid - thickness / 2.0, x1, mid + thickness / 2.0)
            item = QGraphicsRectItem(self._view.scene_rect_for_box(page_index, bar))
            item.setBrush(QBrush(colour))
            item.setPen(QColor(0, 0, 0, 0))
            item.setZValue(6)
            scene.addItem(item)
            self._items.append(item)

    def _drawn_pen(self, color: tuple, width: float) -> QPen:
        pen = QPen(QColor.fromRgbF(*color), width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return pen

    def _paint_drawn(self, scene, page_index: int, annot) -> None:
        """Ink / line / shape preview (M58): authored in unrotated page points and pushed through
        the page transform (like text boxes), so the marks zoom and rotate with the page. The pen
        width is in page points — the transform scales it, matching the baked stroke."""
        transform = self._view.page_transform(page_index)
        pen = self._drawn_pen(annot.color, annot.width)
        if isinstance(annot, Shape):
            x0, y0, x1, y1 = annot.rect
            item_cls = QGraphicsRectItem if annot.kind == "rect" else QGraphicsEllipseItem
            item = item_cls(QRectF(x0, y0, x1 - x0, y1 - y0))
            item.setBrush(QColor.fromRgbF(*annot.fill_color) if annot.fill_color is not None
                          else QBrush(Qt.BrushStyle.NoBrush))
        else:
            if isinstance(annot, Line):
                path = _line_path(annot.start, annot.end, annot.arrow_start, annot.arrow_end,
                                  annot.width)
            else:
                path = QPainterPath()
                for pts in annot.paths:
                    path.moveTo(*pts[0])
                    for point in pts[1:]:
                        path.lineTo(*point)
            item = QGraphicsPathItem(path)
        item.setPen(pen)
        item.setTransform(transform)
        item.setZValue(7)
        scene.addItem(item)
        self._items.append(item)

    def _paint_redaction(self, scene, page_index: int, annot: Redaction) -> None:
        # WYSIWYG: the opaque fill boxes are exactly what bakes into the output at save. A thin red
        # border distinguishes a still-editable (undoable) pending redaction from a flat black box.
        for box in annot.rects:
            item = QGraphicsRectItem(self._view.scene_rect_for_box(page_index, box))
            item.setBrush(QBrush(QColor.fromRgbF(*annot.fill)))
            item.setPen(QPen(QColor(200, 0, 0), 1))
            item.setZValue(9)  # above highlights/text-boxes — it removes what's beneath
            scene.addItem(item)
            self._items.append(item)

    def _paint_textbox(self, scene, page_index: int, annot: TextBox) -> None:
        # Author both the box and its text in **unrotated page points**, then apply the page's
        # transform so they rotate *with* the page (zoom + per-page/view rotation all in one matrix).
        # WYSIWYG: the box shows exactly the fill + outline that bake at save — and nothing when
        # neither is set (just the text, still hit-testable by its rect). The font uses pixelSize ==
        # fontsize (page points); the transform's zoom scales it to the rendered/saved on-screen size.
        x0, y0, x1, y1 = annot.rect
        transform = self._view.page_transform(page_index)
        box = QGraphicsRectItem(QRectF(x0, y0, x1 - x0, y1 - y0))
        box.setTransform(transform)
        box.setBrush(QColor.fromRgbF(*annot.fill_color) if annot.fill_color is not None
                     else QBrush(Qt.BrushStyle.NoBrush))
        box.setPen(QPen(QColor(0, 0, 0), annot.border_width) if annot.border_width > 0
                   else QPen(Qt.PenStyle.NoPen))
        box.setZValue(7)
        scene.addItem(box)
        self._items.append(box)
        text = QGraphicsSimpleTextItem(annot.text)
        text.setFont(qt_font(annot.fontname, annot.fontsize))
        text.setBrush(QColor.fromRgbF(*annot.color))
        # Vertically centre the text within the box. The box auto-hugs the text, so a baked FreeText
        # — which PyMuPDF top-aligns (no vertical-align on the simple appearance path) — lands in
        # nearly the same place; centring here keeps the overlay tidy for any residual slack (e.g.
        # the minimum box height). Horizontal inset stays a small fixed gap.
        text_h = text.boundingRect().height()  # in page points (font pixelSize == fontsize)
        ty = y0 + max(1.0, (y1 - y0 - text_h) / 2.0)
        text_transform = QTransform(transform)
        text_transform.translate(x0 + 2, ty)
        text.setTransform(text_transform)
        text.setZValue(8)
        scene.addItem(text)
        self._items.append(text)

    # ---- hit-testing ------------------------------------------------------------

    def annotation_at(self, scene_pt):
        """The ``(page_index, annotation)`` whose painted area contains ``scene_pt``, else None.
        Topmost (most recently added) wins. Uses the rotation-aware scene mapping, so it works on
        rotated pages too."""
        page_index, _ = self._view.page_and_local_at(scene_pt)
        if page_index is None:
            return None
        for annot in reversed(self._view._vdoc.ordered[page_index].annotations):
            if hasattr(annot, "rects"):
                boxes = annot.rects
            elif isinstance(annot, _DRAWN_TYPES):
                # Drawn marks hit-test on a padded bounding rect — a thin or perfectly
                # horizontal/vertical line has a (near-)degenerate box that nothing could click.
                x0, y0, x1, y1 = annot.bounding_rect()
                boxes = ((x0 - _HIT_PAD, y0 - _HIT_PAD, x1 + _HIT_PAD, y1 + _HIT_PAD),)
            else:
                boxes = (annot.rect,)
            for box in boxes:
                if self._view.scene_rect_for_box(page_index, box).contains(scene_pt):
                    return page_index, annot
        return None

    def textbox_at(self, scene_pt):
        """The ``(page_index, TextBox)`` under ``scene_pt`` (topmost), else None — for move / hover /
        re-edit. Only text boxes are interactive this way; highlights & redactions use right-click."""
        page_index, _ = self._view.page_and_local_at(scene_pt)
        if page_index is None:
            return None
        for annot in reversed(self._view._vdoc.ordered[page_index].annotations):
            if isinstance(annot, TextBox) and self._view.scene_rect_for_box(page_index, annot.rect).contains(scene_pt):
                return page_index, annot
        return None

    def remove(self, page_index: int, annotation) -> None:
        if self._on_remove is not None:
            self._on_remove(page_index, annotation)

    def _replace(self, page_index: int, old, new, text: str | None = None) -> None:
        if self._on_replace is not None:
            self._on_replace(page_index, old, new, text)

    def _replace_many(self, page_index: int, pairs: list, text: str) -> None:
        if self._on_replace_many is not None:
            self._on_replace_many(page_index, pairs, text)

    def _remove_many(self, page_index: int, marks: list, text: str) -> None:
        if self._on_remove_many is not None:
            self._on_remove_many(page_index, marks, text)

    # ---- text-box tool: place (new) / re-edit (existing) ------------------------

    def place_textbox(self, scene_pt) -> bool:
        """One-shot place: open an inline editor for a *new* box at ``scene_pt``. Returns True if it
        consumed the click — i.e. the point was on a page **and inside its bounds** (a click in the
        page margin is rejected, so boxes can't land off-page)."""
        if self._view.rotation != 0:
            return False
        page_index, local = self._view.page_and_local_at(scene_pt)
        if page_index is None:
            return False
        pw, ph = self._view._unrotated_size(page_index)
        if not (0 <= local.x() <= pw and 0 <= local.y() <= ph):
            return False  # clicked in the horizontal/vertical margin beside the page
        w, h = _TEXTBOX_DEFAULT
        # Keep the whole default box on the page (shift the origin in from an edge if needed).
        x0 = min(max(0.0, local.x()), max(0.0, pw - _MIN_BOX_W))
        y0 = min(max(0.0, local.y()), max(0.0, ph - _MIN_BOX_H))
        self._editing = None
        self._open_editor(page_index, (x0, y0, min(x0 + w, pw), min(y0 + h, ph)), text="")
        return True

    def edit_textbox_at(self, scene_pt) -> bool:
        """Double-click: reopen the editor on the text box under ``scene_pt`` (pre-filled). Returns
        True if it consumed the event."""
        if self._view.rotation != 0:
            return False
        hit = self.textbox_at(scene_pt)
        if hit is None:
            return False
        page_index, box = hit
        self._editing = (page_index, box)
        self._open_editor(page_index, box.rect, text=box.text, style=TextBoxStyle.from_textbox(box))
        self._editor.selectAll()
        return True

    def _open_editor(self, page_index: int, rect: tuple, text: str,
                     style: TextBoxStyle | None = None) -> None:
        self._close_editor()
        if style is not None:           # re-edit loads the box's style; a new box keeps the sticky one
            self._style = style
        self._editor_page = page_index
        self._editor_rect = rect
        self._editor_fontsize = self._style.fontsize
        editor = _TextBoxEditor(self._view.viewport())
        editor.setPlaceholderText("Note…")
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        if text:
            editor.setPlainText(text)
        editor.committed.connect(self._on_editor_focus_out)
        editor.textChanged.connect(self.reposition_editor)
        self._editor = editor
        self._apply_editor_style()
        bar = self._ensure_format_bar()
        bar.set_style(self._style)
        bar.show()
        self.reposition_editor()  # size to initial content; place the editor + bar
        editor.show()
        editor.setFocus()

    def reposition_editor(self) -> None:
        """Size + place the open editor: auto-grow its box (both dimensions) to fit the text,
        clamped to the page, and lay the widget over that rect at the current zoom.

        The editor's font is set to the box font at the current zoom (``pixelSize = fontsize *
        zoom``) so the editor is WYSIWYG with the rendered box — and the measurement matches what's
        drawn, so text no longer spills past the box edge. Measured with ``QFontMetricsF`` (the
        ``QPlainTextDocumentLayout`` reports height in *line* units / ``idealWidth`` as 0, so it
        can't size the box). The box maps to ``rect * zoom`` view pixels, so pixels ÷ zoom gives
        the page-point size. Called on text change and after any zoom / scroll."""
        editor = self._editor
        if editor is None:
            return
        z = self._view.zoom
        font = qt_font(self._style.fontname, self._editor_fontsize * z)  # family + zoomed size
        editor.setFont(font)
        pw, ph = self._view._unrotated_size(self._editor_page)
        x0, y0 = self._editor_rect[0], self._editor_rect[1]
        avail_w = max(_MIN_BOX_W, pw - x0)
        avail_h = max(_MIN_BOX_H, ph - y0)
        fm = QFontMetricsF(font)
        pad = 2 * editor.document().documentMargin() + 2 * editor.frameWidth() + fm.averageCharWidth() + 4
        # Height uses only the vertical chrome (frame + doc margin) — not the width pad's trailing
        # cursor room — so the box hugs the text vertically instead of sitting much taller than it
        # (which left the text lopsided at the top). The overlay then centres the text in this tight
        # box, and a top-aligned baked FreeText lands in nearly the same place.
        v_pad = 2 * editor.document().documentMargin() + 2 * editor.frameWidth()
        line_h = fm.lineSpacing()
        lines = editor.toPlainText().split("\n") or [""]
        longest_px = max((fm.horizontalAdvance(ln) for ln in lines), default=0.0)
        avail_px = avail_w * z - pad
        if avail_px > 0 and longest_px > avail_px:
            # Longer than the page allows → wrap to the page edge; height absorbs the extra lines.
            editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
            w_pt = avail_w
            visual_lines = sum(max(1, math.ceil(fm.horizontalAdvance(ln) / avail_px)) for ln in lines)
        else:
            editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            w_pt = max(_MIN_BOX_W, (longest_px + pad) / z)
            visual_lines = len(lines)
        h_pt = min(avail_h, max(_MIN_BOX_H, (visual_lines * line_h + v_pad) / z))
        self._editor_rect = (x0, y0, x0 + min(w_pt, avail_w), y0 + h_pt)
        scene_rect = self._view.scene_rect_for_box(self._editor_page, self._editor_rect)
        top_left = self._view.mapFromScene(scene_rect.topLeft())
        bottom_right = self._view.mapFromScene(scene_rect.bottomRight())
        geom = QRect(top_left, bottom_right)
        editor.setGeometry(geom)
        self._position_format_bar(geom)

    def _on_editor_focus_out(self) -> None:
        """The editor lost focus → maybe commit. **Deferred one event-loop tick** on purpose: when
        focus moves to a formatting-bar menu, the focus-out fires *before* the menu's ``aboutToShow``
        (and before a colour dialog opens), so the suppress flags / active-popup state aren't set
        yet. Waiting a tick lets them settle, so :meth:`_commit_textbox` correctly skips a commit that
        is really just the user reaching for the toolbar. The captured editor guards against the
        session having been replaced in the meantime."""
        editor = self._editor
        QTimer.singleShot(0, lambda: self._commit_textbox() if self._editor is editor else None)

    def _commit_textbox(self) -> None:
        if self._editor is None or self._suppress_commit or self._bar_is_interacting():
            return
        raw = self._editor.toPlainText()
        text = raw.strip()  # only to decide empty-vs-not; the box keeps the verbatim text
        page_index, rect, style = self._editor_page, self._editor_rect, self._style
        editing, self._editing = self._editing, None
        self._close_editor()
        if editing is not None:
            _, old = editing
            if not text:
                self.remove(page_index, old)  # emptied (only whitespace) → delete the box
                return
            # Re-commit when the text, the auto-grown rect, OR the style changed (a no-op re-open
            # that touches nothing commits nothing). One descriptor compare covers all three. The
            # text is stored verbatim — leading/trailing spaces and newlines are preserved.
            new = self._textbox(rect, raw, style)
            if new != old:
                self._replace(page_index, old, new, text="Edit text box")
        elif text:
            self._on_add(page_index, self._textbox(rect, raw, style))

    @staticmethod
    def _textbox(rect: tuple, text: str, style: TextBoxStyle) -> TextBox:
        """Build a :class:`TextBox` from a geometry + text + the current :class:`TextBoxStyle`."""
        return TextBox(rect, text, fontsize=style.fontsize, color=style.color,
                       fontname=style.fontname, fill_color=style.fill_color,
                       border_width=style.border_width)

    # ---- formatting bar ---------------------------------------------------------

    @property
    def current_style(self) -> TextBoxStyle:
        return self._style

    def set_current_style(self, style: TextBoxStyle) -> None:
        """Set the sticky style stamped on the next new box (and reflect it in the bar)."""
        self._style = style
        if self._format_bar is not None:
            self._format_bar.set_style(style)

    @property
    def current_markup_style(self) -> MarkupStyle:
        """The sticky colour · width · fill the markup / draw tools stamp on the next mark (M59.5)."""
        return self._markup_style

    def set_markup_style(self, style: MarkupStyle) -> None:
        """Set the sticky markup / draw style (from the toolbar MarkupStyleButton)."""
        self._markup_style = style

    def _ensure_format_bar(self) -> TextFormatBar:
        if self._format_bar is None:
            self._format_bar = TextFormatBar(
                self._view.viewport(), before_modal=self._begin_modal, after_modal=self._end_modal
            )
            self._format_bar.styleChanged.connect(self._on_style_changed)
        return self._format_bar

    def _on_style_changed(self, style: TextBoxStyle) -> None:
        """The bar changed a style control while editing → restyle + re-size the live editor."""
        self._style = style
        self._editor_fontsize = style.fontsize
        self._apply_editor_style()
        self.reposition_editor()
        # Clicking a focus-less bar control (notably the Outline / Fill toggles, which have no menu /
        # dialog to hand focus back) drops the editor's keyboard focus — restore it so a later click
        # on the page fires the editor's focus-out and commits + closes the bar.
        if self._editor is not None:
            self._editor.setFocus()

    def _apply_editor_style(self) -> None:
        """Paint the live editor in the current colour / fill / outline (WYSIWYG). Font family +
        size are applied in :meth:`reposition_editor` (they drive the auto-grow measurement)."""
        editor = self._editor
        if editor is None:
            return
        fg = QColor.fromRgbF(*self._style.color).name()
        # No-fill still gets a near-opaque editor background so typing stays legible over the page;
        # it does not bake (the committed box has no fill).
        bg = (QColor.fromRgbF(*self._style.fill_color).name()
              if self._style.fill_color is not None else "rgba(255,255,255,235)")
        outline = "1px solid black" if self._style.border_width > 0 else "1px solid rgba(120,120,120,160)"
        editor.setStyleSheet(f"QPlainTextEdit {{ color:{fg}; background:{bg}; border:{outline}; }}")

    def _position_format_bar(self, editor_geom: QRect) -> None:
        """Float the bar just above the editor (or below, if the box hugs the viewport's top edge),
        kept within the viewport horizontally."""
        bar = self._format_bar
        if bar is None or not bar.isVisible():
            return
        bar.adjustSize()
        y = editor_geom.top() - bar.height() - 2
        if y < 0:
            y = editor_geom.bottom() + 2
        x = max(0, min(editor_geom.left(), self._view.viewport().width() - bar.width()))
        bar.move(x, y)

    def _begin_modal(self) -> None:
        self._suppress_commit = True   # a colour dialog / menu will steal focus — don't commit on it

    def _end_modal(self) -> None:
        self._suppress_commit = False
        if self._editor is not None:
            self._editor.setFocus()    # hand focus back so a later click-away still commits

    def _bar_is_interacting(self) -> bool:
        """True while the format bar is mid-interaction — a font/size menu pop-up or a colour dialog
        is open, focus sits in the bar, or the pointer is pressing a bar control. The editor's
        focus-out must not commit the box then, or the style edit lands on the *next* box instead of
        this one."""
        from PySide6.QtWidgets import QApplication

        if QApplication.activePopupWidget() is not None or QApplication.activeModalWidget() is not None:
            return True
        bar = self._format_bar
        if bar is None or not bar.isVisible():
            return False
        focus = QApplication.focusWidget()
        if focus is not None and (focus is bar or bar.isAncestorOf(focus)):
            return True
        return self._pointer_over_bar()

    def _pointer_over_bar(self) -> bool:
        """True when the mouse pointer is over the format bar. The colour / fill / outline buttons are
        focus-less and open their dialog only on *release*, so on press the editor's focus-out fires
        with no pop-up or modal up yet and ``suppress`` not set — the one reliable tell at that instant
        is that the pointer is sitting on the bar."""
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QApplication

        bar = self._format_bar
        if bar is None or not bar.isVisible():
            return False
        widget = QApplication.widgetAt(QCursor.pos())
        return widget is not None and (widget is bar or bar.isAncestorOf(widget))

    def _close_editor(self) -> None:
        if self._editor is not None:
            editor, self._editor = self._editor, None
            editor.hide()
            editor.deleteLater()
        if self._format_bar is not None:
            self._format_bar.hide()
        self._suppress_commit = False

    # ---- text-box tool: move (drag an existing box) -----------------------------

    @property
    def editing(self) -> bool:
        """True while the inline text-box editor is open (placing or re-editing a box)."""
        return self._editor is not None

    @property
    def moving(self) -> bool:
        return bool(self._move_items)

    def drawn_mark_at(self, scene_pt):
        """The ``(page_index, drawn mark)`` under ``scene_pt`` (topmost; padded bounding-rect hit
        like :meth:`annotation_at`), else None — the M58 move/delete hit test."""
        page_index, _ = self._view.page_and_local_at(scene_pt)
        if page_index is None:
            return None
        for annot in reversed(self._view._vdoc.ordered[page_index].annotations):
            if not isinstance(annot, _DRAWN_TYPES):
                continue
            x0, y0, x1, y1 = annot.bounding_rect()
            box = (x0 - _HIT_PAD, y0 - _HIT_PAD, x1 + _HIT_PAD, y1 + _HIT_PAD)
            if self._view.scene_rect_for_box(page_index, box).contains(scene_pt):
                return page_index, annot
        return None

    # ---- object selection (M59 single → M59.6 group) ----------------------------
    #
    # A selection is a list of (page_index, mark), all on one page (a "drawing" is per-page). It is
    # what restyle / move / Delete act on. Built by a click (one), a marquee (a rubber-band box), or
    # Ctrl+click (toggle a member). Each selected mark carries a dashed outline.

    @property
    def selected_objects(self) -> list:
        """Every selected ``(page_index, mark)`` (M59.6 group); empty when nothing is selected."""
        return list(self._selection)

    @property
    def selected_object(self) -> "tuple | None":
        """The single selected ``(page_index, mark)`` when exactly one is selected, else None — the
        single-object seam (clipboard copy/cut, style-load) that predates the group (M59)."""
        return self._selection[0] if len(self._selection) == 1 else None

    def _outline_for(self, page_index: int, mark) -> QGraphicsRectItem:
        x0, y0, x1, y1 = mark_bounds(mark)
        outline = QGraphicsRectItem(QRectF(x0 - _HIT_PAD, y0 - _HIT_PAD,
                                           (x1 - x0) + 2 * _HIT_PAD, (y1 - y0) + 2 * _HIT_PAD))
        outline.setTransform(self._view.page_transform(page_index))
        outline.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        outline.setPen(QPen(QColor(0, 120, 215), 1, Qt.PenStyle.DashLine))
        outline.setZValue(12)
        self._view.scene().addItem(outline)
        return outline

    def _clear_selection_items(self) -> None:
        scene = self._view.scene()
        for item in self._selection_items:
            if item.scene() is scene:
                scene.removeItem(item)
        self._selection_items = []

    def _set_selection(self, entries: list) -> None:
        """Replace the selection with ``entries`` (``[(page_index, mark), …]``, all one page) and
        draw an outline per mark. Notifies the picker (style-load) only for a lone selection — a
        group keeps the picker as-is, so applying it restyles the whole group to one style."""
        self._clear_selection_items()
        self._selection = list(entries)
        for page_index, mark in self._selection:
            self._selection_items.append(self._outline_for(page_index, mark))
        if self._on_select is not None:
            self._on_select(self._selection[0][1] if len(self._selection) == 1 else None)

    def select_object(self, page_index: int, mark) -> None:
        """Select a single free-placed mark, replacing any prior selection."""
        self._set_selection([(page_index, mark)])

    def select_object_at(self, scene_pt) -> bool:
        """Select the free-placed mark under ``scene_pt`` (text box or drawn mark), if any."""
        hit = self.textbox_at(scene_pt) or self.drawn_mark_at(scene_pt)
        if hit is None:
            return False
        self.select_object(*hit)
        return True

    def toggle_object(self, page_index: int, mark) -> None:
        """Ctrl+click: add/remove ``mark`` from the group (M59.6). One page per group — a mark on a
        different page starts a fresh selection."""
        if self._selection and self._selection[0][0] != page_index:
            self.select_object(page_index, mark)
            return
        entries = list(self._selection)
        entry = (page_index, mark)
        if entry in entries:
            entries.remove(entry)
        else:
            entries.append(entry)
        self._set_selection(entries)

    def select_in_rect(self, page_index: int, rect: tuple, add: bool = False) -> None:
        """Marquee (M59.6): select every free-placed mark on ``page_index`` whose (padded) bounds
        intersect ``rect`` (unrotated page points). ``add`` unions with the current same-page
        group; a thin/degenerate mark is padded so it can still be caught."""
        box = QRectF(rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1]).normalized()
        hits: list = []
        for mark in self._view._vdoc.ordered[page_index].annotations:
            if not isinstance(mark, (TextBox, *_DRAWN_TYPES)):
                continue
            mx0, my0, mx1, my1 = mark_bounds(mark)
            padded = QRectF(mx0 - _HIT_PAD, my0 - _HIT_PAD,
                            (mx1 - mx0) + 2 * _HIT_PAD, (my1 - my0) + 2 * _HIT_PAD)
            if box.intersects(padded):
                hits.append((page_index, mark))
        if add and self._selection and self._selection[0][0] == page_index:
            entries = list(self._selection)
            for hit in hits:
                if hit not in entries:
                    entries.append(hit)
            self._set_selection(entries)
        else:
            self._set_selection(hits)

    def clear_object_selection(self) -> None:
        self._clear_selection_items()
        self._selection = []

    def remove_selected_objects(self) -> bool:
        """Delete every selected mark (undoable; one macro for a group). True if anything went."""
        if not self._selection:
            return False
        page_index = self._selection[0][0]
        marks = [mark for _p, mark in self._selection]
        self.clear_object_selection()
        if len(marks) == 1:
            self.remove(page_index, marks[0])                 # single: the plain "Remove shape" undo
        else:
            self._remove_many(page_index, marks, f"Delete {len(marks)} objects")
        return True

    def restyle_selected_objects(self, style) -> bool:
        """Apply ``style`` (a :class:`~viewer.markup_style.MarkupStyle`) to every selected drawn
        mark in place — the "same strategy as text markup" the owner asked for, now for a group:
        one picker change restyles the whole selection (one undo step). Text boxes (own format bar)
        and marks already at ``style`` are skipped. Returns True if anything changed.

        The replace reloads the view, which clears the selection, so capture it first and re-select
        the updated marks afterwards (keeping unchanged members selected too)."""
        if not self._selection:
            return False
        selection = list(self._selection)
        page_index = selection[0][0]
        pairs = []
        for _p, mark in selection:
            new = restyle_mark(mark, style.color, style.width, style.fill_color)
            if new is not None and new != mark:
                pairs.append((mark, new))
        if not pairs:
            return False
        if len(pairs) == 1:
            self._replace(page_index, pairs[0][0], pairs[0][1],
                          text=f"Restyle {type(pairs[0][0]).__name__.lower()}")
        else:
            self._replace_many(page_index, pairs, f"Restyle {len(pairs)} objects")
        new_by_old = dict(pairs)
        self._set_selection([(page_index, new_by_old.get(mark, mark)) for _p, mark in selection])
        return True

    def begin_move(self, scene_pt) -> bool:
        """Press on a text box **or a drawn mark** → start a move (M58 single; M59.6 group). If the
        grabbed mark is part of a live multi-selection on this page, the whole group moves together;
        otherwise just this mark. Returns True if it grabbed something."""
        if self._view.rotation != 0:
            return False
        hit = self.textbox_at(scene_pt) or self.drawn_mark_at(scene_pt)
        if hit is None:
            return False
        page_index, mark = hit
        if len(self._selection) > 1 and (page_index, mark) in self._selection:
            move_marks = [m for _p, m in self._selection]      # drag the whole group
        else:
            move_marks = [mark]
        self._move_page = page_index
        self._move_grabbed = mark
        # Grab point in the page's own (unrotated) coords, so the delta is in the same frame as the
        # rects — on a rotated page a raw scene delta would swap axes. Ghosts ride the page transform.
        self._move_anchor_local = self._view.local_point_on_page(page_index, scene_pt)
        self._move_delta = (0.0, 0.0)
        self._move_items = []
        for m in move_marks:
            bounds = mark_bounds(m)
            x0, y0, x1, y1 = bounds
            ghost = QGraphicsRectItem(QRectF(x0, y0, x1 - x0, y1 - y0))
            ghost.setTransform(self._view.page_transform(page_index))
            ghost.setBrush(QColor(0, 120, 215, 40))
            ghost.setPen(QPen(QColor(0, 120, 215), 1, Qt.PenStyle.DashLine))
            ghost.setZValue(12)
            self._view.scene().addItem(ghost)
            self._move_items.append((m, bounds, ghost))
        return True

    def update_move(self, scene_pt) -> None:
        if not self._move_items:
            return
        local = self._view.local_point_on_page(self._move_page, scene_pt)
        dx = local.x() - self._move_anchor_local.x()
        dy = local.y() - self._move_anchor_local.y()
        # Clamp the whole group by its union bounds — one shared delta keeps the group's shape, and
        # no member leaves the page.
        ux0 = min(b[0] for _m, b, _g in self._move_items)
        uy0 = min(b[1] for _m, b, _g in self._move_items)
        ux1 = max(b[2] for _m, b, _g in self._move_items)
        uy1 = max(b[3] for _m, b, _g in self._move_items)
        pw, ph = self._view._unrotated_size(self._move_page)
        lo_x, hi_x = -ux0, pw - ux1
        lo_y, hi_y = -uy0, ph - uy1
        dx = min(max(dx, lo_x), max(lo_x, hi_x))
        dy = min(max(dy, lo_y), max(lo_y, hi_y))
        self._move_delta = (dx, dy)
        for _m, (x0, y0, x1, y1), ghost in self._move_items:
            ghost.setRect(QRectF(x0 + dx, y0 + dy, x1 - x0, y1 - y0))

    def finish_move(self) -> None:
        if not self._move_items:
            return
        items, self._move_items = self._move_items, []
        scene = self._view.scene()
        for _m, _b, ghost in items:
            if ghost.scene() is scene:
                scene.removeItem(ghost)
        page_index, grabbed = self._move_page, self._move_grabbed
        dx, dy = self._move_delta
        self._move_anchor_local = self._move_grabbed = None
        marks = [m for m, _b, _g in items]
        if dx or dy:
            if len(marks) == 1:
                label = {
                    "TextBox": "Move text box",
                    "InkStroke": "Move ink",
                    "Line": "Move line",
                    "Shape": "Move shape",
                }[type(marks[0]).__name__]
                self._replace(page_index, marks[0], translate_mark(marks[0], dx, dy), text=label)
                self._set_selection([(page_index, translate_mark(marks[0], dx, dy))])
            else:
                pairs = [(m, translate_mark(m, dx, dy)) for m in marks]
                self._replace_many(page_index, pairs, f"Move {len(marks)} objects")
                self._set_selection([(page_index, new) for _old, new in pairs])
        else:
            # A plain click, no drag → select just the grabbed mark (collapses a group to it).
            self.select_object(page_index, grabbed)

    # ---- marquee (M59.6 Objects mode: drag a box to select the marks inside) -----

    @property
    def marqueeing(self) -> bool:
        return self._marquee_item is not None

    def begin_marquee(self, scene_pt, add: bool = False) -> bool:
        """Objects-mode press on empty page → start a selection rubber-band. Returns True if the
        point was on a page. Rotation-0 only, like the other overlays."""
        if self._view.rotation != 0:
            return False
        page_index, _ = self._view.page_and_local_at(scene_pt)
        if page_index is None:
            return False
        self._marquee_page = page_index
        self._marquee_anchor = scene_pt
        self._marquee_add = add
        band = QGraphicsRectItem(QRectF(scene_pt, scene_pt))
        band.setBrush(QBrush(QColor(0, 120, 215, 40)))
        band.setPen(QPen(QColor(0, 120, 215), 1, Qt.PenStyle.DashLine))
        band.setZValue(13)
        self._view.scene().addItem(band)
        self._marquee_item = band
        return True

    def update_marquee(self, scene_pt) -> None:
        if self._marquee_item is not None:
            self._marquee_item.setRect(QRectF(self._marquee_anchor, scene_pt).normalized())

    def finish_marquee(self) -> None:
        """Commit the rubber-band: select the marks it covers. A near-zero drag is a plain click on
        empty space → clear the selection (unless Ctrl, which signals additive intent)."""
        if self._marquee_item is None:
            return
        band, self._marquee_item = self._marquee_item, None
        scene_rect = band.rect().normalized()
        self._view.scene().removeItem(band)
        add, self._marquee_anchor = self._marquee_add, None
        if scene_rect.width() < _MIN_DRAW and scene_rect.height() < _MIN_DRAW:
            if not add:
                self.clear_object_selection()
            return
        box = self._view.local_box_from_scene_rect(self._marquee_page, scene_rect)
        self.select_in_rect(self._marquee_page, box, add=add)

    # ---- redaction tool (rubber-band drag) --------------------------------------

    @property
    def redacting(self) -> bool:
        """True while a redaction rubber-band drag is in progress."""
        return self._redact_band is not None

    def begin_redaction(self, scene_pt) -> bool:
        """Armed-redact press: start a rubber-band on the page under ``scene_pt``. Returns True if
        it consumed the press (i.e. the point was on a page). Rotation-0 only, like the overlays."""
        if self._view.rotation != 0:
            return False
        page_index, _ = self._view.page_and_local_at(scene_pt)
        if page_index is None:
            return False
        self._redact_page = page_index
        self._redact_anchor = scene_pt
        band = QGraphicsRectItem(QRectF(scene_pt, scene_pt))
        band.setBrush(QBrush(QColor(0, 0, 0, 150)))
        band.setPen(QPen(QColor(200, 0, 0), 1, Qt.PenStyle.DashLine))
        band.setZValue(10)
        self._view.scene().addItem(band)
        self._redact_band = band
        return True

    def update_redaction(self, scene_pt) -> None:
        """Resize the live rubber-band as the drag moves."""
        if self._redact_band is not None:
            self._redact_band.setRect(QRectF(self._redact_anchor, scene_pt).normalized())

    def finish_redaction(self) -> None:
        """Commit the rubber-band as a single-rect :class:`Redaction` (undoable). A too-small drag
        is dropped."""
        if self._redact_band is None:
            return
        band, self._redact_band = self._redact_band, None
        scene_rect = band.rect().normalized()
        self._view.scene().removeItem(band)
        self._redact_anchor = None
        if scene_rect.width() < _MIN_REDACT or scene_rect.height() < _MIN_REDACT:
            return  # stray click / accidental nudge — nothing marked
        box = self._view.local_box_from_scene_rect(self._redact_page, scene_rect)
        if box[2] - box[0] >= 1.0 and box[3] - box[1] >= 1.0:
            self._on_add(self._redact_page, Redaction((box,)))

    # ---- draw tools (M58): pen path capture + line/arrow/rect/ellipse gestures --

    @property
    def drawing(self) -> bool:
        """True while a draw press-drag gesture is in progress."""
        return self._draw_item is not None

    def begin_draw(self, tool, scene_pt) -> bool:
        """Armed draw-tool press: start the gesture on the page under ``scene_pt``. Returns True
        if it consumed the press (point on a page, inside its bounds); off-page presses leave the
        tool armed, like the other one-shots. Rotation-0 only, like the overlays."""
        if self._view.rotation != 0:
            return False
        page_index, local = self._view.page_and_local_at(scene_pt)
        if page_index is None:
            return False
        pw, ph = self._view._unrotated_size(page_index)
        if not (0 <= local.x() <= pw and 0 <= local.y() <= ph):
            return False
        self._draw_tool = tool
        self._draw_page = page_index
        self._draw_anchor = self._draw_end = (local.x(), local.y())
        self._draw_points = [self._draw_anchor]
        item = QGraphicsPathItem()
        item.setPen(self._drawn_pen(self._markup_style.color, self._markup_style.width))
        item.setTransform(self._view.page_transform(page_index))
        item.setZValue(11)
        self._view.scene().addItem(item)
        self._draw_item = item
        return True

    def update_draw(self, scene_pt, modifiers=None) -> None:
        """Extend the gesture: the pen appends a point, the others track the end point —
        Shift-constrained (square / circle / 45° line) when held. ``modifiers`` defaults to the
        live keyboard state; tests pass them explicitly."""
        if self._draw_item is None:
            return
        if modifiers is None:
            modifiers = QGuiApplication.keyboardModifiers()
        local = self._view.local_point_on_page(self._draw_page, scene_pt)
        pw, ph = self._view._unrotated_size(self._draw_page)
        x = min(max(0.0, local.x()), pw)  # clamp: a mark never leaves its page
        y = min(max(0.0, local.y()), ph)
        if self._draw_tool is ArmedTool.PEN:
            last = self._draw_points[-1]
            if math.hypot(x - last[0], y - last[1]) >= 0.7:  # thin the samples a touch
                self._draw_points.append((x, y))
        else:
            end = (x, y)
            if modifiers & Qt.KeyboardModifier.ShiftModifier:
                end = self._constrained_end(x, y)
            self._draw_end = end
        self._draw_item.setPath(self._gesture_path())

    def _constrained_end(self, x: float, y: float) -> tuple:
        """The Shift constraint: lines/arrows snap to 45° steps; rect/ellipse drags square up."""
        ax, ay = self._draw_anchor
        dx, dy = x - ax, y - ay
        if self._draw_tool in (ArmedTool.LINE, ArmedTool.ARROW):
            length = math.hypot(dx, dy)
            if length < 1e-6:
                return (x, y)
            step = math.radians(45)
            angle = round(math.atan2(dy, dx) / step) * step
            return (ax + length * math.cos(angle), ay + length * math.sin(angle))
        side = max(abs(dx), abs(dy))
        return (ax + math.copysign(side, dx or 1.0), ay + math.copysign(side, dy or 1.0))

    def _gesture_path(self) -> QPainterPath:
        """The live preview path in page points — the same geometry the commit will bake."""
        tool = self._draw_tool
        path = QPainterPath()
        if tool is ArmedTool.PEN:
            path.moveTo(*self._draw_points[0])
            for point in self._draw_points[1:]:
                path.lineTo(*point)
        elif tool in (ArmedTool.LINE, ArmedTool.ARROW):
            path = _line_path(self._draw_anchor, self._draw_end,
                              False, tool is ArmedTool.ARROW, self._markup_style.width)
        else:
            x0, y0 = self._draw_anchor
            x1, y1 = self._draw_end
            rect = QRectF(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            if tool is ArmedTool.RECT:
                path.addRect(rect)
            else:
                path.addEllipse(rect)
        return path

    def finish_draw(self) -> None:
        """Commit the gesture as its descriptor (undoable). A drag smaller than ``_MIN_DRAW`` in
        both axes is dropped as a stray click."""
        if self._draw_item is None:
            return
        item, self._draw_item = self._draw_item, None
        self._view.scene().removeItem(item)
        tool, page_index = self._draw_tool, self._draw_page
        anchor, end, points = self._draw_anchor, self._draw_end, self._draw_points
        self._draw_tool = self._draw_anchor = self._draw_end = None
        self._draw_points = []
        # Stamp the sticky colour · width (+ shape fill) onto the committed mark (M59.5).
        style = self._markup_style
        if tool is ArmedTool.PEN:
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            if len(points) < 2 or (max(xs) - min(xs) < _MIN_DRAW and max(ys) - min(ys) < _MIN_DRAW):
                return
            self._on_add(page_index, InkStroke((tuple(points),), color=style.color,
                                               width=style.width))
            return
        dx, dy = abs(end[0] - anchor[0]), abs(end[1] - anchor[1])
        if dx < _MIN_DRAW and dy < _MIN_DRAW:
            return
        if tool in (ArmedTool.LINE, ArmedTool.ARROW):
            self._on_add(page_index, Line(anchor, end, color=style.color, width=style.width,
                                          arrow_end=tool is ArmedTool.ARROW))
        else:
            rect = (min(anchor[0], end[0]), min(anchor[1], end[1]),
                    max(anchor[0], end[0]), max(anchor[1], end[1]))
            kind = "rect" if tool is ArmedTool.RECT else "ellipse"
            self._on_add(page_index, Shape(kind, rect, color=style.color, width=style.width,
                                           fill_color=style.fill_color))

    def cancel_draw(self) -> None:
        """Drop an in-progress gesture without committing (Esc / disarm mid-drag)."""
        if self._draw_item is None:
            return
        item, self._draw_item = self._draw_item, None
        if item.scene() is self._view.scene():
            self._view.scene().removeItem(item)
        self._draw_tool = self._draw_anchor = self._draw_end = None
        self._draw_points = []
