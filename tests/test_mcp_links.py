"""M138 — ``get_links``: the navigation structure a PDF already carries.

Tested at the helper level, like every other read tool (``tests/test_mcp_queries.py``): the PDF
behaviour is in :mod:`mcp_bridge.queries` and ``server.py`` is a schema adapter, so a bug in one
must not be able to hide in the other. The protocol side is at the bottom of this file.

Three things here are pinned because a plausible implementation gets them wrong, and each one was
measured against real documents before it was written down:

* **A ``gotor`` link's ``page`` is not a page in this document.** PyMuPDF reports one anyway, so
  the obvious ``entry["page"] + 1`` tells a caller a link jumps to *their* page 4 when it opens
  somebody else's file.
* **Anchor text has to come from word centres.** ``page.get_textbox(rect)`` clips, so on a stack of
  adjacent link rectangles it returns the neighbouring row's text as well — 96 of 710 link
  rectangles across three real documents. :meth:`PageText.struck` is closer but still counts a word
  that merely hangs into the rectangle (3 of the same 710).
* **The reply is bounded twice.** A count cap alone does not bound it: an entry runs 127-647
  characters and 502 real links serialise to 79,518.
"""

from __future__ import annotations

import json

import pymupdf as fitz
import pytest

from klarpdf.mcp_bridge import queries


@pytest.fixture
def linked_pdf(tmp_path) -> str:
    """Four pages whose first carries an internal jump, a web address, a link into another file,
    and a fourth rectangle sitting over blank space.

    Written with `insert_link` rather than hand-built xrefs, so the fixture stays honest about what
    PyMuPDF actually produces — including that a `LINK_LAUNCH` written this way is read back as a
    `gotor` carrying `page: 0`, which is exactly the trap `target_page` has to refuse.
    """
    path = str(tmp_path / "linked.pdf")
    doc = fitz.open()
    for _ in range(4):
        doc.new_page()
    page = doc[0]
    for y, label in ((100, "jump to page three"), (130, "not linked at all"),
                     (160, "the KlarPDF site"), (190, "another document")):
        page.insert_text((72, y), label, fontsize=11)
    doc[2].insert_text((72, 100), "this is page three", fontsize=11)
    page.insert_link({"kind": fitz.LINK_GOTO, "from": fitz.Rect(70, 88, 220, 104),
                      "page": 2, "to": fitz.Point(0, 0)})
    page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(70, 148, 220, 164),
                      "uri": "https://example.invalid/klarpdf"})
    page.insert_link({"kind": fitz.LINK_GOTOR, "from": fitz.Rect(70, 178, 220, 194),
                      "file": "elsewhere.pdf", "page": 3, "to": fitz.Point(0, 0)})
    # Over blank space in the lower half — the shape a photograph's link has in a magazine.
    page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(300, 400, 420, 500),
                      "uri": "https://example.invalid/picture"})
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def named_dest_pdf(tmp_path) -> str:
    """A document whose contents page links by **name**, the shape 591 of 621 links took on the
    manual this milestone was scoped from. PyMuPDF resolves the name to a page itself, which is
    what makes `get_links` enough on its own — no `/Names` tree walking."""
    path = str(tmp_path / "named.pdf")
    doc = fitz.open()
    for _ in range(3):
        doc.new_page()
    doc[0].insert_text((72, 100), "Chapter One", fontsize=11)
    # The name tree has to be built by hand: `insert_link` writes the /Dest name onto the link but
    # creates nothing for it to point at, and a name with no entry reads back with no `page` at all
    # (measured). A catalog `/Dests` dictionary is the older, simpler of the two forms the spec
    # allows and MuPDF resolves it — which is the property under test.
    dests = doc.get_new_xref()
    doc.update_object(dests, "<< /chapter-one [ %d 0 R /XYZ 0 792 0 ] >>" % doc.page_xref(2))
    doc.xref_set_key(doc.pdf_catalog(), "Dests", "%d 0 R" % dests)
    doc[0].insert_link({"kind": fitz.LINK_NAMED, "from": fitz.Rect(70, 88, 200, 104),
                        "name": "chapter-one", "page": 2, "to": fitz.Point(0, 0)})
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def contents_pdf(tmp_path) -> str:
    """A printed contents page: three stacked, abutting link rows over single-spaced text.

    The geometry that breaks `get_textbox` — the rows touch, so a clip-based read of one returns
    its neighbours too. Levels are the indent, as they are in a real manual.
    """
    path = str(tmp_path / "contents.pdf")
    doc = fitz.open()
    for _ in range(5):
        doc.new_page()
    page = doc[0]
    rows = [(100, 72, "Getting started", 1), (112, 96, "Charging the battery", 2),
            (124, 96, "Pairing a device", 3)]
    for y, x, title, target in rows:
        page.insert_text((x, y), title, fontsize=9)
        page.insert_link({"kind": fitz.LINK_GOTO, "from": fitz.Rect(x, y - 8, 400, y + 3),
                          "page": target, "to": fitz.Point(0, 0)})
    doc.save(path)
    doc.close()
    return path


# ---- the fields ---------------------------------------------------------------


def test_reports_one_entry_per_link_with_its_kind_named(linked_pdf):
    result = queries.links(linked_pdf)
    assert result["count"] == 4
    assert result["total_links"] == 4
    assert [entry["kind"] for entry in result["links"]] == ["goto", "uri", "gotor", "uri"]
    assert result["kinds"] == {"goto": 1, "gotor": 1, "uri": 2}
    assert result["pages_scanned"] == [1, 2, 3, 4]
    assert result["more_available"] is False


def test_an_internal_jump_resolves_to_a_one_based_target_page(linked_pdf):
    goto = next(e for e in queries.links(linked_pdf)["links"] if e["kind"] == "goto")
    assert goto["page"] == 1        # where the link sits
    assert goto["target_page"] == 3  # PyMuPDF reports index 2
    assert goto["uri"] is None and goto["file"] is None


def test_a_named_destination_resolves_without_walking_the_names_tree(named_dest_pdf):
    """The measured fact the milestone rests on: 591 of one manual's 621 links are `LINK_NAMED`,
    and PyMuPDF hands back a populated `page` for them."""
    (entry,) = queries.links(named_dest_pdf)["links"]
    assert entry["kind"] == "named"
    assert entry["target_page"] == 3
    assert entry["text"] == "Chapter One"


def test_a_web_address_is_reported_and_points_at_no_page(linked_pdf):
    uri = next(e for e in queries.links(linked_pdf)["links"] if e["uri"])
    assert uri["uri"] == "https://example.invalid/klarpdf"
    assert uri["target_page"] is None
    assert uri["text"] == "the KlarPDF site"


def test_a_link_into_another_file_never_claims_a_page_in_this_one(linked_pdf):
    """The trap. `get_links` returns `page: 3` for this link and that 3 is a page of
    `elsewhere.pdf`; reporting it as `target_page` would send a reader to the wrong document's
    content while looking entirely correct. `model/links_remap.py` makes the same restriction for
    the same reason, which is why an internal link survives a reorder and this one does not move.
    """
    remote = next(e for e in queries.links(linked_pdf)["links"] if e["kind"] == "gotor")
    assert remote["file"] == "elsewhere.pdf"
    assert remote["target_page"] is None


def test_a_link_over_no_text_reports_no_text_rather_than_being_dropped(linked_pdf):
    """A magazine links each contents entry twice — on its photograph and on its caption — and the
    photograph's rectangle covers nothing. Dropping it would be a judgement made invisibly; the
    caller dedupes by target and can only do that if both rows arrive."""
    blank = next(e for e in queries.links(linked_pdf)["links"]
                 if e["uri"] == "https://example.invalid/picture")
    assert blank["text"] is None


def test_the_rect_is_reported_in_the_space_search_and_clip_use(linked_pdf):
    goto = next(e for e in queries.links(linked_pdf)["links"] if e["kind"] == "goto")
    assert goto["rect"] == [70.0, 88.0, 220.0, 104.0]


# ---- anchor text: the reason PageText grew a word-centre lookup ----------------


def test_anchor_text_is_the_row_and_not_its_neighbours(contents_pdf):
    """Stacked, abutting link rows: each entry must read back as its own title alone."""
    titles = [entry["text"] for entry in queries.links(contents_pdf)["links"]]
    assert titles == ["Getting started", "Charging the battery", "Pairing a device"]


def test_get_textbox_would_have_returned_the_neighbouring_rows(contents_pdf):
    """The control for the test above — it proves the fixture can actually catch the defect.

    Without it a passing suite says nothing: if the rows did not overlap, both methods would agree
    and the guard would be vacuous. Here `get_textbox` clips, so at least one row comes back
    carrying text that belongs to another (96 of 710 rectangles on real documents).
    """
    doc = fitz.open(contents_pdf)
    page = doc[0]
    clipped = [page.get_textbox(link["from"]) for link in page.get_links()]
    doc.close()
    assert any(len(text.split("\n")) > 1 for text in clipped)


def test_a_word_hanging_into_the_rectangle_is_not_part_of_the_anchor(tmp_path):
    """Why the anchor is not read with :meth:`PageText.struck`, at the model level.

    `struck` asks which words a box *touches*, which is right for a search hit — MuPDF draws that
    rectangle tightly around part of a word, so a word the box only clips is still the match. An
    authored rectangle asks the opposite question: the words it *contains*. On a real prospectus a
    link covering `www.nseindia.com),` read back through `struck` as `and www.nseindia.com),`.
    """
    from klarpdf.model.page_text import PageText

    path = str(tmp_path / "hanging.pdf")
    doc = fitz.open()
    doc.new_page()
    doc[0].insert_text((72, 100), "see and www.example.invalid), page 4", fontsize=11)
    doc.save(path)
    doc.close()
    doc = fitz.open(path)
    page = doc[0]
    (rect,) = page.search_for("www.example.invalid),")
    text = PageText(page)
    # The link rectangle as an author draws it: a little wider than the words it labels, so it
    # reaches back into `and` without covering it. A search hit's box never does this — MuPDF fits
    # it to the glyphs — which is why the two lookups can disagree at all.
    (and_box,) = [word[:4] for word in text.words if word[4] == "and"]
    box = (and_box[2] - 2.0, rect.y0, rect.x1 + 2.0, rect.y1)
    struck = " ".join(word[4] for _i, word in text.struck(box))
    doc.close()
    assert text.word_text_under(box) == "www.example.invalid),"
    assert struck != text.word_text_under(box)   # the control: the geometry can catch it
    assert "and" in struck


def test_indent_survives_as_the_level_signal(contents_pdf):
    """What `set_outline` will be fed: `rect[0]` separates the two indents cleanly, which is how a
    manual's printed contents yields levels without any typography analysis."""
    indents = sorted({entry["rect"][0] for entry in queries.links(contents_pdf)["links"]})
    assert indents == [72.0, 96.0]


# ---- rotation: the space the rect is reported in (M138.1) ----------------------


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_the_rect_is_unrotated_at_every_rotation(tmp_path, rotation):
    """`search`, `redact_regions` and `clip` all work unrotated, so link rects must too.

    Unlike `get_annotations`, this one **is** a conversion: `page.get_links()` reports in
    *displayed* space, which turns with `/Rotate`, while `annot.rect` does not. The two APIs look
    like siblings and disagree, and no document in the test corpus has a rotated page — so nothing
    but this test can catch it (TC-017 FINDING 2, and the half of it the report could not see).
    """
    path = str(tmp_path / f"rot{rotation}.pdf")
    box = fitz.Rect(70, 88, 220, 104)
    doc = fitz.open()
    page = doc.new_page(width=400, height=700)
    page.insert_text((72, 100), "ROTATED-ANCHOR", fontsize=12)
    page.insert_link({"kind": fitz.LINK_URI, "from": box, "uri": "https://example.invalid/rot"})
    page.set_rotation(rotation)
    doc.save(path)
    doc.close()

    (entry,) = queries.links(path)["links"]
    assert entry["rect"] == pytest.approx(list(box), abs=0.05)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_anchor_text_survives_rotation(tmp_path, rotation):
    """The silent half of the same defect, and the reason the fix belongs in one place.

    `PageText` indexes `get_text("words")` in unrotated space, so before the derotation no word
    centre could fall inside a rotated link's rectangle and every anchor on a rotated page came
    back `null` — a wrong answer that looks exactly like the documented, legitimate "this link
    covers no words" case.
    """
    path = str(tmp_path / f"rot{rotation}.pdf")
    doc = fitz.open()
    page = doc.new_page(width=400, height=700)
    page.insert_text((72, 100), "ROTATED-ANCHOR", fontsize=12)
    page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(70, 88, 220, 104),
                      "uri": "https://example.invalid/rot"})
    page.set_rotation(rotation)
    doc.save(path)
    doc.close()

    (entry,) = queries.links(path)["links"]
    assert entry["text"] == "ROTATED-ANCHOR"


def test_a_link_rect_feeds_search_geometry_on_a_rotated_page(tmp_path):
    """The hand-off the conversion exists for, asserted rather than reasoned about.

    "Redact every external link on this page" is the tool's own privacy use case, and it is a
    composition: the rect goes to `redact_regions`, which works unrotated. Comparing against
    `search_for` on the same words is the check that the two agree.
    """
    path = str(tmp_path / "rot90.pdf")
    doc = fitz.open()
    page = doc.new_page(width=400, height=700)
    page.insert_text((72, 100), "ROTATED-ANCHOR", fontsize=12)
    page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(70, 88, 220, 104),
                      "uri": "https://example.invalid/rot"})
    page.set_rotation(90)
    doc.save(path)
    doc.close()

    (entry,) = queries.links(path)["links"]
    doc = fitz.open(path)
    (hit,) = doc[0].search_for("ROTATED-ANCHOR")
    doc.close()
    assert entry["rect"][0] <= hit.x0 and hit.x1 <= entry["rect"][2]
    assert entry["rect"][1] <= hit.y0 + 1 and hit.y1 <= entry["rect"][3] + 1


# ---- a /Link that points nowhere (M138.1) --------------------------------------


@pytest.fixture
def dead_hotspot_pdf(tmp_path) -> str:
    """A `/Link` annotation with a `/Rect` but no `/A` and no `/Dest`, beside a live one.

    The shape of `kasaragodhr.pdf`'s obj 597, found by TC-017: a dead hotspot a designer left
    behind, referenced from the page's `/Annots` and reachable, pointing at nothing. Built by hand
    because `insert_link` will not write one — which is also why no fixture had this shape before.
    """
    path = str(tmp_path / "dead.pdf")
    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    doc[0].insert_text((72, 100), "live link", fontsize=11)
    doc[0].insert_link({"kind": fitz.LINK_GOTO, "from": fitz.Rect(70, 88, 200, 104),
                        "page": 1, "to": fitz.Point(0, 0)})
    doc.save(path)
    doc.close()

    doc = fitz.open(path)
    xref = doc.get_new_xref()
    doc.update_object(xref, "<< /Type/Annot /Subtype/Link /Rect [70 118 200 134] /Border[0 0 0] >>")
    kind, array = doc.xref_get_key(doc.page_xref(0), "Annots")
    assert kind == "array"
    doc.xref_set_key(doc.page_xref(0), "Annots", f"{array[:-1]} {xref} 0 R]")
    doc.saveIncr()
    doc.close()
    return path


def test_a_link_with_no_destination_is_not_a_row(dead_hotspot_pdf):
    """PyMuPDF omits it and that is the right default — it is not a place the document points, and
    surfacing it in a privacy audit would be noise."""
    result = queries.links(dead_hotspot_pdf)
    assert result["total_links"] == 1
    assert result["links"][0]["kind"] == "goto"


def test_a_link_with_no_destination_is_counted_so_the_arithmetic_closes(dead_hotspot_pdf):
    """The gap TC-017 FINDING 1 is about: dropping it is right, dropping it *silently* is not.

    An auditor reconciling this reply against a raw `/Subtype/Link` count found 156 against 157 on
    a real document with nothing in the reply to explain the difference. Now the reply carries it.
    """
    result = queries.links(dead_hotspot_pdf)
    assert result["links_without_action"] == 1
    assert result["total_links"] + result["links_without_action"] == 2   # the raw /Link count


def test_an_ordinary_document_reports_no_action_less_links(linked_pdf):
    """The common case stays quiet: a number that is almost always 0 must actually be 0."""
    assert queries.links(linked_pdf)["links_without_action"] == 0


def test_none_is_not_offered_as_a_filter_because_it_can_never_match(dead_hotspot_pdf):
    """It was advertised in the error message and matched nothing on the very document that has an
    action-less link — the same false statement `_resolve_kinds` exists to prevent, made by the
    error message instead of the reply (TC-017 FINDING 1)."""
    with pytest.raises(ValueError) as excinfo:
        queries.links(dead_hotspot_pdf, kinds=["none"])
    message = str(excinfo.value)
    assert "'none'" in message
    assert "none" not in message.split("has ")[1]


# ---- narrowing ----------------------------------------------------------------


def test_pages_narrows_the_scan_and_says_what_it_read(linked_pdf):
    result = queries.links(linked_pdf, pages=[2, 3])
    assert result["count"] == 0
    assert result["pages_scanned"] == [2, 3]


def test_an_out_of_range_page_is_an_error_not_a_clamp(linked_pdf):
    with pytest.raises(ValueError, match="out of range"):
        queries.links(linked_pdf, pages=[9])


def test_kinds_filters_the_entries_but_not_the_counts(linked_pdf):
    """`kinds` is what makes "list the external links" cheap — 502 links to 37 on a real
    prospectus. The per-kind census stays whole so a filtered call still says what it skipped."""
    result = queries.links(linked_pdf, kinds=["uri"])
    assert result["count"] == 2
    assert result["total_links"] == 2
    assert all(entry["kind"] == "uri" for entry in result["links"])
    assert result["kinds"] == {"goto": 1, "gotor": 1, "uri": 2}


def test_kinds_accepts_several(linked_pdf):
    result = queries.links(linked_pdf, kinds=["goto", "named"])
    assert [entry["kind"] for entry in result["links"]] == ["goto"]


def test_an_unknown_kind_is_an_error_naming_the_real_ones(linked_pdf):
    """`count: 0` would be a false statement about the document — M106's rule, which the annotation
    palette settled: a name outside the set is a mistake, not a silent empty result."""
    with pytest.raises(ValueError) as excinfo:
        queries.links(linked_pdf, kinds=["external"])
    message = str(excinfo.value)
    assert "'external'" in message
    for kind in ("goto", "named", "uri", "gotor"):
        assert kind in message


# ---- the two caps and the pager -----------------------------------------------


def test_the_count_cap_pages_rather_than_truncating(linked_pdf):
    first = queries.links(linked_pdf, max_links=3)
    assert first["count"] == 3
    assert first["total_links"] == 4
    assert first["more_available"] is True
    assert "offset: 3" in first["warnings"][0]
    rest = queries.links(linked_pdf, max_links=3, offset=first["offset"] + first["count"])
    assert rest["count"] == 1
    assert rest["more_available"] is False
    assert rest["links"][0] == queries.links(linked_pdf)["links"][3]


def test_the_character_cap_bounds_a_reply_the_count_cap_would_let_through(linked_pdf):
    """The bound `get_annotations` learned at 139,288 characters, applied here before it could
    bite: 500 links is a number, not a size."""
    result = queries.links(linked_pdf, max_chars=400)
    assert result["count"] < 4
    assert result["more_available"] is True
    assert len(json.dumps(result["links"])) <= 400 + len(json.dumps(result["links"][-1]))


def test_a_single_oversized_link_is_still_returned(linked_pdf):
    """Or a correctly-paging caller never terminates: an empty batch that says there is more is an
    infinite loop, so a batch always yields at least one entry however small the budget."""
    result = queries.links(linked_pdf, max_chars=1)
    assert result["count"] == 1
    assert result["more_available"] is True


def test_paging_all_the_way_through_returns_every_link_exactly_once(linked_pdf):
    everything = queries.links(linked_pdf)["links"]
    walked: list[dict] = []
    offset = 0
    while True:
        batch = queries.links(linked_pdf, max_links=1, offset=offset)
        walked.extend(batch["links"])
        if not batch["more_available"]:
            break
        offset += batch["count"]
    assert walked == everything


def test_a_negative_offset_is_rejected(linked_pdf):
    with pytest.raises(ValueError, match="offset must be >= 0"):
        queries.links(linked_pdf, offset=-1)


# ---- a document with nothing to report ----------------------------------------


def test_a_document_with_no_links_answers_cleanly(a_pdf):
    result = queries.links(a_pdf)
    assert result["count"] == 0
    assert result["total_links"] == 0
    assert result["kinds"] == {}
    assert result["links"] == []
    assert result["more_available"] is False


# ---- the protocol side --------------------------------------------------------


def test_the_tool_is_registered_and_reaches_the_helper(linked_pdf):
    """Through the server, so the schema adapter is exercised as well as the helper."""
    import asyncio

    from klarpdf.mcp_bridge.server import create_server

    server = create_server()
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert "get_links" in names
    result = asyncio.run(server.call_tool("get_links", {"path": linked_pdf, "kinds": ["uri"]}))
    # One content block, not one per link — the reason every list-returning tool here wraps its
    # list in a dict (see `server.py`'s module docstring).
    assert len(result.content) == 1
    assert json.loads(result.content[0].text)["count"] == 2


def test_a_read_only_build_still_serves_it(linked_pdf):
    """It reads and writes nothing, so `--read-only` must not withhold it."""
    import asyncio

    from klarpdf.mcp_bridge.config import Config
    from klarpdf.mcp_bridge.server import create_server

    server = create_server(Config(read_only=True))
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert "get_links" in names
