"""Move foreign marks (PLAN.md §R5, M67). Headless + offscreen GUI.

M66's second verb. The guarantee is stronger than "it ends up somewhere else": **the appearance
stream is preserved verbatim**, so a rich callout box a different tool drew moves with *zero*
degradation — nothing re-renders it, because nothing needs to.

That is what forces the implementation away from the obvious ``Annot.set_rect``: on the quad-based
text-markup types it **silently returns False** and leaves the rect alone ("Highlight annotations
have no Rect property"), so a move built on it would fail invisibly on every highlight, underline
and strikeout. Translating the geometry keys in the annotation's dictionary works for all of them
and never touches the appearance at all.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from app import PdfApp
from main_window import MainWindow
from model.edit_engine import PyMuPDFEngine
from model.foreign_annots import (
    ForeignDeletion,
    ForeignMove,
    apply_foreign_edits,
    read_foreign_annotations,
)
from model.virtual_document import VirtualDocument
from store.settings import Settings

DX, DY = 40.0, 25.0


def _add(page, kind, rect, contents=""):
    if kind == "square":
        annot = page.add_rect_annot(fitz.Rect(rect))
    elif kind == "circle":
        annot = page.add_circle_annot(fitz.Rect(rect))
    elif kind == "highlight":
        annot = page.add_highlight_annot(fitz.Rect(rect))
    elif kind == "text":
        annot = page.add_text_annot(fitz.Point(rect[0], rect[1]), contents or "note")
    else:
        annot = page.add_freetext_annot(fitz.Rect(rect), contents or "callout")
    annot.set_info(title="Alice", content=contents)
    annot.update()
    return annot


@pytest.fixture
def marked_pdf(tmp_path) -> str:
    path = str(tmp_path / "marked.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 500), "BODYTEXT", fontsize=12)
    _add(page, "square", (100, 100, 200, 160), "a square")
    _add(page, "freetext", (250, 100, 400, 160), "a callout")
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def vdoc(marked_pdf):
    v = VirtualDocument.from_path(marked_pdf)
    yield v
    v.close()


def _materialize(vdoc, tmp_path, name="out.pdf") -> str:
    out = str(tmp_path / name)
    PyMuPDFEngine().materialize(vdoc, out)
    return out


def _source_page(vdoc, index=0):
    ref = vdoc.ordered[index]
    return vdoc.sources[ref.source_id][ref.source_page_index]


def _ap_stream(doc, annot) -> bytes:
    key = doc.xref_get_key(annot.xref, "AP/N")
    return doc.xref_stream(int(key[1].split()[0])) if key[0] == "xref" else b""


# ---- the move itself ------------------------------------------------------------


def test_move_translates_the_annotation(vdoc, tmp_path):
    target = read_foreign_annotations(_source_page(vdoc))[0]
    vdoc.add_annotation(0, ForeignMove(target.fingerprint, DX, DY))
    saved = fitz.open(_materialize(vdoc, tmp_path))
    try:
        moved = read_foreign_annotations(saved[0])[0]
        assert moved.rect == pytest.approx(
            (target.rect[0] + DX, target.rect[1] + DY, target.rect[2] + DX, target.rect[3] + DY),
            abs=0.5,
        )
    finally:
        saved.close()


def test_the_other_annotation_stays_put(vdoc, tmp_path):
    found = read_foreign_annotations(_source_page(vdoc))
    vdoc.add_annotation(0, ForeignMove(found[0].fingerprint, DX, DY))
    saved = fitz.open(_materialize(vdoc, tmp_path))
    try:
        after = {a.kind_name: a.rect for a in read_foreign_annotations(saved[0])}
        assert after["FreeText"] == pytest.approx(found[1].rect, abs=0.5)
    finally:
        saved.close()


@pytest.mark.parametrize("kind", ["square", "circle", "highlight", "text", "freetext"])
def test_every_annotation_type_moves(tmp_path, kind):
    """Including the quad-based ones, which ``set_rect`` refuses outright."""
    path = str(tmp_path / f"{kind}.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 300), "some text to mark", fontsize=12)
    _add(page, kind, (60, 292, 200, 306), "x")
    doc.save(path)
    doc.close()

    vdoc = VirtualDocument.from_path(path)
    try:
        before = read_foreign_annotations(_source_page(vdoc))[0]
        vdoc.add_annotation(0, ForeignMove(before.fingerprint, DX, DY))
        saved = fitz.open(_materialize(vdoc, tmp_path, f"{kind}-out.pdf"))
        try:
            after = read_foreign_annotations(saved[0])[0]
            assert after.rect[0] == pytest.approx(before.rect[0] + DX, abs=1.0)
            assert after.rect[1] == pytest.approx(before.rect[1] + DY, abs=1.0)
        finally:
            saved.close()
    finally:
        vdoc.close()


def test_set_rect_really_does_refuse_quad_annotations(tmp_path):
    """Pins why the implementation edits the dictionary instead.

    And note *how* it refuses: ``set_rect`` returns **False** and leaves the rect alone rather than
    raising. A move built on it would therefore fail **silently** on every highlight, underline and
    strikeout — the worst possible shape for this bug, and the reason it is worth a test.
    """
    doc = fitz.open()
    page = doc.new_page()
    try:
        page.insert_text((60, 300), "mark me", fontsize=12)
        annot = _add(page, "highlight", (60, 292, 130, 306))
        before = tuple(annot.rect)
        assert annot.set_rect(annot.rect + (10, 10, 10, 10)) is False
        assert tuple(annot.rect) == before
    finally:
        doc.close()


# ---- the guarantee: appearance preserved verbatim -------------------------------


def test_appearance_stream_is_byte_identical_after_a_move(vdoc, tmp_path):
    """The whole point of M67: a rich callout box moves with zero degradation."""
    def streams(path):
        doc = fitz.open(path)
        try:
            return {a.type[1]: _ap_stream(doc, a) for a in doc[0].annots()}
        finally:
            doc.close()

    before = streams(_materialize(vdoc, tmp_path, "before.pdf"))
    target = read_foreign_annotations(_source_page(vdoc))[1]      # the FreeText callout
    vdoc.add_annotation(0, ForeignMove(target.fingerprint, DX, DY))
    after = streams(_materialize(vdoc, tmp_path, "after.pdf"))

    assert before and after.keys() == before.keys()
    for kind, stream in after.items():
        assert stream == before[kind], f"{kind}'s appearance was re-rendered by a move"
    assert after["FreeText"]                                       # …and it was a real appearance


def test_quadpoints_travel_with_the_rect(tmp_path):
    """A highlight's drawn geometry lives in ``/QuadPoints``. If only ``/Rect`` moved, any viewer
    that regenerates appearances from the quads would snap the mark back."""
    path = str(tmp_path / "hl.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 300), "highlight this", fontsize=12)
    _add(page, "highlight", (60, 292, 160, 306))
    doc.save(path)
    doc.close()

    vdoc = VirtualDocument.from_path(path)
    try:
        target = read_foreign_annotations(_source_page(vdoc))[0]
        vdoc.add_annotation(0, ForeignMove(target.fingerprint, DX, DY))
        out = _materialize(vdoc, tmp_path)
        saved = fitz.open(out)
        try:
            annot = next(iter(saved[0].annots()))
            quads = saved.xref_get_key(annot.xref, "QuadPoints")[1]
            xs = [float(v) for v in quads.strip("[] ").split()][0::2]
            assert min(xs) == pytest.approx(60 + DX, abs=1.5)
        finally:
            saved.close()
    finally:
        vdoc.close()


def test_the_move_shows_in_the_rendered_page(vdoc, tmp_path):
    """A move that changed the dictionary but not the pixels would be no move at all."""
    plain = fitz.open(_materialize(vdoc, tmp_path, "plain.pdf"))
    before = plain[0].get_pixmap(dpi=72).samples
    plain.close()

    target = read_foreign_annotations(_source_page(vdoc))[0]
    vdoc.add_annotation(0, ForeignMove(target.fingerprint, DX, DY))
    shifted = fitz.open(_materialize(vdoc, tmp_path, "shifted.pdf"))
    try:
        assert shifted[0].get_pixmap(dpi=72).samples != before
    finally:
        shifted.close()


# ---- composing with deletion, and identity stability ----------------------------


def test_a_move_and_a_delete_on_one_page(vdoc, tmp_path):
    """Fingerprints are resolved **before** anything is applied, so a move cannot invalidate the
    descriptor aimed at another mark — the failure mode this ordering exists to prevent."""
    found = read_foreign_annotations(_source_page(vdoc))
    vdoc.add_annotation(0, ForeignMove(found[0].fingerprint, DX, DY))
    vdoc.add_annotation(0, ForeignDeletion(found[1].fingerprint))
    saved = fitz.open(_materialize(vdoc, tmp_path))
    try:
        remaining = read_foreign_annotations(saved[0])
        assert [a.kind_name for a in remaining] == ["Square"]
        assert remaining[0].rect[0] == pytest.approx(found[0].rect[0] + DX, abs=0.5)
    finally:
        saved.close()


def test_deleting_a_moved_mark_deletes_it(vdoc, tmp_path):
    """Moving something and then deleting it must delete it — deletion wins over a move for the
    same mark. Worth pinning because a move changes the rect a hash fingerprint is derived from, so
    a naive implementation could easily leave the deletion unable to find its target."""
    found = read_foreign_annotations(_source_page(vdoc))
    vdoc.add_annotation(0, ForeignMove(found[0].fingerprint, DX, DY))
    vdoc.add_annotation(0, ForeignDeletion(found[0].fingerprint))
    saved = fitz.open(_materialize(vdoc, tmp_path))
    try:
        remaining = read_foreign_annotations(saved[0])
        assert [a.kind_name for a in remaining] == ["FreeText"]      # the square is gone
        assert remaining[0].rect == pytest.approx(found[1].rect, abs=0.5)   # the other untouched
    finally:
        saved.close()


def test_a_move_with_no_target_is_a_no_op(vdoc, tmp_path):
    vdoc.add_annotation(0, ForeignMove("fp:nothing", DX, DY))
    saved = fitz.open(_materialize(vdoc, tmp_path))
    try:
        assert len(read_foreign_annotations(saved[0])) == 2
    finally:
        saved.close()


def test_apply_foreign_edits_reports_both_counts(vdoc):
    doc = fitz.open()
    page = doc.new_page()
    try:
        _add(page, "square", (10, 10, 60, 40), "a")
        _add(page, "circle", (80, 10, 130, 40), "b")
        found = read_foreign_annotations(page)
        counts = apply_foreign_edits(page, (
            ForeignMove(found[0].fingerprint, 5, 5),
            ForeignDeletion(found[1].fingerprint),
        ))
        assert counts == (1, 1)
    finally:
        doc.close()


def test_moves_are_hashable_for_undo_snapshots():
    assert len({ForeignMove("a", 1, 2), ForeignMove("a", 1, 2), ForeignMove("a", 3, 4)}) == 2


def test_move_never_touches_the_shared_source(vdoc, tmp_path):
    target = read_foreign_annotations(_source_page(vdoc))[0]
    vdoc.add_annotation(0, ForeignMove(target.fingerprint, DX, DY))
    _materialize(vdoc, tmp_path)
    assert read_foreign_annotations(_source_page(vdoc))[0].rect == target.rect


# ---- the viewer (offscreen GUI) -------------------------------------------------


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    return qapp


@pytest.fixture
def win(app, marked_pdf):
    w = MainWindow(app, marked_pdf, app.settings)
    yield w
    w.undo_stack.setClean()
    w.close()


def _scene(win, x: float, y: float):
    return win.view.scene_rect_for_box(0, (x, y, x + 0.01, y + 0.01)).center()


def _drag(win, start, end):
    overlay = win.view.annotations
    assert overlay.begin_foreign_move(_scene(win, *start)) is True
    overlay.update_move(_scene(win, *end))
    moved = overlay.finish_foreign_move()
    if moved is not None:
        win._move_foreign_annotation(*moved)
    return moved


def _moves(win):
    return [a for a in win.vdoc.page_annotations(0) if isinstance(a, ForeignMove)]


def test_dragging_a_foreign_mark_records_a_move(win):
    _drag(win, (150, 130), (190, 155))
    assert len(_moves(win)) == 1
    assert (_moves(win)[0].dx, _moves(win)[0].dy) == pytest.approx((40, 25), abs=2)


def test_a_click_without_a_drag_only_selects(win):
    overlay = win.view.annotations
    assert overlay.begin_foreign_move(_scene(win, 150, 130)) is True
    assert overlay.finish_foreign_move() is None
    assert _moves(win) == []


def test_the_move_is_undoable(win):
    _drag(win, (150, 130), (190, 155))
    win.undo_stack.undo()
    assert _moves(win) == []


def test_dragging_twice_combines_into_one_descriptor(win):
    """Two descriptors for one mark would be wrong, not just untidy: the second would be keyed on
    the moved rect, which is not the fingerprint the page arrives with at materialise."""
    _drag(win, (150, 130), (190, 155))
    _drag(win, (190, 155), (210, 165))
    assert len(_moves(win)) == 1
    assert (_moves(win)[0].dx, _moves(win)[0].dy) == pytest.approx((60, 35), abs=3)


def test_a_moved_mark_hit_tests_at_its_new_position(win):
    """The descriptor lives in the model while the annotation stays put in the read-only source, so
    the reported rect has to carry the pending move — or clicking the mark you can see does
    nothing, while clicking empty space grabs it."""
    # The square spans roughly (99, 99)-(201, 161); after +40/+25 it spans (139, 124)-(241, 186).
    # Probe points chosen to fall in exactly one of the two, or the boxes' overlap proves nothing.
    _drag(win, (150, 130), (190, 155))
    assert win.view.annotations.foreign_annotation_at(_scene(win, 110, 110)) is None
    assert win.view.annotations.foreign_annotation_at(_scene(win, 230, 175)) is not None


def test_the_render_shows_the_moved_position(win):
    _drag(win, (150, 130), (190, 155))
    ref = win.vdoc.ordered[0]
    rendered = win.view._deleted_foreign_page(0, ref)
    assert rendered is not None
    moved = read_foreign_annotations(rendered)[0]
    assert moved.rect[0] == pytest.approx(100 + 40, abs=3)


def test_an_editable_mark_wins_a_shared_spot(win):
    """Our own marks are tried first, so placing a text box over a foreign square still lets you
    drag the text box."""
    from model.page_edits import TextBox

    win.vdoc.add_annotation(0, TextBox((110, 110, 190, 150), "mine"))
    win.view.reload()
    overlay = win.view.annotations
    assert overlay.begin_move(_scene(win, 150, 130)) is True
    overlay.finish_move()
    assert _moves(win) == []
