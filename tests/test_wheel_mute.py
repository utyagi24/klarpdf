"""M92.3 — the coast-mute is bounded, so the wheel can never be dead for long (`PLAN.md` §M92).

The defect, owner-reported 2026-07-31: *"if I scroll with mouse wheel really fast and press space bar
while pages are scrolling, the scrolling stops but the mouse wheel becomes unavailable to resume
scrolling for a long duration; I have to click around before it becomes responsive."*

**The mute was indefinitely renewable.** M91.4 mutes the wheel after a deliberate navigation so a
flywheel's coast cannot undo it, and lifts the mute once the wheel has been quiet for
`_WHEEL_QUIET_MS`. But a swallowed event still refreshed `_last_wheel_ts`, so the quiet window could
never elapse while events kept arriving — and the instinctive response to "scrolling stopped working"
is to scroll *more*, which held it open. `test_the_reader_can_scroll_their_way_out` is the report,
reproduced: it fires four seconds of continuous events and asserts the wheel comes back.

M92.3 keeps M91.4's gap test and adds two escapes that cannot be renewed — a **ceiling** measured
from when the mute was armed, and a **direction reversal**. The regression guard cuts the other way
too: `test_a_coast_inside_the_window_is_still_swallowed` is M91.4's defect, and must keep passing.

Both clocks are driven by the tests: `QWheelEvent.setTimestamp` for the gap test, and `_now_ms` for
the ceiling. They are deliberately different clocks in the implementation, so the tests keep them
separate too.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QWheelEvent

from app import PdfApp
from store.settings import Settings
from viewer.pdf_view import _WHEEL_MUTE_MAX_MS, _WHEEL_NOTCH, _WHEEL_QUIET_MS

NONE = Qt.KeyboardModifier.NoModifier


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def tall_pdf(tmp_path) -> str:
    """A 40-page document, because these tests need **room to scroll in both directions**.

    Not incidental setup. `conftest`'s 3-page `a_pdf` gives ~2 550 px of range, so a view parked
    near the bottom cannot move down — and a mute test whose wheel events could not have moved the
    view anyway proves nothing. Writing this file the first time, that mistake made three tests fail
    for the wrong reason and a fourth *pass* while asserting nothing. It is the same shape as the
    M91.4 trap it is testing: scrolling at a limit is a no-op, so the fault hides.
    """
    import pymupdf as fitz

    path = str(tmp_path / "tall.pdf")
    doc = fitz.open()
    for i in range(40):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 100), f"page {i + 1}", fontsize=24)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def view(qapp, tall_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    win = qapp.open_document(tall_pdf)
    win.resize(1000, 700)
    win.show()
    qapp.processEvents()
    v = win.view
    v.set_zoom(1.0)
    qapp.processEvents()
    # The glide is M92.2's business; this file is about the mute, so keep the wheel's effect
    # immediate and observable in the scrollbar.
    v.smooth_scrolling = False
    vbar = v.verticalScrollBar()
    vbar.setValue(vbar.maximum() // 2)          # room above and below
    assert vbar.maximum() > 20000, "fixture: not enough scroll range for these tests to mean anything"
    v._clock = 0.0
    v._now_ms = lambda: v._clock
    yield v
    win.undo_stack.setClean()
    win.close()


def _wheel(view, ts, notches=-1):
    """One detent stamped at ``ts`` (the platform clock the gap test reads)."""
    pt = QPointF(view.viewport().rect().center())
    d = QPoint(0, _WHEEL_NOTCH * notches)
    ev = QWheelEvent(pt, view.viewport().mapToGlobal(pt), QPoint(0, 0), d,
                     Qt.MouseButton.NoButton, NONE, Qt.ScrollPhase.NoScrollPhase, False)
    ev.setTimestamp(ts)
    view.wheelEvent(ev)


def _space(view):
    view.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Space, NONE))


def _moves(view, ts, notches=-1):
    before = view.verticalScrollBar().value()
    _wheel(view, ts, notches)
    return view.verticalScrollBar().value() != before


# ---- the report ------------------------------------------------------------------


def test_the_reader_can_scroll_their_way_out(view):
    """**The bug, exactly as reported.** Spin fast, press Space, then keep scrolling — which is what
    a reader does when nothing happens. Before M92.3 every one of these events was swallowed and the
    mute never lifted; the only escape was to stop touching the wheel."""
    ts = 100_000
    for _ in range(15):                                  # a fast spin: 20 ms apart
        _wheel(view, ts)
        ts += 20
    _space(view)
    assert view._wheel_muted

    dead = 0
    for _ in range(200):                                 # 4 seconds of trying, 20 ms apart
        ts += 20
        view._clock += 20
        if not _moves(view, ts):
            dead += 1
        else:
            break
    assert dead < 200, "the wheel never came back — the mute is still renewable"
    assert dead * 20 <= _WHEEL_MUTE_MAX_MS + 40, \
        f"took {dead * 20} ms to recover, ceiling is {_WHEEL_MUTE_MAX_MS:.0f} ms"


def test_the_ceiling_is_not_renewable_by_a_continuous_stream(view):
    """The precise property: continued events cannot push the ceiling back."""
    ts = 100_000
    _wheel(view, ts)
    _space(view)
    view._clock += _WHEEL_MUTE_MAX_MS + 1                 # ceiling reached...
    ts += 20                                             # ...while events keep arriving < 250 ms
    assert _moves(view, ts), "the ceiling did not lift the mute"


# ---- M91.4 must not regress ------------------------------------------------------


def test_a_coast_inside_the_window_is_still_swallowed(view):
    """**M91.4's defect, guarded.** Inside the ceiling, a coast is still ignored — otherwise the
    events a flywheel is still emitting would undo the deliberate step, which is the whole reason
    the mute exists."""
    ts = 100_000
    for _ in range(10):
        _wheel(view, ts)
        ts += 20
    _space(view)
    landed = view.verticalScrollBar().value()
    for _ in range(20):                                  # 400 ms of coast, inside the 800 ms ceiling
        ts += 20
        view._clock += 20
        _wheel(view, ts)
    assert view.verticalScrollBar().value() == landed, "the coast got through and moved the view"


def test_the_quiet_gap_still_lifts_the_mute(view):
    """M91.4's original escape, untouched — and the one that fires in ordinary use, because a
    reader who simply stops and scrolls again later is past the gap long before the ceiling."""
    _wheel(view, 100_000)
    _space(view)
    assert view._wheel_muted
    assert _moves(view, 100_000 + _WHEEL_QUIET_MS + 50)


# ---- the reversal escape ---------------------------------------------------------


def test_scrolling_the_other_way_lifts_the_mute_at_once(view):
    """A coast is the wheel losing speed, not changing its mind, so a reversal is unambiguously a
    fresh decision — and "go back" is exactly what a reader wants after a step surprised them."""
    ts = 100_000
    for _ in range(5):
        _wheel(view, ts, -1)                             # scrolling down
        ts += 20
    _space(view)
    ts += 20
    view._clock += 20
    assert not _moves(view, ts, -1), "same direction inside the window should still be swallowed"
    ts += 20
    view._clock += 20
    assert _moves(view, ts, +1), "a reversal should lift the mute immediately"


def test_a_fresh_mute_forgets_the_previous_direction(view):
    """Arming again resets the remembered direction: otherwise a mute armed after an upward coast
    would be lifted by the first *downward* event, which is the coast it was armed against."""
    ts = 100_000
    for _ in range(5):
        _wheel(view, ts, -1)
        ts += 20
    _space(view)
    ts += 20
    view._clock += 20
    _moves(view, ts, +1)                                 # reversal lifts it
    for _ in range(5):                                   # now coast upwards instead
        ts += 20
        _wheel(view, ts, +1)
    _space(view)
    ts += 20
    view._clock += 20
    assert not _moves(view, ts, +1), "the new mute inherited the old coast's direction"
