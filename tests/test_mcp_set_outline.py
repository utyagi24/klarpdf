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

import pymupdf as fitz
import pytest

from klarpdf.mcp_bridge import queries, transforms as T

ENTRIES = [
    {"level": 1, "title": "Introduction", "page": 1},
    {"level": 2, "title": "Background", "page": 3},
    {"level": 1, "title": "Chapter Two", "page": 8},
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
        {"level": 1, "title": "Introduction", "page": 10},
        {"level": 2, "title": "Background", "page": 8},
        {"level": 1, "title": "Chapter Two", "page": 3},
    ]


def test_an_existing_outline_is_replaced_not_merged(tmp_path):
    src = _make(str(tmp_path / "src.pdf"), toc=[[1, "Old One", 1], [1, "Old Two", 5]])
    out = str(tmp_path / "out.pdf")
    result = T.set_outline(src, ENTRIES, out)

    assert result["replaced"] == 2
    assert queries.outline(out) == ENTRIES


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
    out = T.set_outline(src, ENTRIES, str(tmp_path / "out.pdf"))["out"]

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
    result = T.set_outline(src, ENTRIES, str(tmp_path / "out.pdf"))

    assert result["source_unchanged"] is True
    assert open(src, "rb").read() == before
