"""Zoom render economics (PLAN.md §M86). Offscreen GUI.

Two fixes, one theme: **one geometry change should cost one rasterise.**

* **M86.1** — a single `set_zoom` ran `_render_visible` **three times** (profiler: `ncalls=3`):
  `_build_scene`'s own call, the anchor restore's call after it scrolls, and `_on_scroll` fired by
  the scrollbar write. Pre-existing, not M80's, so it taxed every zoom, fit, rotate and two-page
  toggle. A `_hold_render` block collapses them to one.
* **M86.2** — Ctrl+wheel can drive `set_zoom` 10–60× a second where a toolbar click drove it once.
  Deltas now accumulate and apply once a frame, so a burst is one rebuild instead of N.

The rest of the suite runs with the coalescer's interval patched to 0 (conftest `_instant_zoom`), so
a detent applies on the next event-loop pass. This file restores the real interval to test the
coalescing itself — the arrangement `test_search_perf.py` has with `_instant_search`.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest

import viewer.pdf_view
from app import PdfApp
from klarpdf.model.virtual_document import VirtualDocument
from viewer.pdf_view import _WHEEL_NOTCH, _ZOOM_COALESCE_MS, _ZOOM_STEP, PdfView


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def view(qapp, a_pdf):
    """A shown view — rendering is gated on the first show, so an unshown one never rasterises."""
    v = PdfView(VirtualDocument.from_path(a_pdf))
    v.resize(600, 700)
    v.show()
    qapp.processEvents()
    v.open_at({})
    qapp.processEvents()
    yield v
    v.deleteLater()


@pytest.fixture
def real_interval(monkeypatch):
    """Undo conftest's `_instant_zoom` — these tests are about the coalescing window itself."""
    monkeypatch.setattr(viewer.pdf_view, "_ZOOM_COALESCE_MS", _ZOOM_COALESCE_MS)


@pytest.fixture
def passes(monkeypatch):
    """Count rasterise passes — bodies of `_render_visible` that actually ran.

    Probed at `_update_current`, which the body calls once and which nothing else calls. Two more
    obvious probes are both useless here: counting *calls* to `_render_visible` cannot see the fix
    (the suppressed calls still happen — they return early, and the hold adds one of its own, so
    the total is unchanged), and filtering those calls on `_render_held` merely re-implements the
    guard under test, so it reports success even with the guard deleted. This one is downstream of
    the guard, so removing the guard makes the number go up.
    """
    n = {"count": 0}
    real = PdfView._update_current

    def counting(self, first, last):
        n["count"] += 1
        return real(self, first, last)

    monkeypatch.setattr(PdfView, "_update_current", counting)
    return n


@pytest.fixture
def rebuilds(monkeypatch):
    """Count scene rebuilds — the unit M86.2 is denominated in ("one rebuild instead of N")."""
    n = {"count": 0}
    real = PdfView._build_scene

    def counting(self):
        n["count"] += 1
        return real(self)

    monkeypatch.setattr(PdfView, "_build_scene", counting)
    return n


def _quiesce(qapp):
    """Drain deferred layout before a measurement.

    A zoom that pushes the content past the viewport makes a scrollbar appear, which resizes the
    viewport, and Qt delivers that resize **later**. That resize legitimately re-renders (the
    visible band really did change), but it belongs to the operation that caused it — left
    undrained it lands in the next measurement and reads as a pass the fix failed to remove.
    """
    for _ in range(3):
        qapp.processEvents()


def _wheel(view, units, pos=QPoint(200, 200)):
    pt = QPointF(pos)
    delta = QPoint(0, units)
    view.wheelEvent(QWheelEvent(pt, view.viewport().mapToGlobal(pt), delta, delta,
                                Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
                                Qt.ScrollPhase.NoScrollPhase, False))


def _settle(qapp, view):
    """Let a pending coalesced zoom land."""
    QTest.qWait(_ZOOM_COALESCE_MS * 4)
    qapp.processEvents()
    assert not view._zoom_timer.isActive()


# ---- M86.1: one geometry change, one rasterise -------------------------------


def test_a_zoom_rasterises_once(view, qapp, passes):
    """The headline: three passes down to one. Measured across the call itself — the deferred
    resize a new scrollbar causes is a separate event, not part of the zoom."""
    _quiesce(qapp)
    passes["count"] = 0
    view.set_zoom(1.6)
    assert passes["count"] == 1


def test_fit_rotate_and_two_page_each_rasterise_once(view, qapp, passes):
    """Not just zoom — the same triple taxed every geometry change (M86.1 is pre-existing work)."""
    for label, act in (("fit_width", view.fit_width),
                       ("fit_page", view.fit_page),
                       ("rotate", lambda: view.rotate_view(90)),
                       ("two-page", lambda: view.set_page_layout("facing"))):
        _quiesce(qapp)
        passes["count"] = 0
        act()
        assert passes["count"] == 1, f"{label} rasterised {passes['count']} times"


def test_nested_holds_collapse_to_one(view, qapp, passes):
    """`set_page_layout` rebuilds and then re-fits, and the re-fit zooms — three nested rebuilds
    that must still rasterise once, or the fix trades three passes for two."""
    view.fit_width()                       # arm the sticky fit so the layout switch re-fits
    _quiesce(qapp)
    passes["count"] = 0
    view.set_page_layout("facing")
    assert passes["count"] == 1


def test_a_resize_that_refits_still_rasterises_once(view, qapp, passes):
    """A sticky fit makes a resize zoom, so the resize's hold and the zoom's hold nest. Two holds,
    one pass — a drag-resize is a stream of these."""
    view.fit_width()
    _quiesce(qapp)
    passes["count"] = 0
    view.resize(view.width() + 120, view.height())
    _quiesce(qapp)
    assert passes["count"] == 1


def test_the_visible_band_is_actually_rendered_after_a_zoom(view, qapp):
    """The pass that survives has to be the *right* one: the band on screen carries pixmaps.

    Collapsing to one pass is only correct if it runs after the scroll has landed — keeping an
    earlier pass instead would rasterise a band the view is about to move off, and this is what
    would catch it.
    """
    view.set_zoom(1.5)
    qapp.processEvents()
    first, last = view._visible_range()
    assert not view._pages[first]["pix"].pixmap().isNull()
    assert not view._pages[last]["pix"].pixmap().isNull()


def test_a_hold_that_raises_still_unwinds(view, qapp, passes):
    """The counter must not latch. A leaked hold would leave the view permanently un-rendering —
    a blank page for the rest of the session, which is worse than the cost it saves."""
    with pytest.raises(RuntimeError):
        with view._hold_render():
            raise RuntimeError("boom")
    assert view._render_held == 0

    _quiesce(qapp)
    passes["count"] = 0
    view.set_zoom(1.3)
    assert passes["count"] == 1


# ---- M86.2: a burst is one rebuild per frame ---------------------------------


def test_a_burst_applies_once_not_per_event(view, qapp, rebuilds, real_interval):
    """40 fine touchpad deltas inside one frame: one zoom, one rebuild — not 40."""
    view.set_zoom(1.0)
    _quiesce(qapp)
    rebuilds["count"] = 0
    zooms: list[float] = []
    view.zoomChanged.connect(zooms.append)

    for _ in range(40):
        _wheel(view, _WHEEL_NOTCH // 8)
    _settle(qapp, view)

    assert len(zooms) == 1
    assert rebuilds["count"] == 1


def test_the_coalesced_zoom_equals_applying_each_event(view, qapp, real_interval):
    """The plan's acceptance test: coalescing changes *when* the work happens, never where it
    lands. Driven both ways against the same view, from the same starting zoom."""
    deltas = [_WHEEL_NOTCH // 4] * 6 + [_WHEEL_NOTCH // 3] * 3

    view.set_zoom(1.0)
    qapp.processEvents()
    for d in deltas:                       # coalesced: no event loop between events
        _wheel(view, d)
    _settle(qapp, view)
    coalesced = view.zoom

    view.set_zoom(1.0)
    qapp.processEvents()
    for d in deltas:                       # one at a time: settle after each
        _wheel(view, d)
        _settle(qapp, view)
    individually = view.zoom

    assert coalesced == pytest.approx(individually)
    assert coalesced == pytest.approx(_ZOOM_STEP ** (sum(deltas) / _WHEEL_NOTCH))


def test_a_single_detent_still_applies(view, qapp, real_interval):
    """One frame of latency, not a swallowed event — a lone detent must still zoom."""
    view.set_zoom(1.0)
    qapp.processEvents()
    _wheel(view, _WHEEL_NOTCH)
    _settle(qapp, view)
    assert view.zoom == pytest.approx(_ZOOM_STEP)


def test_the_accumulator_throttles_rather_than_debounces(view, qapp, real_interval):
    """A sustained gesture must keep painting. Debouncing (restarting the timer per event) would
    hold the zoom back for as long as the fingers kept moving — the view would sit frozen through
    the whole gesture and jump at the end."""
    view.set_zoom(1.0)
    qapp.processEvents()
    _wheel(view, _WHEEL_NOTCH // 8)
    started = view._zoom_timer.remainingTime()
    _wheel(view, _WHEEL_NOTCH // 8)
    assert view._zoom_timer.remainingTime() <= started   # not pushed back by the second event


def test_the_pending_zoom_is_anchored_on_the_latest_pointer(view, qapp, real_interval):
    """The flush anchors where the pointer *is*, not where the gesture began."""
    view.set_zoom(2.0)
    qapp.processEvents()
    _wheel(view, _WHEEL_NOTCH // 4, pos=QPoint(50, 50))
    _wheel(view, _WHEEL_NOTCH // 4, pos=QPoint(300, 400))
    assert view._zoom_anchor == QPoint(300, 400)
    _settle(qapp, view)
    assert view._zoom_anchor is None       # consumed, so the next gesture starts clean


def test_a_wheel_zoom_still_cancels_a_sticky_fit(view, qapp, real_interval):
    """The deferral must not lose the side effects of the zoom it defers."""
    view.fit_width()
    qapp.processEvents()
    assert view._fit_mode == "width"
    _wheel(view, _WHEEL_NOTCH)
    _settle(qapp, view)
    assert view._fit_mode is None
