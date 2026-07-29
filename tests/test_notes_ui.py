"""Notes: creating and editing one (PLAN.md §GUI feature roadmap → M90.1). Offscreen GUI + model.

M81 gave HUS marks a ``note`` field with no way to show it; this is the half that writes one. The
owner's rules the tests below pin:

* **rule 4 — attaching is the primary act.** A note swept over already-marked text lands on *that*
  mark, unchanged in geometry and colour. A Highlight is created only when the span carries no
  markup at all, and that creation plus the attach is **one undo step**;
* **rule 6 — a Highlight wins**, failing that the topmost underline / strikeout, so a passage
  carrying layered markup has one deterministic host;
* **rule 3 — the note takes its host's colour**, which is what the popup is washed in;
* **clearing the text removes the note and leaves the mark** — a note is a *field* of the mark, so
  emptying it is not deleting the mark.

Plus the two placement decisions: Note rides the **Markup ▾ dropdown** (no new toolbar slot,
§Design budgets) and is **one-shot**, not sticky like the HUS quartet — writing a note is a
deliberate single act.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from app import PdfApp
from model.page_edits import (
    Highlight,
    Strikeout,
    Underline,
    resolve_note_host,
)
from store.settings import Settings
from viewer.markup_style import HIGHLIGHT_COLORS, TEXT_LINE_COLORS
from viewer.note_editor import wash
from viewer.tools import ArmedTool

YELLOW = HIGHLIGHT_COLORS[0][1]
GREEN = HIGHLIGHT_COLORS[1][1]
LINE_RED = TEXT_LINE_COLORS[0][1]

# One text line's band; x runs 70 → 220 (the M59.10 / M81 merge fixtures' geometry).
LINE1 = (70.0, 66.0, 220.0, 80.0)


def _bar(x0, x1, line=LINE1):
    return (x0, line[1], x1, line[3])


# ---- rule 6, headless: which mark receives the note --------------------------------


def test_a_span_with_no_markup_has_no_host():
    """``None`` is the signal to *create* one — the caller's fallback, not an error."""
    assert resolve_note_host((), (LINE1,)) is None
    assert resolve_note_host((Highlight(((300.0, 400.0, 380.0, 414.0),)),), (LINE1,)) is None


def test_the_only_mark_is_the_host():
    mark = Underline((LINE1,), color=LINE_RED)
    assert resolve_note_host((mark,), (LINE1,)) is mark


def test_a_highlight_wins_over_an_underline_on_the_same_words():
    """Rule 6. The app deliberately allows layered HUS (M59.10 scopes merging per *type*), so this
    is a real collision, not a hypothetical — and the highlight is the colour a reader already
    associates with the passage, which is what rule 3 then paints the note in."""
    highlight = Highlight((LINE1,), color=YELLOW)
    underline = Underline((LINE1,), color=LINE_RED)
    assert resolve_note_host((highlight, underline), (LINE1,)) is highlight
    assert resolve_note_host((underline, highlight), (LINE1,)) is highlight  # order-independent


def test_without_a_highlight_the_topmost_line_mark_wins():
    """The page's annotation tuple *is* its z-order (later = on top), so the host is the mark the
    user sees on top — and it is deterministic either way round."""
    under = Underline((LINE1,), color=LINE_RED)
    strike = Strikeout((LINE1,), color=LINE_RED)
    assert resolve_note_host((under, strike), (LINE1,)) is strike
    assert resolve_note_host((strike, under), (LINE1,)) is under


def test_a_partly_overlapping_mark_still_hosts():
    """Same overlap test the merge machinery uses — a note swept over part of a highlight belongs
    to that highlight, not to a new one."""
    mark = Highlight((_bar(70, 150),), color=YELLOW)
    assert resolve_note_host((mark,), (_bar(120, 220),)) is mark


# ---- the popup's colour (rule 3) ---------------------------------------------------


def test_the_popup_is_washed_towards_white_but_keeps_its_hue():
    """A note wears its host's colour, but a highlighter wash is meant to sit *under* text — typed
    into directly it fights what you write. The mix keeps the tie visible and the text legible."""
    for color in (YELLOW, GREEN, LINE_RED):
        washed = wash(color)
        assert washed.lightnessF() > 0.7                      # light enough to type black on
        assert washed.saturationF() > 0.05                    # …and still recognisably the colour
    assert wash(YELLOW).hue() == pytest.approx(wash((1.0, 0.86, 0.10)).hue())


# ---- offscreen GUI ------------------------------------------------------------------


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
    w.undo_stack.setClean()
    w.close()


def _word_box(win, page_index=0, n=0) -> tuple:
    ref = win.vdoc.ordered[page_index]
    page = win.vdoc.sources[ref.source_id][ref.source_page_index]
    return tuple(page.get_text("words")[n][:4])


def _drag_over_word(win, page_index: int = 0, n: int = 0) -> None:
    """Press-drag across a word through the real mouse routing, so an armed tool fires as it does
    for a user rather than through a direct handler call."""
    rect = win.view.scene_rect_for_box(page_index, _word_box(win, page_index, n))
    p0 = QPointF(win.view.mapFromScene(QPointF(rect.left() + 1, rect.center().y())))
    p1 = QPointF(win.view.mapFromScene(QPointF(rect.right() - 1, rect.center().y())))
    view = win.view
    view.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, p0, p0,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    view.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, p1, p1,
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    view.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, p1, p1,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))


def _note_over_word(win, text: str, page_index: int = 0, n: int = 0) -> None:
    """The whole user gesture: arm Note, sweep the word, type, commit."""
    win.view.arm(ArmedTool.NOTE)
    _drag_over_word(win, page_index, n)
    notes = win.view.annotations.notes
    assert notes.is_open, "sweeping with Note armed must open the popup"
    notes._popup.setPlainText(text)
    notes._commit()


def _marks(win, cls, page_index: int = 0) -> list:
    return [a for a in win.vdoc.page_annotations(page_index) if isinstance(a, cls)]


def _menu_over(win, box: tuple):
    win.view.annotations.repaint()
    return win._view_context_menu(win.view.scene_rect_for_box(0, box).center())


def _labels(menu) -> list[str]:
    return [a.text() for a in menu.actions() if a.text()]


# ---- rule 4: attaching is primary, creating is the fallback -------------------------


def test_a_note_over_plain_text_makes_a_highlight_to_hold_it(win):
    """The fallback half of rule 4 — and it is *one* undo step, so the highlight the user never
    asked for on its own goes away with the note it was made for."""
    before = win.undo_stack.index()
    _note_over_word(win, "check this figure")
    (mark,) = _marks(win, Highlight)
    assert mark.note == "check this figure"
    assert mark.color == win._highlight_color        # the current highlight colour, not a constant
    assert win.undo_stack.index() == before + 1      # one step for create + attach
    win.undo_stack.undo()
    assert _marks(win, Highlight) == []              # …and one undo takes both back


def test_a_note_over_an_existing_highlight_attaches_to_that_mark(win):
    """The primary half of rule 4: no second mark, and the host's geometry is untouched — the
    failure this pins is a note that silently lays a *new* highlight over the old one."""
    win.view.arm(ArmedTool.HIGHLIGHT)
    _drag_over_word(win)
    (before,) = _marks(win, Highlight)

    _note_over_word(win, "a remark")
    (after,) = _marks(win, Highlight)                # still exactly one mark
    assert after.note == "a remark"
    assert after.rects == before.rects
    assert after.color == before.color


def test_a_note_over_an_underline_attaches_without_creating_a_highlight(win):
    """Rule 4 says *any* HUS mark hosts a note — creating a highlight over an underlined passage
    would both change the look and leave two marks where the user made one."""
    win.view.arm(ArmedTool.UNDERLINE)
    _drag_over_word(win)

    _note_over_word(win, "on the underline")
    assert _marks(win, Highlight) == []
    (mark,) = _marks(win, Underline)
    assert mark.note == "on the underline"


def test_a_layered_passage_notes_the_highlight(win):
    """Rule 6 end to end, through the real gesture: highlight *and* underline the same words, then
    note them — the highlight receives it and the underline is left alone."""
    win.view.arm(ArmedTool.HIGHLIGHT)
    _drag_over_word(win)
    win.view.arm(ArmedTool.UNDERLINE)
    _drag_over_word(win)

    _note_over_word(win, "layered")
    (highlight,) = _marks(win, Highlight)
    (underline,) = _marks(win, Underline)
    assert highlight.note == "layered"
    assert underline.note == ""


def test_the_popup_opens_prefilled_in_the_host_colour(win):
    """Rule 3, and the re-open path: the popup is the host's note, washed in the host's colour."""
    win.view.arm(ArmedTool.HIGHLIGHT)
    _drag_over_word(win)
    _note_over_word(win, "first draft")
    (mark,) = _marks(win, Highlight)

    win._note_mark(0, mark)
    notes = win.view.annotations.notes
    assert notes.text == "first draft"
    assert wash(mark.color).name() in notes._popup.styleSheet()
    notes.close()


# ---- clearing, abandoning, and the no-ops -------------------------------------------


def test_clearing_the_text_removes_the_note_and_leaves_the_mark(win):
    """The one behaviour a note shares with nothing else in the app: emptying the editor is not a
    delete. A note is a *field* of the mark — the mark is what the reader marked."""
    _note_over_word(win, "temporary")
    (mark,) = _marks(win, Highlight)

    win._note_mark(0, mark)
    notes = win.view.annotations.notes
    notes._popup.setPlainText("")
    notes._commit()

    (still_there,) = _marks(win, Highlight)          # the highlight survives…
    assert still_there.note == ""                    # …carrying no note
    assert still_there.rects == mark.rects


def test_abandoning_the_popup_over_plain_text_leaves_no_highlight(win):
    """Esc after an accidental sweep must leave the document exactly as it was — which is why the
    editor opens *before* the fallback highlight is created rather than after."""
    before = win.undo_stack.index()
    win.view.arm(ArmedTool.NOTE)
    _drag_over_word(win)
    assert win.view.annotations.notes.is_open
    win.view.annotations.notes.close()               # Esc

    assert _marks(win, Highlight) == []
    assert win.undo_stack.index() == before


def test_committing_unchanged_text_pushes_nothing(win):
    """Re-opening a note and closing it without typing must not stack a pointless undo entry —
    the same idempotence guard M59.10 keeps for a re-marked span."""
    _note_over_word(win, "unchanged")
    (mark,) = _marks(win, Highlight)
    before = win.undo_stack.index()

    win._note_mark(0, mark)
    win.view.annotations.notes._commit()
    assert win.undo_stack.index() == before


def test_an_empty_note_over_plain_text_creates_nothing(win):
    """Sweeping, typing nothing, and clicking away is not a request for a highlight."""
    before = win.undo_stack.index()
    _note_over_word(win, "   ")
    assert _marks(win, Highlight) == []
    assert win.undo_stack.index() == before


def test_undoing_the_host_while_the_popup_is_open_commits_nothing(win):
    """The popup outlives one event; the mark it points at may not. With the host gone from the
    page's tuple the write matches nothing — which must be *no* undo entry, not an empty one."""
    win.view.arm(ArmedTool.HIGHLIGHT)
    _drag_over_word(win)
    (mark,) = _marks(win, Highlight)

    win._note_mark(0, mark)
    win.undo_stack.undo()                            # the highlight goes away under the popup
    before = win.undo_stack.index()
    notes = win.view.annotations.notes
    notes._popup.setPlainText("orphaned")
    notes._commit()

    assert _marks(win, Highlight) == []
    assert win.undo_stack.index() == before


# ---- the context menu (M76) ---------------------------------------------------------


def test_an_unnoted_mark_offers_add_note_and_not_remove(win):
    """Inapplicable chrome is invisible, not greyed out (owner rule): there is nothing to remove."""
    win.view.arm(ArmedTool.HIGHLIGHT)
    _drag_over_word(win)
    labels = _labels(_menu_over(win, _word_box(win)))
    assert "Add Note…" in labels
    assert "Remove Note" not in labels


def test_a_noted_mark_offers_edit_and_remove(win):
    """The label is the one thing a menu can say that an on-page glyph cannot — whether this mark
    already carries a note."""
    _note_over_word(win, "already said")
    labels = _labels(_menu_over(win, _word_box(win)))
    assert "Edit Note…" in labels
    assert "Remove Note" in labels
    assert "Add Note…" not in labels


def test_remove_note_from_the_menu_keeps_the_mark(win):
    """Remove Note and an emptied editor are the same write, so they cannot drift apart."""
    _note_over_word(win, "to be removed")
    (mark,) = _marks(win, Highlight)
    win._remove_note(0, mark)

    (still_there,) = _marks(win, Highlight)
    assert still_there.note == ""


def test_the_menu_still_leads_with_the_m76_swatch_rows(win):
    """The note verbs are *added below* the layer rows, not in place of them — M76's change set for
    marked text is unchanged."""
    from viewer.markup_style import SwatchRowAction

    _note_over_word(win, "noted")
    menu = _menu_over(win, _word_box(win))
    rows = [a for a in menu.actions() if isinstance(a, SwatchRowAction)]
    assert [r.title for r in rows] == ["Highlight", "Underline", "Strike Out"]


# ---- placement: the dropdown, and one-shot arming -----------------------------------


def test_note_rides_the_markup_dropdown_and_takes_no_toolbar_slot(win):
    """§Design budgets holds the bar at ~10 slots, and a note is a text-markup verb — so it joins
    the Markup ▾ group rather than becoming an eleventh button."""
    actions = win._markup_button.menu().actions()
    assert "Note" in [a.text() for a in actions]
    assert win._armed_actions[ArmedTool.NOTE] in actions


def test_note_carries_no_swatch_row(win):
    """A note takes its host's colour (rule 3), so there is no fourth colour to pick."""
    from viewer.markup_style import SwatchRowAction

    menu = win._markup_button.menu()
    rows = [a for a in menu.actions() if isinstance(a, SwatchRowAction)]
    assert [r.title for r in rows] == ["Highlight", "Underline", "Strike Out"]


def test_note_is_one_shot_not_sticky(win):
    """Unlike the M73 quartet it sits beside: writing a note is a deliberate single act, so the
    tool disarms once the sweep has handed off to the popup."""
    assert not ArmedTool.NOTE.sticky
    win.view.arm(ArmedTool.NOTE)
    _drag_over_word(win)
    assert win.view.armed is None
    win.view.annotations.notes.close()


# ---- M90.2: the on-page glyph -------------------------------------------------------
#
# Without one a note is invisible until the exact mark is right-clicked, which is no affordance
# at all. The four things the milestone asks of it: present only on noted marks, legible at low
# zoom, obscuring no text, and opening the note.


def _glyphs(win) -> list:
    return win.view.annotations._note_glyphs


def _page_rect(win, page_index: int = 0):
    pw, ph = win.view._unrotated_size(page_index)
    return win.view.scene_rect_for_box(page_index, (0.0, 0.0, pw, ph))


def test_a_glyph_appears_only_on_noted_marks(win):
    """One badge per noted mark and none anywhere else — a highlight without a note is unchanged
    on the page, which is what keeps the badge meaningful."""
    win.view.arm(ArmedTool.HIGHLIGHT)       # sticky (M73), so both pages mark on one arm
    _drag_over_word(win, page_index=0)
    _drag_over_word(win, page_index=1)
    win.view.annotations.repaint()
    assert _glyphs(win) == []                        # two highlights, neither noted

    _note_over_word(win, "noted one", page_index=0)
    win.view.annotations.repaint()
    assert len(_glyphs(win)) == 1
    page_index, mark, _box = _glyphs(win)[0]
    assert (page_index, mark.note) == (0, "noted one")


def test_removing_the_note_removes_the_glyph(win):
    """The badge is painted from the model, so clearing the note clears it — no stale chrome
    pointing at a note that no longer exists."""
    _note_over_word(win, "temporary")
    win.view.annotations.repaint()
    assert len(_glyphs(win)) == 1

    (mark,) = _marks(win, Highlight)
    win._remove_note(0, mark)
    win.view.annotations.repaint()
    assert _glyphs(win) == []


def test_the_glyph_obscures_no_text(win):
    """It sits in the page's **right margin**, on the line the mark ends on — not at the end of the
    marked run, where it would cover whatever text follows the highlight, and not straddling the
    mark's own corner, where it would cover the very passage it annotates."""
    _note_over_word(win, "in the margin")
    win.view.annotations.repaint()
    (_page, mark, box) = _glyphs(win)[0]

    for bar in mark.rects:
        assert not box.intersects(win.view.scene_rect_for_box(0, bar))
    page = _page_rect(win)
    assert page.contains(box)                        # …and never floating in the gutter
    assert box.right() > page.center().x()           # right margin, not left


def test_the_glyph_sits_on_its_marks_line(win):
    """A margin badge that drifted off the mark's line would point at the wrong passage on a dense
    page. It is centred on the last bar's vertical middle."""
    _note_over_word(win, "same line", n=0)
    win.view.annotations.repaint()
    (_page, mark, box) = _glyphs(win)[0]
    bar = win.view.scene_rect_for_box(0, mark.rects[-1])
    assert box.center().y() == pytest.approx(bar.center().y(), abs=1.0)


def test_the_glyph_is_the_same_size_at_every_zoom(win):
    """"Legible at low zoom" is a *screen*-size requirement, and it falls out of sizing the badge
    in scene units: zoom rebuilds the scene rather than scaling the view, so a scene unit is a
    logical pixel at any zoom. Page-point sizing would have shrunk it away at Fit Page."""
    _note_over_word(win, "readable")
    sizes = []
    for zoom in (1.0, 0.4, 2.5):
        win.view.set_zoom(zoom)
        win.view.annotations.repaint()
        sizes.append(_glyphs(win)[0][2].width())
    assert sizes[0] == sizes[1] == sizes[2]


def test_the_glyph_wears_its_hosts_colour(win):
    """Rule 3 again, and the tie that says *which* mark this badge belongs to on a page carrying
    several. Washed like the popup, so badge and editor read as one thing."""
    from PySide6.QtGui import QColor

    win.view.arm(ArmedTool.HIGHLIGHT)
    win._set_highlight_color(GREEN)
    _drag_over_word(win)
    _note_over_word(win, "green host")
    win.view.annotations.repaint()

    item = next(i for i in win.view.annotations._items
                if i.brush().color() == wash(GREEN, 0.62))
    assert item.brush().color() != QColor.fromRgbF(*GREEN)   # washed, not the raw mark colour


def test_hovering_the_glyph_reads_the_note(win):
    """The cheap half of "click/hover opens it": the badge carries the note as its tooltip, so a
    passing reader gets the remark without opening anything."""
    _note_over_word(win, "hover reads this")
    win.view.annotations.repaint()
    (_page, _mark, box) = _glyphs(win)[0]
    item = next(i for i in win.view.annotations._items if i.toolTip() == "hover reads this")
    centre = item.sceneBoundingRect().center()       # the tooltip belongs to the badge you see
    assert centre.x() == pytest.approx(box.center().x(), abs=2.0)
    assert centre.y() == pytest.approx(box.center().y(), abs=2.0)


def test_clicking_the_glyph_opens_its_note(win):
    """The affordance's whole point. Routed through the real press handler, so it also pins that
    the badge beats text selection to the click."""
    _note_over_word(win, "click to edit")
    win.view.annotations.repaint()
    (_page, _mark, box) = _glyphs(win)[0]

    point = QPointF(win.view.mapFromScene(box.center()))
    win.view.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, point, point,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

    notes = win.view.annotations.notes
    assert notes.is_open
    assert notes.text == "click to edit"
    assert not win.view.selection.selected_words()   # the click did not start a text selection
    notes.close()


def test_the_glyph_paints_above_every_mark(win):
    """A note badge is chrome *about* a mark, so it must never be buried by one — including a
    filled shape brought to the front over the same words."""
    from viewer.annotations import _NOTE_GLYPH_Z

    _note_over_word(win, "on top")
    win.view.annotations.repaint()
    item = next(i for i in win.view.annotations._items if i.toolTip() == "on top")
    others = [i.zValue() for i in win.view.annotations._items if i is not item]
    assert item.zValue() == _NOTE_GLYPH_Z
    assert all(z < _NOTE_GLYPH_Z for z in others)


# ---- layered marks each keep a visible badge (owner-reported 2026-07-29) --------------
#
# The report: underline a passage and note it, then highlight the *same* passage and note that —
# the first note becomes invisible, and reappears only when the highlight is removed. The model
# was right (owner rule 5: each mark keeps its **own** note); the badges were not. Both anchor to
# the same line, so both landed on exactly the same pixel and the second painted hid the first.


def _layered(win, page_index: int = 0):
    """Underline a passage and note it, then highlight the same passage and note that."""
    win.view.arm(ArmedTool.UNDERLINE)
    _drag_over_word(win, page_index)
    win.view.arm(ArmedTool.HIGHLIGHT)
    _drag_over_word(win, page_index)
    (underline,) = _marks(win, Underline, page_index)
    (highlight,) = _marks(win, Highlight, page_index)
    win._commit_note(page_index, underline.rects, underline, "on the underline")
    (highlight,) = _marks(win, Highlight, page_index)
    win._commit_note(page_index, highlight.rects, highlight, "on the highlight")
    # Highlight is sticky (M73), and an armed drag-over-text tool deliberately wins the press
    # ahead of the note glyph — so leaving it armed is an artefact of this helper, not the
    # scenario. A user reaches the badges the same way: Esc, or clicking the lit button.
    win.view.disarm()
    win.view.annotations.repaint()


def test_two_notes_on_one_passage_get_two_visible_badges(win):
    """The reported fault: both badges anchor to the same line, so without a layout pass the
    second one painted covered the first completely — a note the user had written was invisible
    and unreachable on the page."""
    _layered(win)
    boxes = [box for _p, _m, box in _glyphs(win)]
    assert len(boxes) == 2
    assert not boxes[0].intersects(boxes[1])         # …no longer stacked on the same pixel


def test_the_fanned_badges_stay_on_their_own_line(win):
    """They fan **sideways**, not downwards: the badge's vertical position is what says which line
    the remark is about, so pushing one down would claim the wrong passage."""
    _layered(win)
    first, second = (box for _p, _m, box in _glyphs(win))
    assert first.center().y() == pytest.approx(second.center().y(), abs=0.5)
    assert second.right() <= first.left()            # the later one slides left along the margin


def test_each_badge_wears_its_own_hosts_colour(win):
    """Which is what tells the two apart once they sit side by side (owner rule 3)."""
    _layered(win)
    fills = {}
    for _p, mark, box in _glyphs(win):
        item = next(i for i in win.view.annotations._items if i.toolTip() == mark.note)
        fills[type(mark).__name__] = item.brush().color()
    assert fills["Underline"] == wash(win._underline_color, 0.62)
    assert fills["Highlight"] == wash(win._highlight_color, 0.62)
    assert fills["Underline"] != fills["Highlight"]


def test_each_badge_opens_its_own_note(win):
    """The other half of being hidden: the covered badge could not be clicked either, because the
    hit-test walks last-painted-first and both boxes contained the same points."""
    _layered(win)
    opened = []
    for _p, mark, box in _glyphs(win):
        point = QPointF(win.view.mapFromScene(box.center()))
        win.view.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, point, point,
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        notes = win.view.annotations.notes
        assert notes.is_open
        opened.append(notes.text)
        notes.close()
    assert sorted(opened) == ["on the highlight", "on the underline"]


def test_removing_one_layer_leaves_the_others_note_alone(win):
    """The half of the report that was already correct, pinned so it stays that way: a note dies
    with **its own** host (owner rule 2), so removing the highlight takes the highlight's note and
    leaves the underline's — which then takes the margin slot back."""
    _layered(win)
    (highlight,) = _marks(win, Highlight)
    win.view.annotations.remove(0, highlight)
    win.view.annotations.repaint()

    assert _marks(win, Highlight) == []
    (underline,) = _marks(win, Underline)
    assert underline.note == "on the underline"
    (_p, mark, box) = _glyphs(win)[0]
    assert mark is underline
    page = _page_rect(win)
    assert box.right() > page.center().x()           # back in the margin, nothing to fan around


def test_a_lone_badge_on_another_line_does_not_fan(win):
    """The cry-wolf guard: the layout pass only shifts a badge that would actually overlap one
    already placed, so an unrelated mark keeps the plain margin position."""
    _layered(win)
    win.view.arm(ArmedTool.HIGHLIGHT)
    _drag_over_word(win, page_index=1)
    (other,) = _marks(win, Highlight, 1)
    win._commit_note(1, other.rects, other, "another page")
    win.view.annotations.repaint()

    (_p, _m, box) = next(g for g in _glyphs(win) if g[0] == 1)
    page = _page_rect(win, 1)
    assert box.right() == pytest.approx(page.right() - 3.0, abs=0.5)   # the plain margin slot


# ---- M90.3: the Annotations sidebar ---------------------------------------------------
#
# M77's panel is already "a reading of the document's margin". For a noted mark the note *is* the
# margin remark, so it belongs on the row — and the row is a second place to write one.


@pytest.fixture
def sidebar_win(qapp, a_pdf, tmp_path):
    """A window with the Annotations tab mounted the way a reader mounts it (M79.3)."""
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.settings.set_pref("sidebar_tabs", ["annotations", "outline"])
    w = qapp.open_document(a_pdf)
    w.show()
    qapp.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def _rows(win) -> list[str]:
    panel = win.annotations_panel
    return [panel.item(i).text() for i in range(panel.count())]


def _mount_annotations(win) -> None:
    win._sidebar_tab_actions["annotations"].setChecked(True)


def test_a_noted_mark_shows_its_note_on_its_row(sidebar_win):
    """The passage stays — it is what lets you recognise *which* mark the row is — and the note
    follows it. Appended, not substituted."""
    win = sidebar_win
    _note_over_word(win, "check this figure")
    _mount_annotations(win)

    (row,) = _rows(win)
    assert row.startswith("p. 1 · highlight · ")
    assert row.endswith(" — check this figure")
    assert "ALPHA-zero-A0" in row                     # …the passage, still there


def test_an_unnoted_mark_reads_exactly_as_it_did_before(sidebar_win):
    """M77's row format is unchanged where there is no note — no dangling separator."""
    win = sidebar_win
    win.view.arm(ArmedTool.HIGHLIGHT)
    _drag_over_word(win)
    _mount_annotations(win)

    (row,) = _rows(win)
    assert row == "p. 1 · highlight · ALPHA-zero-A0"


def test_a_long_note_is_clipped_but_the_row_keeps_its_tooltip(sidebar_win):
    """A remark you can only read half of is worse than one you can hover, so the row clips and
    the tooltip carries it whole."""
    win = sidebar_win
    long_note = "a considered remark that runs well past what one sidebar row can show"
    _note_over_word(win, long_note)
    _mount_annotations(win)

    panel = win.annotations_panel
    assert panel.item(0).text().endswith("…")
    assert long_note not in panel.item(0).text()
    assert panel.item(0).toolTip() == long_note      # …in full, on hover


def test_the_row_follows_an_edit_and_dies_with_its_host(sidebar_win):
    """`populate()` re-runs on every edit and reads the live model, so the list tracks add /
    edit / remove / undo without the note needing any bookkeeping of its own."""
    win = sidebar_win
    _note_over_word(win, "first")
    _mount_annotations(win)
    assert _rows(win)[0].endswith(" — first")

    (mark,) = _marks(win, Highlight)
    win._commit_note(0, tuple(mark.rects), mark, "second")
    assert _rows(win)[0].endswith(" — second")

    (mark,) = _marks(win, Highlight)
    win.view.annotations.remove(0, mark)             # deleting the host removes the row
    assert win.annotations_panel is None or win.annotations_panel.count() == 0


def test_double_clicking_a_row_opens_that_marks_note(sidebar_win):
    """Editable *there* — by revealing the mark and opening the one on-page popup, so "the sidebar
    and the page agree" is true by construction rather than by two editors kept in step."""
    win = sidebar_win
    _note_over_word(win, "from the page")
    _mount_annotations(win)
    panel = win.annotations_panel

    panel.itemDoubleClicked.emit(panel.item(0))
    notes = win.view.annotations.notes
    assert notes.is_open
    assert notes.text == "from the page"

    notes._popup.setPlainText("from the sidebar")
    notes._commit()
    (mark,) = _marks(win, Highlight)
    assert mark.note == "from the sidebar"           # …and the write lands on the same field
    assert _rows(win)[0].endswith(" — from the sidebar")


def test_double_clicking_an_unnoted_row_writes_a_first_note(sidebar_win):
    """The sidebar is a creation path too — a row with no note is exactly where you notice you
    want one."""
    win = sidebar_win
    win.view.arm(ArmedTool.HIGHLIGHT)
    _drag_over_word(win)
    _mount_annotations(win)
    panel = win.annotations_panel

    panel.itemDoubleClicked.emit(panel.item(0))
    notes = win.view.annotations.notes
    assert notes.is_open and notes.text == ""
    notes._popup.setPlainText("written from the list")
    notes._commit()

    (mark,) = _marks(win, Highlight)
    assert mark.note == "written from the list"
    assert len(_marks(win, Highlight)) == 1          # no second mark was created


# ---- M90.4: another tool's comments ---------------------------------------------------
#
# A foreign markup's /Contents *is* a note on that passage — what Acrobat, Preview and Edge
# write. It shows read-only, because M68's rule is that a foreign mark is not editable until it
# is adopted; adopting it (M81.3) carries the comment across into a note we own.


@pytest.fixture
def reviewed_pdf(tmp_path) -> str:
    """What a reviewer's tool leaves behind: a commented highlight, an uncommented underline, and
    a commented sticky note (which draws its own icon into the page)."""
    import pymupdf as fitz

    path = str(tmp_path / "reviewed.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "REVIEWED passage here", fontsize=13)
    words = page.get_text("words")
    commented = page.add_highlight_annot(fitz.Rect(words[0][:4]))
    commented.set_info(title="Alice", content="please rewrite this")
    commented.update()
    silent = page.add_underline_annot(fitz.Rect(words[1][:4]))
    silent.update()
    sticky = page.add_text_annot(fitz.Point(300, 300), "a loose remark")
    sticky.update()
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def reviewed_win(qapp, reviewed_pdf, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.settings.set_pref("sidebar_tabs", ["annotations", "outline"])
    w = qapp.open_document(reviewed_pdf)
    w.show()
    qapp.processEvents()
    w.view.annotations.repaint()
    yield w
    w.undo_stack.setClean()
    w.close()


def test_a_foreign_comment_gets_a_grey_badge(reviewed_win):
    """Grey, not the host's colour — a `ForeignAnnot` carries no colour to take, and grey is the
    signal that this comment is read-only until the mark is adopted."""
    from viewer.annotations import FOREIGN_NOTE_GREY

    win = reviewed_win
    (_page, mark, _box) = _glyphs(win)[0]
    assert mark.contents == "please rewrite this"
    item = next(i for i in win.view.annotations._items
                if i.toolTip() == "please rewrite this")
    assert item.brush().color() == wash(FOREIGN_NOTE_GREY, 0.62)


def test_only_the_commented_text_markup_is_badged(reviewed_win):
    """Three foreign annotations, one badge. An **uncommented** markup has no note to show, and a
    **sticky note** already draws its own icon into the page pixmap — badging it would be our
    chrome duplicating the file's."""
    win = reviewed_win
    assert len(_glyphs(win)) == 1
    (_page, mark, _box) = _glyphs(win)[0]
    assert mark.kind_name == "Highlight"


def test_clicking_a_foreign_badge_shows_the_comment_read_only(reviewed_win):
    """The same popup as our own notes, deliberately: a reader should not have to learn a second
    place remarks appear based on who wrote one. What differs is that it cannot be typed in."""
    win = reviewed_win
    (_page, _mark, box) = _glyphs(win)[0]
    point = QPointF(win.view.mapFromScene(box.center()))
    win.view.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, point, point,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

    notes = win.view.annotations.notes
    assert notes.is_open
    assert notes.text == "please rewrite this"
    assert notes.is_read_only
    assert notes._on_commit is None                  # nothing can be saved even by accident
    notes.close()


def test_a_foreign_row_reads_like_one_of_ours(reviewed_win):
    """Before M90.4 a foreign row read ``type · comment``, putting the comment in the slot our own
    rows use for the *passage* — the same position on the same list meaning two different things
    depending on who wrote the mark, with the covered words never shown at all."""
    win = reviewed_win
    win._sidebar_tab_actions["annotations"].setChecked(True)
    rows = _rows(win)
    highlight = next(r for r in rows if "please rewrite this" in r)
    assert highlight == "p. 1 · highlight · REVIEWED — please rewrite this"


def test_adopting_a_commented_mark_makes_its_note_ours(reviewed_win):
    """M68 adoption, end to end at the UI: the grey read-only badge becomes a coloured editable
    one carrying the same words. M81.3 made the model carry the comment across; this pins that the
    interface follows it."""
    from viewer.annotations import FOREIGN_NOTE_GREY

    win = reviewed_win
    (page_index, foreign, _box) = _glyphs(win)[0]
    win._adopt_foreign_annotation(page_index, foreign)
    win.view.annotations.repaint()

    (mark,) = _marks(win, Highlight)
    assert mark.note == "please rewrite this"        # …now a note we own
    (_page, badged, _box) = _glyphs(win)[0]
    assert badged is mark
    item = next(i for i in win.view.annotations._items
                if i.toolTip() == "please rewrite this")
    assert item.brush().color() != wash(FOREIGN_NOTE_GREY, 0.62)   # no longer read-only grey


def test_an_adopted_note_is_editable(reviewed_win):
    """The point of adopting: what was another tool's fixed comment is now a field we can write."""
    win = reviewed_win
    (page_index, foreign, _box) = _glyphs(win)[0]
    win._adopt_foreign_annotation(page_index, foreign)
    win.view.annotations.repaint()

    (_page, mark, _box) = _glyphs(win)[0]
    win._note_mark(0, mark)
    notes = win.view.annotations.notes
    assert not notes.is_read_only
    notes._popup.setPlainText("rewritten by me")
    notes._commit()
    assert _marks(win, Highlight)[0].note == "rewritten by me"


def test_foreign_badges_are_not_read_for_the_whole_document(reviewed_win):
    """Finding them means reading each page's annotation dictionaries, so the pass is band-gated
    like the content marks — the O(document)-per-edit trap M87.3 and M78.8 were spent closing, and
    a reviewed 200-page PDF is exactly the file it would have been slowest on."""
    win = reviewed_win
    seen = []
    original = win.view.annotations.foreign_annotations
    win.view.annotations.foreign_annotations = lambda i: (seen.append(i), original(i))[1]
    try:
        win.view.annotations.repaint()
    finally:
        del win.view.annotations.foreign_annotations
    band = win.view.content_band()
    assert band is None or set(seen) <= set(range(band[0], band[1] + 1))
