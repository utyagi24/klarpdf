"""Notes on text markup — the model, the round-trip, and the data loss it cures (M81).

A note is a **field of its host mark**, not an object: ``note`` on Highlight / Underline /
Strikeout, stored as that annotation's PDF ``/Contents`` — which is exactly what Acrobat, Preview
and Edge already read and write. Three claims under test:

* **M81.1 the round-trip** — a noted mark saves, reopens and still carries its note, unchanged
  across a second save; an empty note writes no ``/Contents`` at all; and note text reaches
  neither ``search_for`` nor ``get_text``, so Find stays body-text-only (PR #190's filter needs no
  change — annotation text is not body text);
* **M81.2 the merge does not eat notes** — :func:`merge_markup` rebuilds an absorbed mark from
  bars and colour only, so before this a user who highlighted *adjacent* text silently destroyed a
  note they had typed, having deleted nothing;
* **M81.3 adoption carries the comment** — ``parse_annotation`` never read ``/Contents`` and
  ``degradations()`` never checked it, so adopting a commented foreign highlight (M68) dropped the
  comment with no warning, contradicting that function's own contract: *empty means adoption is
  lossless*. Reachable in two clicks on any Acrobat/Preview/Edge-reviewed PDF, and live in v0.16.2.

All headless. The *interface* — creating and editing a note — is M90; these marks carry a note
with no way yet to show it, which is deliberate: it stops the loss now.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from model.edit_engine import PyMuPDFEngine
from model.foreign_annots import adopt_annotation, degradations
from model.page_edits import (
    KLARPDF_AUTHOR,
    Highlight,
    Strikeout,
    Underline,
    merge_markup,
    read_klarpdf_annotations,
)
from model.virtual_document import VirtualDocument

YELLOW = (1.0, 0.86, 0.10)
GREEN = (0.10, 0.70, 0.30)

# One text line's band; x runs 70 → 220 (the M59.10 merge fixtures' geometry).
LINE1 = (70.0, 66.0, 220.0, 80.0)


def _bar(x0, x1, line=LINE1):
    return (x0, line[1], x1, line[3])


@pytest.fixture
def text_pdf(tmp_path) -> str:
    path = str(tmp_path / "t.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "The quick brown fox jumps over the lazy dog.", fontsize=14)
    doc.save(path)
    doc.close()
    return path


def _materialize(vdoc, tmp_path, name="out.pdf") -> str:
    out = str(tmp_path / name)
    PyMuPDFEngine().materialize(vdoc, out)
    return out


def _word_rects(vdoc, page_index=0, n=2):
    ref = vdoc.ordered[page_index]
    page = vdoc.sources[ref.source_id][ref.source_page_index]
    return tuple(tuple(w[:4]) for w in page.get_text("words")[:n])


# ---- M81.1 the model round-trips -------------------------------------------------


@pytest.mark.parametrize("mark_type", [Highlight, Underline, Strikeout])
def test_a_noted_mark_saves_and_reopens_with_its_note(text_pdf, tmp_path, mark_type):
    v = VirtualDocument.from_path(text_pdf)
    v.add_annotation(0, mark_type(_word_rects(v), note="check this figure"))
    out = _materialize(v, tmp_path)
    v.close()

    reopened = VirtualDocument.from_path(out)
    (mark,) = reopened.page_annotations(0)
    assert isinstance(mark, mark_type)
    assert mark.note == "check this figure"
    reopened.close()


def test_a_note_survives_a_second_save_unchanged(text_pdf, tmp_path):
    """save → reopen → save. The note must not be dropped, doubled or re-encoded on the way
    through: reopening seeds the model, and materialise re-bakes from that model."""
    v = VirtualDocument.from_path(text_pdf)
    v.add_annotation(0, Highlight(_word_rects(v), note="first pass"))
    once = _materialize(v, tmp_path, "once.pdf")
    v.close()

    v2 = VirtualDocument.from_path(once)
    twice = _materialize(v2, tmp_path, "twice.pdf")
    v2.close()

    v3 = VirtualDocument.from_path(twice)
    (mark,) = v3.page_annotations(0)
    assert mark.note == "first pass"
    v3.close()


def test_the_note_is_written_as_contents_beside_our_author_tag(text_pdf, tmp_path):
    """``/Contents`` is the standard place — what Acrobat, Preview and Edge read — so a note we
    write shows up as a comment in every other viewer, not only in ours. It must not displace the
    ``/T`` tag the round-trip identifies our own marks by."""
    v = VirtualDocument.from_path(text_pdf)
    v.add_annotation(0, Highlight(_word_rects(v), note="a comment"))
    out = _materialize(v, tmp_path)
    v.close()

    doc = fitz.open(out)
    (annot,) = list(doc[0].annots())
    assert annot.info.get("content") == "a comment"
    assert annot.info.get("title") == KLARPDF_AUTHOR
    doc.close()


def test_an_unnoted_mark_writes_no_contents_key(text_pdf, tmp_path):
    """The default is ``""``, and an empty note must leave the PDF exactly as it was before M81 —
    no empty ``/Contents`` on every highlight anyone ever draws."""
    v = VirtualDocument.from_path(text_pdf)
    v.add_annotation(0, Highlight(_word_rects(v)))
    out = _materialize(v, tmp_path)
    v.close()

    doc = fitz.open(out)
    (annot,) = list(doc[0].annots())
    assert doc.xref_get_key(annot.xref, "Contents")[0] == "null"
    doc.close()

    reopened = VirtualDocument.from_path(out)
    (mark,) = reopened.page_annotations(0)
    assert mark.note == ""                       # and it reads back as "", never None
    reopened.close()


def test_note_text_is_invisible_to_find(text_pdf, tmp_path):
    """Find searches *body text*; annotation text is not body text (the PR #190 decision). A note
    reaches neither ``search_for`` nor ``get_text``, so that filter needs no change for M81."""
    v = VirtualDocument.from_path(text_pdf)
    v.add_annotation(0, Highlight(_word_rects(v), note="rhinoceros"))
    out = _materialize(v, tmp_path)
    v.close()

    doc = fitz.open(out)
    page = doc[0]
    assert page.search_for("rhinoceros") == []
    assert "rhinoceros" not in page.get_text()
    assert page.search_for("quick")                       # the body text still searches
    doc.close()


def test_a_note_does_not_leak_between_marks(text_pdf, tmp_path):
    """Two marks on one page, one noted: the note belongs to its own host, not to the page."""
    v = VirtualDocument.from_path(text_pdf)
    rects = _word_rects(v, n=4)
    v.add_annotation(0, Highlight(rects[:2], note="only mine"))
    v.add_annotation(0, Underline(rects[2:]))
    out = _materialize(v, tmp_path)
    v.close()

    reopened = VirtualDocument.from_path(out)
    noted, plain = reopened.page_annotations(0)
    assert noted.note == "only mine"
    assert plain.note == ""
    reopened.close()


def test_removing_the_mark_removes_its_note(text_pdf, tmp_path):
    """Owner rule 2, satisfied by construction: the note *is* a field of the mark, so there is no
    second object to leave behind and no referential integrity to keep."""
    v = VirtualDocument.from_path(text_pdf)
    mark = Highlight(_word_rects(v), note="goes with it")
    v.add_annotation(0, mark)
    v.remove_annotation(0, mark)
    out = _materialize(v, tmp_path)
    v.close()

    doc = fitz.open(out)
    assert list(doc[0].annots()) == []
    doc.close()


# ---- M81.2 the merge inherits notes instead of destroying them --------------------


def test_extending_a_noted_mark_keeps_the_note():
    """The absorb path rebuilds the survivor from bars + colour. Before M81.2 a user who
    highlighted the *next* few words silently destroyed the note they had typed — having deleted
    nothing at all."""
    before = (Highlight((_bar(70, 150),), color=YELLOW, note="keep me"),)
    after = merge_markup(before, (_bar(120, 220),), Highlight, YELLOW)
    (mark,) = after
    assert mark.rects == ((70.0, LINE1[1], 220.0, LINE1[3]),)   # one mark, grown
    assert mark.note == "keep me"


def test_bridging_two_noted_marks_joins_both_notes():
    """Owner call: keep and join. Nothing typed is ever lost, and one undo restores the pair."""
    before = (
        Highlight((_bar(70, 110),), color=YELLOW, note="first"),
        Highlight((_bar(180, 220),), color=YELLOW, note="second"),
    )
    after = merge_markup(before, (_bar(100, 190),), Highlight, YELLOW)
    (mark,) = after
    assert mark.note == "first\n\nsecond"                        # document order, blank line apart


def test_merging_a_noted_and_an_unnoted_mark_yields_just_the_one_note():
    """No stray separator when only one of the absorbed marks carries anything."""
    before = (
        Highlight((_bar(70, 110),), color=YELLOW),
        Highlight((_bar(180, 220),), color=YELLOW, note="only this"),
    )
    after = merge_markup(before, (_bar(100, 190),), Highlight, YELLOW)
    (mark,) = after
    assert mark.note == "only this"


def test_remarking_a_noted_span_is_still_a_no_op():
    """The M59.10 idempotence guard (``merged != current``) must survive the new field, or every
    re-mark of a noted span would push a pointless undo entry."""
    before = (Highlight((LINE1,), color=YELLOW, note="stable"),)
    assert merge_markup(before, (LINE1,), Highlight, YELLOW) == before


def test_a_trimmed_mark_keeps_its_own_note():
    """The different-colour path uses ``replace``, so it preserves every field but ``rects`` —
    **when the mark survives the cut**. That qualifier is the whole of the bug below."""
    before = (Highlight((_bar(70, 220),), color=YELLOW, note="mine"),)
    after = merge_markup(before, (_bar(150, 220),), Highlight, GREEN)
    trimmed = next(m for m in after if m.color == YELLOW)
    assert trimmed.note == "mine"
    assert next(m for m in after if m.color == GREEN).note == ""   # the new paint carries none


# ---- recolouring a noted mark must not eat the note (owner-reported 2026-07-29) ----
#
# M81.2 fixed the *absorb* path and reasoned the trim path beside it was already safe "because it
# uses ``replace``". That is true only while something survives the cut to be replaced. A
# **recolour covers the mark completely**, so the trim leaves nothing, the mark is dropped — and
# the note goes with a mark the user never deleted. Reachable in two clicks from the M76 context
# menu the moment a note exists, and by sweeping a second colour over a noted passage.


def test_recolouring_a_noted_mark_keeps_its_note():
    """The report: pick another colour from the context menu's Highlight row and the note is gone.

    Recolouring deletes nothing — the passage is still marked, in a different colour — so nothing
    the user typed may be lost (the owner's *keep and join* rule, M81)."""
    before = (Highlight((LINE1,), color=YELLOW, note="check this figure"),)
    after = merge_markup(before, (LINE1,), Highlight, GREEN)
    (mark,) = after
    assert mark.color == GREEN
    assert mark.note == "check this figure"


@pytest.mark.parametrize("mark_type", [Highlight, Underline, Strikeout])
def test_recolouring_keeps_the_note_for_every_markup_type(mark_type):
    """Underline and strike-out have their own colour rows on the same menu, and the merge is one
    code path — so the fault and the fix are shared, and so is the guard."""
    before = (mark_type((LINE1,), color=YELLOW, note="a remark"),)
    (mark,) = merge_markup(before, (LINE1,), mark_type, GREEN)
    assert (mark.color, mark.note) == (GREEN, "a remark")


def test_recolouring_across_two_noted_marks_joins_both_notes():
    """Consistent with the absorb path (M81.2): several consumed notes join in document order
    rather than the later one winning."""
    before = (
        Highlight((_bar(70, 110),), color=YELLOW, note="first"),
        Highlight((_bar(180, 220),), color=YELLOW, note="second"),
    )
    after = merge_markup(before, (_bar(60, 230),), Highlight, GREEN)
    (mark,) = after
    assert mark.note == "first\n\nsecond"


def test_a_recolour_does_not_copy_a_note_off_a_mark_that_survives():
    """The other half of the rule, and the reason the fix is scoped to *fully consumed* marks: a
    partial recolour leaves the original standing, so its note stays with **its own** host (owner
    rule 5) instead of being duplicated onto the new colour."""
    before = (Highlight((_bar(70, 220),), color=YELLOW, note="mine"),)
    after = merge_markup(before, (_bar(150, 220),), Highlight, GREEN)
    assert next(m for m in after if m.color == YELLOW).note == "mine"
    assert next(m for m in after if m.color == GREEN).note == ""


def test_removing_a_layer_still_takes_its_note_with_it():
    """The cry-wolf guard for the fix: *removing* a mark is not recolouring it. The slashed dot on
    the same menu row is an explicit delete, and owner rule 2 says the note dies with its host."""
    from model.page_edits import remove_markup

    before = (Highlight((LINE1,), color=YELLOW, note="goes with it"),)
    assert remove_markup(before, (LINE1,), Highlight) == ()


def test_a_note_does_not_cross_mark_types():
    """The merge is scoped per type (M59.10), so a noted highlight is untouched by an underline
    swept over the same words — owner rule 5, and rule 6's layering."""
    before = (Highlight((LINE1,), color=YELLOW, note="on the highlight"),)
    after = merge_markup(before, (LINE1,), Underline, GREEN)
    highlight = next(m for m in after if isinstance(m, Highlight))
    assert highlight.note == "on the highlight"
    assert next(m for m in after if isinstance(m, Underline)).note == ""


# ---- M81.3 adoption carries the comment, and the warning stops lying --------------


def _commented(page, kind, rect, contents, author="Alice"):
    add = {
        "highlight": page.add_highlight_annot,
        "underline": page.add_underline_annot,
        "strikeout": page.add_strikeout_annot,
    }.get(kind)
    annot = add(fitz.Rect(rect)) if add else page.add_rect_annot(fitz.Rect(rect))
    annot.set_info(title=author, content=contents)
    annot.update()
    return annot


@pytest.fixture
def reviewed_pdf(tmp_path):
    """What Edge / Acrobat / Preview leave behind: a highlight carrying a reviewer's comment."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 300), "REVIEWED TEXT", fontsize=12)
    yield doc, page
    doc.close()


@pytest.mark.parametrize("kind", ["highlight", "underline", "strikeout"])
def test_adopting_a_commented_foreign_mark_keeps_the_comment(reviewed_pdf, kind):
    """The live v0.16.2 data loss: M68 adoption strips the original and re-adds ours, so a comment
    the parser never read was gone in two clicks, with nothing said."""
    _doc, page = reviewed_pdf
    annot = _commented(page, kind, (60, 292, 160, 306), "please rewrite this")
    adopted = adopt_annotation(annot)
    assert adopted is not None
    assert adopted.note == "please rewrite this"


def test_a_commented_foreign_mark_no_longer_warns(reviewed_pdf):
    """Cured, not papered over: the descriptor now holds the comment, so there is nothing to warn
    about — and a warning that fires when nothing is lost is how a warning stops being read."""
    _doc, page = reviewed_pdf
    annot = _commented(page, "highlight", (60, 292, 160, 306), "please rewrite this")
    assert "its comment" not in degradations(annot)


def test_a_commented_drawn_mark_still_warns(reviewed_pdf):
    """The four drawn kinds have no field to hold a comment, so there the loss is real. Saying so
    is what makes *empty means adoption is lossless* true for the first time."""
    _doc, page = reviewed_pdf
    annot = _commented(page, "square", (60, 200, 160, 260), "a reviewer's aside")
    assert "its comment" in degradations(annot)


def test_an_uncommented_drawn_mark_warns_about_nothing(reviewed_pdf):
    """The cry-wolf guard for the new check."""
    _doc, page = reviewed_pdf
    annot = _commented(page, "square", (60, 200, 160, 260), "")
    assert degradations(annot) == []


def test_an_adopted_comment_round_trips_as_our_own_note(text_pdf, tmp_path):
    """End to end on the file the loss was found in: adopt a commented foreign highlight, save,
    reopen — the reviewer's comment is now an editable KlarPDF note under our own author tag."""
    doc = fitz.open(text_pdf)
    page = doc[0]
    _commented(page, "highlight", tuple(page.get_text("words")[0][:4]), "reviewer said this")
    reviewed = str(tmp_path / "reviewed.pdf")
    doc.save(reviewed)
    doc.close()

    v = VirtualDocument.from_path(reviewed)
    ref = v.ordered[0]
    source = v.sources[ref.source_id][ref.source_page_index]
    (foreign,) = list(source.annots())
    adopted = adopt_annotation(foreign)
    from model.foreign_annots import ForeignDeletion, fingerprint

    v.add_annotation(0, ForeignDeletion(fingerprint(foreign), "Highlight"))
    v.add_annotation(0, adopted)
    out = _materialize(v, tmp_path, "adopted.pdf")
    v.close()

    saved = fitz.open(out)
    (annot,) = list(saved[0].annots())               # the original was stripped, not left under
    assert annot.info.get("title") == KLARPDF_AUTHOR
    assert annot.info.get("content") == "reviewer said this"
    (mark,) = read_klarpdf_annotations(saved[0])
    assert mark.note == "reviewer said this"
    saved.close()
