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


# ---- a destination PyMuPDF hands back as a string (M138.2) ---------------------


@pytest.fixture
def view_dest_pdf(tmp_path) -> str:
    """A Contents-style link whose action is `/S /GoTo /D [<page> 0 R /Fit]`.

    The shape a real InDesign export writes — the owner's Cisco 2025 annual report has 18 of them
    on its Contents and cross-reference pages. PyMuPDF reports it as `kind: LINK_NAMED` with
    `page: '3'`, **a string**, because `getLinkDict` only converts `#page=N` and `#page=N&zoom=…`
    into an int and `&view=Fit` matches neither.

    Hand-built because `insert_link` cannot produce it: every fixture written through the API comes
    back as a 0-based int, which is why this went unnoticed until a real document arrived.
    """
    path = str(tmp_path / "viewdest.pdf")
    doc = fitz.open()
    for i in range(5):
        doc.new_page().insert_text((72, 72), f"PAGE {i}", fontsize=20)
    # Written as raw PDF, so the /Rect is in the format's own **bottom-left** origin while
    # `_GOTO_BOX` is in PyMuPDF's top-left one — the very flip `klarpdf://docs/get_links` warns
    # callers about. Converting here keeps the fixture comparable with the others in this file.
    height = doc[0].rect.height
    x0, y0, x1, y1 = _GOTO_BOX
    annot, action = doc.get_new_xref(), doc.get_new_xref()
    doc.update_object(action, "<< /S /GoTo /D [ %d 0 R /Fit ] >>" % doc.page_xref(3))
    doc.update_object(annot, "<< /Type/Annot /Subtype/Link /Rect [%g %g %g %g] "
                             "/Border[0 0 0] /A %d 0 R >>"
                             % (x0, height - y1, x1, height - y0, action))
    doc.xref_set_key(doc.page_xref(0), "Annots", "[%d 0 R]" % annot)
    doc.save(path)
    doc.close()
    return path


def test_a_string_spelled_destination_is_clickable(app, view_dest_pdf, monkeypatch):
    """The owner's report: *"in KlarPDF I am not able to click on the page numbers listed under
    Contents on Page 3 of the Cisco Annual report; in Edge those page numbers are clickable"*.

    `LinkNavigator._build` skips any link whose `internal_link_target` is `None`, so before the fix
    these were not links at all — no cursor, no navigation, and no error to notice.
    """
    win = app.open_document(view_dest_pdf)
    calls = _spy_goto(win.view, monkeypatch)
    assert win.view.links.navigate_at(_center_of(win.view, 0, _GOTO_BOX)) is True
    assert calls == [3]


def test_a_string_spelled_destination_shows_the_pointing_hand(app, view_dest_pdf):
    """The other half of "not clickable": the cursor is what tells a reader a link is there."""
    win = app.open_document(view_dest_pdf)
    assert win.view.links.link_at(_center_of(win.view, 0, _GOTO_BOX)) == 3
