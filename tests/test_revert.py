"""Headless tests for M23 Revert to Saved: discard in-memory edits and reload the on-disk file,
clearing the undo history + dirty state. No real display (offscreen, set in conftest)."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QMessageBox

from app import PdfApp
from main_window import MainWindow
from store.settings import Settings


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


def _order(win) -> list[int]:
    return [r.source_page_index for r in win.vdoc.ordered]


def test_revert_discards_edits_and_reloads(app, a_pdf, monkeypatch):
    win = _win(app, a_pdf)
    win._delete_rows([1])  # edit: drop page 1
    assert _order(win) == [0, 2]
    assert not win.undo_stack.isClean()

    monkeypatch.setattr(win, "_confirm_revert", lambda: True)
    win.revert()

    assert _order(win) == [0, 1, 2]  # back to the on-disk page set
    assert win.undo_stack.isClean()  # undo history cleared → nothing to undo back into
    assert win.undo_stack.count() == 0
    assert win.vdoc.dirty is False
    assert win.isWindowModified() is False  # title '*' cleared


def test_revert_cancel_keeps_edits(app, a_pdf, monkeypatch):
    win = _win(app, a_pdf)
    win._delete_rows([1])
    monkeypatch.setattr(win, "_confirm_revert", lambda: False)
    win.revert()
    assert _order(win) == [0, 2]  # edit preserved
    assert not win.undo_stack.isClean()


def test_revert_noop_when_clean_does_not_prompt(app, a_pdf, monkeypatch):
    win = _win(app, a_pdf)

    def _boom():
        raise AssertionError("revert must not prompt when there are no unsaved changes")

    monkeypatch.setattr(win, "_confirm_revert", _boom)
    win.revert()  # clean → no-op, no prompt
    assert _order(win) == [0, 1, 2]


def test_revert_action_disabled_until_dirty(app, a_pdf):
    win = _win(app, a_pdf)
    assert win._a_revert.isEnabled() is False  # clean on open
    win._delete_rows([0])
    assert win._a_revert.isEnabled() is True  # dirty → enabled
    win.undo_stack.undo()
    assert win._a_revert.isEnabled() is False  # clean again → disabled


def test_revert_confirm_uses_discard_button(app, a_pdf, monkeypatch):
    """The confirm offers Discard (proceed) / Cancel (default), and only Discard reverts."""
    win = _win(app, a_pdf)
    win._delete_rows([1])
    seen = {}

    def fake_warning(parent, title, text, buttons, default):
        seen["buttons"] = buttons
        seen["default"] = default
        return QMessageBox.StandardButton.Discard

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(fake_warning))
    win.revert()

    assert seen["default"] == QMessageBox.StandardButton.Cancel
    assert seen["buttons"] & QMessageBox.StandardButton.Discard
    assert _order(win) == [0, 1, 2]  # Discard proceeded with the revert
