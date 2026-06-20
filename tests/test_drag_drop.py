"""Drag-and-drop visuals for the thumbnail panel (PLAN.md, M16). Offscreen GUI.

Covers the drag pixmap (a page image carried under the cursor) and the insertion-marker slot
tracking (`_drop_row`) driven by drag-move / leave / drop. The actual drag loop (startDrag →
QDrag.exec) is modal and can't run headless, so we test its pieces directly.
"""

from __future__ import annotations

import json
import os

import pymupdf as fitz
import pytest
from PySide6.QtCore import QByteArray, QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragLeaveEvent, QDragMoveEvent, QDropEvent

from app import PdfApp
from model.virtual_document import VirtualDocument
from organize.thumbnail_panel import _PAGES_MIME, ThumbnailPanel
from store.settings import Settings


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


# ---- M17: Explorer file drop ------------------------------------------------------

def _file_mime(*paths: str) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    return mime


def test_pdf_url_filter_keeps_only_pdfs(panel, b_pdf, tmp_path):
    txt = tmp_path / "note.txt"
    txt.write_text("not a pdf")
    mime = _file_mime(b_pdf, str(txt), "/no/such/file.pdf")
    result = panel._dropped_file_paths(mime)  # local, existing, .pdf / image only
    # QUrl.toLocalFile normalises separators (forward slashes on Windows), so compare normalised.
    assert [os.path.normpath(p) for p in result] == [os.path.normpath(b_pdf)]


def test_file_drag_is_accepted_and_marks_slot(panel, b_pdf):
    mime = _file_mime(b_pdf)
    event = QDragMoveEvent(QPoint(10, 5), Qt.DropAction.CopyAction, mime,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    event._mime = mime
    panel.dragMoveEvent(event)
    assert event.isAccepted() and panel._drop_row == 0  # droppable + marker at the top slot


def test_file_drop_emits_filesDropped(panel, b_pdf):
    got = []
    panel.filesDropped.connect(lambda paths, before: got.append((paths, before)))
    mime = _file_mime(b_pdf)
    event = QDropEvent(QPointF(10, 5), Qt.DropAction.CopyAction, mime,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    event._mime = mime
    panel.dropEvent(event)
    assert len(got) == 1 and got[0][1] == 0  # dropped at the top → insert before page 0
    assert [os.path.normpath(p) for p in got[0][0]] == [os.path.normpath(b_pdf)]
    assert panel._drop_row is None


def test_files_dropped_inserts_pages_and_undoes(qapp, a_pdf, b_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    win = qapp.open_document(a_pdf)
    base = win.vdoc.page_count
    with fitz.open(b_pdf) as d:
        added = d.page_count
    win._on_files_dropped([b_pdf], 1)  # insert b's pages starting before index 1
    assert win.vdoc.page_count == base + added
    win.undo_stack.undo()
    assert win.vdoc.page_count == base  # undoable
    win.undo_stack.setClean()
    win.close()
