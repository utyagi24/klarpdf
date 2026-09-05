"""M107 — a redaction that lands *inside* a longer word says so.

The last member of the family this series has spent its length closing: a destructive tool that
damages content and reports unqualified success. `redact_text {"query": "Male"}` also removes the
`male` inside `Female`, leaving `Fe` in a driver table, and the reply said `matches: 3`,
`residual_matches: 0`, `residual_literal: 0`, cross-engine verified, with nothing to suggest a word
had been damaged (OPEN-ITEMS 2026-08-19, filed three times across TC-003b/TC-003/TC-007).

**Why no existing check could catch it.** Every residual field is scoped to *the query*, and the
query was removed exactly as asked — the harm is to a word the caller never mentioned, so nothing
that measures the query can see it. The over-redaction guard could not either: `_term_report`
returns on its first line when `len(terms) < 2`, so a one-word query never reached it. The two
guards cover the two different ways this tool destroys more than intended, and neither could see
the other's case.

The false-positive tests matter as much as the positive ones. A warning that fires on ordinary
whole-word redactions would be noise on every call, and this module's warnings are read.
"""

from __future__ import annotations

import os

import pymupdf
import pytest

from klarpdf.mcp_bridge import redaction


@pytest.fixture
def page_with(tmp_path):
    def build(*lines: str) -> str:
        path = str(tmp_path / "doc.pdf")
        doc = pymupdf.open()
        page = doc.new_page()
        y = 100
        for line in lines:
            page.insert_text((72, y), line)
            y += 30
        doc.save(path)
        doc.close()
        return path

    return build


@pytest.fixture
def out(tmp_path):
    return str(tmp_path / "out.pdf")


def redact(path, out, **kwargs):
    return redaction.redact_text(path, kwargs.pop("query", None), out, **kwargs)


def partial_warning(result) -> str | None:
    return next((w for w in result.get("warnings", []) if "inside a longer word" in w), None)


# ---- it fires, and says what was damaged -------------------------------------


def test_the_original_case_is_disclosed(page_with, out):
    """`Male` inside `Female` — the driver table that came back reading `Fe`."""
    path = page_with("UMESH Male Married", "SEEMA Female Married")
    result = redact(path, out, query="Male")

    assert result["partial_word_matches"] == [
        {"term": "Male", "inside": "Female", "leaves": "Fe", "pages": [1], "count": 1}
    ]
    warning = partial_warning(result)
    assert warning and "'Male' inside 'Female'" in warning and "'Fe'" in warning


def test_the_warning_says_the_output_is_disposable(page_with, out):
    """The same remedy the over-redaction guard gives: the input is untouched, so the fix is to
    discard this output rather than to repair it."""
    path = page_with("SEEMA Female Married")
    warning = partial_warning(redact(page_with("SEEMA Female Married"), out, query="Male"))
    assert "whole_words" in warning and "discarded" in warning


def test_it_reports_what_the_damaged_word_now_reads(page_with, out):
    """`leaves` is computed, not described. A caller may shrug at "1 partial match" and will not
    shrug at a name that now says `Fe`."""
    path = page_with("Invoice 2019 total")
    result = redact(path, out, query="1")
    assert result["partial_word_matches"] == [
        {"term": "1", "inside": "2019", "leaves": "209", "pages": [1], "count": 1}
    ]


def test_repeat_occurrences_collapse_into_one_entry_with_a_count(page_with, out):
    path = page_with("Female one", "Female two", "Male three")
    result = redact(path, out, query="Male")
    entry = result["partial_word_matches"][0]
    assert entry["inside"] == "Female" and entry["count"] == 2


def test_the_residual_fields_still_read_clean_which_is_the_point(page_with, out):
    """The disclosure is additive: every existing signal is still correct, and still says nothing
    about this. That is why the field had to be added rather than an existing one re-interpreted."""
    path = page_with("SEEMA Female Married")
    result = redact(path, out, query="Male")
    assert result["residual_matches"] == 0
    assert result["residual_literal"] == 0
    assert result["partial_word_matches"]


# ---- it stays quiet when nothing was damaged ---------------------------------


def test_an_ordinary_whole_word_redaction_is_silent(page_with, out):
    path = page_with("SEEMA Smith Married", "UMESH Smith Single")
    result = redact(path, out, query="Smith")
    assert "partial_word_matches" not in result
    assert partial_warning(result) is None


def test_whole_words_true_cannot_produce_a_partial_match(page_with, out):
    """With the flag on, a match inside a longer word is filtered before it is redacted — so there
    is nothing to warn about, and warning anyway would be false."""
    path = page_with("SEEMA Female Married", "UMESH Male Single")
    result = redact(path, out, query="Male", whole_words=True)
    assert "partial_word_matches" not in result
    assert partial_warning(result) is None


def test_a_phrase_spanning_two_words_is_not_called_a_partial_match(page_with, out):
    """A hit covering two page words is a phrase sitting between them, not a fragment eaten out of
    one. Reporting it as "inside" a word would be a lie."""
    path = page_with("use the regular expression here")
    result = redact(path, out, query="regular expression", whole_words=True)
    assert "partial_word_matches" not in result


def test_a_whole_word_next_to_punctuation_is_not_a_partial_match(page_with, out):
    """`expression.` is one page word including the period, and the hit covers only the letters —
    the exact shape that made whole-word search drop matches in M64/TC-001. It must not now be
    reported as damage either."""
    path = page_with("use the expression.")
    result = redact(path, out, query="expression")
    assert "partial_word_matches" not in result
    assert partial_warning(result) is None


def test_each_query_reports_its_own_partial_matches(page_with, out):
    """Per query, like every other field under `queries` — flattening would attribute one query's
    damage to another."""
    path = page_with("SEEMA Female Married", "UMESH Smith Single")
    result = redaction.redact_text(path, None, out, queries=["Male", "Smith"])
    male, smith = result["queries"]
    assert male["partial_word_matches"][0]["inside"] == "Female"
    assert "partial_word_matches" not in smith


def test_the_source_is_untouched_as_the_warning_claims(page_with, out):
    path = page_with("SEEMA Female Married")
    before = open(path, "rb").read()
    redact(path, out, query="Male")
    assert open(path, "rb").read() == before
    assert os.path.exists(out)
