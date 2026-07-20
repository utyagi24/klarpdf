"""Foreign annotations — infrastructure + delete (PLAN.md §R5, M66). Headless + offscreen GUI.

The R5 keystone. Until now a PDF's *existing* annotations were untouchable scenery: copied through
by ``insert_pdf(annots=True)`` and otherwise ignored, because only KlarPDF-authored marks round-trip
into the editable model. This adds the shared machinery to reach them — enumerate, fingerprint,
hit-test — and its first verb, **delete**.

The claims under test:

* **identity survives the copy** — an annotation's ``xref`` is renumbered by ``insert_pdf``, so
  matching is by ``/NM`` or a hash of type + rect + contents, and identical twins resolve
  positionally rather than both dying to one descriptor;
* **zero fidelity risk** — deleting one annotation leaves every other one *byte-identical*, for
  every annotation type, including ones the model cannot draw;
* **it is an ordinary page edit** — undoable, riding the PageRef, destroying nothing in the source.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from app import PdfApp
from main_window import MainWindow
from model.edit_engine import PyMuPDFEngine
from model.foreign_annots import (
    ForeignDeletion,
    annot_name,
    apply_foreign_deletions,
    fingerprint,
    is_foreign,
    page_has_foreign_annotations,
    read_foreign_annotations,
)
from model.page_edits import KLARPDF_AUTHOR, Highlight
from model.virtual_document import VirtualDocument
from store.settings import Settings


def _clear_nm(page) -> None:
    """Strip every annotation's ``/NM``, to exercise the **hash fallback** deliberately.

    PyMuPDF stamps an auto-generated ``/NM`` ("fitz-A0", …) on annotations it creates, so a fixture
    built with it would only ever test the preferred name path and leave the fallback — the one that
    has to cope with float noise and identical twins — completely unexercised.
    """
    for annot in page.annots():
        page.parent.xref_set_key(annot.xref, "NM", "null")


def _add_foreign(page, kind: str, rect, *, author="Alice", contents="", name=None):
    """Add an annotation as a *different* tool would: authored by someone other than KlarPDF."""
    if kind == "square":
        annot = page.add_rect_annot(fitz.Rect(rect))
    elif kind == "circle":
        annot = page.add_circle_annot(fitz.Rect(rect))
    elif kind == "text":
        annot = page.add_text_annot(fitz.Point(rect[0], rect[1]), contents or "note")
    elif kind == "highlight":
        annot = page.add_highlight_annot(fitz.Rect(rect))
    else:
        annot = page.add_freetext_annot(fitz.Rect(rect), contents or "hi")
    annot.set_info(title=author, content=contents)
    if name is not None:
        page.parent.xref_set_key(annot.xref, "NM", fitz.get_pdf_str(name))
    annot.update()
    return annot


@pytest.fixture
def foreign_pdf(tmp_path) -> str:
    """One page carrying three foreign annotations of different types, plus body text."""
    path = str(tmp_path / "foreign.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 100), "BODYTEXT here", fontsize=12)
    _add_foreign(page, "square", (200, 200, 300, 260), contents="a square")
    _add_foreign(page, "text", (320, 200, 340, 220), contents="a sticky note", author="Bob")
    _add_foreign(page, "circle", (200, 300, 300, 360), contents="a circle")
    doc.new_page()
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def vdoc(foreign_pdf) -> VirtualDocument:
    v = VirtualDocument.from_path(foreign_pdf)
    yield v
    v.close()


def _materialize(vdoc, tmp_path, name="out.pdf") -> str:
    out = str(tmp_path / name)
    PyMuPDFEngine().materialize(vdoc, out)
    return out


def _source_page(vdoc, index=0):
    ref = vdoc.ordered[index]
    return vdoc.sources[ref.source_id][ref.source_page_index]


# ---- enumeration ----------------------------------------------------------------


def test_reads_every_foreign_annotation(vdoc):
    found = read_foreign_annotations(_source_page(vdoc))
    assert [a.kind_name for a in found] == ["Square", "Text", "Circle"]
    assert [a.author for a in found] == ["Alice", "Bob", "Alice"]
    assert found[1].contents == "a sticky note"


def test_our_own_marks_are_not_foreign(vdoc, tmp_path):
    """A KlarPDF highlight round-trips into the editable model and already has a better handle than
    a fingerprint — it must never show up as foreign, or it would be deletable twice over."""
    vdoc.add_annotation(0, Highlight(((60, 90, 140, 105),)))
    saved = fitz.open(_materialize(vdoc, tmp_path))
    try:
        annots = list(saved[0].annots())
        ours = [a for a in annots if (a.info or {}).get("title") == KLARPDF_AUTHOR]
        assert len(ours) == 1 and not is_foreign(ours[0])
        assert len(read_foreign_annotations(saved[0])) == 3   # only the original three
    finally:
        saved.close()


def test_page_has_foreign_annotations(vdoc):
    assert page_has_foreign_annotations(_source_page(vdoc, 0)) is True
    assert page_has_foreign_annotations(_source_page(vdoc, 1)) is False


def test_label_names_the_type_and_author(vdoc):
    found = read_foreign_annotations(_source_page(vdoc))
    assert found[1].label == "Text by Bob"


# ---- fingerprint identity -------------------------------------------------------


def test_fingerprint_survives_a_materialise(vdoc, tmp_path):
    """The whole reason fingerprints exist: ``insert_pdf`` renumbers every xref, so identity must
    come from something that survives the copy."""
    before = read_foreign_annotations(_source_page(vdoc))
    saved = fitz.open(_materialize(vdoc, tmp_path))
    try:
        after = read_foreign_annotations(saved[0])
        assert [a.fingerprint for a in after] == [a.fingerprint for a in before]
    finally:
        saved.close()


def test_xrefs_really_do_change(vdoc, tmp_path):
    """Pins the premise. If xrefs ever did survive, the fingerprint machinery would be unnecessary
    — and this test is what would tell us."""
    before = {a.xref for a in _source_page(vdoc).annots()}
    saved = fitz.open(_materialize(vdoc, tmp_path))
    try:
        after = {a.xref for a in saved[0].annots()}
        assert before != after
    finally:
        saved.close()


def test_nm_name_is_preferred_when_present(tmp_path):
    path = str(tmp_path / "named.pdf")
    doc = fitz.open()
    page = doc.new_page()
    _add_foreign(page, "square", (10, 10, 60, 40), name="acrobat-comment-7")
    doc.save(path)
    doc.close()

    reopened = fitz.open(path)
    try:
        found = read_foreign_annotations(reopened[0])
        assert found[0].fingerprint == "nm:acrobat-comment-7"
    finally:
        reopened.close()


def test_pymupdf_info_does_not_expose_the_name(tmp_path):
    """Pins the trap: ``annot.info["name"]`` is empty even when ``/NM`` is set, so reading identity
    from it would silently send every annotation down the hash fallback."""
    doc = fitz.open()
    page = doc.new_page()
    try:
        _add_foreign(page, "square", (10, 10, 60, 40), name="acrobat-comment-7")
        annot = next(iter(page.annots()))
        assert (annot.info or {}).get("name", "") == ""
        assert annot_name(annot) == "acrobat-comment-7"
    finally:
        doc.close()


def test_different_annotations_fingerprint_differently(vdoc):
    prints = {a.fingerprint for a in read_foreign_annotations(_source_page(vdoc))}
    assert len(prints) == 3


def test_the_hash_fallback_is_used_when_there_is_no_name(tmp_path):
    """Not every writer sets ``/NM``, so the fallback has to work on its own."""
    path = str(tmp_path / "anon.pdf")
    doc = fitz.open()
    page = doc.new_page()
    _add_foreign(page, "square", (10, 10, 60, 40), contents="one")
    _add_foreign(page, "square", (10, 80, 60, 110), contents="two")
    _clear_nm(page)
    doc.save(path)
    doc.close()

    reopened = fitz.open(path)
    try:
        found = read_foreign_annotations(reopened[0])
        assert all(a.fingerprint.startswith("fp:") for a in found)
        assert len({a.fingerprint for a in found}) == 2
    finally:
        reopened.close()


def test_fingerprint_tolerates_float_noise(tmp_path):
    """Coordinates go out and back through PDF decimals, so the hash rounds — an exact match would
    make a fingerprint fail on the very round-trip it exists to survive. (Names cleared, or this
    would compare two auto-assigned ``/NM``s and prove nothing about the hash.)"""
    doc_a = fitz.open()
    page_a = doc_a.new_page()
    _add_foreign(page_a, "square", (10.0, 10.0, 60.0, 40.0), contents="x")
    _clear_nm(page_a)

    doc_b = fitz.open()
    page_b = doc_b.new_page()
    _add_foreign(page_b, "square", (10.00001, 9.99999, 60.00002, 40.0), contents="x")
    _clear_nm(page_b)
    try:
        a = next(iter(page_a.annots()))
        b = next(iter(page_b.annots()))
        assert fingerprint(a) == fingerprint(b)
        assert fingerprint(a).startswith("fp:")
    finally:
        doc_a.close()
        doc_b.close()


# ---- deleting -------------------------------------------------------------------


def test_delete_removes_exactly_that_annotation(vdoc, tmp_path):
    target = read_foreign_annotations(_source_page(vdoc))[1]      # the sticky note
    vdoc.add_annotation(0, ForeignDeletion(target.fingerprint, target.label))
    saved = fitz.open(_materialize(vdoc, tmp_path))
    try:
        remaining = read_foreign_annotations(saved[0])
        assert [a.kind_name for a in remaining] == ["Square", "Circle"]
    finally:
        saved.close()


@pytest.mark.parametrize("index", [0, 1, 2])
def test_any_annotation_type_can_be_deleted(vdoc, tmp_path, index):
    """Works for **every** type because a deletion removes rather than rewrites — the model never
    has to understand what it is looking at."""
    found = read_foreign_annotations(_source_page(vdoc))
    vdoc.add_annotation(0, ForeignDeletion(found[index].fingerprint))
    saved = fitz.open(_materialize(vdoc, tmp_path, f"out{index}.pdf"))
    try:
        assert len(read_foreign_annotations(saved[0])) == 2
    finally:
        saved.close()


def test_remaining_annotations_are_byte_identical(vdoc, tmp_path):
    """The zero-fidelity-risk claim, checked on the annotations' actual PDF objects: deleting one
    must not perturb the others' appearance streams or dictionaries."""
    import re

    def annot_objects(path):
        """Each surviving annotation's dictionary **and** its appearance-stream bytes.

        Indirect references (``7 0 R``) are normalised away: removing an object renumbers the ones
        after it, so a literal byte comparison of the dictionary would fail on a change that is
        purely bookkeeping. The appearance stream itself — the thing that decides how the mark
        *looks* — is compared verbatim, which is the guarantee that actually matters.
        """
        doc = fitz.open(path)
        try:
            out = {}
            for annot in doc[0].annots():
                info = annot.info or {}
                raw = doc.xref_object(annot.xref, compressed=True)
                normalised = re.sub(r"\d+ \d+ R", "<ref>", raw)
                ap = doc.xref_get_key(annot.xref, "AP/N")
                stream = b""
                if ap[0] == "xref":
                    stream = doc.xref_stream(int(ap[1].split()[0]))
                out[fingerprint(annot)] = (
                    annot.type[0], tuple(annot.rect), info.get("content", ""), normalised, stream,
                )
            return out
        finally:
            doc.close()

    untouched = annot_objects(_materialize(vdoc, tmp_path, "before.pdf"))
    target = read_foreign_annotations(_source_page(vdoc))[1]
    vdoc.add_annotation(0, ForeignDeletion(target.fingerprint))
    after = annot_objects(_materialize(vdoc, tmp_path, "after.pdf"))

    assert set(untouched) - set(after) == {target.fingerprint}
    for key, value in after.items():
        assert value == untouched[key], f"{key} was perturbed by an unrelated deletion"


def test_deleting_two_leaves_the_third(vdoc, tmp_path):
    found = read_foreign_annotations(_source_page(vdoc))
    vdoc.add_annotation(0, ForeignDeletion(found[0].fingerprint))
    vdoc.add_annotation(0, ForeignDeletion(found[2].fingerprint))
    saved = fitz.open(_materialize(vdoc, tmp_path))
    try:
        assert [a.kind_name for a in read_foreign_annotations(saved[0])] == ["Text"]
    finally:
        saved.close()


def test_identical_twins_resolve_positionally(tmp_path):
    """Two identical annotations fingerprint the same, so one deletion must remove **one** of
    them — not both, and not neither."""
    path = str(tmp_path / "twins.pdf")
    doc = fitz.open()
    page = doc.new_page()
    _add_foreign(page, "square", (10, 10, 60, 40), contents="")
    _add_foreign(page, "square", (10, 10, 60, 40), contents="")
    _clear_nm(page)          # twins are only twins without their distinct auto-assigned names
    doc.save(path)
    doc.close()

    vdoc = VirtualDocument.from_path(path)
    try:
        found = read_foreign_annotations(_source_page(vdoc))
        assert found[0].fingerprint == found[1].fingerprint     # the premise
        vdoc.add_annotation(0, ForeignDeletion(found[0].fingerprint))
        saved = fitz.open(_materialize(vdoc, tmp_path))
        try:
            assert len(read_foreign_annotations(saved[0])) == 1
        finally:
            saved.close()
    finally:
        vdoc.close()


def test_a_deletion_with_no_target_is_a_no_op(vdoc, tmp_path):
    """The page may have arrived from another document by drag/paste, or an earlier redaction may
    have consumed the annotation. Failing the save over that would be far worse."""
    vdoc.add_annotation(0, ForeignDeletion("fp:nothing-matches-this"))
    saved = fitz.open(_materialize(vdoc, tmp_path))
    try:
        assert len(read_foreign_annotations(saved[0])) == 3
    finally:
        saved.close()


def test_apply_foreign_deletions_reports_its_count(vdoc):
    doc = fitz.open()
    page = doc.new_page()
    try:
        _add_foreign(page, "square", (10, 10, 60, 40), contents="x")
        found = read_foreign_annotations(page)
        assert apply_foreign_deletions(page, (ForeignDeletion(found[0].fingerprint),)) == 1
        assert apply_foreign_deletions(page, (ForeignDeletion(found[0].fingerprint),)) == 0
    finally:
        doc.close()


def test_deletion_never_touches_the_shared_source(vdoc, tmp_path):
    """Sources are shared across windows and must stay read-only — the deletion happens on the
    materialised copy, which is what makes undo a pure model operation."""
    target = read_foreign_annotations(_source_page(vdoc))[0]
    vdoc.add_annotation(0, ForeignDeletion(target.fingerprint))
    _materialize(vdoc, tmp_path)
    assert len(read_foreign_annotations(_source_page(vdoc))) == 3


def test_deletion_rides_the_pageref_through_a_reorder(vdoc):
    target = read_foreign_annotations(_source_page(vdoc))[0]
    mark = ForeignDeletion(target.fingerprint)
    vdoc.add_annotation(0, mark)
    vdoc.move_page(0, 1)
    assert vdoc.page_annotations(1) == (mark,)


def test_deletions_are_hashable_for_undo_snapshots():
    assert len({ForeignDeletion("a"), ForeignDeletion("a"), ForeignDeletion("b")}) == 2


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
def win(app, foreign_pdf):
    w = MainWindow(app, foreign_pdf, app.settings)
    yield w
    w.undo_stack.setClean()
    w.close()


def _scene(win, x: float, y: float):
    return win.view.scene_rect_for_box(0, (x, y, x + 0.01, y + 0.01)).center()


def test_hit_test_finds_a_foreign_annotation(win):
    hit = win.view.annotations.foreign_annotation_at(_scene(win, 250, 230))
    assert hit is not None
    page_index, mark = hit
    assert (page_index, mark.kind_name) == (0, "Square")


def test_hit_test_misses_empty_space(win):
    assert win.view.annotations.foreign_annotation_at(_scene(win, 500, 700)) is None


def test_the_context_menu_offers_delete(win):
    menu = win._view_context_menu(_scene(win, 330, 210))
    labels = [a.text() for a in menu.actions()]
    assert "Delete Text by Bob" in labels
    assert "Copy Comment Text" in labels


def test_deleting_from_the_menu_is_undoable(win, tmp_path):
    hit = win.view.annotations.foreign_annotation_at(_scene(win, 250, 230))
    win._delete_foreign_annotation(*hit)
    assert len(win.view.annotations.foreign_annotations(0)) == 2   # gone from the live list
    win.undo_stack.undo()
    assert len(win.view.annotations.foreign_annotations(0)) == 3   # …and restored


def test_a_deleted_annotation_stops_being_hit_testable(win):
    hit = win.view.annotations.foreign_annotation_at(_scene(win, 250, 230))
    win._delete_foreign_annotation(*hit)
    assert win.view.annotations.foreign_annotation_at(_scene(win, 250, 230)) is None


def test_the_render_drops_a_deleted_annotation(win):
    """A foreign annotation is painted in the page's own pixmap, so unlike our marks it cannot be
    hidden by an overlay — the render copy has to lose it, or a pending delete looks like a no-op."""
    hit = win.view.annotations.foreign_annotation_at(_scene(win, 250, 230))
    page_index, mark = hit
    win._delete_foreign_annotation(page_index, mark)
    ref = win.vdoc.ordered[0]
    rendered = win.view._deleted_foreign_page(0, ref)
    assert rendered is not None
    assert len(read_foreign_annotations(rendered)) == 2


def test_pages_without_deletions_keep_the_fast_render_path(win):
    ref = win.vdoc.ordered[0]
    assert win.view._deleted_foreign_page(0, ref) is None
