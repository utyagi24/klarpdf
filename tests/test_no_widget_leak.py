"""The suite must not leak widgets between tests. Offscreen GUI.

Guards the `pytest_runtest_teardown` hook in `conftest.py`, which exists because the suite leaked
**every window it ever opened** — ~107,000 live widgets and 8 GiB RSS by the end of a CI run, which
is what was segfaulting the Ubuntu runner at a place and in a test that had nothing to do with the
change under review.

The invariant is stated the only way that is order-independent: **a test starts with no widgets
alive**. The pair below makes it self-contained — the first opens a real window, the second (which
pytest runs next, being later in the same file) asserts the sweep took it away. If someone removes
or breaks the hook, the second test fails immediately rather than the suite dying weeks later on a
runner with a traceback pointing somewhere else entirely.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from app import PdfApp
from store.settings import Settings


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


def test_a_window_is_opened_and_merely_closed(qapp, a_pdf, tmp_path):
    """Closing is *not* destroying — which is exactly the trap. This leaves a closed-but-alive
    window behind, the state every other GUI test in the suite also leaves."""
    qapp.settings = Settings(tmp_path / "vs.json")
    win = qapp.open_document(a_pdf)
    win.show()
    qapp.processEvents()
    assert QApplication.topLevelWidgets()          # it is alive, and so are its menus
    win.undo_stack.setClean()
    win.close()
    assert win in QApplication.topLevelWidgets()   # ... still alive after close()


def test_the_previous_tests_widgets_are_gone(qapp):
    """The invariant. Nothing the last test built may still exist — not the window, not its menu
    bar's ten top-level `QMenu`s, not the format bar."""
    assert QApplication.topLevelWidgets() == []
    assert qapp._windows == {}
