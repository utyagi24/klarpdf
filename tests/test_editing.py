"""Headless tests for the M4 editing loop: reorder/delete/cut/copy/paste/insert, undo/redo,
save (materialize), and the dirty-close prompt. No real display (offscreen)."""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMessageBox

import main_window as mw
from app import PdfApp
from main_window import MainWindow
from store.settings import Settings
from tests.conftest import A_TEXT, B_TEXT


@pytest.fixture(scope="session")
def qapp():
    app = PdfApp.instance() or PdfApp([])
    yield app


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "view_state.json")
    qapp.page_clipboard = []
    return qapp


def _win(app, path) -> MainWindow:
    return MainWindow(app, path, app.settings)


def _order(win) -> list[int]:
    return [r.source_page_index for r in win.vdoc.ordered]


# ---- reorder / delete + undo/redo -------------------------------------------


def test_reorder_and_undo_redo(app, a_pdf):
    win = _win(app, a_pdf)
    win._reorder([0], 3)  # move page 0 to the end
    assert _order(win) == [1, 2, 0]
    win.undo_stack.undo()
    assert _order(win) == [0, 1, 2]
    win.undo_stack.redo()
    assert _order(win) == [1, 2, 0]


def test_delete_and_undo(app, a_pdf):
    win = _win(app, a_pdf)
    win._delete_rows([1])
    assert _order(win) == [0, 2]
    assert not win.undo_stack.isClean()
    win.undo_stack.undo()
    assert _order(win) == [0, 1, 2]


# ---- copy / cut / paste -----------------------------------------------------


def test_copy_paste_within_document(app, a_pdf):
    win = _win(app, a_pdf)
    win._copy_pages([0])
    win._paste_pages(before_index=3)
    assert win.vdoc.page_count == 4
    assert _order(win) == [0, 1, 2, 0]
    win.undo_stack.undo()
    assert win.vdoc.page_count == 3


def test_cut_pages_removes_and_fills_clipboard(app, a_pdf):
    win = _win(app, a_pdf)
    win._cut_pages([0])
    assert _order(win) == [1, 2]
    assert len(app.page_clipboard) == 1
    win._paste_pages(before_index=2)
    assert _order(win) == [1, 2, 0]


def test_cross_window_copy_paste_then_save(app, a_pdf, b_pdf, tmp_path):
    src = _win(app, b_pdf)
    dst = _win(app, a_pdf)
    src._copy_pages([0])                       # B's page 0
    dst._paste_pages(before_index=dst.vdoc.page_count)
    assert dst.vdoc.page_count == 4
    assert src.vdoc.page_count == 2            # copy leaves the source intact

    out = str(tmp_path / "merged.pdf")
    assert dst._write_to(out) is True
    doc = fitz.open(out)
    try:
        assert doc.page_count == 4
        assert B_TEXT[0] in doc[3].get_text("text")   # B's page came across losslessly
    finally:
        doc.close()


def test_insert_from_file(app, a_pdf, b_pdf, monkeypatch):
    win = _win(app, a_pdf)
    monkeypatch.setattr(mw.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (b_pdf, "")))
    win._insert_from_file()
    assert win.vdoc.page_count == 5  # 3 (A) + 2 (B)
    win.undo_stack.undo()
    assert win.vdoc.page_count == 3


# ---- save / dirty -----------------------------------------------------------


def test_save_writes_edits_and_marks_clean(app, a_pdf, tmp_path):
    win = _win(app, a_pdf)
    win._delete_rows([1])
    out = str(tmp_path / "out.pdf")
    assert win._write_to(out) is True
    assert win.undo_stack.isClean()
    assert win.vdoc.dirty is False
    doc = fitz.open(out)
    try:
        assert doc.page_count == 2
        assert A_TEXT[0] in doc[0].get_text("text")
        assert A_TEXT[2] in doc[1].get_text("text")
    finally:
        doc.close()


# ---- dirty-close prompt -----------------------------------------------------


def test_close_prompt_cancel_aborts(app, a_pdf, monkeypatch):
    win = _win(app, a_pdf)
    win._delete_rows([0])  # make it dirty
    monkeypatch.setattr(win, "_confirm_discard", lambda: QMessageBox.StandardButton.Cancel)
    event = QCloseEvent()
    win.closeEvent(event)
    assert event.isAccepted() is False  # Cancel keeps the window open


def test_close_prompt_discard_closes(app, a_pdf, monkeypatch):
    win = _win(app, a_pdf)
    win._delete_rows([0])
    monkeypatch.setattr(win, "_confirm_discard", lambda: QMessageBox.StandardButton.Discard)
    event = QCloseEvent()
    win.closeEvent(event)
    assert event.isAccepted() is True


def test_close_clean_does_not_prompt(app, a_pdf, monkeypatch):
    win = _win(app, a_pdf)

    def _boom():
        raise AssertionError("should not prompt when there are no unsaved changes")

    monkeypatch.setattr(win, "_confirm_discard", _boom)
    event = QCloseEvent()
    win.closeEvent(event)  # clean → no prompt
    assert event.isAccepted() is True
