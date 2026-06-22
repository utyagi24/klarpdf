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


@pytest.mark.parametrize("total", [0, 90, 180, 270])
def test_box_display_roundtrip(total):
    # Pure geometry: a box mapped into the rotated display space and a point mapped back must
    # land at the same place (so overlays align on a rotated page).
    W, H = 600.0, 800.0
    box = (10.0, 20.0, 110.0, 60.0)
    d = PdfView._box_to_display(W, H, total, box)
    cx, cy = (d[0] + d[2]) / 2, (d[1] + d[3]) / 2
    sx, sy = PdfView._point_to_source(W, H, total, cx, cy)
    assert sx == pytest.approx((box[0] + box[2]) / 2)
    assert sy == pytest.approx((box[1] + box[3]) / 2)


def test_overlay_box_maps_through_per_page_rotation(qapp, vdoc):
    # Regression: a box's scene rect, mapped back via page_and_local_at, returns the unrotated
    # source point — even when the page carries a rotation override (highlight/form alignment).
    view = PdfView(vdoc)
    box = (72.0, 72.0, 172.0, 92.0)
    vdoc.set_rotation(0, 90)
    view.reload()
    page_index, local = view.page_and_local_at(view.scene_rect_for_box(0, box).center())
    assert page_index == 0
    assert local.x() == pytest.approx((box[0] + box[2]) / 2, abs=2)
    assert local.y() == pytest.approx((box[1] + box[3]) / 2, abs=2)


def test_overlay_aligns_on_baked_in_rotation(qapp, tmp_path):
    # Regression: a saved-then-reopened rotated page has /Rotate baked into native rotation (no
    # override). PyMuPDF reports word/widget coords in the *MediaBox* (unrotated) space while the
    # page renders rotated, so overlays must rotate boxes by the page's own /Rotate.
    import pymupdf as fitz

    path = str(tmp_path / "baked.pdf")
    doc = fitz.open()
    page = doc.new_page(width=400, height=600)
    page.insert_text((50, 100), "HELLO", fontsize=14)
    page.set_rotation(90)
    doc.save(path)
    doc.close()

    vd = VirtualDocument.from_path(path)
    view = PdfView(vd)
    assert vd.sources[vd.ordered[0].source_id][0].rotation == 90  # baked in, no override
    assert vd.ordered[0].rotation_override is None
    assert view._pages[0]["w"] > view._pages[0]["h"]  # displays landscape

    box = (50.0, 80.0, 150.0, 110.0)  # a MediaBox-space box
    rect = view.scene_rect_for_box(0, box)
    p = view._pages[0]
    assert p["x"] <= rect.center().x() <= p["x"] + p["w"]  # lands on the (landscape) page
    assert p["y"] <= rect.center().y() <= p["y"] + p["h"]
    page_index, local = view.page_and_local_at(rect.center())
    assert page_index == 0
    assert local.x() == pytest.approx((box[0] + box[2]) / 2, abs=2)  # round-trips to MediaBox coords
    assert local.y() == pytest.approx((box[1] + box[3]) / 2, abs=2)


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
    assert len(separators) == 8  # nine functional groups → eight dividers
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
    assert not dock.isVisible() and not toggle.isChecked()  # hidden by default
    toggle.trigger()
    qapp.processEvents()
    assert dock.isVisible() and toggle.isChecked()  # shown
    toggle.trigger()
    qapp.processEvents()
    assert not dock.isVisible() and not toggle.isChecked()  # hidden again
    w.close()


def test_sidebar_visibility_is_remembered(qapp, a_pdf, tmp_path):
    """Showing the sidebar persists app-wide, so a new window opens with it shown."""
    qapp.settings = Settings(tmp_path / "view_state.json")
    w1 = qapp.open_document(a_pdf)
    assert not w1.pages_dock.isVisible()              # hidden by default
    w1.pages_dock.toggleViewAction().trigger()        # user shows it → remembered
    assert qapp.settings.get_pref("sidebar_visible") is True
    w1.close()
    w2 = qapp.open_document(a_pdf)
    assert w2.pages_dock.isVisible()                  # new window honours the remembered choice
    w2.close()


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


# ---- window opens full-height, horizontally centred, at Fit Page ------------


def test_open_geometry_full_height_and_horizontally_centered():
    from PySide6.QtCore import QRect

    from main_window import MainWindow

    geo = MainWindow._open_geometry(QRect(0, 0, 1366, 768), 1000, frame_w=16, frame_h=39, title_bar=31)
    assert geo.width() == 1000          # default width (fits) — NOT full width
    assert geo.height() == 768 - 39     # full available height minus the frame
    assert geo.x() == (1366 - 1000) // 2  # horizontally centred (183)
    assert geo.y() == 31                # content dropped by the title bar → frame top at the screen top


def test_open_geometry_clamps_width_on_a_narrow_offset_screen():
    from PySide6.QtCore import QRect

    from main_window import MainWindow

    geo = MainWindow._open_geometry(QRect(100, 50, 800, 600), 1000, frame_w=16, frame_h=39, title_bar=31)
    assert geo.width() == 800 - 16      # width clamped to the screen (minus side borders)
    assert geo.height() == 600 - 39     # full height of the (smaller, offset) screen
    assert geo.x() == 100 + (800 - 784) // 2  # centred within the offset screen (108)
    assert geo.y() == 50 + 31           # offset down by the title bar from the screen top


def test_document_opens_at_fit_page(qapp, a_pdf, tmp_path):
    """The whole page fits the viewport on open (Fit Page) — fit-width would overflow vertically."""
    qapp.settings = Settings(tmp_path / "view_state.json")
    w = qapp.open_document(a_pdf)
    qapp.processEvents()
    v = w.view
    _, page_h = v._natural_size(v.current_page)
    assert page_h * v.zoom <= v.viewport().height() + 2
    w.close()


def test_open_places_window_on_the_screen_under_the_cursor(qapp, a_pdf, tmp_path, monkeypatch):
    """Regression (multi-monitor): a window is placed on the screen under the cursor — where the
    user double-clicked in Explorer — not always the primary screen."""
    from PySide6.QtCore import QRect

    import main_window as mw

    qapp.settings = Settings(tmp_path / "vs.json")
    second_monitor = QRect(2000, 100, 1920, 1040)  # a screen offset to the right of the primary

    class FakeScreen:
        def availableGeometry(self):
            return second_monitor

    monkeypatch.setattr(mw.QGuiApplication, "screenAt", staticmethod(lambda pos: FakeScreen()))
    win = mw.MainWindow(qapp, a_pdf, qapp.settings)
    try:
        expected = mw.MainWindow._open_geometry(second_monitor, 1000, 16, 39, 31)
        assert win.geometry() == expected  # full-height, centred *within the cursor's screen*
    finally:
        win.close()


def test_view_defers_rendering_until_first_show(qapp, vdoc):
    """No page is rasterised until the window is first shown (open_at) — the construction-time and
    pre-show renders are suppressed, so nothing is painted at the wrong zoom (no flicker)."""
    view = PdfView(vdoc)
    assert view._shown_once is False
    view._render_visible()  # gated → no-op before the first show
    assert all(p["pix"].pixmap().isNull() for p in view._pages)  # nothing rendered yet


def test_open_renders_the_current_page_once_shown(qapp, a_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "view_state.json")
    w = qapp.open_document(a_pdf)
    qapp.processEvents()
    assert w.view._shown_once is True
    assert not w.view._pages[w.view.current_page]["pix"].pixmap().isNull()  # rendered on open
    w.close()
