"""Destinations read and written exactly as the file spells them (PLAN.md, M150; #362, #373).

Headless — PyMuPDF only, no Qt. Three kinds of test live here:

* the **library's behaviour pinned**, including two writer bugs this milestone exists to route
  around, so a PyMuPDF upgrade that changes either fails here with the reason rather than quietly
  moving readers' bookmarks;
* the **reader**, over every destination form and every spelling the corpus contains;
* the **round trip** — a page move must give back byte-identical destinations, at every rotation
  and on a page whose boxes do not start at 0,0.
"""

from __future__ import annotations

import re

import pymupdf as fitz
import pytest

from klarpdf.model.destinations import (
    RAW_TAIL_KEY,
    Destination,
    apply_outline_destinations,
    content_point,
    outline_item_xrefs,
    page_index_map,
    read_destination,
    read_outline_destinations,
    split_destination,
    write_destination,
)
from klarpdf.model.edit_engine import PyMuPDFEngine
from klarpdf.model.virtual_document import VirtualDocument

PAGE_W, PAGE_H = 612.0, 792.0


def _doc(pages=3, rotate=None, cropbox=None, width=PAGE_W, height=PAGE_H):
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=width, height=height)
    target = doc[pages - 1]
    if cropbox is not None:
        target.set_cropbox(fitz.Rect(*cropbox))
    if rotate:
        target.set_rotation(rotate)
    return doc


def _set_outline(doc, dest_src: str, title="Bookmark") -> int:
    """Write a one-entry outline whose /Dest is exactly ``dest_src``. Returns the item's xref."""
    item, root = doc.get_new_xref(), doc.get_new_xref()
    doc.update_object(item, f"<< /Title ({title}) /Parent {root} 0 R /Dest {dest_src} >>")
    doc.update_object(root, f"<< /Type /Outlines /First {item} 0 R /Last {item} 0 R /Count 1 >>")
    doc.xref_set_key(doc.pdf_catalog(), "Outlines", f"{root} 0 R")
    return item


def _link(doc, page_index: int, dest_src: str) -> int:
    """A link annotation on ``page_index`` whose /Dest is exactly ``dest_src``. Returns its xref."""
    annot = doc.get_new_xref()
    doc.update_object(
        annot,
        f"<< /Type /Annot /Subtype /Link /Rect [10 10 100 30] /Border [0 0 0] /Dest {dest_src} >>",
    )
    existing = doc.xref_get_key(doc.page_xref(page_index), "Annots")
    prefix = existing[1].strip()[1:-1] + " " if existing[0] == "array" else ""
    doc.xref_set_key(doc.page_xref(page_index), "Annots", f"[{prefix}{annot} 0 R]")
    doc.reload_page(doc[page_index])
    return annot


def _raw_dest(doc, xref: int) -> str:
    kind, text = doc.xref_get_key(xref, "Dest")
    if kind == "array":
        return text
    action = doc.xref_get_key(xref, "A")
    return action[1] if action[0] != "null" else "(none)"


# ---- the library, pinned ------------------------------------------------------


def test_set_toc_still_ignores_our_extra_dest_key():
    """``bake_dest`` parks the carried destination inside the dict ``set_toc`` is handed.

    That works only because ``set_toc`` ignores a key it does not know. If a PyMuPDF ever
    validated its input this would raise, and every save that moves pages would raise with it —
    so it is pinned rather than assumed.
    """
    doc = _doc(2)
    dest = {"kind": 1, "page": 1, "to": fitz.Point(0, 0), "zoom": 0.0, RAW_TAIL_KEY: "/Fit"}
    doc.set_toc([[1, "Entry", 2, dest]])
    assert len(doc.get_toc()) == 1
    doc.close()


def test_set_toc_writes_items_in_the_lists_depth_first_order():
    """The pairing `apply_outline_destinations` relies on: row *i* is outline item *i*."""
    doc = _doc(6)
    titles = ["A", "A1", "A1a", "A1b", "A2", "B", "B1", "C"]
    levels = [1, 2, 3, 3, 2, 1, 2, 1]
    doc.set_toc(
        [
            [lv, t, (i % 6) + 1, {"kind": 1, "page": i % 6, "to": fitz.Point(0, 0), "zoom": 0.0}]
            for i, (lv, t) in enumerate(zip(levels, titles))
        ]
    )
    written = [doc.xref_get_key(x, "Title")[1] for x in outline_item_xrefs(doc)]
    assert written == titles
    assert [e[0] for e in doc.get_toc()] == levels
    doc.close()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_set_toc_still_misplaces_a_point_on_a_270_degree_page(rotation):
    """PyMuPDF's ``set_toc`` does not round-trip a destination point at **270°** (#373).

    Measured on 1.27.2.3: reading gives the point in the target page's displayed frame and writing
    takes it back in that frame, which is exact at 0°, 90° and 180° and swaps the page's width and
    height at 270° — so the bookmark moves, and moves further on every save. This is why M150
    carries the file's own bytes instead of a point.

    **If this test fails at 270°, PyMuPDF has fixed the bug.** That is good news and changes
    nothing that must be undone: the carry-through never asks ``set_toc`` for a position.
    """
    doc = _doc(3, rotate=rotation)
    point = fitz.Point(100.0, 192.0)
    doc.set_toc([[1, "B", 3, {"kind": 1, "page": 2, "to": point, "zoom": 0.0}]])
    read_back = doc.get_toc(simple=False)[0][3]["to"]
    if rotation == 270:
        assert read_back != point, "PyMuPDF fixed set_toc at 270° — see this test's docstring"
    else:
        assert read_back == point
    doc.close()


def test_set_toc_still_ignores_a_crop_box_that_does_not_start_at_the_origin():
    """The second ``set_toc`` bug, and the one #373 did not record: the crop origin is dropped.

    Same reading as the test above — a failure here means PyMuPDF fixed it, and nothing needs
    undoing.
    """
    doc = _doc(3, cropbox=(20, 30, 592, 762))
    point = fitz.Point(100.0, 192.0)
    doc.set_toc([[1, "B", 3, {"kind": 1, "page": 2, "to": point, "zoom": 0.0}]])
    assert doc.get_toc(simple=False)[0][3]["to"] != point
    doc.close()


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_insert_link_still_writes_its_point_unrotated(rotation):
    """``insert_link`` takes ``to`` unrotated while ``get_links`` reports it rotated.

    So handing a link's own ``to`` straight back — what the remap did before M150 — moves the
    point on every rotated page. Pinned for the same reason as the two above.
    """
    doc = _doc(2, rotate=rotation)
    doc[0].insert_link(
        {"kind": fitz.LINK_GOTO, "from": fitz.Rect(10, 10, 100, 30), "page": 1,
         "to": fitz.Point(100.0, 192.0)}
    )
    assert doc[0].get_links()[0]["to"] != fitz.Point(100.0, 192.0)
    doc.close()


def test_a_page_does_not_see_a_link_it_was_just_given():
    """Why ``links_remap`` finds the new annotation through ``/Annots`` and not through the page.

    Measured on 1.27.2.3: ``get_links()`` on the very ``Page`` object that took the
    ``insert_link`` returns nothing, and ``doc[0]`` hands back that same cached object. Without
    this fact the destination pass looks correct and writes nothing at all — which is exactly what
    it did until this was measured.
    """
    doc = _doc(2)
    page = doc[0]
    page.insert_link({"kind": fitz.LINK_GOTO, "from": fitz.Rect(10, 10, 100, 30), "page": 1,
                      "to": fitz.Point(0, 0)})
    assert page.get_links() == []
    assert doc[0].get_links() == []
    assert doc.xref_get_key(doc.page_xref(0), "Annots")[0] == "array"  # the file has it
    doc.close()


# ---- content_point, pinned against PyMuPDF's own (correct) reader --------------


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("cropbox", [None, (20, 30, 592, 762)])
def test_content_point_matches_the_library_at_every_rotation_and_crop(rotation, cropbox):
    """``content_point`` is the one coordinate conversion M150 does itself, so it is checked
    against the library rather than against arithmetic written twice.

    PyMuPDF's *reader* is correct where its writers are not, and at rotation 0 the frame it
    reports a point in — the displayed one — **is** the content frame, so the two are directly
    comparable there. At other rotations the reader's answer is the rotated one while
    ``content_point`` deliberately stays unrotated (the view applies rotation itself), so what is
    checked is that the unrotated answer does not move when the page is spun.
    """
    doc = _doc(2, rotate=rotation, cropbox=cropbox)
    target_xref = doc.page_xref(1)
    link = _link(doc, 0, f"[{target_xref} 0 R /XYZ 100 540 0]")
    dest = read_destination(doc, link, page_index_map(doc))
    mine = content_point(doc[1], dest)

    unrotated = fitz.open()
    unrotated.new_page(width=PAGE_W, height=PAGE_H)
    unrotated.new_page(width=PAGE_W, height=PAGE_H)
    if cropbox is not None:
        unrotated[1].set_cropbox(fitz.Rect(*cropbox))
    plain = _link(unrotated, 0, f"[{unrotated.page_xref(1)} 0 R /XYZ 100 540 0]")
    library = unrotated[0].get_links()[0]["to"]
    assert (round(mine[0], 6), round(mine[1], 6)) == (round(library.x, 6), round(library.y, 6))
    assert plain and dest  # the fixtures really did carry a destination
    doc.close()
    unrotated.close()


def test_content_point_is_none_where_the_destination_says_nothing():
    doc = _doc(2)
    ref = doc.page_xref(1)
    cases = {
        "/Fit": (None, None),
        "/FitB": (None, None),
        "/FitV 100": (100.0, None),
        "/XYZ null 600 0": (None, 192.0),
        "/XYZ 100 null 0": (100.0, None),
        "/FitH 600": (None, 192.0),
        "/FitBH 600": (None, 192.0),
        "/FitR 50 500 300 650": (50.0, 142.0),
    }
    for tail, expected in cases.items():
        dest = split_destination(f"[{ref} 0 R {tail}]", page_index_map(doc))
        assert dest is not None, tail
        assert content_point(doc[1], dest) == expected, tail
    doc.close()


# ---- the reader ---------------------------------------------------------------


def test_reads_the_three_spellings_of_a_destination():
    """``/Dest``, an inline ``/A``, and an ``/A`` that is an indirect reference.

    The third is how Cisco's annual report writes every one of its 156 bookmarks; a reader that
    stopped following the reference would drop them without a word.
    """
    doc = _doc(3)
    ref = doc.page_xref(2)
    pages = page_index_map(doc)

    direct = _set_outline(doc, f"[{ref} 0 R /XYZ 100 600 0]")
    assert read_destination(doc, direct, pages) == Destination(2, "/XYZ 100 600 0")

    inline = doc.get_new_xref()
    doc.update_object(inline, f"<< /Title (B) /A << /S /GoTo /D [{ref} 0 R /FitH 600] >> >>")
    assert read_destination(doc, inline, pages) == Destination(2, "/FitH 600")

    action = doc.get_new_xref()
    doc.update_object(action, f"<< /S /GoTo /D [{ref} 0 R /Fit] >>")
    indirect = doc.get_new_xref()
    doc.update_object(indirect, f"<< /Title (C) /A {action} 0 R >>")
    assert read_destination(doc, indirect, pages) == Destination(2, "/Fit")
    doc.close()


def test_resolves_a_name_from_the_dests_dictionary_and_from_the_name_tree():
    """Both places a document may keep its named destinations, and both key spellings.

    ``/Root/Dests`` is keyed by name objects (the Javadoc pages, the Sony manual); a name tree
    under ``/Root/Names/Dests`` is keyed by strings, which a generator may write as a literal or —
    for anything outside ASCII — as UTF-16 hex. One Nature paper uses the last of those for all 81
    of its links.
    """
    doc = _doc(3)
    ref = doc.page_xref(2)
    pages = page_index_map(doc)

    dests = doc.get_new_xref()
    doc.update_object(dests, f"<< /sec4 [{ref} 0 R /XYZ 0 700 0] >>")
    doc.xref_set_key(doc.pdf_catalog(), "Dests", f"{dests} 0 R")
    item = _set_outline(doc, "/sec4")
    assert read_destination(doc, item, pages) == Destination(2, "/XYZ 0 700 0")
    doc.close()

    for key_src, name in (
        ("(plain name)", "plain name"),
        ("<FEFF00E9007400E9>", "été"),                     # UTF-16BE, leading BOM
        ("(has \\(parens\\))", "has (parens)"),
    ):
        doc = _doc(3)
        ref = doc.page_xref(2)
        leaf = doc.get_new_xref()
        doc.update_object(leaf, f"<< /Names [{key_src} [{ref} 0 R /FitBH 500]] >>")
        root = doc.get_new_xref()
        doc.update_object(root, f"<< /Kids [{leaf} 0 R] >>")
        names = doc.get_new_xref()
        doc.update_object(names, f"<< /Dests {root} 0 R >>")
        doc.xref_set_key(doc.pdf_catalog(), "Names", f"{names} 0 R")
        item = _set_outline(doc, f"({name})" if "(" not in name else "(has \\(parens\\))")
        assert read_destination(doc, item, page_index_map(doc)) == Destination(2, "/FitBH 500"), key_src
        doc.close()


def test_returns_none_where_there_is_nothing_to_carry():
    """Each of these keeps the behaviour that predates M150 — the target page, and nothing more."""
    doc = _doc(3)
    pages = page_index_map(doc)

    bare = doc.get_new_xref()
    doc.update_object(bare, "<< /Title (Just a heading) >>")
    assert read_destination(doc, bare, pages) is None

    web = doc.get_new_xref()
    doc.update_object(web, "<< /Title (Web) /A << /S /URI /URI (https://example.org) >> >>")
    assert read_destination(doc, web, pages) is None

    remote = doc.get_new_xref()
    doc.update_object(remote, "<< /Title (Other file) /A << /S /GoToR /D [0 /Fit] >> >>")
    assert read_destination(doc, remote, pages) is None

    no_page = _set_outline(doc, "[null /XYZ null 759 null]")
    assert read_destination(doc, no_page, pages) is None

    empty_name = doc.get_new_xref()
    doc.update_object(empty_name, "<< /Title (Empty) /Dest / >>")
    assert read_destination(doc, empty_name, pages) is None
    doc.close()


def test_outline_destinations_are_refused_when_the_walk_and_get_toc_disagree(monkeypatch):
    """Pairing by position is only safe while the two agree on how many bookmarks there are."""
    doc = _doc(3)
    _set_outline(doc, f"[{doc.page_xref(2)} 0 R /XYZ 100 600 0]")
    assert len(read_outline_destinations(doc, len(doc.get_toc()))) == 1
    assert read_outline_destinations(doc, 2) == []  # a count that does not match: nothing at all
    doc.close()


# ---- the round trip -----------------------------------------------------------


FORMS = [
    "/XYZ 100 600 0",
    "/XYZ null 600 0",
    "/XYZ null 600 null",
    "/XYZ 100 600 2",
    "/FitH 600",
    "/FitBH 600",
    "/FitV 100",
    "/FitR 50 500 300 650",
    "/Fit",
    "/FitB",
]


@pytest.mark.parametrize("tail", FORMS)
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_a_page_move_keeps_a_bookmarks_destination_exactly(tmp_path, tail, rotation):
    """#373's widened expectation: same form, same point, at every rotation.

    Before M150, four of these ten forms came back as ``/XYZ 72 756 0`` — the top of the page —
    because ``get_toc`` does not report their position and ``set_toc`` cannot write it, and
    ``/XYZ 100 600`` came back displaced on a 270° page.
    """
    src = tmp_path / "in.pdf"
    doc = _doc(3, rotate=rotation)
    _set_outline(doc, f"[{doc.page_xref(2)} 0 R {tail}]")
    doc.save(str(src))
    doc.close()

    vdoc = VirtualDocument.from_path(str(src))
    vdoc.move_pages([0], 3)                     # page 1 to the end: every index shifts
    out = tmp_path / "out.pdf"
    PyMuPDFEngine().materialize(vdoc, str(out))

    doc = fitz.open(out)
    toc = doc.get_toc()
    assert len(toc) == 1 and toc[0][2] == 2     # the bookmark followed its page
    carried = read_destination(doc, outline_item_xrefs(doc)[0], page_index_map(doc))
    assert carried == Destination(1, tail)
    doc.close()


@pytest.mark.parametrize("tail", ["/XYZ 100 600 0", "/XYZ null 600 null", "/FitR 50 500 300 650"])
def test_a_page_move_keeps_a_links_destination_exactly(tmp_path, tail):
    src = tmp_path / "in.pdf"
    doc = _doc(3, rotate=270)
    _link(doc, 0, f"[{doc.page_xref(2)} 0 R {tail}]")
    doc.save(str(src))
    doc.close()

    vdoc = VirtualDocument.from_path(str(src))
    vdoc.move_pages([0], 3)
    out = tmp_path / "out.pdf"
    PyMuPDFEngine().materialize(vdoc, str(out))

    doc = fitz.open(out)
    links = doc[2].get_links()                  # the linking page went to the end
    assert len(links) == 1
    carried = read_destination(doc, links[0]["xref"], page_index_map(doc))
    assert carried == Destination(1, tail)
    doc.close()


def test_a_page_move_keeps_a_destination_on_a_page_whose_boxes_are_offset(tmp_path):
    """The crop-box half of the ``set_toc`` bug, end to end."""
    src = tmp_path / "in.pdf"
    doc = _doc(3, cropbox=(20, 30, 592, 762))
    _set_outline(doc, f"[{doc.page_xref(2)} 0 R /XYZ 100 540 0]")
    doc.save(str(src))
    doc.close()

    vdoc = VirtualDocument.from_path(str(src))
    vdoc.move_pages([0], 3)
    out = tmp_path / "out.pdf"
    PyMuPDFEngine().materialize(vdoc, str(out))

    doc = fitz.open(out)
    carried = read_destination(doc, outline_item_xrefs(doc)[0], page_index_map(doc))
    assert carried == Destination(1, "/XYZ 100 540 0")
    doc.close()


def test_a_named_link_keeps_its_position_through_a_page_move(tmp_path):
    """A named destination is still baked to a direct jump — but now it keeps *where* it pointed.

    ``insert_pdf`` drops named links outright and the remap has always re-emitted them as direct
    GoTos (M33). Until M150 that threw the position away with the name; only the name goes now.
    """
    src = tmp_path / "in.pdf"
    doc = _doc(3)
    dests = doc.get_new_xref()
    doc.update_object(dests, f"<< /sec3 [{doc.page_xref(2)} 0 R /XYZ 40 700 0] >>")
    doc.xref_set_key(doc.pdf_catalog(), "Dests", f"{dests} 0 R")
    _link(doc, 0, "/sec3")
    doc.save(str(src))
    doc.close()

    vdoc = VirtualDocument.from_path(str(src))
    vdoc.move_pages([0], 3)
    out = tmp_path / "out.pdf"
    PyMuPDFEngine().materialize(vdoc, str(out))

    doc = fitz.open(out)
    links = doc[2].get_links()
    assert len(links) == 1, "the named link survived the move"
    assert read_destination(doc, links[0]["xref"], page_index_map(doc)) == \
        Destination(1, "/XYZ 40 700 0")
    doc.close()


def test_a_link_whose_name_pymupdf_cannot_resolve_survives_a_page_move(tmp_path):
    """The 81 links one corpus document lost on every save, in miniature.

    Its destinations are named with UTF-16 hex strings; ``get_links`` resolves none of them to a
    page, so ``internal_link_target`` returned ``None`` and the remap dropped the link. Reading the
    name tree directly still gets there.
    """
    src = tmp_path / "in.pdf"
    doc = _doc(3)
    leaf = doc.get_new_xref()
    doc.update_object(leaf, f"<< /Names [<FEFF00E9> [{doc.page_xref(2)} 0 R /XYZ 40 700 0]] >>")
    names = doc.get_new_xref()
    doc.update_object(names, f"<< /Dests {leaf} 0 R >>")
    doc.xref_set_key(doc.pdf_catalog(), "Names", f"{names} 0 R")
    annot = doc.get_new_xref()
    doc.update_object(
        annot,
        "<< /Type /Annot /Subtype /Link /Rect [10 10 100 30] /Dest <FEFF00E9> >>",
    )
    doc.xref_set_key(doc.page_xref(0), "Annots", f"[{annot} 0 R]")
    doc.save(str(src))
    doc.close()

    vdoc = VirtualDocument.from_path(str(src))
    vdoc.move_pages([0], 3)
    out = tmp_path / "out.pdf"
    PyMuPDFEngine().materialize(vdoc, str(out))

    doc = fitz.open(out)
    links = doc[2].get_links()
    assert len(links) == 1, "the link was kept, where today's resolver drops it"
    assert read_destination(doc, links[0]["xref"], page_index_map(doc)) == \
        Destination(1, "/XYZ 40 700 0")
    doc.close()


def test_a_bookmark_whose_target_page_was_deleted_keeps_no_tail(tmp_path):
    """The bookmark itself is dropped, as it always was — the point is that nothing is carried
    onto whatever page inherits that index."""
    src = tmp_path / "in.pdf"
    doc = _doc(3)
    _set_outline(doc, f"[{doc.page_xref(2)} 0 R /XYZ 100 600 0]")
    doc.save(str(src))
    doc.close()

    vdoc = VirtualDocument.from_path(str(src))
    vdoc.delete_page(2)
    assert vdoc.remapped_toc() == []
    out = tmp_path / "out.pdf"
    PyMuPDFEngine().materialize(vdoc, str(out))
    doc = fitz.open(out)
    assert doc.get_toc() == []
    doc.close()


def test_a_save_that_moves_no_page_does_not_touch_the_outline(tmp_path):
    """M116's route writes no outline at all, so the file's own destinations stay untouched —
    the carry-through must not have given it a reason to start."""
    src = tmp_path / "in.pdf"
    doc = _doc(3)
    _set_outline(doc, f"[{doc.page_xref(2)} 0 R /FitR 50 500 300 650]")
    doc.save(str(src))
    doc.close()

    vdoc = VirtualDocument.from_path(str(src))
    out = tmp_path / "out.pdf"
    PyMuPDFEngine().materialize(vdoc, str(out))
    doc = fitz.open(out)
    assert _raw_dest(doc, outline_item_xrefs(doc)[0]) == \
        f"[{doc.page_xref(2)} 0 R/FitR 50 500 300 650]"
    doc.close()


def test_apply_outline_destinations_writes_nothing_when_the_counts_disagree():
    """The guard that keeps a bookmark from being given another bookmark's destination."""
    doc = _doc(3)
    toc = [[1, "A", 1, {"kind": 1, "page": 0, "zoom": 0.0, RAW_TAIL_KEY: "/Fit"}]]
    doc.set_toc(toc)
    assert apply_outline_destinations(doc, toc) == 1
    assert apply_outline_destinations(doc, toc + list(toc)) == 0
    doc.close()


def test_write_destination_clears_the_action_a_reader_would_have_preferred():
    """``/A`` wins over ``/Dest`` in a PDF reader, so leaving one behind writes a destination
    nobody uses — which is a change that tests green and does nothing."""
    doc = _doc(2)
    item = doc.get_new_xref()
    doc.update_object(item, f"<< /Title (B) /A << /S /GoTo /D [{doc.page_xref(0)} 0 R /Fit] >> >>")
    write_destination(doc, item, doc.page_xref(1), "/XYZ 10 20 0")
    assert doc.xref_get_key(item, "A")[0] == "null"
    assert read_destination(doc, item, page_index_map(doc)) == Destination(1, "/XYZ 10 20 0")
    doc.close()
