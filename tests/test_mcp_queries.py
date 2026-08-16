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
