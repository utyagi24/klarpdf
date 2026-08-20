"""M39 — the read-only query helpers behind the MCP tools.

These call ``mcp_bridge.queries`` directly rather than through the protocol: the helpers hold the
PDF behaviour, ``server.py`` is a schema adapter, and a bug in one should not be able to hide in
the other. ``tests/test_mcp_server.py`` covers the protocol side; ``tests/test_mcp_no_qt.py``
covers the invariant that none of this drags Qt in.

Fixtures are the suite's standard `A.pdf` (3 pages, multi-level outline, a `name` form field) and
`B.pdf` — the same ones `test_materialize.py` uses, so the transform tools land on invariants that
are already pinned.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from mcp_bridge import queries
from model.virtual_document import PasswordRequired
from tests.conftest import A_TEXT


@pytest.fixture
def scanned_pdf(tmp_path) -> str:
    """A PDF with no text layer at all — a stand-in for a scan."""
    path = str(tmp_path / "scan.pdf")
    doc = fitz.open()
    for _ in range(3):
        page = doc.new_page()
        page.draw_rect(fitz.Rect(72, 72, 300, 200), fill=(0.8, 0.8, 0.8))
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def encrypted_pdf(tmp_path) -> str:
    path = str(tmp_path / "locked.pdf")
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "LOCKED-secret-text", fontsize=14)
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret")
    doc.close()
    return path


@pytest.fixture
def restricted_pdf(tmp_path) -> str:
    """Encrypted with an **owner** password only: it opens with no password and still forbids
    copying, modification and assembly. The shape a published form arrives in, and the one
    ``is_encrypted`` reports as False."""
    path = str(tmp_path / "restricted.pdf")
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "restricted", fontsize=14)
    keep = (fitz.PDF_PERM_PRINT | fitz.PDF_PERM_PRINT_HQ | fitz.PDF_PERM_ANNOTATE
            | fitz.PDF_PERM_FORM | fitz.PDF_PERM_ACCESSIBILITY)
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_128, owner_pw="owner", permissions=keep)
    doc.close()
    return path


# ---- get_info: the routing call ---------------------------------------------


def test_info_reports_the_shape_of_the_document(a_pdf):
    info = queries.document_info(a_pdf)
    assert info["pages"] == 3
    assert info["size_bytes"] > 0
    assert info["encrypted"] is False
    assert info["has_outline"] is True
    assert info["has_text_layer"] is True
    assert info["first_page_with_text"] == 1


def test_info_distinguishes_a_scan_from_a_text_document(scanned_pdf):
    """The distinction the tool exists to make: no text layer means search/extract are pointless
    and render_page is the only way in."""
    info = queries.document_info(scanned_pdf)
    assert info["has_text_layer"] is False
    assert info["first_page_with_text"] is None


def test_info_groups_page_sizes(a_pdf):
    sizes = queries.document_info(a_pdf)["page_sizes"]
    assert len(sizes) == 1  # uniform document → one entry, not three
    assert sizes[0]["pages"] == [1, 2, 3]
    assert sizes[0]["width_pt"] == pytest.approx(595.3, abs=1)


def test_info_on_an_encrypted_file_asks_for_a_password_instead_of_failing(encrypted_pdf):
    info = queries.document_info(encrypted_pdf)
    assert info["encrypted"] is True
    assert info["needs_password"] is True
    assert "pages" not in info  # nothing was read, and it does not pretend otherwise


def test_info_with_the_password_reads_the_document(encrypted_pdf):
    info = queries.document_info(encrypted_pdf, password="secret")
    assert info["needs_password"] is False
    assert info["encrypted"] is True  # still an encrypted file; we just have the key
    assert info["pages"] == 1


def test_info_reports_an_owner_password_document_as_encrypted(restricted_pdf):
    """TC-002 ISSUE 5. ``encrypted`` used to be ``password is not None`` — an answer to "did the
    caller hand me a password?" — so the one file that opens freely *and* restricts what may be
    done with it reported ``false``, from the tool documented as the call that answers what changes
    everything else. ``is_encrypted`` is no better: it is False for anything that opened."""
    info = queries.document_info(restricted_pdf)
    assert info["encrypted"] is True
    assert info["needs_password"] is False       # …and no password is needed to read it
    assert "AES" in info["encryption"]


def test_info_names_what_a_restricted_document_forbids(restricted_pdf):
    permissions = queries.document_info(restricted_pdf)["permissions"]
    assert permissions["copy"] is False
    assert permissions["modify"] is False
    assert permissions["assemble"] is False
    assert permissions["print"] is True and permissions["fill_forms"] is True


def test_info_reports_an_unprotected_document_as_permitting_everything(a_pdf):
    info = queries.document_info(a_pdf)
    assert info["encrypted"] is False and info["encryption"] is None
    assert all(info["permissions"].values())


# ---- get_outline -------------------------------------------------------------


def test_outline_preserves_nesting_and_1_based_pages(a_pdf):
    assert queries.outline(a_pdf) == [
        {"level": 1, "title": "Chapter 1", "page": 1},
        {"level": 2, "title": "Section 1.1", "page": 2},
        {"level": 1, "title": "Chapter 2", "page": 3},
    ]


def test_outline_of_a_document_without_one_is_empty(b_pdf):
    assert queries.outline(b_pdf) == []


# ---- search ------------------------------------------------------------------


def test_search_finds_the_page_and_a_snippet(a_pdf):
    hits = queries.search(a_pdf, A_TEXT[1])
    assert len(hits) == 1
    assert hits[0]["page"] == 2  # 1-based: A_TEXT[1] is on the second page
    assert A_TEXT[1] in hits[0]["snippet"]
    assert len(hits[0]["boxes"]) == 1 and len(hits[0]["boxes"][0]) == 4


def test_search_is_case_insensitive_by_default_and_match_case_filters(a_pdf):
    assert queries.search(a_pdf, A_TEXT[0].lower()) != []
    assert queries.search(a_pdf, A_TEXT[0].lower(), match_case=True) == []
    assert queries.search(a_pdf, A_TEXT[0], match_case=True) != []


def test_whole_words_makes_the_query_one_phrase(a_pdf, tmp_path):
    """Off, "ALPHA zero" is two words that each match on their own. On, it is one phrase."""
    path = str(tmp_path / "phrase.pdf")
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "ALPHA zero and ALPHA one", fontsize=11)
    doc.save(path)
    doc.close()

    loose = queries.search(path, "ALPHA zero")
    assert len(loose) == 3  # two ALPHAs + one zero

    phrase = queries.search(path, "ALPHA zero", whole_words=True)
    assert len(phrase) == 1


def test_search_for_nothing_returns_nothing(a_pdf):
    assert queries.search(a_pdf, "") == []
    assert queries.search(a_pdf, "   ") == []


def test_search_misses_are_empty_not_an_error(a_pdf):
    assert queries.search(a_pdf, "no-such-string-anywhere") == []


# ---- extract_text -------------------------------------------------------------


def test_extract_text_defaults_to_the_whole_document(a_pdf):
    result = queries.extract_text(a_pdf)
    assert result["page_count"] == 3
    assert [p["page"] for p in result["pages"]] == [1, 2, 3]
    for i, page in enumerate(result["pages"]):
        assert A_TEXT[i] in page["text"]


def test_extract_text_takes_1_based_pages_in_document_order(a_pdf):
    result = queries.extract_text(a_pdf, pages=[3, 1])
    assert [p["page"] for p in result["pages"]] == [1, 3]
    assert A_TEXT[0] in result["pages"][0]["text"]
    assert A_TEXT[2] in result["pages"][1]["text"]


def test_a_repeated_page_is_extracted_once(a_pdf):
    assert [p["page"] for p in queries.extract_text(a_pdf, pages=[2, 2, 2])["pages"]] == [2]


@pytest.mark.parametrize("bad", [0, 4, -1, 999])
def test_out_of_range_pages_are_rejected_not_clamped(a_pdf, bad):
    """An agent that asked for page 900 of a 3-page file has a wrong belief; returning page 3
    would confirm it."""
    with pytest.raises(ValueError, match="out of range"):
        queries.extract_text(a_pdf, pages=[bad])


def test_non_integer_pages_are_rejected(a_pdf):
    with pytest.raises(ValueError, match="must be integers"):
        queries.extract_text(a_pdf, pages=["2"])


# ---- render_page ---------------------------------------------------------------


def test_render_page_returns_png_bytes(a_pdf):
    result = queries.render_page(a_pdf, 1)
    assert result["png"][:8] == b"\x89PNG\r\n\x1a\n"
    assert result["page"] == 1
    assert result["width_px"] > 0 and result["height_px"] > 0


def test_render_dpi_scales_the_image(a_pdf):
    low = queries.render_page(a_pdf, 1, dpi=72)
    high = queries.render_page(a_pdf, 1, dpi=144)
    assert high["width_px"] == pytest.approx(low["width_px"] * 2, abs=2)


def test_render_rejects_a_nonsense_dpi(a_pdf):
    with pytest.raises(ValueError, match="dpi must be positive"):
        queries.render_page(a_pdf, 1, dpi=0)


def test_render_rejects_an_out_of_range_page(a_pdf):
    with pytest.raises(ValueError, match="out of range"):
        queries.render_page(a_pdf, 99)


# ---- get_form_fields -------------------------------------------------------------


def test_form_fields_are_listed_with_1_based_pages_and_values(a_pdf):
    fields = queries.form_fields(a_pdf)
    assert len(fields) == 1
    field = fields[0]
    assert field["name"] == "name"
    assert field["page"] == 1
    assert field["value"] == "A-value"
    assert field["type"] == "Text"
    assert len(field["rect"]) == 4


def test_a_document_without_fields_returns_an_empty_list(scanned_pdf):
    assert queries.form_fields(scanned_pdf) == []


def test_a_checkboxs_on_state_is_reported_because_it_cannot_be_guessed(awkward_form_pdf):
    """TC-002 ISSUE 6: the value that ticks a box is per-widget — ``"2"`` here, ``"1"`` on the box
    beside it in the form this is modelled on, ``"Yes"`` on neither. ``choices`` cannot carry it
    (PyMuPDF fills ``choice_values`` for combo/list only), so a caller who could not read it had
    nothing to do but guess."""
    fields = {field["name"]: field for field in queries.form_fields(awkward_form_pdf)}
    assert fields["married"]["on_state"] == "2"
    assert set(fields["married"]["states"]) == {"2", "Off"}
    assert fields["remarks"]["on_state"] is None       # not a button; nothing to report
    assert fields["remarks"]["states"] is None


def test_field_flags_separate_form_plumbing_from_fields_a_person_fills(awkward_form_pdf):
    """The form this is modelled on carries three read-only 3-pt slivers that were indistinguishable
    from real fields in the listing."""
    fields = {field["name"]: field for field in queries.form_fields(awkward_form_pdf)}
    assert fields["plumbing"]["read_only"] is True
    assert fields["remarks"]["read_only"] is False and fields["remarks"]["multiline"] is True
    assert fields["ssn"]["required"] is True and fields["ssn"]["max_len"] == 9
    assert fields["married"]["multiline"] is False    # bit 13 means something else on a button


# ---- encryption + resource hygiene -------------------------------------------


def test_an_encrypted_document_without_a_password_raises(encrypted_pdf):
    with pytest.raises(PasswordRequired):
        queries.extract_text(encrypted_pdf)


def test_a_wrong_password_raises_instead_of_looping(encrypted_pdf):
    """The model's provider re-prompts on a wrong password. There is no user behind an agent call,
    so the adapter must decline the retry — otherwise the server hangs forever."""
    with pytest.raises(PasswordRequired):
        queries.extract_text(encrypted_pdf, password="wrong")


def test_the_right_password_reads_the_content(encrypted_pdf):
    text = queries.extract_text(encrypted_pdf, password="secret")["pages"][0]["text"]
    assert "LOCKED-secret-text" in text


def test_queries_do_not_hold_the_file_open(a_pdf, tmp_path):
    """Every helper opens through ``open_document``, which reads the bytes and closes. A lingering
    handle would block the atomic rename the app's own Save depends on (M38.5)."""
    import os

    queries.document_info(a_pdf)
    queries.outline(a_pdf)
    queries.search(a_pdf, "ALPHA")
    queries.extract_text(a_pdf)
    queries.render_page(a_pdf, 1)
    queries.form_fields(a_pdf)

    replacement = tmp_path / "other.pdf"
    replacement.write_bytes(open(a_pdf, "rb").read())
    os.replace(replacement, a_pdf)  # would raise PermissionError on Windows if a handle were open
    assert os.path.exists(a_pdf)


def test_a_missing_file_raises_a_clear_error(tmp_path):
    with pytest.raises((FileNotFoundError, OSError)):
        queries.document_info(str(tmp_path / "nope.pdf"))


# ---- M96 / TC-004: a neighbouring line must not veto a whole-word match ----------------------


@pytest.fixture
def tight_leading_pdf(tmp_path) -> str:
    """Lines close enough that consecutive word boxes overlap vertically — the ordinary shape of a
    dense form, and the one that broke whole-word search.

    12 pt text on a 10 pt pitch: each word box spans ascender to descender (~13.8 pt), so every
    line's boxes intrude into its neighbours' and `boxes_touch` cannot tell that from a word the
    hit actually covers.
    """
    path = str(tmp_path / "tight.pdf")
    doc = fitz.open()
    page = doc.new_page()
    for row, line in enumerate([
        "Discontinue Prior Editions",
        "Social Security Administration",
        "SOCIAL SECURITY NUMBER",
        "Spouse's Social Security Number",
    ]):
        page.insert_text((40, 60 + row * 10), line, fontsize=12)
    doc.save(path)
    doc.close()
    return path


def test_the_fixture_really_has_overlapping_line_boxes(tight_leading_pdf):
    """Guard the fixture: without the overlap it proves nothing, and the overlap is the whole
    mechanism."""
    doc = fitz.open(tight_leading_pdf)
    try:
        words = doc[0].get_text("words")
        rows = sorted({(round(w[1], 1), round(w[3], 1)) for w in words})
        assert any(rows[i + 1][0] < rows[i][1] for i in range(len(rows) - 1)), (
            f"no line-box overlap in {rows} — the fixture does not reproduce the defect"
        )
    finally:
        doc.close()


@pytest.mark.parametrize("query, expected", [("Security", 3), ("Social", 3), ("Number", 2)])
def test_whole_word_search_finds_every_free_standing_occurrence(
    tight_leading_pdf, query, expected
):
    """TC-004. `search "Security"` on a real form returned **1 of 5**: each rejected hit was judged
    against a word on the line above, whose letters naturally run past the hit's left edge, so the
    edge looked like the middle of a word.

    Every occurrence here is free-standing — spaces on both sides — so there is no reading of
    "whole word" under which any of them should be dropped, and whole-word must agree with the
    loose mode that already found them all.
    """
    tight = queries.search(tight_leading_pdf, query, whole_words=True)
    loose = queries.search(tight_leading_pdf, query, whole_words=False)
    assert len(tight) == len(loose) == expected


def test_two_occurrences_on_one_line_are_both_found(tmp_path):
    """The second symptom TC-004 reported as "first match per line only". It was the same defect —
    the second `DATE` sat next to a word from the adjacent line — but it is worth its own test,
    because a per-line dedup would be a genuinely different bug with the same appearance."""
    path = str(tmp_path / "twice.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 100), "SPOUSE'S DATE OF BIRTH GIVE DATE OF DEATH", fontsize=12)
    page.insert_text((40, 110), "IF SPOUSE IS DECEASED", fontsize=12)
    doc.save(path)
    doc.close()

    assert len(queries.search(path, "DATE", whole_words=True)) == 2


def test_a_longer_query_never_matches_more_often_than_a_word_inside_it(tight_leading_pdf):
    """The signature TC-004 flagged as worth chasing: `Social` found 3 of 5 while
    `Social Security Number` found the two it had missed. A longer, more specific query matching
    *more* occurrences than a word contained in it is impossible under any consistent rule."""
    short = queries.search(tight_leading_pdf, "Social", whole_words=True)
    long = queries.search(tight_leading_pdf, "Social Security Number", whole_words=True)
    assert len(long) <= len(short)


def test_sub_word_matches_are_still_rejected(tmp_path):
    """The fix must not buy recall with precision — these are the invariants whole-word exists for
    (M64's hyphenated compound, TC-001's trailing period, and the plain longer-word case)."""
    path = str(tmp_path / "reject.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 100), "Smith and Smithsonian", fontsize=12)
    page.insert_text((40, 130), "ALPHA-zero-A0 and ALPHA", fontsize=12)
    page.insert_text((40, 160), "expression. and expressionless", fontsize=12)
    doc.save(path)
    doc.close()

    assert len(queries.search(path, "Smith", whole_words=True)) == 1
    assert len(queries.search(path, "ALPHA", whole_words=True)) == 1
    assert len(queries.search(path, "expression", whole_words=True)) == 1


def test_the_app_find_bar_agrees_with_the_bridge(tight_leading_pdf):
    """Both go through `PageText.is_whole_word`, so the shipped find bar had the same under-count —
    on this fixture it is the words-only path, which reaches the same rule by a different route."""
    from viewer.search import is_whole_word

    doc = fitz.open(tight_leading_pdf)
    try:
        page, words = doc[0], doc[0].get_text("words")
        shown = [r for r in page.search_for("Security")
                 if is_whole_word(words, (r.x0, r.y0, r.x1, r.y1))]
        assert len(shown) == len(page.search_for("Security"))
    finally:
        doc.close()


# ---- M99: a region clip on the imaging tools ------------------------------------


def test_clip_pixel_size_follows_the_clip_not_the_page(a_pdf):
    """The number a caller sizes a layout from must describe the image it actually received.

    PyMuPDF derives the pixmap from the clip, so this is really pinning that we pass the clip at
    all — but it is the exact failure PLAN.md §M99 names, and it is invisible without the assert:
    a dropped `clip=` still returns a valid PNG, just of the whole page.
    """
    whole = queries.render_page(a_pdf, 1, dpi=72)
    part = queries.render_page(a_pdf, 1, dpi=72, clip=[0, 0, 144, 72])

    assert (part["width_px"], part["height_px"]) == (144, 72)
    assert part["width_px"] < whole["width_px"] and part["height_px"] < whole["height_px"]
    assert part["clip"] == [0.0, 0.0, 144.0, 72.0]
    assert len(part["png"]) < len(whole["png"])


def test_clip_scales_with_dpi(a_pdf):
    """The clip is in points and the dpi multiplies it — the two must not fight."""
    at72 = queries.render_page(a_pdf, 1, dpi=72, clip=[0, 0, 100, 50])
    at144 = queries.render_page(a_pdf, 1, dpi=144, clip=[0, 0, 100, 50])
    assert (at72["width_px"], at72["height_px"]) == (100, 50)
    assert (at144["width_px"], at144["height_px"]) == (200, 100)


def test_no_clip_is_byte_identical_to_before(a_pdf):
    """The regression guard: adding the parameter must not have changed the default render."""
    a = queries.render_page(a_pdf, 1, dpi=100)
    b = queries.render_page(a_pdf, 1, dpi=100, clip=None)
    assert a["png"] == b["png"]
    assert a["clip"] is None


def test_a_search_hit_feeds_straight_back_in_as_a_clip(a_pdf):
    """The composition M99 exists for: `search` → look at the pixels of the match.

    Whether the crop is *legible* is not something a test can assert, so this pins the two things
    that would break the workflow mechanically — the hit's box is accepted verbatim, and the region
    it selects is genuinely a small part of the page rather than the whole thing.
    """
    import pymupdf as fitz

    (hit,) = [h for h in queries.search(a_pdf, A_TEXT[0]) if h["page"] == 1]
    # A hit is `boxes`, one per line (#250). Union them — the documented pattern for seeing a whole
    # match, and the reason `clip` takes one rect rather than the list `redact_regions` takes.
    region = fitz.Rect(hit["boxes"][0])
    for box in hit["boxes"][1:]:
        region |= fitz.Rect(box)
    rendered = queries.render_page(a_pdf, 1, dpi=150, clip=list(region))

    assert rendered["clip"] == pytest.approx(list(region), abs=0.01)
    whole = queries.render_page(a_pdf, 1, dpi=150)
    assert rendered["width_px"] * rendered["height_px"] < whole["width_px"] * whole["height_px"] / 4


@pytest.mark.parametrize(
    "clip, expected",
    [
        ([100, 100, 100, 200], "empty or inverted"),      # zero width
        ([100, 100, 50, 200], "empty or inverted"),       # x reversed
        ([100, 200, 150, 100], "empty or inverted"),      # y reversed
        ([-5, 0, 100, 100], "outside page 1"),            # off the left edge
        ([0, 0, 100, 10_000], "outside page 1"),          # off the bottom
        ([0, 0, 100], r"must be \[x0, y0, x1, y1\]"),     # three numbers
        (["a", 0, 100, 100], "must be four numbers"),     # not numbers
    ],
)
def test_a_bad_clip_is_refused_with_a_reason(a_pdf, clip, expected):
    """Refused rather than clamped — `render_page` returns an image block, so a silently adjusted
    clip has no channel to say so (see `model.export.resolve_clip`). The message names the page
    rect because the caller otherwise cannot tell which edge overhung."""
    with pytest.raises(ValueError, match=expected):
        queries.render_page(a_pdf, 1, clip=clip)


def test_a_clip_a_hair_over_the_edge_is_allowed(a_pdf):
    """Float noise in a computed box must not be an error; a real overhang still is."""
    import pymupdf as fitz

    doc = fitz.open(a_pdf)
    rect = doc[0].rect
    doc.close()

    rendered = queries.render_page(a_pdf, 1, dpi=72, clip=[0, 0, rect.x1 + 0.005, 50])
    assert rendered["width_px"] == round(rect.x1 * 72 / 72)


# ---- M99.1 / TC-008 Finding 3: a clip on a rotated page -----------------------------


@pytest.fixture
def landscape_pdf(tmp_path) -> str:
    """A natively-landscape page with text near the right edge, past a rotated page's width.

    792 wide, so a box out at x≈700 sits beyond the 612 the page reports once turned a quarter —
    which is what made the second failure reachable rather than theoretical.
    """
    path = str(tmp_path / "landscape.pdf")
    doc = fitz.open()
    page = doc.new_page(width=792, height=612)
    page.insert_text((488, 185), "PACIFICA", fontsize=11)
    page.insert_text((700, 310), "1.800.252.4633", fontsize=11)
    doc.save(path)
    doc.close()
    return path


def _ink(png: bytes) -> int:
    """Dark pixels in a PNG — "did anything actually get drawn here?"."""
    pixmap = fitz.Pixmap(png)
    samples = pixmap.samples
    return sum(1 for i in range(0, len(samples), pixmap.n) if samples[i] < 200)


@pytest.mark.parametrize("degrees", [0, 90, 180, 270])
def test_a_search_hit_clips_to_the_same_text_at_every_rotation(landscape_pdf, tmp_path, degrees):
    """The promise `clip` is documented on: hand a `search` box straight back and see that match.

    `search_for` reports **unrotated** coordinates — byte-identical at every rotation — while
    `page.rect` is the *displayed* rect and is also what `get_pixmap` clips in. Validating against
    `page.rect` put `clip` on the far side of the rotation from every box a caller has, and the
    render came back **blank** with no error at all (TC-008 Finding 3). Ink is the assertion because
    a wrong region still returns a perfectly valid PNG.
    """
    from mcp_bridge import transforms

    path = landscape_pdf
    if degrees:
        path = str(tmp_path / f"rot{degrees}.pdf")
        transforms.rotate(landscape_pdf, degrees, path)

    (hit,) = [h for h in queries.search(path, "PACIFICA") if h["page"] == 1]
    rendered = queries.render_page(path, 1, dpi=150, clip=hit["boxes"][0])

    assert _ink(rendered["png"]) > 50, f"blank render at /Rotate {degrees} — wrong region clipped"
    # A quarter turn swaps the image's axes; a half turn does not.
    wide = rendered["width_px"] > rendered["height_px"]
    assert wide is (degrees in (0, 180))


def test_the_clip_echo_stays_in_the_callers_coordinates(landscape_pdf, tmp_path):
    """`resolve_clip` hands the rasteriser a *displayed*-space rect, which on a rotated page is a
    different quadruple from the one passed in. Echoing that would tell the caller their clip had
    been altered."""
    from mcp_bridge import transforms

    rotated = str(tmp_path / "rot90.pdf")
    transforms.rotate(landscape_pdf, 90, rotated)
    (hit,) = [h for h in queries.search(rotated, "PACIFICA") if h["page"] == 1]
    box = hit["boxes"][0]

    assert queries.render_page(rotated, 1, dpi=72, clip=box)["clip"] == pytest.approx(box)


def test_a_box_past_the_displayed_width_is_not_refused_on_a_rotated_page(landscape_pdf, tmp_path):
    """The second half of Finding 3, and the more embarrassing one: one server, one page, one call
    apart — `search` returned a box out to x≈776 and `clip` rejected it as off-page, because the
    turned page reports a width of 612."""
    from mcp_bridge import transforms

    rotated = str(tmp_path / "rot90.pdf")
    transforms.rotate(landscape_pdf, 90, rotated)
    (hit,) = [h for h in queries.search(rotated, "1.800.252.4633") if h["page"] == 1]
    assert hit["boxes"][0][2] > 612, "fixture no longer exercises the case"

    rendered = queries.render_page(rotated, 1, dpi=150, clip=hit["boxes"][0])
    assert _ink(rendered["png"]) > 50


def test_a_genuinely_off_page_clip_is_still_refused_when_rotated(landscape_pdf, tmp_path):
    """Widening the accepted space must not disable the check — the unrotated page is 792x612, so
    y=700 is off it however the page is turned."""
    from mcp_bridge import transforms

    rotated = str(tmp_path / "rot90.pdf")
    transforms.rotate(landscape_pdf, 90, rotated)
    with pytest.raises(ValueError, match="outside page 1"):
        queries.render_page(rotated, 1, dpi=72, clip=[0, 0, 100, 700])


def test_the_refusal_says_the_page_is_rotated(landscape_pdf, tmp_path):
    """A caller told their box is outside `[0, 0, 792, 612]` while looking at a page the viewer
    shows as 612x792 needs to know which of the two they are being measured against."""
    from mcp_bridge import transforms

    rotated = str(tmp_path / "rot90.pdf")
    transforms.rotate(landscape_pdf, 90, rotated)
    with pytest.raises(ValueError, match="rotated 90"):
        queries.render_page(rotated, 1, dpi=72, clip=[0, 0, 100, 700])


# ---- M104 / TC-008 Finding 2: the pixel size rounds outward ------------------------------


@pytest.mark.parametrize(
    "clip, dpi, expected",
    [
        ([100, 100, 200, 200], 150, (209, 209)),   # 100pt at 150dpi is 209px, not 208.33
        ([100, 100, 200, 200], 72, (100, 100)),    # 1:1 scale, exact
        ([0, 0, 200, 100], 72, (200, 100)),
    ],
)
def test_the_clipped_pixel_size_rounds_outward(a_pdf, clip, dpi, expected):
    """`ceil(x1 * s) - floor(x0 * s)`, not `(x1-x0) * s`.

    The right policy — expanding outward drops no partial pixel of the requested region — but it was
    unstated, and a caller sizing a layout from the naive formula is off by up to 1 px per axis
    (TC-008 Finding 2). Pinned so the docs and the behaviour cannot drift apart.
    """
    rendered = queries.render_page(a_pdf, 1, dpi=dpi, clip=clip)
    assert (rendered["width_px"], rendered["height_px"]) == expected


def test_get_info_distinguishes_a_rotated_page_from_a_native_landscape_one(tmp_path):
    """M107.1 — `page_sizes` reports *displayed* dimensions while `clip` and `redact_regions` take
    unrotated ones, so without the angle a portrait page turned 90° and a native landscape page are
    the same row and a caller computing a box by hand cannot tell which convention it is in
    (TC-008). Rotation is part of the grouping key, not just a field, or the two would still merge.
    """
    import pymupdf

    path = str(tmp_path / "rotated.pdf")
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)                      # native portrait
    doc.new_page(width=612, height=792)
    doc[1].set_rotation(90)                                  # portrait turned 90
    doc.new_page(width=792, height=612)                      # native landscape
    doc.save(path)
    doc.close()

    sizes = queries.document_info(path)["page_sizes"]
    by_page = {entry["pages"][0]: entry for entry in sizes}
    assert by_page[1]["rotation"] == 0
    assert by_page[2] == {"width_pt": 792.0, "height_pt": 612.0, "rotation": 90, "pages": [2]}
    assert by_page[3] == {"width_pt": 792.0, "height_pt": 612.0, "rotation": 0, "pages": [3]}
    # Same displayed geometry, different convention — they must not share a row.
    assert by_page[2] is not by_page[3]


def test_the_search_cap_note_does_not_advise_a_flag_that_is_already_set(tmp_path):
    """M108.2 — the truncation note ended "or set `whole_words`…" on calls that had already set it
    (TC-011). Advice that does not apply reads as a stock message and trains a caller to skip the
    note; TC-007 item E fixed the same shape in the redaction warnings by substituting the
    applicable explanation rather than appending a generic one.
    """
    import asyncio
    import json

    import pymupdf

    from mcp_bridge.config import Config
    from mcp_bridge.server import create_server

    path = str(tmp_path / "many.pdf")
    doc = pymupdf.open()
    for _ in range(60):
        page = doc.new_page()
        for row in range(12):
            page.insert_text((72, 60 + row * 20), "alpha beta gamma")
    doc.save(path)
    doc.close()

    server = create_server(Config())

    def note(whole_words: bool) -> str:
        reply = server.call_tool(
            "search", {"path": path, "query": "alpha", "whole_words": whole_words}
        )
        return json.loads(asyncio.run(reply).content[0].text)["note"]

    strict, loose = note(True), note(False)
    assert "set `whole_words`" not in strict and "already on" in strict
    assert "set `whole_words`" in loose
