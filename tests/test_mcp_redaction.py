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
    redaction.redact_regions(secret_pdf, [{"page": hit["page"], "box": hit["box"]}], out)
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
    assert result["hits"] == 1
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
