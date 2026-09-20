"""M139 — `set_outline`: the sink for the structure M138 reads out of a document.

Three claims are being tested, and they fail in different ways.

**It writes what was asked and nothing else.** The entry shape is `get_outline`'s, so the strongest
statement of correctness is a round trip: write entries, read them back with the *reader*, get the
same list. That also pins the two halves together — a change to either that broke the agreement
would fail here rather than in a document six months later.

**It refuses rather than guesses.** The measurements in `set_outline_override`'s docstring are the
reason: `set_toc` does not reject a page the document does not have, it silently clamps to the
nearest real one or writes a bookmark that navigates nowhere, and reports success either way. Every
refusal below is checked for *both* halves — the error raised, and no file left behind — because a
refusal that still wrote something is not a refusal.

**The write preserves the document.** Catalog-only, so encryption, permissions and the page content
all come through, and on a document with no outline of its own it is an append: the original bytes
are still the first N bytes of the output. The append/rewrite fork is asserted directly rather than
through a byte count, since a byte count that happened to match would hide a rewrite.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pymupdf as fitz
import pytest

from klarpdf.mcp_bridge import queries, transforms as T

#: `top` is what a bookmark written with no position of its own reads back as: the PDF layer
#: writes a real spot 36 pt below the page's top edge for "open this page", so the file says 36
#: and `get_outline` reports what the file says (M150.2).
_PAGE_TOP = 36.0

ENTRIES = [
    {"level": 1, "title": "Introduction", "page": 1, "top": _PAGE_TOP},
    {"level": 2, "title": "Background", "page": 3, "top": _PAGE_TOP},
    {"level": 1, "title": "Chapter Two", "page": 8, "top": _PAGE_TOP},
]


def _make(path: str, pages: int = 10, toc: list | None = None, encrypt: bool = False) -> str:
    doc = fitz.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 100), f"Page {i + 1} " + "lorem ipsum " * 30)
    if toc:
        doc.set_toc(toc)
    if encrypt:
        doc.save(
            path,
            encryption=fitz.PDF_ENCRYPT_AES_256,
            owner_pw="owner",
            permissions=int(fitz.PDF_PERM_ACCESSIBILITY | fitz.PDF_PERM_PRINT),
        )
    else:
        doc.save(path)
    doc.close()
    return path


# ---- what it writes ------------------------------------------------------------


def test_entries_round_trip_through_the_reader(tmp_path):
    """`set_outline` writes what `get_outline` reads. Two shapes or one — this decides it."""
    src = _make(str(tmp_path / "src.pdf"))
    out = str(tmp_path / "out.pdf")
    result = T.set_outline(src, ENTRIES, out)

    assert result["entries"] == 3
    assert result["replaced"] == 0
    assert queries.outline(out) == ENTRIES


def test_the_written_outline_is_a_real_goto_that_survives_a_later_reorder(tmp_path):
    """The bookmarks are direct GoTo destinations, so `remap_toc` can move them (M138.4).

    This is the property the milestone was scoped around — "a TOC we write stays correct through a
    later reorder". A named destination would read back looking identical here and break on the
    move, which is exactly how M138.4's defect hid, so the reorder is the assertion.
    """
    src = _make(str(tmp_path / "src.pdf"))
    written = T.set_outline(src, ENTRIES, str(tmp_path / "w.pdf"))["out"]

    reversed_out = T.reorder(written, list(range(10, 0, -1)), str(tmp_path / "r.pdf"))["out"]
    assert queries.outline(reversed_out) == [
        {"level": 1, "title": "Introduction", "page": 10, "top": _PAGE_TOP},
        {"level": 2, "title": "Background", "page": 8, "top": _PAGE_TOP},
        {"level": 1, "title": "Chapter Two", "page": 3, "top": _PAGE_TOP},
    ]


def test_an_existing_outline_is_not_replaced_without_being_asked(tmp_path):
    """The refusal, and the reason it is a refusal rather than a warning.

    Deciding what an *enriched* outline should say is a judgement about meaning, so this tool never
    merges — that onus is the caller's. But making sure the caller knowingly declined to enrich is
    this tool's job, and a `replaced` count in the reply discharges it only for an agent that reads
    the field. It is the same argument `_resolve_out` already makes about an existing output file:
    refuse, and let an agent that meant it say so in one word.

    The error has to name the count, because "how much am I about to lose" is the thing that
    changes the caller's mind.
    """
    src = _make(str(tmp_path / "src.pdf"), toc=[[1, "Old One", 1], [1, "Old Two", 5]])
    out = str(tmp_path / "out.pdf")

    with pytest.raises(ValueError, match="already has an outline of 2 bookmark"):
        T.set_outline(src, ENTRIES, out)
    assert not os.path.exists(out)
    assert queries.outline(src) == [
        {"level": 1, "title": "Old One", "page": 1, "top": _PAGE_TOP},
        {"level": 1, "title": "Old Two", "page": 5, "top": _PAGE_TOP},
    ]


def test_a_document_with_no_outline_never_sees_the_argument(tmp_path):
    """The case the tool was built for — a manual with zero bookmarks — pays nothing for the guard.

    Worth its own test: a refusal that fired on every document would have made the headline use
    case (146 pages, no bookmarks, structure only in its link annotations) need a flag to do the
    one thing it exists to do.
    """
    src = _make(str(tmp_path / "src.pdf"))
    result = T.set_outline(src, ENTRIES, str(tmp_path / "out.pdf"))

    assert result["replaced"] == 0
    assert result["entries"] == 3


def test_replace_outline_discards_the_existing_tree(tmp_path):
    src = _make(str(tmp_path / "src.pdf"), toc=[[1, "Old One", 1], [1, "Old Two", 5]])
    out = str(tmp_path / "out.pdf")
    result = T.set_outline(src, ENTRIES, out, replace_outline=True)

    assert result["replaced"] == 2
    assert queries.outline(out) == ENTRIES


def test_enriching_is_get_outline_plus_concatenation(tmp_path):
    """The workflow the refusal points at, asserted end to end.

    The shapes being identical is what makes this one `+` rather than a translation layer, and that
    is the property M139 was designed around — so it is worth a test that would fail if either side
    of the round trip drifted.
    """
    src = _make(str(tmp_path / "src.pdf"), toc=[[1, "Chapter One", 1], [1, "Chapter Two", 6]])

    existing = queries.outline(src)
    merged = [
        existing[0],
        {"level": 2, "title": "Section 1.1", "page": 3},
        existing[1],
        {"level": 2, "title": "Section 2.1", "page": 7},
    ]
    out = T.set_outline(src, merged, str(tmp_path / "out.pdf"), replace_outline=True)["out"]

    assert queries.outline(out) == [
        {"level": 1, "title": "Chapter One", "page": 1, "top": _PAGE_TOP},
        {"level": 2, "title": "Section 1.1", "page": 3, "top": _PAGE_TOP},
        {"level": 1, "title": "Chapter Two", "page": 6, "top": _PAGE_TOP},
        {"level": 2, "title": "Section 2.1", "page": 7, "top": _PAGE_TOP},
    ]


# ---- levels are repaired, and the repair is reported ---------------------------


def test_levels_are_normalised_and_the_reply_says_which(tmp_path):
    """A contents page that opens at the second indent is ordinary, so this is repaired rather than
    refused — but silently repairing it would hide a genuine mistake, which is what the report is
    for. Note the second entry: a jump from 2 to 4 is not flattened onto its neighbour, it is
    repaired to the depth its nesting implies.
    """
    src = _make(str(tmp_path / "src.pdf"))
    out = str(tmp_path / "out.pdf")
    result = T.set_outline(
        src,
        [
            {"level": 2, "title": "Opens deep", "page": 1},
            {"level": 4, "title": "Deeper still", "page": 2},
            {"level": 2, "title": "Back up", "page": 3},
        ],
        out,
    )

    assert [e["level"] for e in queries.outline(out)] == [1, 2, 1]
    assert [(e["index"], e["from"], e["to"]) for e in result["levels_normalised"]] == [
        (0, 2, 1),
        (1, 4, 2),
        (2, 2, 1),
    ]
    assert len(result["warnings"]) == 1


def test_a_well_formed_outline_reports_no_normalisation(tmp_path):
    """The report is a signal, so it has to be absent when there is nothing to signal."""
    src = _make(str(tmp_path / "src.pdf"))
    result = T.set_outline(src, ENTRIES, str(tmp_path / "out.pdf"))

    assert "levels_normalised" not in result
    assert "warnings" not in result


# ---- what it refuses -----------------------------------------------------------


@pytest.mark.parametrize(
    "entries, expected",
    [
        ([{"level": 1, "title": "X", "page": 99}], "page 99"),
        ([{"level": 1, "title": "X", "page": 0}], "page 0"),
        ([{"level": 1, "title": "X", "page": -1}], "page -1"),
        ([{"level": 1, "title": "   ", "page": 1}], "no title"),
        ([{"level": 0, "title": "X", "page": 1}], "level 0"),
        ([{"level": 1, "text": "X", "page": 1}], "unknown key"),
        ([{"level": 1, "title": "X"}], "missing"),
        ([{"level": 1, "title": "X", "page": True}], "must be an integer"),
        (["Introduction"], "not an object"),
        ([], "empty"),
    ],
)
def test_a_bad_entry_is_refused_and_writes_nothing(tmp_path, entries, expected):
    """Every refusal is checked twice: the message names the problem, and no output exists.

    The page cases are the ones that earn the test. `set_toc` accepts all three — measured on a
    6-page document, `99` writes a bookmark to the last page, `0` to the first, and `-1` one with
    no destination at all — and reports success, so an agent that miscounted would have no way to
    find out. The rest are the `fill_form` argument: a typo that writes nothing and reports
    success is the worst outcome available.
    """
    src = _make(str(tmp_path / "src.pdf"))
    out = str(tmp_path / "out.pdf")

    with pytest.raises(ValueError, match=expected):
        T.set_outline(src, entries, out)
    assert not os.path.exists(out)


def test_it_refuses_to_write_over_its_input(tmp_path):
    """The safety model is per-tool, not inherited by hope — `_resolve_out` has to be reached."""
    src = _make(str(tmp_path / "src.pdf"))
    with pytest.raises(ValueError, match="refusing to write over the input"):
        T.set_outline(src, ENTRIES, src)


# ---- what the write preserves --------------------------------------------------


def test_a_document_with_no_outline_is_appended_to_not_rewritten(tmp_path):
    """The headline case (PLAN.md §M139): a manual with zero bookmarks gains navigation for the
    cost of a few kilobytes on the end.

    The assertion is that the original bytes are *still there, unmoved* — a size comparison would
    pass on a rewrite that happened to be small, and it is the untouched prefix, not the size, that
    makes the claim in the tool's description true.
    """
    src = _make(str(tmp_path / "src.pdf"), pages=40)
    original = open(src, "rb").read()
    out = T.set_outline(src, ENTRIES, str(tmp_path / "out.pdf"))["out"]

    written = open(out, "rb").read()
    assert written[: len(original)] == original
    assert len(written) > len(original)


def test_replacing_an_outline_rewrites_so_the_old_titles_do_not_survive(tmp_path):
    """The other side of that fork, and the reason it is a fork.

    An append cannot take anything away: the entries being replaced would stay in the revision
    underneath, and their titles would be readable in the output's bytes. That is the same argument
    `edits_are_additive` already makes about a removed mark, asked of a different object — so a
    document that has an outline pays for a rewrite, and gets a file that means it.
    """
    src = _make(str(tmp_path / "src.pdf"), toc=[[1, "CODENAME BLUEBIRD", 1]])
    out = T.set_outline(src, ENTRIES, str(tmp_path / "out.pdf"), replace_outline=True)["out"]

    assert b"BLUEBIRD" not in open(out, "rb").read()
    assert queries.outline(out) == ENTRIES


def test_encryption_and_permissions_survive(tmp_path):
    """Catalog-only, so the `insert_pdf` graft hazard does not apply (CLAUDE.md §Gotchas).

    This is what makes the tool usable on the documents that most need it: a published manual is
    routinely owner-password restricted, and handing back a permissive copy of it would be a
    different document. `metadata["encryption"]` is asked, never `is_encrypted` — an
    owner-restricted file that opens without a password reports `False` there.
    """
    src = _make(str(tmp_path / "src.pdf"), encrypt=True)
    out = T.set_outline(src, ENTRIES, str(tmp_path / "out.pdf"))["out"]

    doc = fitz.open(out)
    try:
        assert doc.metadata["encryption"] == "Standard V5 R6 256-bit AES"
        assert doc.permissions == fitz.open(src).permissions
        assert len(doc.get_toc()) == 3
    finally:
        doc.close()


def test_a_password_protected_document_keeps_its_password(tmp_path):
    """The other encryption case, and the one that cannot append at all.

    `origin_bytes()` is `None` for a document that needed a password — it is held decrypted (M32)
    and a save re-encrypts from that copy (M54), which an incremental write cannot do. So this
    necessarily takes the full rewrite, and the thing worth pinning is that the outline still lands
    and the output still demands the password: a copy that opened freely would be a different
    document, however correct its bookmarks.
    """
    src = str(tmp_path / "locked.pdf")
    doc = fitz.open()
    for i in range(8):
        doc.new_page().insert_text((72, 100), f"Page {i + 1}")
    doc.save(src, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    doc.close()

    out = T.set_outline(src, ENTRIES[:2], str(tmp_path / "out.pdf"), password="secret")["out"]

    written = fitz.open(out)
    try:
        assert written.needs_pass
        assert written.authenticate("secret")
        assert written.get_toc() == [[1, "Introduction", 1], [2, "Background", 3]]
    finally:
        written.close()


def test_the_page_content_is_untouched(tmp_path):
    """An outline is navigation. Nothing on any page may move because of it."""
    src = _make(str(tmp_path / "src.pdf"))
    out = T.set_outline(src, ENTRIES, str(tmp_path / "out.pdf"))["out"]

    before, after = fitz.open(src), fitz.open(out)
    try:
        assert after.page_count == before.page_count
        for i in range(before.page_count):
            assert after[i].get_text("text") == before[i].get_text("text")
    finally:
        before.close()
        after.close()


def test_the_source_is_never_touched(tmp_path):
    src = _make(str(tmp_path / "src.pdf"), toc=[[1, "Old", 1]])
    before = open(src, "rb").read()
    result = T.set_outline(src, ENTRIES, str(tmp_path / "out.pdf"), replace_outline=True)

    assert result["source_unchanged"] is True
    assert open(src, "rb").read() == before


# ---- a bookmark that lands on its heading (M150.2, #361) -----------------------


@pytest.fixture
def src10(tmp_path) -> str:
    """A plain ten-page document with no outline of its own."""
    return _make(str(tmp_path / "src10.pdf"))


def _tops(path) -> list:
    """Each bookmark's `top`, as `get_outline` reports it."""
    return [e["top"] for e in queries.outline(path)]


def test_an_entry_with_a_top_lands_where_it_says(tmp_path, src10):
    """FR-001: a reader zoomed in on a page with many short sections clicks the bookmark for one
    near the bottom, and the view jumps to the top of the page, above what they were reading.

    One report's 23-page agreement carried 104 bookmarks with 103 of them sharing a page; one page
    held twelve, and all twelve opened in the same place.
    """
    out = str(tmp_path / "positioned.pdf")
    T.set_outline(
        src10,
        [{"level": 1, "title": "Top of page 1", "page": 1, "top": 0.0},
         {"level": 1, "title": "Part way down 2", "page": 2, "top": 400.0},
         {"level": 2, "title": "Near the foot of 3", "page": 3, "top": 700.25}],
        out,
    )
    assert _tops(out) == [0.0, 400.0, 700.25]


def _raw_destinations(path) -> list[str]:
    """Each bookmark's destination exactly as the file spells it."""
    from klarpdf.model.destinations import outline_item_xrefs

    doc = fitz.open(path)
    try:
        out = []
        for xref in outline_item_xrefs(doc):
            dest = doc.xref_get_key(xref, "Dest")
            out.append(dest[1] if dest[0] != "null" else doc.xref_get_key(xref, "A")[1])
        return out
    finally:
        doc.close()


def _destination_tails(path) -> list[str]:
    """Each bookmark's destination with the page reference stripped — what it says about *where*."""
    return [re.sub(r"^\[\s*\d+\s+\d+\s+R\s*", "", d).rstrip("]").strip()
            for d in _raw_destinations(path)]


def test_leaving_top_out_writes_exactly_what_it_wrote_before(tmp_path, src10):
    """#361's own condition: *"Omitted `top` writes exactly what is written today, so existing
    callers are unaffected."*

    Compared by the **destination bytes** rather than by the whole file, because two saves of the
    same thing are never byte-identical: every PDF carries a pair of identifiers in its trailer and
    a fresh one is drawn each time. The destination is the thing this claim is about, and it is
    compared exactly.
    """
    entries = [{"level": 1, "title": "One", "page": 1}, {"level": 2, "title": "Two", "page": 4}]
    without = str(tmp_path / "without.pdf")
    explicit_none = str(tmp_path / "none.pdf")
    T.set_outline(src10, entries, without)
    T.set_outline(src10, [dict(e, top=None) for e in entries], explicit_none)

    assert _raw_destinations(without) == _raw_destinations(explicit_none)
    assert _tops(without) == _tops(explicit_none) == [_PAGE_TOP, _PAGE_TOP]


def test_a_mixed_list_is_independent_entry_by_entry(tmp_path, src10):
    out = str(tmp_path / "mixed.pdf")
    T.set_outline(
        src10,
        [{"level": 1, "title": "Positioned", "page": 1, "top": 250.0},
         {"level": 1, "title": "Not positioned", "page": 2},
         {"level": 1, "title": "Positioned too", "page": 3, "top": 111.5}],
        out,
    )
    assert _tops(out) == [250.0, _PAGE_TOP, 111.5]


def test_a_top_that_is_not_a_number_is_refused_and_nothing_is_written(tmp_path, src10):
    out = str(tmp_path / "bad.pdf")
    for bad in ("abc", [1], {"y": 2}, True):
        with pytest.raises(ValueError, match="top"):
            T.set_outline(
                src10, [{"level": 1, "title": "X", "page": 1, "top": bad}], out
            )
        assert not Path(out).exists()


def test_a_top_outside_the_page_is_written_and_reported(tmp_path, src10):
    """Owner's call, 2026-09-20: accept and warn rather than refuse.

    Refusing would reject documents that already work — 51 bookmarks and 232 links across the
    123-file corpus name a spot their own page does not contain, so `get_outline` → `set_outline`
    would fail on them.
    """
    out = str(tmp_path / "outside.pdf")
    result = T.set_outline(
        src10,
        [{"level": 1, "title": "Below the paper", "page": 1, "top": 5000.0},
         {"level": 1, "title": "Above it", "page": 2, "top": -40.0},
         {"level": 1, "title": "Fine", "page": 3, "top": 100.0}],
        out,
    )
    assert [o["title"] for o in result["tops_outside_the_page"]] == ["Below the paper", "Above it"]
    assert any("outside their page" in w for w in result["warnings"])
    assert _tops(out) == [5000.0, -40.0, 100.0]      # written as asked, not clamped


def test_a_round_trip_keeps_every_position(tmp_path, src10):
    """Read it, send it straight back, read it again — the point of `get_outline` returning `top`."""
    first = str(tmp_path / "first.pdf")
    T.set_outline(
        src10,
        [{"level": 1, "title": "A", "page": 1, "top": 90.0},
         {"level": 2, "title": "B", "page": 1, "top": 300.0},
         {"level": 1, "title": "C", "page": 5, "top": 640.75}],
        first,
    )
    entries = queries.outline(first)
    second = str(tmp_path / "second.pdf")
    T.set_outline(first, entries, second, replace_outline=True)
    assert queries.outline(second) == entries


def test_replacing_a_positioned_outline_without_tops_says_what_was_lost(tmp_path, src10):
    """#361 item 4. The old reply said `replaced: 104` and nothing else, and one report watched 85
    distinct positions collapse to 1 without a word."""
    positioned = str(tmp_path / "positioned.pdf")
    T.set_outline(
        src10,
        [{"level": 1, "title": "A", "page": 1, "top": 90.0},
         {"level": 1, "title": "B", "page": 2, "top": 300.0}],
        positioned,
    )
    out = str(tmp_path / "flattened.pdf")
    result = T.set_outline(
        positioned, [{"level": 1, "title": "Renamed", "page": 1}], out, replace_outline=True
    )
    assert result["positions_discarded"] == 2
    assert any("name no `top`" in w for w in result["warnings"])


def test_nothing_is_reported_as_lost_when_the_caller_is_setting_positions(tmp_path, src10):
    """A caller steering the positions themselves does not need to be told what the old outline
    happened to have — that is noise, not a warning."""
    positioned = str(tmp_path / "positioned.pdf")
    T.set_outline(
        src10, [{"level": 1, "title": "A", "page": 1, "top": 90.0}], positioned
    )
    result = T.set_outline(
        positioned, [{"level": 1, "title": "A", "page": 1, "top": 500.0}],
        str(tmp_path / "out.pdf"), replace_outline=True,
    )
    assert "positions_discarded" not in result


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_a_top_means_the_same_thing_on_a_rotated_page(tmp_path, rotation):
    """#361 item 1 asks for a defined answer at every rotation.

    `top` is measured on the page as it was authored, not as it is displayed — the same frame
    `get_heading_candidates` reports a `bbox` in, so a heading's box feeds in unchanged whatever
    the page's rotation.
    """
    src = str(tmp_path / f"rot{rotation}.pdf")
    doc = fitz.open()
    for _ in range(3):
        doc.new_page(width=612, height=792)
    if rotation:
        doc[1].set_rotation(rotation)
    doc.save(src)
    doc.close()

    out = str(tmp_path / f"out{rotation}.pdf")
    T.set_outline(src, [{"level": 1, "title": "Spot", "page": 2, "top": 250.0}], out)
    assert _tops(out) == [250.0]


def test_a_top_on_a_page_whose_printed_area_is_inset_lands_on_the_page(tmp_path):
    """#361 acceptance test 7. A page can be trimmed so its visible area starts inside the paper;
    `top` is measured from the top of what the reader sees, not from the edge of the paper."""
    src = str(tmp_path / "cropped.pdf")
    doc = fitz.open()
    for _ in range(3):
        doc.new_page(width=612, height=792)
    doc[1].set_cropbox(fitz.Rect(20, 30, 592, 762))
    doc.save(src)
    doc.close()

    out = str(tmp_path / "out.pdf")
    T.set_outline(src, [{"level": 1, "title": "Spot", "page": 2, "top": 100.0}], out)
    assert _tops(out) == [100.0]


def test_a_written_bookmark_never_names_a_left_edge(tmp_path, src10):
    """Owner's rule, 2026-09-20: *"A bookmark or link should only control the vertical position
    within a document, clicking on it should not result in horzontal scroll."*

    A PDF destination can name a left edge beside a height, and a viewer that honours one slides
    the page sideways under a reader who did not ask for it. So the written destination leaves the
    left blank — the word `null` below is the file saying "keep whatever sideways position the
    reader has". There is no `left` key on an entry, so a caller cannot ask for one either, and
    this pins the value actually written rather than the absence of the argument.
    """
    out = str(tmp_path / "noleft.pdf")
    T.set_outline(
        src10,
        [{"level": 1, "title": "A", "page": 1, "top": 90.0},
         {"level": 1, "title": "B", "page": 2, "top": 250.5}],
        out,
    )
    # Compared without the page reference in front, whose object number is the document's own
    # business. `_make` builds A4 pages (842 pt tall), so a height of 90 from the top is 752 up
    # from the bottom, which is how a PDF measures. The middle value is the one under test.
    assert _destination_tails(out) == ["/XYZ null 752 0", "/XYZ null 591.5 0"]
    assert _tops(out) == [90.0, 250.5]


def test_an_entry_may_not_ask_for_a_left_edge(tmp_path, src10):
    """The other half: `left` is not a key an entry has, and an unknown key is refused rather than
    ignored — so a caller who tries gets told, instead of silently getting a bookmark that behaves
    differently from the one they asked for."""
    with pytest.raises(ValueError, match=r"unknown key\(s\) \['left'\]"):
        T.set_outline(
            src10, [{"level": 1, "title": "A", "page": 1, "top": 90.0, "left": 72.0}],
            str(tmp_path / "left.pdf"),
        )
    assert not Path(tmp_path / "left.pdf").exists()
