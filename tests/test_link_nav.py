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


# ---- external (web) links open in the browser (M149, #333) ---------------------


_WEB_BOX = (72, 180, 200, 200)      # page 0 → https://example.org/docs
_MAILTO_BOX = (72, 220, 200, 240)   # page 0 → mailto:support@example.org
_JS_BOX = (72, 260, 200, 280)       # page 0 → javascript:… — never openable
_WEB_URI = "https://example.org/docs"
_MAILTO_URI = "mailto:support@example.org"
_JS_URI = "javascript:alert(1)"


@pytest.fixture
def web_pdf(tmp_path) -> str:
    """One page carrying three URI links: a web page, a contact address, and a script.

    `file:` is deliberately absent. Measured on PyMuPDF 1.27.2.3, MuPDF reports a `file:` action as
    `LINK_LAUNCH` with `uri: None` — whether MuPDF wrote the link itself or it was hand-crafted as a
    `/URI` action — so such a link never reaches `uri_at` and there is nothing here to gate. The
    schemes that *do* arrive as ordinary `LINK_URI` are `javascript:`, `data:`, `ftp:` and
    `ms-msdt:`, which is what `OPENABLE_SCHEMES` exists to stop.
    """
    path = str(tmp_path / "web.pdf")
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "PAGE 0", fontsize=20)
    for box, uri in ((_WEB_BOX, _WEB_URI), (_MAILTO_BOX, _MAILTO_URI), (_JS_BOX, _JS_URI)):
        doc[0].insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(*box), "uri": uri})
    doc.save(path)
    doc.close()
    return path


def _handed_to_browser(monkeypatch) -> list[str]:
    """Capture what `ui.about._open_url` hands to `QDesktopServices` — the one place a URL leaves."""
    handed: list[str] = []
    monkeypatch.setattr("ui.about.QDesktopServices.openUrl", lambda url: handed.append(url.toString()))
    return handed


def _click(win, box):
    """A real left press on the centre of ``box``, through the view's own handler."""
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    vp = QPointF(win.view.mapFromScene(_center_of(win.view, 0, box)))
    event = QMouseEvent(QEvent.Type.MouseButtonPress, vp, vp, Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    win.view.mousePressEvent(event)
    return event


def test_clicking_a_web_link_hands_it_to_the_browser(app, web_pdf, monkeypatch):
    """#333, reversing M33/M46's copy-only rule. Two readers expected a click to work; it did
    nothing at all."""
    win = app.open_document(web_pdf)
    handed = _handed_to_browser(monkeypatch)
    event = _click(win, _WEB_BOX)
    assert handed == [_WEB_URI]
    assert event.isAccepted()  # consumed, so the click does not also start a text selection


def test_clicking_a_mailto_link_hands_it_over_too(app, web_pdf, monkeypatch):
    """The owner's call on schemes: a contact link in a manual is what `mailto:` is usually for."""
    win = app.open_document(web_pdf)
    handed = _handed_to_browser(monkeypatch)
    _click(win, _MAILTO_BOX)
    assert handed == [_MAILTO_URI]


def test_a_script_link_is_never_handed_anywhere(app, web_pdf, monkeypatch):
    """`QDesktopServices.openUrl` hands whatever it is given to the shell, so the gate is an
    allowlist. A refused scheme stays exactly as it was before M149: copy-only."""
    win = app.open_document(web_pdf)
    handed = _handed_to_browser(monkeypatch)
    center = _center_of(win.view, 0, _JS_BOX)
    assert win.view.links.uri_at(center) == _JS_URI          # the document's link is still read...
    assert win.view.links.openable_uri_at(center) is None    # ...and refused
    _click(win, _JS_BOX)
    assert handed == []


def test_hover_over_a_web_link_shows_the_pointing_hand(app, web_pdf):
    win = app.open_document(web_pdf)
    win.view._update_hover_cursor(_center_of(win.view, 0, _WEB_BOX))
    assert win.view.viewport().cursor().shape() == Qt.CursorShape.PointingHandCursor


def test_a_refused_scheme_does_not_even_look_clickable(app, web_pdf):
    win = app.open_document(web_pdf)
    win.view._update_hover_cursor(_center_of(win.view, 0, _JS_BOX))
    assert win.view.viewport().cursor().shape() != Qt.CursorShape.PointingHandCursor


# ---- the URL on hover, asserted as a reader sees it -----------------------------
#
# The first version of these tests asserted `view.viewport().toolTip() == url`, which passed while
# the reader saw **nothing**: a `QGraphicsView`'s viewport tooltip is never shown, because
# `viewportEvent` intercepts `QEvent.ToolTip`, hands it to the *scene* for its items, and returns —
# so `QWidget`'s handler, the only thing that reads that property, never runs. Setting the property
# was the assertion and also the whole of the behaviour, so the test could not fail. These send a
# real `QHelpEvent` through the view instead, and assert on what is handed to `QToolTip`.


@pytest.fixture
def tooltips(monkeypatch):
    """Capture what reaches QToolTip: ``(shown_texts, hide_calls)``."""
    from PySide6.QtWidgets import QToolTip

    shown: list[str] = []
    hidden: list[int] = []
    monkeypatch.setattr(QToolTip, "showText",
                        staticmethod(lambda pos, text, *a, **k: shown.append(text)))
    monkeypatch.setattr(QToolTip, "hideText", staticmethod(lambda: hidden.append(1)))
    return shown, hidden


def _hover_for_tooltip(app, win, box):
    """Ask the view for a tooltip at ``box``, the way Qt does when the pointer rests there.

    Returns the event, whose ``isAccepted()`` is Qt's own answer to "did a scene **item** take
    this": ``QGraphicsScene.helpEvent`` accepts only when it showed an item's tooltip.
    """
    from PySide6.QtCore import QEvent, QPoint
    from PySide6.QtGui import QHelpEvent
    from PySide6.QtWidgets import QApplication

    pt = QPoint(win.view.mapFromScene(_center_of(win.view, 0, box)))
    event = QHelpEvent(QEvent.Type.ToolTip, pt, win.view.viewport().mapToGlobal(pt))
    QApplication.sendEvent(win.view.viewport(), event)
    app.processEvents()
    return event


def test_resting_on_a_web_link_shows_its_url(app, web_pdf, tooltips):
    """A PDF shows the reader a link's *text* and never where it goes, so the URL is the tooltip."""
    shown, _ = tooltips
    win = app.open_document(web_pdf)
    event = _hover_for_tooltip(app, win, _WEB_BOX)
    assert shown == [_WEB_URI]
    assert not event.isAccepted()   # no item claimed it, which is why we were asked at all


def test_resting_off_a_link_shows_nothing_and_clears_what_was_there(app, web_pdf, tooltips):
    """A stale URL would claim a link is under the pointer when none is."""
    shown, hidden = tooltips
    win = app.open_document(web_pdf)
    _hover_for_tooltip(app, win, _WEB_BOX)
    _hover_for_tooltip(app, win, (300, 400, 360, 420))
    assert shown == [_WEB_URI]      # nothing added for the bare page
    assert hidden                   # and the previous one was actively dismissed


def test_a_refused_scheme_offers_no_url(app, web_pdf, tooltips):
    shown, _ = tooltips
    win = app.open_document(web_pdf)
    _hover_for_tooltip(app, win, _JS_BOX)
    assert shown == []


def test_an_armed_tool_offers_no_url(app, web_pdf, tooltips):
    """Gated on the same state as the pointing hand: a link this mode will not follow must not
    advertise itself either."""
    from viewer.tools import ArmedTool

    shown, _ = tooltips
    win = app.open_document(web_pdf)
    win.view.arm(ArmedTool.HIGHLIGHT)
    _hover_for_tooltip(app, win, _WEB_BOX)
    assert shown == []


def test_a_scene_items_own_tooltip_still_wins(app, web_pdf, tooltips):
    """The regression this interception could have caused.

    A note badge carries its note as a **QGraphicsItem** tooltip (``annotations.py``) — the one hover
    text in this view that always worked, because items are what the scene offers the event to. Mouse
    presses prefer an annotation over a link, so hover text has to agree. Stand-in for the badge: a
    tooltip on the page item under the link, which is the same mechanism.
    """
    shown, _ = tooltips
    win = app.open_document(web_pdf)
    centre = _center_of(win.view, 0, _WEB_BOX)
    item = win.view.scene().items(centre)[0]
    item.setToolTip("the item got there first")
    event = _hover_for_tooltip(app, win, _WEB_BOX)
    # The scene shows an item's tooltip from C++, which a Python patch of `QToolTip.showText`
    # cannot observe — so the evidence is that Qt accepted the event and that we added nothing.
    assert event.isAccepted(), "the scene did not take the event, so nothing shows the item's note"
    assert shown == [], "the link's URL was offered over the top of an item's own tooltip"
    # No cleanup: the scene rebuilds on its own deferred pass and takes this item with it (the
    # tooltip is read during the synchronous `sendEvent`, before that pass, so the order is fixed).


def test_the_scheme_gate_in_isolation():
    """The decision itself, with no document or window in the way."""
    from viewer.links import openable_uri

    for allowed in ("https://example.org", "http://example.org", "HTTPS://Example.org",
                    "mailto:a@b.c", "  https://example.org/padded  "):
        assert openable_uri(allowed) == allowed.strip()
    for refused in ("javascript:alert(1)", "data:text/html,<b>x", "ftp://example.org",
                    "ms-msdt:/id", "file:///C:/Windows/System32/calc.exe",
                    "www.example.org", "", "   ", "http s://x", "https://[oops"):
        assert openable_uri(refused) is None
