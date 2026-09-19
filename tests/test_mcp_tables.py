"""M141 — ``get_tables``: rows read from the page's own structure, checked against the page.

Every fixture here is built with PyMuPDF and read by PyMuPDF's real table finder, so each test runs
the same path a caller's document does. The behaviours pinned fall into four groups:

* **Reading** — a drawn grid, and ruled or shaded rows whose columns come from whitespace; the table
  growing only on evidence the page states (a line crossing its edge, text in its own ruled rows,
  a row just outside it that fits its columns).
* **Declining** — every check that compares the table with the page, each shown declining the shape
  it exists for.
* **Wiring** — each check's call site is broken on purpose and the fixture that depends on it is
  shown to change, so a check that is defined but no longer called cannot pass silently (#348 had
  six "negative controls" that pinned a function while its call site could be deleted).
* **Titles** (M145) — each rule that picks a table's caption, shown deciding the shape it exists for.
  Every one was also removed in turn and its test seen to fail; ``tools/table_corpus_check.py``
  measures the same rules against an answer key of real pages.
* **The reply** — pagination, caps, rotation, continuation, and ``extract_text``'s ``table_pages``.
"""

from __future__ import annotations

import collections
import threading

import pymupdf as fitz
import pytest

from klarpdf.mcp_bridge import queries, tables

FONT = 9
PITCH = 16

ASSETS = [
    ("Cash and cash equivalents", "39,544", "35,934"),
    ("Marketable securities", "22,855", "18,763"),
    ("Accounts receivable, net", "31,398", "39,777"),
    ("Vendor non-trade receivables", "27,509", "33,180"),
    ("Inventories", "11,092", "5,718"),
    ("Other current assets", "17,420", "14,585"),
    ("Total current assets", "149,818", "147,957"),
    ("Marketable securities, non-current", "84,118", "77,723"),
    ("Property, plant and equipment, net", "51,431", "49,834"),
    ("Intangible assets, net", "(20,342)", "(11,093)"),
    ("Other non-current assets", "77,557", "72,634"),
    ("Total assets", "342,582", "347,561"),
]


def _right(page: fitz.Page, x: float, y: float, text: str, size: float = FONT) -> None:
    """Right-align ``text`` so it ends at ``x`` — how a statement sets its figures."""
    page.insert_text((x - fitz.get_text_length(text, fontsize=size), y), text, fontsize=size)


def _statement(
    page: fitz.Page,
    top: float,
    rows,
    *,
    labels: tuple[float, ...] = (60,),
    columns: tuple[float, ...] = (420, 520),
    rule: tuple[float, float] = (55, 540),
    ruled_rows: range | None = None,
    bands: bool = False,
) -> float:
    """Draw a row-ruled statement and return the y of its last row's baseline.

    Each row gets a rule above it (``ruled_rows`` narrows which do) and the last row a rule below.
    ``labels`` cycles label indents, so a ragged label column can be left out of the finder's region.
    ``bands`` shades alternate rows full-width instead of drawing rules.
    """
    ruled_rows = range(len(rows)) if ruled_rows is None else ruled_rows
    for i, (label, *figures) in enumerate(rows):
        y = top + i * PITCH
        if bands:
            fill = (0.8, 0.93, 1.0) if i % 2 == 0 else (1, 1, 1)
            page.draw_rect(fitz.Rect(rule[0], y, rule[1], y + PITCH), color=None, fill=fill)
        elif i in ruled_rows:
            page.draw_line((rule[0], y), (rule[1], y))
        if label:
            page.insert_text((labels[i % len(labels)], y + 11), label, fontsize=FONT)
        for x, figure in zip(columns, figures):
            if figure:
                _right(page, x, y + 11, figure)
    bottom = top + len(rows) * PITCH
    if not bands and (len(rows) - 1) in ruled_rows:
        page.draw_line((rule[0], bottom), (rule[1], bottom))
    return top + (len(rows) - 1) * PITCH + 11


def _save(doc: fitz.Document, tmp_path, name: str) -> str:
    path = str(tmp_path / name)
    doc.save(path)
    doc.close()
    return path


def _read(path: str, page: int = 1) -> tables.PageRead:
    return tables.read_page(fitz.open(path)[page - 1])


def _rows(result: tables.PageRead, index: int = 0) -> list[list[str]]:
    return result.tables[index]["rows"]


def _codes(result: tables.PageRead) -> list[str]:
    return [u["_code"] for u in result.unread]


def _expected(rows) -> list[list[str]]:
    return [list(row) for row in rows]


# ---------------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------------


@pytest.fixture
def statement_pdf(tmp_path) -> str:
    doc = fitz.open()
    _statement(doc.new_page(), 100, ASSETS)
    return _save(doc, tmp_path, "statement.pdf")


@pytest.fixture
def grid_pdf(tmp_path) -> str:
    """A drawn grid: every cell boxed, a header row, one cell holding an address with an underscore
    (the character PyMuPDF's own cell extraction lifted out of its word and put on a line of its
    own, TC-034)."""
    doc = fitz.open()
    page = doc.new_page()
    xs = [60, 200, 340, 480]
    ys = [100 + i * 22 for i in range(5)]
    for x in xs:
        page.draw_line((x, ys[0]), (x, ys[-1]))
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y))
    cells = [
        ["Name", "Contact", "Balance"],
        ["Alpha", "first_last@example.com", "1,234.50"],
        ["Beta", "second_person@example.com", "(98.10)"],
        ["Gamma", "n/a", "0.00"],
    ]
    for r, row in enumerate(cells):
        for c, text in enumerate(row):
            page.insert_text((xs[c] + 4, ys[r] + 15), text, fontsize=FONT)
    return _save(doc, tmp_path, "grid.pdf")


# ---------------------------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------------------------


def test_a_drawn_grid_is_read_cell_for_cell_with_its_header(grid_pdf):
    result = _read(grid_pdf)
    assert len(result.tables) == 1
    table = result.tables[0]
    assert table["reader"] == "grid"
    assert table["rows"] == [
        ["Name", "Contact", "Balance"],
        ["Alpha", "first_last@example.com", "1,234.50"],
        ["Beta", "second_person@example.com", "(98.10)"],
        ["Gamma", "n/a", "0.00"],
    ]
    assert table["header"] == ["Name", "Contact", "Balance"]


def test_ruled_rows_read_whole_labels_and_whole_figures(statement_pdf):
    """Columns from whitespace: nothing is cut, negatives keep both brackets, no header is claimed."""
    result = _read(statement_pdf)
    assert len(result.tables) == 1
    table = result.tables[0]
    assert table["reader"] == "ruled"
    assert table["rows"] == _expected(ASSETS)
    assert table["header"] is None


def test_an_outdented_label_crossing_the_regions_edge_is_read_whole(tmp_path):
    """Totals outdented left of the indented rows the finder's region starts at (TEAM's balance
    sheet, Cisco's cash flows). The line crossing the edge belongs to the table whole."""
    rows = [(("Total " if "Total" in label else "") + label.replace("Total ", ""), *f) for label, *f in ASSETS]
    doc = fitz.open()
    page = doc.new_page()
    _statement(page, 100, [(label if not label.startswith("Total") else "", *f) for label, *f in rows])
    for i, (label, *_f) in enumerate(rows):
        if label.startswith("Total"):
            page.insert_text((44, 100 + i * PITCH + 11), label, fontsize=FONT)
    path = _save(doc, tmp_path, "outdented.pdf")
    result = _read(path)
    assert len(result.tables) == 1
    got = result.tables[0]["rows"]
    assert ["Total current assets", "149,818", "147,957"] in got
    assert ["Total assets", "342,582", "347,561"] in got
    assert result.tables[0]["bbox"].x0 <= 44


def test_a_label_column_left_outside_the_region_is_taken_in_when_its_bands_run_under_it(tmp_path):
    """QCOM's statement of operations: ragged labels form no column the finder can see, so its
    region starts at the figures — but the shaded bands run under the labels too."""
    rows = [(label, *f) for label, *f in ASSETS]
    rows[4] = ("", *rows[4][1:])  # one row with no label, so it is the bands and not every-row alignment
    doc = fitz.open()
    _statement(doc.new_page(), 100, rows, labels=(60, 66, 72, 78, 64), bands=True)
    path = _save(doc, tmp_path, "banded.pdf")
    result = _read(path)
    assert len(result.tables) == 1
    got = result.tables[0]["rows"]
    assert ["Cash and cash equivalents", "39,544", "35,934"] in got
    assert ["", "11,092", "5,718"] in got


def test_a_label_column_with_no_ruling_under_it_is_reported_beside_the_table(tmp_path):
    """The same statement drawn with rules under the figures only: the labels are not the table's
    rows by any drawn evidence, so they are not taken in — and a table of figures is never returned
    without saying there is text level with its rows beside it (TC-030)."""
    rows = [(label, *f) for label, *f in ASSETS]
    rows[4] = ("", *rows[4][1:])
    doc = fitz.open()
    _statement(doc.new_page(), 100, rows, labels=(60, 66, 72, 78, 64), rule=(360, 540))
    path = _save(doc, tmp_path, "figures_ruled.pdf")
    result = _read(path)
    if result.tables:
        labels = {row[0] for row in result.tables[0]["rows"]}
        if "Cash and cash equivalents" not in labels:
            assert "beside" in _codes(result)


def test_a_first_row_above_the_ruling_and_a_total_below_it_are_taken_in(tmp_path):
    """A list ruled under each row has no rule above its first row; a total is set apart below the
    last rule. Both fit the table's columns at its own row spacing."""
    doc = fitz.open()
    page = doc.new_page()
    _statement(page, 100, ASSETS, ruled_rows=range(1, len(ASSETS) - 1))
    path = _save(doc, tmp_path, "edges.pdf")
    result = _read(path)
    assert len(result.tables) == 1
    got = result.tables[0]["rows"]
    assert got[0] == list(ASSETS[0])
    assert got[-1] == list(ASSETS[-1])


def test_a_title_above_a_table_is_its_title_and_not_a_row(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((230, 90), "CONDENSED BALANCE SHEETS", fontsize=FONT, fontname="hebo")
    _statement(page, 100, ASSETS)
    path = _save(doc, tmp_path, "titled.pdf")
    result = _read(path)
    assert len(result.tables) == 1
    assert all("CONDENSED" not in cell for row in _rows(result) for cell in row)
    assert result.tables[0]["title"] == "CONDENSED BALANCE SHEETS"


def test_a_linked_heading_is_navigation_rather_than_a_title(tmp_path):
    """Every filing prints a "Table of Contents" link in its top margin, and it was handed back as
    the title of the statement below it — Cisco p61, Broadcom p49 and salesforce p4 alike (TC-039).
    The same heading without the link is an ordinary caption, and still titles the table."""
    titles = {}
    for linked in (True, False):
        doc = fitz.open()  # one page each: a heading repeated on the page before is a running header
        page = doc.new_page()
        heading = fitz.Rect(55, 28, 150, 40)
        page.insert_text((heading.x0, heading.y1 - 2), "Table of Contents", fontsize=FONT, fontname="hebo")
        if linked:
            page.insert_link({"kind": fitz.LINK_GOTO, "from": heading, "page": 0})
        _statement(page, 100, ASSETS)
        titles[linked] = _read(_save(doc, tmp_path, f"linked_{linked}.pdf")).tables[0]["title"]

    assert titles[True] is None
    assert titles[False] == "Table of Contents"


def test_a_two_up_list_whose_rows_line_up_is_read_as_one_table(tmp_path):
    """The owner's reading of a list printed in two halves (2026-09-13): four columns, not half a
    list. Each half is ruled on its own, so nothing drawn joins them; every row of one half has a
    row of the other on the same line."""
    left = [(f"State {chr(65 + i)}", str(100 + i)) for i in range(12)]
    right = [(f"State {chr(77 + i)}", str(200 + i)) for i in range(12)]
    doc = fitz.open()
    page = doc.new_page()
    _statement(page, 100, left, labels=(60,), columns=(230,), rule=(55, 235))
    _statement(page, 100, right, labels=(300,), columns=(470,), rule=(295, 475))
    path = _save(doc, tmp_path, "two_up.pdf")
    result = _read(path)
    assert len(result.tables) == 1
    assert _rows(result)[0] == ["State A", "100", "State M", "200"]


DOLLAR = 372
"""Where a statement's first `$` column sits: 16 pt clear of the widest figure, so MuPDF keeps the
`$` and the figure as two text lines. Set a few points closer and it joins them into one line,
which is a different shape — the one ``one_run_row`` builds on purpose."""


def _dollar_statement(
    page: fitz.Page,
    top: float,
    rows,
    *,
    dollar_rows: tuple[int, ...] = (0, 11),
    one_run_row: int | None = None,
) -> None:
    """A statement set the way SEC filings set one: a two-line period heading over each figure
    column, a `$` printed apart from its figure on ``dollar_rows``, and a rule above each row.

    ``one_run_row`` prints that row's first `$` and figure as a single run of text — the `$` at the
    `$` column and the figure ending at the figure column, joined by spaces — which is how Apple
    prints ``"$ 109,417"`` beside rows that print the two apart."""
    page.draw_line((55, top), (540, top))
    for (x0, x1), (month, year) in (((DOLLAR, 420), ("June 27,", "2026")), ((460, 520), ("June 28,", "2025"))):
        for text, dy in ((month, 11), (year, 23)):
            width = fitz.get_text_length(text, fontsize=FONT)
            page.insert_text(((x0 + x1) / 2 - width / 2, top + dy), text, fontsize=FONT)
    start = top + 2 * PITCH
    for i, (label, first, second) in enumerate(rows):
        y = start + i * PITCH
        page.draw_line((55, y), (540, y))
        page.insert_text((60, y + 11), label, fontsize=FONT)
        if i == one_run_row:
            run = "$"
            while fitz.get_text_length(run + " " + first, fontsize=FONT) < 420 - DOLLAR:
                run += " "
            page.insert_text((DOLLAR, y + 11), run + first, fontsize=FONT)
        else:
            if i in dollar_rows:
                page.insert_text((DOLLAR, y + 11), "$", fontsize=FONT)
            _right(page, 420, y + 11, first)
        if i in dollar_rows:
            page.insert_text((460, y + 11), "$", fontsize=FONT)
        _right(page, 520, y + 11, second)
    bottom = start + len(rows) * PITCH
    page.draw_line((55, bottom), (540, bottom))


def test_a_statement_reads_its_period_heading_and_its_dollar_signs_apart(tmp_path):
    """The heading's two printed lines become the first two rows; each `$` printed apart from its
    figure is a column of its own, empty where the page prints no `$`.

    PyMuPDF's finder locates only the rows between the first and the total — the two rows carrying
    `$` — so both arrive from outside the region with each `$` in whitespace no column covers. That
    they join, rather than the table silently starting at its second row, is the point."""
    doc = fitz.open()
    _dollar_statement(doc.new_page(), 80, ASSETS)
    path = _save(doc, tmp_path, "dollars.pdf")
    result = _read(path)
    assert len(result.tables) == 1
    rows = _rows(result)
    assert rows[0] == ["", "", "June 27,", "", "June 28,"]
    assert rows[1] == ["", "", "2026", "", "2025"]
    assert rows[2] == ["Cash and cash equivalents", "$", "39,544", "$", "35,934"]
    assert rows[3] == ["Marketable securities", "", "22,855", "", "18,763"]
    assert rows[-1] == ["Total assets", "$", "342,582", "$", "347,561"]


def test_a_dollar_sign_printed_in_one_run_with_its_figure_is_divided_by_the_rows_that_print_them_apart(
    tmp_path, monkeypatch
):
    """Apple's ``"$ 109,417"``: one run of text bridging the `$` column and the figure column, on a row
    inside the region, below another row inside it that prints the two apart. That row states the
    boundary, so the merged column is divided and the run stays whole in the figure's cell. With the
    division disabled the two columns stay merged, the row printing them apart holds two pieces in
    one cell, and the table declines — which is what shows the division is wired in."""
    doc = fitz.open()
    _dollar_statement(doc.new_page(), 80, ASSETS, dollar_rows=(0, 3, 11), one_run_row=6)
    path = _save(doc, tmp_path, "one_run.pdf")
    result = _read(path)
    assert len(result.tables) == 1
    rows = _rows(result)
    assert ["Vendor non-trade receivables", "$", "27,509", "$", "33,180"] in rows
    total_current = next(row for row in rows if row[0] == "Total current assets")
    assert total_current[1] == "" and total_current[2].startswith("$") and total_current[2].endswith("149,818")

    monkeypatch.setattr(tables, "_divide", lambda columns, rows, spanning: columns)
    assert "side_by_side" in _codes(_read(path))


def test_a_banded_block_drawn_as_a_box_is_read_from_its_bands_not_from_the_box(tmp_path):
    """Alphabet's stockholders'-equity roll-forward (10-K p54, TC-039). The shading is drawn in
    column-wide pieces, so the finder stitches the page's boxes into a "grid" whose rows are whole
    blocks. Read as drawn cells that returns two cells holding twelve values joined by newlines,
    with the label column — printed outside the box — dropped without a word. The page's own bands
    cross every cell of each drawn row, which says those rows are not the page's rows, so the region
    is left to the reader that takes its rows from the bands."""
    doc = fitz.open()
    page = doc.new_page()
    bottom = 100 + len(ASSETS) * PITCH
    for i, (label, *figures) in enumerate(ASSETS):
        y = 100 + i * PITCH
        page.draw_rect(fitz.Rect(240, y, 480, y + PITCH), color=None, fill=(0.8, 0.93, 1.0) if i % 2 == 0 else (1, 1, 1))
        page.insert_text((60, y + 11), label, fontsize=FONT)
        for x, figure in zip((355, 475), figures):
            _right(page, x, y + 11, figure)
    page.draw_rect(fitz.Rect(240, 100, 480, bottom), color=(0, 0, 0), fill=None)
    page.draw_line((240, 100 + 6 * PITCH), (480, 100 + 6 * PITCH))
    page.draw_line((360, 100), (360, bottom))
    path = _save(doc, tmp_path, "banded_block.pdf")

    result = _read(path)
    assert len(result.tables) == 1
    table = result.tables[0]
    assert table["reader"] == "ruled"
    assert table["rows"] == _expected(ASSETS)


def test_dot_leaders_are_not_read_into_cells(tmp_path):
    """A dot-leader statement (the SpaceX prospectus p251, TC-039). Set far enough from its label,
    the leader gets a text line of its own and then shares the label's column, and the page
    declined; set closer it joins the label's line, and its dots were read into the label's cell.
    Both pages here hold the same statement, and both must read the same."""
    doc = fitz.open()
    for gap in (12, 4):
        page = doc.new_page()
        for i, (label, *figures) in enumerate(ASSETS):
            y = 100 + i * PITCH
            page.draw_line((55, y), (540, y))
            page.insert_text((60, y + 11), label, fontsize=FONT)
            page.insert_text((60 + fitz.get_text_length(label, fontsize=FONT) + gap, y + 11), "." * 40, fontsize=FONT)
            for x, figure in zip((420, 520), figures):
                _right(page, x, y + 11, figure)
        page.draw_line((55, 100 + len(ASSETS) * PITCH), (540, 100 + len(ASSETS) * PITCH))
    path = _save(doc, tmp_path, "leaders.pdf")

    for page_number in (1, 2):
        assert _rows(_read(path, page_number)) == _expected(ASSETS)


def test_a_chart_beside_a_table_is_named_and_not_taken_in(tmp_path):
    """NADA's service-labor page: a bar chart to the left of a ruled table. The chart's gridlines stop
    short of the table, so nothing drawn joins them; the table comes back exactly, and the chart text
    level with its rows is named rather than read (TC-038)."""
    rows = [(f"Line item {i + 1}", f"{(i + 1) * 1111:,}", f"{(i + 2) * 777:,}") for i in range(12)]
    doc = fitz.open()
    page = doc.new_page()
    _statement(page, 100, rows, labels=(330,), columns=(470, 535), rule=(325, 540))
    for i in range(0, len(rows) + 1, 3):
        page.draw_line((60, 100 + i * PITCH), (250, 100 + i * PITCH))
    for i, tick in enumerate(["$40", "30", "20", "10", "0"]):
        page.insert_text((36, 100 + i * 3 * PITCH + 3), tick, fontsize=FONT)
    page.insert_text((110, 100 + 2 * PITCH + 11), "$31.48", fontsize=FONT)
    page.insert_text((180, 100 + 5 * PITCH + 11), "$15.46", fontsize=FONT)
    path = _save(doc, tmp_path, "chart_beside.pdf")
    result = _read(path)
    assert len(result.tables) == 1
    assert _rows(result) == _expected(rows)
    assert "beside" in _codes(result)


# ---------------------------------------------------------------------------------------------
# Declining
# ---------------------------------------------------------------------------------------------


def test_text_running_across_a_drawn_grids_border_declines(tmp_path):
    """A chart's frame and gridlines look like a grid; its labels cross them (TC-038)."""
    doc = fitz.open()
    page = doc.new_page()
    for y in (100, 130, 160, 190):
        page.draw_line((100, y), (400, y))
    for x in (100, 250, 400):
        page.draw_line((x, 100), (x, 190))
    page.insert_text((105, 120), "Domestic", fontsize=FONT)
    page.insert_text((230, 147), "Import 47", fontsize=FONT)  # straddles the column line at x=250
    page.insert_text((260, 180), "54", fontsize=FONT)
    path = _save(doc, tmp_path, "chart_grid.pdf")
    result = _read(path)
    assert not result.tables
    assert "grid_crossed" in _codes(result)


def _bar_chart(page: fitz.Page) -> None:
    """Bars with white outlines standing on gridlines inside a frame, each with its value printed
    above it: how NADA's dealer report draws the chart on its p4 (#354)."""
    left, right, top, bottom = 80, 320, 100, 300
    for y in range(top, bottom + 1, 50):
        page.draw_line((left, y), (right, y), color=(0.6, 0.6, 0.6), width=0.5)
    for x in (left, right):
        page.draw_line((x, top), (x, bottom), width=0.5)
    for i, (bar_top, label) in enumerate([(90, "19.1%"), (130, "17.6%"), (160, "16.8%"), (75, "20.9%"), (230, "10.2%")]):
        x = 90 + 45 * i
        page.draw_rect(fitz.Rect(x, bar_top + 20, x + 30, bottom), color=(1, 1, 1), fill=(0.5, 0.7, 0.9), width=0.5)
        page.insert_text((x + 2, bar_top + 16), label, fontsize=FONT)


def test_a_bar_chart_drawn_over_its_gridlines_is_declined(tmp_path):
    """The finder boxes the band between two gridlines and each bar top standing inside it, so its
    cells overlap. A table's never do."""
    doc = fitz.open()
    _bar_chart(doc.new_page())
    path = _save(doc, tmp_path, "bar_chart.pdf")
    result = _read(path)
    assert not result.tables
    assert _codes(result) == ["overlapping_cells"]


def test_a_merged_cell_is_not_an_overlap(tmp_path):
    """A header cell spanning two columns is one drawn cell, not two overlapping ones."""
    doc = fitz.open()
    page = doc.new_page()
    for y in (100, 120, 140, 160, 180):
        page.draw_line((100, y), (400, y))
    for x in (100, 200, 400):
        page.draw_line((x, 100), (x, 180))
    page.draw_line((300, 120), (300, 180))  # no line between the two figure columns in the header
    page.insert_text((105, 114), "Segment", fontsize=FONT)
    page.insert_text((265, 114), "Net sales", fontsize=FONT)
    for i, (name, first, second) in enumerate([("Americas", "12", "14"), ("Europe", "8", "9"), ("Japan", "3", "4")]):
        y = 134 + 20 * i
        page.insert_text((105, y), name, fontsize=FONT)
        page.insert_text((205, y), first, fontsize=FONT)
        page.insert_text((305, y), second, fontsize=FONT)
    path = _save(doc, tmp_path, "merged_header.pdf")
    result = _read(path)
    assert not result.unread
    assert _rows(result) == [["Segment", "Net sales", ""], ["Americas", "12", "14"], ["Europe", "8", "9"], ["Japan", "3", "4"]]


def test_a_rule_through_text_declines(tmp_path):
    """Gridlines drawn through labels are not row separators."""
    doc = fitz.open()
    page = doc.new_page()
    _statement(page, 100, ASSETS)
    page.draw_line((55, 100 + 3 * PITCH + 8), (540, 100 + 3 * PITCH + 8))  # through row 4's letters
    path = _save(doc, tmp_path, "rule_through.pdf")
    result = _read(path)
    assert not result.tables
    assert "rule_through_text" in _codes(result)


def test_text_printed_twice_to_look_bold_declines(tmp_path):
    """A document that doubles its glyphs offset by a fraction of a point (a prospectus does this for
    bold): two overlapping pieces of text in one cell are not two columns."""
    doc = fitz.open()
    page = doc.new_page()
    _statement(page, 100, ASSETS)
    page.insert_text((60.4, 100 + 2 * PITCH + 11), "Accounts receivable, net", fontsize=FONT)
    path = _save(doc, tmp_path, "doubled.pdf")
    result = _read(path)
    assert not result.tables
    assert "side_by_side" in _codes(result)


def test_rows_stacked_in_one_ruled_band_decline_when_nothing_says_how_to_divide_them(tmp_path):
    """A band holding two rows of figures and a lone line between them: the lone line belongs to
    the row above or the row below and the page does not say which."""
    rows = list(ASSETS)
    doc = fitz.open()
    page = doc.new_page()
    _statement(page, 100, rows, ruled_rows=[i for i in range(len(rows)) if i != 5])
    page.insert_text((60, 100 + 5 * PITCH + 4), "of which restricted", fontsize=FONT)
    path = _save(doc, tmp_path, "stacked.pdf")
    result = _read(path)
    assert not result.tables
    assert "stacked" in _codes(result)


def test_two_columns_of_prose_between_rules_are_not_read_as_a_table(tmp_path):
    """Header and footer rules around two text columns: one band of side-by-side lines, and no
    band anywhere holding a single row, so the ruling marks no rows."""
    doc = fitz.open()
    page = doc.new_page()
    for y in (90, 90 + 14 * 13):
        page.draw_line((55, y), (540, y))
    page.draw_line((55, 60), (540, 60))
    for i in range(12):
        y = 104 + i * 13
        page.insert_text((60, y), f"left column line {i} of some prose", fontsize=FONT)
        page.insert_text((310, y), f"right column line {i} continues here", fontsize=FONT)
    path = _save(doc, tmp_path, "prose.pdf")
    result = _read(path)
    assert not result.tables


def test_a_decline_with_no_region_points_at_the_text_rather_than_the_whole_page(tmp_path):
    """Nothing located means no region to name, and the whole page was handed back — salesforce p5
    reported [0, 0, 612, 792] where a sub-page box had been shipped before (TC-039). The text's own
    box is what a caller can clip, search or render."""
    doc = fitz.open()
    page = doc.new_page()
    for i in range(20):
        page.insert_text((80, 120 + i * 14), f"An ordinary paragraph line {i}, with nothing tabular about it.", fontsize=FONT)
    path = _save(doc, tmp_path, "prose_page.pdf")

    result = _read(path)
    assert not result.tables
    assert _codes(result) == ["unruled"]
    box = fitz.Rect(result.unread[0]["bbox"])
    page_rect = fitz.open(path)[0].rect
    assert box.y0 > 100 and box.x0 > 50
    assert box.width < page_rect.width and box.height < page_rect.height


def test_a_page_with_no_text_says_it_is_an_image(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    for y in range(100, 400, 20):
        page.draw_line((60, y), (500, y))
    path = _save(doc, tmp_path, "image.pdf")
    result = _read(path)
    assert not result.tables
    assert _codes(result) == ["no_text"]
    assert "render_page" in result.unread[0]["suggestion"]
    assert "extract_text" not in result.unread[0]["suggestion"]


def test_rows_printed_as_single_runs_of_text_say_so(tmp_path):
    """A line-printer payslip: each row is one run of text and its columns are spaces inside it."""
    doc = fitz.open()
    page = doc.new_page()
    for i, (label, a, b) in enumerate(ASSETS):
        y = 100 + i * PITCH
        page.draw_line((55, y), (540, y))
        page.insert_text((60, y + 11), f"{label:<40}{a:>12}{b:>12}", fontsize=FONT, fontname="cour")
    page.draw_line((55, 100 + len(ASSETS) * PITCH), (540, 100 + len(ASSETS) * PITCH))
    path = _save(doc, tmp_path, "single_runs.pdf")
    result = _read(path)
    assert not result.tables
    assert set(_codes(result)) <= {"single_runs", "no_region"}


def test_text_below_one_point_is_not_read_into_cells(tmp_path):
    """A market report carries a regular-weight copy of a row at 0.1 pt, drawn just before the row a
    reader sees. The owner's decision: it is not read (MIN_TEXT_SIZE)."""
    doc = fitz.open()
    page = doc.new_page()
    y = 100 + 6 * PITCH + 11
    page.insert_text((60, y - 5), "Total current assets", fontsize=0.1)
    _right(page, 420, y - 5, "999,999", size=0.1)
    _statement(page, 100, ASSETS)
    path = _save(doc, tmp_path, "tiny.pdf")
    result = _read(path)
    assert len(result.tables) == 1
    assert result.tables[0]["rows"] == _expected(ASSETS)


# ---------------------------------------------------------------------------------------------
# Wiring — each check's call site, broken on purpose
# ---------------------------------------------------------------------------------------------


def test_the_word_check_is_wired_into_the_read(statement_pdf, monkeypatch):
    """Sabotage assembly so one word goes missing: the independent check must decline the table.
    Then disable the check too, and the damaged table would have shipped — which is what proves the
    first half exercised the call site rather than a function nobody calls."""
    real = tables._cell_text

    def lossy(lines):
        text = real(lines)
        return text.replace("Inventories", "") if text == "Inventories" else text

    monkeypatch.setattr(tables, "_cell_text", lossy)
    result = _read(statement_pdf)
    assert not result.tables
    assert "unaccounted" in _codes(result)

    monkeypatch.setattr(tables, "_check_accounted", lambda table, inventory: None)
    damaged = _read(statement_pdf)
    assert damaged.tables and ["", "11,092", "5,718"] in damaged.tables[0]["rows"]


def test_the_side_by_side_check_is_wired_into_the_read(tmp_path, monkeypatch):
    doc = fitz.open()
    page = doc.new_page()
    _statement(page, 100, ASSETS)
    page.insert_text((60.4, 100 + 2 * PITCH + 11), "Accounts receivable, net", fontsize=FONT)
    path = _save(doc, tmp_path, "doubled.pdf")
    assert "side_by_side" in _codes(_read(path))

    real_visual = tables._visual_rows
    calls = collections.Counter()

    def counting(lines):
        calls["n"] += 1
        return real_visual(lines)

    monkeypatch.setattr(tables, "_visual_rows", counting)
    _read(path)
    assert calls["n"] > 0


def test_the_grid_crossing_check_is_what_declines_a_chart(tmp_path, monkeypatch):
    doc = fitz.open()
    page = doc.new_page()
    for y in (100, 130, 160, 190):
        page.draw_line((100, y), (400, y))
    for x in (100, 250, 400):
        page.draw_line((x, 100), (x, 190))
    page.insert_text((105, 120), "Domestic", fontsize=FONT)
    page.insert_text((230, 147), "Import 47", fontsize=FONT)
    page.insert_text((260, 180), "54", fontsize=FONT)
    path = _save(doc, tmp_path, "chart_grid.pdf")
    assert "grid_crossed" in _codes(_read(path))

    real = tables._read_grid

    def permissive(found, inventory, edges):
        try:
            return real(found, inventory, edges)
        except tables._Decline as decline:
            if decline.reason != "grid_crossed":
                raise
            raise tables._NotATable() from None

    monkeypatch.setattr(tables, "_read_grid", permissive)
    assert "grid_crossed" not in _codes(_read(path))


def test_the_overlapping_cells_check_is_what_declines_a_bar_chart(tmp_path, monkeypatch):
    """Blind the check and the chart comes back as a table with bar labels stacked in one cell, the
    shape #354 reported."""
    doc = fitz.open()
    _bar_chart(doc.new_page())
    path = _save(doc, tmp_path, "bar_chart.pdf")
    monkeypatch.setattr(tables, "_overlapping", lambda cells: None)
    table = _read(path).tables[0]
    assert table["reader"] == "grid"
    assert table["rows"][0][0] == "19.1%\n17.6%"


def test_the_drawn_row_check_is_what_saves_a_banded_block(tmp_path, monkeypatch):
    """Blind the check — by telling it the page draws nothing across — and the twelve rows come
    back merged into one drawn row per block, which is what GOOGL p54 returned (TC-039)."""
    doc = fitz.open()
    page = doc.new_page()
    bottom = 100 + len(ASSETS) * PITCH
    for i, (label, *figures) in enumerate(ASSETS):
        y = 100 + i * PITCH
        page.draw_rect(fitz.Rect(240, y, 480, y + PITCH), color=None, fill=(0.8, 0.93, 1.0) if i % 2 == 0 else (1, 1, 1))
        page.insert_text((60, y + 11), label, fontsize=FONT)
        for x, figure in zip((355, 475), figures):
            _right(page, x, y + 11, figure)
    page.draw_rect(fitz.Rect(240, 100, 480, bottom), color=(0, 0, 0), fill=None)
    page.draw_line((240, 100 + 6 * PITCH), (480, 100 + 6 * PITCH))
    page.draw_line((360, 100), (360, bottom))
    path = _save(doc, tmp_path, "banded_block_blinded.pdf")
    assert _rows(_read(path)) == _expected(ASSETS)

    monkeypatch.setattr(tables, "_horizontal_edges", lambda page: [])
    table = _read(path).tables[0]
    assert table["reader"] == "grid"
    assert "\n" in table["rows"][0][0]


SEGMENTS = [(f"Segment {chr(65 + i)}", f"{3 + i},{401 + i}") for i in range(12)]
LONG_SEGMENTS = [("Segment A, Americas and Canada", SEGMENTS[0][1]), *SEGMENTS[1:]]


def _two_notes(tmp_path, name: str, *, heading: str | None = None, segments=SEGMENTS,
               rule_through: int | None = None) -> str:
    """A statement, a paragraph, then a one-figure table, all in one ruled region — the shape of
    Apple's 10-Q p14. ``heading`` adds a heading row between the paragraph and the second table:
    ``"fits"`` sets ``Amount`` over the figures, ``"bridging"`` sets a piece reaching from the label
    column into the figures. ``rule_through`` draws a rule through that row of the second table."""
    doc = fitz.open()
    page = doc.new_page()
    y = 60
    for label, *figures in ASSETS:
        page.draw_line((55, y), (540, y))
        page.insert_text((60, y + 11), label, fontsize=FONT)
        for x, figure in zip((420, 520), figures):
            _right(page, x, y + 11, figure)
        y += PITCH
    page.draw_line((55, y), (540, y))
    for line in (
        "The total vesting-date fair value of restricted stock units was $12.3 billion and $10.9 billion",
        "for the three- and nine-month periods, and share-based compensation expense for each reportable",
        "segment was as follows:",
    ):
        y += 13
        page.insert_text((60, y), line, fontsize=FONT)
    if heading:
        y += 16
        page.insert_text((60, y), "Segment", fontsize=FONT)
        if heading == "fits":
            _right(page, 300, y, "Amount")
        else:
            page.insert_text((150, y), "Amount, in millions", fontsize=FONT)
        y += 6
    else:
        y += 14
    for i, (label, figure) in enumerate(segments):
        page.draw_line((55, y), (540, y))
        page.insert_text((60, y + 11), label, fontsize=FONT)
        _right(page, 300, y + 11, figure)
        if i == rule_through:
            page.draw_line((55, y + 8), (540, y + 8))
        y += PITCH
    page.draw_line((55, y), (540, y))
    return _save(doc, tmp_path, name)


def _recover_the_region(path: str):
    page = fitz.open(path)[0]
    reading = tables._read_with(
        page, vertical_strategy="text", horizontal_strategy="lines",
        min_words_vertical=tables._MIN_WORDS_VERTICAL,
    )
    assert len(reading.found) == 1
    return tables._recover(reading.found[0], tables._page_inventory(page), reading.edges, [], [])


def _centre_of(path: str, text: str) -> fitz.Point:
    hits = fitz.open(path)[0].search_for(text)
    assert len(hits) == 1, text
    return hits[0].tl + (hits[0].br - hits[0].tl) * 0.5


def test_a_region_holding_two_tables_is_read_in_the_parts_its_prose_divides(tmp_path):
    """Apple's 10-Q p14 carries three notes in one located region and a retirement statement two
    (TC-039). Read as one, their columns come from layouts with nothing to do with each other and
    the whole page declined; the prose between them is the boundary the finder missed."""
    recovered, refused = _recover_the_region(_two_notes(tmp_path, "two_notes.pdf"))

    assert not refused
    assert [len(table["rows"]) for table in recovered] == [12, 12]
    assert recovered[0]["rows"] == _expected(ASSETS)
    assert recovered[1]["rows"][0][0] == "Segment A"
    assert recovered[1]["rows"][-1][0] == "Segment L"


def test_table_text_a_part_cannot_take_in_declines_that_part(tmp_path):
    """The band that divides a region can hold a table's own heading as well as prose. A heading the
    table below could not take in was dropped with the band — in no table and no declined region —
    and the table came back headed by what was left (a retirement statement's "period" for "For
    this statement period"). A table with text pressed against it that its rows cannot hold is not
    known to be whole, so it is declined, and the declined region names the heading too."""
    path = _two_notes(tmp_path, "bridging_heading.pdf", heading="bridging", segments=LONG_SEGMENTS)
    recovered, refused = _recover_the_region(path)

    assert [table["rows"] for table in recovered] == [_expected(ASSETS)]
    assert [decline.reason for _, decline in refused] == ["unaccounted"]
    assert refused[0][0].contains(_centre_of(path, "Amount, in millions"))


def test_table_text_beside_a_declined_part_widens_the_declined_region(tmp_path):
    """The same heading, over a table that declines on its own account (a rule through one of its
    rows): the declined region grows to take the heading in, rather than leaving it in neither list —
    how a statement's beneficiary row and a 10-Q's date headings went unmentioned (TC-039)."""
    path = _two_notes(tmp_path, "declined_part.pdf", heading="fits", segments=LONG_SEGMENTS, rule_through=5)
    recovered, refused = _recover_the_region(path)

    assert [table["rows"] for table in recovered] == [_expected(ASSETS)]
    assert [decline.reason for _, decline in refused] == ["rule_through_text"]
    assert refused[0][0].contains(_centre_of(path, "Amount"))


def test_recovery_is_offered_for_ambiguous_rows_and_never_for_a_chart(statement_pdf, monkeypatch):
    """The call site and `_RECOVERABLE` together. A region whose rows or columns are ambiguous is
    read again in parts; one whose drawn lines run through text never is, because the parts would
    be cut along those same lines — which is how a schools poster's chart axis became the header
    rows of a table (TC-039, `report_CA_06_california.pdf` p4)."""
    offered: list[str] = []

    def spy(found, inventory, edges, claimed, others):
        offered.append("offered")
        return [], []

    def refuse(reason):
        def fail(*args, **kwargs):
            raise tables._Decline(reason, "as if the region failed this way")

        return fail

    monkeypatch.setattr(tables, "_recover", spy)
    for reason, expected in (("side_by_side", 1), ("stacked", 1), ("rule_through_text", 0), ("grid_crossed", 0)):
        offered.clear()
        monkeypatch.setattr(tables, "_read_ruled", refuse(reason))
        result = _read(statement_pdf)
        assert not result.tables, reason
        assert len(offered) == expected, reason


def test_the_every_row_beside_rule_is_what_joins_a_two_up_list(tmp_path, monkeypatch):
    left = [(f"State {chr(65 + i)}", str(100 + i)) for i in range(12)]
    right = [(f"State {chr(77 + i)}", str(200 + i)) for i in range(12)]
    doc = fitz.open()
    page = doc.new_page()
    _statement(page, 100, left, labels=(60,), columns=(230,), rule=(55, 235))
    _statement(page, 100, right, labels=(300,), columns=(470,), rule=(295, 475))
    path = _save(doc, tmp_path, "two_up.pdf")
    assert len(_read(path).tables) == 1

    monkeypatch.setattr(tables, "_same_row", lambda a, b: False)
    assert len(_read(path).tables) != 1 or _rows(_read(path))[0] != ["State A", "100", "State M", "200"]


# ---------------------------------------------------------------------------------------------
# Titles (M145) — each rule shown deciding the shape it exists for
# ---------------------------------------------------------------------------------------------


def _bold(page: fitz.Page, x: float, y: float, text: str, size: float = FONT) -> None:
    page.insert_text((x, y), text, fontsize=size, fontname="hebo")


def _titled(tmp_path, name: str, heading, top: float = 200, **statement) -> str | None:
    """The title given to a statement at ``top``, under whatever ``heading`` draws above it."""
    doc = fitz.open()
    page = doc.new_page()
    heading(page)
    _statement(page, top, ASSETS, **statement)
    return _read(_save(doc, tmp_path, name)).tables[0]["title"]


def test_a_title_is_set_to_be_seen_as_one(tmp_path):
    """A line in the body's own type is prose, however short: the rule this replaced took
    "information upon which", a fragment of a sentence, for a survey table's title."""
    assert _titled(tmp_path, "bold.pdf", lambda p: _bold(p, 60, 185, "Balance Sheets")) == "Balance Sheets"
    assert _titled(tmp_path, "plain.pdf", lambda p: p.insert_text((60, 185), "Balance Sheets", fontsize=FONT)) is None


def test_a_title_printed_on_two_lines_is_one_title(tmp_path):
    """#355: NADA's report prints its title on two lines, and a rule that refused any block of two
    lines took the source note of the charts above instead."""
    two = "Average Number of New Vehicles Sold Per Dealership\nand Selling Price, by Year"
    title = _titled(tmp_path, "two_lines.pdf", lambda p: _bold(p, 60, 172, two))
    assert title == "Average Number of New Vehicles Sold Per Dealership and Selling Price, by Year"


def test_a_statements_heading_block_gives_its_name_not_the_section_above(tmp_path):
    """#355: Apple's and QCOM's statements came back titled "Item 1. …", the heading above the block
    that holds the company, the statement's name, its units and "(Unaudited)"."""
    def heading(page):
        _bold(page, 60, 110, "Item 1. Financial Statements")
        # From x 150 every line reaches the label column, so only the bracket rule can step over the
        # last two; set further right, they would pass for column headings.
        _bold(page, 150, 140, "ACME INC.\nCONSOLIDATED BALANCE SHEETS\n(In millions)\n(Unaudited)")
    assert _titled(tmp_path, "statement.pdf", heading) == "ACME INC. CONSOLIDATED BALANCE SHEETS"


def test_a_column_heading_left_above_the_table_is_stepped_over(tmp_path):
    """The table's box can leave a column heading above it, set small over the figures. A short title
    centred over the table misses the label column as well, and is still the title: Amazon's
    "Segment Information"."""
    def heading(page):
        _bold(page, 250, 160, "Segment Information")
        _bold(page, 440, 190, "Nine Months Ended", size=7)
    assert _titled(tmp_path, "column_heading.pdf", heading) == "Segment Information"


def test_one_block_of_text_may_stand_between_a_heading_and_its_table(tmp_path):
    """Apple's notes print a heading, a sentence introducing the table, then the table. A second block
    of prose means the heading heads more than the table, and there is no title."""
    intro = "The following table shows net sales by category (in millions):"

    def one(page):
        _bold(page, 60, 150, "Note 2 - Revenue")
        page.insert_text((60, 175), intro, fontsize=FONT)

    def two(page):
        _bold(page, 60, 120, "Note 2 - Revenue")
        page.insert_text((60, 145), "Net sales rose in every region this quarter.", fontsize=FONT)
        page.insert_text((60, 175), intro, fontsize=FONT)

    assert _titled(tmp_path, "one.pdf", one) == "Note 2 - Revenue"
    assert _titled(tmp_path, "two.pdf", two) is None


def test_a_sentence_set_in_bold_is_not_a_title(tmp_path):
    """A form sets its instructions in bold right above the part they govern (SSA-1)."""
    assert _titled(tmp_path, "form.pdf", lambda p: _bold(p, 60, 185, "Answer this item only if you are now retired.")) is None


def test_the_search_stops_at_another_objects_text(tmp_path):
    """A title is over its own table, not over something else: a chart declined above this table has
    a bold label that would otherwise be taken."""
    doc = fitz.open()
    page = doc.new_page()
    for y in (100, 130, 160, 190):
        page.draw_line((100, y), (400, y))
    for x in (100, 250, 400):
        page.draw_line((x, 100), (x, 190))
    _bold(page, 105, 120, "Domestic")
    page.insert_text((230, 147), "Import 47", fontsize=FONT)  # across a column line: the grid is declined
    _statement(page, 220, ASSETS)
    result = _read(_save(doc, tmp_path, "under_a_chart.pdf"))
    assert "grid_crossed" in _codes(result)
    assert result.tables[0]["title"] is None


def test_a_line_the_page_before_prints_in_the_same_place_is_a_running_header(tmp_path):
    """Notes pages repeat their section's name at the head of each page. An earlier draft of this rule
    titled QCOM's, NVIDIA's and Cisco's notes tables with it."""
    doc = fitz.open()
    for _ in range(2):
        page = doc.new_page()
        _bold(page, 150, 185, "Notes to Consolidated Financial Statements")
        _statement(page, 200, ASSETS)
    path = _save(doc, tmp_path, "running_header.pdf")
    assert [_read(path, n).tables[0]["title"] for n in (1, 2)] == ["Notes to Consolidated Financial Statements", None]


@pytest.mark.parametrize(("caption_y", "expected"), [(172, None), (192, "Cumulative Miles")])
def test_a_caption_under_a_figure_belongs_to_whichever_it_is_nearer(tmp_path, caption_y, expected):
    """Tesla's update sets a chart's title under the chart, and a table can follow. Nearer the figure,
    the line is its caption; nearer the table, the same line is the table's title."""
    def heading(page):
        page.draw_rect(fitz.Rect(55, 60, 540, 160), color=(0, 0, 0))
        _bold(page, 60, caption_y, "Cumulative Miles")
    assert _titled(tmp_path, f"caption_{caption_y}.pdf", heading) == expected


def _grid(page: fitz.Page, x0: float, y0: float, rows: list[tuple[str, str]]) -> None:
    widths, height = (110, 70), 20
    xs = [x0, x0 + widths[0], x0 + sum(widths)]
    for i in range(len(rows) + 1):
        page.draw_line((xs[0], y0 + i * height), (xs[-1], y0 + i * height))
    for x in xs:
        page.draw_line((x, y0), (x, y0 + len(rows) * height))
    for i, row in enumerate(rows):
        for x, text in zip(xs, row):
            page.insert_text((x + 4, y0 + i * height + 14), text, fontsize=FONT)


def test_a_title_in_its_own_band_is_a_heading_whatever_stands_above(tmp_path):
    """GE's guide heads a grid in white on a black band, right under a boxed note: nearer the note
    than the grid, and still the grid's title, because the band is its own."""
    doc = fitz.open()
    page = doc.new_page()
    page.draw_rect(fitz.Rect(55, 100, 540, 150), color=None, fill=(0, 0, 0))
    page.insert_text((60, 125), "Call a technician only after reading the guide.", fontsize=FONT, color=(1, 1, 1))
    page.draw_rect(fitz.Rect(55, 153, 540, 173), color=None, fill=(0, 0, 0))
    page.insert_text((60, 168), "Myth or Fact", fontsize=14, color=(1, 1, 1))
    _grid(page, 60, 200, [("Claim", "Answer"), ("Doors come off", "Myth"), ("Filters last", "Fact")])
    assert _read(_save(doc, tmp_path, "band.pdf")).tables[0]["title"] == "Myth or Fact"


def test_the_halves_of_a_list_printed_two_up_share_its_title(tmp_path):
    """NADA's report sets its state lists two and three across, titled over the first part only."""
    doc = fitz.open()
    page = doc.new_page()
    _bold(page, 60, 90, "Dealerships by State, 2025")
    _grid(page, 60, 100, [("State", "Dealers"), ("Alabama", "278"), ("Alaska", "27"), ("Arizona", "250")])
    _grid(page, 320, 100, [("State", "Dealers"), ("Nebraska", "153"), ("Nevada", "107")])
    result = _read(_save(doc, tmp_path, "two_up.pdf"))
    assert [t["title"] for t in result.tables] == ["Dealerships by State, 2025"] * 2


def test_text_that_cannot_be_read_stops_the_search(tmp_path):
    """GE's guide encodes a page's prose so that control characters stand where letters were, its full
    stops too, and the sentence test cannot see the prose for what it is."""
    assert _titled(tmp_path, "garbled.pdf", lambda p: _bold(p, 60, 185, "Syst\x03me certifi\x03 par l'IAPMO")) is None


def test_a_titles_small_bracketed_tail_does_not_hide_its_style(tmp_path):
    """NADA p12 ends a two-line title's last line with "(in billions of dollars)" in small type, whose
    characters outnumber the title's own on that line."""
    def heading(page):
        writer = fitz.TextWriter(page.rect)
        bold, plain = fitz.Font("hebo"), fitz.Font("helv")
        writer.append((60, 172), "Dealerships Total Service and", font=bold, fontsize=12)
        tail = writer.append((60, 188), "Parts Sales, 2025", font=bold, fontsize=12)[1]
        writer.append((tail.x + 3, 188), "(in billions of dollars)", font=plain, fontsize=7)
        writer.write_text(page)
    title = _titled(tmp_path, "tail.pdf", heading)
    assert title == "Dealerships Total Service and Parts Sales, 2025 (in billions of dollars)"


def test_labels_side_by_side_in_one_block_are_not_one_title(tmp_path):
    """A brochure prints "FIRST FLOOR" and "SECOND FLOOR" in one text block, over two plans."""
    doc = fitz.open()
    page = doc.new_page()
    writer = fitz.TextWriter(page.rect)
    font = fitz.Font("hebo")
    writer.append((60, 185), "FIRST FLOOR", font=font, fontsize=FONT)
    writer.append((330, 185), "SECOND FLOOR", font=font, fontsize=FONT)
    writer.write_text(page)
    _grid(page, 330, 200, [("Room", "Size"), ("Primary suite", "21 x 12"), ("Bedroom 4", "11 x 10")])
    assert _read(_save(doc, tmp_path, "floors.pdf")).tables[0]["title"] == "SECOND FLOOR"


def test_a_line_naming_nothing_is_stepped_over(tmp_path):
    """A year row or a chart's figures name nothing; the title is above them."""
    def heading(page):
        _bold(page, 60, 160, "Deferred Revenue")
        _bold(page, 60, 185, "2025")
    assert _titled(tmp_path, "year.pdf", heading) == "Deferred Revenue"


# ---------------------------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------------------------


def _rotated(tmp_path, draw, rotation: int, name: str) -> str:
    """A page that *displays* upright under ``/Rotate``: the content is drawn upright, placed on a
    page rotated the other way, and the page's rotation set so a viewer sees it upright — the real
    shape of a landscape exhibit, unlike merely setting /Rotate on upright content."""
    src = fitz.open()
    draw(src.new_page(width=612, height=500))
    doc = fitz.open()
    width, height = (500, 612) if rotation in (90, 270) else (612, 500)
    page = doc.new_page(width=width, height=height)
    page.show_pdf_page(page.rect, src, 0, rotate=rotation)
    page.set_rotation(rotation)
    return _save(doc, tmp_path, name)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_a_rotated_statement_reads_the_same_and_reports_an_unrotated_box(tmp_path, rotation):
    path = _rotated(tmp_path, lambda page: _statement(page, 60, ASSETS), rotation, f"rot{rotation}.pdf")
    reply = tables.tables(path, pages=[1])
    assert reply["count"] == 1
    table = reply["tables"][0]
    assert table["rows"] == _expected(ASSETS)
    box = fitz.Rect(table["bbox"]) + (-0.05, -0.05, 0.05, 0.05)
    words = fitz.open(path)[0].get_text("words")  # unrotated, like search and redact_regions
    covered = collections.Counter(w[4] for w in words if fitz.Rect(w[:4]) in box)
    cells = collections.Counter(token for row in table["rows"] for cell in row for token in cell.split())
    assert not cells - covered


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_a_rotated_grid_reads_the_same(tmp_path, rotation, grid_pdf):
    def draw(page):
        page.show_pdf_page(page.rect, fitz.open(grid_pdf), 0)

    path = _rotated(tmp_path, draw, rotation, f"grid{rotation}.pdf")
    reply = tables.tables(path, pages=[1])
    assert reply["count"] == 1
    assert reply["tables"][0]["rows"][1] == ["Alpha", "first_last@example.com", "1,234.50"]


# ---------------------------------------------------------------------------------------------
# The reply
# ---------------------------------------------------------------------------------------------


def test_a_whole_document_scan_is_refused(statement_pdf):
    with pytest.raises(ValueError, match="explicit page range"):
        tables.tables(statement_pdf, pages=[])


def test_every_page_asked_for_is_accounted_for(statement_pdf, tmp_path):
    doc = fitz.open()
    doc.insert_pdf(fitz.open(statement_pdf))
    doc.new_page().insert_text((72, 100), "Just a paragraph of text with no table at all.", fontsize=11)
    path = _save(doc, tmp_path, "mixed.pdf")
    reply = tables.tables(path, pages=[1, 2])
    pages_with_tables = {t["page"] for t in reply["tables"]}
    pages_declined = {u["page"] for u in reply["unread_regions"]}
    assert pages_with_tables | pages_declined == {1, 2}
    assert all(not k.startswith("_") for u in reply["unread_regions"] for k in u)


def test_the_reply_paginates(tmp_path):
    doc = fitz.open()
    for _ in range(3):
        _statement(doc.new_page(), 100, ASSETS)
    path = _save(doc, tmp_path, "three.pdf")
    first = tables.tables(path, pages=[1, 2, 3], max_tables=2)
    assert first["count"] == 2 and first["more_available"] and first["total_tables"] == 3
    rest = tables.tables(path, pages=[1, 2, 3], max_tables=2, offset=2)
    assert rest["count"] == 1 and not rest["more_available"]


def test_a_character_budget_bounds_the_reply_as_well_as_a_count(tmp_path):
    doc = fitz.open()
    for _ in range(3):
        _statement(doc.new_page(), 100, ASSETS)
    path = _save(doc, tmp_path, "three.pdf")
    reply = tables.tables(path, pages=[1, 2, 3], max_chars=100)
    assert reply["count"] == 1 and reply["more_available"]


def test_continuation_is_flagged_and_says_when_it_could_not_look(tmp_path):
    doc = fitz.open()
    _statement(doc.new_page(), 40, ASSETS)
    _statement(doc.new_page(), 40, ASSETS)
    path = _save(doc, tmp_path, "continued.pdf")
    both = tables.tables(path, pages=[1, 2])["tables"]
    assert both[1]["continues_from"] == 1 and both[1]["continuation_checked"]
    alone = tables.tables(path, pages=[2])["tables"][0]
    assert alone["continues_from"] is None and not alone["continuation_checked"]


def test_a_stranded_title_starts_a_new_table_rather_than_continuing_one(tmp_path):
    """A product manual ends a page with a caption and starts its table on the next; the two tables
    share every column position, and the stranded caption is what says they differ."""
    doc = fitz.open()
    first = doc.new_page()
    _statement(first, 40, ASSETS)
    first.insert_text((60, 40 + len(ASSETS) * PITCH + 30), "Headphone cable connected", fontsize=FONT, fontname="hebo")
    _statement(doc.new_page(), 40, ASSETS)
    path = _save(doc, tmp_path, "stranded.pdf")
    second = tables.tables(path, pages=[1, 2])["tables"][1]
    assert second["title"] == "Headphone cable connected"
    assert second["title_from_previous_page"] is True
    assert second["continues_from"] is None


def _on_page_three(path: str, pages: list[int]) -> dict:
    return next(t for t in tables.tables(path, pages=pages)["tables"] if t["page"] == 3)


def test_a_stranded_title_reaches_the_next_page_only_and_never_across_a_skipped_one(tmp_path):
    """The previous page *asked for* stood in for the page before, so a request that skipped a page
    gave its table a caption stranded two pages up (#366)."""
    doc = fitz.open()
    first = doc.new_page()
    _statement(first, 40, ASSETS)
    first.insert_text((60, 40 + len(ASSETS) * PITCH + 30), "Headphone cable connected", fontsize=FONT, fontname="hebo")
    doc.new_page().insert_text((72, 100), "A page with no table on it.", fontsize=11)
    _statement(doc.new_page(), 40, ASSETS)
    path = _save(doc, tmp_path, "gap.pdf")
    for pages in ([1, 2, 3], [1, 3]):
        third = _on_page_three(path, pages)
        assert third["title"] is None, pages
        assert third["title_from_previous_page"] is False, pages


def test_continuation_is_checked_against_the_page_before_and_only_that_page(tmp_path):
    """``continuation_checked`` says whether the page before was read: true when it was, even with no
    table on it, and false when the request skipped it — where it had said the opposite of both, and a
    table could be flagged as continuing one two pages up (#366)."""
    doc = fitz.open()
    _statement(doc.new_page(), 40, ASSETS)
    doc.new_page().insert_text((72, 100), "A page with no table on it.", fontsize=11)
    _statement(doc.new_page(), 40, ASSETS)
    path = _save(doc, tmp_path, "gap.pdf")
    read_through = _on_page_three(path, [1, 2, 3])
    assert read_through["continuation_checked"] is True
    assert read_through["continues_from"] is None
    skipped = _on_page_three(path, [1, 3])
    assert skipped["continuation_checked"] is False
    assert skipped["continues_from"] is None


def test_extract_text_names_the_pages_worth_calling_get_tables_on(statement_pdf, tmp_path):
    doc = fitz.open()
    doc.insert_pdf(fitz.open(statement_pdf))
    doc.new_page().insert_text((72, 100), "No ruling here.", fontsize=11)
    banded = doc.new_page()
    _statement(banded, 100, ASSETS, bands=True)
    path = _save(doc, tmp_path, "hint.pdf")
    assert queries.extract_text(path, [1, 2, 3])["table_pages"] == [1, 3]


def test_concurrent_calls_read_the_same_as_sequential_ones(statement_pdf, grid_pdf):
    """PyMuPDF's finder keeps the page's characters in module-level state; the lock keeps two
    calls in two worker threads from reading each other's."""
    expected = {path: tables.tables(path, pages=[1])["tables"] for path in (statement_pdf, grid_pdf)}
    failures = []

    def run(path):
        for _ in range(3):
            if tables.tables(path, pages=[1])["tables"] != expected[path]:
                failures.append(path)

    threads = [threading.Thread(target=run, args=(p,)) for p in (statement_pdf, grid_pdf) * 2]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not failures
