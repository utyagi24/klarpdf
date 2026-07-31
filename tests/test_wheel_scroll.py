"""M92.1 — a mouse-wheel detent moves a defined distance (`PLAN.md` §M92). Offscreen GUI.

The defect these pin: Qt's `QGraphicsView` sets the vertical scrollbar's `singleStep` to
**`viewportHeight / 20`**, so before M92.1 one detent moved `wheelScrollLines × singleStep` — *15% of
the window height and nothing else*. Unrelated to the document, the text or the zoom, and worse the
more screen the window was given; measured on the owner's display (viewport 1246 px tall) it threw
the page **183 px, ten lines of body text, per click**.

The replacement is `wheelScrollLines × _WHEEL_LINE_PX × zoom`, which is what the three properties
below assert one at a time: **window-independent** (the regression that started this), **zoom-scaled**
(so a detent always moves the same amount of *document*), and **lossless** across a burst of events.

The fourth group is the scope boundary. M92.1 changes the **mouse** only — touchpad scrolling was
declared out of scope by the owner, so a precision device must still reach `super().wheelEvent()`
untouched, as must a tilt wheel, `Ctrl+wheel` (zoom), `Shift+wheel` (pan) and the slideshow.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from app import PdfApp
from store.settings import Settings
from viewer.pdf_view import _WHEEL_LINE_PX, _WHEEL_NOTCH

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
    v = win.view
    v.set_zoom(1.0)
    PdfApp.instance().processEvents()
    v.verticalScrollBar().setValue(0)
    return v


def _wheel(view, angle_y, angle_x=0, mods=NONE, pixel=None):
    """One wheel event carrying a raw `angleDelta` — in eighths of a degree, so a mouse detent is
    ±`_WHEEL_NOTCH` and a precision device sends fractions of it.

    `pixel` sets `pixelDelta`, which Qt fills on macOS/Wayland and never on Windows; passing it is
    how the portable arm of `_is_mouse_detent` gets exercised on a Windows/Linux test runner.
    """
    pt = QPointF(view.viewport().rect().center())
    angle = QPoint(angle_x, angle_y)
    px = QPoint(0, 0) if pixel is None else QPoint(0, pixel)
    event = QWheelEvent(pt, view.viewport().mapToGlobal(pt), px, angle,
                        Qt.MouseButton.NoButton, mods, Qt.ScrollPhase.NoScrollPhase, False)
    view.wheelEvent(event)
    return event


def _expected_step(view):
    return QApplication.wheelScrollLines() * _WHEEL_LINE_PX * view.zoom


# ---- the step itself ---------------------------------------------------------


def test_one_detent_moves_the_defined_distance(view):
    vbar = view.verticalScrollBar()
    start = vbar.value()
    _wheel(view, -_WHEEL_NOTCH)                       # wheel down
    assert vbar.value() - start == pytest.approx(_expected_step(view), abs=1)


def test_wheel_up_moves_back_by_the_same_distance(view):
    vbar = view.verticalScrollBar()
    vbar.setValue(2000)
    _wheel(view, _WHEEL_NOTCH)                        # wheel up
    assert 2000 - vbar.value() == pytest.approx(_expected_step(view), abs=1)


def test_the_step_does_not_depend_on_the_window_height(view, win):
    """**The M92.1 defect.** Qt's `singleStep` is `viewportHeight / 20`, so the pre-M92.1 step grew
    with the window — which is why the owner's full-height window moved 183 px a click while a
    900 px-tall bench window moved 126. Doubling the viewport must not move the page any further."""
    vbar = view.verticalScrollBar()

    win.resize(1000, 500)
    PdfApp.instance().processEvents()
    short_viewport = view.viewport().height()
    vbar.setValue(0)
    _wheel(view, -_WHEEL_NOTCH)
    short_step = vbar.value()

    win.resize(1000, 1000)
    PdfApp.instance().processEvents()
    tall_viewport = view.viewport().height()
    vbar.setValue(0)
    _wheel(view, -_WHEEL_NOTCH)
    tall_step = vbar.value()

    assert tall_viewport > short_viewport * 1.5, "fixture failed to change the viewport height"
    assert short_step == pytest.approx(tall_step, abs=1)
    # And the old rule really would have differed here — otherwise this test proves nothing.
    assert short_viewport // 20 != tall_viewport // 20


def test_the_step_scales_with_zoom(view):
    """A detent moves the same amount of *document* at every zoom: at 200% the text is twice as tall,
    so covering the same three lines takes twice the pixels. Qt's rule ignored zoom entirely."""
    vbar = view.verticalScrollBar()
    view.set_zoom(1.0)
    PdfApp.instance().processEvents()
    vbar.setValue(0)
    _wheel(view, -_WHEEL_NOTCH)
    at_100 = vbar.value()

    view.set_zoom(2.0)
    PdfApp.instance().processEvents()
    vbar.setValue(0)
    _wheel(view, -_WHEEL_NOTCH)
    at_200 = vbar.value()

    assert at_200 == pytest.approx(at_100 * 2, abs=2)


def test_detents_accumulate_without_losing_ground(view):
    """Ten detents move ten steps. The scrollbar takes whole pixels, so each event leaves a fraction
    over; `_scroll_remainder` carries it rather than rounding it away ten times."""
    vbar = view.verticalScrollBar()
    vbar.setValue(0)
    for _ in range(10):
        _wheel(view, -_WHEEL_NOTCH)
    assert vbar.value() == pytest.approx(_expected_step(view) * 10, abs=1)


def test_a_coalesced_multi_detent_event_moves_proportionally(view):
    """A fast spin can arrive as one event carrying several detents; it moves that many steps, not
    one. (The slideshow quantises to whole detents — reading does not.)"""
    vbar = view.verticalScrollBar()
    vbar.setValue(0)
    _wheel(view, -_WHEEL_NOTCH * 3)
    assert vbar.value() == pytest.approx(_expected_step(view) * 3, abs=1)


def test_the_step_honours_the_windows_lines_to_scroll_setting(view, monkeypatch):
    """`wheelScrollLines` is Windows' *"roll the mouse wheel to scroll: N lines at a time"* slider.
    Before M92.1 it multiplied a "line" that was 5% of the window, so the setting meant nothing here;
    now a line is a line and the slider is the reader's own control."""
    vbar = view.verticalScrollBar()
    monkeypatch.setattr(QApplication, "wheelScrollLines", staticmethod(lambda: 1))
    vbar.setValue(0)
    _wheel(view, -_WHEEL_NOTCH)
    one_line = vbar.value()

    monkeypatch.setattr(QApplication, "wheelScrollLines", staticmethod(lambda: 6))
    vbar.setValue(0)
    _wheel(view, -_WHEEL_NOTCH)
    six_lines = vbar.value()

    assert one_line == pytest.approx(_WHEEL_LINE_PX * view.zoom, abs=1)
    assert six_lines == pytest.approx(one_line * 6, abs=1)


def test_the_wheel_stops_at_the_ends_of_the_document(view):
    vbar = view.verticalScrollBar()
    vbar.setValue(0)
    for _ in range(5):
        _wheel(view, _WHEEL_NOTCH)              # up, already at the top
    assert vbar.value() == vbar.minimum()
    for _ in range(400):
        _wheel(view, -_WHEEL_NOTCH)
    assert vbar.value() == vbar.maximum()


# ---- the scope boundary: what M92.1 must NOT take over -----------------------


def test_a_precision_device_keeps_qts_own_arithmetic(view):
    """**Touchpad scrolling is out of M92's scope** (owner, 2026-07-30). A precision device reports
    fractions of a detent — that granularity is the discriminator on Windows, where `pixelDelta` is
    null for every device — and must reach `super().wheelEvent()` unchanged, i.e. move by Qt's
    `singleStep`-derived distance, not ours."""
    vbar = view.verticalScrollBar()
    qt_step = QApplication.wheelScrollLines() * vbar.singleStep()
    assert qt_step != pytest.approx(_expected_step(view), abs=2), \
        "fixture: the two rules must differ here or the assertion below proves nothing"

    vbar.setValue(0)
    _wheel(view, -40)                            # a third of a detent: a touchpad, not a click
    assert vbar.value() == pytest.approx(qt_step / 3, abs=2)


def test_a_pixel_delta_event_keeps_qts_own_arithmetic(view):
    """The portable arm of the same rule: where Qt reports `pixelDelta` (macOS, Wayland) the device
    has told us the distance in pixels, so the event is not ours to re-quantise.

    Note what Qt then does with it: `QAbstractSlider::wheelEvent` derives its distance from
    **`angleDelta`** and ignores `pixelDelta` entirely, so declining the event yields Qt's
    `singleStep`-based number, not the 13 px the device reported. That is pre-M92.1 behaviour
    preserved exactly, which is all this arm promises — improving Qt's handling of precision devices
    is the deferred touchpad work, not M92.1."""
    vbar = view.verticalScrollBar()
    qt_step = QApplication.wheelScrollLines() * vbar.singleStep()
    vbar.setValue(0)
    _wheel(view, -_WHEEL_NOTCH, pixel=-13)
    assert vbar.value() == pytest.approx(qt_step, abs=2)
    assert vbar.value() != pytest.approx(_expected_step(view), abs=2)


def test_a_tilt_wheel_still_scrolls_horizontally(view):
    """A horizontal-dominant wheel is a tilt wheel; `QAbstractScrollArea` already routes the larger
    axis to the matching bar, so M92.1 takes over the vertical axis only."""
    view.set_zoom(3.0)                            # give the h-bar a range to move across
    PdfApp.instance().processEvents()
    hbar, vbar = view.horizontalScrollBar(), view.verticalScrollBar()
    assert hbar.maximum() > hbar.minimum(), "fixture failed to produce a horizontal range"
    hbar.setValue(0)
    before_v = vbar.value()
    _wheel(view, 0, angle_x=-_WHEEL_NOTCH)
    assert hbar.value() > 0
    assert vbar.value() == before_v


def test_ctrl_wheel_still_zooms_and_never_reaches_the_scroll_path(view, monkeypatch):
    """The zoom sits ahead of the M92.1 step and consumes the event.

    Asserted by spying on `_wheel_scroll` rather than on the scrollbar: an anchored zoom legitimately
    *moves* the scrollbar to keep the anchor point under the pointer, so a stationary bar is not what
    "did not scroll" means here."""
    calls = []
    original = view._wheel_scroll
    monkeypatch.setattr(view, "_wheel_scroll",
                        lambda e: (calls.append(e), original(e))[1])
    before = view.zoom
    _wheel(view, _WHEEL_NOTCH, mods=CTRL)
    view._flush_wheel_zoom()                      # M86.2 coalesces to one zoom per frame
    assert view.zoom > before
    assert calls == []


def test_shift_wheel_still_pans_horizontally(view):
    """M89.3's override sits ahead of the M92.1 step, so a shifted wheel is still the horizontal
    axis and never falls through to a vertical scroll."""
    view.set_zoom(3.0)
    PdfApp.instance().processEvents()
    hbar, vbar = view.horizontalScrollBar(), view.verticalScrollBar()
    assert hbar.maximum() > hbar.minimum(), "fixture failed to produce a horizontal range"
    hbar.setValue(0)
    before_v = vbar.value()
    _wheel(view, -_WHEEL_NOTCH, mods=SHIFT)
    assert hbar.value() > 0
    assert vbar.value() == before_v


def test_the_slideshow_still_steps_whole_slides(view):
    """The slideshow's contract is one page per screen (M78), so its wheel steps slides and never
    reaches the M92.1 scroll path."""
    view.slideshow = True
    PdfApp.instance().processEvents()
    assert view.current_page == 0
    _wheel(view, -_WHEEL_NOTCH)
    assert view.current_page == 1
    view.slideshow = False
