"""M41 — redaction that is destructive, cross-engine verified, and honest about its limits.

The bar here is set by what already exists in the wild: `redact_mcp`, the one PDF-redaction MCP
server the 2026-08 sweep found, paints a **visual overlay and reports success** — the text is still
in the file. So these tests are not only "does it remove the text", they are "can it ever report
success over a file that still contains the secret", and the answer has to be no.

Fixtures mirror `tests/test_redaction.py` — single-token strings so a word box is exact and a leak
is unambiguous — and the Poppler cross-check is the same one CI asserts must not skip.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pymupdf as fitz
import pytest

from mcp_bridge import queries, redaction
from mcp_bridge.redaction import RedactionLeak

SECRET = "SECRETDATA"
PUBLIC = "PUBLICINFO"

needs_poppler = pytest.mark.skipif(
    shutil.which("pdftotext") is None, reason="Poppler pdftotext not installed"
)


@pytest.fixture
def secret_pdf(tmp_path) -> str:
    path = str(tmp_path / "secret.pdf")
    doc = fitz.open()
    for _ in range(2):
        page = doc.new_page()
        page.insert_text((72, 100), SECRET, fontsize=14)
        page.insert_text((72, 200), PUBLIC, fontsize=14)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def out(tmp_path) -> str:
    return str(tmp_path / "redacted.pdf")


def _text(path: str, index: int) -> str:
    with fitz.open(path) as doc:
        return doc[index].get_text("text")


def _poppler(path: str, page1: int) -> str:
    return subprocess.run(
        ["pdftotext", "-f", str(page1), "-l", str(page1), path, "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _box_of(path: str, page_index: int, token: str) -> list[float]:
    with fitz.open(path) as doc:
        word = next(w for w in doc[page_index].get_text("words") if w[4] == token)
    return [word[0], word[1], word[2], word[3]]


# ---- redact_text: the content is gone, not covered ----------------------------


def test_redact_text_physically_removes_the_secret(secret_pdf, out):
    redaction.redact_text(secret_pdf, SECRET, out)
    assert SECRET not in _text(out, 0)
    assert SECRET not in _text(out, 1)
    assert PUBLIC in _text(out, 0)  # neighbouring text untouched


def test_the_redaction_annotation_is_consumed(secret_pdf, out):
    """The cover-only trap: an output carrying a deletable annotation over the text is not
    redacted. `apply_redactions` consumes the annotation, leaving nothing to peel off."""
    redaction.redact_text(secret_pdf, SECRET, out)
    with fitz.open(out) as doc:
        assert list(doc[0].annots()) == []


@needs_poppler
def test_a_second_engine_agrees_the_text_is_gone(secret_pdf, out):
    """The claim "the text is gone" must not rest on the library that removed it."""
    redaction.redact_text(secret_pdf, SECRET, out)
    assert SECRET not in _poppler(out, 1)
    assert PUBLIC in _poppler(out, 1)


@needs_poppler
def test_the_result_reports_that_both_engines_ran(secret_pdf, out):
    result = redaction.redact_text(secret_pdf, SECRET, out)
    assert result["verified_with"] == ["pymupdf", "poppler"]
    assert result["cross_engine_verified"] is True
    assert result["verified_text"] == {"1": [SECRET], "2": [SECRET]}


def test_the_result_never_claims_a_check_it_did_not_run(secret_pdf, out, monkeypatch):
    """With Poppler absent the tool still works, and says plainly that the cross-engine check did
    not happen — the honesty principle, in the payload rather than only in the docs."""
    monkeypatch.setattr(redaction.shutil, "which", lambda _name: None)
    result = redaction.redact_text(secret_pdf, SECRET, out)
    assert result["verified_with"] == ["pymupdf"]
    assert result["cross_engine_verified"] is False
    assert "did NOT run" in result["verification_note"]


def test_redact_text_scopes_to_named_pages(secret_pdf, out):
    result = redaction.redact_text(secret_pdf, SECRET, out, pages=[1])
    assert result["pages_redacted"] == [1]
    assert SECRET not in _text(out, 0)
    assert SECRET in _text(out, 1)  # page 2 was out of scope


def test_redact_text_honours_whole_words(tmp_path, out):
    path = str(tmp_path / "smith.pdf")
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "Smith and Smithsonian", fontsize=12)
    doc.save(path)
    doc.close()

    redaction.redact_text(path, "Smith", out, whole_words=True)
    remaining = _text(out, 0)
    assert "Smithsonian" in remaining  # the longer word survives
    assert remaining.count("Smith") == 1  # only the one inside Smithsonian


def test_a_query_that_matches_nothing_fails_loudly(secret_pdf, out):
    """A redaction tool reporting success over a file it did not change is how a secret ships."""
    with pytest.raises(ValueError, match="was not found"):
        redaction.redact_text(secret_pdf, "NOT-IN-THIS-FILE", out)
    assert not os.path.exists(out)


def test_an_empty_query_is_refused(secret_pdf, out):
    with pytest.raises(ValueError, match="nothing to redact"):
        redaction.redact_text(secret_pdf, "   ", out)


# ---- redact_regions ------------------------------------------------------------


def test_redact_regions_removes_the_named_box(secret_pdf, out):
    box = _box_of(secret_pdf, 0, SECRET)
    result = redaction.redact_regions(secret_pdf, [{"page": 1, "box": box}], out)
    assert SECRET not in _text(out, 0)
    assert PUBLIC in _text(out, 0)
    assert result["verified_text"] == {"1": [SECRET]}


def test_a_search_hit_can_be_fed_straight_back_as_a_region(secret_pdf, out):
    """The coordinate spaces line up on purpose — find it with `search`, remove it with
    `redact_regions`, which is the review-then-apply workflow the tools are shaped for."""
    hit = queries.search(secret_pdf, SECRET)[0]
    # The whole hit, `boxes` and all — a wrapped match carries more than one, and taking only the
    # first is the mistake the plural key exists to prevent.
    redaction.redact_regions(secret_pdf, [{"page": hit["page"], "boxes": hit["boxes"]}], out)
    assert SECRET not in _text(out, 0)


def test_regions_across_several_pages(secret_pdf, out):
    regions = [
        {"page": 1, "box": _box_of(secret_pdf, 0, SECRET)},
        {"page": 2, "box": _box_of(secret_pdf, 1, PUBLIC)},
    ]
    result = redaction.redact_regions(secret_pdf, regions, out)
    assert result["pages_redacted"] == [1, 2]
    assert SECRET not in _text(out, 0)
    assert PUBLIC not in _text(out, 1)
    assert PUBLIC in _text(out, 0)  # page 1's PUBLIC was not in the region list


@pytest.mark.parametrize(
    "region, match",
    [
        ({"page": 1}, "needs 'page' and 'box'"),
        ({"box": [0, 0, 1, 1]}, "needs 'page' and 'box'"),
        ({"page": 1, "box": [10, 10, 5, 20]}, "empty or inverted"),
        ({"page": 1, "box": [10, 10, 10, 20]}, "empty or inverted"),
        ({"page": 99, "box": [0, 0, 10, 10]}, "out of range"),
    ],
)
def test_malformed_regions_are_rejected(secret_pdf, out, region, match):
    with pytest.raises(ValueError, match=match):
        redaction.redact_regions(secret_pdf, [region], out)
    assert not os.path.exists(out)


def test_redacting_nothing_is_refused(secret_pdf, out):
    with pytest.raises(ValueError, match="must remove something"):
        redaction.redact_regions(secret_pdf, [], out)


# ---- the part that matters most: a failed verification must not ship a file -------


def test_a_cross_engine_disagreement_deletes_the_output_and_raises(secret_pdf, out, monkeypatch):
    """The reason the second engine exists. PyMuPDF says the text is gone; Poppler still sees it.
    The caller must get an exception and **no file** — never a path to a false-secure PDF.

    The stub answers the "before" call honestly (the real text) and the "after" call with a leak,
    so the count comparison sees an occurrence that should not have survived.
    """
    calls = {"n": 0}

    def poppler(path, page1, password=None):
        calls["n"] += 1
        return f"{SECRET} {PUBLIC}"  # same text before AND after → nothing was removed

    monkeypatch.setattr(redaction, "_poppler_text", poppler)
    monkeypatch.setattr(redaction.shutil, "which", lambda _name: "/usr/bin/pdftotext")

    with pytest.raises(RedactionLeak, match="Poppler"):
        redaction.redact_text(secret_pdf, SECRET, out)
    assert not os.path.exists(out), "a file that failed verification must not be left behind"
    assert calls["n"] >= 2  # measured before and after, not just once


def test_a_pymupdf_detected_leak_also_deletes_the_output(secret_pdf, out, monkeypatch):
    """The first-engine half of the same guard — patched at the removal step so the written file
    genuinely still contains the secret, rather than faking the reader."""
    monkeypatch.setattr("model.page_edits.apply_redactions", lambda page, annotations: None)

    with pytest.raises(RedactionLeak, match="PyMuPDF"):
        redaction.redact_text(secret_pdf, SECRET, out)
    assert not os.path.exists(out)


def test_partial_removal_is_caught_where_a_presence_check_would_pass(tmp_path, out, monkeypatch):
    """Why verification counts occurrences instead of testing for presence.

    Two copies of the secret on one page, both marked for redaction. Break the removal so only one
    actually goes. A presence check sees the word still there and reports a leak by luck; a *count*
    check knows two boxes covered it, so at most zero occurrences may remain — and one did.
    """
    path = str(tmp_path / "twice.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), SECRET, fontsize=12)
    page.insert_text((72, 160), SECRET, fontsize=12)
    doc.save(path)
    doc.close()

    from model import page_edits

    real_apply = page_edits.apply_redactions

    def half(page, annotations):
        """Remove only the first rect of each redaction — a plausible partial-failure mode."""
        trimmed = tuple(
            page_edits.Redaction(a.rects[:1], a.fill)
            if isinstance(a, page_edits.Redaction)
            else a
            for a in annotations
        )
        real_apply(page, trimmed)

    monkeypatch.setattr("model.page_edits.apply_redactions", half)
    with pytest.raises(RedactionLeak, match="still appears"):
        redaction.redact_text(path, SECRET, out)
    assert not os.path.exists(out)


def test_a_legitimate_survivor_is_not_mistaken_for_a_leak(tmp_path, out):
    """The other side of the count rule: redacting page 1's copy while page 2 keeps its own is
    correct, and must not trip the guard."""
    path = str(tmp_path / "two_pages.pdf")
    doc = fitz.open()
    for _ in range(2):
        doc.new_page().insert_text((72, 100), SECRET, fontsize=12)
    doc.save(path)
    doc.close()

    result = redaction.redact_text(path, SECRET, out, pages=[1])
    assert result["pages_redacted"] == [1]
    assert SECRET not in _text(out, 0)
    assert SECRET in _text(out, 1)


def test_the_leak_guard_is_not_vacuous(secret_pdf, out):
    """A control: with nothing patched, the same call succeeds — so the two tests above are
    detecting an injected fault, not an always-failing path."""
    result = redaction.redact_text(secret_pdf, SECRET, out)
    assert os.path.exists(result["out"])


# ---- the source is never touched, same as every other write tool -------------------


def test_redaction_leaves_the_source_byte_identical(secret_pdf, out):
    before = open(secret_pdf, "rb").read()
    redaction.redact_text(secret_pdf, SECRET, out)
    assert open(secret_pdf, "rb").read() == before
    assert SECRET in _text(secret_pdf, 0)  # and the original still has its content


def test_redaction_refuses_to_write_over_its_input(secret_pdf):
    with pytest.raises(ValueError, match="refusing to write over the input"):
        redaction.redact_text(secret_pdf, SECRET, secret_pdf)


# ---- encrypted input, end to end (M41's other half) ---------------------------------


@pytest.fixture
def locked_pdf(tmp_path) -> str:
    path = str(tmp_path / "locked.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), SECRET, fontsize=14)
    page.insert_text((72, 200), PUBLIC, fontsize=14)
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret")
    doc.close()
    return path


def test_an_encrypted_document_can_be_redacted_with_its_password(locked_pdf, out):
    result = redaction.redact_text(locked_pdf, SECRET, out, password="secret")
    assert result["matches"] == 1
    with fitz.open(out) as doc:
        assert doc.authenticate("secret")
        assert SECRET not in doc[0].get_text("text")
        assert PUBLIC in doc[0].get_text("text")


def test_the_redacted_copy_of_an_encrypted_file_is_still_encrypted(locked_pdf, out):
    """M54 carry-through: a document opened with a password saves back with that password. A
    redacted copy that silently lost its encryption would be a second leak."""
    redaction.redact_text(locked_pdf, SECRET, out, password="secret")
    with fitz.open(out) as doc:
        assert doc.needs_pass  # PyMuPDF returns 1, not True


def test_redacting_an_encrypted_document_without_the_password_fails(locked_pdf, out):
    from model.virtual_document import PasswordRequired

    with pytest.raises(PasswordRequired):
        redaction.redact_text(locked_pdf, SECRET, out)
    assert not os.path.exists(out)


def test_transforms_carry_encryption_through_too(locked_pdf, tmp_path):
    """The write half of encrypted support, which M41 owns: a transform on an encrypted input
    produces an encrypted output, not a quietly decrypted one."""
    from mcp_bridge import transforms

    target = str(tmp_path / "rotated.pdf")
    transforms.rotate(locked_pdf, 90, target, password="secret")
    with fitz.open(target) as doc:
        assert doc.needs_pass  # PyMuPDF returns 1, not True
        assert doc.authenticate("secret")
        assert doc[0].rotation == 90


# ---- coverage: the query is gone, not merely the boxes -------------------------
#
# TC-001, the defect M44's verification pass found. `redact_text "regular expression"` with
# `whole_words: true` redacted 2 of 5 occurrences and reported success, because both checks in
# place at the time were box-scoped: they confirmed the regions chosen for redaction had lost
# their text, which they had. An occurrence the matcher never found was never a region, so it
# was never checked — and it widened the count budget by exactly the amount it leaked.

PHRASE = "regular expression"


@pytest.fixture
def phrase_pdf(tmp_path) -> str:
    """The three layouts the phrase takes in the javadoc extract that produced TC-001.

    Line 1 ends the sentence — the trailing-period boundary. Lines 2–3 wrap the phrase across a
    break, so MuPDF returns it as one box per fragment and *both* have to be cleared. Line 4 is
    the plain mid-line case that always worked, kept so a failure localises.
    """
    path = str(tmp_path / "phrases.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 100), "matches the given regular expression.", fontsize=11)
    page.insert_text((60, 130), "around matches of the given regular", fontsize=11)
    page.insert_text((60, 150), "expression. Trailing words follow.", fontsize=11)
    page.insert_text((60, 180), f"the given {PHRASE} with the given", fontsize=11)
    page.insert_text((60, 210), PUBLIC, fontsize=11)
    doc.save(path)
    doc.close()
    return path


def test_a_phrase_is_redacted_wherever_it_wraps_or_ends_a_sentence(phrase_pdf, out):
    """The end-to-end TC-001 assertion: nothing the caller searched for survives."""
    redaction.redact_text(phrase_pdf, PHRASE, out, whole_words=True)
    assert queries.search(out, PHRASE, whole_words=True) == []
    assert queries.search(out, "regular") == []
    assert queries.search(out, "expression") == []
    assert PUBLIC in _text(out, 0)  # neighbouring text untouched


def test_the_result_reports_the_residual_count_it_verified(phrase_pdf, out):
    result = redaction.redact_text(phrase_pdf, PHRASE, out, whole_words=True)
    assert result["residual_matches"] == 0


def test_a_matching_gap_is_caught_and_the_output_deleted(phrase_pdf, out, monkeypatch):
    """The check that would have failed TC-001 instead of passing it.

    The pre-fix boundary test is restored here — purely geometric, so a word box carrying a
    trailing period reads as a longer word and the match is dropped. The redaction then covers
    2 of 5 occurrences, and every box-scoped check still passes, exactly as it did in the report.
    The textual scan is what catches it: it owes the matcher nothing, so a match the matcher
    cannot see is still visible to it.
    """
    from model.page_text import PageText

    def geometric_only(self, box, tol=0.5):
        struck = self.struck(box)
        if not struck:
            return True
        return struck[0][1][0] >= box[0] - tol and struck[-1][1][2] <= box[2] + tol

    monkeypatch.setattr(PageText, "is_whole_word", geometric_only)
    with pytest.raises(RedactionLeak, match="still reads back"):
        redaction.redact_text(phrase_pdf, PHRASE, out, whole_words=True)
    assert not os.path.exists(out)


def test_the_residual_check_respects_the_page_scope(secret_pdf, out):
    """An occurrence outside the requested pages is out of scope, not a leak — otherwise every
    scoped redaction of a repeated string would delete its own output."""
    result = redaction.redact_text(secret_pdf, SECRET, out, pages=[1])
    assert result["residual_matches"] == 0
    assert SECRET in _text(out, 1)  # page 2 still has it, and that is correct


def test_a_substring_of_a_longer_word_is_not_reported_as_a_leak(tmp_path, out):
    """The false-positive `_verify` was built to avoid, which the new check must not reintroduce:
    redacting the standalone "Smith" leaves "Smithsonian", and that is a clean output."""
    path = str(tmp_path / "names.pdf")
    doc = fitz.open()
    doc.new_page().insert_text((60, 100), "Smith met Smithsonian staff.", fontsize=11)
    doc.save(path)
    doc.close()
    result = redaction.redact_text(path, "Smith", out, whole_words=True)
    assert result["residual_matches"] == 0
    assert "Smithsonian" in _text(out, 0)


def test_redact_regions_makes_no_residual_claim(secret_pdf, out):
    """There is no query to re-run, so the payload must not imply the wider check happened."""
    box = _box_of(secret_pdf, 0, SECRET)
    result = redaction.redact_regions(secret_pdf, [{"page": 1, "box": box}], out)
    assert "residual_matches" not in result


def test_a_wrapped_phrase_is_one_match_carrying_both_boxes(phrase_pdf, out):
    """`search` counts occurrences; `redact_text` clears every rectangle each one occupies.

    The two numbers differ exactly when a match wraps, and conflating them is what made the tool
    report "hits: 4" for a phrase occurring 5 times in TC-001.
    """
    hits = queries.search(phrase_pdf, PHRASE, whole_words=True)
    assert len(hits) == 3                                    # three occurrences on the page
    assert sorted(len(hit["boxes"]) for hit in hits) == [1, 1, 2]   # one of them wraps
    result = redaction.redact_text(phrase_pdf, PHRASE, out, whole_words=True)
    assert result["matches"] == 3
    assert result["boxes_redacted"] == 4                     # …and all four rectangles are cleared


def test_a_wrapped_hit_snippet_reads_as_the_whole_phrase(phrase_pdf):
    wrapped = next(h for h in queries.search(phrase_pdf, PHRASE, whole_words=True)
                   if len(h["boxes"]) == 2)
    assert "regular" in wrapped["snippet"] and "expression" in wrapped["snippet"]


def test_a_whole_search_hit_can_be_redacted_as_one_region(phrase_pdf, out):
    """The interop the plural key exists for: hand the hit back untouched and both halves go."""
    wrapped = next(h for h in queries.search(phrase_pdf, PHRASE, whole_words=True)
                   if len(h["boxes"]) == 2)
    redaction.redact_regions(phrase_pdf, [{"page": wrapped["page"], "boxes": wrapped["boxes"]}], out)
    left = queries.search(out, PHRASE, whole_words=True)
    assert len(left) == 2                             # the other two occurrences, untouched
    assert all(len(hit["boxes"]) == 1 for hit in left)  # the wrapped one is wholly gone


def test_a_region_cannot_carry_both_box_and_boxes(secret_pdf, out):
    box = _box_of(secret_pdf, 0, SECRET)
    with pytest.raises(ValueError, match="not both"):
        redaction.redact_regions(secret_pdf, [{"page": 1, "box": box, "boxes": [box]}], out)
    assert not os.path.exists(out)


# ---- M95 / TC-003: the checks that can fail when the matcher is wrong ----------------
#
# The fixture is the shape TC-003 found in a real utility bill: the value appears twice as ordinary
# visible text and twice more inside a machine-readable tag, in white-on-white 10 pt at the page
# margins. Both properties matter and neither is decoration — the tag is what `whole_words: true`
# cannot see, and the white ink is what a human approving the redaction cannot see.

TAGGED = "220885-1063303"


@pytest.fixture
def tagged_pdf(tmp_path) -> str:
    path = str(tmp_path / "tagged.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), TAGGED, fontsize=12)                       # plain, visible
    page.insert_text((72, 200), f"Account {TAGGED} due", fontsize=12)      # plain, visible
    for y, tag in ((16, f"<AccountNumber:{TAGGED}>"), (760, f"</AccountNumber:{TAGGED}>")):
        page.insert_text((72, y), tag, fontsize=10, color=(1, 1, 1))       # white on white
    doc.save(path)
    doc.close()
    return path


def test_the_fixture_reproduces_the_shape_the_report_found(tagged_pdf):
    """Guard the fixture itself: if these two counts ever agree, the tests below prove nothing."""
    assert len(queries.search(tagged_pdf, TAGGED, whole_words=True)) == 2
    assert len(queries.search(tagged_pdf, TAGGED, whole_words=False)) == 4


def test_a_value_hidden_inside_a_tag_is_reported_not_silently_left(tagged_pdf, out):
    """TC-003 ISSUE 1 — the defect this milestone exists for.

    `whole_words: true` is the documented, natural choice for an account number, and it matches
    only the two plain occurrences: a "word" ends at a space, so the whole `<AccountNumber:…>` tag
    is one word and the value inside it is not a match. That much is by design. What was not is
    that the verification then agreed — it re-checked with the same whole-word rule, found nothing,
    and reported `residual_matches: 0` with two engines behind it, over a file that still contained
    the account number twice.
    """
    result = redaction.redact_text(tagged_pdf, TAGGED, out, whole_words=True)

    assert result["matches"] == 2                  # the matcher still behaves as designed…
    assert result["residual_matches"] == 0         # …and the strict re-check still agrees…
    assert result["residual_literal"] == 2         # …but the literal scan does not, and says so.

    warning = "\n".join(result["warnings"])
    assert f"<AccountNumber:{TAGGED}>" in warning  # named, so the caller can judge it
    assert "whole_words: false" in warning         # and told what to do about it


def test_the_literal_scan_warns_rather_than_destroying_a_good_output(tmp_path, out):
    """It must never be wired to the delete. Redacting whole-word "Smith" correctly leaves
    "Smithsonian", which literally contains the query — failing on that would throw away a correct
    result, which is why this reports a token instead of a verdict."""
    path = str(tmp_path / "smith.pdf")
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "Smith and Smithsonian", fontsize=12)
    doc.save(path)
    doc.close()

    result = redaction.redact_text(path, "Smith", out, whole_words=True)
    assert os.path.exists(result["out"])           # not deleted
    assert result["residual_literal"] == 1
    assert "'Smithsonian'" in "\n".join(result["warnings"])   # the token is what makes it benign


def test_the_loose_mode_removes_the_tagged_value_and_reports_nothing_left(tagged_pdf, out):
    """The correct call, and the one the report had to reach by distrusting the tool."""
    result = redaction.redact_text(tagged_pdf, TAGGED, out, whole_words=False)
    assert result["matches"] == 4
    assert result["residual_literal"] == 0
    assert TAGGED not in _text(out, 0)


def test_a_matcher_that_cannot_see_an_occurrence_no_longer_passes_verification(
    tagged_pdf, out, monkeypatch
):
    """The invariant TC-003 asked for by name: *"assert that the post-write check is not the same
    code path as the matcher — a deliberately-broken matcher should fail the verification, not pass
    it."*

    So break the matcher outright — make every box look like part of a longer word, which is what
    the tag did on the real document — and require the report to disagree with it. Before M95 both
    checks consulted the same rule and this came back clean.
    """
    from model.page_text import PageText

    monkeypatch.setattr(PageText, "is_whole_word", lambda self, box, tol=0.5: False)
    with pytest.raises(ValueError, match="was not found"):
        redaction.redact_text(tagged_pdf, TAGGED, out, whole_words=True)
    assert not os.path.exists(out)

    # And a matcher broken by *degree* rather than outright — it finds one occurrence and misses an
    # identical one beside it — fails verification instead of certifying its own gap. This is the
    # textual pass doing its job: it does not consult `is_whole_word` at all, so patching the
    # matcher cannot patch the check.
    first = queries.search(tagged_pdf, TAGGED, whole_words=False)[0]
    boxes = {tuple(round(v, 1) for v in box) for box in first["boxes"]}
    monkeypatch.setattr(
        PageText, "is_whole_word",
        lambda self, box, tol=0.5: tuple(round(v, 1) for v in box) in boxes,
    )
    with pytest.raises(RedactionLeak, match="still reads back"):
        redaction.redact_text(tagged_pdf, TAGGED, out, whole_words=True)
    assert not os.path.exists(out)          # and no false-secure file is left behind


# ---- invisible text (TC-003 ISSUE 2) -------------------------------------------------


def test_invisible_text_is_flagged_on_a_search_hit(tagged_pdf):
    """A caller has no other way to learn this: the hit, the snippet and the box are identical to
    visible text, and `render_page` shows nothing there."""
    hits = queries.search(tagged_pdf, TAGGED, whole_words=False)
    assert [hit["invisible"] for hit in hits] == [False, False, True, True]


def test_visible_text_is_not_flagged_merely_for_being_white(tmp_path):
    """The rule is "was anything drawn", not "is it white" — and that distinction is the whole
    reason this renders instead of reading the colour. The bill this came from had 21 white spans,
    19 of them ordinary table headers on dark banners; flagging those would have made the flag
    worthless on the two that mattered."""
    path = str(tmp_path / "banner.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.draw_rect(fitz.Rect(60, 80, 300, 110), fill=(0, 0, 0))       # a dark banner…
    page.insert_text((72, 100), "HEADERWORD", fontsize=12, color=(1, 1, 1))  # …with white text on it
    doc.save(path)
    doc.close()

    hit = queries.search(path, "HEADERWORD")[0]
    assert hit["invisible"] is False


def test_redacting_invisible_text_says_so(tagged_pdf, out):
    """It was removed — and the caller is told, because the render they would check it against
    never showed it in the first place."""
    result = redaction.redact_text(tagged_pdf, TAGGED, out, whole_words=False)
    assert result["invisible_matches"] == 2
    assert "invisible" in "\n".join(result["warnings"])


def test_an_ordinary_redaction_carries_no_warnings(secret_pdf, out):
    """The quiet case stays quiet, or the noisy one stops being read."""
    result = redaction.redact_text(secret_pdf, SECRET, out)
    assert result["residual_literal"] == 0
    assert "warnings" not in result
    assert result.get("invisible_matches", 0) == 0


def test_both_warnings_survive_together(tmp_path, out):
    """The two checks emit into the same key from different places, and a plain dict merge would
    keep whichever ran last. A dropped warning is the defect this milestone is about.

    Needs a document that triggers both at once: one invisible occurrence the matcher *does* reach
    (so it is redacted and reported), and one inside a tag that it does not (so it survives and is
    reported).
    """
    path = str(tmp_path / "both.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), TAGGED, fontsize=12, color=(1, 1, 1))     # invisible, matchable
    page.insert_text((72, 300), f"<Account:{TAGGED}>", fontsize=12)       # visible, unmatchable
    doc.save(path)
    doc.close()

    result = redaction.redact_text(path, TAGGED, out, whole_words=True)
    assert result["invisible_matches"] == 1
    assert result["residual_literal"] == 1
    assert len(result["warnings"]) == 2
    assert any("invisible" in w for w in result["warnings"])
    assert any("whole_words: false" in w for w in result["warnings"])


# ---- M97 / TC-005: a region box spanning several text lines ----------------------------------


@pytest.fixture
def block_pdf(tmp_path) -> str:
    """Three stacked lines — the "signature block, letterhead, table cell" case the tool's own
    docs recommend region redaction for, and the one that always failed."""
    path = str(tmp_path / "block.pdf")
    doc = fitz.open()
    page = doc.new_page()
    for row, line in enumerate(["UMESH TYAGI", "1703 PORCELLANO WAY", "DUBLIN CA 94568"]):
        page.insert_text((72, 100 + row * 14), line, fontsize=11)
    page.insert_text((72, 300), "UMESH TYAGI appears again lower down", fontsize=11)
    doc.save(path)
    doc.close()
    return path


def _line_boxes(path: str) -> list[list[float]]:
    """One tight box per line of the block, top to bottom."""
    with fitz.open(path) as doc:
        words = [w for w in doc[0].get_text("words") if w[1] < 200]
    rows: dict = {}
    for w in words:
        rows.setdefault(round(w[1], 1), []).append(w)
    return [[min(w[0] for w in ws), min(w[1] for w in ws),
             max(w[2] for w in ws), max(w[3] for w in ws)]
            for _y, ws in sorted(rows.items())]


@pytest.mark.parametrize("lines", [1, 2, 3])
def test_a_region_box_covering_several_lines_succeeds(block_pdf, out, lines):
    """TC-005, the whole of it. A single `box` over one line worked; the same box extended to touch
    a second always failed and deleted its own correct output. The threshold was exactly one line,
    because the verification derived its needle by concatenating the characters in the box —
    welding `TYAGI` to `1703` into a token the document never had, whose budget was therefore
    impossible to satisfy.
    """
    boxes = _line_boxes(block_pdf)
    span = [boxes[0][0], boxes[0][1], max(b[2] for b in boxes[:lines]), boxes[lines - 1][3]]

    result = redaction.redact_regions(block_pdf, [{"page": 1, "box": span}], out)
    assert os.path.exists(result["out"])
    assert "TYAGI1703" not in " ".join(result["verified_text"]["1"])
    assert "TYAGI" in result["verified_text"]["1"]


def test_one_tall_box_and_one_box_per_line_agree(block_pdf, tmp_path):
    """The plural form was the workaround, so the fix is only complete when the two paths produce
    the same answer — that is what says the tall box is now processed correctly rather than merely
    not failing."""
    boxes = _line_boxes(block_pdf)
    span = [min(b[0] for b in boxes), boxes[0][1], max(b[2] for b in boxes), boxes[-1][3]]

    tall = redaction.redact_regions(block_pdf, [{"page": 1, "box": span}], str(tmp_path / "1.pdf"))
    each = redaction.redact_regions(block_pdf, [{"page": 1, "boxes": boxes}],
                                    str(tmp_path / "2.pdf"))
    assert tall["verified_text"] == each["verified_text"]


def test_the_block_really_is_gone_and_the_copy_elsewhere_is_not(block_pdf, out):
    """The contract the tool actually promises: these boxes are empty, nothing wider. The same name
    lower down survives — which is correct, and is the gap the tool doc now points at."""
    boxes = _line_boxes(block_pdf)
    span = [min(b[0] for b in boxes), boxes[0][1], max(b[2] for b in boxes), boxes[-1][3]]
    redaction.redact_regions(block_pdf, [{"page": 1, "box": span}], out)

    text = _text(out, 0)
    assert "94568" not in text                      # only ever appeared in the block
    assert "appears again lower down" in text       # outside the boxes, untouched


def test_an_impossible_budget_is_reported_as_such_not_as_a_contradiction(monkeypatch):
    """The second half of TC-005's error. When more boxes claim a token than the source ever had,
    the budget goes negative — and the message printed `max(allowed, 0)`, so an impossible -1 was
    rendered as `at most 0 expected` beside `still appears 0 time(s)`. That reads as a
    contradiction and sent the reporter hunting for a comparison bug that did not exist."""
    from mcp_bridge.redaction import _shortfall

    impossible = _shortfall(1, "TYAGI1703", "/tmp/x.pdf", found=0, allowed=-1,
                            before=0, covered=1, engine="PyMuPDF")
    assert "at most 0 expected" not in impossible
    assert "no output could satisfy" in impossible
    assert "not a leak" in impossible

    ordinary = _shortfall(1, "SECRET", "/tmp/x.pdf", found=2, allowed=1,
                          before=3, covered=2, engine="PyMuPDF")
    assert "still appears 2 time(s)" in ordinary and "at most 1 expected" in ordinary


# ---- M98 / TC-007: the two silent failures redaction had no counterweight for ------------------


@pytest.fixture
def policy_pdf(tmp_path) -> str:
    """A policy number written two ways, plus a page full of standalone digits — the two halves of
    TC-007 in one document."""
    path = str(tmp_path / "policy.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 90), "Policy Number: 607347469 203 1", fontsize=10)
    page.insert_text((60, 110), "Reference 6073474692031 on file", fontsize=10)
    page.insert_text((60, 130), "item 1 of 1, line 1, note 1, page 1", fontsize=10)
    page.insert_text((60, 150), "row 1 col 1 total 1 sum 1 count 1", fontsize=10)
    doc.save(path)
    doc.close()
    return path


def test_a_variant_spelling_left_in_the_file_is_reported(policy_pdf, out):
    """TC-007 FINDING 2. `607347469 203 1` and `6073474692031` are one policy number written two
    ways, and a literal scan sees neither in the other — so redacting one form reported the file
    clean while the other was still in it. Nothing is deleted here: the tool reports the spelling
    and the caller decides, because whether two spellings mean one value is a fact about the
    document."""
    result = redaction.redact_text(policy_pdf, "607347469 203 1", out, whole_words=True)

    assert result["residual_literal"] == 0            # the literal scan is genuinely blind to it
    assert result["residual_normalized"] == [
        {"as_written": "6073474692031", "pages": [1], "count": 1}
    ]
    assert "written differently" in "\n".join(result["warnings"])


def test_an_identifier_broken_by_a_line_wrap_is_reported(tmp_path, out):
    """Found while measuring the corpus rather than in the report: a number split across a line
    break (`526-\\n5999`) is invisible to every literal check, because the newline is a character
    the query does not have. Normalising separators is what makes it visible."""
    path = str(tmp_path / "wrapped.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 90), "Call 526-5999 for support", fontsize=10)
    page.insert_text((60, 130), "or dial 526-", fontsize=10)
    page.insert_text((60, 145), "5999 after hours", fontsize=10)
    doc.save(path)
    doc.close()

    result = redaction.redact_text(path, "526-5999", out, whole_words=True)
    assert result["residual_normalized"], "the wrapped copy was not reported"


def test_no_variant_no_warning(secret_pdf, out):
    """The quiet case stays quiet, or the noisy one stops being read.

    Changed deliberately at M98.1: this used to assert the *key* was absent when nothing was found.
    That is the contract the TC-007 retest asked to change, and rightly — omitting the field made
    "looked and found none" indistinguishable from "never looked", and the second reads as the
    first. The list is now always present; what stays quiet is the warning.
    """
    result = redaction.redact_text(secret_pdf, SECRET, out)
    assert result["residual_normalized"] == []
    assert "warnings" not in result


@pytest.mark.parametrize("query", ["1 2", "000000", "CA 1"])
def test_a_short_or_degenerate_query_never_triggers_a_variant_scan(tmp_path, out, query):
    """The floor is set by measurement, not taste: over 49 documents every false positive came from
    a query like these — `000000` matched across `708.000 0.00`, digits welded from two unrelated
    numbers. Nothing below the floor is scanned."""
    from mcp_bridge.redaction import _variant_residuals

    assert _variant_residuals("708.000 0.00 and 1-2 and CA-1", query, match_case=False) == []


def test_the_boundary_test_reads_the_source_not_the_normalised_stream():
    """TC-007 proposed requiring that the match "not sit inside a longer alphanumeric run", which
    is vacuous applied to the normalised form — stripping separators makes the whole stream
    alphanumeric, so every interior match is inside a longer run. Judged against the source, a
    query embedded in a longer identifier is correctly not a variant."""
    from mcp_bridge.redaction import _variant_residuals

    assert _variant_residuals("ref 1234567 here", "123-4567", match_case=False) == ["1234567"]
    assert _variant_residuals("ref 99123456789 here", "123-4567", match_case=False) == []


def test_over_redaction_is_reported_against_the_phrase_the_caller_meant(policy_pdf, out):
    """TC-007 FINDING 1. Default mode is a word list, so this query became three words — one of
    them `1` — and every standalone digit in the document was destroyed while the call reported
    zero residuals and cross-engine verification. Under-redaction had two checks; over-redaction
    had none, and it is the harder one to notice: a missed occurrence survives in the output and
    can be looked for, while destroyed content leaves no trace there at all."""
    result = redaction.redact_text(policy_pdf, "607347469 203 1", out)

    assert result["matches"] > 10
    terms = {entry["term"]: entry["matches"] for entry in result["query_terms"]}
    assert terms["1"] > terms["607347469"]
    warning = "\n".join(result["warnings"])
    assert "whole_words" in warning and "more than the phrase" in warning


def test_a_deliberate_word_list_is_not_warned_about(tmp_path, out):
    """The signal is "did you mean the phrase?", not "is one term commoner?" — share alone would
    fire on any two-word query whose second word happens to be more frequent. A query whose phrase
    never occurs is unambiguously a word list, and must stay quiet."""
    path = str(tmp_path / "names.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 90), "Smith reported and Jones agreed", fontsize=10)
    page.insert_text((60, 110), "Smith again, and Smith once more", fontsize=10)
    doc.save(path)
    doc.close()

    result = redaction.redact_text(path, "Smith Jones", out)
    assert result["matches"] == 4                       # it did redact both words everywhere…
    assert "warnings" not in result                     # …and that is what was asked for


def test_phrase_mode_is_never_warned_about_for_over_redaction(policy_pdf, out):
    result = redaction.redact_text(policy_pdf, "607347469 203 1", out, whole_words=True)
    assert "query_terms" not in result                  # one term; nothing was split
    assert not any("whole_words` was not set" in w for w in result.get("warnings", []))


# ---- M98.1 / TC-007 retest: the floor was blunter than the risk ------------------------------


@pytest.fixture
def variants_pdf(tmp_path) -> str:
    """Structured identifiers a person would type, each present in two or three spellings."""
    path = str(tmp_path / "variants.pdf")
    doc = fitz.open()
    page = doc.new_page()
    for row, line in enumerate([
        "SSN 999 99 9999 on record", "also 999-99-9999 and 999999999",
        "code AB 12 CD here", "also AB-12-CD there",
        "card 4444 5555 issued", "also 4444-5555 noted",
    ]):
        page.insert_text((50, 70 + row * 18), line, fontsize=10)
    doc.save(path)
    doc.close()
    return path


@pytest.mark.parametrize("query, expected", [
    ("999 99 9999", ["999-99-9999", "999999999"]),   # one repeated digit — was skipped
    ("AB 12 CD", ["AB-12-CD"]),                      # six normalised characters — was skipped
    ("4444 5555", ["4444-5555"]),                    # two distinct digits — was skipped
])
def test_a_punctuated_query_is_scanned_however_short_or_repetitive(
    variants_pdf, out, query, expected
):
    """TC-007 retest. The first floor applied to every query and silently skipped three obviously
    structured identifiers — two for repeating a character, one for being six characters long.

    Separators are the caller saying *this is a structured value*: `999 99 9999` has already
    declared what it is, while a bare `000000` could be anything and has to earn the scan. Measured
    over the same 49 documents, scanning 36 more queries produced exactly the same 41 hits, so the
    relaxation costs no precision.
    """
    result = redaction.redact_text(variants_pdf, query, out, whole_words=True)
    assert [v["as_written"] for v in result["residual_normalized"]] == expected


def test_a_bare_repetitive_run_is_still_not_scanned(tmp_path, out):
    """The other side of the same rule, and the reason there is a floor at all: `000000` matched
    across `708.000 0.00` in the corpus — digits from two unrelated numbers welded by dropping the
    separators. An unpunctuated query has declared nothing and still has to earn it."""
    path = str(tmp_path / "bare.pdf")
    doc = fitz.open()
    doc.new_page().insert_text((50, 70), "value 000000 and 708.000 0.00 here", fontsize=10)
    doc.save(path)
    doc.close()

    result = redaction.redact_text(path, "000000", out)
    assert result["residual_normalized"] is None


def test_scanned_and_found_nothing_is_not_spelled_the_same_way_as_never_scanned(
    variants_pdf, secret_pdf, out, tmp_path
):
    """TC-007 retest's own suggested minimum, and the sharpest point in it: the feature exists to
    close an *invisible* failure, so when it declines it must not look like a clean result. A list
    means it looked; `null` means it did not, and the `null` says why."""
    looked = redaction.redact_text(secret_pdf, SECRET, out)
    assert looked["residual_normalized"] == []          # looked, found none
    assert not any("NOT scanned" in w for w in looked.get("warnings", []))

    did_not_look = redaction.redact_text(variants_pdf, "AB", str(tmp_path / "b.pdf"))
    assert did_not_look["residual_normalized"] is None  # did not look…
    assert any("NOT scanned" in w for w in did_not_look["warnings"])   # …and says so


def test_the_key_is_always_present(secret_pdf, out):
    """Absence of the key must never be a third answer."""
    assert "residual_normalized" in redaction.redact_text(secret_pdf, SECRET, out)
