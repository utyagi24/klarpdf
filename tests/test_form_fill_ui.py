"""Inline form-fill interaction in the viewer (PLAN.md, M14 — PR-B). Offscreen GUI.

Drives FormFiller through a real MainWindow: clicking a field hit-tests it, the right inline editor
appears, committing pushes an undoable SetFieldValueCommand, and an edited page renders from a
filled copy.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QComboBox, QLineEdit

from app import PdfApp
from model.page_edits import read_form_fields
from store.settings import Settings


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


def _build_form(path: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    specs = [
        ("fullname", fitz.PDF_WIDGET_TYPE_TEXT, (72, 72, 272, 92), "", None),
        ("color", fitz.PDF_WIDGET_TYPE_COMBOBOX, (72, 110, 272, 130), "Red", ["Red", "Green", "Blue"]),
        ("agree", fitz.PDF_WIDGET_TYPE_CHECKBOX, (72, 150, 92, 170), None, None),
    ]
    for name, wtype, rect, value, choices in specs:
        w = fitz.Widget()
        w.field_name, w.field_type, w.rect = name, wtype, fitz.Rect(*rect)
        if choices is not None:
            w.choice_values = choices
        if value is not None:
            w.field_value = value
        page.add_widget(w)
    doc.save(path)
    doc.close()


@pytest.fixture
def win(qapp, tmp_path):
    path = str(tmp_path / "form.pdf")
    _build_form(path)
    qapp.settings = Settings(tmp_path / "vs.json")
    w = qapp.open_document(path)
    yield w
    w.undo_stack.setClean()  # avoid the dirty-close QMessageBox blocking headless teardown
    w.close()


def _field(win, name):
    return next(f for f in read_form_fields(win.vdoc) if f.name == name)


def _center(win, name) -> QPointF:
    return win.view.scene_rect_for_box(_field(win, name).page_index, _field(win, name).rect).center()


def test_field_hit_test(win):
    hit = win.view.form.field_at(_center(win, "fullname"))
    assert hit is not None and hit.name == "fullname"


def test_click_off_field_is_not_a_field(win):
    assert win.view.form.field_at(QPointF(5, 2)) is None  # in the gap above page 0


def test_text_field_inline_edit_commits(win):
    assert win.view.form.handle_press(_center(win, "fullname")) is True
    editor = win.view.form._editor
    assert isinstance(editor, QLineEdit)
    editor.setText("Jane Doe")
    editor.editingFinished.emit()
    assert win.vdoc.field_value("fullname") == "Jane Doe"


def test_checkbox_click_toggles(win):
    win.view.form.handle_press(_center(win, "agree"))
    assert win.vdoc.field_value("agree") is True
    win.view.form.handle_press(_center(win, "agree"))
    assert win.vdoc.field_value("agree") is False


def test_dropdown_inline_edit_commits(win):
    assert win.view.form.handle_press(_center(win, "color")) is True
    editor = win.view.form._editor
    assert isinstance(editor, QComboBox)
    editor.activated.emit(editor.findText("Blue"))
    assert win.vdoc.field_value("color") == "Blue"


def test_fill_is_undoable(win):
    win.view.form.handle_press(_center(win, "agree"))
    assert win.vdoc.field_value("agree") is True
    win.undo_stack.undo()
    assert win.vdoc.field_value("agree") is None
    win.undo_stack.redo()
    assert win.vdoc.field_value("agree") is True


def test_filled_page_renders_from_copy(win):
    win.vdoc.set_field_value("fullname", "Rendered")
    win.view.reload()
    pixmap = win.view._render_pixmap(0)
    assert pixmap is not None and not pixmap.isNull()


def test_fields_are_highlighted(win):
    win.view.form.repaint()
    assert len(win.view.form._items) == 3  # one wash per fillable field
