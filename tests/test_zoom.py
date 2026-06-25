"""Zoom UX — zoomChanged signal, Actual Size reset, and the % indicator widget (M11).

Headless (offscreen, set in conftest): the view's zoom is the single source of truth and the
widget mirrors it both ways without feedback loops.
"""

from __future__ import annotations

import pytest

from app import PdfApp
from model.virtual_document import VirtualDocument
from viewer.pdf_view import PdfView
from viewer.zoom_widget import ZoomWidget


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def view(qapp, a_pdf):
    return PdfView(VirtualDocument.from_path(a_pdf))


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
