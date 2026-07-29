"""The rest of the reading-input conventions — M89.1 / M89.2 / M89.3 (`PLAN.md` §M89). Offscreen GUI.

Three gestures every PDF reader has and this view did not:

* **M89.1** `Home` / `End` / `Ctrl+Home` / `Ctrl+End` → the **document's** start / end, all four one
  verb. Qt left every one of them dead here: `QAbstractScrollArea` binds Home/End only on macOS.
* **M89.2** `Space` / `Shift+Space` → one screenful down / up, the same `SliderPageStep` the
  working `PgDn`/`PgUp` already trigger.
* **M89.3** `Shift+wheel` → horizontal pan. An *override*, not a gap — Qt's own `Shift+wheel`
  scrolls this view vertically, so a page wider than the window had no wheel gesture to cross it.

The design decision under all three is that they live in `PdfView.keyPressEvent` / `wheelEvent`,
**never** as window-level `QAction` shortcuts: a window shortcut fires wherever focus is, so `Home`
and `Space` bound that way would hijack those keys from the inline editors that are children of this
viewport. Two tests pin that (`test_the_navigation_keys_are_not_window_shortcuts`,
`test_space_still_types_a_space_in_a_form_field`).
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QWheelEvent
from PySide6.QtTest import QTest

from app import PdfApp
from store.settings import Settings
from viewer.pdf_view import _WHEEL_NOTCH

NONE = Qt.KeyboardModifier.NoModifier
CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier


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
    w.undo_stack.setClean()
    w.close()


@pytest.fixture
def view(win):
    return win.view


def _key(view, key, mods=NONE):
    view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, mods))


def _wheel(view, notches, mods=NONE, x_notches=0):
    """One mouse-wheel detent per notch (positive = towards the reader / up-and-left)."""
    pt = QPointF(view.viewport().rect().center())
    delta = QPoint(_WHEEL_NOTCH * x_notches, _WHEEL_NOTCH * notches)
    view.wheelEvent(QWheelEvent(pt, view.viewport().mapToGlobal(pt), delta, delta,
                                Qt.MouseButton.NoButton, mods,
                                Qt.ScrollPhase.NoScrollPhase, False))


def _widen(view, zoom=3.0):
    """Zoom past the viewport's width so there is a horizontal range to pan across."""
    view.set_zoom(zoom)
    PdfApp.instance().processEvents()
    hbar = view.horizontalScrollBar()
    assert hbar.maximum() > hbar.minimum(), "fixture failed to produce a horizontal range"
    return hbar


# ---- M89.1: Home / End go to the ends of the document ------------------------


@pytest.mark.parametrize("mods", [NONE, CTRL], ids=["bare", "ctrl"])
def test_end_jumps_to_the_document_end(view, mods):
    """`End` and `Ctrl+End` are the same verb (owner call): a continuous strip has no separate
    "line end" for the bare form to mean, and Preview/Edge/Chrome bind them alike in a PDF."""
    vbar = view.verticalScrollBar()
    assert vbar.value() == vbar.minimum()
    _key(view, Qt.Key.Key_End, mods)
    assert vbar.value() == vbar.maximum()


@pytest.mark.parametrize("mods", [NONE, CTRL], ids=["bare", "ctrl"])
def test_home_jumps_to_the_document_start(view, mods):
    vbar = view.verticalScrollBar()
    vbar.setValue(vbar.maximum())
    _key(view, Qt.Key.Key_Home, mods)
    assert vbar.value() == vbar.minimum()


def test_the_page_indicator_follows_the_jump(view):
    """Implemented as the scrollbar's minimum/maximum, which is the literal reading — and which
    gets the current-page update for free, since `_on_scroll` already does it."""
    seen: list[int] = []
    view.currentPageChanged.connect(seen.append)
    _key(view, Qt.Key.Key_End)
    assert view.current_page == len(view._pages) - 1
    assert seen and seen[-1] == view.current_page
    _key(view, Qt.Key.Key_Home)
    assert view.current_page == 0


def test_the_keypad_home_key_works_too(view):
    """The keypad's Home carries `KeypadModifier`, so an exact modifier match would accept the main
    keyboard's key and silently ignore the keypad's — a difference no reader means."""
    vbar = view.verticalScrollBar()
    vbar.setValue(vbar.maximum())
    _key(view, Qt.Key.Key_Home, Qt.KeyboardModifier.KeypadModifier)
    assert vbar.value() == vbar.minimum()


def test_the_slideshow_keeps_its_own_home_and_end(win, view):
    """M78 already binds Home/End to the first/last *slide*; that branch returns long before the
    new one, so whole-slide stepping is untouched."""
    win._a_slideshow.trigger()
    PdfApp.instance().processEvents()
    assert view.slideshow
    _key(view, Qt.Key.Key_End)
    assert view.current_page == len(view._pages) - 1
    _key(view, Qt.Key.Key_Home)
    assert view.current_page == 0
    _key(view, Qt.Key.Key_Escape)
    PdfApp.instance().processEvents()


# ---- M89.2: Space pages ------------------------------------------------------


def test_space_pages_down_one_screenful(view):
    vbar = view.verticalScrollBar()
    before = vbar.value()
    _key(view, Qt.Key.Key_Space)
    assert vbar.value() == before + vbar.pageStep()


def test_shift_space_pages_up(view):
    vbar = view.verticalScrollBar()
    vbar.setValue(vbar.maximum())
    before = vbar.value()
    _key(view, Qt.Key.Key_Space, SHIFT)
    assert vbar.value() == before - vbar.pageStep()


def test_space_matches_page_down(view):
    """Same verb, same distance — `Space` is `PgDn` on the key most readers actually reach for."""
    vbar = view.verticalScrollBar()
    _key(view, Qt.Key.Key_PageDown)
    by_pgdn = vbar.value()
    vbar.setValue(vbar.minimum())
    _key(view, Qt.Key.Key_Space)
    assert vbar.value() == by_pgdn


def test_space_still_types_a_space_in_a_form_field(win, view):
    """The reason these are view keys and not window shortcuts. The inline field editor is a child
    of this viewport, so with focus in it a real `Space` must reach the editor and type — never
    page the document."""
    rect = fitz.Rect(72, 200, 272, 220)                    # the `name` field in the A.pdf fixture
    assert view.form.handle_press(view.scene_rect_for_box(0, rect).center())
    editor = view.form._editor
    editor.setText("ab")
    editor.setCursorPosition(1)
    before = view.verticalScrollBar().value()
    QTest.keyClick(editor, Qt.Key.Key_Space)
    assert editor.text() == "a b"
    assert view.verticalScrollBar().value() == before      # ... and the page did not move


def test_the_navigation_keys_are_not_window_shortcuts(win):
    """Pins the decision itself: no `QAction` anywhere in the window may claim these keys, or a
    focused editor would never see them."""
    hijacked = {Qt.Key.Key_Space, Qt.Key.Key_Home, Qt.Key.Key_End}
    for action in win.findChildren(type(win.menuBar().actions()[0])):
        for sequence in action.shortcuts():
            for i in range(sequence.count()):
                assert Qt.Key(sequence[i].key()) not in hijacked, action.text()


# ---- M89.3: Shift+wheel pans horizontally ------------------------------------


def test_shift_wheel_pans_horizontally(view):
    hbar = _widen(view)
    hbar.setValue(hbar.maximum() // 2)
    before = hbar.value()
    _wheel(view, -1, SHIFT)                      # wheel away from the reader → pan right
    assert hbar.value() > before
    _wheel(view, 1, SHIFT)                       # ... and back
    assert hbar.value() == before


def test_shift_wheel_does_not_scroll_vertically(view):
    """The override. Qt's own `Shift+wheel` scrolls *down* here — measured with the h-bar at full
    range — which is why a wide page had no wheel gesture that could cross it."""
    _widen(view)
    vbar = view.verticalScrollBar()
    before = vbar.value()
    _wheel(view, -1, SHIFT)
    assert vbar.value() == before


def test_shift_wheel_is_inert_when_the_page_fits(view):
    """With nothing to pan across the gesture does nothing — rather than silently scrolling down,
    so the shifted wheel means one thing everywhere, as it does in a browser."""
    view.fit_page()
    PdfApp.instance().processEvents()
    hbar, vbar = view.horizontalScrollBar(), view.verticalScrollBar()
    assert hbar.maximum() == hbar.minimum()
    before = vbar.value()
    _wheel(view, -1, SHIFT)
    assert vbar.value() == before


def test_a_real_horizontal_wheel_is_left_to_qt(view):
    """A tilt wheel or touchpad already sends an x component, and Qt routes it correctly — we only
    reinterpret the vertical axis Shift is decorating."""
    hbar = _widen(view)
    hbar.setValue(hbar.minimum())
    _wheel(view, 0, SHIFT, x_notches=-1)
    assert hbar.value() > hbar.minimum()


def test_a_plain_wheel_still_scrolls_vertically(view):
    _widen(view)
    hbar, vbar = view.horizontalScrollBar(), view.verticalScrollBar()
    hbar.setValue(hbar.maximum() // 2)
    before_h, before_v = hbar.value(), vbar.value()
    _wheel(view, -1)
    assert vbar.value() > before_v
    assert hbar.value() == before_h


def test_ctrl_wheel_still_zooms(view):
    """M80 is checked first, so a Ctrl+Shift+wheel zooms rather than panning."""
    before = view.zoom
    _wheel(view, 1, CTRL)
    PdfApp.instance().processEvents()
    assert view.zoom > before
