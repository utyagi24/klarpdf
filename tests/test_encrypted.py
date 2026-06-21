"""Encrypted / password-protected PDFs (PLAN.md, M32).

On open, an encrypted source is authenticated via an injected password provider and stored
**decrypted** in memory, so everything downstream (render / materialise / export) is password-free
and the saved output is unencrypted. Cancelling the prompt raises ``PasswordRequired`` and the GUI
simply opens no window. Model tests are headless; two offscreen GUI tests cover the prompt wiring.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

import main_window as mw
from app import PdfApp
from model.edit_engine import PyMuPDFEngine
from model.virtual_document import PasswordRequired, VirtualDocument
from store.settings import Settings

_SECRET = "TOP SECRET payslip"


@pytest.fixture
def encrypted_pdf(tmp_path) -> str:
    path = str(tmp_path / "locked.pdf")
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), _SECRET, fontsize=14)
    doc.set_toc([[1, "Cover", 1]])
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret")
    doc.close()
    return path


def _correct(path, retry):
    return "secret"


# ---- model: authenticate + decrypt-in-memory --------------------------------


def test_open_encrypted_with_correct_password(encrypted_pdf):
    vd = VirtualDocument.from_path(encrypted_pdf, password_provider=_correct)
    assert vd.page_count == 1
    src = vd.sources[vd.origin_source_id]
    assert not src.needs_pass                    # stored decrypted — no further password needed
    assert _SECRET in src[0].get_text()          # content is readable


def test_open_encrypted_retries_after_wrong_password(encrypted_pdf):
    seen = []

    def provider(path, retry):
        seen.append(retry)
        return "nope" if len(seen) == 1 else "secret"

    vd = VirtualDocument.from_path(encrypted_pdf, password_provider=provider)
    assert vd.page_count == 1
    assert seen == [False, True]  # first call is not a retry; the second is, after the wrong pw


def test_open_encrypted_cancel_raises(encrypted_pdf):
    with pytest.raises(PasswordRequired):
        VirtualDocument.from_path(encrypted_pdf, password_provider=lambda path, retry: None)


def test_open_encrypted_without_provider_raises(encrypted_pdf):
    with pytest.raises(PasswordRequired):
        VirtualDocument.from_path(encrypted_pdf)


def test_unencrypted_open_never_consults_provider(a_pdf):
    def boom(path, retry):
        raise AssertionError("provider must not be called for an unencrypted file")

    vd = VirtualDocument.from_path(a_pdf, password_provider=boom)
    assert vd.page_count >= 1


def test_materialize_of_encrypted_is_unencrypted_and_keeps_content(encrypted_pdf, tmp_path):
    vd = VirtualDocument.from_path(encrypted_pdf, password_provider=_correct)
    out = str(tmp_path / "out.pdf")
    PyMuPDFEngine().materialize(vd, out)
    doc = fitz.open(out)
    try:
        assert not doc.needs_pass                 # output is unencrypted (re-encryption is deferred)
        assert _SECRET in doc[0].get_text()       # content survived the decrypt + materialise
        assert doc.get_toc() == [[1, "Cover", 1]]  # outline preserved
    finally:
        doc.close()


def test_reload_reuses_stored_password_provider(encrypted_pdf):
    """Revert of an encrypted original re-reads the on-disk (still-encrypted) file, re-prompting via
    the provider stored at open — so it authenticates again without the caller re-supplying it."""
    calls = []

    def provider(path, retry):
        calls.append(retry)
        return "secret"

    vd = VirtualDocument.from_path(encrypted_pdf, password_provider=provider)
    before = len(calls)
    vd.reload_from_file(encrypted_pdf)
    assert vd.page_count == 1
    assert len(calls) == before + 1


# ---- GUI: the password prompt wiring ----------------------------------------


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    for w in list(qapp._windows.values()):
        w.close()
    qapp._windows.clear()
    yield qapp
    for w in list(qapp._windows.values()):
        w.undo_stack.setClean()
        w.close()
    qapp._windows.clear()


def test_open_document_encrypted_prompts_then_opens(app, encrypted_pdf, monkeypatch):
    monkeypatch.setattr(mw, "_ask_pdf_password", lambda path, retry: "secret")
    win = app.open_document(encrypted_pdf)
    assert win is not None and win.vdoc.page_count == 1


def test_open_document_encrypted_cancel_opens_no_window(app, encrypted_pdf, monkeypatch):
    monkeypatch.setattr(mw, "_ask_pdf_password", lambda path, retry: None)
    before = len(app._windows)
    assert app.open_document(encrypted_pdf) is None  # cancelled → no window
    assert len(app._windows) == before
