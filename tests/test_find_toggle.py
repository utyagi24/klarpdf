"""The toolbar Find button is a toggle that mirrors the find bar (fix/find-toggle). Offscreen GUI.

Find's two toolbar neighbours (the sidebar button, the Markup toggle) both light while their
chrome is up; Find used to stay dark with its bar open, so a second click looked dead. The button
is now checkable and its state is authored from the bar's own ``visibilityChanged`` — so it stays
right no matter which path opened or closed the bar (the button, Ctrl+F, ✕, Esc, a doc change).

The Ctrl+F contract is deliberately *not* the toggle: the shortcut stays on the Edit-menu "Find…"
launcher (show + select-all, never close), so this file also pins that separation.
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QAction, QKeySequence

from app import PdfApp
from store.settings import Settings


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def win(qapp, a_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    w = qapp.open_document(a_pdf)
    w.show()
    qapp.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def test_toggle_starts_unlit_with_the_bar_hidden(win):
    assert win._a_find_toggle.isCheckable()
    assert not win.find_bar.isVisible()
    assert not win._a_find_toggle.isChecked()


def test_clicking_the_toggle_opens_then_closes_the_bar(win):
    win._a_find_toggle.trigger()                 # a user click: unchecked → checked
    assert win.find_bar.isVisible()
    assert win._a_find_toggle.isChecked()
    win._a_find_toggle.trigger()                 # click again: a lit button closes it
    assert not win.find_bar.isVisible()
    assert not win._a_find_toggle.isChecked()


def test_opening_by_shortcut_lights_the_toggle(win):
    win._show_find()                             # the Ctrl+F / Edit-menu launcher
    assert win.find_bar.isVisible()
    assert win._a_find_toggle.isChecked()        # even though the button was not the opener


def test_every_close_path_unlights_the_toggle(win):
    win._show_find()
    assert win._a_find_toggle.isChecked()
    win.find_bar.hide_bar()                      # ✕ and Esc both route here
    assert not win.find_bar.isVisible()
    assert not win._a_find_toggle.isChecked()


def test_shortcut_reopen_while_open_keeps_it_open_and_lit(win):
    """Ctrl+F while the bar is up refocuses — it must never toggle the bar shut (the launcher and
    the toggle are separate actions precisely so the shortcut can't close)."""
    win._show_find()
    win._show_find()                             # second Ctrl+F
    assert win.find_bar.isVisible()
    assert win._a_find_toggle.isChecked()


def test_the_shortcut_stays_on_the_launcher_not_the_toggle(win):
    """The checkable toolbar action carries no shortcut; Ctrl+F lives on the Edit-menu Find…."""
    assert win._a_find_toggle.shortcut().isEmpty()
    launcher = [a for a in win.findChildren(QAction) if a.text() == "Find…"]
    assert launcher and launcher[0].shortcut() == QKeySequence(QKeySequence.StandardKey.Find)
