"""UI polish: thumbnail multi-select visibility (#3) + vertical-centering a fitting page (#4).
Offscreen GUI.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtCore import Qt

from app import PdfApp
from model.virtual_document import VirtualDocument
from organize.thumbnail_panel import ThumbnailPanel
from viewer.pdf_view import PdfView


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


# ---- #3: selected pages are clearly marked ----------------------------------


def _accent_pixels(img) -> int:
    """Count (sampled) pixels close to the accent blue (0,120,215) — the selection / current
    markers. Thumbnails are otherwise greyscale page renders, so accent pixels are our overlays."""
    n = 0
    for y in range(0, img.height(), 3):
        for x in range(0, img.width(), 3):
            c = img.pixelColor(x, y)
            if c.red() < 120 and 70 <= c.green() <= 180 and c.blue() > 160:
                n += 1
    return n


def test_selecting_more_pages_adds_more_selection_marking(qapp, a_pdf):
    panel = ThumbnailPanel(VirtualDocument.from_path(a_pdf))  # A.pdf has 3 pages
    panel.resize(200, 900)
    panel.show()
    qapp.processEvents()
    try:
        panel.clearSelection()
        panel.setCurrentRow(0)
        qapp.processEvents()
        one = _accent_pixels(panel.viewport().grab().toImage())

        panel.item(1).setSelected(True)
        panel.item(2).setSelected(True)  # now 3 pages marked instead of 1
        qapp.processEvents()
        many = _accent_pixels(panel.viewport().grab().toImage())

        assert many > one  # extra selected pages add visible accent marking
    finally:
        panel.deleteLater()


def test_selection_marker_helper_runs_for_a_multi_selection(qapp, a_pdf):
    """The paint helper iterates the selection without error (current page is skipped)."""
    from PySide6.QtGui import QPainter, QPixmap

    panel = ThumbnailPanel(VirtualDocument.from_path(a_pdf))
    panel.resize(200, 900)
    panel.show()
    qapp.processEvents()
    try:
        panel.item(0).setSelected(True)
        panel.item(2).setSelected(True)
        panel.setCurrentRow(2)
        pm = QPixmap(panel.viewport().size())
        pm.fill(Qt.GlobalColor.white)
        painter = QPainter(pm)
        panel._paint_selection_markers(painter)  # should not raise
        painter.end()
    finally:
        panel.deleteLater()


def test_thumbnail_width_is_invariant_to_scrollbar_visibility(qapp, a_pdf):
    """Regression: at a narrow bar width the thumbnail tracks the width, so if its size depended on
    whether the vertical scrollbar is showing, the bar toggling on/off would resize the thumbnails —
    which changes the content height, which toggles the bar again: a flicker loop that fires when the
    window's bottom edge lands right at the last thumbnail. The icon width must not change when the
    scrollbar appears/disappears (its slot is reserved unconditionally)."""
    from organize.thumbnail_panel import _THUMB_MAX_W

    panel = ThumbnailPanel(VirtualDocument.from_path(a_pdf))
    panel.resize(180, 900)  # narrow enough that the thumbnail tracks the width (not clamped at max)
    panel.show()
    qapp.processEvents()
    try:
        panel.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        qapp.processEvents()
        panel._apply_thumb_size()
        without_bar = panel.iconSize().width()

        panel.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        qapp.processEvents()
        panel._apply_thumb_size()
        with_bar = panel.iconSize().width()

        assert without_bar < _THUMB_MAX_W  # in the width-tracking regime, so the check is meaningful
        assert with_bar == without_bar     # scrollbar toggling must not resize the thumbnail
    finally:
        panel.deleteLater()


# ---- #4: a short page that fits the viewport is vertically centered ----------


@pytest.fixture
def one_page_pdf(tmp_path) -> str:
    path = str(tmp_path / "one.pdf")
    doc = fitz.open()
    doc.new_page(width=300, height=200).insert_text((20, 100), "ONE", fontsize=24)
    doc.save(path)
    doc.close()
    return path


def test_view_alignment_centers_vertically(qapp, one_page_pdf):
    view = PdfView(VirtualDocument.from_path(one_page_pdf))
    try:
        align = view.alignment()
        assert align & Qt.AlignmentFlag.AlignVCenter  # short/fitting page centres vertically
        assert align & Qt.AlignmentFlag.AlignHCenter  # (still horizontally centred, as before)
        assert not (align & Qt.AlignmentFlag.AlignTop)  # no longer pinned to the top
    finally:
        view.deleteLater()


def test_fitting_page_is_pushed_down_from_the_top(qapp, one_page_pdf):
    """With the scene far shorter than a tall viewport, the page's top maps well below y=0 — it is
    centred, not stuck at the top (which would map to ~0)."""
    from PySide6.QtCore import QPointF

    from viewer.pdf_view import _PAGE_GAP

    view = PdfView(VirtualDocument.from_path(one_page_pdf))
    try:
        view.resize(500, 1000)
        view.show()
        qapp.processEvents()
        view.set_zoom(0.3)  # shrink the 200pt-tall page to ~60px << 1000px viewport
        qapp.processEvents()
        scene_h = view.scene().sceneRect().height() * view.zoom
        if view.viewport().height() <= scene_h + 200:
            pytest.skip("viewport not tall enough in this environment to exercise centering")
        top_y = view.mapFromScene(QPointF(0.0, float(_PAGE_GAP))).y()
        assert top_y > 100  # the page top sits well below the viewport top → centred
    finally:
        view.deleteLater()
