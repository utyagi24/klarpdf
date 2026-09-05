"""A save that only *adds* marks appends to the file it was given (M116). Headless.

A PDF can be updated by leaving the file alone and writing the changed objects onto the end of it.
Microsoft Edge does exactly that for one highlight on a 572-page prospectus: **2,680 bytes
appended, the first 9,015,879 untouched**. Every save this project made rewrote the whole document
instead — M114 stopped it re-serialising every content stream on the way (572/572 pages now come
through byte-identical), and it still wrote 8.8 MB to add one mark.

So the write mode becomes the second fork on the axis §M110 opened. The route asks *who copied the
objects*; this asks *whether anything in the file needs to change at all*. When nothing does, the
answer is the format's own: keep the bytes, append the difference.

**The whole risk lives in the predicate, and it is not symmetric.** An append leaves the previous
revision inside the file, recoverable by anything that reads a PDF properly. That is harmless for a
highlight and a betrayal for a **redaction**, whose entire promise is that the content is gone. So
:meth:`VirtualDocument.edits_are_additive` is a whitelist — every mark kind named, every other edit
refused, unknown kinds refused by default — and most of this file is about what it says *no* to.
Being wrong the safe way costs a full rewrite, which is what yesterday's save did to everything.

**And then it has to append the right thing (M117).** The pass that draws the marks strips every
KlarPDF annotation off the page and redraws the lot, which was free while the whole file was being
rewritten and is the cost of an append: it cannot delete, so a page carrying 200 marks writes 200
fresh copies and orphans the 200 already there — ``marks already in the file × ~800 B``, on every
save, set by the document rather than by the edit. The last section is about drawing only the
difference, and about the two things that constrain it: ``/Annots`` is an *order*, and a mark left
in place is the file's own bytes rather than a fresh copy of the model's.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib

import pymupdf as fitz
import pytest

from klarpdf.model import content_marks, foreign_annots, form_fields, page_edits
from klarpdf.model.content_marks import ImageStamp, Stamp
from klarpdf.model.edit_engine import GARBAGE_APPEND, PyMuPDFEngine, append_options
from klarpdf.model.foreign_annots import ForeignDeletion, ForeignMove
from klarpdf.model.form_fields import NewField
from klarpdf.model.page_edits import (
    ADDITIVE_MARK_TYPES,
    Highlight,
    InkStroke,
    Line,
    Redaction,
    Shape,
    Strikeout,
    TextBox,
    Underline,
    is_additive_mark,
    merge_markup,
    reorder_marks,
)
from klarpdf.model.virtual_document import VirtualDocument

_MARK = Highlight(((72, 60, 200, 80),), (1.0, 0.86, 0.10))
_ELSEWHERE = Highlight(((72, 100, 200, 120),), (0.4, 0.8, 1.0))


def _save(vdoc, tmp_path, name="out.pdf") -> str:
    out = str(tmp_path / name)
    PyMuPDFEngine().materialize(vdoc, out)
    return out


def _flat(rects) -> list[float]:
    """``pytest.approx`` will not walk a tuple of tuples, and a PDF stores its numbers as 32-bit
    floats — so a round-tripped rect is compared flat and approximately."""
    return [float(v) for rect in rects for v in rect]


def _marked(path: str, page: int = 0) -> VirtualDocument:
    """``path`` opened with one highlight added to ``page`` — the milestone's own edit."""
    vdoc = VirtualDocument.from_path(path)
    vdoc.add_annotation(page, _MARK)
    return vdoc


@pytest.fixture
def plain_pdf(tmp_path) -> str:
    """Three pages of text. Nothing special — the case the append route is for."""
    path = str(tmp_path / "plain.pdf")
    doc = fitz.open()
    for i in range(3):
        doc.new_page().insert_text((72, 72), f"page {i} of the plain document", fontsize=14)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def restricted_pdf(tmp_path) -> str:
    """Encrypted with an **owner** password only: it opens freely and still restricts what you may
    do with it — the shape a published form takes, and the encrypted case an append *can* serve."""
    path = str(tmp_path / "restricted.pdf")
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "restricted", fontsize=14)
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_128, owner_pw="owner",
             permissions=int(fitz.PDF_PERM_PRINT))
    doc.close()
    return path


@pytest.fixture
def locked_pdf(tmp_path) -> str:
    """Needs a **user** password to open, so the model stores it decrypted (M32) and a save
    re-encrypts it from that copy (M54)."""
    path = str(tmp_path / "locked.pdf")
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "secret", fontsize=14)
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="open-me")
    doc.close()
    return path


@pytest.fixture
def repaired_pdf(plain_pdf, tmp_path) -> str:
    """A file whose cross-reference table MuPDF has to rebuild before it can read it."""
    path = str(tmp_path / "repaired.pdf")
    data = pathlib.Path(plain_pdf).read_bytes()
    cut = data.rfind(b"startxref")
    pathlib.Path(path).write_bytes(data[:cut] + b"startxref\n999999\n%%EOF\n")
    with fitz.open(path) as doc:
        assert doc.is_repaired, "fixture did not actually force a repair"
    return path


# ---- what the route writes ---------------------------------------------------


def test_a_save_that_only_adds_a_mark_leaves_the_whole_source_file_in_place(plain_pdf, tmp_path):
    """The milestone in one assertion: every byte of the file that was opened is still there, in
    order, at the front of the file that was written — and the mark is what follows it."""
    source = pathlib.Path(plain_pdf).read_bytes()

    out = _save(_marked(plain_pdf), tmp_path)

    written = pathlib.Path(out).read_bytes()
    assert written[: len(source)] == source
    assert len(written) > len(source)
    with fitz.open(out) as doc:
        assert len(list(doc[0].annots())) == 1
        assert [len(list(doc[i].annots())) for i in (1, 2)] == [0, 0]


def test_the_pages_nobody_marked_are_untouched_by_construction(plain_pdf, tmp_path):
    """Not "the content streams happen to match" (M114's result on the rewrite route) but that the
    bytes were never rewritten at all — pages 2 and 3 are the same region of the same file."""
    out = _save(_marked(plain_pdf), tmp_path)

    with fitz.open(plain_pdf) as before, fitz.open(out) as after:
        assert after.page_count == before.page_count
        for i in range(before.page_count):
            assert after[i].read_contents() == before[i].read_contents()


def test_a_save_with_nothing_to_write_returns_the_file_it_was_given(plain_pdf, tmp_path):
    """Saving an unedited document is now a copy, byte for byte. MuPDF appends only what it
    considers dirty, and a document nobody edited has nothing — measured, **+0 bytes**."""
    out = _save(VirtualDocument.from_path(plain_pdf), tmp_path)

    assert pathlib.Path(out).read_bytes() == pathlib.Path(plain_pdf).read_bytes()


def test_an_appended_mark_reopens_as_an_editable_mark(plain_pdf, tmp_path):
    """The M31 round trip has to survive the new route: a mark written by appending is read back
    off the saved file as the same descriptor, not as somebody else's annotation. (The colour comes
    back through PDF's 32-bit floats, so it is compared as a number rather than as bytes.)"""
    out = _save(_marked(plain_pdf), tmp_path)

    (read_back,) = VirtualDocument.from_path(out).ordered[0].annotations
    assert isinstance(read_back, Highlight)
    assert _flat(read_back.rects) == pytest.approx(_flat(_MARK.rects))
    assert read_back.color == pytest.approx(_MARK.color, abs=1e-6)


def test_saving_twice_from_one_model_does_not_stack_revisions(plain_pdf, tmp_path):
    """The append is against the *origin's* bytes, not against the last thing written, so a second
    save of the same edits writes the same one revision rather than piling one on another. (The two
    files are not byte-identical — an annotation carries a modification date and MuPDF writes a
    fresh trailer ``/ID`` — but nothing may grow, and no third ``%%EOF`` may appear.)"""
    vdoc = _marked(plain_pdf)
    first = pathlib.Path(_save(vdoc, tmp_path, "first.pdf")).read_bytes()
    second = pathlib.Path(_save(vdoc, tmp_path, "second.pdf")).read_bytes()

    assert len(first) == len(second)
    assert first.count(b"%%EOF") == second.count(b"%%EOF") == 2


def test_a_second_engine_reads_the_appended_file(plain_pdf, tmp_path):
    """An incremental update is ordinary PDF, so the check is that it really is one: pypdf parses
    the appended file, finds every page, and sees the mark."""
    pypdf = pytest.importorskip("pypdf", reason="the second engine is a dev-only cross-check")

    out = _save(_marked(plain_pdf), tmp_path)

    reader = pypdf.PdfReader(out)
    assert len(reader.pages) == 3
    assert len(reader.pages[0].get("/Annots", [])) == 1


def test_the_append_writes_with_the_keywords_it_names(plain_pdf, tmp_path):
    """``append_options`` is what ``materialize`` writes with, not a second opinion about it — the
    same promise ``save_options`` carries for the rewrite routes (M111)."""
    out_path = str(tmp_path / "out.pdf")
    calls = []
    real_save = fitz.Document.save

    def spy(self, path, **kwargs):
        if path == out_path:
            calls.append(kwargs)
        return real_save(self, path, **kwargs)

    fitz.Document.save = spy
    try:
        PyMuPDFEngine().materialize(_marked(plain_pdf), out_path)
    finally:
        fitz.Document.save = real_save

    assert len(calls) == 1
    assert calls[0] == append_options()
    assert calls[0]["incremental"] is True
    assert calls[0]["garbage"] == GARBAGE_APPEND
    assert calls[0]["encryption"] == fitz.PDF_ENCRYPT_KEEP


# ---- the whitelist -----------------------------------------------------------


def test_a_redaction_is_never_appended(plain_pdf, tmp_path):
    """The reason the predicate is a whitelist. An append leaves the previous revision in the file,
    so appending over a redaction would leave the removed content sitting in it — and the write
    also runs below the ``garbage=1`` orphan floor ``test_redaction_orphans.py`` pins, which is a
    second, independent reason. The output must therefore be a rewrite, and the proof is that the
    source's bytes are *not* what it starts with."""
    assert GARBAGE_APPEND < 1  # below the floor: the exclusion is not a nicety
    vdoc = VirtualDocument.from_path(plain_pdf)
    vdoc.add_annotation(0, Redaction(((72, 60, 200, 80),)))

    assert not PyMuPDFEngine().appends(vdoc)

    out = _save(vdoc, tmp_path)
    source = pathlib.Path(plain_pdf).read_bytes()
    assert pathlib.Path(out).read_bytes()[: len(source)] != source


@pytest.mark.parametrize(
    "name, edit",
    [
        ("rotation", lambda v: v.set_rotation(0, 90)),
        ("crop", lambda v: v.set_crop([0], (10, 10, 200, 200))),
        ("form fill", lambda v: v.set_field_value("name", "typed")),
        ("metadata edited", lambda v: v.set_metadata_override({"title": "new"})),
        ("metadata removed", lambda v: v.set_metadata_override({})),
        ("encryption staged", lambda v: v.set_encryption("secret")),
        ("password removed", lambda v: v.set_encryption(None)),
        ("page deleted", lambda v: v.delete_page(1)),
        ("pages reordered", lambda v: v.move_page(0, 2)),
        ("page duplicated", lambda v: v.append_pages([v.ordered[0]])),
        ("redaction", lambda v: v.add_annotation(0, Redaction(((72, 60, 200, 80),)))),
        ("stamp", lambda v: v.add_annotation(0, Stamp((72, 60, 200, 80), "DRAFT"))),
        ("image stamp", lambda v: v.add_annotation(0, ImageStamp((72, 60, 200, 80), b"png"))),
        ("foreign deletion", lambda v: v.add_annotation(0, ForeignDeletion("fp:x", "Highlight"))),
        ("foreign move", lambda v: v.add_annotation(0, ForeignMove("fp:x", 3.0, 4.0))),
        ("new form field", lambda v: v.add_annotation(0, NewField((72, 60, 200, 80), "extra"))),
    ],
)
def test_an_edit_that_is_not_an_added_mark_is_refused(plain_pdf, name, edit):
    """One row per way of changing a document that is **not** adding a mark to it. Each rewrites
    something the file already had — a page's rotation, its crop, a field's value, the metadata
    stores, the encryption, the page set itself, or an annotation somebody else put there — and an
    append can only add. The stamp pair bakes into the page's content stream, which is a rewrite of
    the page however it is spelled."""
    vdoc = VirtualDocument.from_path(plain_pdf)
    assert PyMuPDFEngine().appends(vdoc), "the fixture itself must be appendable"

    edit(vdoc)

    assert not PyMuPDFEngine().appends(vdoc), f"{name} was let through"


def test_an_unrecognised_mark_kind_is_refused(plain_pdf):
    """The default. A descriptor this module has never heard of is not additive, so a kind added to
    the model tomorrow is safe on the day it lands rather than on the day somebody remembers to
    come back here."""

    class SomethingNew:
        pass

    assert not is_additive_mark(SomethingNew())
    vdoc = VirtualDocument.from_path(plain_pdf)
    vdoc.ordered[0] = vdoc.ordered[0].with_annotations((SomethingNew(),))

    assert not PyMuPDFEngine().appends(vdoc)


def test_every_mark_the_model_can_hold_is_classified():
    """The roster, so a new descriptor cannot arrive unclassified. Failing here means a dataclass
    was added to one of the mark modules: decide whether it only *adds* something to a page, then
    write the answer down — in :data:`ADDITIVE_MARK_TYPES` and here.
    """
    additive_by_name = {
        # the R4 markup kit — each adds one PDF annotation to a page and touches nothing else
        "Highlight": True, "Underline": True, "Strikeout": True,
        "InkStroke": True, "Line": True, "Shape": True, "TextBox": True,
        # destructive, or a rewrite of something the page already had
        "Redaction": False, "Stamp": False, "ImageStamp": False,
        "ForeignDeletion": False, "ForeignMove": False, "NewField": False,
        # read results, never stored on a PageRef — no classification to make
        "FormField": None, "ForeignAnnot": None,
    }
    # `__module__` rather than `hasattr`, so a name re-exported into a second module is counted
    # once, where it is defined.
    found = {
        name: obj
        for module in (page_edits, content_marks, foreign_annots, form_fields)
        for name, obj in vars(module).items()
        if dataclasses.is_dataclass(obj) and getattr(obj, "__module__", "") == module.__name__
    }

    assert set(found) == set(additive_by_name), "a mark module gained a dataclass; classify it above"
    for name, additive in additive_by_name.items():
        if additive is None:
            continue
        assert (found[name] in ADDITIVE_MARK_TYPES) is additive, name


@pytest.mark.parametrize(
    "mark",
    [
        Highlight(((10, 10, 20, 20),)),
        Underline(((10, 10, 20, 20),)),
        Strikeout(((10, 10, 20, 20),)),
        InkStroke((((10.0, 10.0), (20.0, 20.0)),)),
        Line((10.0, 10.0), (20.0, 20.0)),
        Shape("rect", (10, 10, 20, 20)),
        TextBox((10, 10, 20, 20), "note"),
    ],
)
def test_each_markup_kind_can_be_appended(plain_pdf, tmp_path, mark):
    """The other half of the whitelist: every kind on it really does append, and the file it was
    given survives underneath. A whitelist nobody checks the *yes* side of is a list of refusals."""
    vdoc = VirtualDocument.from_path(plain_pdf)
    vdoc.add_annotation(0, mark)
    assert PyMuPDFEngine().appends(vdoc)

    out = _save(vdoc, tmp_path)

    source = pathlib.Path(plain_pdf).read_bytes()
    assert pathlib.Path(out).read_bytes()[: len(source)] == source
    with fitz.open(out) as doc:
        assert len(list(doc[0].annots())) == 1


# ---- nothing may be taken away ----------------------------------------------


@pytest.fixture
def already_marked_pdf(plain_pdf, tmp_path) -> str:
    """A document saved once with two of our marks on it — the state a markup session reopens."""
    vdoc = VirtualDocument.from_path(plain_pdf)
    vdoc.add_annotation(0, _MARK)
    vdoc.add_annotation(1, _ELSEWHERE)
    return _save(vdoc, tmp_path, "already-marked.pdf")


def test_adding_to_a_document_that_already_carries_our_marks_still_appends(already_marked_pdf,
                                                                          tmp_path):
    """The commonest markup session: a document annotated last week, opened again, given one more
    highlight. Nothing is taken away, so the append stands — refusing this case would send that
    session back to the full rewrite every time after the first."""
    vdoc = VirtualDocument.from_path(already_marked_pdf)
    assert vdoc.has_baked_klarpdf_annotations()
    vdoc.add_annotation(2, _ELSEWHERE)

    assert PyMuPDFEngine().appends(vdoc)

    out = _save(vdoc, tmp_path)
    source = pathlib.Path(already_marked_pdf).read_bytes()
    assert pathlib.Path(out).read_bytes()[: len(source)] == source
    (read_back,) = VirtualDocument.from_path(out).ordered[2].annotations
    assert _flat(read_back.rects) == pytest.approx(_flat(_ELSEWHERE.rects))


def test_removing_a_mark_the_file_arrived_with_is_not_an_append(already_marked_pdf):
    """Appending cannot take something away. The previous revision stays in the file, so the
    "deleted" mark would still be in there — a text box the user emptied and saved would still
    carry its old wording. Removing anything is a rewrite."""
    vdoc = VirtualDocument.from_path(already_marked_pdf)
    vdoc.clear_annotations(0)

    assert not PyMuPDFEngine().appends(vdoc)


def test_editing_a_mark_the_file_arrived_with_is_not_an_append(already_marked_pdf):
    """Editing is removing, by the same argument — the old descriptor is gone from the model and
    its bytes would not be gone from the file."""
    vdoc = VirtualDocument.from_path(already_marked_pdf)
    (as_read,) = vdoc.ordered[0].annotations
    vdoc.replace_annotation(0, as_read, Highlight(as_read.rects, (0.1, 0.9, 0.1)))
    assert vdoc.ordered[0].annotations != (as_read,), "the fixture did not actually edit anything"

    assert not PyMuPDFEngine().appends(vdoc)


def test_merging_a_new_mark_into_an_existing_one_is_not_an_append(already_marked_pdf):
    """The case the bridge's ``annotate`` reaches (M101): overlapping markup is *merged*, and the
    survivor replaces the marks it absorbed. That is a removal however friendly it looks, and it is
    refused without the predicate knowing anything about merging."""
    vdoc = VirtualDocument.from_path(already_marked_pdf)
    arrived = vdoc.page_annotations(0)
    merged = merge_markup(arrived, arrived[0].rects, Highlight, (0.1, 0.9, 0.1))
    assert merged != arrived, "the fixture did not actually merge anything"
    vdoc.set_annotations(0, merged)

    assert not PyMuPDFEngine().appends(vdoc)


# ---- what the file itself has to allow ---------------------------------------


def test_an_owner_password_document_keeps_its_encryption_through_an_append(restricted_pdf,
                                                                          tmp_path):
    """The encrypted case an append serves. Such a document opens without a password and is never
    decrypted, so the bytes on disk *are* the model's source: the append rides on top of them and
    ``PDF_ENCRYPT_KEEP`` leaves the encryption dictionary exactly as it found it — permissions
    included, which is what M93 was about."""
    vdoc = _marked(restricted_pdf)
    assert PyMuPDFEngine().appends(vdoc)

    out = _save(vdoc, tmp_path)

    with fitz.open(restricted_pdf) as before, fitz.open(out) as after:
        assert after.metadata["encryption"] == before.metadata["encryption"]
        assert after.permissions == before.permissions
        assert len(list(after[0].annots())) == 1


def test_a_document_that_needed_a_password_is_refused(locked_pdf, tmp_path):
    """Two reasons, both structural. Such a source is stored *decrypted*, so the file's bytes and
    the model's document are not the same thing; and the save re-encrypts from that copy (M54),
    which MuPDF refuses to do incrementally — an append may not change encryption at all."""
    vdoc = VirtualDocument.from_path(locked_pdf, password_provider=lambda *_: "open-me")
    vdoc.add_annotation(0, _MARK)

    assert vdoc.origin_bytes() is None
    assert not PyMuPDFEngine().appends(vdoc)

    out = _save(vdoc, tmp_path)
    with fitz.open(out) as doc:
        assert doc.needs_pass


def test_a_repaired_file_is_refused(repaired_pdf, tmp_path):
    """MuPDF will not append to a file whose cross-reference table it had to rebuild — the offsets
    an incremental update chains onto are the ones it just had to guess. The full rewrite is also
    what repairs the file, so this is the right answer twice over."""
    vdoc = _marked(repaired_pdf)

    assert vdoc.origin_needed_repair()
    assert not PyMuPDFEngine().appends(vdoc)

    out = _save(vdoc, tmp_path)
    with fitz.open(out) as doc:
        assert not doc.is_repaired
        assert len(list(doc[0].annots())) == 1


def test_a_subset_view_is_refused(plain_pdf, tmp_path):
    """``subset`` shares the sources but not the per-page baseline, so there is nothing to prove an
    additive claim against — and an extract is a new document anyway, not an update to one."""
    vdoc = VirtualDocument.from_path(plain_pdf)

    assert not PyMuPDFEngine().appends(vdoc.subset([0, 1, 2]))


def test_a_page_from_a_second_document_is_refused(plain_pdf, b_pdf, tmp_path):
    """The page set changed, so there is no file to append *to*: the output has to be assembled."""
    vdoc = VirtualDocument.from_path(plain_pdf)
    other = VirtualDocument.from_path(b_pdf)
    vdoc.import_pages(1, other, [0])

    assert not PyMuPDFEngine().appends(vdoc)


def test_the_seed_is_the_file_that_was_opened_not_the_file_that_is_there_now(plain_pdf, tmp_path):
    """The bytes are captured at open, not re-read at save. A document that changed underneath us
    must not have this session's marks appended to *its* pages — the user would be saving a file
    they have never seen. (The app asks before overwriting an externally changed file; this is the
    layer below that, where the answer cannot depend on a prompt.)"""
    vdoc = _marked(plain_pdf)
    opened_as = pathlib.Path(plain_pdf).read_bytes()

    replacement = fitz.open()
    replacement.new_page().insert_text((72, 72), "somebody else's document", fontsize=14)
    replacement.save(plain_pdf)
    replacement.close()
    assert pathlib.Path(plain_pdf).read_bytes() != opened_as

    out = _save(vdoc, tmp_path)

    assert pathlib.Path(out).read_bytes()[: len(opened_as)] == opened_as
    with fitz.open(out) as doc:
        assert doc.page_count == 3
        assert "somebody else" not in doc[0].get_text()


# ---- and it writes the mark you added, not the ones already there (M117) ------
#
# The route above appends, and then draws every mark on the page into that append. While a save
# rewrote the whole file the strip-and-redraw cost nothing; an append cannot delete, so redrawing
# 200 marks writes 200 fresh copies onto the end of the file and orphans the 200 already in it —
# `marks already in the file × ~800 B`, paid on every save, set by the document rather than by the
# edit. Six later sittings of one highlight each took a 30-page document from 239,692 to 933,069 B;
# they now take it to 246,527.
#
# `edits_are_additive` has already proved the model's marks are a superset of the file's, so the
# file's own copies can stay where they are and only the difference needs drawing. What that costs
# is the two constraints below: the marks left alone are in the file's `/Annots` order, so the model
# has to still *start* with them (else Bring to Front is a silent no-op), and a mark not redrawn is
# the file's own bytes rather than a fresh copy of the model's.


def _bar(n: int) -> Highlight:
    """A highlight on its own line, so a page can carry many without any of them merging."""
    y = 60.0 + (n % 40) * 8
    return Highlight(((72.0, y, 200.0, y + 6.0),), (1.0, 0.86, 0.10))


def _annot_objects(path: str, page: int = 0) -> dict:
    """``{xref: the annotation's raw object plus its appearance stream}`` for one page — what
    "the file's own mark, untouched" means when spelled out in bytes."""
    with fitz.open(path) as doc:
        out = {}
        for annot in doc[page].annots():
            ap = doc.xref_get_key(annot.xref, "AP/N")
            stream = doc.xref_stream(int(ap[1].split()[0])) if ap[0] == "xref" else None
            out[annot.xref] = (doc.xref_object(annot.xref), stream)
        return out


def _marked_n(src: str, count: int, tmp_path, name: str) -> str:
    """``src`` saved once carrying ``count`` of our marks on page 0 — a heavy first sitting."""
    vdoc = VirtualDocument.from_path(src)
    for i in range(count):
        vdoc.add_annotation(0, _bar(i))
    return _save(vdoc, tmp_path, name)


def test_what_a_second_sitting_costs_is_set_by_the_edit_not_by_the_document(plain_pdf, tmp_path):
    """The milestone. Adding one highlight costs the same whether the file already carries one mark
    or fifty — measured **+911 B against +920 B**, where the strip-and-redraw charged ~800 B for
    every mark already in the file (+8,221 B on a 9-mark document, +114,269 B on a 200-mark one).

    Asserted as a ratio rather than a byte count so it pins the shape of the cost — flat in the size
    of the document — instead of one build of one fixture."""
    deltas = []
    for count in (1, 50):
        base = _marked_n(plain_pdf, count, tmp_path, f"base{count}.pdf")
        vdoc = VirtualDocument.from_path(base)
        vdoc.add_annotation(1, _ELSEWHERE)
        assert PyMuPDFEngine().appends(vdoc)

        out = _save(vdoc, tmp_path, f"plus{count}.pdf")
        deltas.append(os.path.getsize(out) - os.path.getsize(base))

    one, fifty = deltas
    assert fifty < one * 1.5, f"the 50-mark document paid {fifty} B against the 1-mark {one} B"


def test_the_marks_the_file_arrived_with_are_the_same_objects_afterwards(plain_pdf, tmp_path):
    """Not "equivalent" — *the same objects*. Every mark the page arrived with keeps its xref
    number, its raw object and its appearance stream byte for byte, because nothing rewrote them:
    they are still the bytes at the front of the file, and the append only added the new one."""
    base = _marked_n(plain_pdf, 5, tmp_path, "five.pdf")
    before = _annot_objects(base)
    vdoc = VirtualDocument.from_path(base)
    vdoc.add_annotation(0, _ELSEWHERE)

    out = _save(vdoc, tmp_path, "six.pdf")

    after = _annot_objects(out)
    assert set(before) <= set(after), "an arrived mark was renumbered"
    assert {x: after[x] for x in before} == before
    assert len(after) == len(before) + 1


@pytest.mark.parametrize(
    "mark",
    [
        Highlight(((72, 60, 200, 80),)),
        Underline(((72, 100, 200, 118),)),
        Strikeout(((72, 140, 200, 158),)),
        InkStroke((((72.0, 200.0), (120.0, 240.0), (160.0, 200.0)),), opacity=0.5, dashed=True),
        Line((72.0, 300.0), (300.0, 340.0), arrow_end=True, dashed=True),
        Shape("rect", (72, 380, 300, 440), fill_color=(0.2, 0.4, 0.9), opacity=0.4),
        Shape("ellipse", (72, 460, 300, 520)),
        TextBox((320, 60, 500, 100), "hello there", fill_color=(1.0, 1.0, 0.6), border_width=1.5),
    ],
)
def test_a_mark_left_in_place_is_indistinguishable_from_a_redrawn_one(plain_pdf, tmp_path, mark):
    """Per kind, because "almost certainly invisible" is not a thing to say about a save path. The
    mark goes into the file, a *second* mark is then appended to another page, and the first one is
    checked from three sides: the bytes of its object and appearance stream, the descriptor the
    model reads back off it, and the pixels the page renders to.

    The render is compared against ``render_output`` — the full-rewrite build, which is what the
    viewer and the print path draw — so this is literally "does it still look the same on screen".
    The two shapes are at the **default** 2.0 stroke width, which is also the only width at which
    the redraw is faithful: PyMuPDF grows a Square/Circle ``/Rect`` by exactly 1 point per side
    whatever the border, while ``parse_annotation`` insets by ``width / 2``, so a redrawn shape of
    any other width creeps by the difference on every save (issue #292, found by this comparison
    over the corpus). That defect belongs to the redraw, not to this route — a mark left in place
    cannot creep, because nothing rewrites it."""
    base = VirtualDocument.from_path(plain_pdf)
    base.add_annotation(0, mark)
    marked = _save(base, tmp_path, "marked.pdf")
    before = _annot_objects(marked)

    vdoc = VirtualDocument.from_path(marked)
    (as_read,) = vdoc.ordered[0].annotations
    vdoc.add_annotation(2, _ELSEWHERE)
    assert PyMuPDFEngine().appends(vdoc)

    out = _save(vdoc, tmp_path, "out.pdf")

    assert _annot_objects(out) == before
    (read_back,) = VirtualDocument.from_path(out).ordered[0].annotations
    assert read_back == as_read
    redrawn = PyMuPDFEngine().render_output(vdoc)
    try:
        with fitz.open(out) as doc:
            assert doc[0].get_pixmap(dpi=72).samples == redrawn[0].get_pixmap(dpi=72).samples
    finally:
        redrawn.close()


def test_a_second_engine_sees_the_marks_that_were_left_alone(plain_pdf, tmp_path):
    """The marks nobody rewrote are still *reachable*, not merely still present: pypdf walks the
    page's ``/Annots`` in the appended revision and finds all six."""
    pypdf = pytest.importorskip("pypdf", reason="the second engine is a dev-only cross-check")
    base = _marked_n(plain_pdf, 5, tmp_path, "five.pdf")
    vdoc = VirtualDocument.from_path(base)
    vdoc.add_annotation(0, _ELSEWHERE)

    out = _save(vdoc, tmp_path, "six.pdf")

    assert len(pypdf.PdfReader(out).pages[0].get("/Annots", [])) == 6


def test_bringing_a_mark_to_the_front_still_writes_the_new_order(plain_pdf, tmp_path):
    """The constraint that stops this being a two-line diff. A z-order change leaves the multiset
    identical, so the save still appends — and answering with the plain set difference would draw
    *nothing*, reporting success over a file where nothing moved. The page falls back to the full
    strip-and-redraw instead, and the reopened file reads back in the model's order."""
    base = _marked_n(plain_pdf, 3, tmp_path, "three.pdf")
    vdoc = VirtualDocument.from_path(base)
    arrived = vdoc.page_annotations(0)
    vdoc.set_annotations(0, reorder_marks(arrived, [arrived[0]], "front"))
    assert vdoc.page_annotations(0) != arrived, "the fixture did not actually reorder anything"
    assert PyMuPDFEngine().appends(vdoc), "a z-order change is still additive"
    assert vdoc.marks_to_append(0) is None, "the page must not keep its own order"

    out = _save(vdoc, tmp_path, "reordered.pdf")

    assert VirtualDocument.from_path(out).ordered[0].annotations == vdoc.page_annotations(0)
    source = pathlib.Path(base).read_bytes()
    assert pathlib.Path(out).read_bytes()[: len(source)] == source, "and it is still an append"


def test_the_fallback_is_per_page(plain_pdf, tmp_path):
    """A page that reordered pays; the pages beside it do not. The two are independent because the
    baseline is per page, which is what keeps one Bring to Front from re-writing a whole document's
    marks."""
    vdoc = VirtualDocument.from_path(plain_pdf)
    for page in (0, 1):
        for i in range(3):
            vdoc.add_annotation(page, _bar(i))
    base = _save(vdoc, tmp_path, "both.pdf")
    before = {page: _annot_objects(base, page) for page in (0, 1)}

    vdoc = VirtualDocument.from_path(base)
    arrived = vdoc.page_annotations(0)
    vdoc.set_annotations(0, reorder_marks(arrived, [arrived[0]], "front"))
    vdoc.add_annotation(1, _ELSEWHERE)

    out = _save(vdoc, tmp_path, "mixed.pdf")

    # Asserted on the objects rather than on their xref numbers: MuPDF is free to hand a deleted
    # object's number back out, so "these are different xrefs" would be a claim about its free list.
    assert _annot_objects(out, 0) != before[0], "page 0 was not redrawn"
    assert VirtualDocument.from_path(out).ordered[0].annotations == vdoc.page_annotations(0)
    kept = _annot_objects(out, 1)
    assert {x: kept[x] for x in before[1]} == before[1], "page 1 was rewritten anyway"


def test_a_foreign_annotation_keeps_its_place(plain_pdf, tmp_path):
    """A consequence worth having on purpose. The strip-and-redraw re-lays *our* marks on the end
    of ``/Annots``, which pushes anything the file already had underneath them: measured on a page
    carrying one of ours and one of somebody else's, a save that only added a highlight moved the
    foreign annotation from last to **first**. Nothing asked for that, and the model has no opinion
    about where a foreign mark sits, so leaving it alone is the honest answer."""
    marked = _save(_marked(plain_pdf), tmp_path, "marked.pdf")
    with fitz.open(marked) as doc:
        annot = doc[0].add_rect_annot(fitz.Rect(300, 300, 400, 400))
        annot.set_info(title="somebody else")
        annot.update()
        doc.save(marked, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)

    vdoc = VirtualDocument.from_path(marked)
    vdoc.add_annotation(0, _ELSEWHERE)
    out = _save(vdoc, tmp_path, "out.pdf")

    with fitz.open(out) as doc:
        assert [a.info.get("title") for a in doc[0].annots()] == [
            "klarpdf", "somebody else", "klarpdf",
        ]


def test_the_baseline_only_speaks_for_the_origins_own_page(plain_pdf, b_pdf, tmp_path):
    """``marks_to_append`` answers ``None`` for a page it cannot vouch for rather than trusting its
    caller to have checked. The append route can never hand it one — its predicate demands an
    unchanged page set — but a method that is only correct because of what somebody else asked
    first is a method that stops being correct when somebody else stops asking."""
    vdoc = VirtualDocument.from_path(plain_pdf)
    assert vdoc.marks_to_append(0) == ()

    vdoc.add_annotation(0, _MARK)
    assert vdoc.marks_to_append(0) == (_MARK,)

    other = VirtualDocument.from_path(b_pdf)
    vdoc.import_pages(0, other, [0])
    assert vdoc.marks_to_append(0) is None, "a page from a second document"
    assert vdoc.marks_to_append(2) is None, "the origin's own page, at the wrong index"


# ---- the app's own Save, end to end ------------------------------------------
#
# The model tests above drive `materialize` directly. This one drives the window, because the
# append route changed what `MainWindow._write_to` hands it: the temp `mkstemp` created is no
# longer written from scratch but *filled with the origin's bytes and then appended to*, and it
# still has to survive the atomic rename that follows. Qt is imported inside the fixtures so the
# rest of this file stays importable without it.


@pytest.fixture(scope="session")
def qapp():
    from app import PdfApp

    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    from store.settings import Settings

    qapp.settings = Settings(tmp_path / "view_state.json")
    qapp.page_clipboard = []
    return qapp


def test_the_apps_own_save_appends_in_place(app, plain_pdf):
    """Save over the open document, and the file on disk is the file that was opened plus the
    mark — through `mkstemp` → seed → append → `atomic_replace`, in place, with the window's
    dirty state and its file watcher both settling as they always did."""
    from main_window import MainWindow

    opened_as = pathlib.Path(plain_pdf).read_bytes()
    win = MainWindow(app, plain_pdf, app.settings)
    win.vdoc.add_annotation(0, _MARK)
    win.vdoc.dirty = True

    assert win.save() is True

    written = pathlib.Path(plain_pdf).read_bytes()
    assert written[: len(opened_as)] == opened_as
    assert len(written) > len(opened_as)
    assert not win.vdoc.dirty
    with fitz.open(plain_pdf) as doc:
        assert len(list(doc[0].annots())) == 1
