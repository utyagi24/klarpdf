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

from model.edit_engine import PyMuPDFEngine
from model.virtual_document import VirtualDocument

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
    """Asserted on the written bytes, not on what PyMuPDF reads back: PyMuPDF *reads* this link as
    a file link either way, which is exactly why the corruption went unnoticed."""
    out = _materialize(VirtualDocument.from_path(weblink_pdf), tmp_path)
    written = open(out, "rb").read()
    assert b"/Launch" not in written
    assert written.count(b"/URI") >= 1


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
