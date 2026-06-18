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

from PySide6.QtCore import QRect, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPen, QTransform
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsSimpleTextItem, QPlainTextEdit

from model.page_edits import Highlight, Redaction, TextBox

_TEXTBOX_DEFAULT = (200.0, 56.0)  # starting size for a new box, in page points (then auto-grows)
_MIN_BOX_W, _MIN_BOX_H = 40.0, 20.0  # a text box never shrinks below this (page points)
_MIN_REDACT = 3.0                 # ignore a redaction drag smaller than this (a stray click)


class _TextBoxEditor(QPlainTextEdit):
    committed = Signal()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.committed.emit()


class AnnotationOverlay:
    def __init__(self, view, on_add, on_remove=None, on_replace=None) -> None:
        self._view = view
        self._on_add = on_add               # on_add(page_index, annotation) — pushes Add command
        self._on_remove = on_remove         # on_remove(page_index, annotation)
        self._on_replace = on_replace       # on_replace(page_index, old, new) — move / re-edit
        self._items: list[QGraphicsRectItem] = []
        # Inline editor (placing a new box, or re-editing an existing one).
        self._editor: _TextBoxEditor | None = None
        self._editor_page = 0
        self._editor_rect: tuple = (0, 0, 0, 0)
        self._editing: tuple | None = None  # (page_index, existing TextBox) when re-editing, else None
        # Live redaction rubber-band: a scene rect item dragged out over a page.
        self._redact_band: QGraphicsRectItem | None = None
        self._redact_page = 0
        self._redact_anchor = None          # the scene point where the drag started
        self._editor_fontsize = 11.0        # the box font the editor mirrors (WYSIWYG sizing)
        # Live text-box move: a ghost rect following the drag.
        self._move_ghost: QGraphicsRectItem | None = None
        self._move_page = 0
        self._move_box: TextBox | None = None
        self._move_anchor_local = None      # grab point in unrotated page coords (rotation-safe)
        self._move_rect: tuple = (0, 0, 0, 0)

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
                elif isinstance(annot, TextBox):
                    self._paint_textbox(scene, page_index, annot)
                elif isinstance(annot, Redaction):
                    self._paint_redaction(scene, page_index, annot)

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
        # The font uses pixelSize == fontsize (page points); the transform's zoom scales it to the
        # same on-screen size as the rendered/saved box, so text never spills the box.
        x0, y0, x1, y1 = annot.rect
        transform = self._view.page_transform(page_index)
        box = QGraphicsRectItem(QRectF(x0, y0, x1 - x0, y1 - y0))
        box.setTransform(transform)
        box.setBrush(QColor(255, 255, 250, 235))
        box.setPen(QPen(QColor(150, 150, 150)))
        box.setZValue(7)
        scene.addItem(box)
        self._items.append(box)
        text = QGraphicsSimpleTextItem(annot.text)
        font = QFont()
        font.setPixelSize(max(1, round(annot.fontsize)))
        text.setFont(font)
        text.setBrush(QColor.fromRgbF(*annot.color))
        text_transform = QTransform(transform)
        text_transform.translate(x0 + 2, y0 + 1)  # small inset, in unrotated page points
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
            boxes = annot.rects if hasattr(annot, "rects") else (annot.rect,)
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
        self._open_editor(page_index, box.rect, text=box.text, fontsize=box.fontsize)
        self._editor.selectAll()
        return True

    def _open_editor(self, page_index: int, rect: tuple, text: str, fontsize: float = 11.0) -> None:
        self._close_editor()
        self._editor_page = page_index
        self._editor_rect = rect
        self._editor_fontsize = fontsize
        editor = _TextBoxEditor(self._view.viewport())
        editor.setPlaceholderText("Note…")
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        if text:
            editor.setPlainText(text)
        editor.committed.connect(self._commit_textbox)
        editor.textChanged.connect(self.reposition_editor)
        self._editor = editor
        self.reposition_editor()  # size to initial content + place
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
        font = editor.font()
        font.setPixelSize(max(1, round(self._editor_fontsize * z)))
        editor.setFont(font)
        pw, ph = self._view._unrotated_size(self._editor_page)
        x0, y0 = self._editor_rect[0], self._editor_rect[1]
        avail_w = max(_MIN_BOX_W, pw - x0)
        avail_h = max(_MIN_BOX_H, ph - y0)
        fm = QFontMetricsF(font)
        pad = 2 * editor.document().documentMargin() + 2 * editor.frameWidth() + fm.averageCharWidth() + 4
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
        h_pt = min(avail_h, max(_MIN_BOX_H, (visual_lines * line_h + pad) / z))
        self._editor_rect = (x0, y0, x0 + min(w_pt, avail_w), y0 + h_pt)
        scene_rect = self._view.scene_rect_for_box(self._editor_page, self._editor_rect)
        top_left = self._view.mapFromScene(scene_rect.topLeft())
        bottom_right = self._view.mapFromScene(scene_rect.bottomRight())
        editor.setGeometry(QRect(top_left, bottom_right))

    def _commit_textbox(self) -> None:
        if self._editor is None:
            return
        text = self._editor.toPlainText().strip()
        page_index, rect = self._editor_page, self._editor_rect
        editing, self._editing = self._editing, None
        self._close_editor()
        if editing is not None:
            _, old = editing
            if not text:
                self.remove(page_index, old)  # emptied → delete the box
            elif text != old.text:
                # New rect too (the editor auto-grew to fit the new text). A no-op re-open that
                # leaves the text unchanged commits nothing, even if the box auto-sized slightly.
                new = TextBox(rect, text, old.fontsize, old.color, old.fontname)
                self._replace(page_index, old, new, text="Edit text box")
        elif text:
            self._on_add(page_index, TextBox(rect, text, fontsize=self._editor_fontsize))

    def _close_editor(self) -> None:
        if self._editor is not None:
            editor, self._editor = self._editor, None
            editor.hide()
            editor.deleteLater()

    # ---- text-box tool: move (drag an existing box) -----------------------------

    @property
    def moving(self) -> bool:
        return self._move_ghost is not None

    def begin_move(self, scene_pt) -> bool:
        """SELECT-mode press on a text box → start a move. Returns True if it grabbed a box."""
        if self._view.rotation != 0:
            return False
        hit = self.textbox_at(scene_pt)
        if hit is None:
            return False
        self._move_page, self._move_box = hit
        # Grab point in the page's own (unrotated) coords, so the move delta is computed in the same
        # frame as the box rect — on a rotated page a raw scene delta would swap axes (drag down →
        # box left). The ghost rides the page transform so it rotates with the page.
        self._move_anchor_local = self._view.local_point_on_page(self._move_page, scene_pt)
        self._move_rect = self._move_box.rect
        x0, y0, x1, y1 = self._move_rect
        ghost = QGraphicsRectItem(QRectF(x0, y0, x1 - x0, y1 - y0))
        ghost.setTransform(self._view.page_transform(self._move_page))
        ghost.setBrush(QColor(0, 120, 215, 40))
        ghost.setPen(QPen(QColor(0, 120, 215), 1, Qt.PenStyle.DashLine))
        ghost.setZValue(12)
        self._view.scene().addItem(ghost)
        self._move_ghost = ghost
        return True

    def update_move(self, scene_pt) -> None:
        if self._move_ghost is None:
            return
        local = self._view.local_point_on_page(self._move_page, scene_pt)
        dx = local.x() - self._move_anchor_local.x()
        dy = local.y() - self._move_anchor_local.y()
        x0, y0, x1, y1 = self._move_box.rect
        w, h = x1 - x0, y1 - y0
        pw, ph = self._view._unrotated_size(self._move_page)
        nx0 = min(max(0.0, x0 + dx), max(0.0, pw - w))   # clamp so the box stays on the page
        ny0 = min(max(0.0, y0 + dy), max(0.0, ph - h))
        self._move_rect = (nx0, ny0, nx0 + w, ny0 + h)
        self._move_ghost.setRect(QRectF(nx0, ny0, w, h))  # ghost transform already rotates it

    def finish_move(self) -> None:
        if self._move_ghost is None:
            return
        ghost, self._move_ghost = self._move_ghost, None
        self._view.scene().removeItem(ghost)
        page_index, old, new_rect = self._move_page, self._move_box, self._move_rect
        self._move_box = self._move_anchor_local = None
        if old is not None and new_rect != old.rect:
            new = TextBox(new_rect, old.text, old.fontsize, old.color, old.fontname)
            self._replace(page_index, old, new, text="Move text box")

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
