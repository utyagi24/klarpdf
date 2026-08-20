"""M108 — the residual fields count occurrences, and `export_images` caps its listing (TC-011).

Found at scale: on a 320-page document `redact_text` reported `residual_literal: 2` where **12**
residual occurrences remained, because the field counted distinct *spellings* and then called them
"place(s)". It is the field the tool's own docs single out as "the check that catches the matcher
being wrong, so it is the one worth reading", and small documents hid it — with one occurrence per
spelling, spellings and places coincide.

**The reported fix was to adopt `residual_normalized`'s shape. That shape had the same bug**: its
`count` was `len(pages)`, so three occurrences on one page reported as `1`. Both are fixed here, or
the fix would have propagated a second undercount into the field it was copied from.

**And the obvious repair is wrong in the other direction.** `residual_literal` reads text extracted
by *both* PyMuPDF and Poppler, so counting every occurrence across `extracted` double-reports: 12
where 6 remain. Occurrences are maxed per page across engines, never summed — which also keeps a
spelling only one extractor can see.
"""

from __future__ import annotations

import os

import pymupdf
import pytest

from mcp_bridge import redaction
from mcp_bridge.config import Config
from mcp_bridge.server import create_server


@pytest.fixture
def out(tmp_path):
    return str(tmp_path / "out.pdf")


@pytest.fixture
def pages_of(tmp_path):
    def build(*pages: tuple[str, ...]) -> str:
        path = str(tmp_path / "doc.pdf")
        doc = pymupdf.open()
        for lines in pages:
            page = doc.new_page()
            y = 100
            for line in lines:
                page.insert_text((72, y), line)
                y += 30
        doc.save(path)
        doc.close()
        return path

    return build


def occurrences_in(path: str, needle: str) -> int:
    doc = pymupdf.open(path)
    try:
        return sum(page.get_text().count(needle) for page in doc)
    finally:
        doc.close()


# ---- residual_literal --------------------------------------------------------


def test_residual_literal_counts_occurrences_not_spellings(pages_of, out):
    """The TC-011 case, shrunk. Two spellings, three occurrences each: the answer is 6, not 2."""
    path = pages_of(
        ("Starship flies", "the Starship's engines", "a Starship-enabled mission"),
        ("Starship again", "the Starship's crew", "a Starship-enabled flight"),
        ("Starship thrice", "the Starship's tanks", "a Starship-enabled test"),
    )
    result = redaction.redact_text(path, "Starship", out, whole_words=True)

    assert result["residual_literal"] == 6
    assert occurrences_in(out, "Starship's") + occurrences_in(out, "Starship-enabled") == 6


def test_residual_literal_names_each_spelling_with_its_own_count_and_pages(pages_of, out):
    path = pages_of(
        ("Starship flies", "the Starship's engines"),
        ("Starship again", "the Starship's crew"),
        ("Starship thrice", "a Starship-enabled test"),
    )
    result = redaction.redact_text(path, "Starship", out, whole_words=True)
    forms = {entry["as_written"]: entry for entry in result["residual_literal_forms"]}

    assert forms["Starship's"]["count"] == 2
    assert forms["Starship's"]["pages"] == [1, 2]
    assert forms["Starship-enabled"]["count"] == 1
    assert forms["Starship-enabled"]["pages"] == [3]


def test_the_warning_reports_times_and_spellings_as_different_numbers(pages_of, out):
    """The old wording said "in 2 place(s)" for two spellings. Both numbers are now stated, and
    labelled as what they are."""
    path = pages_of(("Starship one", "the Starship's a", "the Starship's b"))
    result = redaction.redact_text(path, "Starship", out, whole_words=True)
    warning = next(w for w in result["warnings"] if "literally" in w)

    assert "2 time(s)" in warning
    assert "1 spelling(s)" in warning
    assert "(2x)" in warning


def test_two_extraction_engines_do_not_double_count(pages_of, out):
    """The literal scan reads PyMuPDF *and* Poppler when Poppler is installed, so the same page is
    extracted twice. Summing across `extracted` would report 4 where 2 remain — the same defect
    this milestone fixes, pointing the other way."""
    path = pages_of(("Starship one", "the Starship's a", "the Starship's b"))
    result = redaction.redact_text(path, "Starship", out, whole_words=True)

    assert result["residual_literal"] == occurrences_in(out, "Starship's") == 2


def test_a_clean_redaction_still_reports_zero_and_no_forms(pages_of, out):
    path = pages_of(("Starship one", "Starship two"))
    result = redaction.redact_text(path, "Starship", out, whole_words=True)

    assert result["residual_literal"] == 0
    assert "residual_literal_forms" not in result


# ---- residual_normalized -----------------------------------------------------


def test_residual_normalized_counts_occurrences_not_pages(pages_of, out):
    """Its `count` was `len(pages)`, so three variants on one page reported as `1` — the shape the
    TC-011 report proposed copying."""
    path = pages_of(
        (
            "ref 607347469 2031 here",
            "also 607347469 203 1 one",
            "also 607347469 203 1 two",
            "also 607347469 203 1 three",
        )
    )
    result = redaction.redact_text(path, "607347469 2031", out, whole_words=True)
    variant = result["residual_normalized"][0]

    assert variant["as_written"] == "607347469 203 1"
    assert variant["pages"] == [1]
    assert variant["count"] == 3
    assert occurrences_in(out, "607347469 203 1") == 3


def test_a_scan_that_ran_and_found_nothing_is_still_an_empty_list(pages_of, out):
    """The `[]` vs `null` contract (M103) is untouched by the counting change."""
    path = pages_of(("ref 607347469 2031 here", "nothing else"))
    result = redaction.redact_text(path, "607347469 2031", out, whole_words=True)
    assert result["residual_normalized"] == []


# ---- export_images listing cap (M108.1) --------------------------------------


def _blank(tmp_path, pages: int) -> str:
    path = str(tmp_path / "many.pdf")
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(path)
    doc.close()
    return path


def _export(tmp_path, pages: int, **extra):
    import asyncio
    import json

    source = _blank(tmp_path, pages)
    out_dir = tmp_path / "img"
    out_dir.mkdir()
    server = create_server(Config())
    reply = asyncio.run(
        server.call_tool(
            "export_images", {"path": source, "out_dir": str(out_dir), "dpi": 20, **extra}
        )
    )
    return json.loads(reply.content[0].text), out_dir


def test_export_images_caps_the_listing_and_says_so(tmp_path):
    """The one bulk tool that returned N paths for any N, with no `truncated` (TC-011)."""
    result, out_dir = _export(tmp_path, 40)

    assert result["count"] == 40
    assert len(result["files"]) == 25
    assert result["truncated"] is True
    assert len(list(out_dir.iterdir())) == 40, "every file is still written — only the list is cut"


def test_the_cap_note_says_where_the_files_are_and_how_they_are_named(tmp_path):
    """What the reply is actually for. A caller who wants all 320 paths can list the directory;
    what they cannot reconstruct is the naming pattern or the destination."""
    result, out_dir = _export(tmp_path, 40)

    assert str(out_dir) in result["note"]
    assert result["out_dir"] == str(out_dir)
    assert "many-01.png" in result["note"] and "many-40.png" in result["note"]


def test_a_small_export_is_listed_in_full_with_no_truncation(tmp_path):
    result, _ = _export(tmp_path, 5)

    assert len(result["files"]) == 5
    assert "truncated" not in result
    assert "note" not in result
