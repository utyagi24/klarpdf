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
from PySide6.QtGui import QBrush, QColor, QFontMetricsF, QPen, QTransform
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsSimpleTextItem, QPlainTextEdit

from model.page_edits import Highlight, Redaction, TextBox
from viewer.text_format_bar import TextBoxStyle, TextFormatBar, qt_font

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
            new = replace(old, rect=new_rect)  # preserve text + all styling, just move it
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
