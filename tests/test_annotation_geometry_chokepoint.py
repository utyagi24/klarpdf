"""The annotations tuple is heterogeneous, and the hit-tests must all know it (M83).

Owner-reported 2026-07-27 while testing interop with a PDF annotated in Edge — a console traceback,
filed as "expose any unknown gap" rather than as a fix request::

    AttributeError: 'ForeignDeletion' object has no attribute 'rect'
      viewer/annotations.py:826 in annotation_at

**What the user saw was not a crash.** Qt swallows exceptions raised from a Python override of one
of its virtuals, so the app survived and the **context menu silently never appeared**. Once a
document was in that state every right-click in the page view was dead, with nothing surfaced —
which is why it reached the console instead of a failure dialog.

``PageRef.annotations`` holds real marks *plus* non-geometric bookkeeping — ``ForeignDeletion``
(fingerprint, label) and ``ForeignMove`` (fingerprint, dx, dy, label). Riding the same tuple is
deliberate (M66/M67): it is how they snapshot for undo and follow their page through a reorder. The
defect was that nothing enforced the split — four of five sites happened to ``isinstance``-guard
first, one resolved geometry with ``hasattr``, and nothing would have caught the difference.

Two halves under test:

* **M83.1** the hit-test survives — right-click still works after deleting, moving or adopting a
  foreign annotation, which is exactly the owner's repro;
* **M83.2** it is structural rather than conventional — one ``rects_of`` / ``is_geometric``
  answering "does this describe a region of the page?", which every geometry site routes through.
  The load-bearing test is the one that invents a **brand-new** non-geometric descriptor: it fails
  for the *next* one, not just for the two that exist today.

Headless + offscreen GUI.
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf as fitz
import pytest

from app import PdfApp
from main_window import MainWindow
from model.content_marks import Stamp
from model.foreign_annots import ForeignDeletion, ForeignMove, read_foreign_annotations
from model.form_fields import NewField
from model.page_edits import (
    Highlight,
    InkStroke,
    Line,
    Redaction,
    Shape,
    Strikeout,
    TextBox,
    Underline,
    is_geometric,
    mark_bounds,
    rects_of,
)
from store.settings import Settings

BOX = (60.0, 292.0, 200.0, 306.0)


# ---- M83.2 the chokepoint -------------------------------------------------------


GEOMETRIC = [
    Highlight((BOX,)),
    Underline((BOX,)),
    Strikeout((BOX,)),
    Redaction((BOX,)),
    TextBox((60.0, 200.0, 260.0, 240.0), "hi"),
    Shape("rect", (60.0, 100.0, 160.0, 160.0)),
    Line((60.0, 100.0), (160.0, 160.0)),
    InkStroke((((60.0, 100.0), (160.0, 160.0)),)),
    Stamp((60.0, 100.0, 160.0, 160.0), "DRAFT"),
    NewField((60.0, 100.0, 160.0, 130.0), "field1"),
]

NON_GEOMETRIC = [
    ForeignDeletion("fp:x", "Highlight"),
    ForeignMove("fp:x", 10.0, 5.0),
]


@pytest.mark.parametrize("mark", GEOMETRIC, ids=lambda m: type(m).__name__)
def test_every_real_mark_reports_geometry(mark):
    boxes = rects_of(mark)
    assert boxes and all(len(box) == 4 for box in boxes)
    assert is_geometric(mark) is True


@pytest.mark.parametrize("mark", NON_GEOMETRIC, ids=lambda m: type(m).__name__)
def test_the_bookkeeping_descriptors_report_none(mark):
    """``()`` rather than a raise: the hit-tests then skip them by iterating zero times, instead of
    each site remembering to guard — which is the arrangement that failed."""
    assert rects_of(mark) == ()
    assert is_geometric(mark) is False


@dataclass(frozen=True)
class _FutureBookkeeping:
    """A non-geometric descriptor that does not exist yet — the whole point of M83.2.

    M85 is safe because it adds a *field* to existing marks, but the next descriptor of this shape
    would have landed in exactly the same trap. The predicate must answer for it without anyone
    having listed it anywhere.
    """

    fingerprint: str
    label: str = ""


def test_a_descriptor_type_nobody_listed_is_still_answered():
    assert rects_of(_FutureBookkeeping("fp:future")) == ()
    assert is_geometric(_FutureBookkeeping("fp:future")) is False


def test_rects_of_never_raises_on_anything_in_the_tuple():
    """Total by contract. A geometry accessor that can raise is a geometry accessor every caller
    must remember to guard, which is the convention this replaces."""
    for mark in GEOMETRIC + NON_GEOMETRIC + [_FutureBookkeeping("fp:x")]:
        rects_of(mark)


def test_mark_bounds_unions_a_multi_line_markup():
    """It now shares one geometry source with the hit-tests instead of re-deriving it, so it also
    answers for the text-markup types — which have ``rects`` and no ``bounding_rect``, and used to
    raise ``AttributeError`` here."""
    two_lines = Highlight(((70.0, 66.0, 220.0, 80.0), (70.0, 86.0, 150.0, 100.0)))
    assert mark_bounds(two_lines) == (70.0, 66.0, 220.0, 100.0)


def test_mark_bounds_still_answers_a_text_box_with_its_own_rect():
    """``TextBox`` is the one free-placed type with no ``bounding_rect()``, which is why the old
    implementation special-cased it. The fallback order must keep giving the same answer."""
    box = TextBox((60.0, 200.0, 260.0, 240.0), "hi")
    assert mark_bounds(box) == box.rect


def test_mark_bounds_refuses_a_descriptor_with_no_geometry():
    """Its callers are all asking "where do I draw this mark's outline / handles" — a question a
    ``ForeignDeletion`` has no answer to. A silent zero rect would put selection chrome at the page
    corner, which is worse than the raise."""
    with pytest.raises(ValueError):
        mark_bounds(ForeignDeletion("fp:x"))


# ---- M83.1 the owner's repro (offscreen GUI) ------------------------------------


def _foreign(page, kind, rect, contents="", author="Alice"):
    add = {
        "highlight": lambda: page.add_highlight_annot(fitz.Rect(rect)),
        "text": lambda: page.add_text_annot(fitz.Point(rect[0], rect[1]), contents or "note"),
    }[kind]
    annot = add()
    annot.set_info(title=author, content=contents)
    annot.update()
    return annot


@pytest.fixture
def edge_pdf(tmp_path) -> str:
    """Modelled on the owner's ``ClientStatements_5752_043026.pdf``: highlights annotated in Edge
    carrying their comment in ``/Contents`` with an empty ``/T``, so they read as foreign — plus a
    sticky note, the unmodeled type whose deletion is the obvious thing to try when testing
    interop, and which is what put a ``ForeignDeletion`` in the tuple."""
    path = str(tmp_path / "edge.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 302), "REVIEWERS MARKED THIS SENTENCE", fontsize=12)
    _foreign(page, "highlight", BOX, "Comment to yello highlight", author="")
    _foreign(page, "text", (300.0, 100.0, 320.0, 120.0), "a sticky note", author="")
    doc.save(path)
    doc.close()
    return path


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    return qapp


@pytest.fixture
def win(app, edge_pdf):
    w = MainWindow(app, edge_pdf, app.settings)
    w.show()
    app.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def _scene(win, x: float, y: float):
    return win.view.scene_rect_for_box(0, (x, y, x + 0.01, y + 0.01)).center()


def _on_the_text(win):
    return _scene(win, (BOX[0] + BOX[2]) / 2, (BOX[1] + BOX[3]) / 2)


def _bare_page_point(win):
    """Empty page, well clear of every mark — so the hit-test walks the *whole* tuple."""
    return _scene(win, 420.0, 600.0)


def _foreign_marks(win):
    ref = win.vdoc.ordered[0]
    return read_foreign_annotations(win.vdoc.sources[ref.source_id][ref.source_page_index])


def test_the_hit_test_survives_a_foreign_deletion(win):
    """The traceback itself. Deleting the sticky note puts a ``ForeignDeletion`` in the tuple; the
    next hit-test walked into it looking for a rect."""
    note = next(m for m in _foreign_marks(win) if m.kind_name == "Text")
    win._delete_foreign_annotation(0, note)
    assert win.view.annotations.annotation_at(_on_the_text(win)) is None   # not an exception


def test_the_hit_test_survives_a_foreign_move(win):
    """The other bookkeeping type, by the other verb (M67)."""
    note = next(m for m in _foreign_marks(win) if m.kind_name == "Text")
    win._move_foreign_annotation(0, note, 30.0, 20.0)
    assert win.view.annotations.annotation_at(_on_the_text(win)) is None


def test_the_context_menu_still_appears_after_deleting_a_foreign_mark(win):
    """What the user actually lost. Qt swallows the exception, so the symptom was not a crash but
    a right-click that did nothing — for the rest of the session, on every page."""
    note = next(m for m in _foreign_marks(win) if m.kind_name == "Text")
    win._delete_foreign_annotation(0, note)
    menu = win._view_context_menu(_on_the_text(win))
    assert menu is not None
    assert menu.actions()                       # a menu with no verbs is the same dead right-click


def test_the_context_menu_still_appears_after_adopting_a_foreign_highlight(win):
    """Adoption (M68) is a deletion **plus** a parsed descriptor in one macro, so it reaches the
    same state by the path a reader is most likely to take on a reviewed document.

    Right-clicking **bare page** rather than the adopted mark, deliberately. The tuple is walked
    reversed, so a click *on* the mark returns before reaching the bookkeeping entry behind it and
    would pass either way; the reported symptom was that every right-click in the page view went
    dead, and only a walk that runs to the end reproduces it.
    """
    highlight = next(m for m in _foreign_marks(win) if m.kind_name == "Highlight")
    assert win._adopt_foreign_annotation(0, highlight) is True
    menu = win._view_context_menu(_bare_page_point(win))
    assert menu is not None
    assert menu.actions()


def test_the_adopted_mark_is_still_found_under_the_cursor(win):
    """Not merely "no exception": the surviving geometric entry must still hit-test, or the fix
    would be a skip that swallowed the real marks with the bookkeeping."""
    highlight = next(m for m in _foreign_marks(win) if m.kind_name == "Highlight")
    win._adopt_foreign_annotation(0, highlight)
    hit = win.view.annotations.annotation_at(_on_the_text(win))
    assert hit is not None
    assert isinstance(hit[1], Highlight)


def test_a_real_mark_behind_a_bookkeeping_entry_still_wins(win):
    """The tuple is walked reversed (topmost first). A bookkeeping entry added *after* a mark must
    not stop the walk before reaching it — it must be transparent, not terminal."""
    win.vdoc.add_annotation(0, Highlight((BOX,)))
    win.vdoc.add_annotation(0, ForeignDeletion("fp:whatever", "Highlight"))
    hit = win.view.annotations.annotation_at(_on_the_text(win))
    assert hit is not None
    assert isinstance(hit[1], Highlight)
