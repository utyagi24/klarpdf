"""Viewer annotation interaction (PLAN.md, M20 — PR-B). Offscreen GUI.

Select-then-highlight, the text-box placement tool, and the overlay preview — driven through a real
MainWindow on the page-edit-layer model from PR-A.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QGraphicsRectItem

from app import PdfApp
from model.page_edits import Highlight, TextBox
from store.settings import Settings
from viewer.text_format_bar import TextBoxStyle, TextFormatBar
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


def test_current_page_marker_survives_repopulate(win):
    win.thumbs.set_current(1)
    win.thumbs.populate()                 # an edit repopulates the list
    assert win.thumbs.currentRow() == 1   # the marker stays on the current page


def test_current_page_marker_survives_an_edit(win):
    win.thumbs.set_current(1)
    win._add_annotation(1, TextBox((100, 120, 300, 160), "edit"))  # pushes a command → repopulate
    assert win.thumbs.currentRow() == 1   # still highlighted after the edit


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


# ---- M27 styled text boxes: the formatting bar + style threading ----------------
#
# Colour pickers are modal QColorDialogs (they can't run offscreen), so these drive style through
# the overlay's current style + the bar's non-dialog controls (family / size / outline menus).

_STYLED = TextBoxStyle(fontname="cour", fontsize=16.0, color=(1.0, 0.0, 0.0),
                       fill_color=(0.2, 0.4, 0.9), border_width=1.0)


def _place_box(win, text, box=(100, 120, 300, 160)):
    win.view.arm(ArmedTool.TEXTBOX)
    win.view.annotations.place_textbox(win.view.scene_rect_for_box(0, box).center())
    win.view.annotations._editor.setPlainText(text)


def test_new_box_bakes_the_current_style(win):
    win.view.annotations.set_current_style(_STYLED)
    _place_box(win, "styled")
    win.view.annotations._commit_textbox()
    box = next(a for a in win.vdoc.page_annotations(0) if isinstance(a, TextBox))
    assert box.text == "styled"
    assert (box.fontname, box.fontsize, box.color) == ("cour", 16.0, (1.0, 0.0, 0.0))
    assert box.fill_color == (0.2, 0.4, 0.9) and box.border_width == 1.0


def test_format_bar_shows_with_editor_and_hides_on_commit(win):
    _place_box(win, "x")
    ov = win.view.annotations
    assert ov._format_bar is not None and not ov._format_bar.isHidden()  # bar up while editing
    ov._commit_textbox()
    assert ov._format_bar.isHidden()                                     # gone after commit


def test_bar_controls_update_overlay_style_and_editor(win):
    _place_box(win, "x")
    ov, bar = win.view.annotations, win.view.annotations._format_bar
    bar._set_family("cour")
    bar._set_size(18)
    bar._outline_btn.setChecked(True)
    bar._toggle_outline()
    assert ov.current_style.fontname == "cour"
    assert ov.current_style.fontsize == 18.0
    assert ov.current_style.border_width == 1.0
    assert ov._editor.font().pixelSize() == max(1, round(18 * win.view.zoom))  # editor tracked size


def test_reedit_loads_the_box_style_into_the_bar(win):
    win.vdoc.add_annotation(0, TextBox((100, 120, 300, 160), "styled", fontsize=20, color=(0, 0, 1),
                                       fontname="tiro", fill_color=(1, 1, 0), border_width=2.0))
    win.view.annotations.repaint()
    ov = win.view.annotations
    ov.edit_textbox_at(win.view.scene_rect_for_box(0, (100, 120, 300, 160)).center())
    loaded = TextBoxStyle(fontname="tiro", fontsize=20.0, color=(0, 0, 1),
                          fill_color=(1, 1, 0), border_width=2.0)
    assert ov.current_style == loaded
    assert ov._format_bar.style() == loaded


def test_style_only_change_on_reedit_is_committed(win):
    win.vdoc.add_annotation(0, TextBox((100, 120, 300, 160), "keep"))  # plain, no outline
    win.view.annotations.repaint()
    ov = win.view.annotations
    ov.edit_textbox_at(win.view.scene_rect_for_box(0, (100, 120, 300, 160)).center())
    ov._format_bar._outline_btn.setChecked(True)
    ov._format_bar._toggle_outline()       # change only the outline (text untouched)
    ov._commit_textbox()
    box = next(a for a in win.vdoc.page_annotations(0) if isinstance(a, TextBox))
    assert box.text == "keep" and box.border_width == 1.0   # restyled despite the same text


def test_move_preserves_style(win):
    win.vdoc.add_annotation(0, TextBox((100, 120, 300, 160), "m", **{
        "fontname": "cour", "fontsize": 14, "color": (0, 0, 1),
        "fill_color": (0.9, 0.9, 0.2), "border_width": 1.5}))
    win.view.annotations.repaint()
    ov = win.view.annotations
    start = win.view.scene_rect_for_box(0, (100, 120, 300, 160)).center()
    ov.begin_move(start)
    ov.update_move(QPointF(start.x() + 30 * win.view.zoom, start.y() + 20 * win.view.zoom))
    ov.finish_move()
    box = next(a for a in win.vdoc.page_annotations(0) if isinstance(a, TextBox))
    assert box.rect != (100, 120, 300, 160)                # it moved …
    assert (box.fontname, box.fill_color, box.border_width) == ("cour", (0.9, 0.9, 0.2), 1.5)  # … style kept


def test_modal_colour_picker_suppresses_focus_out_commit(win):
    _place_box(win, "z")
    ov = win.view.annotations
    ov._begin_modal()                       # a colour dialog is up → editor focus-out must not commit
    ov._commit_textbox()
    assert ov._editor is not None                       # editor still open
    assert win.vdoc.page_annotations(0) == ()           # nothing committed
    ov._end_modal()                         # dialog closed → focus back, commit re-enabled
    ov._commit_textbox()
    assert any(isinstance(a, TextBox) and a.text == "z" for a in win.vdoc.page_annotations(0))


def test_wysiwyg_preview_paints_fill_and_outline(win):
    win.vdoc.add_annotation(0, TextBox((100, 120, 300, 160), "p",
                                       fill_color=(0.2, 0.4, 0.9), border_width=2.0))
    win.view.annotations.repaint()
    rects = [it for it in win.view.annotations._items if isinstance(it, QGraphicsRectItem)]
    assert any(it.brush().style() != Qt.BrushStyle.NoBrush for it in rects)   # fill drawn
    assert any(it.pen().style() != Qt.PenStyle.NoPen for it in rects)         # outline drawn


# ---- the formatting bar in isolation (no MainWindow) ----------------------------


def test_format_bar_roundtrips_style_through_controls(qapp):
    bar = TextFormatBar(None)
    bar.set_style(_STYLED)
    assert bar.style() == _STYLED
    assert bar._build() == _STYLED          # controls reconstruct the same style (no dialog)
    bar.deleteLater()


def test_format_bar_outline_toggle_emits_styled(qapp):
    bar = TextFormatBar(None)
    seen = []
    bar.styleChanged.connect(seen.append)
    bar._outline_btn.setChecked(True)
    bar._toggle_outline()
    assert seen and seen[-1].border_width == 1.0
    bar.deleteLater()
