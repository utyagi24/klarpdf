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


def test_per_page_rotation_swaps_only_that_page(qapp, vdoc):
    view = PdfView(vdoc)
    (w0, h0), (w1, h1) = (view._pages[0]["w"], view._pages[0]["h"]), (view._pages[1]["w"], view._pages[1]["h"])
    vdoc.set_rotation(0, 90)  # rotate page 0 only (a per-page override)
    view.reload()
    assert (round(view._pages[0]["w"], 3), round(view._pages[0]["h"], 3)) == (round(h0, 3), round(w0, 3))
    assert (round(view._pages[1]["w"], 3), round(view._pages[1]["h"], 3)) == (round(w1, 3), round(h1, 3))
    assert view._render_pixmap(0) is not None  # the rotated page still renders


def test_thumbnail_panel_accepts_page_drops(qapp, vdoc):
    """Regression: item-view drops land on the VIEWPORT — it must accept them or the drag shows a
    blocked cursor and never reaches our handlers — and our MIME must drive dragMove/drop."""
    import json

    from PySide6.QtCore import QByteArray, QMimeData, QPoint, QPointF, Qt
    from PySide6.QtGui import QDragMoveEvent, QDropEvent

    from organize.thumbnail_panel import _PAGES_MIME

    panel = ThumbnailPanel(vdoc)
    panel.source_key = "doc"
    assert panel.viewport().acceptDrops() is True

    mime = QMimeData()
    mime.setData(_PAGES_MIME, QByteArray(json.dumps({"source": "other", "rows": [1]}).encode()))
    acts = Qt.DropAction.MoveAction | Qt.DropAction.CopyAction
    btn, mod = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier

    move = QDragMoveEvent(QPoint(5, 5), acts, mime, btn, mod)
    panel.dragMoveEvent(move)
    assert move.isAccepted()  # cursor reads "droppable", not blocked

    got = []
    panel.pagesDropped.connect(lambda s, r, b: got.append((s, r, b)))
    panel.dropEvent(QDropEvent(QPointF(5, 5), acts, mime, btn, mod))
    assert got and got[0][0] == "other" and got[0][1] == [1]


def test_drop_slot_chosen_by_vertical_position(qapp, vdoc):
    """Drop above the first thumbnail → index 0; below the last → append (single vertical column)."""
    import json

    from PySide6.QtCore import QByteArray, QMimeData, QPointF, Qt
    from PySide6.QtGui import QDropEvent

    from organize.thumbnail_panel import _PAGES_MIME

    panel = ThumbnailPanel(vdoc)
    panel.resize(180, 800)  # narrow → one thumbnail per row
    panel.show()
    qapp.processEvents()

    def before_at(y):
        mime = QMimeData()
        mime.setData(_PAGES_MIME, QByteArray(json.dumps({"source": None, "rows": [0]}).encode()))
        ev = QDropEvent(QPointF(10.0, float(y)), Qt.DropAction.MoveAction, mime,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        return panel._drop_before_index(ev)

    top = panel.visualItemRect(panel.item(0)).top()
    bottom = panel.visualItemRect(panel.item(vdoc.page_count - 1)).bottom()
    assert before_at(top + 1) == 0                    # above the first page's centre → before all
    assert before_at(bottom + 50) == vdoc.page_count  # below the last page → append


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


def test_toolbar_grouped_with_feedback(qapp, a_pdf, tmp_path):
    """Toolbar is split into functional groups (separators) and gives hover/press feedback."""
    from PySide6.QtWidgets import QToolBar

    qapp.settings = Settings(tmp_path / "view_state.json")
    w = qapp.open_document(a_pdf)
    bar = next(b for b in w.findChildren(QToolBar) if b.windowTitle() == "Main")
    separators = [a for a in bar.actions() if a.isSeparator()]
    assert len(separators) == 7  # eight functional groups → seven dividers
    style = bar.styleSheet()
    assert ":hover" in style and ":pressed" in style  # visible click feedback
    assert "separator" in style  # group spacing
    w.close()


def test_pages_dock_locked_and_toggleable(qapp, a_pdf, tmp_path):
    """The Pages sidebar must stay docked (not floatable/movable) and be hide/show-able.

    Regression: it was a default QDockWidget — could tear off into its own window and, once
    closed, had no way back.
    """
    from PySide6.QtWidgets import QDockWidget, QToolBar

    Feat = QDockWidget.DockWidgetFeature
    qapp.settings = Settings(tmp_path / "view_state.json")
    w = qapp.open_document(a_pdf)
    dock = w.pages_dock
    feats = dock.features()
    assert feats & Feat.DockWidgetClosable          # can be hidden
    assert not (feats & Feat.DockWidgetFloatable)    # cannot become its own window
    assert not (feats & Feat.DockWidgetMovable)      # cannot be dragged around

    toggle = dock.toggleViewAction()
    bar = next(b for b in w.findChildren(QToolBar) if b.windowTitle() == "Main")
    assert toggle in bar.actions()  # dedicated toolbar button wired to the same toggle
    assert toggle.isChecked()  # visible after open
    toggle.trigger()
    qapp.processEvents()
    assert not dock.isVisible() and not toggle.isChecked()  # hidden
    toggle.trigger()
    qapp.processEvents()
    assert dock.isVisible() and toggle.isChecked()  # restored
    w.close()


def test_open_recent_menu_populates(qapp, a_pdf, b_pdf, tmp_path):
    """Opening documents fills the File ▸ Open Recent submenu, newest first, across windows."""
    import os

    qapp.settings = Settings(tmp_path / "view_state.json")
    w1 = qapp.open_document(a_pdf)
    w2 = qapp.open_document(b_pdf)
    w2._populate_recent_menu()  # normally driven by aboutToShow
    labels = [a.text() for a in w2._recent_menu.actions() if a.text() and a.isEnabled()]
    assert any(os.path.basename(b_pdf) in t for t in labels)
    assert any(os.path.basename(a_pdf) in t for t in labels)
    # b was opened last → it appears before a in the list.
    b_pos = next(i for i, t in enumerate(labels) if os.path.basename(b_pdf) in t)
    a_pos = next(i for i, t in enumerate(labels) if os.path.basename(a_pdf) in t)
    assert b_pos < a_pos
    w1.close()
    w2.close()


def test_app_open_document_dedupes(qapp, a_pdf, b_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "view_state.json")
    w1 = qapp.open_document(a_pdf)
    w1_again = qapp.open_document(a_pdf)
    assert w1 is w1_again  # one window per document
    w2 = qapp.open_document(b_pdf)
    assert w2 is not w1
    w1.close()
    w2.close()
