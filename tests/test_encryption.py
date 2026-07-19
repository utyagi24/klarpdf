"""Document encryption — set / change / remove / carry-through (PLAN.md, M54 ⭐).

One save-path capability, four verbs, AES-256 only (real cryptography, user password) with
optional advisory restriction flags. Cross-engine verification is an **independent PyMuPDF
reopen** (pypdf can't do AES without a dev-only ``cryptography`` extra — PLAN.md's NB); the
M32 carry-through/open tests live in ``test_encrypted.py``. Passwords are memory-only: the
model holds them until a save writes the encrypted output.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtGui import QUndoStack

from app import PdfApp
from main_window import MainWindow
from model.edit_commands import SetEncryptionCommand
from model.edit_engine import PyMuPDFEngine, PyPdfEngine
from model.virtual_document import VirtualDocument
from store.settings import Settings
from tests.conftest import A_TEXT
from ui.encrypt_dialog import PasswordDialog


def _materialized(vdoc, tmp_path, name="out.pdf") -> str:
    out = str(tmp_path / name)
    PyMuPDFEngine().materialize(vdoc, out)
    return out


# ---- set: save → reopen round-trips under the password -----------------------


def test_set_password_round_trips_on_both_open_paths(a_pdf, tmp_path):
    v = VirtualDocument.from_path(a_pdf)
    v.set_encryption("pw-A")
    out = _materialized(v, tmp_path)

    # Open path 1: the independent PyMuPDF reopen (the cross-engine check).
    doc = fitz.open(out)
    try:
        assert doc.needs_pass and not doc.authenticate("wrong")
        assert doc.authenticate("pw-A")
        assert A_TEXT[0] in doc[0].get_text()
    finally:
        doc.close()

    # Open path 2: our own model open via the password provider (what the GUI prompt drives).
    reopened = VirtualDocument.from_path(out, password_provider=lambda p, retry: "pw-A")
    assert reopened.page_count == 3
    assert reopened.password == "pw-A"            # and the carry-through is armed again


def test_set_password_keeps_edits_metadata_and_outline(a_pdf, tmp_path):
    v = VirtualDocument.from_path(a_pdf)
    v.set_rotation(0, 90)
    values = v.effective_metadata()
    values["title"] = "Locked Title"
    v.set_metadata_override(values)
    v.set_encryption("pw-B")
    out = _materialized(v, tmp_path)
    doc = fitz.open(out)
    try:
        assert doc.authenticate("pw-B")
        assert doc[0].rotation == 90
        assert doc.metadata["title"] == "Locked Title"
        assert [e[1] for e in doc.get_toc()] == ["Chapter 1", "Section 1.1", "Chapter 2"]
    finally:
        doc.close()


# ---- change / remove ---------------------------------------------------------


def test_change_password_invalidates_the_old_one(a_pdf, tmp_path):
    v = VirtualDocument.from_path(a_pdf)
    v.set_encryption("old-pw")
    first = _materialized(v, tmp_path, "first.pdf")
    reopened = VirtualDocument.from_path(first, password_provider=lambda p, r: "old-pw")
    reopened.set_encryption("new-pw")             # Change
    second = _materialized(reopened, tmp_path, "second.pdf")
    doc = fitz.open(second)
    try:
        assert not doc.authenticate("old-pw")     # the old password is dead
        assert doc.authenticate("new-pw")
    finally:
        doc.close()


def test_remove_password_saves_unencrypted(a_pdf, tmp_path):
    v = VirtualDocument.from_path(a_pdf)
    v.set_encryption("pw-C", permissions=fitz.PDF_PERM_ACCESSIBILITY)
    v.set_encryption(None)                        # Remove
    assert v.permissions == -1                    # flags die with the password
    out = _materialized(v, tmp_path)
    with fitz.open(out) as doc:
        assert not doc.needs_pass


# ---- advisory restriction flags ----------------------------------------------


def test_restriction_flags_are_written_and_read_back(a_pdf, tmp_path):
    v = VirtualDocument.from_path(a_pdf)
    no_print = fitz.PDF_PERM_ACCESSIBILITY | fitz.PDF_PERM_COPY
    v.set_encryption("pw-D", permissions=no_print)
    out = _materialized(v, tmp_path)
    doc = fitz.open(out)
    try:
        assert doc.authenticate("pw-D") == 2      # user authentication, not owner —
        # a shared owner password would authenticate readers as owner and void the flags.
        assert not doc.permissions & fitz.PDF_PERM_PRINT
        assert doc.permissions & fitz.PDF_PERM_COPY
        assert doc.permissions & fitz.PDF_PERM_ACCESSIBILITY
    finally:
        doc.close()


def test_no_restrictions_means_full_permissions(a_pdf, tmp_path):
    v = VirtualDocument.from_path(a_pdf)
    v.set_encryption("pw-E")                      # default: everything allowed
    out = _materialized(v, tmp_path)
    doc = fitz.open(out)
    try:
        assert doc.authenticate("pw-E")
        assert doc.permissions & fitz.PDF_PERM_PRINT and doc.permissions & fitz.PDF_PERM_MODIFY
    finally:
        doc.close()


# ---- model state -------------------------------------------------------------


def test_encryption_rides_the_undo_stack(a_pdf):
    v = VirtualDocument.from_path(a_pdf)
    stack = QUndoStack()
    stack.push(SetEncryptionCommand(v, "pw-1"))
    assert stack.undoText() == "Set password" and v.password == "pw-1" and v.dirty
    stack.push(SetEncryptionCommand(v, "pw-2"))
    assert stack.undoText() == "Change password"
    stack.push(SetEncryptionCommand(v, None))
    assert stack.undoText() == "Remove password" and v.password is None
    stack.undo()
    assert v.password == "pw-2"
    stack.undo()
    stack.undo()
    assert v.password is None                     # back to the unprotected origin


def test_pypdf_fallback_refuses_an_encrypted_save(a_pdf, tmp_path):
    """pypdf can't write AES without the dev-only cryptography extra — a weaker cipher or a
    silent unencrypted write would betray the password promise, so the fallback refuses."""
    v = VirtualDocument.from_path(a_pdf)
    v.set_encryption("pw-F")
    with pytest.raises(NotImplementedError):
        PyPdfEngine().materialize(v, str(tmp_path / "nope.pdf"))


# ---- the dialog --------------------------------------------------------------


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    return qapp


def test_set_mode_requires_matching_passwords_twice(qapp, a_pdf):
    v = VirtualDocument.from_path(a_pdf)
    dialog = PasswordDialog(v)
    dialog._new.setText("abc")
    dialog._confirm.setText("abd")
    dialog._validate_and_accept()
    assert dialog.staged() is None                # mismatch → stays open, nothing staged
    assert "do not match" in dialog._error.text()
    dialog._confirm.setText("abc")
    dialog._validate_and_accept()
    assert dialog.staged() == ("abc", -1)


def test_set_mode_rejects_an_empty_password(qapp, a_pdf):
    dialog = PasswordDialog(VirtualDocument.from_path(a_pdf))
    dialog._validate_and_accept()
    assert dialog.staged() is None and "Enter a password" in dialog._error.text()


def test_flags_uncheck_stages_restricted_permissions(qapp, a_pdf):
    dialog = PasswordDialog(VirtualDocument.from_path(a_pdf))
    dialog._new.setText("pw")
    dialog._confirm.setText("pw")
    dialog._flags[0].setChecked(False)            # disallow printing
    dialog._validate_and_accept()
    password, permissions = dialog.staged()
    assert password == "pw"
    assert not permissions & fitz.PDF_PERM_PRINT
    assert permissions & fitz.PDF_PERM_COPY and permissions & fitz.PDF_PERM_ACCESSIBILITY


def test_remove_requires_the_current_password(qapp, a_pdf):
    v = VirtualDocument.from_path(a_pdf)
    v.set_encryption("right-pw")
    dialog = PasswordDialog(v)
    dialog._remove.setChecked(True)
    dialog._current.setText("wrong-pw")
    dialog._validate_and_accept()
    assert dialog.staged() is None                # the Done-when: Remove needs the current password
    assert "not correct" in dialog._error.text()
    dialog._current.setText("right-pw")
    dialog._validate_and_accept()
    assert dialog.staged() == (None, -1)


def test_change_requires_the_current_password_too(qapp, a_pdf):
    v = VirtualDocument.from_path(a_pdf)
    v.set_encryption("right-pw")
    dialog = PasswordDialog(v)
    dialog._current.setText("right-pw")
    dialog._new.setText("next")
    dialog._confirm.setText("next")
    dialog._validate_and_accept()
    assert dialog.staged() == ("next", -1)


# ---- the menu wiring + the full GUI save loop --------------------------------


def test_password_action_pushes_one_undoable_command(app, a_pdf, monkeypatch):
    win = MainWindow(app, a_pdf, app.settings)
    import ui.encrypt_dialog as ed

    monkeypatch.setattr(ed.PasswordDialog, "exec", lambda self: 1)
    monkeypatch.setattr(ed.PasswordDialog, "staged", lambda self: ("ui-pw", -1))
    win._password_protection()
    assert win.vdoc.password == "ui-pw"
    assert not win.undo_stack.isClean()
    win.undo_stack.undo()
    assert win.vdoc.password is None


def test_gui_save_writes_encrypted_and_reopens_via_prompt(app, a_pdf, monkeypatch):
    import main_window as mw

    win = MainWindow(app, a_pdf, app.settings)
    win.vdoc.set_encryption("gui-pw")
    win.undo_stack.push(SetEncryptionCommand(win.vdoc, "gui-pw"))
    assert win.save()                             # in-place save carries the encryption
    with fitz.open(a_pdf) as doc:
        assert doc.needs_pass                     # the on-disk file is protected now

    monkeypatch.setattr(mw, "_ask_pdf_password", lambda path, retry: "gui-pw")
    win2 = MainWindow(app, a_pdf, app.settings)   # the second open path: the GUI prompt
    assert win2.vdoc.page_count == 3
    assert win2.vdoc.password == "gui-pw"         # carry-through armed for the next save
