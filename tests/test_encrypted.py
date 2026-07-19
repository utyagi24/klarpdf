"""Encrypted / password-protected PDFs (PLAN.md, M32; carry-through updated by M54).

On open, an encrypted source is authenticated via an injected password provider and stored
**decrypted** in memory, so everything downstream (render / materialise / export) is
password-free. Since M54 the password that opened the document is **carried through**: a save
re-encrypts with it (superseding M32's save-unencrypted deferral — the set/change/remove verbs
live in ``test_encryption.py``). Cancelling the prompt raises ``PasswordRequired`` and the GUI
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


def test_materialize_of_encrypted_carries_the_password_through(encrypted_pdf, tmp_path):
    """M54 supersedes M32's save-unencrypted deferral: the password that opened the document is
    the password the save writes (AES-256), and the content/outline survive the round trip."""
    vd = VirtualDocument.from_path(encrypted_pdf, password_provider=_correct)
    assert vd.password == "secret"                # carry-through seeded at open
    out = str(tmp_path / "out.pdf")
    PyMuPDFEngine().materialize(vd, out)
    doc = fitz.open(out)
    try:
        assert doc.needs_pass                     # the output is protected again…
        assert doc.authenticate("secret")         # …with the same password
        assert _SECRET in doc[0].get_text()       # content survived the decrypt + materialise
        assert doc.get_toc() == [[1, "Cover", 1]]  # outline preserved
    finally:
        doc.close()


def test_reload_tries_the_known_password_before_prompting(encrypted_pdf):
    """Revert / redaction-commit of an encrypted document re-reads the (still-encrypted) file.
    Since M54 the held carry-through password is tried silently first — no re-prompt for a
    password we know."""
    calls = []

    def provider(path, retry):
        calls.append(retry)
        return "secret"

    vd = VirtualDocument.from_path(encrypted_pdf, password_provider=provider)
    before = len(calls)
    vd.reload_from_file(encrypted_pdf)
    assert vd.page_count == 1
    assert len(calls) == before                   # the known password opened it — no prompt
    assert vd.password == "secret"                # carry-through still armed


def test_reload_falls_back_to_the_provider_on_a_changed_password(encrypted_pdf, tmp_path):
    """If an external program re-encrypted the file with a different password, the known one
    fails and the stored provider is consulted (the ordinary retry loop)."""
    other = str(tmp_path / "rekeyed.pdf")
    doc = fitz.open(encrypted_pdf)
    doc.authenticate("secret")
    doc.save(other, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="changed", user_pw="changed")
    doc.close()
    calls = []

    def provider(path, retry):
        calls.append(retry)
        # First open (not a retry) → the original password; the reload's fallback arrives as a
        # retry, after the silently-tried known password failed → the new one.
        return "changed" if retry else "secret"

    vd = VirtualDocument.from_path(encrypted_pdf, password_provider=provider)
    vd.reload_from_file(other)
    assert vd.page_count == 1
    assert calls[-1] is True                      # the fallback ran as a retry after "secret" failed
    assert vd.password == "changed"               # re-baselined to what actually opened it


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
