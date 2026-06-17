"""Inline form-field filling in the viewer (PLAN.md, M14 — PR-B).

Click a fillable AcroForm field on the page and edit it in place:

* **text** → a ``QLineEdit`` over the field; Enter / focus-out commits;
* **combo / list** → a ``QComboBox`` of the field's choices;
* **checkbox** → a single click toggles it (no editor).

Committing calls back into the MainWindow, which pushes a
:class:`~model.edit_commands.SetFieldValueCommand` (so fills are undoable). The value itself is
*displayed* by :class:`~viewer.pdf_view.PdfView`, which re-renders an edited page from a filled
copy — this controller only drives editing and paints faint highlights so fillable fields are
discoverable. Rotation-0 only, like the other overlays (the geometry helpers it uses are).
"""

from __future__ import annotations

import pymupdf as fitz
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QComboBox, QGraphicsRectItem, QLineEdit

from model.page_edits import read_form_fields

_FIELD_TINT = QColor(70, 130, 180, 40)  # faint steel-blue wash marking a fillable field
_CHOICE_TYPES = {fitz.PDF_WIDGET_TYPE_COMBOBOX, fitz.PDF_WIDGET_TYPE_LISTBOX}
# "Off"/False/empty all read as unchecked; anything else is checked.
_UNCHECKED = {None, "", "Off", "off", False, 0}


class FormFiller:
    def __init__(self, view, on_edit) -> None:
        self._view = view
        self._on_edit = on_edit          # callback(field_name, value) -> pushes the undo command
        self._fields = read_form_fields(view._vdoc)
        self._items: list[QGraphicsRectItem] = []
        self._editor = None              # the live inline QLineEdit/QComboBox, if any

    # ---- field lookup -----------------------------------------------------------

    def _current_value(self, field):
        v = self._view._vdoc.field_value(field.name)
        return field.current_value if v is None else v

    def field_at(self, scene_pt):
        """The fillable field whose box contains ``scene_pt`` (rotation 0), else None."""
        if self._view.rotation != 0:
            return None
        page_index, local = self._view.page_and_local_at(scene_pt)
        if page_index is None:
            return None
        lx, ly = local.x(), local.y()
        for f in self._fields:
            if f.page_index == page_index:
                x0, y0, x1, y1 = f.rect
                if x0 <= lx <= x1 and y0 <= ly <= y1:
                    return f
        return None

    # ---- click handling ---------------------------------------------------------

    def handle_press(self, scene_pt) -> bool:
        """Begin editing the field under the press. Returns True if it consumed the event."""
        field = self.field_at(scene_pt)
        if field is None:
            return False
        self._close_editor()
        if field.type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
            checked = self._current_value(field) not in _UNCHECKED
            self._on_edit(field.name, not checked)  # toggle
        elif field.type in _CHOICE_TYPES:
            self._open_combo(field)
        elif field.type == fitz.PDF_WIDGET_TYPE_TEXT:
            self._open_text(field)
        else:
            return False  # radio etc. — not inline-editable yet; let the view handle the click
        return True

    # ---- inline editors ---------------------------------------------------------

    def _editor_geometry(self, field) -> QRect:
        scene_rect = self._view.scene_rect_for_box(field.page_index, field.rect)
        top_left = self._view.mapFromScene(scene_rect.topLeft())
        bottom_right = self._view.mapFromScene(scene_rect.bottomRight())
        return QRect(top_left, bottom_right)

    def _open_text(self, field) -> None:
        editor = QLineEdit(self._view.viewport())
        value = self._current_value(field)
        editor.setText("" if value is None else str(value))
        editor.setGeometry(self._editor_geometry(field))
        committed = {"done": False}

        def commit():
            if committed["done"]:
                return
            committed["done"] = True
            name, text = field.name, editor.text()
            self._close_editor()
            self._on_edit(name, text)

        editor.editingFinished.connect(commit)  # Enter or focus-out
        self._editor = editor
        editor.show()
        editor.setFocus()
        editor.selectAll()

    def _open_combo(self, field) -> None:
        editor = QComboBox(self._view.viewport())
        choices = list(field.choices or [])
        editor.addItems(choices)
        current = self._current_value(field)
        if current in choices:
            editor.setCurrentIndex(choices.index(current))
        editor.setGeometry(self._editor_geometry(field))

        def commit(index: int):
            name, value = field.name, editor.itemText(index)
            self._close_editor()
            self._on_edit(name, value)

        editor.activated.connect(commit)
        self._editor = editor
        editor.show()
        editor.setFocus()
        editor.showPopup()

    def _close_editor(self) -> None:
        if self._editor is not None:
            self._editor.hide()
            self._editor.deleteLater()
            self._editor = None

    # ---- discoverability highlights ---------------------------------------------

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
        """Refresh field geometry + paint faint wash over each fillable field (rotation 0)."""
        self._fields = read_form_fields(self._view._vdoc)  # page order may have changed
        self._clear_items()
        if self._view.rotation != 0:
            return
        scene = self._view.scene()
        brush = QBrush(_FIELD_TINT)
        for f in self._fields:
            rect = self._view.scene_rect_for_box(f.page_index, f.rect)
            item = QGraphicsRectItem(rect)
            item.setBrush(brush)
            item.setPen(QColor(0, 0, 0, 0))
            item.setZValue(5)  # below selection (10), above the page
            scene.addItem(item)
            self._items.append(item)
