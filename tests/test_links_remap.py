"""Internal link remap at materialize (PLAN.md, M33). Headless.

insert_pdf drops internal **GoTo** links whose target isn't in the contiguous run being copied, and
drops **named-destination** links entirely (the /Dests name tree isn't rebuilt) — so our
reorder/delete materialize loses them. We rebuild both from the source against the new page order
(repoint survivors, drop links to deleted pages, baking a named dest to a direct GoTo), like the
outline remap. URI links pass through — except one whose text PyMuPDF's unescaped re-serialisation
chokes on (an unbalanced paren, seen in the wild), which insert_pdf silently drops and the remap
pass restores with proper escaping.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from model.edit_engine import PyMuPDFEngine
from model.links_remap import link_target_map
from model.virtual_document import PageRef, VirtualDocument


@pytest.fixture
def linked_pdf(tmp_path) -> str:
    """4 pages with internal GoTo links 0->3, 1->2, 3->0, plus a URI link on page 0."""
    path = str(tmp_path / "links.pdf")
    doc = fitz.open()
    for i in range(4):
        doc.new_page().insert_text((72, 72), f"PAGE {i}", fontsize=20)
    for src_pg, target in {0: 3, 1: 2, 3: 0}.items():
        doc[src_pg].insert_link(
            {"kind": fitz.LINK_GOTO, "from": fitz.Rect(72, 100, 200, 120),
             "page": target, "to": fitz.Point(0, 0)}
        )
    doc[0].insert_link(
        {"kind": fitz.LINK_URI, "from": fitz.Rect(72, 130, 200, 150), "uri": "https://example.com"}
    )
    doc.save(path)
    doc.close()
    return path


def _materialize(vdoc, tmp_path, name="out.pdf") -> str:
    out = str(tmp_path / name)
    PyMuPDFEngine().materialize(vdoc, out)
    return out


def _goto_targets(path, page_index) -> list[int]:
    doc = fitz.open(path)
    page = doc[page_index]
    targets = sorted(link["page"] for link in page.get_links() if link["kind"] == fitz.LINK_GOTO)
    doc.close()
    return targets


# ---- the pure index map -----------------------------------------------------


def test_link_target_map_first_occurrence_and_drops_deleted():
    refs = [PageRef("s", 3), PageRef("s", 1), PageRef("s", 1)]  # reordered + duplicated; src0/2 gone
    assert link_target_map(refs) == {("s", 3): 0, ("s", 1): 1}  # dup → first occurrence


# ---- materialize integration ------------------------------------------------


def test_internal_links_are_lost_without_the_remap_fixture_sanity(linked_pdf):
    """Sanity: the fixture really does carry internal GoTo links on the source."""
    assert _goto_targets(linked_pdf, 0) == [3] and _goto_targets(linked_pdf, 3) == [0]


def test_internal_links_follow_reorder(linked_pdf, tmp_path):
    v = VirtualDocument.from_path(linked_pdf)
    v.ordered = list(reversed(v.ordered))  # [3,2,1,0] → src0→out3, src1→out2, src2→out1, src3→out0
    out = _materialize(v, tmp_path)
    assert _goto_targets(out, 3) == [0]   # src0's link → src3, now at out0
    assert _goto_targets(out, 2) == [1]   # src1's link → src2, now at out1
    assert _goto_targets(out, 0) == [3]   # src3's link → src0, now at out3
    assert _goto_targets(out, 1) == []    # src2 had no link


def test_link_to_deleted_page_is_dropped(linked_pdf, tmp_path):
    v = VirtualDocument.from_path(linked_pdf)
    v.delete_page(2)  # ordered [0,1,3]; src1's link target (src2) is gone
    out = _materialize(v, tmp_path)
    assert _goto_targets(out, 0) == [2]   # src0 → src3 (now out2)
    assert _goto_targets(out, 1) == []    # src1 → src2 (deleted) → dropped
    assert _goto_targets(out, 2) == [0]   # src3 → src0 (out0)


def test_uri_link_preserved(linked_pdf, tmp_path):
    v = VirtualDocument.from_path(linked_pdf)
    out = _materialize(v, tmp_path)
    doc = fitz.open(out)
    uris = [link.get("uri") for link in doc[0].get_links() if link["kind"] == fitz.LINK_URI]
    doc.close()
    assert uris == ["https://example.com"]


def test_identity_order_keeps_links_without_duplicating(linked_pdf, tmp_path):
    """No reorder → insert_pdf keeps the links; the rebuild must not duplicate them."""
    v = VirtualDocument.from_path(linked_pdf)
    out = _materialize(v, tmp_path)
    assert _goto_targets(out, 0) == [3]
    assert _goto_targets(out, 1) == [2]
    assert _goto_targets(out, 3) == [0]
    doc = fitz.open(out)
    for i in (0, 1, 3):
        assert sum(1 for ln in doc[i].get_links() if ln["kind"] == fitz.LINK_GOTO) == 1
    doc.close()


def test_duplicated_page_links_point_to_first_occurrence(linked_pdf, tmp_path):
    v = VirtualDocument.from_path(linked_pdf)
    v.ordered = v.ordered + [v.ordered[3]]  # [0,1,2,3,3]
    out = _materialize(v, tmp_path)
    assert _goto_targets(out, 0) == [3]   # src0 → src3 → first occurrence (out3)
    assert _goto_targets(out, 3) == [0]   # both copies of src3 → src0 (out0)
    assert _goto_targets(out, 4) == [0]


# ---- named-destination links (the Javadoc / printed-web case) ---------------


@pytest.fixture
def named_pdf(tmp_path) -> str:
    """4 pages; page 0 links to a named dest on page 2, page 1 to a named dest on page 3."""
    path = str(tmp_path / "named.pdf")
    doc = fitz.open()
    for i in range(4):
        doc.new_page().insert_text((72, 72), f"P{i}", fontsize=20)
    doc.xref_set_key(
        doc.pdf_catalog(),
        "Dests",
        "<< /dA [ %d 0 R /XYZ 0 700 0 ] /dB [ %d 0 R /XYZ 0 500 0 ] >>"
        % (doc.page_xref(2), doc.page_xref(3)),
    )
    doc[0].insert_link({"kind": fitz.LINK_NAMED, "from": fitz.Rect(72, 100, 200, 120), "nameddest": "dA"})
    doc[1].insert_link({"kind": fitz.LINK_NAMED, "from": fitz.Rect(72, 100, 200, 120), "nameddest": "dB"})
    doc.save(path)
    doc.close()
    return path


def test_named_links_dropped_by_insert_pdf_without_the_remap(named_pdf):
    """Sanity: insert_pdf alone drops named-destination links — so the remap is what saves them."""
    out_doc = fitz.open()
    out_doc.insert_pdf(fitz.open(named_pdf), links=True)
    assert [ln for ln in out_doc[0].get_links() if ln["kind"] == fitz.LINK_GOTO] == []
    assert [ln for ln in out_doc[0].get_links() if ln["kind"] == fitz.LINK_NAMED] == []
    out_doc.close()


def test_named_links_survive_identity_save_as_goto(named_pdf, tmp_path):
    out = _materialize(VirtualDocument.from_path(named_pdf), tmp_path)
    assert _goto_targets(out, 0) == [2]  # page0's named dest (page 2) baked to a working GoTo
    assert _goto_targets(out, 1) == [3]


def test_named_links_follow_reorder(named_pdf, tmp_path):
    v = VirtualDocument.from_path(named_pdf)
    v.ordered = list(reversed(v.ordered))  # [3,2,1,0]: src2→out1, src3→out0
    out = _materialize(v, tmp_path)
    assert _goto_targets(out, 3) == [1]   # src0 (out3) → named src2, now out1
    assert _goto_targets(out, 2) == [0]   # src1 (out2) → named src3, now out0


def test_named_link_to_deleted_page_dropped(named_pdf, tmp_path):
    v = VirtualDocument.from_path(named_pdf)
    v.delete_page(2)  # ordered [0,1,3]; page0's target (src2) is gone
    out = _materialize(v, tmp_path)
    assert _goto_targets(out, 0) == []    # named target deleted → dropped
    assert _goto_targets(out, 1) == [2]   # page1 → named src3, now out2


def test_no_links_document_materializes_clean(tmp_path):
    src = str(tmp_path / "plain.pdf")
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "no links here", fontsize=14)
    doc.save(src)
    doc.close()
    out = _materialize(VirtualDocument.from_path(src), tmp_path)
    result = fitz.open(out)
    try:
        assert result[0].get_links() == []
    finally:
        result.close()


# ---- URI links insert_pdf drops (unescaped re-serialisation) --------------------

_PAREN_URI = "http://www.adobe.com)"  # the in-the-wild shape (novaPDF): unbalanced closing paren


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


@pytest.fixture
def paren_uri_pdf(tmp_path) -> str:
    """2 pages; page 0 carries a paren-poisoned URI link first (like the reporting file), then two
    well-formed ones. Built via insert_link with pre-escaped text — the only way PyMuPDF writes
    the poisoned link at all — which produces exactly the valid file the original producer wrote."""
    path = str(tmp_path / "paren.pdf")
    doc = fitz.open()
    for i in range(2):
        doc.new_page().insert_text((72, 72), f"PAGE {i}", fontsize=14)
    for j, uri in enumerate([_PAREN_URI, "http://x.com/(balanced)", "https://plain.example"]):
        doc[0].insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(72, 100 + 30 * j, 200, 120 + 30 * j),
                            "uri": _pdf_escape(uri)})
    doc.save(path)
    doc.close()
    return path


def _uris(path, page_index) -> list[str]:
    doc = fitz.open(path)
    try:
        return sorted(l["uri"] for l in doc[page_index].get_links() if l["kind"] == fitz.LINK_URI)
    finally:
        doc.close()


def test_paren_uri_link_survives_save(paren_uri_pdf, tmp_path):
    """insert_pdf silently drops the unbalanced-paren URI ("skipping bad link / annot item 0");
    the restore pass re-adds it, so a save loses no link."""
    assert _uris(paren_uri_pdf, 0) == sorted([_PAREN_URI, "http://x.com/(balanced)",
                                              "https://plain.example"])  # fixture is faithful
    out = _materialize(VirtualDocument.from_path(paren_uri_pdf), tmp_path)
    assert _uris(out, 0) == _uris(paren_uri_pdf, 0)


def test_paren_uri_link_follows_a_reorder(paren_uri_pdf, tmp_path):
    v = VirtualDocument.from_path(paren_uri_pdf)
    v.ordered = list(reversed(v.ordered))  # the linked page moves to index 1
    out = _materialize(v, tmp_path)
    assert _PAREN_URI in _uris(out, 1)
    assert _uris(out, 0) == []


def test_well_formed_uri_links_are_not_duplicated(linked_pdf, tmp_path):
    """A URI link insert_pdf carries fine must appear exactly once — the restore pass only adds
    what went missing."""
    out = _materialize(VirtualDocument.from_path(linked_pdf), tmp_path)
    assert _uris(out, 0) == ["https://example.com"]


def test_pdf_string_escape_round_trips_through_a_written_file(tmp_path):
    from model.links_remap import _pdf_string_escape

    nasty = r"http://e.x/a)b(c\d"  # unbalanced paren + a literal backslash
    doc = fitz.open()
    doc.new_page().insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(72, 100, 200, 120),
                                "uri": _pdf_string_escape(nasty)})
    path = str(tmp_path / "esc.pdf")
    doc.save(path)
    doc.close()
    assert _uris(path, 0) == [nasty]  # the escape is undone by PDF string decoding
