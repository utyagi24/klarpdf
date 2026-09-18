"""M140 — ``get_heading_candidates``: lines set to stand out from the body text, for an agent to judge.

Every fixture is built with PyMuPDF and read back through the real extraction, so each test runs the
path a caller's document does. The groups:

* **What qualifies** — larger, bold, italic at body size, a run-in opening phrase; and the two
  comparisons that keep those honest: the body is the whole document's, and a bold phrase wrapping
  from the line above is not a run-in.
* **What is not read** — undrawn text, text under a point, angled text, lines naming nothing.
* **The style table and its filter** — ids stable across page ranges, examples that are not running
  heads, an unknown id refused.
* **``tables``** — ``in_table`` agrees with ``get_tables`` because it *is* ``get_tables``' reader,
  which one test proves by breaking that reader.
* **The reply** — pagination on both caps, rotation, and the tool as a client calls it.
"""

from __future__ import annotations

import asyncio
import json

import pymupdf as fitz
import pytest

from klarpdf.mcp_bridge import headings, queries
from klarpdf.mcp_bridge.server import server

BODY_LINE = "Given the share of roads in the overall transport of goods and passenger traffic it is"


def _paragraph(page: fitz.Page, y: float, lines: int = 5, x: float = 72, **style) -> float:
    """Set ``lines`` of body text from baseline ``y``; return the baseline after the last one."""
    style.setdefault("fontname", "helv")
    style.setdefault("fontsize", 10)
    for i in range(lines):
        page.insert_text((x, y + i * 12), BODY_LINE, **style)
    return y + lines * 12


def _line(page: fitz.Page, y: float, *runs: tuple[str, str], x: float = 72, size: float = 10) -> None:
    """One line of ``(font, text)`` runs set end to end, as a single text line."""
    writer = fitz.TextWriter(page.rect)
    at = fitz.Point(x, y)
    for font, text in runs:
        _, at = writer.append(at, text, font=fitz.Font(font), fontsize=size)
    writer.write_text(page)


def _save(doc: fitz.Document, tmp_path, name: str) -> str:
    path = str(tmp_path / name)
    doc.save(path)
    doc.close()
    return path


def _texts(reply: dict) -> list[str]:
    return [c["text"] for c in reply["candidates"]]


def _style(reply: dict, style_id: str) -> dict:
    return next(s for s in reply["styles"] if s["style"] == style_id)


# ---------------------------------------------------------------------------------------------
# What qualifies
# ---------------------------------------------------------------------------------------------


def test_bold_larger_and_italic_lines_qualify_and_the_body_does_not(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 60), "Annual Review", fontname="helv", fontsize=16)
    page.insert_text((72, 90), "Key Regulations", fontname="hebo", fontsize=10)
    y = _paragraph(page, 110)
    page.insert_text((72, y + 10), "Laws on highways", fontname="heit", fontsize=10)
    _paragraph(page, y + 30)
    reply = headings.heading_candidates(_save(doc, tmp_path, "styles.pdf"))

    assert _texts(reply) == ["Annual Review", "Key Regulations", "Laws on highways"]
    assert reply["body"] == {"font": "Helvetica", "size": 10.0, "bold": False, "italic": False}
    assert all(c["run_in"] is False for c in reply["candidates"])


def test_small_bold_qualifies_but_small_italic_does_not(tmp_path):
    """Bold is emphasis at any size — a boxed heading can be set small. Italic smaller than the body
    is a source note or a caption far more often, and no measured heading was set that way."""
    doc = fitz.open()
    page = doc.new_page()
    y = _paragraph(page, 72)
    page.insert_text((72, y + 10), "Boxed heading", fontname="hebo", fontsize=8)
    page.insert_text((72, y + 30), "Source: industry estimates", fontname="heit", fontsize=8)
    _paragraph(page, y + 50)
    reply = headings.heading_candidates(_save(doc, tmp_path, "small.pdf"))

    assert _texts(reply) == ["Boxed heading"]


def test_a_number_and_its_title_side_by_side_are_one_entry(tmp_path):
    """A prospectus sets ``2.8.1`` and its title as two lines of one text block, 36 pt apart."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "2.8.1", fontname="hebo", fontsize=10)
    page.insert_text((108, 100), "Issues and challenges", fontname="hebo", fontsize=10)
    _paragraph(page, 120)
    path = _save(doc, tmp_path, "numbered.pdf")
    reply = headings.heading_candidates(path)

    assert _texts(reply) == ["2.8.1 Issues and challenges"]
    x0, _, x1, _ = reply["candidates"][0]["bbox"]
    assert x0 == pytest.approx(72, abs=1) and x1 > 200


def test_headings_level_with_each_other_in_two_columns_stay_apart(tmp_path):
    """Lines in different text blocks are never joined: on a two-column page that would weld a
    heading to whatever is level with it in the other column."""
    doc = fitz.open()
    page = doc.new_page()
    for x in (50, 320):
        page.insert_text((x, 100), "Methods" if x == 50 else "Results", fontname="hebo", fontsize=10)
        _paragraph(page, 116, x=x, lines=6, fontsize=8)
    _paragraph(page, 300, lines=20)  # the body is 10 pt, so the 8 pt columns are not candidates
    reply = headings.heading_candidates(_save(doc, tmp_path, "columns.pdf"))

    assert _texts(reply) == ["Methods", "Results"]


def test_a_run_in_heading_is_its_opening_phrase(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    y = _paragraph(page, 72)
    _line(page, y + 10, ("hebo", "Plan Information:"), ("helv", " Following the amendment of our formulas"))
    _paragraph(page, y + 22)
    path = _save(doc, tmp_path, "runin.pdf")
    reply = headings.heading_candidates(path)

    assert _texts(reply) == ["Plan Information:"]
    (entry,) = reply["candidates"]
    assert entry["run_in"] is True
    # The box is the phrase's, not the line's: it stops where the regular text begins.
    (hit,) = queries.search(path, "Following", whole_words=True)
    assert entry["bbox"][2] <= hit["boxes"][0][0]


def test_a_run_in_heading_longer_than_the_rest_of_its_line_is_still_its_opening_phrase(tmp_path):
    """Judged by the line's commonest style, this came back with ``amends laws`` attached."""
    doc = fitz.open()
    page = doc.new_page()
    y = _paragraph(page, 72)
    _line(page, y + 10, ("hebo", "Occupational Safety, Health and Working Conditions Code, 2020,"),
          ("helv", " amends laws"))
    _paragraph(page, y + 22)
    reply = headings.heading_candidates(_save(doc, tmp_path, "longlead.pdf"))

    assert _texts(reply) == ["Occupational Safety, Health and Working Conditions Code, 2020,"]
    assert reply["candidates"][0]["run_in"] is True


def test_an_opening_phrase_may_mix_emphasised_styles(tmp_path):
    """A bold number before a bold italic title is one opening phrase; with nothing ordinary after
    it, the whole line is the heading."""
    doc = fitz.open()
    page = doc.new_page()
    y = _paragraph(page, 72)
    _line(page, y + 10, ("hebo", "2.10.1.1"), ("hebi", " Key policy measures"), ("helv", " were announced"))
    _line(page, y + 40, ("hebo", "2.10.1.2"), ("hebi", " Impact"))
    _paragraph(page, y + 60)
    reply = headings.heading_candidates(_save(doc, tmp_path, "mixed.pdf"))

    assert [(c["text"], c["run_in"]) for c in reply["candidates"]] == [
        ("2.10.1.1 Key policy measures", True),
        ("2.10.1.2 Impact", False),
    ]


def test_a_heading_under_a_heading_keeps_its_opening_phrase(tmp_path):
    """The line above ends in the same bold, but it is a heading, not a paragraph line with a bold
    tail — so this line's bold opening is a heading of its own, not a phrase wrapping onto it."""
    doc = fitz.open()
    page = doc.new_page()
    y = _paragraph(page, 72)
    _line(page, y + 10, ("hebo", "SECTION IV - ABOUT OUR COMPANY"))
    _line(page, y + 22, ("hebo", "INDUSTRY OVERVIEW"), ("helv", " (continued)"))
    _paragraph(page, y + 34)
    reply = headings.heading_candidates(_save(doc, tmp_path, "stacked.pdf"))

    assert _texts(reply) == ["SECTION IV - ABOUT OUR COMPANY", "INDUSTRY OVERVIEW"]


def test_a_bold_phrase_wrapping_onto_a_line_is_not_a_run_in(tmp_path):
    """The line before ends in the same bold, so this line's bold opening is the rest of a phrase.
    The control below differs only in how that line ends, and does produce the candidate — so the
    test is about the comparison, not about bold openings in general."""

    def build(name: str, first_line_ends_bold: bool) -> str:
        doc = fitz.open()
        page = doc.new_page()
        y = _paragraph(page, 72)
        ending = ("hebo", " Highways Authority Act, 19") if first_line_ends_bold else ("helv", " and its rules")
        _line(page, y, ("helv", "The regulatory framework stems from the"), ending)
        _line(page, y + 12, ("hebo", "88 (the NHAI Act)"), ("helv", " as amended or supplemented."))
        _paragraph(page, y + 24)
        return _save(doc, tmp_path, name)

    wrapped = headings.heading_candidates(build("wrapped.pdf", True))
    assert _texts(wrapped) == []
    fresh = headings.heading_candidates(build("fresh.pdf", False))
    assert _texts(fresh) == ["88 (the NHAI Act)"]


def test_a_space_set_in_another_style_still_separates_the_words(tmp_path):
    """A prospectus sets the space in ``Naked Short Selling`` as a span of its own, in italic."""
    doc = fitz.open()
    page = doc.new_page()
    _line(page, 60, ("hebo", "Naked Short"), ("heit", " "), ("hebo", "Selling"))
    _paragraph(page, 80)
    reply = headings.heading_candidates(_save(doc, tmp_path, "space.pdf"))

    assert _texts(reply) == ["Naked Short Selling"]


def test_the_body_is_the_whole_documents_not_the_pages_asked_for(tmp_path):
    """Page 2 is mostly 8 pt table text. Measured on page 2 alone, its 10 pt paragraph would count
    as larger than the body; measured on the document, it is the body."""
    doc = fitz.open()
    first = doc.new_page()
    _paragraph(first, 72, lines=40)
    second = doc.new_page()
    _paragraph(second, 72, lines=2)
    _paragraph(second, 120, lines=40, fontsize=8)
    reply = headings.heading_candidates(_save(doc, tmp_path, "body.pdf"), [2])

    assert reply["body"]["size"] == 10.0
    assert reply["candidates"] == []


def test_a_heavy_weight_named_in_the_font_counts_as_bold():
    """MuPDF does not flag ``Arial-Black``; its name says what a reader sees. The weight is read
    from the style part of the name only, so a family called *Blackwood* is not heavy."""

    def span(font: str) -> dict:
        return {"font": font, "flags": 0, "char_flags": 16, "size": 11.0}

    assert headings._style_of(span("Arial-Black")).bold is True
    assert headings._style_of(span("SourceSansPro-Semibold")).bold is True
    assert headings._style_of(span("TimesNewRoman,Bold")).bold is True
    assert headings._style_of(span("Blackwood-Regular")).bold is False
    assert headings._style_of(span("ArialMT")).bold is False
    assert headings._style_of({**span("ArialMT"), "flags": fitz.TEXT_FONT_BOLD}).bold is True


# ---------------------------------------------------------------------------------------------
# What is not read
# ---------------------------------------------------------------------------------------------


def test_text_a_reader_cannot_see_is_not_read(tmp_path):
    """A hidden copy of the page's text would double every line and, set at other sizes, make
    ordinary lines look like candidates — on a real prospectus it added 31 bogus styles. Counted, the
    hidden 9 pt text here would also become the body, and every visible line would stand out."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 60), "Visible heading", fontname="hebo", fontsize=10)
    page.insert_text((72, 60), "Visible heading", fontname="helv", fontsize=14, render_mode=3)
    _paragraph(page, 80)
    for i in range(30):  # enough hidden text to be the body, if it counted
        page.insert_text((72, 200 + i * 12), BODY_LINE, fontname="tiro", fontsize=9, render_mode=3)
    reply = headings.heading_candidates(_save(doc, tmp_path, "hidden.pdf"))

    assert _texts(reply) == ["Visible heading"]
    assert reply["body"]["font"] == "Helvetica"
    assert reply["pages_without_visible_text"] == []


def test_a_page_with_nothing_drawn_is_named(tmp_path):
    doc = fitz.open()
    _paragraph(doc.new_page(), 72)
    ocr = doc.new_page()  # a scan's hidden OCR layer: text, but nothing painted
    ocr.insert_text((72, 60), "SCANNED TITLE", fontname="helv", fontsize=20, render_mode=3)
    doc.new_page()        # blank
    reply = headings.heading_candidates(_save(doc, tmp_path, "scan.pdf"))

    assert reply["pages_without_visible_text"] == [2, 3]
    assert reply["candidates"] == []


def test_a_document_with_nothing_drawn_has_no_body(tmp_path):
    doc = fitz.open()
    doc.new_page().insert_text((72, 60), "hidden", fontname="helv", fontsize=20, render_mode=3)
    reply = headings.heading_candidates(_save(doc, tmp_path, "allhidden.pdf"))

    assert reply["body"] is None
    assert reply["count"] == 0 and reply["styles"] == []
    assert reply["pages_without_visible_text"] == [1]


def test_tiny_text_angled_text_and_lines_naming_nothing_are_not_candidates(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 60), "Duplicate total", fontname="hebo", fontsize=0.5)
    page.insert_text((40, 400), "Margin note", fontname="hebo", fontsize=14, rotate=90)
    page.insert_text((72, 90), "• — •", fontname="helv", fontsize=18)
    page.insert_text((72, 120), "Real heading", fontname="hebo", fontsize=10)
    _paragraph(page, 140)
    reply = headings.heading_candidates(_save(doc, tmp_path, "noise.pdf"))

    assert _texts(reply) == ["Real heading"]


# ---------------------------------------------------------------------------------------------
# The style table and its filter
# ---------------------------------------------------------------------------------------------


@pytest.fixture
def report_pdf(tmp_path) -> str:
    """Three pages: a title, section headings, subsections, and a bold running head on every page."""
    doc = fitz.open()
    for n in range(1, 4):
        page = doc.new_page()
        page.insert_text((72, 40), "ACME CORPORATION", fontname="hebo", fontsize=8)
        if n == 1:
            page.insert_text((72, 80), "Annual Report", fontname="helv", fontsize=20)
        page.insert_text((72, 120), f"Section {n}", fontname="hebo", fontsize=14)
        y = _paragraph(page, 140)
        page.insert_text((72, y + 10), f"Subsection {n}.1", fontname="hebo", fontsize=10)
        _paragraph(page, y + 30)
    return _save(doc, tmp_path, "report.pdf")


def test_the_style_table_describes_each_style_once_loudest_first(report_pdf):
    reply = headings.heading_candidates(report_pdf)
    table = [(s["font"], s["size"], s["count"], s["distinct"], s["pages"]) for s in reply["styles"]]

    assert table == [
        ("Helvetica", 20.0, 1, 1, 1),
        ("Helvetica-Bold", 14.0, 3, 3, 3),
        ("Helvetica-Bold", 10.0, 3, 3, 3),
        ("Helvetica-Bold", 8.0, 3, 1, 3),
    ]
    ids = [s["style"] for s in reply["styles"]]
    assert ids == sorted(ids, key=lambda i: int(i[1:]))  # a lower id is a louder style
    by_id = {s["style"]: s for s in reply["styles"]}
    assert all(c["style"] in by_id for c in reply["candidates"])
    assert "in_table" not in reply["styles"][0]


def test_examples_prefer_texts_that_occur_once(tmp_path):
    """A style's first lines are often a running head. Here the bold style opens with the company
    name on every page, and the example should be the heading it also sets."""
    doc = fitz.open()
    for n in range(1, 4):
        page = doc.new_page()
        page.insert_text((72, 40), "ACME CORPORATION", fontname="hebo", fontsize=10)
        if n == 3:
            page.insert_text((72, 80), "Risk Factors", fontname="hebo", fontsize=10)
        _paragraph(page, 100)
    reply = headings.heading_candidates(_save(doc, tmp_path, "running.pdf"))
    (row,) = reply["styles"]

    assert row["examples"] == ["Risk Factors", "ACME CORPORATION"]
    assert (row["count"], row["distinct"]) == (4, 2)


def test_style_ids_are_the_documents_and_hold_across_page_ranges(report_pdf):
    whole = headings.heading_candidates(report_pdf)
    page2 = headings.heading_candidates(report_pdf, [2])
    ids_whole = {(s["font"], s["size"]): s["style"] for s in whole["styles"]}
    ids_page2 = {(s["font"], s["size"]): s["style"] for s in page2["styles"]}

    assert ("Helvetica", 20.0) not in ids_page2  # the title is on page 1 only
    assert all(ids_whole[key] == value for key, value in ids_page2.items())


def test_the_styles_filter_narrows_candidates_but_not_the_table(report_pdf):
    whole = headings.heading_candidates(report_pdf)
    sections = next(s["style"] for s in whole["styles"] if s["size"] == 14.0)
    reply = headings.heading_candidates(report_pdf, styles=[sections])

    assert _texts(reply) == ["Section 1", "Section 2", "Section 3"]
    assert reply["total_candidates"] == 3
    assert reply["styles"] == whole["styles"]


def test_a_real_style_with_nothing_on_these_pages_returns_nothing(report_pdf):
    whole = headings.heading_candidates(report_pdf)
    title = next(s["style"] for s in whole["styles"] if s["size"] == 20.0)
    reply = headings.heading_candidates(report_pdf, [3], styles=[title])

    assert reply["candidates"] == [] and reply["total_candidates"] == 0


def test_an_unknown_style_id_is_an_error_naming_the_real_ones(report_pdf):
    with pytest.raises(ValueError) as caught:
        headings.heading_candidates(report_pdf, [2], styles=["s999", "bold"])
    message = str(caught.value)
    assert "'s999'" in message and "'bold'" in message
    page2 = headings.heading_candidates(report_pdf, [2])
    assert all(s["style"] in message for s in page2["styles"])

    with pytest.raises(ValueError, match="style ids"):
        headings.heading_candidates(report_pdf, styles=[3])


# ---------------------------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------------------------


@pytest.fixture
def grid_report_pdf(tmp_path) -> str:
    """A bold heading above a drawn grid whose header row is bold too."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 80), "Fee Schedule", fontname="hebo", fontsize=12)
    xs = [60, 200, 340, 480]
    ys = [100 + i * 22 for i in range(5)]
    for x in xs:
        page.draw_line((x, ys[0]), (x, ys[-1]))
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y))
    cells = [
        ["Service", "Code", "Amount"],
        ["Filing", "F-01", "1,234.50"],
        ["Review", "R-02", "98.10"],
        ["Listing", "L-03", "10.00"],
    ]
    for r, row in enumerate(cells):
        for c, text in enumerate(row):
            page.insert_text((xs[c] + 4, ys[r] + 15), text, fontname="hebo" if r == 0 else "helv", fontsize=10)
    _paragraph(page, 240, lines=20)
    return _save(doc, tmp_path, "grid_report.pdf")


def test_in_table_marks_what_get_tables_returns(grid_report_pdf):
    reply = headings.heading_candidates(grid_report_pdf, [1], tables=True)
    marks = {c["text"]: c["in_table"] for c in reply["candidates"]}

    # The header cells share one text block here, so they are one entry.
    assert marks == {"Fee Schedule": False, "Service Code Amount": True}
    assert reply["tables_checked"] is True
    header = next(s for s in reply["styles"] if s["size"] == 10.0)
    heading = next(s for s in reply["styles"] if s["size"] == 12.0)
    assert (header["count"], header["in_table"]) == (1, 1)
    assert (heading["count"], heading["in_table"]) == (1, 0)


def test_in_table_comes_from_get_tables_own_reader(grid_report_pdf, monkeypatch):
    """Break the reader and the marks follow it: ``in_table`` is not a second table detector."""
    monkeypatch.setattr(headings, "read_page", lambda page: type("Read", (), {"tables": []})())
    reply = headings.heading_candidates(grid_report_pdf, [1], tables=True)

    assert not any(c["in_table"] for c in reply["candidates"])


def test_without_tables_nothing_is_marked(grid_report_pdf):
    reply = headings.heading_candidates(grid_report_pdf)

    assert reply["tables_checked"] is False
    assert all("in_table" not in c for c in reply["candidates"])
    assert all("in_table" not in s for s in reply["styles"])


@pytest.mark.parametrize("pages", [None, []])
def test_tables_needs_pages(grid_report_pdf, pages):
    with pytest.raises(ValueError, match="explicit page range"):
        headings.heading_candidates(grid_report_pdf, pages, tables=True)


# ---------------------------------------------------------------------------------------------
# The reply
# ---------------------------------------------------------------------------------------------


def test_the_reply_pages_on_a_count_cap_and_covers_everything_once(report_pdf):
    whole = headings.heading_candidates(report_pdf)
    seen: list[dict] = []
    offset = 0
    while True:
        reply = headings.heading_candidates(report_pdf, max_candidates=4, offset=offset)
        seen.extend(reply["candidates"])
        assert reply["count"] <= 4
        if not reply["more_available"]:
            assert "warnings" not in reply
            break
        assert f"offset: {offset + reply['count']}" in reply["warnings"][0]
        offset += reply["count"]

    assert seen == whole["candidates"] and len(seen) == whole["total_candidates"] == 10


def test_the_style_table_is_charged_against_the_character_cap(report_pdf):
    table = len(json.dumps(headings.heading_candidates(report_pdf)["styles"]))
    one = headings.heading_candidates(report_pdf, max_chars=table + 200)
    none_left = headings.heading_candidates(report_pdf, max_chars=table)

    assert one["count"] >= 1 and one["more_available"]
    # Always at least one candidate, or `more_available` would page forever.
    assert none_left["count"] == 1 and none_left["more_available"]


def test_a_negative_offset_is_an_error(report_pdf):
    with pytest.raises(ValueError, match="offset"):
        headings.heading_candidates(report_pdf, offset=-1)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_a_rotated_page_is_read_as_displayed_and_reports_unrotated_boxes(tmp_path, rotation):
    """A landscape exhibit: content drawn upright, placed on a page turned the other way, and
    ``/Rotate`` set so a viewer sees it upright (the construction ``test_mcp_tables`` uses). Lines are
    judged as displayed, and boxes come back unrotated, like ``search``'s, so they feed
    ``render_page``'s ``clip`` untouched."""
    src = fitz.open()
    upright = src.new_page(width=612, height=500)
    upright.insert_text((72, 60), "Rotated heading", fontname="hebo", fontsize=10)
    _paragraph(upright, 80)
    doc = fitz.open()
    width, height = (500, 612) if rotation in (90, 270) else (612, 500)
    page = doc.new_page(width=width, height=height)
    page.show_pdf_page(page.rect, src, 0, rotate=rotation)
    page.set_rotation(rotation)
    path = _save(doc, tmp_path, f"rot{rotation}.pdf")
    reply = headings.heading_candidates(path)
    (hit,) = queries.search(path, "Rotated heading", whole_words=True)

    assert _texts(reply) == ["Rotated heading"]
    assert reply["candidates"][0]["bbox"] == pytest.approx(hit["boxes"][0], abs=1.5)


def test_text_turned_sideways_as_displayed_is_not_read(tmp_path):
    """The opposite construction: upright content under ``/Rotate 90`` is shown running down the
    page, and a line a reader sees sideways is a label, not a heading."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 60), "Sideways heading", fontname="hebo", fontsize=10)
    _paragraph(page, 80)
    page.set_rotation(90)
    reply = headings.heading_candidates(_save(doc, tmp_path, "sideways.pdf"))

    assert reply["candidates"] == []


def test_the_tool_through_the_server(report_pdf):
    result = asyncio.run(server.call_tool("get_heading_candidates", {"path": report_pdf, "pages": [1]}))
    assert len(result.content) == 1
    reply = json.loads(result.content[0].text)

    assert _texts(reply)[:2] == ["ACME CORPORATION", "Annual Report"]
    assert reply["pages_scanned"] == [1]
