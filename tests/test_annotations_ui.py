"""Viewer annotation interaction (PLAN.md, M20 — PR-B). Offscreen GUI.

Select-then-highlight, the text-box placement tool, and the overlay preview — driven through a real
MainWindow on the page-edit-layer model from PR-A.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from app import PdfApp
from model.page_edits import Highlight, TextBox
from store.settings import Settings
from viewer.tools import ArmedTool, InteractionMode


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def win(qapp, a_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    w = qapp.open_document(a_pdf)
    w.show()
    qapp.processEvents()
    yield w
    w.undo_stack.setClean()  # avoid the dirty-close prompt blocking teardown
    w.close()


def _first_word_center(win):
    ref = win.vdoc.ordered[0]
    page = win.vdoc.sources[ref.source_id][ref.source_page_index]
    box = page.get_text("words")[0][:4]
    return win.view.scene_rect_for_box(0, box).center()


def test_highlight_selection_creates_highlight(win):
    win.view.selection.select_word_at(_first_word_center(win))  # select a word
    assert win.view.selection.selected_words()
    win._highlight_selection()
    annots = win.vdoc.page_annotations(0)
    assert any(isinstance(a, Highlight) for a in annots)


def test_highlight_with_no_selection_does_nothing(win):
    win.view.selection.clear()
    win._highlight_selection()
    assert win.vdoc.page_annotations(0) == ()


def test_highlight_unions_words_into_one_bar_per_line(win):
    # Two words on line (block 0, line 0) and one on line (0, 1): expect two continuous bars,
    # the first spanning both words (no inter-word gap).
    words = [
        (10, 10, 30, 20, "foo", 0, 0, 0),
        (35, 10, 60, 20, "bar", 0, 0, 1),
        (10, 30, 40, 40, "baz", 0, 1, 0),
    ]
    win.view.selection.selected_words = lambda: [(0, i, w) for i, w in enumerate(words)]
    win._highlight_selection()
    hl = next(a for a in win.vdoc.page_annotations(0) if isinstance(a, Highlight))
    assert len(hl.rects) == 2                       # one bar per line, not per word
    line0 = next(r for r in hl.rects if r[1] == 10)
    assert line0[0] == 10 and line0[2] == 60        # unioned across both words on the line


def test_textbox_tool_places_annotation(win):
    win.view.arm(ArmedTool.TEXTBOX)
    center = win.view.scene_rect_for_box(0, (100, 120, 300, 160)).center()
    assert win.view.annotations.place_textbox(center) is True
    win.view.annotations._editor.setPlainText("Hello note")
    win.view.annotations._commit_textbox()
    annots = win.vdoc.page_annotations(0)
    assert any(isinstance(a, TextBox) and a.text == "Hello note" for a in annots)


def test_textbox_editor_follows_zoom(win):
    win.view.arm(ArmedTool.TEXTBOX)
    win.view.annotations.place_textbox(win.view.scene_rect_for_box(0, (100, 120, 300, 160)).center())
    before = win.view.annotations._editor.geometry()
    win.view.set_zoom(win.view.zoom * 1.5)
    assert win.view.annotations._editor.geometry() != before  # editor tracked the zoom


def test_empty_textbox_adds_nothing(win):
    win.view.arm(ArmedTool.TEXTBOX)
    win.view.annotations.place_textbox(win.view.scene_rect_for_box(0, (100, 120, 300, 160)).center())
    win.view.annotations._editor.setPlainText("   ")  # whitespace only
    win.view.annotations._commit_textbox()
    assert win.vdoc.page_annotations(0) == ()


def test_textbox_mode_click_routes_to_tool(win):
    win.view.arm(ArmedTool.TEXTBOX)
    center = win.view.scene_rect_for_box(0, (100, 120, 300, 160)).center()
    vp = QPointF(win.view.mapFromScene(center))
    press = QMouseEvent(QEvent.Type.MouseButtonPress, vp, vp, Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    win.view.mousePressEvent(press)
    assert win.view.annotations._editor is not None  # the click opened the inline editor


def test_overlay_paints_existing_annotations(win):
    win.vdoc.add_annotation(0, Highlight(((72, 72, 160, 92),)))
    win.vdoc.add_annotation(0, TextBox((72, 150, 300, 180), "note"))
    win.view.annotations.repaint()
    assert len(win.view.annotations._items) >= 2  # highlight rect + text-box box/text


def test_annotation_at_hit_test_and_remove(win):
    tb = TextBox((100, 120, 300, 160), "removable")
    win.vdoc.add_annotation(0, tb)
    win.view.annotations.repaint()
    center = win.view.scene_rect_for_box(0, (100, 120, 300, 160)).center()
    hit = win.view.annotations.annotation_at(center)
    assert hit is not None and hit[1] is tb           # right-click finds the annotation
    win.view.annotations.remove(hit[0], hit[1])       # what the context menu calls
    assert tb not in win.vdoc.page_annotations(0)     # removed (undoable)


def test_annotation_at_returns_none_off_annotation(win):
    win.vdoc.add_annotation(0, TextBox((100, 120, 300, 160), "x"))
    win.view.annotations.repaint()
    off = win.view.scene_rect_for_box(0, (100, 400, 200, 430)).center()  # empty area
    assert win.view.annotations.annotation_at(off) is None


# ---- move / re-edit / auto-grow (text box, SELECT mode) -------------------------

_TB = (100, 120, 300, 160)


def test_drag_moves_textbox_and_is_undoable(win):
    win.vdoc.add_annotation(0, TextBox(_TB, "movable"))
    win.view.annotations.repaint()
    ov = win.view.annotations
    z = win.view.zoom
    start = win.view.scene_rect_for_box(0, _TB).center()
    assert ov.begin_move(start) is True
    assert ov.moving is True
    ov.update_move(QPointF(start.x() + 40 * z, start.y() + 25 * z))
    ov.finish_move()
    assert ov.moving is False
    box = next(a for a in win.vdoc.page_annotations(0) if isinstance(a, TextBox))
    assert box.rect != _TB and box.text == "movable"   # moved, text preserved
    win.undo_stack.undo()
    assert win.vdoc.page_annotations(0)[0].rect == _TB  # one undo restores the position


def test_begin_move_ignores_non_textbox(win):
    win.vdoc.add_annotation(0, Highlight(((100, 120, 300, 140),)))
    win.view.annotations.repaint()
    center = win.view.scene_rect_for_box(0, (100, 120, 300, 140)).center()
    assert win.view.annotations.begin_move(center) is False  # highlights aren't move-draggable


def test_double_click_reedits_textbox_text(win):
    win.vdoc.add_annotation(0, TextBox(_TB, "old"))
    win.view.annotations.repaint()
    ov = win.view.annotations
    assert ov.edit_textbox_at(win.view.scene_rect_for_box(0, _TB).center()) is True
    ov._editor.setPlainText("edited")
    ov._commit_textbox()
    box = next(a for a in win.vdoc.page_annotations(0) if isinstance(a, TextBox))
    assert box.text == "edited"


def test_emptying_textbox_on_reedit_removes_it(win):
    win.vdoc.add_annotation(0, TextBox(_TB, "remove me"))
    win.view.annotations.repaint()
    ov = win.view.annotations
    ov.edit_textbox_at(win.view.scene_rect_for_box(0, _TB).center())
    ov._editor.setPlainText("   ")  # cleared
    ov._commit_textbox()
    assert not any(isinstance(a, TextBox) for a in win.vdoc.page_annotations(0))


def test_textbox_editor_autogrows_height_with_lines(win):
    win.view.arm(ArmedTool.TEXTBOX)
    ov = win.view.annotations
    ov.place_textbox(win.view.scene_rect_for_box(0, (90, 90, 110, 110)).center())
    ov._editor.setPlainText("one line")
    short_h = ov._editor_rect[3] - ov._editor_rect[1]
    ov._editor.setPlainText("l1\nl2\nl3\nl4\nl5\nl6")
    tall_h = ov._editor_rect[3] - ov._editor_rect[1]
    assert tall_h > short_h  # height grew to fit more lines


def test_textbox_editor_autogrows_width_with_long_line(win):
    win.view.arm(ArmedTool.TEXTBOX)
    ov = win.view.annotations
    ov.place_textbox(win.view.scene_rect_for_box(0, (60, 90, 80, 110)).center())
    ov._editor.setPlainText("x")
    narrow_w = ov._editor_rect[2] - ov._editor_rect[0]
    ov._editor.setPlainText("a substantially longer single line of note text here")
    wide_w = ov._editor_rect[2] - ov._editor_rect[0]
    assert wide_w > narrow_w  # width grew toward the longest line (clamped to the page)


def test_arming_lights_button_toggles_and_disarms_on_mode_switch(win):
    acts = win._armed_actions
    win._arm_tool(ArmedTool.TEXTBOX)
    assert win.view.armed is ArmedTool.TEXTBOX
    assert acts[ArmedTool.TEXTBOX].isChecked()
    assert not any(a.isChecked() for t, a in acts.items() if t is not ArmedTool.TEXTBOX)
    win._arm_tool(ArmedTool.REDACT_REGION)                # arming another swaps the lit button
    assert win.view.armed is ArmedTool.REDACT_REGION
    assert acts[ArmedTool.REDACT_REGION].isChecked() and not acts[ArmedTool.TEXTBOX].isChecked()
    win.view.set_mode(InteractionMode.SELECT)             # picking a mode disarms
    assert win.view.armed is None
    assert not any(a.isChecked() for a in acts.values())
    win._arm_tool(ArmedTool.HIGHLIGHT)
    win._arm_tool(ArmedTool.HIGHLIGHT)                     # clicking the lit tool again disarms it
    assert win.view.armed is None


def _drag_over_first_word(win):
    """Press-drag across page 0's first word via real mouse events; returns nothing (side effects
    apply the armed tool on release)."""
    box = _first_word_center(win)  # scene center of the first word
    ref = win.vdoc.ordered[0]
    page = win.vdoc.sources[ref.source_id][ref.source_page_index]
    rect = win.view.scene_rect_for_box(0, page.get_text("words")[0][:4])
    p0 = QPointF(win.view.mapFromScene(QPointF(rect.left() + 1, rect.center().y())))
    p1 = QPointF(win.view.mapFromScene(QPointF(rect.right() - 1, rect.center().y())))
    win.view.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, p0, p0,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    win.view.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, p1, p1,
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    win.view.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, p1, p1,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))


def test_armed_highlight_drag_over_text_applies_and_disarms(win):
    win.view.arm(ArmedTool.HIGHLIGHT)
    _drag_over_first_word(win)
    assert win.view.armed is None  # one-shot reverts to Select after the gesture
    assert any(isinstance(a, Highlight) for a in win.vdoc.page_annotations(0))


def test_cross_window_paste_carries_annotations(qapp, a_pdf, b_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    src = qapp.open_document(a_pdf)
    dst = qapp.open_document(b_pdf)
    try:
        src.vdoc.add_annotation(0, TextBox((72, 150, 300, 180), "carry me"))
        src._copy_pages([0])
        dst._paste_pages(0)
        annots = dst.vdoc.page_annotations(0)
        assert any(isinstance(a, TextBox) and a.text == "carry me" for a in annots)
    finally:
        src.undo_stack.setClean()
        dst.undo_stack.setClean()
        src.close()
        dst.close()


def test_placement_off_page_is_rejected(win):
    win.view.arm(ArmedTool.TEXTBOX)
    # A point in the vertical band of page 0 but far past its right edge (in the margin).
    pw, _ = win.view._unrotated_size(0)
    off = win.view.scene_rect_for_box(0, (pw + 50, 100, pw + 120, 130)).center()
    assert win.view.annotations.place_textbox(off) is False


def test_editor_font_matches_box_and_fits_text(win):
    """The editor font is the box font at the current zoom, and the box is wide enough for the text
    at that font — so the rendered text never spills past the box edge."""
    from PySide6.QtGui import QFontMetricsF

    win.view.arm(ArmedTool.TEXTBOX)
    ov = win.view.annotations
    ov.place_textbox(win.view.scene_rect_for_box(0, (80, 100, 100, 120)).center())
    ov._editor.setPlainText("Some sample annotation text")
    z = win.view.zoom
    assert ov._editor.font().pixelSize() == max(1, round(11 * z))  # WYSIWYG: 11 pt × zoom
    fm = QFontMetricsF(ov._editor.font())
    box_w_px = (ov._editor_rect[2] - ov._editor_rect[0]) * z
    assert box_w_px >= fm.horizontalAdvance("Some sample annotation text")  # fits → no spill


def test_page_transform_matches_scene_rect_for_box_when_rotated(win):
    """The text-box paint transform maps page points to the same place the proven box mapping does,
    on a per-page-rotated page — so the box (and its text) rotate with the page."""
    from PySide6.QtCore import QRectF

    win.vdoc.set_rotation(0, 90)
    win.view.reload()
    rect = (100, 120, 260, 150)
    transform = win.view.page_transform(0)
    pts = [
        transform.map(QPointF(rect[0], rect[1])),
        transform.map(QPointF(rect[2], rect[1])),
        transform.map(QPointF(rect[2], rect[3])),
        transform.map(QPointF(rect[0], rect[3])),
    ]
    xs, ys = [p.x() for p in pts], [p.y() for p in pts]
    mapped = QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
    expected = win.view.scene_rect_for_box(0, rect)
    assert abs(mapped.x() - expected.x()) < 1 and abs(mapped.y() - expected.y()) < 1
    assert abs(mapped.width() - expected.width()) < 1 and abs(mapped.height() - expected.height()) < 1


def test_move_on_rotated_page_follows_cursor(win):
    """On a 90°-rotated page a downward screen-drag moves the box *down* on screen (not sideways) —
    the move delta is taken in the page's own frame, not raw scene axes."""
    win.vdoc.add_annotation(0, TextBox((100, 120, 260, 150), "r"))
    win.vdoc.set_rotation(0, 90)
    win.view.reload()
    ov = win.view.annotations
    start = win.view.scene_rect_for_box(0, (100, 120, 260, 150)).center()
    assert ov.begin_move(start) is True
    ov.update_move(QPointF(start.x(), start.y() + 80))  # drag straight down on screen
    ov.finish_move()
    moved = next(a for a in win.vdoc.page_annotations(0) if isinstance(a, TextBox))
    new_center = win.view.scene_rect_for_box(0, moved.rect).center()
    assert new_center.y() > start.y() + 20          # followed the cursor downward …
    assert abs(new_center.x() - start.x()) < 20     # … and did not drift sideways
