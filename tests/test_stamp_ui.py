"""Stamp / signature / watermark UI (PLAN.md §R4, M62). Offscreen GUI + headless page-range.

M61 built the engine; this is the placement layer over it. The design claim under test is that
**there is no second placement system**: a content mark is a free-placed rect, so arming a drag,
moving it, resizing it and stacking it all come from the M58/M59 object tools that already existed.
The tests therefore drive the *existing* gestures and assert they work on a stamp.

Plus the two flows that differ — a stamp is composed then placed, a watermark applies straight to a
page range — and the page-range grammar both share.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from app import PdfApp
from main_window import MainWindow
from model.content_marks import ImageStamp, Stamp
from store.settings import Settings
from util.page_range import PageRangeError, format_page_range, parse_page_range
from viewer.tools import ArmedTool


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


def _scene(win, x: float, y: float, page: int = 0):
    return win.view.scene_rect_for_box(page, (x, y, x + 0.01, y + 0.01)).center()


def _place(win, mark, start=(100, 300), end=(300, 360), pages=None, page=0):
    """Compose → arm → drag the box, the M62 stamp flow end to end."""
    win._arm_content_mark(mark, pages)
    overlay = win.view.annotations
    assert overlay.begin_draw(ArmedTool.STAMP, _scene(win, *start, page)) is True
    overlay.update_draw(_scene(win, *end, page), Qt.KeyboardModifier.NoModifier)
    overlay.finish_draw()


def _stamps(win, page: int = 0):
    return [a for a in win.vdoc.page_annotations(page) if isinstance(a, Stamp)]


TEMPLATE = Stamp(rect=(0.0, 0.0, 1.0, 1.0), text="APPROVED")


# ---- placement: compose, arm, drag the box --------------------------------------


def test_placing_a_stamp_puts_it_where_it_was_dragged(win):
    _place(win, TEMPLATE, start=(100, 300), end=(300, 360))
    stamp = _stamps(win)[0]
    assert stamp.text == "APPROVED"
    assert stamp.rect == pytest.approx((100, 300, 300, 360), abs=1.0)


def test_the_arm_is_one_shot(win):
    """Like every other armed tool: the composed mark is consumed by the placement, so a second
    drag needs a second trip through the dialog rather than silently stamping again."""
    _place(win, TEMPLATE)
    assert win.view.annotations.pending_content_mark is None
    overlay = win.view.annotations
    overlay.begin_draw(ArmedTool.STAMP, _scene(win, 100, 500))
    overlay.update_draw(_scene(win, 200, 540), Qt.KeyboardModifier.NoModifier)
    overlay.finish_draw()
    assert len(_stamps(win)) == 1


def test_a_stray_click_places_nothing(win):
    """A click with no drag is not a zero-sized stamp."""
    win._arm_content_mark(TEMPLATE)
    overlay = win.view.annotations
    overlay.begin_draw(ArmedTool.STAMP, _scene(win, 100, 300))
    overlay.finish_draw()
    assert _stamps(win) == []


def test_placing_a_stamp_is_one_undo_step(win):
    _place(win, TEMPLATE)
    assert len(_stamps(win)) == 1
    win.undo_stack.undo()
    assert _stamps(win) == []


def test_shift_squares_the_placement_box(win):
    win._arm_content_mark(TEMPLATE)
    overlay = win.view.annotations
    overlay.begin_draw(ArmedTool.STAMP, _scene(win, 100, 300))
    overlay.update_draw(_scene(win, 300, 340), Qt.KeyboardModifier.ShiftModifier)
    overlay.finish_draw()
    x0, y0, x1, y1 = _stamps(win)[0].rect
    assert (x1 - x0) == pytest.approx(y1 - y0, abs=1.0)


def test_an_image_stamp_places_the_same_way(win, tmp_path):
    """One placement gesture for both payloads — the M61 'one engine' claim, at the UI layer."""
    import pymupdf as fitz

    path = str(tmp_path / "sig.png")
    fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 10), False).save(path)
    _place(win, ImageStamp(rect=(0.0, 0.0, 1.0, 1.0), image_path=path))
    marks = [a for a in win.vdoc.page_annotations(0) if isinstance(a, ImageStamp)]
    assert len(marks) == 1
    assert marks[0].image_path == path


# ---- a placed stamp is an ordinary object (the whole reuse argument) -------------


def test_a_placed_stamp_is_hit_testable_and_selectable(win):
    _place(win, TEMPLATE, start=(100, 300), end=(300, 360))
    overlay = win.view.annotations
    assert overlay.select_object_at(_scene(win, 200, 330)) is True
    page_index, mark = overlay.selected_object
    assert (page_index, mark) == (0, _stamps(win)[0])


def test_a_placed_stamp_moves_with_the_object_drag(win):
    _place(win, TEMPLATE, start=(100, 300), end=(300, 360))
    overlay = win.view.annotations
    assert overlay.begin_move(_scene(win, 200, 330)) is True
    overlay.update_move(_scene(win, 240, 350))
    overlay.finish_move()
    x0, y0, _x1, _y1 = _stamps(win)[0].rect
    assert (x0, y0) == pytest.approx((140, 320), abs=1.5)


def test_a_placed_stamp_resizes_by_its_handles(win):
    """Stretching is correct for a stamp (unlike a text box, which only repositions): the rect is
    the box the artwork is fitted into, so resizing it *is* resizing the stamp."""
    _place(win, TEMPLATE, start=(100, 300), end=(300, 360))
    overlay = win.view.annotations
    overlay.select_object(0, _stamps(win)[0])
    assert overlay.begin_resize("se", _scene(win, 300, 360)) is True
    overlay.update_resize(_scene(win, 360, 400), Qt.KeyboardModifier.NoModifier)
    overlay.finish_resize()
    assert _stamps(win)[0].rect == pytest.approx((100, 300, 360, 400), abs=1.5)


def test_a_placed_stamp_deletes_like_any_object(win):
    _place(win, TEMPLATE)
    overlay = win.view.annotations
    overlay.select_object(0, _stamps(win)[0])
    assert overlay.remove_selected_objects() is True
    assert _stamps(win) == []


def test_the_pen_style_picker_leaves_a_stamp_alone(win):
    """A stamp's style comes from its own dialog, so the pen & shapes picker must not touch it —
    `restyle_mark` returning None for a content mark is what makes that automatic."""
    from viewer.markup_style import MarkupStyle

    _place(win, TEMPLATE)
    before = _stamps(win)[0]
    overlay = win.view.annotations
    overlay.select_object(0, before)
    assert overlay.restyle_selected_objects(MarkupStyle(color=(0, 0, 1.0))) is False
    assert _stamps(win)[0] == before


def test_a_placed_stamp_paints_a_preview_item(win):
    """The preview is rendered through the same generator that bakes at save, so placing a stamp
    must actually produce a scene item — a silent no-paint would look like nothing happened."""
    before = len(win.view.annotations._items)
    _place(win, TEMPLATE)
    win.view.annotations.repaint()
    assert len(win.view.annotations._items) > before


# ---- preview fidelity: the on-screen mark is the size the saved one will be -------
#
# `show_pdf_page` fits a mark's *rotated* artwork inside its rect and centres it, so a rotated mark
# bakes smaller than its box. The preview has to apply the same shrink. It did not at first — a 45°
# watermark previewed ~1.8x too large, which no assertion here caught until the two were rendered
# side by side. These pin it.


@pytest.mark.parametrize("angle,expected", [
    (0.0, 1.0),
    (90.0, 595 / 842),  # a quarter turn makes a portrait mark landscape — it must shrink to fit
    (-45.0, 0.5856),    # 595x842 at 45°: min(595, 842) / ((595+842)·cos45)
    (45.0, 0.5856),     # direction does not change the shrink
])
def test_rotation_fit_matches_show_pdf_page(angle, expected):
    from viewer.annotations import _rotation_fit

    assert _rotation_fit((0, 0, 595, 842), angle) == pytest.approx(expected, abs=0.002)


def _content_item(win):
    from PySide6.QtWidgets import QGraphicsPixmapItem

    items = [i for i in win.view.annotations._items if isinstance(i, QGraphicsPixmapItem)]
    assert len(items) == 1
    return items[0]


def test_an_unrotated_stamp_preview_fills_its_rect(win):
    _place(win, TEMPLATE, start=(100, 300), end=(300, 360))
    win.view.annotations.repaint()
    painted = _content_item(win).sceneBoundingRect()
    target = win.view.scene_rect_for_box(0, _stamps(win)[0].rect)
    assert painted.width() == pytest.approx(target.width(), rel=0.02)
    assert painted.height() == pytest.approx(target.height(), rel=0.02)


def test_a_rotated_stamp_preview_stays_inside_its_rect(win):
    """The regression: with the shrink missing, the painted item spilled well outside its box."""
    _place(win, Stamp(rect=(0.0, 0.0, 1.0, 1.0), text="TILTED", angle=-45.0),
           start=(100, 300), end=(300, 360))
    win.view.annotations.repaint()
    painted = _content_item(win).sceneBoundingRect()
    target = win.view.scene_rect_for_box(0, _stamps(win)[0].rect)
    assert target.adjusted(-2, -2, 2, 2).contains(painted)
    assert painted.center().x() == pytest.approx(target.center().x(), abs=1.5)
    assert painted.center().y() == pytest.approx(target.center().y(), abs=1.5)


# ---- page range: "initials on every page" ---------------------------------------


def test_a_page_range_stamps_every_page_in_one_undo_step(win):
    _place(win, TEMPLATE, pages=[0, 1, 2])
    assert len(_stamps(win, 0)) == 1
    assert len(_stamps(win, 1)) == 1
    assert len(_stamps(win, 2)) == 1
    win.undo_stack.undo()                       # one macro, not three separate adds
    assert _stamps(win, 0) == _stamps(win, 1) == _stamps(win, 2) == []


def test_the_range_lands_at_the_same_rect_on_each_page(win):
    _place(win, TEMPLATE, start=(100, 300), end=(300, 360), pages=[0, 1, 2])
    rects = {_stamps(win, i)[0].rect for i in range(3)}
    assert len(rects) == 1                      # one rect — the point of "on every page"


def test_a_single_page_range_is_a_plain_add(win):
    """Scope 'This page' must not go through the macro path — a one-page 'range' is just a stamp."""
    _place(win, TEMPLATE, pages=[0])
    assert len(_stamps(win, 0)) == 1
    assert _stamps(win, 1) == []


def test_the_pending_range_does_not_leak_into_the_next_mark(win):
    """The range belongs to the placement that was armed with it, not to the window."""
    _place(win, TEMPLATE, pages=[0, 1, 2])
    _place(win, Stamp(rect=(0.0, 0.0, 1.0, 1.0), text="SECOND"), start=(50, 500), end=(150, 540))
    seconds = [i for i in range(3) if any(s.text == "SECOND" for s in _stamps(win, i))]
    assert seconds == [0]


# ---- watermarks: applied, not placed --------------------------------------------


def test_watermark_covers_each_page_at_its_own_size(win, monkeypatch):
    """Applied straight across the range, full-page — and sized per page, so a mixed-size document
    does not inherit the current page's rect."""
    from ui.stamp_dialog import WatermarkDialog

    monkeypatch.setattr(WatermarkDialog, "exec", lambda self: 1)
    monkeypatch.setattr(WatermarkDialog, "selected_pages", lambda self: [0, 1, 2])
    win._add_watermark()
    for index in range(3):
        mark = _stamps(win, index)[0]
        assert mark.under is True                     # under the content — that is what makes it one
        assert mark.rect == (0.0, 0.0, *win.view._unrotated_size(index))
    win.undo_stack.undo()
    assert _stamps(win, 0) == []                      # one undo step for the whole document


def test_watermark_dialog_defaults_are_translucent_and_diagonal(win):
    from ui.stamp_dialog import WatermarkDialog

    dialog = WatermarkDialog(win, 3, 0)
    mark = dialog.watermark((0, 0, 595, 842))
    assert mark.under is True
    assert mark.angle == -45.0
    assert 0 < mark.opacity < 0.5
    assert mark.border_width == 0.0                   # a frame would read as a stamp
    dialog.deleteLater()


def test_stamp_dialog_preset_prefills_but_stays_editable(win):
    """Way 2: a preset is a prefill of the custom generator, not a separate kind of stamp."""
    from ui.stamp_dialog import StampDialog

    dialog = StampDialog(win, 3, 0)
    dialog.presets.setCurrentText("Confidential")
    assert dialog.text.text() == "CONFIDENTIAL"
    dialog.text.setText("MY OWN WORDS")               # still editable after choosing a preset
    assert dialog.stamp().text == "MY OWN WORDS"
    dialog.deleteLater()


def test_stamp_dialog_frame_toggle_drives_border_width(win):
    from ui.stamp_dialog import StampDialog

    dialog = StampDialog(win, 3, 0)
    assert dialog.stamp().border_width > 0
    dialog.frame.setChecked(False)
    assert dialog.stamp().border_width == 0.0
    dialog.deleteLater()


# ---- page-range grammar (headless) ----------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("", [0, 1, 2, 3, 4]),          # empty means every page
    ("1", [0]),
    ("2-4", [1, 2, 3]),
    ("1,3,5", [0, 2, 4]),
    ("1-2, 4", [0, 1, 3]),
    ("3-", [2, 3, 4]),              # open-ended: "from here on"
    ("-2", [0, 1]),
    ("4-2", [1, 2, 3]),             # reversed spans are the same span
    ("1-3, 2-4", [0, 1, 2, 3]),     # overlaps collapse
    ("  2 , 4  ", [1, 3]),
    ("99", [4]),                    # out of range clamps rather than erroring
])
def test_parse_page_range(text, expected):
    assert parse_page_range(text, 5) == expected


@pytest.mark.parametrize("text", ["abc", "1,x", "0", "1-0"])
def test_bad_page_range_raises(text):
    with pytest.raises(PageRangeError):
        parse_page_range(text, 5)


def test_format_page_range_collapses_runs():
    assert format_page_range([0, 1, 2, 4]) == "1-3, 5"
    assert format_page_range([]) == ""
