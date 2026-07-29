"""Pinch-zoom — M89.5 (`PLAN.md` §M89). Offscreen GUI.

Windows has been delivering `QNativeGestureEvent` / `ZoomNativeGesture` to this view all along and
nothing consumed it, so the gesture every touchpad user tries first did nothing. It now zooms
continuously, **anchored between the fingers** — the same pointer-anchored contract M80 gave
Ctrl+wheel, through the same `anchor_pos` seam.

**Validation limit, stated up front** (`PLAN.md` §M89): these tests exercise the handler from a
constructed event delivered to the viewport, which is exactly how Qt delivers the real one. What
they *cannot* certify is that Windows delivers it at all on a given machine — that needs a
precision touchpad and a hand. M89.5 ships flagged for hands-on validation, not reported green.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QNativeGestureEvent, QPointingDevice
from PySide6.QtWidgets import QApplication

from app import PdfApp
from model.virtual_document import VirtualDocument
from viewer.pdf_view import PdfView


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def view(qapp, tmp_path):
    """A laid-out, shown view over a tall single page — big enough that a zoom-in overflows the
    viewport both ways, which is what gives the anchor somewhere to hold."""
    path = str(tmp_path / "pinch.pdf")
    doc = fitz.open()
    doc.new_page(width=612, height=792)
    doc.save(path)
    doc.close()
    v = PdfView(VirtualDocument.from_path(path))
    v.resize(400, 400)
    v.show()
    qapp.processEvents()
    v.open_at({})
    qapp.processEvents()
    yield v
    v.deleteLater()


def _pinch(view, value, pos=QPoint(200, 200),
           kind=Qt.NativeGestureType.ZoomNativeGesture):
    """Deliver one native gesture **to the viewport**, which is where Windows sends it: a native
    gesture targets the widget under the fingers, and for a scroll area that is the viewport."""
    pt = QPointF(pos)
    event = QNativeGestureEvent(kind, QPointingDevice.primaryPointingDevice(), 2,
                                pt, pt, QPointF(view.viewport().mapToGlobal(pos)),
                                value, QPointF(0, 0))
    QApplication.sendEvent(view.viewport(), event)
    return event


def test_pinch_zooms_in_and_out(view, qapp):
    view.set_zoom(1.0)
    qapp.processEvents()
    _pinch(view, 0.5)
    qapp.processEvents()
    assert view.zoom > 1.0
    _pinch(view, -0.5)
    qapp.processEvents()
    assert view.zoom < 1.0


def test_the_value_is_an_increment_to_the_factor(view, qapp):
    """Qt reports the pinch as an *incremental change in the zoom factor*, so the new magnification
    is `zoom × (1 + value)` — already continuous, with nothing to accumulate to make it so."""
    view.set_zoom(1.0)
    qapp.processEvents()
    _pinch(view, 0.25)
    qapp.processEvents()
    assert view.zoom == pytest.approx(1.25)


def test_successive_pinch_events_compose(view, qapp):
    """Folding the pinch into the Ctrl+wheel accumulator is **exact**, not an approximation:
    `Π(1 + vᵢ)` and one power of the summed wheel-unit delta are the same number."""
    view.set_zoom(1.0)
    qapp.processEvents()
    for _ in range(4):
        _pinch(view, 0.1)
    qapp.processEvents()
    assert view.zoom == pytest.approx(1.1 ** 4)


def test_pinch_holds_the_point_between_the_fingers(view, qapp):
    """The anchor that makes it a *pointing* gesture — pinching open on a figure in the corner needs
    no follow-up scroll to find it again."""
    view.set_zoom(2.0)
    qapp.processEvents()
    hbar, vbar = view.horizontalScrollBar(), view.verticalScrollBar()
    if hbar.maximum() == hbar.minimum() or vbar.maximum() == vbar.minimum():
        pytest.skip("no scroll overflow in this offscreen environment")
    hbar.setValue((hbar.minimum() + hbar.maximum()) // 2)
    vbar.setValue((vbar.minimum() + vbar.maximum()) // 2)
    qapp.processEvents()

    pos = QPoint(90, 70)                    # off-centre: a centre anchor would visibly drift here
    before = view._anchor_at(pos)
    _pinch(view, 0.3, pos)
    qapp.processEvents()
    after = view._anchor_at(pos)

    assert view.zoom == pytest.approx(2.6)                    # it really did zoom...
    assert after[0] == before[0]                              # ...and held: same page
    assert after[1] == pytest.approx(before[1], abs=0.01)     # same spot across it
    assert after[2] == pytest.approx(before[2], abs=0.01)


def test_the_gesture_is_consumed(view, qapp):
    """Accepted so it cannot fall through to `QGraphicsView`, which would hand it to the scene."""
    event = _pinch(view, 0.2)
    qapp.processEvents()
    assert event.isAccepted()


def test_a_pinch_respects_the_zoom_limits(view, qapp):
    """The bounds are `set_zoom`'s, so the gesture inherits them rather than restating them."""
    from viewer.pdf_view import _MAX_ZOOM

    view.set_zoom(_MAX_ZOOM)
    qapp.processEvents()
    _pinch(view, 5.0)
    qapp.processEvents()
    assert view.zoom == pytest.approx(_MAX_ZOOM)


def test_other_native_gestures_are_left_alone(view, qapp):
    """Only the zoom gesture is ours. A rotate or swipe must fall through untouched — consuming
    every native gesture would silently break anything Qt does with them later."""
    view.set_zoom(1.0)
    qapp.processEvents()
    _pinch(view, 0.5, kind=Qt.NativeGestureType.RotateNativeGesture)
    qapp.processEvents()
    assert view.zoom == pytest.approx(1.0)


def test_pinch_is_inert_in_the_slideshow(view, qapp):
    """The mode's contract is one page per screen at Fit Page (M78), which is why Ctrl+wheel does
    not zoom there either. Consumed rather than ignored, so the gesture means one thing everywhere."""
    view.slideshow = True
    qapp.processEvents()
    before = view.zoom
    event = _pinch(view, 0.5)
    qapp.processEvents()
    assert view.zoom == pytest.approx(before)
    assert event.isAccepted()
    view.slideshow = False
    qapp.processEvents()


def test_a_degenerate_value_cannot_produce_a_non_positive_zoom(view, qapp):
    """`1 + value` is the magnification multiplier, so a value at or below −1 would ask for zero or
    negative magnification. Guarded rather than trusted."""
    view.set_zoom(1.0)
    qapp.processEvents()
    _pinch(view, -1.0)
    qapp.processEvents()
    assert view.zoom == pytest.approx(1.0)
