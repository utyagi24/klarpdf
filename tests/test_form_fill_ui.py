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


def test_toolbar_save_commits_open_editor(win):
    """Regression (Issue Two): Save must flush an inline editor that still has focus.

    The toolbar Save button doesn't move focus out of the QLineEdit, so without an explicit
    commit the typed value was lost; commit_pending (called by save()) fixes it.
    """
    win.view.form.handle_press(_center(win, "fullname"))
    win.view.form._editor.setText("TOOLBAR SAVE")
    win.view.form.commit_pending()  # what save()/save_as() invoke before writing
    assert win.vdoc.field_value("fullname") == "TOOLBAR SAVE"


def test_inline_editor_follows_zoom(win):
    """Regression: an open field editor must move/resize with the page on zoom, not be left behind."""
    win.view.form.handle_press(_center(win, "fullname"))
    before = win.view.form._editor.geometry()
    win.view.set_zoom(win.view.zoom * 2)
    assert win.view.form._editor is not None  # still open
    assert win.view.form._editor.geometry() != before  # tracked the zoom


def test_filled_page_renders_from_copy(win):
    win.vdoc.set_field_value("fullname", "Rendered")
    win.view.reload()
    pixmap = win.view._render_pixmap(0)
    assert pixmap is not None and not pixmap.isNull()


def test_fields_are_highlighted(win):
    win.view.form.repaint()
    assert len(win.view.form._items) == 3  # one wash per fillable field


def _interior_dark_pixels(win, name) -> int:
    """Count dark pixels inside a field (inset past its border) in the rendered page pixmap.

    The synthetic form's page is otherwise blank around the fields, so dark interior pixels mean
    the entered value actually rendered.
    """
    f = _field(win, name)
    pm = win.view._render_pixmap(f.page_index)
    img = pm.toImage()
    z = win.view.zoom
    x0, y0, x1, y1 = (int(v * z) for v in f.rect)
    count = 0
    for y in range(y0 + 2, y1 - 2):
        for x in range(x0 + 2, x1 - 2):
            c = img.pixelColor(x, y)
            if (c.red() + c.green() + c.blue()) // 3 < 160:
                count += 1
    return count


def test_repeated_fills_keep_rendering(win):
    """Regression: a filled field must keep rendering after *further* edits.

    Repeated insert_pdf from one source dropped widgets after the first call, so only the first
    fill ever showed; we now render from a fresh value-applied copy instead.
    """
    win._set_field_value("fullname", "ALPHA TEXT")
    win.view.reload()
    assert _interior_dark_pixels(win, "fullname") > 0  # first fill renders

    win._set_field_value("color", "Blue")  # a SECOND edit — used to blank the first field
    win.view.reload()
    assert _interior_dark_pixels(win, "fullname") > 0  # still renders after more edits
