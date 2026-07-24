"""Shared markup / draw colour · width · fill picker (PLAN.md §GUI feature roadmap, M59.5).

M56/M58 baked the descriptor *defaults* (redline red, 2 pt, no fill) with no way to change them.
M59.5 adds one sticky :class:`~viewer.markup_style.MarkupStyle`, edited via the toolbar
``MarkupStyleButton`` and stamped onto the next underline / strikeout / pen / line / arrow / rect /
ellipse. No model or file-format change — the descriptors already carried (and round-tripped) the
style. Highlight (translucent yellow) and redaction (opaque black) stay out of the shared palette.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest
from PySide6.QtCore import Qt

from app import PdfApp
from main_window import MainWindow
from model.edit_engine import PyMuPDFEngine
from model.page_edits import (
    Highlight,
    InkStroke,
    Line,
    Shape,
    Strikeout,
    Underline,
)
from model.virtual_document import VirtualDocument
from store.settings import Settings
from viewer.markup_style import MarkupStyle, MarkupStyleButton
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


def _scene(win, x: float, y: float):
    return win.view.scene_rect_for_box(0, (x, y, x + 0.01, y + 0.01)).center()


def _drag(win, tool, start, *moves, modifiers=Qt.KeyboardModifier.NoModifier):
    overlay = win.view.annotations
    assert overlay.begin_draw(tool, _scene(win, *start)) is True
    for point in moves:
        overlay.update_draw(_scene(win, *point), modifiers)
    overlay.finish_draw()


def _only_mark(win, cls):
    marks = [a for a in win.vdoc.page_annotations(0) if isinstance(a, cls)]
    assert len(marks) == 1
    return marks[0]


def _select_first_word(win):
    ref = win.vdoc.ordered[0]
    page = win.vdoc.sources[ref.source_id][ref.source_page_index]
    word = page.get_text("words")[0]
    center = win.view.scene_rect_for_box(0, word[:4]).center()
    assert win.view.selection.select_word_at(center) is True


# ---- the style bundle --------------------------------------------------------


def test_defaults_match_the_legacy_fixed_style():
    """An untouched picker draws exactly as M58 did — redline red, 2 pt, no fill — so nothing
    about the existing behaviour changes until the user picks something."""
    s = MarkupStyle()
    assert s.color == pytest.approx((0.86, 0.10, 0.10))
    assert s.width == 2.0
    assert s.fill_color is None
    # ... and they equal the descriptor defaults the tools used before this milestone.
    assert Line((0, 0), (1, 1)).color == pytest.approx(s.color)
    assert InkStroke((((0, 0),),)).width == s.width


# ---- draw tools pick up the sticky style -------------------------------------


def test_pen_and_line_use_the_sticky_colour_and_width(win):
    win.view.annotations.set_markup_style(MarkupStyle(color=(0.0, 0.0, 1.0), width=4.0))
    _drag(win, ArmedTool.PEN, (100, 100), (140, 120), (170, 130))
    ink = _only_mark(win, InkStroke)
    assert ink.color == pytest.approx((0.0, 0.0, 1.0))
    assert ink.width == 4.0
    win.undo_stack.undo()
    _drag(win, ArmedTool.LINE, (100, 200), (220, 260))
    line = _only_mark(win, Line)
    assert line.color == pytest.approx((0.0, 0.0, 1.0)) and line.width == 4.0


def test_shape_carries_colour_width_and_fill(win):
    win.view.annotations.set_markup_style(
        MarkupStyle(color=(0.13, 0.60, 0.20), width=1.0, fill_color=(0.78, 0.86, 0.97))
    )
    _drag(win, ArmedTool.RECT, (200, 300), (280, 360))
    rect = _only_mark(win, Shape)
    assert rect.color == pytest.approx((0.13, 0.60, 0.20))
    assert rect.width == 1.0
    assert rect.fill_color == pytest.approx((0.78, 0.86, 0.97))


def test_ellipse_with_no_fill_stays_outline_only(win):
    win.view.annotations.set_markup_style(MarkupStyle(color=(0.0, 0.0, 0.0), fill_color=None))
    _drag(win, ArmedTool.ELLIPSE, (100, 100), (180, 160))
    assert _only_mark(win, Shape).fill_color is None


# ---- text markup is NOT on this picker (M59.9 moved it to its own palettes) --


def test_this_picker_does_not_colour_text_markup(win):
    """M59.5 routed underline/strikeout through the shared stroke picker; M59.9 moved text markup
    onto its own curated palettes, leaving this button meaning exactly "pen & shapes"."""
    win.view.annotations.set_markup_style(MarkupStyle(color=(0.0, 0.0, 1.0)))
    _select_first_word(win)
    win._underline_selection()
    assert _only_mark(win, Underline).color == pytest.approx(win._underline_color)
    assert _only_mark(win, Underline).color != pytest.approx((0.0, 0.0, 1.0))


def test_underline_and_strikeout_have_independent_colours(win):
    # M78.5: underline and strike out no longer share one line colour.
    win._set_underline_color((0.13, 0.35, 0.85))
    win._set_strike_color((0.13, 0.60, 0.20))
    _select_first_word(win)
    win._underline_selection()
    assert _only_mark(win, Underline).color == pytest.approx((0.13, 0.35, 0.85))
    win.undo_stack.undo()
    _select_first_word(win)
    win._apply_text_tool(ArmedTool.STRIKEOUT)
    assert _only_mark(win, Strikeout).color == pytest.approx((0.13, 0.60, 0.20))


def test_highlight_has_its_own_colour(win):
    """Highlight is a translucent wash, so it keeps a palette (and a default) of its own —
    neither the stroke picker nor the underline/strikeout colour touches it."""
    win.view.annotations.set_markup_style(MarkupStyle(color=(0.0, 0.0, 1.0)))
    win._set_underline_color((0.0, 0.0, 0.0))
    _select_first_word(win)
    win._highlight_selection()
    assert _only_mark(win, Highlight).color == pytest.approx(Highlight.color)  # yellow default
    win.undo_stack.undo()
    win._set_highlight_color((0.55, 0.92, 0.45))
    _select_first_word(win)
    win._highlight_selection()
    assert _only_mark(win, Highlight).color == pytest.approx((0.55, 0.92, 0.45))


# ---- the toolbar button seam -------------------------------------------------


def test_button_seeds_the_overlay_and_tracks_changes(win):
    """The overlay is the single source of truth; the button seeds it on start and pushes every
    later edit into it."""
    assert win.view.annotations.current_markup_style == win._markup_style_button.style()
    win._markup_style_button._set_color((0.13, 0.60, 0.20))   # what a preset click calls
    assert win.view.annotations.current_markup_style.color == pytest.approx((0.13, 0.60, 0.20))
    win._markup_style_button._set_width(4.0)
    assert win.view.annotations.current_markup_style.width == 4.0
    win._markup_style_button._set_fill((1.0, 0.94, 0.60))
    assert win.view.annotations.current_markup_style.fill_color == pytest.approx((1.0, 0.94, 0.60))


def test_button_menu_offers_colour_line_style_and_fill():
    btn = MarkupStyleButton()
    labels = [a.text() for a in btn.menu().actions() if not a.isSeparator()]
    # "Width" became "Line Style" when the solid/dashed choice joined the thickness options.
    assert "Custom Colour…" in labels and "Line Style" in labels and "Fill" in labels
    line_style = [a.text() for a in btn._width_menu.actions() if a.text()]
    assert line_style == ["Thin", "Medium", "Thick", "Solid", "Dashed"]
    fills = [a.text() for a in btn._fill_menu.actions()]
    assert fills[0] == "No Fill" and "Custom…" in fills


def test_set_style_does_not_emit():
    """Loading a style into the button (wiring it up) must not look like a user edit."""
    btn = MarkupStyleButton()
    seen = []
    btn.styleChanged.connect(seen.append)
    btn.set_style(MarkupStyle(color=(0.0, 0.0, 1.0)))
    assert seen == []


# ---- the picker's output round-trips through a save --------------------------


# ---- restyle a selected object via the picker (the text-markup 'apply to selection' rule) ----


def _add_and_select(win, mark):
    win.vdoc.add_annotation(0, mark)
    win.view.reload()
    got = _only_mark(win, type(mark))
    win.view.annotations.select_object(0, got)
    return got


def test_picker_restyles_the_selected_shape(win):
    _add_and_select(win, Shape("rect", (100.0, 100.0, 180.0, 150.0),
                               color=(0.86, 0.10, 0.10), width=2.0))
    win._markup_style_button._set_color((0.0, 0.0, 1.0))   # what a preset click emits
    restyled = _only_mark(win, Shape)
    assert restyled.color == pytest.approx((0.0, 0.0, 1.0))
    assert win.undo_stack.undoText() == "Restyle shape"
    win.undo_stack.undo()
    assert _only_mark(win, Shape).color == pytest.approx((0.86, 0.10, 0.10))


def test_partial_edit_keeps_the_objects_other_attributes(win):
    """Selecting loads the object's style into the picker, so nudging *one* control leaves the
    rest alone — change the width and the blue colour + fill survive."""
    _add_and_select(win, Shape("rect", (100.0, 100.0, 180.0, 150.0),
                               color=(0.0, 0.0, 1.0), width=4.0, fill_color=(1.0, 0.94, 0.60)))
    win._markup_style_button._set_width(1.0)
    s = _only_mark(win, Shape)
    assert s.width == 1.0
    assert s.color == pytest.approx((0.0, 0.0, 1.0))          # untouched
    assert s.fill_color == pytest.approx((1.0, 0.94, 0.60))   # untouched


def test_selecting_a_mark_loads_its_style_into_the_picker(win):
    _add_and_select(win, Line((100.0, 200.0), (220.0, 240.0), color=(0.13, 0.60, 0.20), width=4.0))
    loaded = win._markup_style_button.style()
    assert loaded.color == pytest.approx((0.13, 0.60, 0.20))
    assert loaded.width == 4.0


def test_restyle_keeps_the_object_selected_for_the_next_tweak(win):
    _add_and_select(win, Line((100.0, 200.0), (220.0, 240.0), color=(0.86, 0.10, 0.10)))
    win._markup_style_button._set_color((0.13, 0.60, 0.20))
    sel = win.view.annotations.selected_object
    assert sel is not None and isinstance(sel[1], Line)
    assert sel[1].color == pytest.approx((0.13, 0.60, 0.20))   # selection followed the restyle


def test_textbox_selection_is_left_to_its_format_bar(win):
    """A text box keeps its richer format bar — the markup picker doesn't restyle it, and doesn't
    even load into the button (so drawing style isn't hijacked by selecting a box)."""
    from model.page_edits import TextBox

    _add_and_select(win, TextBox((100.0, 100.0, 200.0, 140.0), "hi", color=(0.0, 0.0, 0.0)))
    at = win.undo_stack.index()
    win._markup_style_button._set_color((0.0, 0.0, 1.0))
    box = [a for a in win.vdoc.page_annotations(0) if isinstance(a, TextBox)][0]
    assert box.color == pytest.approx((0.0, 0.0, 0.0))   # untouched
    assert win.undo_stack.index() == at                  # no restyle command pushed


def test_no_selection_only_updates_the_sticky_default(win):
    win.view.annotations.clear_object_selection()
    at = win.undo_stack.index()
    win._markup_style_button._set_color((0.0, 0.0, 1.0))
    assert win.view.annotations.current_markup_style.color == pytest.approx((0.0, 0.0, 1.0))
    assert win.undo_stack.index() == at                  # nothing to restyle → no command


def test_coloured_shape_round_trips(a_pdf, tmp_path):
    """A blue, thick, blue-filled rectangle survives save→reopen with its style intact — proving
    the picker changes nothing about the (already-styled) descriptor's persistence."""
    v = VirtualDocument.from_path(a_pdf)
    v.add_annotation(0, Shape("rect", (100.0, 100.0, 200.0, 160.0),
                              color=(0.13, 0.35, 0.85), width=4.0, fill_color=(0.78, 0.86, 0.97)))
    out = str(tmp_path / "styled.pdf")
    PyMuPDFEngine().materialize(v, out)
    reopened = VirtualDocument.from_path(out)
    shape = [a for a in reopened.page_annotations(0) if isinstance(a, Shape)][0]
    assert shape.color == pytest.approx((0.13, 0.35, 0.85), abs=0.02)
    assert shape.width == pytest.approx(4.0, abs=0.1)
    assert shape.fill_color == pytest.approx((0.78, 0.86, 0.97), abs=0.02)
