"""The whole-word boundary and the case filter, at the level they are actually decided.

Headless: no Qt, no window, just :class:`model.page_text.PageText` over a page. Both filters live
there and both callers — the find bar (``viewer.search``) and the MCP bridge's ``search`` /
``redact_text`` — compose them the same way, so testing the model tests both.

Two defects are pinned here, found when `redact_text` was asked for a phrase and left it in the
file (TC-001):

* **Trailing punctuation was read as more word.** MuPDF splits ``get_text("words")`` on whitespace,
  so ``expression.`` is one word whose box includes the period. A hit covering just the letters
  stops short of that box, and the old purely-geometric test concluded the match sat *inside* a
  longer word — so every whole-word match at the end of a sentence was dropped silently.
* **A case-sensitive phrase lost anything that wrapped.** MuPDF returns a phrase spanning a line
  break as one box per line fragment, and the filter compared each fragment against the whole term,
  which never matches.

The costume both wore is the reason the fixtures are explicit about *where* a word sits: the bug is
invisible mid-line and only shows at a sentence end, before a delimiter, or across a wrap.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from model.page_text import PageText

# One line per boundary a whole-word match has to survive. The last is the control: an ordinary
# space, which the geometric test always handled, so a failure there means something else broke.
BOUNDARIES = [
    "the given expression.",
    "the given expression, and more",
    "the given expression; and more",
    "call the given expression)",
    'he said "expression" aloud',
    "the given expression: and more",
    "is it an expression?",
    "what an expression!",
    "the given expression at the end",
]


def _hits(page, query: str, *, match_case: bool = False) -> list[str]:
    """The text under every hit for ``query`` that survives the whole-word (and optionally case)
    filter — composed exactly as ``viewer.search`` and ``mcp_bridge.queries.search`` compose it."""
    text = PageText(page)
    out = []
    for rect in page.search_for(query):
        box = (rect.x0, rect.y0, rect.x1, rect.y1)
        if not text.is_whole_word(box):
            continue
        if match_case and not text.matches_case(box, query):
            continue
        out.append(text.text_under(box))
    return out


@pytest.fixture
def boundaries_pdf(tmp_path) -> str:
    path = str(tmp_path / "boundaries.pdf")
    doc = fitz.open()
    page = doc.new_page()
    for i, line in enumerate(BOUNDARIES):
        page.insert_text((60, 100 + i * 20), line, fontsize=11)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def wrapped_pdf(tmp_path) -> str:
    """A phrase broken by a line break, the way a justified paragraph breaks one.

    Both halves end in the trap: ``regular`` ends a line, ``expression`` is followed by a period.
    """
    path = str(tmp_path / "wrapped.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 100), "Splits this string around matches of the given regular", fontsize=11)
    page.insert_text((60, 120), "expression. Trailing words follow.", fontsize=11)
    doc.save(path)
    doc.close()
    return path


# ---- the boundary itself --------------------------------------------------------


def test_a_whole_word_survives_every_delimiter(boundaries_pdf):
    """The regression TC-001 found: `expression.` matched 2 of 5 times, never at a sentence end."""
    with fitz.open(boundaries_pdf) as doc:
        assert _hits(doc[0], "expression") == ["expression"] * len(BOUNDARIES)


def test_a_substring_is_still_rejected(tmp_path):
    """The whole point of the toggle — widening the boundary must not widen the match."""
    path = str(tmp_path / "decoy.pdf")
    doc = fitz.open()
    doc.new_page().insert_text((60, 100), "the expressionless face said Smithsonian", fontsize=11)
    doc.save(path)
    doc.close()
    with fitz.open(path) as opened:
        assert _hits(opened[0], "expression") == []      # inside "expressionless"
        assert _hits(opened[0], "Smith") == []           # inside "Smithsonian"
        assert _hits(opened[0], "face") == ["face"]      # a real whole word on the same line


def test_a_neighbouring_word_is_not_mistaken_for_an_overhang(boundaries_pdf):
    """Scoping the character lookup to the struck word is what makes this hold.

    ``given`` sits immediately left of ``expression``; a lookup that took "the nearest character to
    the left" without regard for word breaks would find its ``n`` and reject the match.
    """
    with fitz.open(boundaries_pdf) as doc:
        assert _hits(doc[0], "given") == ["given"] * 6


def test_a_hyphenated_compound_is_still_one_word(tmp_path):
    """The line the widened boundary must *not* cross (M64, and the find bar's own fixture).

    A regex ``\\b`` would match ``ALPHA`` here, because it looks only at the adjacent ``-``. The
    rule is the whole overhang instead: ``-zero-A0`` carries letters, so the hit is inside a longer
    word. Punctuation *around* a word is ignorable; punctuation *joining* two is not.
    """
    path = str(tmp_path / "compound.pdf")
    doc = fitz.open()
    doc.new_page().insert_text((60, 100), "ALPHA-zero-A0 and ALPHA alone.", fontsize=11)
    doc.save(path)
    doc.close()
    with fitz.open(path) as opened:
        assert _hits(opened[0], "ALPHA") == ["ALPHA"]     # the standalone one only
        assert _hits(opened[0], "zero") == []             # joined on both sides


def test_words_only_page_text_stays_geometric():
    """``viewer.search.is_whole_word`` has no page, so it has no characters to consult. It must
    fall back to the old geometric answer rather than assume a boundary it cannot see."""
    words = [(100.0, 10.0, 180.0, 22.0, "Smithsonian", 0, 0, 0)]
    text = PageText(words=words)
    assert text.is_whole_word((100.0, 10.0, 130.0, 22.0)) is False   # "Smith" inside it
    assert text.is_whole_word((100.0, 10.0, 180.0, 22.0)) is True    # the whole word


# ---- phrases that wrap ----------------------------------------------------------


def test_a_wrapped_phrase_yields_a_box_per_line_fragment(wrapped_pdf):
    """MuPDF already splits the hit per line; the filter must keep *both* halves.

    Keeping only the first is what left a legible `expression.` under the black box in TC-001.
    """
    with fitz.open(wrapped_pdf) as doc:
        assert _hits(doc[0], "regular expression") == ["regular", "expression"]


def test_a_wrapped_phrase_survives_the_case_filter(tmp_path):
    """Each fragment is only ever part of the term, so the case test compares containment."""
    path = str(tmp_path / "wrapped_case.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 100), "matches of the given Regular", fontsize=11)
    page.insert_text((60, 120), "Expression. Trailing words follow.", fontsize=11)
    doc.save(path)
    doc.close()
    with fitz.open(path) as opened:
        assert _hits(opened[0], "Regular Expression", match_case=True) == ["Regular", "Expression"]
        # …and the filter still filters: the document does not carry a lowercase phrase.
        assert _hits(opened[0], "regular expression", match_case=True) == []


def test_the_case_filter_still_rejects_a_wrong_case_word(boundaries_pdf):
    with fitz.open(boundaries_pdf) as doc:
        assert _hits(doc[0], "EXPRESSION", match_case=True) == []
        assert len(_hits(doc[0], "expression", match_case=True)) == len(BOUNDARIES)


# ---- fragments regrouped into occurrences ---------------------------------------


def test_group_matches_folds_a_wrapped_phrase_into_one_occurrence(wrapped_pdf):
    """Seven boxes for five occurrences is what the raw matcher gives; a caller counting matches
    wants five. Grouping reads the page to decide, closing an occurrence when the accumulated
    text spells the term."""
    with fitz.open(wrapped_pdf) as doc:
        page = doc[0]
        text = PageText(page)
        boxes = [(r.x0, r.y0, r.x1, r.y1) for r in page.search_for("regular expression")]
        groups = text.group_matches(boxes, "regular expression")
        assert len(boxes) == 2          # the raw matcher split it across the line break
        assert len(groups) == 1         # …and it is one occurrence
        assert len(groups[0]) == 2      # carrying both rectangles


def test_group_matches_leaves_an_unwrapped_hit_alone(boundaries_pdf):
    with fitz.open(boundaries_pdf) as doc:
        page = doc[0]
        text = PageText(page)
        boxes = [(r.x0, r.y0, r.x1, r.y1) for r in page.search_for("expression")]
        groups = text.group_matches(boxes, "expression")
        assert [len(g) for g in groups] == [1] * len(BOUNDARIES)


def test_group_matches_never_drops_a_box(wrapped_pdf):
    """The safety property. A caller may be about to redact these, and an ungrouped box costs a
    miscount while a missing one costs a leak — so a run that does not spell the term is emitted
    box by box rather than discarded."""
    with fitz.open(wrapped_pdf) as doc:
        page = doc[0]
        text = PageText(page)
        boxes = [(r.x0, r.y0, r.x1, r.y1) for r in page.search_for("regular expression")]
        # A term the boxes cannot possibly spell: every box must still come back, singly.
        groups = text.group_matches(boxes, "something else entirely")
        assert [b for g in groups for b in g] == boxes


# ---- M97 / TC-005: a box covering two lines must not weld them together ----------------------


@pytest.fixture
def stacked_pdf(tmp_path) -> str:
    """A mailing block — three stacked lines, the shape region redaction exists for."""
    path = str(tmp_path / "stacked.pdf")
    doc = fitz.open()
    page = doc.new_page()
    for row, line in enumerate(["UMESH TYAGI", "1703 PORCELLANO WAY", "DUBLIN, CA 94568"]):
        page.insert_text((72, 100 + row * 14), line, fontsize=11)
    doc.save(path)
    doc.close()
    return path


def test_text_under_separates_lines_instead_of_welding_them(stacked_pdf):
    """TC-005. `"".join` over every character in a tall box ran the end of one line into the start
    of the next, inventing `TYAGI1703` — a string in no document, and therefore one nothing could
    ever find. Splitting on whitespace must give back the words that are really there."""
    doc = fitz.open(stacked_pdf)
    try:
        text = PageText(doc[0])
        words = doc[0].get_text("words")
        top = min(w[1] for w in words)
        two_lines = (70, top - 1, 200, min(w[3] for w in words if w[1] > top + 5) + 1)
        under = text.text_under(two_lines)
        assert "TYAGI1703" not in under
        assert {"TYAGI", "1703"} <= set(under.split())
    finally:
        doc.close()


def test_a_single_line_box_is_unchanged_by_the_separator(boundaries_pdf):
    """The separator must not leak into the common case: a one-line box gains nothing, which is
    what keeps `matches_case` and the hit-grouping working exactly as before."""
    doc = fitz.open(boundaries_pdf)
    try:
        text = PageText(doc[0])
        for rect in doc[0].search_for("expression"):
            under = text.text_under((rect.x0, rect.y0, rect.x1, rect.y1))
            assert "\n" not in under
    finally:
        doc.close()
