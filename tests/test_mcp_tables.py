"""M141 — ``get_tables``: read a table when the document says where its cells are, decline otherwise.

Tested at the helper level like every other read tool (``tests/test_mcp_queries.py``): the PDF
behaviour is in :mod:`mcp_bridge.tables` and ``server.py`` is a schema adapter, so a bug in one must
not be able to hide in the other. The protocol side is at the bottom.

The fixtures are built to carry the shapes that a plausible implementation gets wrong, each one
drawn from a real document that got it wrong first:

* **A fully ruled table and a row-ruled one need different readers**, and the row-ruled shape (a
  rule above and below the header, no column lines) is the common filing layout the original design
  had no reader for at all.
* **A caption is often not the nearest thing above its table** — the report shape is heading, then
  an introductory paragraph, then the table, so a nearest-block rule returns the paragraph.
* **A column heading passes every text test a caption passes.** Only its horizontal position
  separates them.
* **Two tables can share a header and column positions exactly and be different tables**, so
  continuation must be flagged rather than applied.
* **One region can hold several tables** when the ruling runs the width of the page; the prose
  between them is the boundary.
"""

from __future__ import annotations

import asyncio

import pymupdf as fitz
import pytest

from klarpdf.mcp_bridge import queries, tables


def _rule(page: fitz.Page, y: float, x0: float = 50, x1: float = 400) -> None:
    page.draw_line(fitz.Point(x0, y), fitz.Point(x1, y), width=0.7)


@pytest.fixture
def ruled_pdf(tmp_path) -> str:
    """One page with a fully boxed-in grid — the shape ``lines_strict`` reads exactly."""
    path = str(tmp_path / "ruled.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 90), "Quarterly totals", fontsize=13)
    rows = [("Region", "Q1", "Q2"), ("North", "120", "140"), ("South", "90", "110")]
    top, height, edges = 110.0, 24.0, [50.0, 200.0, 300.0, 400.0]
    for index, row in enumerate(rows):
        y = top + index * height
        _rule(page, y, edges[0], edges[-1])
        for column, value in enumerate(row):
            page.insert_text((edges[column] + 4, y + 16), value, fontsize=10)
    _rule(page, top + len(rows) * height, edges[0], edges[-1])
    for edge in edges:
        page.draw_line(
            fitz.Point(edge, top), fitz.Point(edge, top + len(rows) * height), width=0.7
        )
    doc.save(path)
    doc.close()
    return path


# Real filing tables run to dozens of rows, and the fixtures must too: the column inference needs
# `_MIN_WORDS_VERTICAL` words stacked in a column before it will believe a boundary, so a three-row
# stand-in produces no table at all and would test nothing. Ten officers is the smallest honest size.
_OFFICERS = ["Ricks", "Hakim", "Montarce", "Skovronsky", "Naarden",
             "Ashkenazi", "Bourla", "Coxe", "Dabiri", "Hudson"]


def _write_row_ruled(page: fitz.Page, top: float, rows, edges) -> float:
    """Rows separated by drawn rules, columns by alignment only — the filing shape."""
    height = 22.0
    # The rules must reach past the *last* column's text, not stop at its left edge, or the final
    # column falls outside the ruled band and is never seen as part of the table.
    right = edges[-1] + 100
    _rule(page, top, edges[0], right)
    for index, row in enumerate(rows):
        y = top + index * height
        for column, value in enumerate(row):
            page.insert_text((edges[column] + 4, y + 15), value, fontsize=10)
        _rule(page, y + height, edges[0], right)
    return top + len(rows) * height


@pytest.fixture
def captioned_pdf(tmp_path) -> str:
    """Two row-ruled tables, each introduced by a caption *and then a paragraph*.

    The shape LLY's proxy page 56 takes twice, and the one that defeats "nearest block above": the
    paragraph sits between the caption and the table, so the nearest block is never the title.
    """
    path = str(tmp_path / "captioned.pdf")
    doc = fitz.open()
    page = doc.new_page()
    edges = [50.0, 200.0, 300.0]

    page.insert_text((50, 70), "Annual Cash Bonus Targets", fontsize=12)
    # A real introductory paragraph, at a real length. A short stand-in would read as a caption
    # itself and the test would pass for the wrong reason.
    page.insert_text(
        (50, 92),
        "After reviewing internal relativity, peer group data and individual performance, the",
        fontsize=9,
    )
    page.insert_text(
        (50, 106),
        "committee maintained the same percent-of-salary targets for every named officer.",
        fontsize=9,
    )
    bonus = [("Name", "2024", "2025")] + [
        (name, f"{100 + index * 5}%", f"{125 + index * 5}%")
        for index, name in enumerate(_OFFICERS)
    ]
    bottom = _write_row_ruled(page, 120.0, bonus, edges)

    page.insert_text((50, bottom + 40), "Stock Incentives", fontsize=12)
    page.insert_text(
        (50, bottom + 62),
        "The committee set grant target values based on internal relativity, peer group data",
        fontsize=9,
    )
    page.insert_text(
        (50, bottom + 76),
        "and individual performance, allocated equally between the two award types.",
        fontsize=9,
    )
    grants = [("Name", "2024", "2025")] + [
        (name, f"{3 + index},750", f"{6 + index},325") for index, name in enumerate(_OFFICERS)
    ]
    _write_row_ruled(page, bottom + 90, grants, edges)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def straddling_pdf(tmp_path) -> str:
    """A stranded caption at the foot of page 1 titling the table at the top of page 2 — and the
    trap underneath it: page 1's own last table shares that table's header and column edges exactly.

    Built from the product manual's pages 29-30, where the two tables differ only in their values
    (24 hours against 28) and geometry alone would merge them.
    """
    path = str(tmp_path / "straddling.pdf")
    doc = fitz.open()
    edges = [50.0, 250.0, 400.0]
    header = ("Mode", "Operating time")

    first = doc.new_page()
    first.insert_text((50, 70), "Communication time", fontsize=12)
    bottom = _write_row_ruled(
        first,
        90.0,
        [header] + [(f"Codec {name}", "24 hours") for name in _OFFICERS],
        edges,
    )
    # The stranded caption: its table is overleaf.
    first.insert_text((50, bottom + 22), "Headphone cable connected", fontsize=12)

    second = doc.new_page()
    _write_row_ruled(
        second,
        40.0,
        [header] + [(f"Codec {name}", "38 hours") for name in _OFFICERS],
        edges,
    )
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def columned_prose_pdf(tmp_path) -> str:
    """Ruled rows whose cells are a paragraph chopped at the column edges.

    The brokerage-statement shape: structurally a perfectly good table — ruled rows, single-line
    cells, a full header — and semantically a run of sentences. Only the words cut in half give it
    away. It must be long enough for the column inference to fire at all (`_MIN_WORDS_VERTICAL`),
    or the page is declined for having no table rather than for holding prose, and the test would
    pass without exercising anything.
    """
    path = str(tmp_path / "columned_prose.pdf")
    words = (
        "the committee reviewed the arrangements and concluded that existing targets remained "
        "appropriate for the coming year given prevailing market conditions and peer practice "
        "across the wider industry as reported by the independent consultants engaged for this "
        "purpose and presented to the board at its regular meeting held before the year ended "
        "without any further amendment being proposed by management or requested by the auditors"
    ).split()
    rows = [
        (" ".join(words[i : i + 2]), " ".join(words[i + 2 : i + 4]), " ".join(words[i + 4 : i + 6]))
        for i in range(0, len(words) - 6, 6)
    ]
    doc = fitz.open()
    page = doc.new_page()
    _write_row_ruled(page, 60.0, rows, [50.0, 200.0, 350.0])
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def mismatched_rules_pdf(tmp_path) -> str:
    """Rules that do not correspond to the rows — four text lines inside every ruled band.

    A pension statement does this: the printed rules divide *sections*, not rows, so a reader that
    trusts them packs several lines into one cell and the values come back stacked and unusable.
    """
    path = str(tmp_path / "mismatched.pdf")
    doc = fitz.open()
    page = doc.new_page()
    edges = [50.0, 220.0, 360.0]
    line = 0
    for band in range(5):
        top = 60.0 + band * 72.0
        _rule(page, top, edges[0], edges[-1] + 100)
        for within in range(4):
            y = top + 14 + within * 15
            for column, edge in enumerate(edges):
                page.insert_text((edge + 4, y), f"{column}-{line}", fontsize=9)
            line += 1
    _rule(page, 60.0 + 5 * 72.0, edges[0], edges[-1] + 100)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def prose_pdf(tmp_path) -> str:
    """A page of running prose with a couple of rules on it — nothing a grid can be read from."""
    path = str(tmp_path / "prose.pdf")
    doc = fitz.open()
    page = doc.new_page()
    _rule(page, 60)
    _rule(page, 300)
    _rule(page, 500)
    for index in range(14):
        page.insert_text(
            (50, 80 + index * 15),
            "The committee reviewed the arrangements and concluded that the existing",
            fontsize=9,
        )
    doc.save(path)
    doc.close()
    return path


# ---- the readers ---------------------------------------------------------


def test_reads_a_fully_ruled_grid_exactly(ruled_pdf):
    result = tables.tables(ruled_pdf, pages=[1])
    assert result["total_tables"] == 1
    table = result["tables"][0]
    # A drawn grid is the only case where the header is claimed at all.
    assert table["header"] == ["Region", "Q1", "Q2"]
    assert table["rows"][1] == ["North", "120", "140"]
    assert table["rows"][2] == ["South", "90", "110"]
    assert table["page"] == 1


def test_reads_rows_from_rules_when_only_the_rows_are_ruled(captioned_pdf):
    """The reader the original design had none of: no column lines anywhere on this page."""
    page = fitz.open(captioned_pdf)[0]
    assert not page.find_tables(strategy="lines_strict").tables, "fixture must not be fully ruled"

    result = tables.tables(captioned_pdf, pages=[1])
    assert result["total_tables"] == 2
    names = [row[0] for row in result["tables"][0]["rows"]]
    # Every officer's row is read, with its values in the right columns.
    assert set(names) <= {"Name", *_OFFICERS}
    assert len(names) >= len(_OFFICERS) - 1
    first = result["tables"][0]["rows"][0]
    assert first[1].endswith("%") and first[2].endswith("%")


def test_a_header_is_claimed_only_when_a_drawn_grid_proves_it(ruled_pdf, captioned_pdf):
    """Null is the honest answer for a table read from rules, and the reason is measurable.

    That reader starts its band one row inside the table, so `rows[0]` holds data while the real
    header sits outside it — Apple's 10-Q page 4 returns `["Products $", "78,678 $", ...]` there.
    PyMuPDF's own detection reports `external: false` and returns the same row, so it assumes what
    it is meant to determine and cannot be leaned on.
    """
    assert tables.tables(ruled_pdf, pages=[1])["tables"][0]["header"] == ["Region", "Q1", "Q2"]
    for table in tables.tables(captioned_pdf, pages=[1])["tables"]:
        assert table["header"] is None
        assert table["rows"], "the rows are still returned in full"


def test_declines_prose_rather_than_inventing_a_grid(prose_pdf):
    result = tables.tables(prose_pdf, pages=[1])
    assert result["total_tables"] == 0
    assert [region["page"] for region in result["unread_regions"]] == [1]
    assert "extract_text" in result["unread_regions"][0]["suggestion"]


def test_every_requested_page_is_accounted_for(ruled_pdf, prose_pdf, tmp_path):
    """The contract a caller relies on: a page is never silently absent from both lists."""
    merged = str(tmp_path / "merged.pdf")
    doc = fitz.open(ruled_pdf)
    doc.insert_pdf(fitz.open(prose_pdf))
    doc.save(merged)
    doc.close()

    result = tables.tables(merged, pages=[1, 2])
    seen = {t["page"] for t in result["tables"]} | {r["page"] for r in result["unread_regions"]}
    assert seen == {1, 2}


def test_rules_that_do_not_match_the_rows_are_rejected(mismatched_rules_pdf):
    """The stuffed-cell test, and the reason it is the load-bearing one.

    Nobody anticipated a statement whose rules divide sections rather than rows. Nobody had to: the
    result arrives with several lines packed into each cell, and that is visible in the output
    without knowing anything about the layout that produced it.
    """
    result = tables.tables(mismatched_rules_pdf, pages=[1])
    assert result["total_tables"] == 0
    assert [region["page"] for region in result["unread_regions"]] == [1]


def test_prose_laid_into_columns_is_rejected_even_though_it_has_ruled_rows(columned_prose_pdf):
    """The shatter test, and the one the *stuffed* test cannot catch.

    A brokerage statement's "Standard Disclosures" block is ruled, has short single-line cells and
    a full header, so it passes every structural check — and is prose in a grid, its sentences cut
    across the column edges. What gives it away is that adjacent cells read as one word broken in
    two, which no real table does.
    """
    result = tables.tables(columned_prose_pdf, pages=[1])
    assert result["total_tables"] == 0
    assert [region["page"] for region in result["unread_regions"]] == [1]


def test_the_shatter_measure_separates_split_from_lost(columned_prose_pdf, captioned_pdf):
    """Split-but-complete is tolerated; cut-in-half is not. The line is what the reply promises.

    Below the limit a label lands across two cells with every character intact and the caller can
    rejoin it. Above it the reading has stopped tracking the page and characters vanish —
    `'Statutory federal inco'` + `'e tax rate'` — which is the one failure an agent cannot see.
    """
    shattered = [
        ["The committee reviewed", "the arrangements and", "concluded that the"],
        ["existing targets rem", "ained appropriate for", "the coming year and"],
    ]
    assert tables._shattered_ratio(shattered) > tables._SHATTER_LIMIT

    # Cisco's 10-K page 46, whose labels split across cells while every character survives and the
    # figures stay in their own columns. It measures 0.40 — kept, and the caller can rejoin the
    # label. This is the shape the limit is drawn to admit.
    intact = [
        ["Net cash provided by", "operating act", "ivities", "$ 14,177 $", "14,193 $", "10,880"],
        ["Acquisition of prope", "rty and equipm", "ent", "(1,410)", "(905)", "(670)"],
        ["Free cash flow", "", "", "$ 12,767 $", "13,288 $", "10,210"],
    ]
    assert tables._shattered_ratio(intact) <= tables._SHATTER_LIMIT


# ---- titles --------------------------------------------------------------


def test_title_steps_over_the_paragraph_between_caption_and_table(captioned_pdf):
    """The measured report shape. A nearest-block rule returns the paragraph and no title at all."""
    result = tables.tables(captioned_pdf, pages=[1])
    assert [t["title"] for t in result["tables"]] == [
        "Annual Cash Bonus Targets",
        "Stock Incentives",
    ]


def test_a_column_heading_is_not_a_caption():
    """`Salary`, three columns in, passes every text test a caption passes (LLY proxy p56)."""
    assert tables.looks_like_title("Annual Cash Bonus Targets")
    assert tables.looks_like_title("Music playback time")
    # A data row that the table's bbox stopped one line short of.
    assert not tables.looks_like_title("Cash and cash equivalents $  39,544  $  35,934")
    # A column header made of dates.
    assert not tables.looks_like_title("December 31,  2025 2024")
    # A sentence.
    assert not tables.looks_like_title(
        "See accompanying Notes to Condensed Consolidated Financial Statements."
    )


def test_a_stranded_caption_titles_the_next_pages_table(straddling_pdf):
    result = tables.tables(straddling_pdf, pages=[1, 2])
    overleaf = [t for t in result["tables"] if t["page"] == 2]
    assert len(overleaf) == 1
    assert overleaf[0]["title"] == "Headphone cable connected"
    assert overleaf[0]["title_from_previous_page"] is True


# ---- continuation --------------------------------------------------------


def test_identical_geometry_does_not_merge_two_different_tables(straddling_pdf):
    """Both tables share a header and column edges; only their values differ.

    Merging them by geometry would report one table saying two different things. The stranded
    caption on page 1 is what proves page 2 starts something new, so `continues_from` stays unset.
    """
    result = tables.tables(straddling_pdf, pages=[1, 2])
    overleaf = next(t for t in result["tables"] if t["page"] == 2)
    assert overleaf["continues_from"] is None
    values = {cell for t in result["tables"] for row in t["rows"] for cell in row}
    assert "24 hours" in values and "38 hours" in values


# ---- accounting negatives ------------------------------------------------


def test_a_split_negative_regains_its_closing_bracket():
    """35 of 36 measured cases lose only the closer, so restoring it is a rule, not a guess."""
    assert tables.repair_negative("(1,234") == "(1,234)"
    assert tables.repair_negative("(112") == "(112)"


def test_a_balanced_or_positive_cell_is_left_alone():
    assert tables.repair_negative("(1,234)") == "(1,234)"
    assert tables.repair_negative("1,234") == "1,234"
    # The 36th case: it lost the *opening* bracket and still reads positive. Deliberately not
    # repaired — treating a stray closer as a sign is not sound in the other direction.
    assert tables.repair_negative("1,051.42)") == "1,051.42)"
    # Not a number at all.
    assert tables.repair_negative("(see note 4") == "(see note 4"


# ---- cost and caps -------------------------------------------------------


def test_a_whole_document_scan_is_refused(ruled_pdf):
    """Reading tables is ~27x the cost of reading text, so the page range is mandatory."""
    with pytest.raises(ValueError, match="explicit page range"):
        tables.tables(ruled_pdf, pages=[])


def test_the_reply_paginates(captioned_pdf):
    first = tables.tables(captioned_pdf, pages=[1], max_tables=1)
    assert first["count"] == 1
    assert first["total_tables"] == 2
    assert first["more_available"] is True
    assert "offset: 1" in first["warnings"][0]

    second = tables.tables(captioned_pdf, pages=[1], max_tables=1, offset=1)
    assert second["count"] == 1
    assert second["more_available"] is False
    assert second["tables"][0]["title"] == "Stock Incentives"


def test_a_character_budget_bounds_the_reply_as_well_as_a_count(captioned_pdf):
    """A count cap alone does not bound a reply — one real financial table exceeds 20,000 chars."""
    result = tables.tables(captioned_pdf, pages=[1], max_chars=1)
    # Always at least one, or an empty batch with `more_available` would page forever.
    assert result["count"] == 1
    assert result["more_available"] is True


# ---- the extract_text hint -----------------------------------------------


def test_extract_text_names_the_pages_worth_calling_get_tables_on(captioned_pdf, prose_pdf):
    """The discovery path: a table's text arrives complete but flat, nothing saying it was one."""
    assert queries.extract_text(captioned_pdf)["table_pages"] == [1]
    # And the text itself is complete either way — which is what makes declining affordable.
    flat = queries.extract_text(captioned_pdf)["pages"][0]["text"]
    for value in ("Ricks", "100%", "125%", "6,325"):
        assert value in flat


def test_the_hint_costs_a_ruling_scan_not_a_table_scan(ruled_pdf):
    """It must never be tempting to make this precise: `find_tables` is ~27x `get_text`."""
    page = fitz.open(ruled_pdf)[0]
    assert tables.horizontal_rules(page) >= tables.MIN_RULES


# ---- the protocol side ---------------------------------------------------


def test_get_tables_is_registered_and_documented():
    from klarpdf.mcp_bridge import docs
    from klarpdf.mcp_bridge.server import create_server

    server = create_server()
    registered = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    assert "get_tables" in registered
    # M105: a description over the client's cap is silently cut in half.
    assert len(registered["get_tables"].description or "") <= 1900
    assert "get_tables" in docs.REFERENCE


def test_get_tables_is_withheld_from_no_one_it_is_a_read_tool():
    """`--read-only` withholds the write tools; a reader must survive it."""
    from klarpdf.mcp_bridge.config import Config
    from klarpdf.mcp_bridge.server import create_server

    server = create_server(Config(read_only=True))
    assert "get_tables" in {tool.name for tool in asyncio.run(server.list_tools())}
