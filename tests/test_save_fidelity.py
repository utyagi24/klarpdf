"""What a save keeps when it has no reason to throw it away. Headless.

A PDF holds a lot at the *document* level rather than on a page: the accessibility structure tree
and ``/MarkInfo``, Reader Extensions ``/Perms``, the ``/Names`` tree, encryption. ``insert_pdf``
copies **pages**, so a save that assembled the output by grafting pages into an empty document
silently dropped every one of them — and the drop was invisible, because the pages themselves came
through perfectly.

Found by filling a federal form (TC-002, 2026-08-13): a tagged, AES-encrypted SSA-3 came back
untagged, unencrypted, with every permission granted, and with both of its hyperlinks rewritten
into ``/Launch`` actions naming local files that do not exist. The values were correct, the pages
were correct, and everything a screen reader or a security policy relies on was gone.

The pattern was already in the engine: the outline and internal links are rebuilt (M33) and the
metadata stores carried across (M53), each pass added after a save was caught dropping something.
The structure tree is the one that cannot have such a pass written — it references page content, so
it can only be kept, never reconstructed. Hence the split these tests pin: an **unchanged page set**
edits a copy of the origin, and anything structural still rebuilds.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from klarpdf.model.edit_engine import (
    CLEAN_COPIED,
    CLEAN_REWRITTEN,
    GARBAGE_COPY,
    GARBAGE_GRAFT,
    PyMuPDFEngine,
)
from klarpdf.model.virtual_document import VirtualDocument

_PERMS = int(fitz.PDF_PERM_PRINT | fitz.PDF_PERM_ACCESSIBILITY)


def _materialize(vdoc, tmp_path, name="out.pdf") -> str:
    out = str(tmp_path / name)
    PyMuPDFEngine().materialize(vdoc, out)
    return out


def _catalog(path) -> dict:
    with fitz.open(path) as doc:
        cat = doc.pdf_catalog()
        return {k: doc.xref_get_key(cat, k)[0]
                for k in ("MarkInfo", "StructTreeRoot", "Perms", "Names")}


@pytest.fixture
def tagged_pdf(tmp_path) -> str:
    """Three pages carrying the document-level furniture a graft cannot see."""
    path = str(tmp_path / "tagged.pdf")
    doc = fitz.open()
    for i in range(3):
        doc.new_page().insert_text((72, 72), f"page {i}", fontsize=20)
    struct = doc.get_new_xref()
    doc.update_object(struct, "<< /Type /StructTreeRoot >>")
    doc.xref_set_key(doc.pdf_catalog(), "StructTreeRoot", f"{struct} 0 R")
    doc.xref_set_key(doc.pdf_catalog(), "MarkInfo", "<< /Marked true >>")
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def photo_pdf(tmp_path) -> str:
    """One page that is almost entirely a photograph — a gradient, which is what compresses to a
    large stream rather than to nothing. The shape that makes stream-level deduplication matter."""
    path = str(tmp_path / "photo.pdf")
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 600, 400))
    for x in range(600):
        for y in range(400):
            pix.set_pixel(x, y, (x % 256, (x * y) % 256, y % 256))
    doc = fitz.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=pix.tobytes("png"))
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def restricted_pdf(tmp_path) -> str:
    """Encrypted with an **owner** password only: it opens with no password at all, but says what
    you may do with it. The shape a published form takes, and the one M54 never covered — there is
    no password to record at open, so there was nothing to re-encrypt from at save."""
    path = str(tmp_path / "restricted.pdf")
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "restricted", fontsize=20)
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_128, owner_pw="owner", permissions=_PERMS)
    doc.close()
    return path


@pytest.fixture
def weblink_pdf(tmp_path) -> str:
    """A link to a **scheme-less** web address — `www.ssa.gov/privacy`, no `http://`.

    PyMuPDF reads that back as a *file* link (no scheme, so it guesses a path), which is how a
    round-trip through the graft turned both of the SSA-3's hyperlinks into `/Launch` actions
    pointing at local files that do not exist. Dead, and `/Launch` is the action type viewers warn
    about — so the round-trip made them worse than dead.
    """
    path = str(tmp_path / "weblink.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "privacy", fontsize=20)
    page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(72, 60, 200, 80),
                      "uri": "www.ssa.gov/privacy"})
    doc.save(path)
    doc.close()
    return path


# ---- which route a save takes ---------------------------------------------------


def test_an_untouched_document_keeps_its_page_set(tagged_pdf):
    assert VirtualDocument.from_path(tagged_pdf).page_set_unchanged() is True


@pytest.mark.parametrize("mutate, why", [
    (lambda v: v.delete_page(1), "a deleted page"),
    (lambda v: setattr(v, "ordered", list(reversed(v.ordered))), "a reorder"),
    (lambda v: setattr(v, "ordered", v.ordered + [v.ordered[0]]), "a duplicated page"),
])
def test_a_structural_edit_gives_up_the_page_set(tagged_pdf, mutate, why):
    """The predicate has to be conservative: anything that moves a page means the output is a new
    document and the graft is the only way to build it."""
    v = VirtualDocument.from_path(tagged_pdf)
    mutate(v)
    assert v.page_set_unchanged() is False, why


def test_a_page_edit_does_not_count_as_structural(tagged_pdf):
    """Rotation, crops, annotations and fills apply to a page wherever it lives, so they must not
    push the save onto the rebuilding route and cost the document its structure."""
    v = VirtualDocument.from_path(tagged_pdf)
    v.ordered = [v.ordered[0].with_rotation(90), *v.ordered[1:]]
    assert v.page_set_unchanged() is True


# ---- what the route costs (M110) -------------------------------------------------


def test_the_cleanup_level_follows_the_route(tagged_pdf):
    """The expensive cleanup runs exactly when *we* did the copying.

    A Save was not asked to optimise the user's file, so a copy of the origin is written as packed
    as it arrived; the graft cleans up after its own page-copying. The cost of the other choice is
    the milestone: the duplicate hunt is quadratic in object count, and on a 48,877-object document
    it took 289 s to find almost nothing.
    """
    v = VirtualDocument.from_path(tagged_pdf)
    assert PyMuPDFEngine().save_options(v)["garbage"] == GARBAGE_COPY

    v.ordered = v.ordered + [v.ordered[0]]  # a duplicated page — now a graft
    assert PyMuPDFEngine().save_options(v)["garbage"] == GARBAGE_GRAFT


def test_neither_route_goes_below_the_orphan_floor():
    """Level 1 drops unreferenced objects, and that is what deletes an image a redaction detaches
    from its page. Below it the picture stays in the file, recoverable by anything that walks
    objects rather than pages — see ``tests/test_redaction_orphans.py``, which proves it."""
    assert GARBAGE_COPY >= 1
    assert GARBAGE_GRAFT >= 1


def test_only_the_two_decided_options_ever_vary(tagged_pdf):
    """``garbage`` and ``clean`` are decided per write; **everything else** is one fixed set.

    This used to say only the cleanup level ever varies (M111, when the rest were four copies of a
    literal and ``use_objstms`` reached exactly one of them). M114 adds a second decision — but the
    point of the original test survives it: the *number* of varying options is small, named and
    deliberate, and nothing else may quietly join them.
    """
    v = VirtualDocument.from_path(tagged_pdf)
    copy_route = PyMuPDFEngine().save_options(v)
    v.ordered = v.ordered + [v.ordered[0]]
    graft_route = PyMuPDFEngine().save_options(v)

    decided = {"garbage", "clean"}
    assert copy_route["use_objstms"] == 1 and graft_route["use_objstms"] == 1
    assert {k: val for k, val in copy_route.items() if k not in decided} == \
           {k: val for k, val in graft_route.items() if k not in decided}


def test_a_plain_save_does_not_sanitise_the_content_streams(tagged_pdf):
    """A save that only copies pages through leaves their content exactly as it arrived (M114).

    Measured over the 56-document corpus, cleaning there is a straight loss: streams left
    byte-identical to the source go from 324/1,315 pages to 1,315/1,315 without it, the corpus
    saves 70% faster, and three documents stop having their text re-ordered by a second extraction
    engine.
    """
    v = VirtualDocument.from_path(tagged_pdf)
    assert PyMuPDFEngine().save_options(v)["clean"] is CLEAN_COPIED


def test_a_save_that_rewrites_page_content_still_cleans_up_after_itself(tagged_pdf):
    """A redaction rewrites a page and an R4 content mark appends a stream to one — there the
    output is our own construction, so it is sanitised (M114). This is the half of the decision the
    corpus does *not* speak to, and it is kept deliberately rather than dropped for symmetry."""
    from klarpdf.model.page_edits import Redaction

    v = VirtualDocument.from_path(tagged_pdf)
    assert PyMuPDFEngine().save_options(v)["clean"] is CLEAN_COPIED

    v.add_annotation(0, Redaction(rects=((72, 72, 200, 90),)))
    assert v.has_redactions()
    assert PyMuPDFEngine().save_options(v)["clean"] is CLEAN_REWRITTEN


def _spy_on_the_save(engine, vdoc, out_path):
    """``materialize`` ``vdoc`` to ``out_path``, returning the keywords it handed ``Document.save``."""
    calls = []
    real_save = fitz.Document.save

    def spy(self, path, **kwargs):
        if path == out_path:  # the copy-a-source `tobytes` also lands here
            calls.append(kwargs)
        return real_save(self, path, **kwargs)

    fitz.Document.save = spy
    try:
        engine.materialize(vdoc, out_path)
    finally:
        fitz.Document.save = real_save
    assert len(calls) == 1
    return calls[0]


def test_a_save_writes_with_the_level_it_reports(tagged_pdf, tmp_path):
    """``save_options`` is what ``materialize`` writes with, not a second opinion about it — the
    Reduced-Size baseline reports it as "what a plain Save would write" (M111).

    Scoped to a save that **rewrites the document**, which is what ``save_options`` describes. A
    save with nothing but added marks appends instead and has its own keywords (M116) — the test
    below. The rotation is the cheapest edit that is not additive, so this one still takes the
    copy route it was written for.
    """
    out_path = str(tmp_path / "out.pdf")
    engine = PyMuPDFEngine()
    v = VirtualDocument.from_path(tagged_pdf)
    v.set_rotation(0, 90)
    assert not engine.appends(v)
    reported = engine.save_options(v)

    written = _spy_on_the_save(engine, v, out_path)

    assert written["garbage"] == GARBAGE_COPY  # nothing structural happened to this document
    assert written["use_objstms"] == 1
    assert written["clean"] is reported["clean"]  # writes what it reports, whichever way it decided


def test_a_save_does_not_grow_the_file_it_was_given(photo_pdf, tmp_path):
    """Leaving a document as packed as it arrived must not mean writing a *bigger* one, and
    re-saving must not ratchet: measured across the corpus, every file still saves smaller than the
    file it came from, and a second save of an output reproduces its size exactly."""
    once = str(tmp_path / "once.pdf")
    PyMuPDFEngine().materialize(VirtualDocument.from_path(photo_pdf), once)
    twice = str(tmp_path / "twice.pdf")
    PyMuPDFEngine().materialize(VirtualDocument.from_path(once), twice)

    import os

    assert os.path.getsize(once) <= os.path.getsize(photo_pdf)
    assert os.path.getsize(twice) == os.path.getsize(once)


def test_a_duplicated_image_page_does_not_duplicate_the_image(photo_pdf, tmp_path):
    """Why the graft keeps level 4: only it merges identical **streams**.

    Duplicating an image-heavy page copies a reference to the same picture, and levels 1–3 write
    the picture again for every copy — measured on a real page, 39.5 MB against 1.9 MB at level 4,
    which is also 4× faster because detecting twenty identical images beats compressing twenty
    copies of them.
    """
    v = VirtualDocument.from_path(photo_pdf)
    one_page = tmp_path / "one.pdf"
    PyMuPDFEngine().materialize(v, str(one_page))

    v.ordered = v.ordered * 6  # the page plus five duplicates
    six_pages = tmp_path / "six.pdf"
    PyMuPDFEngine().materialize(v, str(six_pages))

    assert v.page_set_unchanged() is False  # the graft route, so level 4
    # Six copies of a page whose bytes are almost entirely one photograph, for well under twice
    # the size of one — anything near 6× means the streams were written out again.
    assert six_pages.stat().st_size < one_page.stat().st_size * 2


# ---- what survives ---------------------------------------------------------------


def test_a_tagged_document_stays_tagged(tagged_pdf, tmp_path):
    """The Section 508 case: a filled copy of a federal form that is no longer tagged is not an
    equivalent document for a screen-reader user."""
    before = _catalog(tagged_pdf)
    out = _materialize(VirtualDocument.from_path(tagged_pdf), tmp_path)
    after = _catalog(out)
    assert before["MarkInfo"] == after["MarkInfo"] == "dict"
    assert before["StructTreeRoot"] == after["StructTreeRoot"] == "xref"


def test_a_form_fill_keeps_the_structure(tagged_pdf, tmp_path):
    """The actual TC-002 shape — filling a field must not cost the document its tags."""
    v = VirtualDocument.from_path(tagged_pdf)
    v.set_field_value("whatever", "value")   # no such widget; the save path is what is under test
    out = _materialize(v, tmp_path)
    assert _catalog(out)["StructTreeRoot"] == "xref"


def test_owner_password_encryption_and_permissions_round_trip(restricted_pdf, tmp_path):
    """Silently turning a restricted document into an unrestricted one is a provenance change the
    caller never asked for and was never told about."""
    with fitz.open(restricted_pdf) as doc:
        before, cipher = doc.permissions, doc.metadata["encryption"]
    assert cipher and before != -4, "fixture is not actually restricted"   # -4 == everything allowed
    out = _materialize(VirtualDocument.from_path(restricted_pdf), tmp_path)
    with fitz.open(out) as doc:
        assert doc.permissions == before          # not -4: the restrictions are still there
        assert doc.metadata["encryption"] == cipher


def test_a_scheme_less_web_link_is_not_turned_into_a_launch_action(weblink_pdf, tmp_path):
    """Asserted on the link's **action dictionary**, not on what PyMuPDF reads back.

    ``get_links()`` reports this link as a *file* link either way — that misreading is the bug's
    cause, so it cannot also be the test's oracle. Reading the raw object is the way round it, and
    ``xref_object`` decodes objects out of object streams, which a byte grep of the file cannot.
    """
    out = _materialize(VirtualDocument.from_path(weblink_pdf), tmp_path)
    with fitz.open(out) as doc:
        actions = [doc.xref_object(link["xref"]) for link in doc[0].get_links()]
    assert actions, "the link did not survive at all"
    assert any("/S /URI" in a for a in actions)
    assert not any("/Launch" in a for a in actions)


def test_a_reordered_document_still_saves(tagged_pdf, tmp_path):
    """The other route still works. It still loses the structure tree — rebuilding one across moved
    pages is a separate problem — but it must keep producing a correct document."""
    v = VirtualDocument.from_path(tagged_pdf)
    v.ordered = list(reversed(v.ordered))
    out = _materialize(v, tmp_path)
    with fitz.open(out) as doc:
        assert doc.page_count == 3
        assert "page 0" in doc[2].get_text()
        assert "page 2" in doc[0].get_text()


# ---- restrictions survive a password change --------------------------------------


def test_a_documents_restrictions_are_read_at_open(restricted_pdf):
    """`_permissions` used to start at -1, "allow everything", whatever the document said."""
    v = VirtualDocument.from_path(restricted_pdf)
    with fitz.open(restricted_pdf) as doc:
        assert v.permissions == doc.permissions != -1


def test_setting_a_password_keeps_the_existing_restrictions(restricted_pdf, tmp_path):
    """The defect: the password dialog pre-ticks its boxes from `vdoc.permissions`, so with the -1
    default every box arrived ticked whatever the file restricted — and accepting the dialog
    granted copying, modification and assembly on a document that forbade them. Nobody was told.
    """
    v = VirtualDocument.from_path(restricted_pdf)
    before = v.permissions
    v.set_encryption("secret", v.permissions)      # what the dialog now stages unchanged
    out = _materialize(v, tmp_path)
    with fitz.open(out) as doc:
        assert doc.authenticate("secret")
        assert doc.permissions == before != -4     # -4 would be "everything allowed"


def test_removing_a_password_drops_the_restrictions_with_it(restricted_pdf, tmp_path):
    """The other direction, unchanged: permission bits live inside the encryption dictionary, so
    restrictions without a password are not a thing that exists."""
    v = VirtualDocument.from_path(restricted_pdf)
    v.set_encryption(None)
    assert v.permissions == -1
    out = _materialize(v, tmp_path)
    with fitz.open(out) as doc:
        assert not doc.needs_pass
        assert doc.metadata["encryption"] in (None, "")
