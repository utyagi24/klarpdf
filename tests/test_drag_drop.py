"""Drag-and-drop visuals for the thumbnail panel (PLAN.md, M16). Offscreen GUI.

Covers the drag pixmap (a page image carried under the cursor) and the insertion-marker slot
tracking (`_drop_row`) driven by drag-move / leave / drop. The actual drag loop (startDrag →
QDrag.exec) is modal and can't run headless, so we test its pieces directly.
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QByteArray, QMimeData, QPoint, Qt
from PySide6.QtGui import QDragLeaveEvent, QDragMoveEvent

from app import PdfApp
from model.virtual_document import VirtualDocument
from organize.thumbnail_panel import _PAGES_MIME, ThumbnailPanel


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def panel(qapp, a_pdf):
    p = ThumbnailPanel(VirtualDocument.from_path(a_pdf))
    p.source_key = "doc"
    p.resize(180, 800)  # narrow → one thumbnail per row
    p.show()
    qapp.processEvents()
    return p


def _move(y: float) -> QDragMoveEvent:
    mime = QMimeData()
    mime.setData(_PAGES_MIME, QByteArray(json.dumps({"source": "doc", "rows": [0]}).encode()))
    event = QDragMoveEvent(QPoint(10, int(y)), Qt.DropAction.MoveAction, mime,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    event._mime = mime  # keep the QMimeData alive for the event's lifetime (PySide won't)
    return event


def test_drag_pixmap_single_is_non_null(panel):
    pm = panel._drag_pixmap([0])
    assert not pm.isNull() and pm.width() > 0 and pm.height() > 0


def test_drag_pixmap_multi_has_badge_and_stack(panel):
    one = panel._drag_pixmap([0])
    many = panel._drag_pixmap([0, 1, 2])
    assert not many.isNull()
    # the stacked-page offset makes the multi-drag canvas larger than the single
    assert many.width() > one.width() and many.height() > one.height()


def test_drop_row_tracks_drag_move(panel):
    top = panel.visualItemRect(panel.item(0)).top()
    panel.dragMoveEvent(_move(top + 1))
    assert panel._drop_row == 0  # above the first page's centre → insert before it

    bottom = panel.visualItemRect(panel.item(panel.count() - 1)).bottom()
    panel.dragMoveEvent(_move(bottom + 50))
    assert panel._drop_row == panel.count()  # below the last page → append


def test_drag_leave_clears_marker(panel):
    panel.dragMoveEvent(_move(5))
    assert panel._drop_row is not None
    panel.dragLeaveEvent(QDragLeaveEvent())
    assert panel._drop_row is None


def test_drop_clears_marker(panel):
    got = []
    panel.pagesDropped.connect(lambda s, r, b: got.append((s, r, b)))
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QDropEvent

    mime = QMimeData()
    mime.setData(_PAGES_MIME, QByteArray(json.dumps({"source": "other", "rows": [1]}).encode()))
    panel.dragMoveEvent(_move(5))
    panel.dropEvent(QDropEvent(QPointF(10, 5), Qt.DropAction.MoveAction, mime,
                               Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert panel._drop_row is None      # marker cleared after the drop
    assert got and got[0][1] == [1]     # drop still delivered
