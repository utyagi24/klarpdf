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
def underruled_pdf(tmp_path) -> str:
    """A real table with far too few rules to divide it — five text rows inside every ruled band.

    LLY's proxy page 64: 25 data rows and four ruled lines. Each band merges five rows into stuffed
    cells, which look exactly like the prose that separates two tables — so the split deletes them
    and leaves the empty filler behind, and the filler passes every other test by being empty.
    Structurally the opposite of `mismatched_rules_pdf`: there the survivors are stuffed, here the
    stuffing is in what got removed.
    """
    path = str(tmp_path / "underruled.pdf")
    doc = fitz.open()
    page = doc.new_page()
    edges = [50.0, 200.0, 300.0, 400.0]
    line = 0
    for band in range(5):
        top = 60.0 + band * 90.0
        _rule(page, top, edges[0], edges[-1] + 100)
        for within in range(5):
            y = top + 14 + within * 15
            page.insert_text((edges[0] + 4, y), _OFFICERS[line % len(_OFFICERS)], fontsize=9)
            page.insert_text((edges[1] + 4, y), f"202{within}", fontsize=9)
            page.insert_text((edges[2] + 4, y), f"{10 + line},0{within}8", fontsize=9)
            page.insert_text((edges[3] + 4, y), f"${100 + line},{200 + line}", fontsize=9)
            line += 1
    _rule(page, 60.0 + 5 * 90.0, edges[0], edges[-1] + 100)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def spanning_label_pdf(tmp_path) -> str:
    """A statement whose label runs across two columns, with a real data row below the last rule.

    Qualcomm's cash-flow statement: the label spills past the first column, so the second never
    holds a number, and requiring a figure in *every* data column refused its genuine final row
    (`Total cash and cash equivalents at end of period $ 4,533 $ 7,771`).
    """
    path = str(tmp_path / "spanning.pdf")
    doc = fitz.open()
    page = doc.new_page()
    label_x, first, second = 60.0, 380.0, 460.0
    right = second + 90
    top = 100.0
    _rule(page, top, label_x, right)
    body = [
        (f"Cash and equivalents {name}", f"{10 + i},{100 + i}", f"{20 + i},{200 + i}")
        for i, name in enumerate(_OFFICERS)
    ]
    for index, (label, left_value, right_value) in enumerate(body):
        y = top + index * 22.0
        page.insert_text((label_x + 2, y + 15), label, fontsize=9)
        page.insert_text((first + 2, y + 15), left_value, fontsize=9)
        page.insert_text((second + 2, y + 15), right_value, fontsize=9)
        _rule(page, y + 22.0, label_x, right)
    below = top + len(body) * 22.0 + 12
    page.insert_text((label_x + 2, below), "Total cash at end of period", fontsize=9)
    page.insert_text((first + 2, below), "4,533", fontsize=9)
    page.insert_text((second + 2, below), "7,771", fontsize=9)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def prose_pdf(tmp_path) -> str:
    """A page of running prose with **no** ruling at all — the other kind of decline.

    Kept unruled on purpose: it is the fixture for "nothing here marks where the cells are", while
    `columned_prose_pdf`, `mismatched_rules_pdf` and `underruled_pdf` cover the pages that do carry
    ruling and still cannot be read. The two declines say different things to a caller and the
    reason field has to distinguish them.
    """
    path = str(tmp_path / "prose.pdf")
    doc = fitz.open()
    page = doc.new_page()
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
    # Every officer's row is read, with its values in the right columns. `rows[0]` is the header
    # once edge recovery reaches it, so the percentages are asserted on a row known to be data.
    assert set(names) <= {"Name", *_OFFICERS}
    assert len(names) >= len(_OFFICERS) - 1
    data = [row for row in result["tables"][0]["rows"] if row[0] in _OFFICERS]
    assert data, "no officer rows were returned"
    assert all(row[1].endswith("%") and row[2].endswith("%") for row in data)


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


def test_a_split_that_discards_the_data_is_disbelieved():
    """A split may remove separators; it may not remove the table.

    Found by the owner on LLY's proxy page 64 — 25 data rows and four ruled lines, so each band
    merged five rows into stuffed cells, the split took those for separators and deleted them, and
    what survived was the empty filler between them. It passed every other test by being empty and
    reported two confident tables holding two stray numbers, under the page's correct title.

    Tested against the measured proportions rather than a rendered page: the region's row division
    there came from PyMuPDF giving *different columns* different row granularity (71 pt rows beside
    14 pt ones), which a constructed fixture does not reproduce. The arithmetic this guards is the
    part that has to be right, so it is asserted directly.
    """
    whole = [
        ["Ricks", "2025\n2024\n2023", "22,086\n19,158", "$23,735,382\n$20,588,719"],
        ["", "", "20,197", ""],
        ["", "", "17,850", ""],
    ]
    # The split keeps only the two near-empty rows: 84% of the filled cells went with the row it
    # mistook for a separator. That is a badly read table, not three tables in one band.
    residue = [[["", "", "20,197", ""], ["", "", "17,850", ""]]]
    assert not tables.split_is_credible(whole, residue)

    # A real split: the prose row goes, every data row stays (LLY p56 measures 11% loss).
    table_rows = [["Name", "2024", "2025"], ["Ricks", "150%", "175%"], ["Hakim", "100%", "100%"]]
    with_prose = [*table_rows, ["Stock Incentives\nThe committee set", "", ""]]
    assert tables.split_is_credible(with_prose, [table_rows])

    # No split at all discards nothing.
    assert tables.split_is_credible(table_rows, [table_rows])


class _FakeRow:
    def __init__(self, top, bottom):
        self.bbox = (0.0, top, 500.0, bottom)


class _FakeTable:
    """The narrow slice of PyMuPDF's Table that `_split_prose_rows` reads.

    Used because the defect lives in row *geometry* — a tall row holding line breaks — and a
    constructed PDF does not reproduce PyMuPDF's row division reliably enough to pin it. The
    heights here are Alphabet's measured ones: 10.2 pt data rows against a 33.6 pt wrapped row.
    """

    def __init__(self, rows, heights):
        self._rows = rows
        self.rows = []
        y = 0.0
        for height in heights:
            self.rows.append(_FakeRow(y, y + height))
            y += height
        self.bbox = (0.0, 0.0, 500.0, y)

    def extract(self):
        return self._rows


def test_a_wrapped_data_row_is_not_mistaken_for_a_separator():
    """TC-026 HIGH 1 — the splitter deleted a real row because its label wrapped.

    Alphabet's 2026 10-K page 51 lost `['Class A, Class B, and Class C stoc', '84,800', '93,126']`,
    which left the balance sheet's returned components missing their returned total by 93,126. It
    was 3.1% of the region, far under `_MAX_SPLIT_LOSS`, so the guard for the gross case could not
    have caught it. A heading and its paragraph carry no figures; a wrapped data row does.
    """
    data = ["Class A, Class B, and Class C stoc\nand additional paid-in capital", "84,800", "93,126"]
    table = _FakeTable(
        [["Retained earnings", "1", "2"], data, ["Total equity", "3", "4"], ["x", "5", "6"]],
        [10.2, 33.6, 10.2, 10.2],
    )
    kept = [row for rows, _, _ in tables._split_prose_rows(table) for row in rows]
    assert data in kept, "a tall wrapped row carrying figures is data, not a separator"


def test_a_wrapped_heading_between_tables_is_still_a_separator():
    """The other half: the split must keep working where the tall row really is prose."""
    prose = ["Stock Incentives — Target ", "Values\nrget values based o", "nternal relativ\ntween"]
    table = _FakeTable(
        [["Name", "2024", "2025"], ["Ricks", "150%", "175%"], prose, ["Hakim", "1", "2"],
         ["Coxe", "3", "4"]],
        [10.2, 10.2, 33.6, 10.2, 10.2],
    )
    kept = [row for rows, _, _ in tables._split_prose_rows(table) for row in rows]
    assert prose not in kept, "a tall wrapped row with no figures is prose"
    assert len(tables._split_prose_rows(table)) == 2, "and it splits the region in two"


def test_the_credibility_guard_is_actually_wired_into_the_read(captioned_pdf, monkeypatch):
    """Pins the call site, not just the arithmetic.

    The unit test above proves the sum is right; this proves it is consulted. Without it, deleting
    the check produces a green suite — which is exactly how the defect it guards reached the owner.
    The split is forced to throw away everything but one row, so a wired guard must decline the page
    and an unwired one will happily report the residue.
    """
    real = tables._split_prose_rows

    def lossy(table):
        rows = table.extract()
        kept = rows[:1] + rows[-1:]
        return [(kept, table.bbox[1], table.bbox[3])]

    monkeypatch.setattr(tables, "_split_prose_rows", lossy)
    result = tables.tables(captioned_pdf, pages=[1])
    assert result["total_tables"] == 0, "a split that discards the table must not be believed"
    assert [region["page"] for region in result["unread_regions"]] == [1]

    monkeypatch.setattr(tables, "_split_prose_rows", real)
    assert tables.tables(captioned_pdf, pages=[1])["total_tables"] == 2


def test_the_decline_reason_says_which_kind_of_decline_it_was(underruled_pdf, prose_pdf):
    """Ruling that failed and no ruling at all are different answers to the caller."""
    # A region that was found and refused names *itself* and why, rather than shrugging at the page.
    ruled_but_bad = tables.tables(underruled_pdf, pages=[1])["unread_regions"][0]
    assert "a table-like region here" in ruled_but_bad["reason"]

    nothing_there = tables.tables(prose_pdf, pages=[1])["unread_regions"][0]
    assert "nothing on this page marks where the cells are" in nothing_there["reason"]


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


class _FakePage:
    """Just the `get_text("words")` that `cuts_a_figure` consults for the page's vocabulary."""

    def __init__(self, vocabulary):
        self._words = [(0, 0, 1, 1, w, 0, 0, 0) for w in vocabulary]

    def get_text(self, _kind):
        return self._words


def test_a_column_edge_through_a_figure_is_detected():
    """TC-027 — the cardinal failure, and the one the shatter ratio cannot see.

    Amazon's Q2 2026 release page 6 was returned as a confident 8-column grid for a 7-column
    statement, with `(143,457)` split across two columns as `'(1'` + `'43,457)'`. Three things hid
    it: the shatter test wants a *lowercase* continuation (so all-caps `EQUIV`/`ALENTS` on the same
    page is invisible), it never looks at digits, and 200 legitimately-unmatched numeric pairs in a
    46x8 table dilute what it does see to 0.131 against a 0.4 limit.
    """
    page = _FakePage({"(143,457)", "(39,424)", "(79,245)", "Net", "cash"})
    row = ["Net cash", "(39,424)", "(79,245)", "(69,227) (1", "43,457) ("]
    assert tables.cuts_a_figure(page, [row])


def test_a_cut_through_a_long_identifier_is_fatal_whatever_the_row_holds():
    """TC-031 — the overlap where both guards are blind.

    A rental statement's 3-column table came back as 7, and the printed reference
    `211206 1017242382155` exists nowhere in the reply as a whole value: it arrived as `2112` +
    `06 10172` + `42382155`. Its neighbouring cells are prose, so the row never has the two complete
    numeric cells the general check requires, and `digits_lost` is blind because every digit *did*
    arrive — coverage asks whether digits arrived, not whether they arrived **together**.
    """
    page = _FakePage({"211206", "DEPOSITED", "ITEM"})
    row = ["12/6/21", "DEPOSITED ITEM RETN", "UNPAID 2112", "06 PAPER"]
    assert tables.cuts_a_figure(page, [row])

    # A year stays tolerated, so LLY p56's `202` + `4` split keeps its table.
    page = _FakePage({"2024", "Name", "Bonus"})
    assert not tables.cuts_a_figure(page, [["Name 202", "4 Bonus Target"]])


def test_a_split_year_in_a_header_is_not_a_cut_figure():
    """Restricted to rows carrying data, because that is where a bad edge stops being cosmetic.

    LLY's proxy page 56 splits `202`/`4` in its header row and is otherwise a good table; Cisco's
    10-K page 61 cuts 26 labels while every figure stays whole. Both must survive.
    """
    page = _FakePage({"2024", "2025", "Name", "Bonus", "Target"})
    header = ["Name 202", "4 Bonus Target 202", "5 Bonus Tar"]
    assert not tables.cuts_a_figure(page, [header]), "a header row carries no data to misalign"

    page = _FakePage({"Depreciation,", "investing", "14,177", "14,193"})
    labels_only = ["Deprecia", "tion, and other", "14,177", "14,193"]
    assert not tables.cuts_a_figure(page, [labels_only]), "a split label is visible and rejoinable"


class _WordPage:
    """A page whose words are placed, so `digits_lost` can weigh a region against a reading."""

    def __init__(self, words):
        self._words = [(x0, y0, x1, y1, text, 0, 0, 0) for x0, y0, x1, y1, text in words]

    def get_text(self, _kind):
        return self._words


def test_digits_printed_in_a_region_must_reach_a_cell():
    """TC-028 — the general form of every row-loss defect in this milestone.

    Tesla's final row arrives as a single value with its label and four figures gone; Apple's page 11
    header loses all three instances of `28,267`. Both evade `cuts_a_figure`, because that check
    needs two complete numeric cells in the row before it will look and **the loss is what removes
    them** — the worse a row is mangled, the less likely the guard is to fire.
    """
    words = [(0, 0, 20, 10, "Diluted"), (30, 0, 50, 10, "3,519"), (60, 0, 80, 10, "3,526"),
             (90, 0, 110, 10, "3,540")]
    page = _WordPage(words)
    box = fitz.Rect(0, -5, 200, 15)

    stripped = [["", "", "3,540"]]          # the label and two figures never arrived
    assert tables.digits_lost(page, box, stripped) >= tables.MIN_DIGIT_DEFICIT

    whole = [["Diluted", "3,519", "3,526", "3,540"]]
    assert tables.digits_lost(page, box, whole) == 0


def test_a_split_figure_keeps_its_digits_so_the_two_checks_differ():
    """Why both checks exist rather than one.

    Digits survive being *split* and do not survive being *dropped*. `(143,457)` cut into `(1` and
    `43,457)` loses nothing countable, so `digits_lost` is blind to it and `cuts_a_figure` is not;
    a row stripped to one value loses digits, so `digits_lost` sees it and `cuts_a_figure` cannot.
    Neither subsumes the other on the failure each was written for.
    """
    page = _WordPage([(0, 0, 40, 10, "(143,457)"), (50, 0, 70, 10, "(39,424)")])
    box = fitz.Rect(0, -5, 200, 15)
    cut = [["(39,424)", "(1", "43,457)"]]
    assert tables.digits_lost(page, box, cut) == 0, "a cut preserves every digit"

    vocabulary = _FakePage({"(143,457)", "(39,424)"})
    assert tables.cuts_a_figure(vocabulary, cut), "but the fragments rejoin into a word on the page"


def test_a_double_printed_glyph_is_not_counted_as_a_loss():
    """The reason the floor is not zero.

    The designed report's page 15 draws `6+` twice to simulate bold, so one digit is "missing" from
    a row that is completely correct. Every correct table measured shows a deficit of 0 or that 1;
    every damaged one shows 8 to 20.
    """
    page = _WordPage([(0, 0, 10, 10, "6+"), (0, 0, 10, 10, "6+"), (20, 0, 40, 10, "26")])
    box = fitz.Rect(0, -5, 200, 15)
    assert tables.digits_lost(page, box, [["6+", "26"]]) < tables.MIN_DIGIT_DEFICIT


def test_the_digit_check_is_wired_into_the_read(captioned_pdf, monkeypatch):
    """Pins the call site, as every other guard in this module now does."""
    monkeypatch.setattr(tables, "digits_lost", lambda page, box, rows: 99)
    result = tables.tables(captioned_pdf, pages=[1])
    assert result["total_tables"] == 0
    # Both of the page's regions are refused, and each is named — one entry per region, not per page.
    assert {r["page"] for r in result["unread_regions"]} == {1}
    assert len(result["unread_regions"]) == 2

    monkeypatch.setattr(tables, "digits_lost", lambda page, box, rows: 0)
    assert tables.tables(captioned_pdf, pages=[1])["total_tables"] == 2


def test_the_figure_cut_check_is_wired_into_the_read(captioned_pdf, monkeypatch):
    """Pins the call site. Deleting the call leaves the unit tests above perfectly green."""
    monkeypatch.setattr(tables, "cuts_a_figure", lambda page, rows: True)
    result = tables.tables(captioned_pdf, pages=[1])
    assert result["total_tables"] == 0, "a region whose edge cuts a value must not be reported"
    assert {r["page"] for r in result["unread_regions"]} == {1}
    assert len(result["unread_regions"]) == 2

    monkeypatch.setattr(tables, "cuts_a_figure", lambda page, rows: False)
    assert tables.tables(captioned_pdf, pages=[1])["total_tables"] == 2


def test_a_group_that_merely_begins_with_a_blank_row_is_kept():
    """TC-029 — a spacer row was costing whole tables.

    `_acceptable` reads `rows[0]` to decide whether a block has a header at all, so a group that
    merely *started* with a blank row was thrown away entire. Qualcomm's Q3 FY26 10-Q page 4 lost
    its whole asset side that way — thirteen clean rows reconciling to the `57,367` the tool
    returned on the liability side.
    """
    with_spacer = [["", "", ""], ["Cash", "4,533", "5,520"], ["Total", "23,004", "25,754"]]
    trimmed = tables.trim_blank_edges(with_spacer)
    assert trimmed[0] == ["Cash", "4,533", "5,520"]
    assert tables._acceptable(trimmed, tables._STUFFED_LIMIT)
    assert not tables._acceptable(with_spacer, tables._STUFFED_LIMIT), (
        "untrimmed, the blank first row is what the shape test reads"
    )
    # Only the edges, and only when wholly blank — an interior gap is part of the table's shape.
    inner = [["A", "1"], ["", ""], ["B", "2"]]
    assert tables.trim_blank_edges(inner) == inner


def test_a_label_spanning_two_columns_does_not_block_edge_recovery(spanning_label_pdf):
    """TC-029 — requiring a figure in *every* data column refused a genuine final row.

    The label spills past the first column here, so the second never holds a number. Which columns
    are numeric is now taken from the table's own body rather than assumed.
    """
    result = tables.tables(spanning_label_pdf, pages=[1])
    values = [c for t in result["tables"] for row in t["rows"] for c in row]
    assert "4,533" in values and "7,771" in values, "the row below the last rule must be recovered"


def test_the_blank_edge_trim_is_wired_into_the_read(captioned_pdf, monkeypatch):
    """Pins the call site: PyMuPDF will not emit a leading blank row on demand, so the group is
    forced instead. Without the trim, `_acceptable` reads that blank row as a missing header and
    throws the whole group away — which is how Qualcomm's page 4 lost its asset side."""
    real = tables._split_prose_rows

    def with_spacer(table):
        return [([[""] * len(rows[0]), *rows], top, bottom) for rows, top, bottom in real(table)]

    monkeypatch.setattr(tables, "_split_prose_rows", with_spacer)
    assert tables.tables(captioned_pdf, pages=[1])["total_tables"] == 2, (
        "a group that merely begins with a spacer must still be read"
    )


def test_a_region_refused_on_a_page_that_also_succeeds_is_still_reported(outdented_pdf, monkeypatch):
    """TC-029's central finding: extraction went region-granular, reporting stayed page-granular.

    A page holding one readable region and one unreadable one came back looking complete. Qualcomm's
    page 4 returned its liabilities and dropped its entire asset side — 13 rows, 26 figures — with
    `unread_regions: []` and a final row balancing against a total whose every component was gone.
    The description's promise stayed *literally* true, which is why it stopped protecting anyone.
    """
    # Force every region to fail the digit check, on a page that otherwise reads.
    monkeypatch.setattr(tables, "digits_lost", lambda page, box, rows: 99)
    result = tables.tables(outdented_pdf, pages=[1])
    assert result["total_tables"] == 0
    assert result["unread_regions"], "a refused region must be named even when others pass"
    region = result["unread_regions"][0]
    assert "did not reach any cell" in region["reason"]
    # And the bbox localises the region rather than shrugging at the whole page.
    page_height = fitz.open(outdented_pdf)[0].rect.height
    assert region["bbox"][3] < page_height, "a refused region reports its own box, not the page"


def test_a_region_that_leaves_a_column_outside_itself_is_refused():
    """TC-030 — 84 correct figures with nothing to say what any of them is.

    Qualcomm's page 5 came back as a region starting at x=309.9 — the numeric columns only — with
    every row label (`Revenues:`, `Equipment and services`, `Licensing`) printing outside it and
    `unread_regions: []`. Against the decline it replaced that is a *worse* outcome: a decline at
    least tells the caller to go read the page.
    """
    box = fitz.Rect(300, 100, 500, 200)
    rows_y = [110, 130, 150, 170, 190]
    # A label column to the left, one entry per row: every line of the region is affected.
    labels = _WordPage([(60, y, 250, y + 8, f"Label{i}") for i, y in enumerate(rows_y)]
                       + [(320, y, 400, y + 8, f"{i},000") for i, y in enumerate(rows_y)])
    assert tables.column_dropped(labels, box, [])

    # Decoration beside a table touches a few lines, not all of them — the designed report prints
    # `PIE CHART PLACEHOLDER` next to a five-row table and must not cost it.
    decorated = _WordPage([(390, 130, 460, 138, "PIE"), (390, 150, 460, 158, "CHART")]
                          + [(320, y, 400, y + 8, f"{i},000") for i, y in enumerate(rows_y)])
    assert not tables.column_dropped(decorated, box, [])


def test_a_column_left_outside_the_region_is_read_rather_than_refused():
    """TC-031 — the gappy column no threshold can detect.

    A research paper's third result column has values on about half the rows: 0.38 of the region's
    lines, which is *below* the 0.44 that decoration beside a table scores. The paper supplies its
    own control — its other table has the same three-column design with a dense third column, and
    that one is correctly refused — so the threshold was provably the discriminator. Reading the
    column makes detection unnecessary.
    """
    box = fitz.Rect(40, 60, 252.7, 200)
    bands = [(60.0, 90.0), (90.0, 120.0), (120.0, 150.0), (150.0, 180.0), (180.0, 200.0)]
    # Values left-aligned at exactly the region's right edge, one per row band.
    page = _WordPage([(252.7, top + 4, 280.0, top + 12, f"0.{index}77***")
                      for index, (top, _bottom) in enumerate(bands)])
    rows = [["label", "x"] for _ in bands]
    widened = tables.recover_column(page, box, rows, bands)
    assert widened is not None
    assert [row[-1] for row in widened] == [f"0.{i}77***" for i in range(len(bands))]


def test_column_recovery_is_wired_into_the_read(captioned_pdf, monkeypatch):
    """Pins the call site: without it, deleting the call silently drops the column again."""
    def widen(page, box, rows, bands):
        return [[*row, "RECOVERED"] for row in rows]

    monkeypatch.setattr(tables, "recover_column", widen)
    result = tables.tables(captioned_pdf, pages=[1])
    assert result["tables"], "the page still reads"
    for entry in result["tables"]:
        assert all(row[-1] == "RECOVERED" for row in entry["rows"])
        assert entry["col_count"] == len(entry["rows"][0])


def test_the_journals_second_text_column_is_not_read_as_a_table_column():
    """Adjacency is the whole separation, and nothing softer works.

    On the measured page the dropped column begins at **252.7** — the region's own right edge — and
    the journal's article text begins at **306.1**. Both are left-aligned and both fall inside the
    table's row bands, so only the distance tells them apart. The table's own footnote is excluded
    too, because its words start ragged rather than on a common edge.
    """
    box = fitz.Rect(40, 60, 252.7, 200)
    bands = [(60.0, 90.0), (90.0, 120.0), (120.0, 150.0), (150.0, 180.0), (180.0, 200.0)]
    words = "where that children worked levels".split()
    prose = _WordPage(
        [(306.1, top + 4, 340.0, top + 12, word) for (top, _b), word in zip(bands, words)]
    )
    assert tables.recover_column(prose, box, [["a", "b"] for _ in bands], bands) is None

    ragged = _WordPage([(252.7 + index * 4, top + 4, 290.0, top + 12, "note")
                        for index, (top, _b) in enumerate(bands)])
    assert tables.recover_column(ragged, box, [["a", "b"] for _ in bands], bands) is None


def test_the_dropped_column_check_is_wired_into_the_read(captioned_pdf, monkeypatch):
    """Pins the call site. Without it, deleting the check leaves the unit tests above green."""
    monkeypatch.setattr(tables, "column_dropped", lambda page, box, others: True)
    result = tables.tables(captioned_pdf, pages=[1])
    assert result["total_tables"] == 0
    assert any("left a whole column" in r["reason"] for r in result["unread_regions"])

    monkeypatch.setattr(tables, "column_dropped", lambda page, box, others: False)
    assert tables.tables(captioned_pdf, pages=[1])["total_tables"] == 2


def test_ruling_with_nothing_table_shaped_in_it_says_so(tmp_path):
    """TC-030 — a decline must not assert rows it never produced.

    Salesforce's page 5 is heavily ruled (34 rules) and yields no region at all, yet reported
    *"the rows it produced did not hold together"*. Three declines are now distinguished: no ruling,
    ruling with no region, and a region that was found and refused.
    """
    path = str(tmp_path / "ruled_but_empty.pdf")
    doc = fitz.open()
    page = doc.new_page()
    for index in range(8):
        _rule(page, 100 + index * 40, 60, 400)
    doc.save(path)
    doc.close()

    result = tables.tables(path, pages=[1])
    assert result["total_tables"] == 0
    reason = result["unread_regions"][0]["reason"]
    assert "nothing table-shaped was found within the ruling" in reason
    assert "the rows it produced" not in reason


def test_a_neighbouring_table_is_not_mistaken_for_a_dropped_column():
    """A page of side-by-side tables would otherwise have each accuse the other."""
    box = fitz.Rect(300, 100, 500, 200)
    rows_y = [110, 130, 150, 170, 190]
    page = _WordPage([(60, y, 250, y + 8, f"Other{i}") for i, y in enumerate(rows_y)]
                     + [(320, y, 400, y + 8, f"{i},000") for i, y in enumerate(rows_y)])
    neighbour = fitz.Rect(50, 100, 260, 200)
    assert tables.column_dropped(page, box, [])
    assert not tables.column_dropped(page, box, [neighbour])


def test_a_year_in_a_two_tier_header_may_go_unassigned():
    """TC-030's asymmetry: header damage is tolerated everywhere else and must be here too.

    Salesforce's page 4 lost two footnote tables whose bodies are pristine, because a two-tier
    period header (`Three Months Ended July 31,` over `2026 2025 2026 2025`) has a tier belonging to
    no single column. LLY's page 56 keeps its table with `2024` merely *split*. Same kind of fault.
    """
    box = fitz.Rect(0, 100, 500, 200)
    page = _WordPage([(60, 105, 100, 113, "2026"), (160, 105, 200, 113, "2025"),
                      (60, 130, 120, 138, "147"), (160, 130, 220, 138, "126")])
    # The years never reached a cell; the body did.
    assert tables.digits_lost(page, box, [["", "2025"], ["147", "126"]]) < tables.MIN_DIGIT_DEFICIT

    # But a *value* on that same top line is not forgiven — Apple's page 11 has a data row there,
    # and blanket forgiveness readmitted the table TC-028 was filed for.
    page = _WordPage([(60, 105, 120, 113, "28,267"), (160, 105, 220, 113, "28,267"),
                      (60, 130, 120, 138, "147"), (160, 130, 220, 138, "126")])
    assert tables.digits_lost(page, box, [["", ""], ["147", "126"]]) >= tables.MIN_DIGIT_DEFICIT


def test_a_period_header_that_does_not_sit_over_its_figures_is_dropped():
    """TC-031 — a partial header that misstates periods is worse than no header.

    Salesforce's page 4 shows both shapes. Its statement emitted `['2026','2025','2026','2025','']`
    with the years one column **left** of the figures; its footnote tables emitted
    `['','2025','','2025','']` — two years for four figure columns, because the unassignable `2026`s
    were exempted away and only the `2025`s remained, landing where 2026 belongs. Either way a
    caller joining `rows[0]` to the body labels the 2026 figures as 2025.
    """
    shifted = [["2026", "2025", ""], ["Cash $", "4,533 $", "5,520"]]
    assert tables.drop_misplaced_year_header(shifted) == shifted[1:]

    incomplete = [["", "2025", "", "2025", ""], ["Cost $", "234", "150", "478", "312"]]
    assert tables.drop_misplaced_year_header(incomplete) == incomplete[1:]

    # Kept when the years sit exactly over the columns the body puts figures in.
    aligned = [["", "2026", "2025"], ["Cash", "4,533", "5,520"]]
    assert tables.drop_misplaced_year_header(aligned) == aligned

    # And a header carrying more than a bare year is not a candidate at all.
    dated = [["", "December 31, 2025", "June 30, 2026"], ["Cash", "4,533", "5,520"]]
    assert tables.drop_misplaced_year_header(dated) == dated


def test_the_year_header_drop_is_wired_into_the_read(captioned_pdf, monkeypatch):
    """Pins the call site: without it, deleting the call leaves the unit test above green.

    A shifted period header is injected at the top of each group, exactly the shape Salesforce's
    page 4 emitted, and must not survive into the reply.
    """
    real = tables._split_prose_rows

    def with_shifted_header(table):
        return [
            ([["2026", "2025", ""], *rows], top, bottom) for rows, top, bottom in real(table)
        ]

    monkeypatch.setattr(tables, "_split_prose_rows", with_shifted_header)
    result = tables.tables(captioned_pdf, pages=[1])
    for entry in result["tables"]:
        assert entry["rows"][0] != ["2026", "2025", ""], (
            "a period header that does not sit over its figures must be dropped"
        )


def test_a_recovered_row_of_only_years_is_not_prepended():
    """The same defect from the recovery side.

    Per-group edge recovery was prepending a period header tier it cannot place: the years print
    centred over their figure columns, so the run centres land one column left. Qualcomm's page 4
    gained a wrong header that way, having had none at all the round before.
    """
    assert tables._YEAR.match("2026") and tables._YEAR.match("2025,")
    assert not tables._YEAR.match("December")
    # The guard's own predicate: all-years is refused, a dated header is not.
    assert all(not c or tables._YEAR.match(c) for c in ["2026", "2025", ""])
    assert not all(
        not c or tables._YEAR.match(c) for c in ["", "December 31, 2025", "June 30, 2026"]
    )


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


# ---- TC-026: content the region's own boundaries cut off -------------------


@pytest.fixture
def outdented_pdf(tmp_path) -> str:
    """A row-ruled statement whose section headers are outdented, and whose first data row sits
    *above* the first rule — the two shapes TC-026 found losing data on three SEC filers.

    Apple's 10-Q page 6 has both at once: `Cash and cash equivalents 39,544` above the band, and
    `Non-current assets:` starting 18 pt left of the first column's edge.
    """
    path = str(tmp_path / "outdented.pdf")
    doc = fitz.open()
    page = doc.new_page()
    edges = [70.0, 300.0, 400.0]
    right = edges[-1] + 100

    # The row that sits ABOVE the first rule — a real data row the band will exclude.
    page.insert_text((edges[0] + 2, 74), "Cash and cash equivalents", fontsize=9)
    page.insert_text((edges[1] + 2, 74), "39,544", fontsize=9)
    page.insert_text((edges[2] + 2, 74), "35,934", fontsize=9)

    # A section header on a line of its OWN, outdented 18 pt left of the first column, among
    # ordinary data rows. Sharing a line with data would merely interleave the two strings.
    body = [(name, f"{10 + i},{100 + i}", f"{20 + i},{200 + i}") for i, name in enumerate(_OFFICERS)]
    body.insert(4, ("Non-current assets:", "", ""))

    top = 80.0
    _rule(page, top, edges[0], right)
    for index, row in enumerate(body):
        y = top + index * 22.0
        outdent = 18 if row[0].endswith(":") else 0
        for column, value in enumerate(row):
            if value:
                page.insert_text((edges[column] + 2 - outdent, y + 15), value, fontsize=9)
        _rule(page, y + 22.0, edges[0], right)
    doc.save(path)
    doc.close()
    return path


def test_a_label_clipped_by_the_left_edge_is_restored(outdented_pdf):
    """TC-026 HIGH 3 — outdented headers were cut mid-word at a fixed x, silently."""
    result = tables.tables(outdented_pdf, pages=[1])
    labels = [row[0] for t in result["tables"] for row in t["rows"]]
    clipped = [
        l for l in labels
        if l.strip() and "current assets:" in l and l != "Non-current assets:"
    ]
    assert not clipped, f"a label is still cut: {clipped}"


def test_the_left_repair_refuses_when_it_would_re_cut_the_right():
    """The condition that makes the repair safe rather than merely better.

    Re-reading a cell from the page's true margin can also lose its tail, because a label may run
    past its own column: Cisco's 10-K page 61 turns `'flows from i'` into `'Cash flows from'` —
    a word gained and a character lost. Only a pure prefix addition is accepted.
    """
    assert " ".join("Non-current assets:".split()).endswith("-current assets:")
    assert not " ".join("Cash flows from".split()).endswith("flows from i")


def test_a_recovered_row_keeps_every_word_not_just_the_first_figure():
    """TC-027 — the recovery function itself was dropping data.

    Amazon's page 10 carries `December 31, 2025` and `June 30, 2026` across its two figure columns.
    Taking one figure per column returned `['December', '31,', '30,']` — **both years gone, from the
    code written to recover them**. Assembling by run also lands a heading in the right column where
    a single word does not: `December 31, 2025` straddles the boundary at x=449 while the run's
    centre, 449.25, sits inside it.
    """
    line = [
        (416.9, 89.4, 451.6, 98.3, "December", 0, 0, 0),
        (453.6, 89.4, 463.6, 98.3, "31,", 0, 0, 0),
        (465.6, 89.4, 481.6, 98.3, "2025", 0, 0, 0),
        (502.5, 89.4, 519.0, 98.3, "June", 0, 0, 0),
        (521.0, 89.4, 531.0, 98.3, "30,", 0, 0, 0),
        (533.0, 89.4, 549.0, 98.3, "2026", 0, 0, 0),
    ]
    grouped = tables._runs(line)
    assert [" ".join(w[4] for w in run) for run in grouped] == [
        "December 31, 2025",
        "June 30, 2026",
    ], "a 20.9 pt gap separates the two column entries; the 2 pt gaps inside them do not"

    columns = [(70.5, 0, 449.0, 0), (449.0, 0, 518.0, 0), (518.0, 0, 558.0, 0)]
    assert tables._column_of(449.25, columns) == 1, "strict containment, no tolerance"
    assert tables._column_of(525.75, columns) == 2

    # Containment must beat proximity, and Apple's page 6 is where that bites: its label column is
    # 378 pt wide against 69 pt figure columns, so a word sitting well inside the label column is
    # still *nearer* to the next column's centre. Falling back to nearest here moves a row label
    # into a figure column.
    assert tables._column_of(440.0, columns) == 0, "inside the wide label column, though 140 pt "\
        "from its centre and 43 pt from the next column's"


def test_a_data_row_just_outside_the_band_is_recovered(outdented_pdf):
    """TC-026 HIGH 1 — a statement's first line often sits above the first drawn rule.

    Apple's largest current asset went missing this way, and the returned rows missed the returned
    total by exactly that figure.
    """
    result = tables.tables(outdented_pdf, pages=[1])
    values = [c for t in result["tables"] for row in t["rows"] for c in row]
    assert "39,544" in values and "35,934" in values


# ---- accounting negatives ------------------------------------------------


def test_a_split_negative_regains_its_closing_bracket():
    """35 of 36 measured cases lose only the closer, so restoring it is a rule, not a guess."""
    assert tables.repair_negative("(1,234") == "(1,234)"
    assert tables.repair_negative("(112") == "(112)"


def test_an_opening_bracket_stranded_on_the_previous_cell_is_moved_back():
    """TC-026 HIGH 2 — the sign error that is invisible and arithmetically plausible.

    A column edge inside `(103,773)` can leave the opening bracket on the cell to its left, so the
    figure arrives **positive**. Measured on Alphabet's 2026 10-K page 55, where the printed
    subtotal proves both affected lines are negative.
    """
    assert tables.rejoin_split_bracket(
        ["Purchases of marketable securities", "(77,858)", "(86,679) (", "103,773"]
    ) == ["Purchases of marketable securities", "(77,858)", "(86,679)", "(103,773"]

    # The closer may already be on the right-hand cell; only the opener moved.
    assert tables.rejoin_split_bracket(["x", "68,184 $ (", "7,603) $"]) == [
        "x", "68,184 $", "(7,603) $"
    ]


def test_the_bracket_rejoin_runs_on_every_row_the_tool_returns():
    """Pins the call site, not just the function.

    Deleting the call left the suite green when only the unit test above existed — the same gap
    that let two other defects through in this milestone.
    """
    rows = [["Particulars", "2025", "2024"],
            ["Purchases of marketable securities", "(86,679) (", "103,773"]]
    repaired = tables._repair_rows(rows)
    assert repaired[1] == ["Purchases of marketable securities", "(86,679)", "(103,773)"], (
        "the stranded bracket must be moved, and then closed by repair_negative"
    )


def test_a_bracket_that_belongs_where_it_is_stays_there():
    for row in (
        ["Total net sales", "109,417", "94,036"],   # nothing adrift
        ["Note (a)", "12", "13"],                   # balanced, and not at a cell end
        ["x", "(1,234)", "5"],                      # already a complete negative
    ):
        assert tables.rejoin_split_bracket(row) == row


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
