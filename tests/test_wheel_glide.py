"""M92.2 — a wheel detent is eased, not teleported (`PLAN.md` §M92). Offscreen GUI.

M92.1 fixed *how far* a detent moves; this fixes *how it gets there*. The distance is unchanged and
so is the landing pixel — the tests below assert exactly that, because a glide that quietly altered
where you end up would be a worse bug than the jump it replaced.

**These tests never sleep.** `PdfView._glide_now_ms` is the animator's only clock and is a method
precisely so a test can drive the curve: `_at(view, ms)` sets the clock and fires one tick. That is
what makes it possible to assert the *shape* of the motion — front-loaded, monotone, landing exactly
once — rather than just its endpoint, and it keeps a 200 ms animation a sub-millisecond test.

The interesting cases are the ones where a glide meets something else: a second detent mid-flight
(extend the target, don't restart), a reversal (collapse it, don't unwind), and any deliberate
navigation (stop, or M91.4's defect comes back wearing our own animation).
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QWheelEvent
from PySide6.QtWidgets import QApplication

from app import PdfApp
from store.settings import Settings
from viewer.pdf_view import _WHEEL_EASE_MS, _WHEEL_LINE_PX, _WHEEL_NOTCH

NONE = Qt.KeyboardModifier.NoModifier


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
    v.smooth_scrolling = True
    v.verticalScrollBar().setValue(0)
    v._glide_clock = 0.0
    v._glide_now_ms = lambda: v._glide_clock          # the injected clock (see module docstring)
    return v


def _wheel(view, notches=-1):
    pt = QPointF(view.viewport().rect().center())
    d = QPoint(0, _WHEEL_NOTCH * notches)
    view.wheelEvent(QWheelEvent(pt, view.viewport().mapToGlobal(pt), QPoint(0, 0), d,
                                Qt.MouseButton.NoButton, NONE, Qt.ScrollPhase.NoScrollPhase, False))


def _at(view, ms):
    """Advance the animator's clock to ``ms`` after the current glide began, and tick once."""
    view._glide_clock = view._glide_start + ms
    view._glide_tick()
    return view.verticalScrollBar().value()


def _step(view):
    return QApplication.wheelScrollLines() * _WHEEL_LINE_PX * view.zoom


# ---- the glide moves, and lands exactly where M92.1 would have --------------------


def test_a_detent_does_not_move_the_view_immediately(view):
    """The whole point: the wheel event itself no longer writes the bar."""
    vbar = view.verticalScrollBar()
    vbar.setValue(1000)
    _wheel(view)
    assert vbar.value() == 1000
    assert view._glide_target == pytest.approx(1000 + _step(view), abs=1)


def test_the_glide_lands_on_exactly_the_m92_1_pixel(view):
    vbar = view.verticalScrollBar()
    vbar.setValue(1000)
    _wheel(view)
    _at(view, _WHEEL_EASE_MS)
    assert vbar.value() - 1000 == pytest.approx(_step(view), abs=1)


def test_the_motion_is_gradual_and_monotone(view):
    """Sampled every 16 ms across the glide: many distinct positions, never going backwards."""
    vbar = view.verticalScrollBar()
    vbar.setValue(1000)
    _wheel(view)
    samples = [_at(view, ms) for ms in range(16, int(_WHEEL_EASE_MS) + 16, 16)]
    assert len(set(samples)) >= 8, f"not a glide, only {len(set(samples))} positions: {samples}"
    assert samples == sorted(samples), f"the glide went backwards: {samples}"
    assert samples[-1] - 1000 == pytest.approx(_step(view), abs=1)


def test_the_curve_is_ease_out_not_linear_or_ease_in(view):
    """Front-loaded: past the half-way *time*, well past the half-way *distance*. This is what makes
    the wheel feel responsive — ease-in-out would still be near the start here."""
    vbar = view.verticalScrollBar()
    vbar.setValue(1000)
    _wheel(view)
    half = _at(view, _WHEEL_EASE_MS / 2) - 1000
    assert half > _step(view) * 0.7, f"not front-loaded: {half} px of {_step(view):.0f} at halfway"
    # ...and the first frame moves a real distance rather than creeping out of the gate.
    vbar.setValue(1000)
    view.stop_glide()
    _wheel(view)
    first = _at(view, 16) - 1000
    assert first > _step(view) * 0.15, f"first frame moved only {first} px"


def test_the_glide_stops_itself_at_the_end(view):
    vbar = view.verticalScrollBar()
    vbar.setValue(1000)
    _wheel(view)
    assert view._glide_timer.isActive()
    _at(view, _WHEEL_EASE_MS)
    assert not view._glide_timer.isActive()
    assert view._glide_target is None


def test_the_glide_ends_on_the_pixels_not_on_the_clock(view):
    """An ease-out asymptotes, so its tail moves less than half a pixel a frame and the reader sees
    nothing. Measured on the owner's display, a detent finished moving at 161 ms of a 200 ms glide.
    Ending there costs the landing pixel nothing and stops a glide outliving its own motion —
    which matters because duty cycle is what argued against 200 ms in the first place."""
    vbar = view.verticalScrollBar()
    vbar.setValue(1000)
    _wheel(view)
    target = 1000 + _step(view)
    finished_at = None
    for ms in range(16, int(_WHEEL_EASE_MS) + 1, 16):
        _at(view, ms)
        if not view._glide_timer.isActive():
            finished_at = ms
            break
    assert finished_at is not None and finished_at < _WHEEL_EASE_MS, \
        "the glide ran the full clock even though the pixels had stopped"
    assert vbar.value() == pytest.approx(target, abs=1), "finishing early moved the landing pixel"


# ---- a glide meeting another input ----------------------------------------------


def test_a_second_detent_extends_the_target_instead_of_restarting(view):
    """The biggest thing M92.2 buys: a held spin is one motion, and no delta is lost to a glide
    that was still running when the next click arrived."""
    vbar = view.verticalScrollBar()
    vbar.setValue(1000)
    _wheel(view)
    _at(view, 40)                                    # part-way through the first glide
    _wheel(view)                                     # ...and here comes the second click
    assert view._glide_target == pytest.approx(1000 + _step(view) * 2, abs=2)
    _at(view, _WHEEL_EASE_MS)
    assert vbar.value() - 1000 == pytest.approx(_step(view) * 2, abs=2)


def test_four_quick_detents_accumulate_to_four_steps(view):
    vbar = view.verticalScrollBar()
    vbar.setValue(1000)
    for _ in range(4):
        _wheel(view)
        _at(view, 20)
    _at(view, _WHEEL_EASE_MS)
    assert vbar.value() - 1000 == pytest.approx(_step(view) * 4, abs=4)


def test_a_reversal_collapses_the_target_rather_than_unwinding_it(view):
    """Flick back mid-glide and the view turns round from where it *is* — it does not first travel
    the rest of the way to a destination the reader has changed their mind about."""
    vbar = view.verticalScrollBar()
    vbar.setValue(2000)
    _wheel(view, -1)                                 # down
    here = _at(view, 30)
    _wheel(view, +1)                                 # up, mid-glide
    assert view._glide_target == pytest.approx(here - _step(view), abs=2)
    _at(view, _WHEEL_EASE_MS)
    assert vbar.value() == pytest.approx(here - _step(view), abs=2)


def test_a_paging_key_cancels_the_glide(view):
    """**M91.4's lesson, applied to our own motion.** There a flywheel's events undid a keypress and
    the coast had to be inferred; a glide we own is simply stopped — `_park_coasting_wheel` does it
    for every deliberate navigation."""
    vbar = view.verticalScrollBar()
    vbar.setValue(1000)
    _wheel(view)
    _at(view, 30)
    view.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Space, NONE))
    assert view._glide_target is None
    assert not view._glide_timer.isActive()
    landed = vbar.value()
    _at(view, _WHEEL_EASE_MS)                        # a stray tick must not resurrect it
    assert vbar.value() == landed


def test_goto_page_cancels_the_glide(view):
    vbar = view.verticalScrollBar()
    vbar.setValue(500)
    _wheel(view)
    _at(view, 30)
    view.goto_page(2)
    assert view._glide_target is None
    assert not view._glide_timer.isActive()


def test_the_glide_clamps_at_the_document_ends(view):
    vbar = view.verticalScrollBar()
    vbar.setValue(vbar.maximum())
    _wheel(view)                                     # further down, with nowhere to go
    assert view._glide_target is None                # nothing to animate
    assert vbar.value() == vbar.maximum()


# ---- the preference --------------------------------------------------------------


def test_turning_smooth_scrolling_off_restores_the_instant_step(view):
    vbar = view.verticalScrollBar()
    view.smooth_scrolling = False
    vbar.setValue(1000)
    _wheel(view)
    assert vbar.value() - 1000 == pytest.approx(_step(view), abs=1)   # moved, with no tick at all
    assert not view._glide_timer.isActive()


def test_turning_it_off_mid_glide_stops_the_glide(view):
    vbar = view.verticalScrollBar()
    vbar.setValue(1000)
    _wheel(view)
    _at(view, 30)
    view.smooth_scrolling = False
    assert view._glide_target is None
    assert not view._glide_timer.isActive()


def test_the_menu_toggle_persists_the_choice(win, qapp):
    """View ▸ Smooth Scrolling is remembered app-wide, like Night Reading Mode."""
    assert win._a_smooth_scroll.isChecked()          # on by default
    win._a_smooth_scroll.trigger()
    assert not win.view.smooth_scrolling
    assert qapp.settings.get_pref("smooth_scrolling") is False
    win._a_smooth_scroll.trigger()
    assert win.view.smooth_scrolling
    assert qapp.settings.get_pref("smooth_scrolling") is True


# ---- the clock-driven property ---------------------------------------------------


def test_a_stalled_frame_does_not_stretch_the_glide(view):
    """**Why the animator reads a clock rather than counting frames.** A page rasterise blocks the
    UI thread for 4-48 ms (`_render_pixmap`), so ticks are not evenly spaced. Position must depend
    on *elapsed time*, which means a glide that loses a frame still lands on time and on target —
    where a per-frame-increment animator would stretch by exactly the time it lost."""
    vbar = view.verticalScrollBar()

    vbar.setValue(1000)
    _wheel(view)
    smooth = [_at(view, ms) for ms in (16, 32, 48, 64, 80, 96, 112, 128, 144, 160, 176, 200)]

    vbar.setValue(1000)
    view.stop_glide()
    _wheel(view)
    stalled = [_at(view, ms) for ms in (16, 96, 112, 128, 144, 160, 176, 200)]  # an 80 ms stall

    assert stalled[-1] == smooth[-1], "a stalled frame moved the landing point"
    assert stalled[1] == smooth[5], "position did not follow the clock across the stall"
