"""A file that cannot be opened says so, and a launch that opens nothing exits (M149). Offscreen GUI.

Two halves of one story, filed as [#332](https://github.com/utyagi24/klarpdf/issues/332) and
[#374](https://github.com/utyagi24/klarpdf/issues/374):

* ``PdfApp.open_document`` caught only ``PasswordRequired``, so a 0-byte, damaged, vanished or
  unreadable file let ``pymupdf.EmptyFileError`` & co. out — a traceback on a console the reader
  never sees, and at a **cold start** the whole launch died before a window existed;
* ``launcher.main`` then ran ``app.exec()`` whatever came back. Qt quits when the *last window
  closes*, so with none ever opened there was nothing to close: the process stayed alive, invisible,
  as the resident instance holding the mutex the Inno installer reads.

The reason wording is asserted through :func:`app.unopenable_reason` — a plain function, so the
words are pinned without a dialog — and the dialog itself is asserted by capturing
``QMessageBox.warning`` (``conftest``'s ``_no_real_modals`` makes a real one a failure).
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

import app as app_module
from app import PdfApp, unopenable_reason
from store.settings import Settings


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp._windows.clear()
    yield qapp
    for w in list(qapp._windows.values()):
        w.undo_stack.setClean()
        w.close()
    qapp._windows.clear()


@pytest.fixture
def warnings(monkeypatch) -> list[tuple[str, str]]:
    """Capture the error dialog instead of showing it: [(title, text), …].

    Overrides ``_no_real_modals``' denial for this test only — that fixture ran first, so this
    ``setattr`` is the one in force (monkeypatch undoes in reverse).
    """
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda parent, title, text, *a, **k: seen.append((title, text))),
    )
    return seen


@pytest.fixture
def empty_file(tmp_path) -> str:
    path = tmp_path / "empty.pdf"
    path.write_bytes(b"")
    return str(path)


@pytest.fixture
def not_a_pdf(tmp_path) -> str:
    path = tmp_path / "notes.pdf"
    path.write_bytes(b"Dear Bob,\nthis is a text file someone renamed.\n")
    return str(path)


@pytest.fixture
def truncated(tmp_path) -> str:
    """Half a real PDF — MuPDF will not repair a stream it cannot find a trailer in."""
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "HALF", fontsize=14)
    data = doc.tobytes()
    doc.close()
    path = tmp_path / "half.pdf"
    path.write_bytes(data[: len(data) // 2])
    return str(path)


@pytest.fixture
def good_pdf(tmp_path) -> str:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "REAL", fontsize=14)
    path = tmp_path / "real.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


# ---- the reason, in words -------------------------------------------------------

@pytest.mark.parametrize(
    "fixture_name, expected",
    [
        ("empty_file", "The file is empty — there is nothing in it to show."),
        ("not_a_pdf", "It is damaged, or it is not a PDF."),
        ("truncated", "It is damaged, or it is not a PDF."),
    ],
)
def test_the_reason_names_what_is_wrong_with_the_file(request, fixture_name, expected):
    """Raised by the real open path, not by a hand-made exception — the point is that these three
    failures reach us as three different PyMuPDF errors and each gets its own sentence."""
    from klarpdf.model.virtual_document import VirtualDocument

    path = request.getfixturevalue(fixture_name)
    with pytest.raises((OSError, RuntimeError)) as caught:
        VirtualDocument.from_path(path)
    assert unopenable_reason(caught.value) == expected


def test_a_vanished_file_says_so(tmp_path):
    from klarpdf.model.virtual_document import VirtualDocument

    with pytest.raises(OSError) as caught:
        VirtualDocument.from_path(str(tmp_path / "gone.pdf"))
    assert unopenable_reason(caught.value) == "The file is no longer there."


def test_an_unrecognised_cause_still_reaches_the_reader_as_words():
    """The fallback arm. A cause nobody anticipated must produce a sentence, never an empty dialog."""
    assert unopenable_reason(RuntimeError("something new went wrong")) == "something new went wrong"
    assert unopenable_reason(RuntimeError()) == "RuntimeError"


# ---- the dialog, and the app staying up -----------------------------------------

def test_a_zero_byte_file_reports_and_opens_no_window(app, warnings, empty_file):
    assert app.open_document(empty_file) is None
    assert len(warnings) == 1
    title, text = warnings[0]
    assert title == "Can't open document"
    assert "empty.pdf" in text                      # names the file, not just the failure
    assert "The file is empty" in text
    assert app._windows == {}                       # nothing registered...
    assert QApplication.topLevelWidgets() == []     # ...and no half-built window left alive


def test_a_damaged_file_reports_and_the_app_stays_up(app, warnings, not_a_pdf, good_pdf):
    assert app.open_document(not_a_pdf) is None
    assert len(warnings) == 1
    win = app.open_document(good_pdf)               # still usable afterwards
    assert win is not None
    assert len(warnings) == 1


def test_an_unopenable_file_does_not_enter_the_recent_list(app, warnings, empty_file):
    app.open_document(empty_file)
    assert app.settings.recent_files() == []        # only a document we actually opened is recorded


def test_a_bug_in_our_own_code_is_not_reported_as_a_broken_document(app, warnings, good_pdf,
                                                                    monkeypatch):
    """The catch is ``(OSError, RuntimeError)`` on purpose. A ``TypeError`` from constructing the
    window is a defect in KlarPDF and must keep crashing loudly, not be shown to the reader as
    "this file can't be opened"."""
    import main_window

    def broken(*args, **kwargs):
        raise TypeError("MainWindow got an unexpected keyword argument")

    monkeypatch.setattr(main_window, "MainWindow", broken)
    with pytest.raises(TypeError):
        app.open_document(good_pdf)
    assert warnings == []


# ---- the launch that opens nothing ----------------------------------------------

def _run_launcher(monkeypatch, qapp, path: str) -> tuple[int, list[str]]:
    """Drive ``launcher.main`` to just before the event loop, and report whether it entered it.

    Every step before the open is stubbed the way ``test_single_instance`` does it: one
    ``QApplication`` for the whole session, no mutex, no hand-off, and this process "wins" the
    server. ``exec`` is replaced rather than allowed to run — offscreen, a real event loop with no
    window in it is precisely the hang #374 describes.
    """
    import launcher

    entered: list[str] = []
    monkeypatch.setattr(launcher, "PdfApp", lambda argv: qapp)
    monkeypatch.setattr(launcher, "acquire_app_mutex", lambda: None)
    monkeypatch.setattr(launcher, "send_path_to_running_instance", lambda *a, **k: False)
    monkeypatch.setattr(launcher, "silence_mupdf_console_noise", lambda: None)
    monkeypatch.setattr(qapp, "start_server", lambda name: True)
    monkeypatch.setattr(qapp, "exec", lambda: entered.append("exec") or 0)
    return launcher.main(["klarpdf", path]), entered


def test_the_launcher_exits_when_the_file_cannot_be_opened(app, warnings, monkeypatch, empty_file):
    rc, entered = _run_launcher(monkeypatch, app, empty_file)
    assert rc == 0
    assert entered == []                            # never reached the event loop
    assert len(warnings) == 1                       # and the reader was told why
    assert app._windows == {}


def test_the_launcher_exits_when_the_password_prompt_is_cancelled(app, monkeypatch, tmp_path):
    """#374 as reported: a protected file, Cancel at the prompt, and nothing left running."""
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "SECRET", fontsize=14)
    locked = tmp_path / "locked.pdf"
    doc.save(str(locked), encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
    doc.close()
    import main_window

    monkeypatch.setattr(main_window, "_ask_pdf_password", lambda path, retry=False: None)
    rc, entered = _run_launcher(monkeypatch, app, str(locked))
    assert rc == 0
    assert entered == []
    assert QApplication.topLevelWidgets() == []


def test_the_launcher_does_run_the_event_loop_for_a_document_that_opens(app, monkeypatch, good_pdf):
    """The positive control. Without this, a ``return 0`` that fired unconditionally would pass the
    two tests above — they would be checking nothing."""
    rc, entered = _run_launcher(monkeypatch, app, good_pdf)
    assert entered == ["exec"]
    assert rc == 0
    assert len(app._windows) == 1


def test_open_document_is_documented_as_returning_none(app):
    """``launcher.main`` relies on the ``None``; a future edit that stops returning it would
    reintroduce #374 silently. Pin the contract where the caller reads it."""
    doc = (app_module.PdfApp.open_document.__doc__ or "")
    assert "None" in doc and "no window opened" in doc
