"""The window keeps a usable size (M149, [#358](https://github.com/utyagi24/klarpdf/issues/358)).
Offscreen GUI.

``MainWindow`` set no minimum of its own, so its floor was whatever the toolbar and sidebar needed:
measured at **70 × 124** with the sidebar closed and 222 × 124 with it open, leaving a 54 × 68 page
area at a 3.3% Fit Width. Who stopped the drag was then the window manager's business, and they
disagree — Windows keeps a window at least as wide as its own caption buttons so the edge stays
grabbable, while WSLg's compositor lets the window become a bar with its resize edge underneath the
title bar, from which it cannot be dragged back.

The fix is a size on the window, not OS-specific code, and the value is the floor
``_open_geometry`` has always clamped the *opening* window to, now single-sourced.

**Two halves, because the first was not enough.** ``setMinimumSize`` constrains Qt's own
resizes and is handed to the window system as a hint — measured under WSLg, ``QWindow.minimumSize()``
really is 400 x 300 and Qt really does send ``xdg_toplevel.set_min_size``. Windows honours it and the
drag stops. WSLg's compositor ignores it: the size arrives as a configure, Qt applies it, and the
widget follows its window down to a bar. So the floor is also enforced where the size *lands*, by a
debounced snap-back. The tests below cover both halves — a Qt-side resize (``win.resize``) and a
window-system-side one (``win.windowHandle().resize``), which the offscreen platform reproduces
exactly as WSLg does.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtCore import QRect

import main_window as main_window_module
from app import PdfApp
from main_window import MIN_WINDOW_SIZE, MainWindow
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
def win(app, tmp_path):
    doc = fitz.open()
    for i in range(3):
        doc.new_page().insert_text((72, 72), f"PAGE {i}", fontsize=14)
    path = tmp_path / "pages.pdf"
    doc.save(str(path))
    doc.close()
    w = app.open_document(str(path))
    w.show()
    app.processEvents()
    return w


def _shrink_to_nothing(app, w):
    w.resize(1, 1)
    app.processEvents()
    return w.width(), w.height()


def test_the_window_refuses_to_shrink_below_the_minimum(app, win):
    assert win.minimumSize().width() == MIN_WINDOW_SIZE[0]
    assert win.minimumSize().height() == MIN_WINDOW_SIZE[1]
    assert _shrink_to_nothing(app, win) == MIN_WINDOW_SIZE


def test_it_holds_with_the_sidebar_closed_too(app, win):
    """The sidebar's own 146 px floor was carrying most of the old minimum; with it closed the
    window used to reach 70 px wide."""
    win.pages_dock.setVisible(False)
    app.processEvents()
    assert _shrink_to_nothing(app, win) == MIN_WINDOW_SIZE


def test_the_page_is_still_worth_looking_at_at_the_minimum(app, win):
    """The reason for the number: at the old floor the page area was 54 × 68 at a 3.3% Fit Width,
    which is not a document. Here it is a readable fraction of one."""
    _shrink_to_nothing(app, win)
    win.view.fit_width()
    app.processEvents()
    assert win.view.viewport().width() >= 150
    assert win.view.viewport().height() >= 200
    assert win.view.zoom > 0.15


def test_it_can_always_be_made_bigger_again(app, win):
    """#358's actual complaint: on WSLg the shrunken window could not be resized back."""
    _shrink_to_nothing(app, win)
    win.resize(900, 700)
    app.processEvents()
    assert (win.width(), win.height()) == (900, 700)


def test_the_opening_geometry_uses_the_same_floor():
    """Two floors that can drift are one bug waiting: ``_open_geometry`` clamped to a literal
    400 × 300 of its own. On a screen too small to honour the request, both must give the same size."""
    tiny = QRect(0, 0, 200, 150)
    opened = MainWindow._open_geometry(tiny, width=1000, frame_w=16, frame_h=39, title_bar=31)
    assert (opened.width(), opened.height()) == MIN_WINDOW_SIZE


def test_the_minimum_fits_a_small_screen():
    """A minimum larger than a screen is a window that cannot be placed. 1024 × 768 is the smallest
    display anyone plausibly runs this on; the floor must sit well inside it."""
    assert MIN_WINDOW_SIZE[0] <= 1024 // 2
    assert MIN_WINDOW_SIZE[1] <= 768 // 2


# ---- the window system's own resize, which ignores Qt's minimum ----------------


@pytest.fixture
def instant_snap(monkeypatch):
    """Zero the snap-back debounce, so a test can assert on the next line.

    The same arrangement ``conftest``'s ``_instant_search`` and ``_instant_zoom`` have with the
    debounces they cover: the interval exists so a drag is not fought mid-gesture, which no test
    here is exercising.
    """
    monkeypatch.setattr(main_window_module, "_MIN_SIZE_SNAP_MS", 0)


def _force_from_window_system(app, win, width: int, height: int):
    """Resize the way a compositor does — through the QWindow, around Qt's own constraint.

    ``win.resize()`` is clamped by ``setMinimumSize`` before it reaches the platform, so it cannot
    reproduce #358's remaining half; this is the path WSLg's configure event arrives on.
    """
    win.windowHandle().resize(width, height)
    app.processEvents()
    app.processEvents()
    return win.width(), win.height()


def test_a_resize_from_the_window_system_is_pushed_back_up(app, win, instant_snap):
    """The WSLg report: *"I am still able to resize the window to a single vertical bar almost"*,
    with Qt's minimum already in place and honoured on Windows."""
    assert _force_from_window_system(app, win, 70, 124) == MIN_WINDOW_SIZE


def test_only_the_short_side_is_pushed_back(app, win, instant_snap):
    """A window that is short in one direction keeps its other dimension — a snap-back that also
    resized the wide side would be moving the window for no reason.

    This is the case that caught a wrong comparison while building: the first version asked whether
    the *floor* equalled the expanded size, which is true whenever the window is below the floor in
    **both** directions — so 70 x 124 and 200 x 200 were skipped and only 120 x 500 was caught.
    """
    assert _force_from_window_system(app, win, 120, 500) == (MIN_WINDOW_SIZE[0], 500)
    assert _force_from_window_system(app, win, 900, 200) == (900, MIN_WINDOW_SIZE[1])


def test_a_legitimate_size_from_the_window_system_is_left_alone(app, win, instant_snap):
    """The control: the guard must not touch a window that is big enough, or every resize fights."""
    assert _force_from_window_system(app, win, 900, 700) == (900, 700)


def test_the_floor_never_exceeds_the_screen(app, win, monkeypatch):
    """A minimum larger than the available area is a window that can never satisfy it, and pushing
    it back up forever is the one way this guard could misbehave. The clamp removes that."""
    monkeypatch.setattr(main_window_module, "MIN_WINDOW_SIZE", (100000, 100000))
    allowed = win._smallest_allowed()
    available = win.screen().availableSize()
    assert allowed.width() <= available.width()
    assert allowed.height() <= available.height()
