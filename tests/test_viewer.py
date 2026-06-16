"""Offscreen GUI smoke tests for the M2 viewer (constructs + renders, no real display).

These run under ``QT_QPA_PLATFORM=offscreen`` (set in conftest). They prove the view/thumbnail/
window wiring constructs and renders without error and that the zoom/fit/rotate/navigation math
behaves; pixel-level visual fidelity is verified manually via WSLg.
"""

from __future__ import annotations

import pytest

from app import PdfApp
from model.virtual_document import VirtualDocument
from organize.thumbnail_panel import ThumbnailPanel
from store.settings import Settings
from viewer.pdf_view import PdfView


@pytest.fixture(scope="session")
def qapp():
    app = PdfApp.instance() or PdfApp([])
    yield app


@pytest.fixture
def vdoc(a_pdf):
    return VirtualDocument.from_path(a_pdf)


def test_view_lays_out_all_pages(qapp, vdoc):
    view = PdfView(vdoc)
    assert len(view._pages) == vdoc.page_count == 3
    # Pages stack downward with no overlap.
    ys = [p["y"] for p in view._pages]
    assert ys == sorted(ys)
    assert view._pages[1]["y"] >= view._pages[0]["y"] + view._pages[0]["h"]


def test_pages_positioned_at_distinct_scene_coords(qapp, vdoc):
    # Regression: every page's pixmap item must sit at its own scene position, not piled at
    # the origin (which made all pages but the first look blank in the main view).
    view = PdfView(vdoc)
    view._render_visible()
    scene_ys = []
    for i, p in enumerate(view._pages):
        sp = p["pix"].scenePos()
        assert (sp.x(), sp.y()) == pytest.approx((p["x"], p["y"]))
        if i > 0:
            assert sp.y() > 0  # not stacked at the top-left origin
        scene_ys.append(sp.y())
    assert scene_ys == sorted(scene_ys)
    assert len(set(scene_ys)) == len(scene_ys)  # all distinct


def test_render_pixmap_non_null(qapp, vdoc):
    view = PdfView(vdoc)
    pm = view._render_pixmap(0)
    assert pm is not None and not pm.isNull()
    assert pm.width() > 0 and pm.height() > 0


def test_zoom_changes_and_caches(qapp, vdoc):
    view = PdfView(vdoc)
    before = view.zoom
    view.zoom_in()
    assert view.zoom > before
    view.zoom_out()
    assert view.zoom == pytest.approx(before)


def test_fit_width_sets_positive_zoom(qapp, vdoc):
    view = PdfView(vdoc)
    view.resize(800, 600)
    view.show()
    view.fit_width()
    assert view.zoom > 0


def test_rotate_swaps_page_dimensions(qapp, vdoc):
    view = PdfView(vdoc)
    w0, h0 = view._pages[0]["w"], view._pages[0]["h"]
    view.rotate_view(90)
    assert view.rotation == 90
    w1, h1 = view._pages[0]["w"], view._pages[0]["h"]
    assert (round(w1, 3), round(h1, 3)) == (round(h0, 3), round(w0, 3))


def test_view_state_roundtrip(qapp, vdoc):
    view = PdfView(vdoc)
    view.set_zoom(1.7)
    view.rotate_view(180)
    state = view.view_state()
    assert state["zoom"] == pytest.approx(1.7)
    assert state["rotation"] == 180

    fresh = PdfView(VirtualDocument.from_path(vdoc.path))
    fresh.apply_state(state)
    assert fresh.zoom == pytest.approx(1.7)
    assert fresh.rotation == 180


def test_thumbnail_panel_jump_and_highlight(qapp, vdoc):
    panel = ThumbnailPanel(vdoc)
    assert panel.count() == 3

    seen: list[int] = []
    panel.pageActivated.connect(seen.append)

    panel.setCurrentRow(2)  # user click → emits
    assert seen == [2]

    panel.set_current(0)  # programmatic highlight → must NOT emit
    assert seen == [2]
    assert panel.currentRow() == 0


def test_app_open_document_dedupes(qapp, a_pdf, b_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "view_state.json")
    w1 = qapp.open_document(a_pdf)
    w1_again = qapp.open_document(a_pdf)
    assert w1 is w1_again  # one window per document
    w2 = qapp.open_document(b_pdf)
    assert w2 is not w1
    w1.close()
    w2.close()
