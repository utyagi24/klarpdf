"""On-screen annotation preview + the text-box placement tool (PLAN.md, M20 — PR-B).

The rendered page pixmap does not include annotations (they only bake in at materialise), so this
controller paints them as scene overlay items — translucent rects for highlights, a bordered box +
text for text-boxes — the same pattern as ``text_selection`` / ``search``. It also drives the
text-box tool: in TEXTBOX mode a click opens an inline editor; committing adds a ``TextBox`` via the
MainWindow callback (so it is undoable). Rotation-0 only, like the other overlays.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPen
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsSimpleTextItem, QPlainTextEdit

from model.page_edits import Highlight, TextBox

_TEXTBOX_DEFAULT = (200.0, 56.0)  # default new-box size in page points


class _TextBoxEditor(QPlainTextEdit):
    committed = Signal()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.committed.emit()


class AnnotationOverlay:
    def __init__(self, view, on_add) -> None:
        self._view = view
        self._on_add = on_add               # on_add(page_index, annotation) — pushes the command
        self._items: list[QGraphicsRectItem] = []
        self._editor: _TextBoxEditor | None = None
        self._editor_page = 0
        self._editor_rect: tuple = (0, 0, 0, 0)

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

    def _paint_textbox(self, scene, page_index: int, annot: TextBox) -> None:
        rect = self._view.scene_rect_for_box(page_index, annot.rect)
        box = QGraphicsRectItem(rect)
        box.setBrush(QColor(255, 255, 250, 235))
        box.setPen(QPen(QColor(150, 150, 150)))
        box.setZValue(7)
        scene.addItem(box)
        self._items.append(box)
        text = QGraphicsSimpleTextItem(annot.text, box)  # child → clipped-ish, moves with the box
        font = QFont()
        font.setPointSizeF(max(1.0, annot.fontsize * self._view.zoom))
        text.setFont(font)
        text.setBrush(QColor.fromRgbF(*annot.color))
        text.setPos(rect.x() + 3, rect.y() + 2)
        text.setZValue(8)
        self._items.append(text)

    # ---- text-box tool ----------------------------------------------------------

    def place_textbox(self, scene_pt) -> bool:
        """TEXTBOX mode: open an inline editor for a new box at ``scene_pt``. Returns True if it
        consumed the click (i.e. the point was on a page)."""
        if self._view.rotation != 0:
            return False
        page_index, local = self._view.page_and_local_at(scene_pt)
        if page_index is None:
            return False
        self._close_editor()
        w, h = _TEXTBOX_DEFAULT
        self._editor_page = page_index
        self._editor_rect = (local.x(), local.y(), local.x() + w, local.y() + h)
        scene_rect = self._view.scene_rect_for_box(page_index, self._editor_rect)
        editor = _TextBoxEditor(self._view.viewport())
        editor.setPlaceholderText("Note…")
        top_left = self._view.mapFromScene(scene_rect.topLeft())
        bottom_right = self._view.mapFromScene(scene_rect.bottomRight())
        editor.setGeometry(QRect(top_left, bottom_right))
        editor.committed.connect(self._commit_textbox)
        self._editor = editor
        editor.show()
        editor.setFocus()
        return True

    def _commit_textbox(self) -> None:
        if self._editor is None:
            return
        text = self._editor.toPlainText().strip()
        page_index, rect = self._editor_page, self._editor_rect
        self._close_editor()
        if text:
            self._on_add(page_index, TextBox(rect, text))

    def _close_editor(self) -> None:
        if self._editor is not None:
            editor, self._editor = self._editor, None
            editor.hide()
            editor.deleteLater()
