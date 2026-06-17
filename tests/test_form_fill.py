"""Form-fill model + materialise (PLAN.md, M14 — page-edit layer, first consumer).

Headless: AcroForm field values are document-level state on the VirtualDocument, restored by
undo/redo via the snapshot, and written onto the output at materialise — the shared read-only
sources are never touched.
"""

from __future__ import annotations

import os

import pymupdf as fitz
import pytest
from PySide6.QtGui import QUndoStack

from model.edit_commands import SetFieldValueCommand
from model.edit_engine import PyMuPDFEngine
from model.page_edits import apply_form_values, read_form_fields
from model.virtual_document import VirtualDocument


def _add_widget(page, name, wtype, rect, value=None, choices=None):
    w = fitz.Widget()
    w.field_name = name
    w.field_type = wtype
    w.rect = fitz.Rect(*rect)
    if choices is not None:
        w.choice_values = choices
    if value is not None:
        w.field_value = value
    page.add_widget(w)


@pytest.fixture
def form_pdf(tmp_path) -> str:
    """2-page form: page 0 has a text field + a dropdown; page 1 has a checkbox."""
    path = str(tmp_path / "form.pdf")
    doc = fitz.open()
    p0 = doc.new_page()
    _add_widget(p0, "fullname", fitz.PDF_WIDGET_TYPE_TEXT, (72, 72, 272, 92), value="")
    _add_widget(p0, "color", fitz.PDF_WIDGET_TYPE_COMBOBOX, (72, 110, 272, 130),
                value="Red", choices=["Red", "Green", "Blue"])
    p1 = doc.new_page()
    _add_widget(p1, "agree", fitz.PDF_WIDGET_TYPE_CHECKBOX, (72, 72, 92, 92))
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def vdoc(form_pdf) -> VirtualDocument:
    return VirtualDocument.from_path(form_pdf)


def _materialize(vdoc, tmp_path) -> str:
    out = str(tmp_path / "out.pdf")
    PyMuPDFEngine().materialize(vdoc, out)
    return out


def _values(out_path) -> dict[str, object]:
    with fitz.open(out_path) as doc:
        return {w.field_name: w.field_value for page in doc for w in (page.widgets() or [])}


def test_read_form_fields(vdoc):
    fields = {f.name: f for f in read_form_fields(vdoc)}
    assert set(fields) == {"fullname", "color", "agree"}
    assert fields["fullname"].type == fitz.PDF_WIDGET_TYPE_TEXT
    assert fields["color"].choices == ("Red", "Green", "Blue")
    assert fields["fullname"].page_index == 0 and fields["agree"].page_index == 1
    assert fields["fullname"].rect[0] == pytest.approx(72)


def test_read_form_fields_tracks_page_reorder(vdoc):
    vdoc.move_pages([1], 0)  # move the checkbox page to the front
    fields = {f.name: f for f in read_form_fields(vdoc)}
    assert fields["agree"].page_index == 0  # follows the live page order


def test_fill_text_persists_through_materialize(vdoc, tmp_path):
    vdoc.set_field_value("fullname", "Jane Doe")
    assert _values(_materialize(vdoc, tmp_path))["fullname"] == "Jane Doe"


def test_fill_checkbox_and_dropdown(vdoc, tmp_path):
    vdoc.set_field_value("agree", True)
    vdoc.set_field_value("color", "Blue")
    out = _values(_materialize(vdoc, tmp_path))
    assert out["agree"] == "Yes"  # checkbox on-state
    assert out["color"] == "Blue"


def test_unfilled_fields_keep_their_defaults(vdoc, tmp_path):
    vdoc.set_field_value("fullname", "Only Name")
    out = _values(_materialize(vdoc, tmp_path))
    assert out["color"] == "Red"  # untouched default preserved
    assert out["agree"] == "Off"


def test_set_field_value_marks_dirty(vdoc):
    assert vdoc.dirty is False
    vdoc.set_field_value("fullname", "x")
    assert vdoc.dirty is True


def test_clearing_value_removes_it(vdoc):
    vdoc.set_field_value("fullname", "x")
    vdoc.set_field_value("fullname", None)
    assert "fullname" not in vdoc.form_values


def test_undo_redo_restores_fills(vdoc):
    stack = QUndoStack()
    stack.push(SetFieldValueCommand(vdoc, "fullname", "Jane"))
    assert vdoc.field_value("fullname") == "Jane"
    stack.undo()
    assert vdoc.field_value("fullname") is None
    stack.redo()
    assert vdoc.field_value("fullname") == "Jane"


def test_value_for_deleted_fields_page_is_skipped(vdoc, tmp_path):
    vdoc.set_field_value("agree", True)  # agree is on page 1
    vdoc.delete_page(1)                   # delete that page
    out = _values(_materialize(vdoc, tmp_path))
    assert "agree" not in out             # gone, and no error raised


def test_snapshot_roundtrips_form_values(vdoc):
    vdoc.set_field_value("fullname", "snap")
    snap = vdoc.snapshot()
    vdoc.set_field_value("fullname", "changed")
    vdoc.restore(snap)
    assert vdoc.field_value("fullname") == "snap"


def test_repeated_materialize_keeps_fields(vdoc, tmp_path):
    """Regression (Issue 2): a second save from the same document must NOT strip form fields.

    Reusing one fitz source across insert_pdf calls dropped widgets after the first; materialize
    now copies each source fresh.
    """
    vdoc.set_field_value("fullname", "TWICE")
    outs = [str(tmp_path / "a.pdf"), str(tmp_path / "b.pdf")]
    for out in outs:
        PyMuPDFEngine().materialize(vdoc, out)
    for out in outs:
        with fitz.open(out) as doc:
            widgets = [w for p in doc for w in (p.widgets() or [])]
            assert widgets, f"{out} lost all form fields"
            assert any(w.field_name == "fullname" and w.field_value == "TWICE" for w in widgets)


def test_clear_prefilled_field_through_materialize(tmp_path):
    """Regression (Issue One): an already-filled field must be clearable to empty.

    PyMuPDF ignores field_value='' so a reopened saved file's fields couldn't be cleared (only
    overwritten with spaces); apply_form_values now resets them via the xref.
    """
    path = str(tmp_path / "prefilled.pdf")
    doc = fitz.open()
    page = doc.new_page()
    _add_widget(page, "t", fitz.PDF_WIDGET_TYPE_TEXT, (72, 72, 272, 92), value="PREFILLED")
    doc.save(path)
    doc.close()

    vd = VirtualDocument.from_path(path)
    assert read_form_fields(vd)[0].current_value == "PREFILLED"  # starts filled
    vd.set_field_value("t", "")  # user clears it
    out = str(tmp_path / "cleared.pdf")
    PyMuPDFEngine().materialize(vd, out)
    with fitz.open(out) as result:
        vals = {w.field_name: w.field_value for p in result for w in (p.widgets() or [])}
    assert vals["t"] == ""  # actually empty, not "PREFILLED" and not a space


def test_inplace_save_not_blocked_by_file_lock(form_pdf):
    """Regression (Issue 1): the open document must not lock its file, so in-place Save's atomic
    os.replace succeeds on Windows (PermissionError otherwise)."""
    vd = VirtualDocument.from_path(form_pdf)  # opened from memory → no file handle held
    vd.set_field_value("fullname", "LOCKTEST")
    tmp = form_pdf + ".tmp"
    PyMuPDFEngine().materialize(vd, tmp)
    os.replace(tmp, form_pdf)  # would raise PermissionError if the file were locked open
    with fitz.open(form_pdf) as doc:
        vals = {w.field_name: w.field_value for p in doc for w in (p.widgets() or [])}
    assert vals["fullname"] == "LOCKTEST"
