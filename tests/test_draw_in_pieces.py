"""A slow page is drawn in pieces, and the window keeps working in between (PLAN.md §M152, M152.2).
Offscreen GUI.

A page that takes longer than ``_DRAW_BUDGET_S`` to draw is not drawn in one go on the window's
thread any more. It shows a stand-in at once: a picture of it drawn for another size, stretched,
or the picture carried from before a zoom, an edit or a minimize. Its pieces then replace the
stand-in, starting from the middle of the window, a few per turn, with the window handling events
between turns. Pieces within a window height above and below are drawn ahead.

The rest of the suite counts every page as quick (conftest ``_quick_pages``), so nothing there is
drawn in pieces. Here the budget is set to zero, which makes every page drawn once slow, and every
turn draw one piece.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest

import viewer.pdf_view
from app import PdfApp
from klarpdf.model.virtual_document import VirtualDocument
from viewer.pdf_view import _DRAW_BUDGET_S, PdfView
from viewer.pieces import TILE


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def eight_pages(tmp_path) -> str:
    path = str(tmp_path / "eight.pdf")
    doc = fitz.open()
    for i in range(8):
        page = doc.new_page()   # A4
        page.insert_text((72, 72), f"page {i + 1}", fontsize=24)
        page.draw_rect(fitz.Rect(100, 150, 480, 700), color=(0.2, 0.3, 0.8), fill=(0.9, 0.9, 1.0))
        page.insert_text((120, 400), "the middle of the page " * 3, fontsize=9)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture(autouse=True)
def long_wait(monkeypatch):
    """A resize's wait for the edge to rest cannot run out by itself during a test (M152.1)."""
    monkeypatch.setattr(viewer.pdf_view, "_RESIZE_SETTLE_MS", 60_000)


@pytest.fixture
def slow(monkeypatch):
    """Every page drawn once counts as slow, and each turn draws one piece."""
    monkeypatch.setattr(viewer.pdf_view, "_DRAW_BUDGET_S", 0.0)


@pytest.fixture
def view(qapp, eight_pages):
    v = PdfView(VirtualDocument.from_path(eight_pages))
    v.resize(600, 700)
    v.show()
    qapp.processEvents()
    v.open_at({})
    qapp.processEvents()
    yield v
    v.deleteLater()


@pytest.fixture
def drawings(monkeypatch):
    """What PyMuPDF was asked to draw, in order: ``("page", n)`` for a whole page and
    ``("piece", n)`` for a piece drawn from page ``n``'s display list. The pages here are not
    cropped, so a whole page is drawn with no clip."""
    seen: list[tuple[str, int]] = []
    real_page, real_list, real_make = fitz.Page.get_pixmap, fitz.DisplayList.get_pixmap, fitz.Page.get_displaylist

    inside = []   # PyMuPDF draws a whole page through a display list of its own

    def page_drawn(self, *args, **kwargs):
        seen.append(("page", self.number))
        inside.append(True)
        try:
            return real_page(self, *args, **kwargs)
        finally:
            inside.pop()

    def list_made(self, *args, **kwargs):
        dlist = real_make(self, *args, **kwargs)
        dlist.page_number = self.number
        return dlist

    def piece_drawn(self, *args, **kwargs):
        if not inside:   # a page with a display list is drawn whole from it too, with no clip
            seen.append(("piece" if kwargs.get("clip") is not None else "page", self.page_number))
        return real_list(self, *args, **kwargs)

    monkeypatch.setattr(fitz.Page, "get_pixmap", page_drawn)
    monkeypatch.setattr(fitz.Page, "get_displaylist", list_made)
    monkeypatch.setattr(fitz.DisplayList, "get_pixmap", piece_drawn)
    return seen


def _drain(qapp, view) -> None:
    """End any wait for an edge to rest, and let every turn run until nothing is left to draw."""
    for _ in range(3000):
        # Shorten the wait once and let it run out. Restarting it on every pass kept it from ever
        # firing: seen on Windows, where a resize after the page turned started the wait.
        if view._settle_timer.isActive() and view._settle_timer.remainingTime() > 1:
            view._settle_timer.start(1)
        if (not view._settle_timer.isActive() and not view._piece_timer.isActive()
                and not view._prefetch_timer.isActive()):
            return
        QTest.qWait(1)
    pytest.fail("the drawing never finished")


def _image(pixmap) -> QImage:
    return pixmap.toImage().convertToFormat(QImage.Format.Format_RGB32)


def _drawn_whole(view, index: int) -> QImage:
    """Page ``index`` drawn whole at the view's size, the way the view draws a quick page."""
    page, clip, total = view._render_target(index)
    ds = view.device_scale
    pm = page.get_pixmap(matrix=fitz.Matrix(ds, ds), clip=clip, alpha=False)
    img = QImage(pm.samples, pm.width, pm.height, pm.stride, QImage.Format.Format_RGB888).copy()
    return _image(view._to_pixmap(img, total))


def _has_own_picture(view, index: int) -> bool:
    item = view._pages[index]["pix"]
    return (not item.pixmap().isNull() and item.scale() == 1.0 and index not in view._stand_ins
            and index not in view._wip)


def _zoom(qapp, view, zoom: float) -> None:
    """Zoom with every earlier drawing finished, so the zoom's own drawing is what is measured."""
    _drain(qapp, view)
    view.set_zoom(zoom)


# ---- a stand-in at once, then pieces ---------------------------------------------


def test_a_zoom_shows_a_stand_in_at_once_and_the_pieces_follow(view, qapp, slow, drawings):
    _drain(qapp, view)
    drawings.clear()

    view.set_zoom(2.0)

    item = view._pages[0]["pix"]
    assert not item.pixmap().isNull() and item.scale() != 1.0   # the old picture, stretched
    assert len(view._wip[0].pieces) == 1                         # one piece in the first turn
    assert view._piece_timer.isActive()

    _drain(qapp, view)
    first, last = view._visible_range()
    for i in range(first, last + 1):
        wip = view._wip.get(i)
        if wip is not None:          # a page still unfinished has every tile on screen drawn
            local = view._local_rect(i, view.mapToScene(view.viewport().rect()).boundingRect())
            assert wip.tiles.tiles_in(local) <= wip.tiles.drawn
    # Every piece of page 1 came from its display list, which reads the page once: read again for
    # every piece, the NADA cover cost 45 ms a piece instead of 3-4 ms.
    assert [kind for kind, n in drawings if n == 0] == ["piece"] * len([n for _k, n in drawings if n == 0])


def test_the_first_piece_is_in_the_middle_of_the_window(view, qapp, slow):
    _zoom(qapp, view, 3.0)

    wip = view._wip[view.current_page]
    centre = view._local_point(view.current_page, view.mapToScene(view.viewport().rect()).boundingRect().center())
    c0, r0, c1, r1 = wip.pieces[0][0]
    assert c0 * TILE <= centre[0] <= (c1 + 1) * TILE + TILE
    assert r0 * TILE <= centre[1] <= (r1 + 1) * TILE + TILE


def test_the_window_handles_events_between_pieces(view, qapp, slow):
    _zoom(qapp, view, 3.0)
    wip = view._wip[view.current_page]
    seen = []
    QTimer.singleShot(0, lambda: seen.append(len(wip.tiles.drawn)))

    _drain(qapp, view)

    assert seen and 0 < seen[0] < wip.tiles.cols * wip.tiles.rows


def test_a_page_whose_pieces_are_all_drawn_becomes_one_picture(view, qapp, slow):
    """At a size where the whole page is on screen or just ahead, every piece is drawn, and they
    are joined into one picture for the store. It matches the page drawn whole, pixel for pixel.
    Pieces are drawn ahead above and below the window, not beside it, so the page must fit across."""
    _zoom(qapp, view, 0.6)
    _drain(qapp, view)

    assert _has_own_picture(view, 0)
    assert view._cache.get(view._pixmap_key(0)) is not None
    assert _image(view._pages[0]["pix"].pixmap()) == _drawn_whole(view, 0)


@pytest.mark.parametrize("setup", ["rotated view", "rotated page", "cropped page", "night mode"])
def test_pieces_join_up_exactly_however_the_page_is_shown(view, qapp, slow, setup):
    if setup == "rotated view":
        view.rotate_view(90)
    elif setup == "rotated page":
        view._vdoc.rotate_pages([0], 270)
        view.reload()
    elif setup == "cropped page":
        view._vdoc.set_crop([0], (40.5, 60.25, 520.0, 700.75))
        view.reload()
    else:
        view.set_night_mode(True)
    _zoom(qapp, view, 0.55)
    _drain(qapp, view)

    assert _has_own_picture(view, 0)
    assert _image(view._pages[0]["pix"].pixmap()) == _drawn_whole(view, 0)


# ---- what is drawn, and when -------------------------------------------------------


def test_pieces_ahead_stop_a_window_height_away(view, qapp, slow):
    _zoom(qapp, view, 4.0)
    _drain(qapp, view)

    view_rect = view.mapToScene(view.viewport().rect()).boundingRect()
    ahead = view._local_rect(0, view._ahead_rect(view_rect))
    wip = view._wip[0]
    assert wip.tiles.drawn <= wip.tiles.tiles_in(ahead)
    assert wip.tiles.tiles_in(ahead) <= wip.tiles.drawn
    assert not wip.tiles.complete()                             # the page is far taller


def test_pieces_no_longer_needed_are_dropped(view, qapp, slow):
    _zoom(qapp, view, 4.0)
    _drain(qapp, view)
    wip = view._wip[0]
    before = set(wip.tiles.drawn)
    scene = view.scene()
    count = len(scene.items())

    bar = view.verticalScrollBar()
    bar.setValue(bar.value() + 3 * view.viewport().height())

    assert not before <= wip.tiles.drawn                       # the old tiles are forgotten
    assert len(scene.items()) < count + len(wip.pieces)        # and their items are gone


def test_pieces_ahead_wait_for_a_wheel_glide(view, qapp, slow, drawings):
    _zoom(qapp, view, 4.0)
    while view._pieces_on_screen_pending():
        QTest.qWait(1)                                          # the window's pieces first
    view._glide_timer.start(10_000)                             # a glide in flight
    drawings.clear()

    view._turn()

    assert drawings == []
    view._glide_timer.stop()


def test_quick_pages_ahead_wait_for_the_pieces_on_screen(view, qapp, slow, drawings):
    """Pages drawn ahead whole wait while pieces of the page on screen are still to draw."""
    _drain(qapp, view)
    view._draw_rate = {0: view._draw_rate[0]}      # page 1 slow; the others never drawn, so quick
    view.set_zoom(0.6)
    assert view._pieces_on_screen_pending() and view._prefetch_queue
    drawings.clear()

    view._drain_prefetch()

    assert drawings == []


def test_a_quick_page_is_drawn_whole_as_before(view, qapp, drawings):
    """Conftest counts every page as quick: a zoom draws the page whole and begins no pieces."""
    _zoom(qapp, view, 2.0)

    assert ("page", 0) in drawings and not any(kind == "piece" for kind, _n in drawings)
    assert not view._wip and _has_own_picture(view, 0)


def test_a_page_not_timed_and_bigger_than_the_window_starts_with_a_window_of_it(view, qapp, monkeypatch):
    """With the app's own budget, a page with no timing that is bigger than the window is begun in
    pieces, and its first piece is about a window's worth: as much as a page no bigger than the
    window is drawn blind. A single tile would be mostly the fixed cost of a piece, and would make
    a quick page look slow."""
    monkeypatch.setattr(viewer.pdf_view, "_DRAW_BUDGET_S", _DRAW_BUDGET_S)
    _drain(qapp, view)
    view._draw_rate.clear()
    pieces = []
    real = PdfView._draw_piece
    monkeypatch.setattr(PdfView, "_draw_piece", lambda self, i, piece: (
        pieces.append(self._wip[i].tiles.box(piece)), real(self, i, piece)))

    view.set_zoom(3.0)

    window = view.viewport().width() * view.viewport().height() * view._dpr ** 2
    x0, y0, x1, y1 = pieces[0]
    assert (x1 - x0) * (y1 - y0) >= window / 2


def test_a_page_that_turns_out_quick_is_drawn_whole_in_the_same_turn(view, qapp, monkeypatch, drawings):
    """A page is taken as slow, and its first piece shows it is quick: the rest is drawn whole at
    once. The page is made quick after its first piece by raising the budget."""
    monkeypatch.setattr(viewer.pdf_view, "_DRAW_BUDGET_S", _DRAW_BUDGET_S)
    _drain(qapp, view)
    view._draw_rate[0] = 1e-3               # slow: 1 ms a pixel
    real = PdfView._draw_piece

    def first_then_quick(self, index, piece):
        real(self, index, piece)
        monkeypatch.setattr(viewer.pdf_view, "_DRAW_BUDGET_S", 1e9)

    monkeypatch.setattr(PdfView, "_draw_piece", first_then_quick)
    drawings.clear()

    view.set_zoom(3.0)

    assert drawings[0] == ("piece", 0) and ("page", 0) in drawings
    assert _has_own_picture(view, 0)


# ---- carried pictures: zoom, screen, edit, minimize ----------------------------------


def test_a_minimized_window_keeps_a_quarter_size_copy(view, qapp, slow):
    _zoom(qapp, view, 0.6)
    _drain(qapp, view)
    shown = view.mapToScene(view.viewport().rect()).boundingRect().intersected(
        view._pages[0]["bg"].sceneBoundingRect())

    view.release_pixmaps(keep_visible=False)

    carried = view._carried[0]
    assert carried.pixmap.width() == pytest.approx(shown.width() * view._dpr / 4, abs=2)
    assert len(view._cache) == 0 and not view._wip


def test_a_restore_shows_the_copy_at_once_and_then_draws(view, qapp, slow):
    _zoom(qapp, view, 0.6)
    _drain(qapp, view)
    view.release_pixmaps(keep_visible=False)

    view.restore_pixmaps()

    carry = view._pages[0]["carry"]
    assert not carry.pixmap().isNull() and carry.scale() > 3    # the blurry copy, stretched
    _drain(qapp, view)
    assert _has_own_picture(view, 0)
    assert 0 not in view._carried


def test_a_move_to_another_screen_stretches_then_draws_for_it(view, qapp, slow):
    _zoom(qapp, view, 0.6)
    _drain(qapp, view)
    width = view._pages[0]["pix"].pixmap().width()

    view._dpr = 2.0
    view._apply_display_change("render")

    item = view._pages[0]["pix"]
    assert item.scale() != 1.0 and item.pixmap().width() == width     # the old picture, stretched
    shown = item.sceneBoundingRect()                                    # over exactly the page
    assert shown.width() == pytest.approx(view._pages[0]["w"], abs=1)
    assert shown.height() == pytest.approx(view._pages[0]["h"], abs=1)
    _drain(qapp, view)
    assert _has_own_picture(view, 0)
    assert view._pages[0]["pix"].pixmap().width() == pytest.approx(2 * width, abs=2)


def test_an_edit_that_moves_no_page_keeps_the_old_picture_until_the_new_one_is_drawn(view, qapp, slow):
    _zoom(qapp, view, 0.6)
    _drain(qapp, view)

    view.reload()

    carry = view._pages[0].get("carry")
    assert carry is not None and not carry.pixmap().isNull()
    _drain(qapp, view)
    assert _has_own_picture(view, 0)


def test_an_edit_that_moves_pages_drops_the_old_pictures(view, qapp, slow):
    """After a page is deleted, the picture carried for page 1 shows what is now another page. At
    this zoom the pages are bigger than the window, so with no timing they stay slow and would
    show it."""
    _zoom(qapp, view, 2.0)
    _drain(qapp, view)

    view._vdoc.delete_page(0)
    view.reload()

    assert not view._carried
    carry = view._pages[0].get("carry")
    assert carry is None or carry.pixmap().isNull()


def _part_shown(view, index: int, rect) -> tuple[float, float, float, float]:
    """The part of page ``index`` that scene rectangle ``rect`` covers, as fractions of the page:
    left, top, right, bottom."""
    page = view._pages[index]["bg"].sceneBoundingRect()
    return ((rect.left() - page.left()) / page.width(), (rect.top() - page.top()) / page.height(),
            (rect.right() - page.left()) / page.width(), (rect.bottom() - page.top()) / page.height())


def _assert_covers(view, index: int, item, part) -> None:
    """``item`` covers ``part`` of page ``index`` (fractions, as :func:`_part_shown`), to a pixel."""
    page = view._pages[index]["bg"].sceneBoundingRect()
    got = item.sceneBoundingRect()
    want = (page.left() + part[0] * page.width(), page.top() + part[1] * page.height(),
            page.left() + part[2] * page.width(), page.top() + part[3] * page.height())
    assert (got.left(), got.top(), got.right(), got.bottom()) == pytest.approx(want, abs=1)


@pytest.mark.parametrize("dpr", [1.0, 2.0])
@pytest.mark.parametrize("change", ["zoom in", "wider window at Fit Width", "screen with another DPI", "edit"])
def test_the_old_picture_stands_in_over_the_whole_page(view, qapp, slow, change, dpr):
    """While a page is drawn again, its old picture covers the whole page at the new size.

    Two faults once put it elsewhere, both seen on the NADA cover. Its size in points was measured
    after the scale had already changed, so a zoom in showed it at its old size in the top-left of
    the page, and each piece then replaced a different part of the page. And its size on screen
    counted its pixels as points, so with two pixels to a point it covered a quarter of the page.
    """
    view._dpr = dpr
    view._apply_display_change("render")
    if change == "wider window at Fit Width":
        _drain(qapp, view)
        view.fit_width()
    else:
        _zoom(qapp, view, 0.6)
    _drain(qapp, view)
    index = view.current_page
    assert _has_own_picture(view, index)
    view._cache.clear(keep_pinned=False)   # with nothing stored to stand in, the old picture does

    if change == "zoom in":
        view.set_zoom(0.75)
    elif change == "wider window at Fit Width":
        view.resize(800, 700)
        qapp.processEvents()
    elif change == "screen with another DPI":
        view._logical_dpi = 120.0
        view._apply_display_change("layout")
    else:
        view.reload()

    carry = view._pages[index].get("carry")
    assert carry is not None and not carry.pixmap().isNull()
    _assert_covers(view, index, carry, (0.0, 0.0, 1.0, 1.0))


@pytest.mark.parametrize("dpr", [1.0, 2.0])
def test_after_a_zoom_out_the_old_pieces_cover_the_part_of_the_page_they_showed(view, qapp, slow, dpr):
    """A page zoomed in this far is drawn in pieces, and is never drawn whole: only near the
    window. What the window shows of it is kept as one picture, and after a zoom out it covers
    that part of the page. (The pieces are not all drawn first: with two pixels to a point there
    are too many for the test to wait for, and the picture is made the same way from one.)"""
    view._dpr = dpr
    view._apply_display_change("render")
    _zoom(qapp, view, 2.0)
    index = view.current_page
    assert view._wip[index].pieces
    part = _part_shown(view, index, view.mapToScene(view.viewport().rect()).boundingRect().intersected(
        view._pages[index]["bg"].sceneBoundingRect()))
    view._cache.clear(keep_pinned=False)

    view.set_zoom(1.6)

    _assert_covers(view, index, view._pages[index]["carry"], part)


def test_a_slow_page_with_no_picture_gets_a_quick_low_resolution_one(view, qapp, slow):
    _zoom(qapp, view, 2.0)
    _drain(qapp, view)

    view.set_night_mode(True)          # empties the store and drops every picture on screen

    item = view._pages[0]["pix"]
    low = 0.25 * view._logical_dpi / 72 * view._dpr         # drawn at 25%
    assert item.pixmap().width() == pytest.approx(595 * low, abs=2)
    assert item.scale() > 3                                  # stretched to the page
    assert _image(item.pixmap()).pixelColor(2, 2).lightness() < 30   # in the new palette
    _drain(qapp, view)


def test_night_mode_never_shows_a_picture_in_the_old_palette(view, qapp, slow):
    """At 40% a low-resolution picture would be hardly smaller than the page, so none is drawn:
    the page is white, or here black, until its pieces arrive. Never the old light picture."""
    _zoom(qapp, view, 0.4)
    _drain(qapp, view)

    view.set_night_mode(True)

    item = view._pages[0]["pix"]
    assert item.pixmap().isNull() or _image(item.pixmap()).pixelColor(2, 2).lightness() < 30
    _drain(qapp, view)
    assert _image(view._pages[0]["pix"].pixmap()).pixelColor(2, 2).lightness() < 30
