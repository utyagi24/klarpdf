"""In-viewer internal-link navigation (PLAN.md, M33). Offscreen GUI.

Clicking a GoTo or named-destination link jumps to the page its target currently sits on, following
reorders/deletes live; hovering shows a pointing-hand cursor; non-link clicks fall through to text
selection. Navigation is verified by spying on goto_page (the scroll itself is goto_page's job).
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtCore import Qt

from app import PdfApp
from store.settings import Settings


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    for w in list(qapp._windows.values()):
        w.close()
    qapp._windows.clear()
    yield qapp
    for w in list(qapp._windows.values()):
        w.undo_stack.setClean()
        w.close()
    qapp._windows.clear()


_GOTO_BOX = (72, 100, 200, 120)   # link on page 0 -> page 3 (GoTo)
_NAMED_BOX = (72, 140, 200, 160)  # link on page 0 -> page 4 (named destination)


@pytest.fixture
def linked_pdf(tmp_path) -> str:
    path = str(tmp_path / "nav.pdf")
    doc = fitz.open()
    for i in range(5):
        doc.new_page().insert_text((72, 72), f"PAGE {i}", fontsize=20)
    doc[0].insert_link({"kind": fitz.LINK_GOTO, "from": fitz.Rect(*_GOTO_BOX), "page": 3,
                        "to": fitz.Point(0, 0)})
    doc.xref_set_key(doc.pdf_catalog(), "Dests",
                     "<< /sec4 [ %d 0 R /XYZ 0 700 0 ] >>" % doc.page_xref(4))
    doc[0].insert_link({"kind": fitz.LINK_NAMED, "from": fitz.Rect(*_NAMED_BOX), "nameddest": "sec4"})
    doc.save(path)
    doc.close()
    return path


def _center_of(view, page_index, box):
    return view.scene_rect_for_box(page_index, box).center()


def _spy_goto(view, monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(view, "goto_page", lambda index: calls.append(index))
    return calls


def test_click_goto_link_navigates_to_target(app, linked_pdf, monkeypatch):
    win = app.open_document(linked_pdf)
    calls = _spy_goto(win.view, monkeypatch)
    assert win.view.links.navigate_at(_center_of(win.view, 0, _GOTO_BOX)) is True
    assert calls == [3]


def test_click_named_destination_link_navigates(app, linked_pdf, monkeypatch):
    win = app.open_document(linked_pdf)
    calls = _spy_goto(win.view, monkeypatch)
    assert win.view.links.navigate_at(_center_of(win.view, 0, _NAMED_BOX)) is True
    assert calls == [4]


def test_click_off_a_link_does_not_navigate(app, linked_pdf, monkeypatch):
    win = app.open_document(linked_pdf)
    calls = _spy_goto(win.view, monkeypatch)
    assert win.view.links.navigate_at(_center_of(win.view, 0, (300, 400, 360, 420))) is False
    assert calls == []


def test_navigation_follows_a_reorder(app, linked_pdf, monkeypatch):
    win = app.open_document(linked_pdf)
    win.vdoc.move_pages([3], 5)  # GoTo target (src3) → the end: now display index 4
    win.view.reload()
    calls = _spy_goto(win.view, monkeypatch)
    assert win.view.links.navigate_at(_center_of(win.view, 0, _GOTO_BOX)) is True
    assert calls == [4]


def test_link_to_deleted_target_is_not_clickable(app, linked_pdf, monkeypatch):
    win = app.open_document(linked_pdf)
    win.vdoc.delete_page(3)  # the GoTo target is gone
    win.view.reload()
    calls = _spy_goto(win.view, monkeypatch)
    assert win.view.links.navigate_at(_center_of(win.view, 0, _GOTO_BOX)) is False
    assert calls == []


def test_hover_over_link_shows_pointing_hand(app, linked_pdf):
    win = app.open_document(linked_pdf)
    win.view._update_hover_cursor(_center_of(win.view, 0, _GOTO_BOX))
    assert win.view.viewport().cursor().shape() == Qt.CursorShape.PointingHandCursor
