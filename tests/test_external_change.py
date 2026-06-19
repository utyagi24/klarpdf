"""Headless tests for M24 external-change detection: FileWatcher signature logic + the MainWindow
overwrite / reload / keep prompts. Offscreen (conftest). The QFileSystemWatcher signal itself is
verified manually on Windows; here we drive the handlers + signature directly (deterministic)."""

from __future__ import annotations

from pathlib import Path

import pymupdf as fitz
import pytest

from app import PdfApp
from main_window import MainWindow
from store.file_watch import FileWatcher
from store.settings import Settings
from tests.conftest import A_TEXT, B_TEXT


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "view_state.json")
    qapp.page_clipboard = []
    return qapp


def _win(app, path) -> MainWindow:
    return MainWindow(app, path, app.settings)


def _external_change(path: str, src_pdf: str) -> None:
    """Replace the file at `path` with a different valid PDF (changes both mtime and size)."""
    Path(path).write_bytes(Path(src_pdf).read_bytes())


def _first_page_text(win) -> str:
    ref = win.vdoc.ordered[0]
    return win.vdoc.sources[ref.source_id][ref.source_page_index].get_text("text")


# ---- FileWatcher signature logic (pure, no async watcher event) -------------


def test_watcher_detects_size_change(tmp_path):
    f = tmp_path / "f.bin"
    f.write_bytes(b"hello")
    w = FileWatcher()
    w.watch(str(f))
    assert w.has_changed() is False
    f.write_bytes(b"hello world")  # larger
    assert w.has_changed() is True


def test_watcher_record_current_resyncs(tmp_path):
    f = tmp_path / "f.bin"
    f.write_bytes(b"hello")
    w = FileWatcher()
    w.watch(str(f))
    f.write_bytes(b"changed!!")
    assert w.has_changed() is True
    w.record_current()
    assert w.has_changed() is False


def test_watcher_detects_removal(tmp_path):
    f = tmp_path / "f.bin"
    f.write_bytes(b"hello")
    w = FileWatcher()
    w.watch(str(f))
    f.unlink()
    assert w.has_changed() is True


# ---- in-place Save when the file changed on disk ----------------------------


def test_save_cancel_does_not_overwrite(app, a_pdf, b_pdf, monkeypatch):
    win = _win(app, a_pdf)
    _external_change(win.path, b_pdf)  # disk now holds B (2 pages)
    monkeypatch.setattr(win, "_confirm_overwrite_external", lambda: "cancel")
    assert win.save() is False
    with fitz.open(win.path) as doc:
        assert doc.page_count == 2  # our save did NOT run; B's content intact on disk


def test_save_overwrite_proceeds(app, a_pdf, b_pdf, monkeypatch):
    win = _win(app, a_pdf)  # 3-page A in memory
    _external_change(win.path, b_pdf)  # disk now holds B (2 pages)
    monkeypatch.setattr(win, "_confirm_overwrite_external", lambda: "overwrite")
    assert win.save() is True
    with fitz.open(win.path) as doc:
        assert doc.page_count == 3  # our 3-page A written over B
        assert A_TEXT[0] in doc[0].get_text("text")
    assert win._watcher.has_changed() is False  # our own write recorded as the synced state


def test_save_reload_supersedes_save(app, a_pdf, b_pdf, monkeypatch):
    win = _win(app, a_pdf)  # 3-page A in memory
    _external_change(win.path, b_pdf)
    monkeypatch.setattr(win, "_confirm_overwrite_external", lambda: "reload")
    assert win.save() is False  # reloaded instead of writing
    assert win.vdoc.page_count == 2  # in-memory now matches disk (B)
    assert B_TEXT[0] in _first_page_text(win)
    assert win.undo_stack.isClean()
    with fitz.open(win.path) as doc:
        assert doc.page_count == 2  # disk still B — our 3-page version was not written


def test_save_no_external_change_does_not_prompt(app, a_pdf, monkeypatch):
    win = _win(app, a_pdf)
    win._delete_rows([1])  # A -> [0, 2]

    def _boom():
        raise AssertionError("must not prompt when the file has not changed on disk")

    monkeypatch.setattr(win, "_confirm_overwrite_external", _boom)
    assert win.save() is True
    assert win._watcher.has_changed() is False  # our own save recorded, not flagged external


# ---- external change surfaced on focus (Reload / Keep) ----------------------


def test_external_reload_replaces_in_memory_doc(app, a_pdf, b_pdf, monkeypatch):
    win = _win(app, a_pdf)
    _external_change(win.path, b_pdf)
    monkeypatch.setattr(win, "_confirm_external_reload", lambda: True)
    win._prompt_external_change()
    assert win.vdoc.page_count == 2  # reloaded from disk (B)
    assert B_TEXT[0] in _first_page_text(win)
    assert win.undo_stack.isClean()
    assert win._watcher.has_changed() is False


def test_external_keep_acknowledges_and_stops_nagging(app, a_pdf, b_pdf, monkeypatch):
    win = _win(app, a_pdf)
    win._delete_rows([0])  # local edit we want to keep
    _external_change(win.path, b_pdf)
    monkeypatch.setattr(win, "_confirm_external_reload", lambda: False)
    win._prompt_external_change()
    assert win.vdoc.page_count == 2  # our edited A ([1, 2]) kept, not B
    assert A_TEXT[1] in _first_page_text(win)
    assert win._watcher.has_changed() is False  # acknowledged → won't prompt again for this change


def test_external_no_change_does_not_prompt(app, a_pdf, monkeypatch):
    win = _win(app, a_pdf)

    def _boom():
        raise AssertionError("must not prompt when the file has not changed on disk")

    monkeypatch.setattr(win, "_confirm_external_reload", _boom)
    win._prompt_external_change()  # no external change → no-op
    assert win.vdoc.page_count == 3
