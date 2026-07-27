"""Zoom UX — zoomChanged signal, Actual Size reset, the % indicator widget (M11), and the
Ctrl+wheel pointer zoom (M80).

Headless (offscreen, set in conftest): the view's zoom is the single source of truth and the
widget mirrors it both ways without feedback loops.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent

from app import PdfApp
from model.virtual_document import VirtualDocument
from viewer.pdf_view import _WHEEL_NOTCH, _ZOOM_STEP, PdfView
from viewer.zoom_widget import ZoomWidget


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def view(qapp, a_pdf):
    return PdfView(VirtualDocument.from_path(a_pdf))


def _wheel(view, pos, units, ctrl=True):
    """Deliver one wheel event of ``units`` eighths-of-a-degree at viewport point ``pos``.
    ``_WHEEL_NOTCH`` units is one notched-mouse detent; smaller values are what a precision
    touchpad sends."""
    pt = QPointF(pos)
    delta = QPoint(0, units)
    mods = Qt.KeyboardModifier.ControlModifier if ctrl else Qt.KeyboardModifier.NoModifier
    view.wheelEvent(QWheelEvent(pt, view.viewport().mapToGlobal(pt), delta, delta,
                                Qt.MouseButton.NoButton, mods,
                                Qt.ScrollPhase.NoScrollPhase, False))


def test_zoom_changed_emitted_on_change(view):
    seen: list[float] = []
    view.zoomChanged.connect(seen.append)
    view.zoom_in()
    view.zoom_out()
    assert seen == pytest.approx([view.zoom * 1.25, view.zoom])  # one emit per actual change


def test_no_emit_when_zoom_unchanged(view):
    view.set_zoom(1.0)  # already 1.0
    seen: list[float] = []
    view.zoomChanged.connect(seen.append)
    view.set_zoom(1.0)  # no-op → no signal
    assert seen == []


def test_actual_size_resets_to_100(view):
    view.set_zoom(2.5)
    assert view.zoom == pytest.approx(2.5)
    view.actual_size()
    assert view.zoom == pytest.approx(1.0)


def test_widget_displays_live_percent(view):
    widget = ZoomWidget(view)
    view.set_zoom(1.5)
    assert widget.lineEdit().text() == "150%"
    view.actual_size()
    assert widget.lineEdit().text() == "100%"


def test_widget_typed_percent_applies(view):
    widget = ZoomWidget(view)
    widget.setEditText("200%")
    widget.lineEdit().editingFinished.emit()
    assert view.zoom == pytest.approx(2.0)


def test_widget_typed_without_percent_sign(view):
    widget = ZoomWidget(view)
    widget.setEditText("75")
    widget.lineEdit().editingFinished.emit()
    assert view.zoom == pytest.approx(0.75)


def test_widget_garbage_reverts_to_current(view):
    widget = ZoomWidget(view)
    view.set_zoom(1.25)
    widget.setEditText("nonsense")
    widget.lineEdit().editingFinished.emit()
    assert view.zoom == pytest.approx(1.25)  # unchanged
    assert widget.lineEdit().text() == "125%"  # restored


def test_widget_preset_selection_applies(view):
    widget = ZoomWidget(view)
    index = widget.findData(0.5)
    assert index >= 0
    widget.activated.emit(index)  # simulate the user picking 50%
    assert view.zoom == pytest.approx(0.5)


def test_widget_clamps_out_of_range(view):
    widget = ZoomWidget(view)
    widget.setEditText("5000%")
    widget.lineEdit().editingFinished.emit()
    assert view.zoom <= 8.0  # clamped to _MAX_ZOOM
    assert widget.lineEdit().text() == "800%"


def test_zoom_holds_the_viewport_centre(qapp, tmp_path):
    """A manual zoom keeps the content under the viewport centre centred — it must not snap to the
    page top and leave the horizontal scroll pinned at the left edge, which made the page drift
    toward the top-left corner further with every zoom-in step."""
    import pymupdf as fitz

    path = str(tmp_path / "big.pdf")
    doc = fitz.open()
    doc.new_page(width=612, height=792)
    doc.save(path)
    doc.close()

    view = PdfView(VirtualDocument.from_path(path))
    try:
        view.resize(260, 260)
        view.show()
        qapp.processEvents()
        view.open_at({})  # first frame lands at Fit Page — the page fits, so it is centred both ways
        qapp.processEvents()

        def centre_fraction() -> tuple[float, float]:
            c = view.mapToScene(view.viewport().rect().center())
            p = view._pages[view._current]
            return ((c.x() - p["x"]) / p["w"], (c.y() - p["y"]) / p["h"])

        before = centre_fraction()
        if not (0.4 < before[0] < 0.6 and 0.4 < before[1] < 0.6):
            pytest.skip("page not centred at fit in this offscreen environment")

        view.set_zoom(3.0)  # a big manual zoom: the page now overflows the viewport both ways
        qapp.processEvents()
        hbar, vbar = view.horizontalScrollBar(), view.verticalScrollBar()
        if hbar.maximum() == hbar.minimum() or vbar.maximum() == vbar.minimum():
            pytest.skip("no scroll overflow in this offscreen environment")

        after = centre_fraction()  # the same content point must still be under the centre
        assert after[0] == pytest.approx(before[0], abs=0.03)
        assert after[1] == pytest.approx(before[1], abs=0.03)
    finally:
        view.deleteLater()


# ---- Ctrl+wheel pointer zoom (M80) -------------------------------------------


@pytest.fixture
def shown_view(qapp, tmp_path):
    """A laid-out, shown view over a tall single page — big enough that a zoom-in overflows the
    viewport both ways, which is what gives the pointer anchor somewhere to hold."""
    import pymupdf as fitz

    path = str(tmp_path / "wheel.pdf")
    doc = fitz.open()
    doc.new_page(width=612, height=792)
    doc.save(path)
    doc.close()
    view = PdfView(VirtualDocument.from_path(path))
    view.resize(400, 400)
    view.show()
    qapp.processEvents()
    view.open_at({})
    qapp.processEvents()
    yield view
    view.deleteLater()


def test_ctrl_wheel_zooms_instead_of_scrolling(shown_view, qapp):
    """The reported gap: Ctrl+wheel fell through to QAbstractScrollArea and **scrolled** — the
    worst outcome, since the reader asked for zoom and got motion. One detent is one Ctrl+± step."""
    view = shown_view
    view.set_zoom(1.0)
    qapp.processEvents()
    before_scroll = view.verticalScrollBar().value()

    _wheel(view, QPoint(200, 200), _WHEEL_NOTCH)
    qapp.processEvents()
    assert view.zoom == pytest.approx(_ZOOM_STEP)

    _wheel(view, QPoint(200, 200), -_WHEEL_NOTCH)
    qapp.processEvents()
    assert view.zoom == pytest.approx(1.0)
    # Back at the starting zoom, the view is back where it started — the gesture zoomed, never paged.
    assert view.verticalScrollBar().value() == pytest.approx(before_scroll, abs=2)


def test_ctrl_wheel_holds_the_point_under_the_pointer(shown_view, qapp):
    """The anchor that makes it a *pointing* gesture: the content under the cursor stays under the
    cursor, so zooming into a corner needs no follow-up scroll. (Every other zoom entry point holds
    the viewport centre instead — there is no pointer behind a menu item.)"""
    view = shown_view
    view.set_zoom(2.0)  # already overflowing, so both scrollbars have room to hold the anchor
    qapp.processEvents()
    hbar, vbar = view.horizontalScrollBar(), view.verticalScrollBar()
    if hbar.maximum() == hbar.minimum() or vbar.maximum() == vbar.minimum():
        pytest.skip("no scroll overflow in this offscreen environment")
    hbar.setValue((hbar.minimum() + hbar.maximum()) // 2)
    vbar.setValue((vbar.minimum() + vbar.maximum()) // 2)
    qapp.processEvents()

    pos = QPoint(90, 70)  # deliberately off-centre: a centre anchor would visibly drift here
    before = view._anchor_at(pos)
    _wheel(view, pos, _WHEEL_NOTCH)
    qapp.processEvents()
    after = view._anchor_at(pos)

    assert after[0] == before[0]                              # same page
    assert after[1] == pytest.approx(before[1], abs=0.01)     # same spot across it
    assert after[2] == pytest.approx(before[2], abs=0.01)


def test_hi_res_ctrl_wheel_accumulates_to_one_step(shown_view, qapp):
    """A precision touchpad sends fractional deltas. The factor is continuous
    (``_ZOOM_STEP ** (delta / _WHEEL_NOTCH)``), so four quarter-detents land exactly where one
    whole detent does — instead of being swallowed as sub-detent noise."""
    view = shown_view
    view.set_zoom(1.0)
    qapp.processEvents()
    for _ in range(4):
        _wheel(view, QPoint(200, 200), _WHEEL_NOTCH // 4)
    qapp.processEvents()
    assert view.zoom == pytest.approx(_ZOOM_STEP)


def test_ctrl_wheel_at_the_zoom_limit_does_not_leak_into_a_scroll(shown_view, qapp):
    """Clamped at max zoom there is no zoom left to give — but the event must still be consumed,
    or the gesture degrades right back into the scroll it was replacing."""
    view = shown_view
    view.set_zoom(8.0)  # _MAX_ZOOM
    qapp.processEvents()
    before = view.verticalScrollBar().value()
    _wheel(view, QPoint(200, 200), _WHEEL_NOTCH)
    qapp.processEvents()
    assert view.zoom == pytest.approx(8.0)
    assert view.verticalScrollBar().value() == before


def test_plain_wheel_still_scrolls(shown_view, qapp):
    """Without Ctrl the wheel is untouched — it scrolls and never zooms."""
    view = shown_view
    view.set_zoom(2.0)
    qapp.processEvents()
    vbar = view.verticalScrollBar()
    if vbar.maximum() == vbar.minimum():
        pytest.skip("no vertical overflow in this offscreen environment")
    vbar.setValue((vbar.minimum() + vbar.maximum()) // 2)
    qapp.processEvents()
    before_zoom, before_scroll = view.zoom, vbar.value()

    _wheel(view, QPoint(200, 200), -_WHEEL_NOTCH, ctrl=False)
    qapp.processEvents()
    assert view.zoom == pytest.approx(before_zoom)
    assert vbar.value() != before_scroll


def test_ctrl_wheel_cancels_a_sticky_fit(shown_view, qapp):
    """It is a *manual* zoom, so it drops the sticky Fit Width/Page like every other manual zoom —
    otherwise the next window resize would snap the reader's chosen magnification away."""
    view = shown_view
    view.fit_width()
    qapp.processEvents()
    assert view._fit_mode == "width"
    _wheel(view, QPoint(200, 200), _WHEEL_NOTCH)
    qapp.processEvents()
    assert view._fit_mode is None


def test_fit_width_centres_current_page_when_another_page_is_rotated_wider(qapp, tmp_path):
    """Fit Width on the current (narrower) page must centre + fit *that* page even when another page
    is rotated 90°/270° and so is wider — the wider page overflows symmetrically (h-scrollable)
    instead of shoving the current page off to one side (where it fit neither page)."""
    import pymupdf as fitz

    path = str(tmp_path / "rot.pdf")
    doc = fitz.open()
    doc.new_page(width=612, height=792)  # page 0 — portrait
    doc.new_page(width=612, height=792)  # page 1 — portrait
    doc.save(path)
    doc.close()
    vdoc = VirtualDocument.from_path(path)
    vdoc.set_rotation(0, 90)  # page 0 displays landscape (792 wide) — now the widest page

    view = PdfView(vdoc)
    try:
        view.resize(480, 700)
        view.show()
        qapp.processEvents()
        view.open_at({})
        view._current = 1  # focus the non-rotated, narrower page
        view.fit_width()
        qapp.processEvents()
        hbar = view.horizontalScrollBar()
        if hbar.maximum() == hbar.minimum():
            pytest.skip("no horizontal overflow in this offscreen environment")
        mid = (hbar.minimum() + hbar.maximum()) // 2
        assert abs(hbar.value() - mid) <= 1  # current page centred (h-scroll at the midpoint)
    finally:
        view.deleteLater()
