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
