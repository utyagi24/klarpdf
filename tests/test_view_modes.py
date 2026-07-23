"""View modes (PLAN.md §GUI feature roadmap → R6, M78). Offscreen GUI.

The reading modes Preview offers: **Full Screen** (chrome-free reading, F11) · **Slideshow**
(one page per screen at Fit Page, click/arrow advance, Esc exits) · **Two-Page view** (facing
pages in the ordinary window). View menu + the bare-page right-click menu carry them; everything
is **view-only** — file, print and export untouched (the M49 principle).
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest

from app import PdfApp
from store.settings import Settings
from viewer.pdf_view import _PAGE_GAP


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def win(qapp, a_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    w = qapp.open_document(a_pdf)
    w.resize(1000, 700)
    w.show()
    qapp.processEvents()
    yield w
    if w._chrome_state is not None:
        w._exit_chromeless()
    w.undo_stack.setClean()
    w.close()


def _esc(widget):
    widget.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                   Qt.KeyboardModifier.NoModifier))


def _key(widget, key):
    widget.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))


def _click(view):
    pt = QPointF(view.viewport().rect().center())
    view.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, pt, pt,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def _wheel(view, notches):
    """One mouse-wheel detent per notch (negative = away from the reader, i.e. forward)."""
    pt = QPointF(view.viewport().rect().center())
    delta = QPoint(0, 120 * notches)
    view.wheelEvent(QWheelEvent(pt, view.viewport().mapToGlobal(pt), delta, delta,
                                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                Qt.ScrollPhase.NoScrollPhase, False))


def _page_top(view, index):
    return int(view._pages[index]["y"]) - _PAGE_GAP


# ---- two-page view -----------------------------------------------------------


def test_two_page_lays_facing_pairs(win):
    win._a_twopage.trigger()
    pages = win.view._pages
    assert pages[0]["y"] == pages[1]["y"]          # 1 | 2 share a row…
    assert pages[1]["x"] > pages[0]["x"]           # …side by side
    assert pages[2]["y"] > pages[0]["y"]           # 3 starts the next row
    win._a_twopage.trigger()                       # back to the strip
    ys = [p["y"] for p in win.view._pages]
    assert ys == sorted(ys) and len(set(ys)) == 3  # one page per row again


def test_two_page_hit_maps_the_right_hand_page(win):
    win._a_twopage.trigger()
    p1 = win.view._pages[1]
    centre = QPointF(p1["x"] + p1["w"] / 2, p1["y"] + p1["h"] / 2)
    page_index, local = win.view.page_and_local_at(centre)
    assert page_index == 1                          # not page 0, whose y-band it shares
    w, h = win.view._unrotated_size(1)
    assert 0 <= local.x() <= w and 0 <= local.y() <= h


def test_two_page_fit_width_frames_the_spread(win):
    win._a_twopage.trigger()
    win.view.fit_width()
    pair_width = (win.view._pages[0]["w"] + win.view._pages[1]["w"])
    viewport = win.view.viewport().width()
    assert pair_width <= viewport                   # the whole spread fits…
    assert pair_width > viewport * 0.7              # …and actually fills most of it


def test_two_page_is_view_only_and_reversible(win):
    win._a_twopage.trigger()
    assert win.vdoc.dirty is False and win.undo_stack.count() == 0
    win._a_twopage.trigger()
    assert win.view.page_layout == "single"


# ---- full screen -------------------------------------------------------------


def test_fullscreen_strips_the_chrome_and_esc_restores(qapp, win):
    win.pages_dock.show()                           # some chrome up, to prove restoration
    assert win.menuBar().isVisible()
    win._a_fullscreen.trigger()
    qapp.processEvents()
    assert win.isFullScreen()
    assert not win.menuBar().isVisible()
    assert not win._main_toolbar.isVisible()
    assert not win.pages_dock.isVisible()
    assert win._a_fullscreen.isChecked()
    _esc(win)                                       # the Esc path (bubbles from the view)
    qapp.processEvents()
    assert not win.isFullScreen()
    assert win.menuBar().isVisible()
    assert win._main_toolbar.isVisible()
    assert win.pages_dock.isVisible()               # exactly what was up comes back
    assert not win._a_fullscreen.isChecked()


def test_f11_exits_full_screen(qapp, win):
    """F11 out, as well as in. The action lives on the View menu, and a menu action's shortcut is
    only live while the menu bar is visible — which full screen hides, leaving Esc as the only way
    out. The window carries the action too, so the shortcut survives the hidden menu bar."""
    assert win._a_fullscreen in win.actions()
    win._a_fullscreen.trigger()
    qapp.processEvents()
    assert win.isFullScreen() and not win.menuBar().isVisible()
    win.activateWindow()
    QTest.keyClick(win, Qt.Key.Key_F11)             # the real shortcut, menu bar hidden
    qapp.processEvents()
    assert not win.isFullScreen()
    assert win.menuBar().isVisible()
    assert not win._a_fullscreen.isChecked()


def test_f11_leaves_a_slideshow_too(qapp, win):
    """F11 toggles chrome-free reading, whichever mode is up — it can't check a box over a
    slideshow already running."""
    win._a_slideshow.trigger()
    qapp.processEvents()
    assert win.view.slideshow
    win._a_fullscreen.trigger()
    qapp.processEvents()
    assert not win.isFullScreen()
    assert not win.view.slideshow
    assert not win._a_fullscreen.isChecked()


def test_fullscreen_does_not_rewrite_the_remembered_prefs(qapp, win):
    """Programmatic hides must not persist: reading full-screen never rewrites the sidebar /
    markup-bar choices (only explicit user toggles do)."""
    win.pages_dock.show()
    qapp.settings.set_pref("sidebar_visible", True)
    win._a_fullscreen.trigger()
    _esc(win)
    assert qapp.settings.get_pref("sidebar_visible") is True


# ---- slideshow ---------------------------------------------------------------


def test_slideshow_steps_pages_by_click_and_keys(qapp, win):
    win._a_slideshow.trigger()
    qapp.processEvents()
    assert win.isFullScreen() and win.view.slideshow
    assert win.view._fit_mode == "page"             # one page per screen
    pt = QPointF(win.view.viewport().rect().center())
    win.view.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, pt, pt,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert win.view.current_page == 1               # click advances
    _key(win.view, Qt.Key.Key_Right)
    assert win.view.current_page == 2
    _key(win.view, Qt.Key.Key_Right)
    assert win.view.current_page == 2               # clamped at the end, no wrap
    _key(win.view, Qt.Key.Key_Left)
    assert win.view.current_page == 1
    _esc(win)
    assert not win.view.slideshow and not win.isFullScreen()


def test_slideshow_steps_spreads_in_two_page_view(qapp, win):
    """A slideshow step is a **row**, not a page index. Pages 1|2 share a row in the facing
    layout, so stepping by index scrolled to the offset already on screen — the click and the
    forward keys looked dead, and only the backward ones (which land on the previous row) moved."""
    win._a_twopage.trigger()
    win._a_slideshow.trigger()
    qapp.processEvents()
    assert win.view.current_page == 0
    _click(win.view)                                # click advances a whole spread…
    assert win.view.current_page == 2
    _key(win.view, Qt.Key.Key_Left)                 # …and back
    assert win.view.current_page == 0
    _key(win.view, Qt.Key.Key_Down)
    assert win.view.current_page == 2
    _key(win.view, Qt.Key.Key_Right)
    assert win.view.current_page == 2               # clamped on the last spread, no wrap
    _esc(win)


def test_slideshow_wheel_steps_whole_pages(qapp, win):
    """The wheel projects the next slide instead of free-scrolling: a mode showing one page per
    screen must never come to rest straddling two — from a straddle the page under the viewport
    centre isn't the page being read, and the next click stepped from the wrong one."""
    win._a_slideshow.trigger()
    qapp.processEvents()
    view = win.view
    _wheel(view, -1)                                # one detent forward
    assert view.current_page == 1
    assert view.verticalScrollBar().value() == _page_top(view, 1)   # exactly a page top
    _wheel(view, -1)
    assert view.current_page == 2
    _wheel(view, -1)
    assert view.current_page == 2                   # clamped at the end
    _wheel(view, 2)                                 # two detents back, in one event
    assert view.current_page == 0
    assert view.verticalScrollBar().value() == _page_top(view, 0)
    _esc(win)


def test_slideshow_click_advances_after_a_round_trip(qapp, win):
    """To the end of the deck and back, then click: the next slide follows, first click."""
    win._a_slideshow.trigger()
    qapp.processEvents()
    view = win.view
    _wheel(view, -10)                               # all the way to the end
    assert view.current_page == 2
    _wheel(view, 10)                                # all the way back to the first page
    assert view.current_page == 0
    _click(view)
    assert view.current_page == 1
    assert view.verticalScrollBar().value() == _page_top(view, 1)
    _esc(win)


def test_slideshow_restores_the_prior_zoom(qapp, win):
    win.view.set_zoom(1.7)                          # a manual zoom, no sticky fit
    win._a_slideshow.trigger()
    qapp.processEvents()
    assert win.view._fit_mode == "page"
    _esc(win)
    qapp.processEvents()
    assert win.view._fit_mode is None
    assert win.view.zoom == pytest.approx(1.7)      # back exactly where the reader had it


def test_slideshow_blocks_selection_and_menus(qapp, win):
    win._a_slideshow.trigger()
    qapp.processEvents()
    pt = QPointF(win.view.viewport().rect().center())
    win.view.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, pt, pt,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert win.view.selection.selected_words() == []   # the click stepped, it didn't select
    _esc(win)


def test_slideshow_leaves_night_mode_alone(qapp, win):
    win.view.set_night_mode(True)
    win._a_slideshow.trigger()
    qapp.processEvents()
    assert win.view.night_mode                      # night reading carries into the slideshow
    _esc(win)
    assert win.view.night_mode
    win.view.set_night_mode(False)


def test_modes_touch_nothing_savable(qapp, win):
    win._a_twopage.trigger()
    win._a_slideshow.trigger()
    qapp.processEvents()
    _esc(win)
    win._a_fullscreen.trigger()
    _esc(win)
    assert win.vdoc.dirty is False
    assert win.undo_stack.count() == 0              # nothing undoable happened
    assert win.isWindowModified() is False


# ---- surfacing ---------------------------------------------------------------


def test_modes_ride_the_bare_page_context_menu(win):
    menu = win._view_context_menu(win.view.scene_rect_for_box(0, (300, 500, 360, 520)).center())
    actions = menu.actions()
    assert win._a_fullscreen in actions
    assert win._a_slideshow in actions
    assert win._a_twopage in actions
