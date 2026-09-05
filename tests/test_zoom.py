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
from klarpdf.model.virtual_document import VirtualDocument
from viewer.pdf_view import _MAX_ZOOM, _MIN_ZOOM, _WHEEL_NOTCH, _ZOOM_STEP, PdfView
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
    """Written against the constants, not the numbers: M88.6 re-based the range from 10%–800% to
    25%–500% and a literal here would have to be chased each time the bounds move."""
    widget = ZoomWidget(view)
    widget.setEditText("5000%")
    widget.lineEdit().editingFinished.emit()
    assert view.zoom == pytest.approx(_MAX_ZOOM)
    assert widget.lineEdit().text() == f"{round(_MAX_ZOOM * 100)}%"

    widget.setEditText("1%")
    widget.lineEdit().editingFinished.emit()
    assert view.zoom == pytest.approx(_MIN_ZOOM)
    assert widget.lineEdit().text() == f"{round(_MIN_ZOOM * 100)}%"


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
    view.set_zoom(_MAX_ZOOM)
    qapp.processEvents()
    before = view.verticalScrollBar().value()
    _wheel(view, QPoint(200, 200), _WHEEL_NOTCH)
    qapp.processEvents()
    assert view.zoom == pytest.approx(_MAX_ZOOM)
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


# ---- M88.6: the zoom range is 25%-500% ---------------------------------------


def test_the_range_is_twenty_five_to_five_hundred_percent():
    """The bounds themselves. Sequenced after M88.1 by the plan, because the DPI correction shifts
    what every percentage draws — deciding before it would have meant deciding twice."""
    assert (_MIN_ZOOM, _MAX_ZOOM) == (0.25, 5.0)


def test_stepping_clamps_at_both_ends(view):
    """Ctrl+/Ctrl- walk to the bounds and stop there, rather than running away or wrapping."""
    for _ in range(40):
        view.zoom_in()
    assert view.zoom == pytest.approx(_MAX_ZOOM)
    for _ in range(40):
        view.zoom_out()
    assert view.zoom == pytest.approx(_MIN_ZOOM)


def test_the_preset_list_agrees_with_the_range(view):
    """No preset may sit outside the bounds: picking one would silently clamp, and the widget would
    then show a different number from the item the reader just clicked. Both ends are present, so
    the limits are reachable from the list without knowing to type them."""
    widget = ZoomWidget(view)
    factors = [widget.itemData(i) for i in range(widget.count())]
    assert all(_MIN_ZOOM <= f <= _MAX_ZOOM for f in factors), factors
    assert min(factors) == pytest.approx(_MIN_ZOOM) and max(factors) == pytest.approx(_MAX_ZOOM)
    for i, factor in enumerate(factors):          # every preset applies exactly as labelled
        widget.activated.emit(i)
        assert view.zoom == pytest.approx(factor)
        assert widget.lineEdit().text() == f"{round(factor * 100)}%"


def test_a_fit_may_go_below_the_floor_because_a_fit_must_fit(qapp, tmp_path):
    """The exception in `_clamp_zoom`, and the reason it exists.

    Fit Page on an A0 sheet in a 1100x850 window wants ~17%. Measured while building M88.6: a hard
    25% floor makes the page **overshoot the viewport** in both portrait and landscape — a "Fit
    Page" that does not fit the page. The floor bounds the magnification a reader *asks* for, not
    one the app *computed* in order to satisfy "show me the whole page".
    """
    import pymupdf as fitz

    path = str(tmp_path / "a0.pdf")
    doc = fitz.open()
    for _ in range(2):
        doc.new_page(width=2384, height=3370)   # ISO A0 in points
    doc.save(path)
    doc.close()

    view = PdfView(VirtualDocument.from_path(path))
    try:
        view.resize(1100, 850)
        view.show()
        qapp.processEvents()
        view.open_at({})
        view.fit_page()
        qapp.processEvents()
        assert view.zoom < _MIN_ZOOM, "the fit was clamped and can no longer fit"
        page = view._pages[view.current_page]
        assert page["h"] <= view.viewport().height() + 1
        assert page["w"] <= view.viewport().width() + 1
    finally:
        view.deleteLater()


def test_below_the_floor_the_reader_can_still_get_back_to_the_fit(qapp, tmp_path):
    """Why the floor is derived from Fit Page and not from the current zoom.

    The first attempt held the floor at ``min(_MIN_ZOOM, current)`` — "no step may zoom you *in*",
    which is true but traps: zoom in one step from a 17% A0 fit and the floor follows you up to
    21%, so stepping back out to the fit becomes impossible and the reader cannot recover the view
    they started from. Zooming out must always be able to reach the whole page.
    """
    import pymupdf as fitz

    path = str(tmp_path / "a0.pdf")
    doc = fitz.open()
    for _ in range(2):
        doc.new_page(width=2384, height=3370)
    doc.save(path)
    doc.close()

    view = PdfView(VirtualDocument.from_path(path))
    try:
        view.resize(1100, 850)
        view.show()
        qapp.processEvents()
        view.open_at({})
        view.fit_page()
        qapp.processEvents()
        fitted = view.zoom
        assert fitted < _MIN_ZOOM

        view.zoom_out()
        assert view.zoom == pytest.approx(fitted), "a zoom-out moved the page the wrong way"

        view.zoom_in()                       # step in, then walk all the way back out
        assert view.zoom > fitted
        for _ in range(20):
            view.zoom_out()
        assert view.zoom == pytest.approx(fitted), "the reader cannot get back to the fit"
        page = view._pages[view.current_page]
        assert page["h"] <= view.viewport().height() + 1   # and it really is the whole page
    finally:
        view.deleteLater()


def test_an_ordinary_page_still_stops_at_the_floor(qapp, tmp_path):
    """The Fit-Page-derived floor must not become a licence to zoom out past 25% on a normal
    document: Letter fits well above the floor, so 25% binds exactly as written."""
    import pymupdf as fitz

    path = str(tmp_path / "letter.pdf")
    doc = fitz.open()
    for _ in range(2):
        doc.new_page(width=612, height=792)
    doc.save(path)
    doc.close()

    view = PdfView(VirtualDocument.from_path(path))
    try:
        view.resize(1100, 850)
        view.show()
        qapp.processEvents()
        view.open_at({})
        assert view._fit_zoom(fit_height=True) > _MIN_ZOOM   # the fit is not what binds here
        for _ in range(40):
            view.zoom_out()
        assert view.zoom == pytest.approx(_MIN_ZOOM)
    finally:
        view.deleteLater()


def test_a_saved_zoom_outside_the_new_range_falls_back_cleanly(view):
    """State files predate M88.6 and can hold 10% or 800%. `apply_state` range-checks, so an
    out-of-range value is ignored and the view keeps the zoom it had — no crash, no silent clamp
    to a magnification the reader never chose."""
    view.set_zoom(1.0)
    view.apply_state({"page": 0, "zoom": 0.1, "rotation": 0})    # the old floor
    assert view.zoom == pytest.approx(1.0)
    view.apply_state({"page": 0, "zoom": 8.0, "rotation": 0})    # the old ceiling
    assert view.zoom == pytest.approx(1.0)
    view.apply_state({"page": 0, "zoom": 2.0, "rotation": 0})    # in range → honoured
    assert view.zoom == pytest.approx(2.0)
