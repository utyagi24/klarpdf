"""Dashed lines keep a **solid** arrowhead in the on-screen overlay (WYSIWYG fix).

The overlay used to stroke a line's shaft and its arrowheads as one path with a single pen, so a
dashed line got a dashed chevron. But the saved PDF renders an ``OPEN_ARROW`` ending **solid**
regardless of the border's dash pattern — so the preview disagreed with the file, and a dashed
chevron reads as broken anyway. The overlay now draws the shaft and the arrowheads as separate
items: the shaft takes the (maybe dashed) pen, the heads always a solid one — in both the committed
mark and the live drag preview.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGraphicsPathItem

from app import PdfApp
from main_window import MainWindow
from model.page_edits import Line
from store.settings import Settings
from viewer.annotations import _arrowheads_path, _line_shaft_path
from viewer.tools import ArmedTool


# ---- the geometry split (pure) -----------------------------------------------


def test_shaft_and_arrowheads_are_separate_paths():
    assert not _line_shaft_path((0.0, 0.0), (100.0, 0.0)).isEmpty()
    assert not _arrowheads_path((0.0, 0.0), (100.0, 0.0), False, True, 2.0).isEmpty()   # end arrow
    assert not _arrowheads_path((0.0, 0.0), (100.0, 0.0), True, True, 2.0).isEmpty()    # both ends
    assert _arrowheads_path((0.0, 0.0), (100.0, 0.0), False, False, 2.0).isEmpty()      # no arrow
    assert _arrowheads_path((5.0, 5.0), (5.0, 5.0), True, True, 2.0).isEmpty()          # zero length


# ---- the painted overlay (offscreen GUI) -------------------------------------


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    return qapp


@pytest.fixture
def win(app, a_pdf):
    w = MainWindow(app, a_pdf, app.settings)
    yield w
    w.undo_stack.setClean()
    w.close()


def _path_items(win):
    return [i for i in win.view.annotations._items if isinstance(i, QGraphicsPathItem)]


def _is_dashed(item) -> bool:
    return item.pen().style() != Qt.PenStyle.SolidLine


def test_dashed_arrow_line_paints_a_solid_arrowhead(win):
    win.vdoc.add_annotation(0, Line((100.0, 200.0), (260.0, 200.0), width=3.0,
                                    arrow_end=True, dashed=True))
    win.view.reload()
    items = _path_items(win)
    assert len(items) == 2                                  # shaft + a separate arrowhead item
    assert any(_is_dashed(i) for i in items)                # the shaft is dashed…
    assert any(not _is_dashed(i) for i in items)            # …but the arrowhead is solid


def test_dashed_line_without_an_arrow_has_no_extra_item(win):
    win.vdoc.add_annotation(0, Line((100.0, 200.0), (260.0, 200.0), width=3.0, dashed=True))
    win.view.reload()
    items = _path_items(win)
    assert len(items) == 1                                  # just the shaft — no arrowhead item
    assert _is_dashed(items[0])


def test_solid_arrow_line_is_unchanged(win):
    win.vdoc.add_annotation(0, Line((100.0, 200.0), (260.0, 200.0), width=3.0,
                                    arrow_end=True, dashed=False))
    win.view.reload()
    items = _path_items(win)
    assert len(items) == 2 and all(not _is_dashed(i) for i in items)   # both solid, as before


def test_live_dashed_arrow_preview_uses_a_solid_head_item(win):
    """The drag preview matches the committed mark: a separate, always-solid arrowhead item."""
    win.view.annotations.set_markup_style(
        win.view.annotations.current_markup_style.__class__(width=3.0, dashed=True,
                                                            line_ends=(False, True))
    )
    ov = win.view.annotations
    start = win.view.scene_rect_for_box(0, (100, 200, 100.01, 200.01)).center()
    end = win.view.scene_rect_for_box(0, (260, 200, 260.01, 200.01)).center()
    assert ov.begin_draw(ArmedTool.LINE, start) is True
    ov.update_draw(end)
    assert ov._draw_arrow_item is not None                  # a dedicated head item exists…
    assert ov._draw_arrow_item.pen().style() == Qt.PenStyle.SolidLine   # …and it is solid
    assert not ov._draw_arrow_item.path().isEmpty()         # with the head geometry drawn
    assert _is_dashed(ov._draw_item)                        # while the shaft preview is dashed
    ov.cancel_draw()
    assert ov._draw_arrow_item is None                      # cleaned up on cancel
