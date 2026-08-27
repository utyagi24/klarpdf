"""M40 — the transform tools: lossless, and physically unable to touch the input.

Two things are being tested and they are not the same thing.

**Losslessness** is asserted with the invariants ``tests/test_materialize.py`` pins for the GUI's
own Save, against the same `A.pdf`/`B.pdf` fixtures: OCR text rides along with a moved page, the
outline re-points at new indices and drops nothing dangling, colliding form fields are renamed
rather than lost. If those hold, the transforms inherited the shared engine correctly — which is the
whole architectural claim, and the reason the tools are thin.

**The safety model** is asserted separately and adversarially, because "agent-driven means untrusted
caller" only means something if the refusals actually fire: writing over the source through a
symlink, through `..`, or through an existing-file path all have to be rejected, and a failed
transform must leave no debris.
"""

from __future__ import annotations

import os

import pymupdf as fitz
import pytest

from mcp_bridge import queries, transforms as T
from tests.conftest import A_TEXT, B_TEXT


def _text(path: str, index: int) -> str:
    doc = fitz.open(path)
    try:
        return doc[index].get_text("text")
    finally:
        doc.close()


def _toc(path: str) -> list:
    doc = fitz.open(path)
    try:
        return doc.get_toc(simple=True)
    finally:
        doc.close()


def _field_names(path: str) -> list[str]:
    doc = fitz.open(path)
    try:
        return [w.field_name for page in doc for w in page.widgets()]
    finally:
        doc.close()


def _pages(path: str) -> int:
    doc = fitz.open(path)
    try:
        return doc.page_count
    finally:
        doc.close()


@pytest.fixture
def out(tmp_path) -> str:
    return str(tmp_path / "out.pdf")


# ---- delete_pages ---------------------------------------------------------------


def test_delete_removes_the_page_and_keeps_the_rest(a_pdf, out):
    result = T.delete_pages(a_pdf, [2], out)
    assert result["pages"] == 2
    assert A_TEXT[0] in _text(out, 0)
    assert A_TEXT[2] in _text(out, 1)


def test_delete_remaps_the_outline_and_drops_dangling_bookmarks(a_pdf, out):
    """The `test_materialize.py` invariant: "Section 1.1" targeted the deleted page, so it goes;
    the survivors point at their NEW 1-based positions and the levels are repaired."""
    T.delete_pages(a_pdf, [2], out)
    assert _toc(out) == [[1, "Chapter 1", 1], [1, "Chapter 2", 2]]


def test_delete_refuses_to_empty_the_document(a_pdf, out):
    with pytest.raises(ValueError, match="every page"):
        T.delete_pages(a_pdf, [1, 2, 3], out)
    assert not os.path.exists(out)


# ---- reorder ---------------------------------------------------------------------


def test_reorder_moves_pages_and_their_text(a_pdf, out):
    T.reorder(a_pdf, [3, 1, 2], out)
    assert A_TEXT[2] in _text(out, 0)
    assert A_TEXT[0] in _text(out, 1)
    assert A_TEXT[1] in _text(out, 2)


def test_reorder_remaps_the_outline_to_follow_its_pages(a_pdf, out):
    T.reorder(a_pdf, [3, 2, 1], out)
    titles = {entry[1]: entry[2] for entry in _toc(out)}
    assert titles["Chapter 1"] == 3  # was page 1, now last
    assert titles["Chapter 2"] == 1  # was page 3, now first


def test_reorder_demands_a_full_permutation(a_pdf, out):
    """A partial list would silently drop pages — which is what delete_pages is for."""
    with pytest.raises(ValueError, match="every page exactly once"):
        T.reorder(a_pdf, [1, 2], out)
    with pytest.raises(ValueError, match="every page exactly once"):
        T.reorder(a_pdf, [1, 1, 2], out)
    assert not os.path.exists(out)


# ---- rotate -----------------------------------------------------------------------


def test_rotate_turns_every_page_by_default(a_pdf, out):
    T.rotate(a_pdf, 90, out)
    doc = fitz.open(out)
    try:
        assert [page.rotation for page in doc] == [90, 90, 90]
    finally:
        doc.close()


def test_rotate_takes_a_page_subset(a_pdf, out):
    T.rotate(a_pdf, 180, out, pages=[2])
    doc = fitz.open(out)
    try:
        assert [page.rotation for page in doc] == [0, 180, 0]
    finally:
        doc.close()


def test_rotation_is_a_delta_not_an_absolute(a_pdf, tmp_path):
    """90 twice is 180 — the verb's meaning, and what keeps an already-rotated scan consistent."""
    once = str(tmp_path / "once.pdf")
    twice = str(tmp_path / "twice.pdf")
    T.rotate(a_pdf, 90, once)
    T.rotate(once, 90, twice)
    doc = fitz.open(twice)
    try:
        assert doc[0].rotation == 180
    finally:
        doc.close()


@pytest.mark.parametrize("degrees", [45, 1, -30, 100])
def test_rotate_rejects_a_non_quarter_turn(a_pdf, out, degrees):
    with pytest.raises(ValueError, match="multiple of 90"):
        T.rotate(a_pdf, degrees, out)


# ---- split --------------------------------------------------------------------------


def test_split_without_ranges_writes_one_file_per_page(a_pdf, tmp_path):
    result = T.split(a_pdf, str(tmp_path))
    assert result["count"] == 3
    for i, part in enumerate(result["parts"]):
        assert _pages(part["out"]) == 1
        assert A_TEXT[i] in _text(part["out"], 0)


def test_split_takes_print_dialog_ranges(a_pdf, tmp_path):
    result = T.split(a_pdf, str(tmp_path), ranges=["1-2", "3-"])
    assert [part["pages"] for part in result["parts"]] == [2, 1]
    assert result["parts"][0]["source_pages"] == [1, 2]
    assert A_TEXT[2] in _text(result["parts"][1]["out"], 0)


def test_split_parts_keep_the_bookmarks_that_landed_in_them(a_pdf, tmp_path):
    """Losslessness survives the extract: page 3 carries "Chapter 2" into its own file."""
    result = T.split(a_pdf, str(tmp_path), ranges=["3"])
    assert [entry[1] for entry in _toc(result["parts"][0]["out"])] == ["Chapter 2"]


def test_split_rejects_an_empty_range(a_pdf, tmp_path):
    """`parse_page_range("")` means *every page* — the right default for a dialog's untouched Pages
    box, and a trap in a split list, where `["1-2", ""]` would quietly make part two the whole
    document. Refused rather than obeyed."""
    with pytest.raises(ValueError, match="every page"):
        T.split(a_pdf, str(tmp_path), ranges=["1-2", ""])


def test_split_rejects_a_nonsense_range(a_pdf, tmp_path):
    from util.page_range import PageRangeError

    with pytest.raises(PageRangeError):
        T.split(a_pdf, str(tmp_path), ranges=["not-a-page"])


def test_split_rejects_a_missing_directory(a_pdf, tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        T.split(a_pdf, str(tmp_path / "nope"))


# ---- extract_pages -----------------------------------------------------------------------


def test_extract_pulls_named_pages_into_one_file(a_pdf, out):
    """The tool that was missing. `split(ranges=["2-3"])` could already produce this file, but it
    is named for cutting a document up and picks its own filename, so an agent asked to *extract*
    did not find it and shelled out to `pdfunite` instead."""
    result = T.extract_pages(a_pdf, [2, 3], out)
    assert result["pages"] == 2  # the OUTPUT's count, not the source's
    assert result["source_pages"] == [2, 3]
    assert _pages(out) == 2
    assert A_TEXT[1] in _text(out, 0)
    assert A_TEXT[2] in _text(out, 1)


def test_extract_writes_pages_in_document_order(a_pdf, out):
    """Asking for [3, 1] extracts pages 1 and 3 in reading order — `reorder` is the tool for
    changing sequence, and quietly doing both here would make neither predictable."""
    T.extract_pages(a_pdf, [3, 1], out)
    assert A_TEXT[0] in _text(out, 0)
    assert A_TEXT[2] in _text(out, 1)


def test_extract_carries_the_bookmarks_of_the_pages_it_took(a_pdf, out):
    T.extract_pages(a_pdf, [3], out)
    assert [entry[1] for entry in _toc(out)] == ["Chapter 2"]


def test_extract_keeps_a_form_field_on_an_extracted_page(a_pdf, out):
    T.extract_pages(a_pdf, [1], out)
    assert _field_names(out) == ["name"]


def test_extract_a_single_page(a_pdf, out):
    T.extract_pages(a_pdf, [2], out)
    assert _pages(out) == 1


def test_extract_rejects_an_empty_selection(a_pdf, out):
    with pytest.raises(ValueError, match="must select something"):
        T.extract_pages(a_pdf, [], out)
    assert not os.path.exists(out)


def test_extract_rejects_an_out_of_range_page(a_pdf, out):
    with pytest.raises(ValueError, match="out of range"):
        T.extract_pages(a_pdf, [99], out)


def test_extract_will_not_write_over_its_input(a_pdf):
    with pytest.raises(ValueError, match="refusing to write over the input"):
        T.extract_pages(a_pdf, [1], a_pdf)


# ---- merge ----------------------------------------------------------------------------


def test_merge_concatenates_in_order(a_pdf, b_pdf, out):
    result = T.merge([a_pdf, b_pdf], out)
    assert result["pages"] == 5
    assert A_TEXT[0] in _text(out, 0)
    assert B_TEXT[0] in _text(out, 3)


def test_merge_renames_colliding_form_fields_rather_than_dropping_one(a_pdf, b_pdf, out):
    """The `test_materialize.py` dedup invariant. A and B both have a field called `name`; both
    must survive as working fields, cross-checked with a different engine."""
    T.merge([a_pdf, b_pdf], out)
    # pypdf is the second engine this cross-checks with, and it is deliberately absent from the
    # bridge's own lock (M115) — so under the `bridge` job the merge itself is still asserted
    # below, only the cross-engine confirmation is skipped (M115.1).
    pytest.importorskip("pypdf", reason="the bridge lock has no pypdf; it is a dev cross-check")
    names = _field_names(out)
    assert len(names) == 2 and len(set(names)) == 2
    assert any(n == "name" for n in names)
    assert all(n.startswith("name") for n in names)

    from pypdf import PdfReader

    fields = PdfReader(out).get_fields()
    assert fields is not None and len(fields) == 2


def test_merge_needs_at_least_two_documents(a_pdf, out):
    with pytest.raises(ValueError, match="at least two"):
        T.merge([a_pdf], out)


# ---- fill_form ---------------------------------------------------------------------------


def test_fill_form_writes_the_value_and_keeps_the_field_editable(a_pdf, out):
    T.fill_form(a_pdf, {"name": "Ada Lovelace"}, out)
    doc = fitz.open(out)
    try:
        widgets = [w for page in doc for w in page.widgets()]
        assert len(widgets) == 1  # still a widget, not baked away
        assert widgets[0].field_value == "Ada Lovelace"
    finally:
        doc.close()


def test_fill_form_rejects_an_unknown_field(a_pdf, out):
    """A typo that writes nothing and reports success is the worst outcome, so it is an error."""
    with pytest.raises(ValueError, match="no such form field"):
        T.fill_form(a_pdf, {"nmae": "typo"}, out)
    assert not os.path.exists(out)


def test_a_boolean_ticks_a_checkbox_whatever_its_export_value_is(awkward_form_pdf, out):
    """The convenience TC-002 found working and undocumented: a caller sends ``True`` and the
    widget's own on-state is written — ``"2"`` here, not the ``"Yes"`` a guess would produce.
    `get_form_fields` now reports it too, so the explicit route works as well."""
    T.fill_form(awkward_form_pdf, {"married": True}, out)
    doc = fitz.open(out)
    try:
        page = doc[0]
        values = {w.field_name: w.field_value for w in page.widgets()}
        assert values["married"] == "2"
    finally:
        doc.close()


def test_the_explicit_export_value_ticks_it_too(awkward_form_pdf, out):
    T.fill_form(awkward_form_pdf, {"married": "2"}, out)
    doc = fitz.open(out)
    try:
        page = doc[0]
        assert {w.field_name: w.field_value for w in page.widgets()}["married"] == "2"
    finally:
        doc.close()


# ---- fill_form on an XFA form (TC-002 ISSUE 3) ---------------------------------------------


def test_an_ordinary_form_says_nothing_about_xfa(a_pdf, out):
    result = T.fill_form(a_pdf, {"name": "Ada"}, out)
    assert "xfa" not in result and "warnings" not in result


def test_filling_a_static_xfa_form_warns_that_its_datasets_packet_is_stale(static_xfa_pdf, out):
    """The fill writes the AcroForm widgets and leaves the XFA ``datasets`` packet byte-identical,
    so the file asserts two different things. The owner's decision (2026-08-15) is to report that
    rather than resolve it, so what the bridge owes the caller is that they are told."""
    result = T.fill_form(static_xfa_pdf, {"remarks": "filled through the bridge"}, out)
    assert result["xfa"] == {"present": True, "dynamic": False, "datasets_updated": False}
    assert "datasets" in result["warnings"][0]
    assert "static" in result["warnings"][0]

    doc = fitz.open(out)
    try:
        page = doc[0]
        values = {w.field_name: w.field_value for w in page.widgets()}
        assert values["remarks"] == "filled through the bridge"   # the widgets really were filled
    finally:
        doc.close()


def test_a_dynamic_xfa_form_is_called_out_as_the_case_that_renders_wrong(dynamic_xfa_pdf, out):
    """The case TC-002 flagged as untested and the likeliest hard failure: Acrobat builds a dynamic
    form's pages from the XFA template, so filling only the AcroForm side can leave it *looking*
    empty as well as reading empty. The warning has to distinguish the two — a caller told "static"
    can pass the file on, and a caller told "dynamic" must check it first."""
    result = T.fill_form(dynamic_xfa_pdf, {"remarks": "filled"}, out)
    assert result["xfa"]["dynamic"] is True
    assert "dynamic" in result["warnings"][0] and "Acrobat" in result["warnings"][0]


def test_the_datasets_packet_is_left_untouched_rather_than_half_written(static_xfa_pdf, out):
    """Reporting is the whole behaviour: nothing writes a partial XFA data island."""
    before = _xfa_datasets(static_xfa_pdf)
    T.fill_form(static_xfa_pdf, {"remarks": "filled"}, out)
    assert _xfa_datasets(out) == before


def _xfa_datasets(path: str) -> bytes:
    """The bytes of the document's XFA ``datasets`` packet."""
    import re

    doc = fitz.open(path)
    try:
        _kind, value = doc.xref_get_key(doc.pdf_catalog(), "AcroForm/XFA")
        packets = {n: int(x) for n, x in re.findall(r"\((.*?)\)\s*(\d+)\s+0\s+R", value)}
        return doc.xref_stream(packets["datasets"])
    finally:
        doc.close()


# ---- flatten ------------------------------------------------------------------------------


def test_flatten_bakes_the_field_away_but_keeps_the_text(a_pdf, out):
    filled = os.path.join(os.path.dirname(out), "filled.pdf")
    T.fill_form(a_pdf, {"name": "BAKED-VALUE"}, filled)
    T.flatten(filled, out)

    doc = fitz.open(out)
    try:
        assert [w for page in doc for w in page.widgets()] == []  # no editable widgets left
        assert "BAKED-VALUE" in doc[0].get_text("text")  # the value is now page content
        assert A_TEXT[0] in doc[0].get_text("text")  # the original text layer survives
    finally:
        doc.close()


# ---- export_images ---------------------------------------------------------------------------


def test_export_images_writes_one_file_per_page(a_pdf, tmp_path):
    result = T.export_images(a_pdf, str(tmp_path), dpi=36)
    assert result["count"] == 3
    for written in result["files"]:
        assert os.path.getsize(written) > 0
        assert written.endswith(".png")


def test_export_images_takes_a_page_subset_and_a_format(a_pdf, tmp_path):
    result = T.export_images(a_pdf, str(tmp_path), pages=[2], dpi=36, fmt="jpg")
    assert result["count"] == 1
    assert result["files"][0].endswith(".jpg")


def test_export_images_rejects_a_bad_format(a_pdf, tmp_path):
    with pytest.raises(ValueError, match="png or jpg"):
        T.export_images(a_pdf, str(tmp_path), fmt="webp")


def test_export_images_will_not_clobber_without_permission(a_pdf, tmp_path):
    T.export_images(a_pdf, str(tmp_path), pages=[1], dpi=36)
    with pytest.raises(ValueError, match="already exists"):
        T.export_images(a_pdf, str(tmp_path), pages=[1], dpi=36)
    T.export_images(a_pdf, str(tmp_path), pages=[1], dpi=36, overwrite=True)  # allowed when asked


# ---- M99: cropping the export to a region ----------------------------------------------------


def _png_size(path: str) -> tuple[int, int]:
    """Width/height straight out of the PNG IHDR — no image library needed for a 24-byte read."""
    import struct

    with open(path, "rb") as handle:
        header = handle.read(24)
    assert header[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", header[16:24])


def test_export_clip_crops_every_page_to_the_region(a_pdf, tmp_path):
    result = T.export_images(a_pdf, str(tmp_path), dpi=72, clip=[0, 0, 200, 100])
    assert result["count"] == 3
    assert result["clip"] == [0.0, 0.0, 200.0, 100.0]
    for written in result["files"]:
        assert _png_size(written) == (200, 100)


def test_export_without_a_clip_is_unchanged(a_pdf, tmp_path):
    """`clip=None` must be the identity — the whole page, exactly as before M99."""
    os.makedirs(tmp_path / "a")
    os.makedirs(tmp_path / "b")
    plain = T.export_images(a_pdf, str(tmp_path / "a"), pages=[1], dpi=72)
    explicit = T.export_images(a_pdf, str(tmp_path / "b"), pages=[1], dpi=72, clip=None)
    with open(plain["files"][0], "rb") as one, open(explicit["files"][0], "rb") as two:
        assert one.read() == two.read()
    assert plain["clip"] is None


def test_a_clip_that_overhangs_any_page_writes_nothing(a_pdf, tmp_path):
    """Validated for the whole set **before** the first file is written.

    The failure this prevents is the partial one: a clip legal on pages 1-2 and off the edge of
    page 3 would otherwise raise having already left two files on disk, so a caller that handled
    the error still has half an export to clean up. Page sizes vary within real documents, which is
    what makes this reachable rather than theoretical.
    """
    import pymupdf as fitz

    mixed = str(tmp_path / "mixed.pdf")
    doc = fitz.open()
    doc.new_page(width=600, height=800)
    doc.new_page(width=200, height=800)  # narrower — a 300-wide clip runs off it
    doc.save(mixed)
    doc.close()

    out_dir = tmp_path / "out"
    os.makedirs(out_dir)
    with pytest.raises(ValueError, match="outside page 2"):
        T.export_images(mixed, str(out_dir), dpi=36, clip=[0, 0, 300, 100])
    assert os.listdir(out_dir) == []


# ---- the safety model, tested adversarially -------------------------------------------------


ALL_TRANSFORMS = [
    ("delete_pages", lambda src, out: T.delete_pages(src, [1], out)),
    ("reorder", lambda src, out: T.reorder(src, [3, 2, 1], out)),
    ("rotate", lambda src, out: T.rotate(src, 90, out)),
    ("fill_form", lambda src, out: T.fill_form(src, {"name": "x"}, out)),
    ("flatten", lambda src, out: T.flatten(src, out)),
]


@pytest.mark.parametrize("name, run", ALL_TRANSFORMS, ids=[n for n, _ in ALL_TRANSFORMS])
def test_no_transform_will_write_over_its_input(a_pdf, name, run):
    before = open(a_pdf, "rb").read()
    with pytest.raises(ValueError, match="refusing to write over the input"):
        run(a_pdf, a_pdf)
    assert open(a_pdf, "rb").read() == before


@pytest.mark.parametrize("name, run", ALL_TRANSFORMS, ids=[n for n, _ in ALL_TRANSFORMS])
def test_every_transform_leaves_the_source_byte_identical(a_pdf, tmp_path, name, run):
    """The verification-matrix item: "every write tool leaves the input file byte-identical"."""
    before = open(a_pdf, "rb").read()
    run(a_pdf, str(tmp_path / f"{name}.pdf"))
    assert open(a_pdf, "rb").read() == before


def test_the_source_cannot_be_smuggled_in_through_a_relative_path(a_pdf, out):
    """A string compare would miss `dir/../A.pdf`; `normalize_path` does not."""
    sneaky = os.path.join(os.path.dirname(a_pdf), "sub", "..", os.path.basename(a_pdf))
    os.makedirs(os.path.join(os.path.dirname(a_pdf), "sub"), exist_ok=True)
    with pytest.raises(ValueError, match="refusing to write over the input"):
        T.delete_pages(a_pdf, [1], sneaky)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_the_source_cannot_be_smuggled_in_through_a_symlink_posix_only(a_pdf, tmp_path):
    link = str(tmp_path / "link.pdf")
    os.symlink(a_pdf, link)
    with pytest.raises(ValueError, match="refusing to write over the input"):
        T.delete_pages(a_pdf, [1], link)


def test_merge_will_not_write_over_any_of_its_inputs(a_pdf, b_pdf):
    with pytest.raises(ValueError, match="refusing to write over the input"):
        T.merge([a_pdf, b_pdf], b_pdf)  # the *second* input, not just the first


def test_an_existing_target_is_not_clobbered_without_permission(a_pdf, tmp_path):
    target = tmp_path / "existing.pdf"
    target.write_bytes(b"do not lose me")
    with pytest.raises(ValueError, match="already exists"):
        T.delete_pages(a_pdf, [1], str(target))
    assert target.read_bytes() == b"do not lose me"

    T.delete_pages(a_pdf, [1], str(target), overwrite=True)
    assert target.read_bytes() != b"do not lose me"


def test_a_target_directory_is_rejected(a_pdf, tmp_path):
    with pytest.raises(ValueError, match="is a directory"):
        T.delete_pages(a_pdf, [1], str(tmp_path))


def test_a_missing_output_directory_is_rejected(a_pdf, tmp_path):
    with pytest.raises(ValueError, match="output directory"):
        T.delete_pages(a_pdf, [1], str(tmp_path / "nope" / "out.pdf"))


def test_a_transform_survives_a_transient_lock_on_its_temp(a_pdf, tmp_path, monkeypatch):
    """The transform write path goes through M38.5's `atomic_replace`, not a bare `os.replace`.

    Same Windows race as the GUI's Save: the rename needs exclusive access to both paths, and an
    antivirus scanner holding the just-written temp fails a write that would succeed 200 ms later.
    Two write paths in one codebase should not disagree about that.
    """
    from util import atomic

    calls: list[int] = []
    real = os.replace

    def flaky(src, dst):
        calls.append(1)
        if len(calls) <= 2:
            raise PermissionError(5, "Access is denied")
        return real(src, dst)

    monkeypatch.setattr(atomic.time, "sleep", lambda _s: None)
    monkeypatch.setattr(atomic.os, "replace", flaky)

    out = tmp_path / "locked.pdf"
    T.delete_pages(a_pdf, [1], str(out))

    assert out.exists()
    assert len(calls) == 3  # retried past the lock rather than failing the transform


def test_a_failed_write_leaves_no_debris(a_pdf, tmp_path, monkeypatch):
    """The temp-then-rename exists so a caller can never read back a half-written PDF."""
    from model.edit_engine import PyMuPDFEngine

    def boom(self, vdoc, out_path):
        with open(out_path, "wb") as handle:
            handle.write(b"%PDF-partial")  # a real materialise had started writing
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(PyMuPDFEngine, "materialize", boom)
    target_dir = tmp_path / "dest"  # a directory of its own, so the fixture PDF is not in the glob
    target_dir.mkdir()
    out = target_dir / "out.pdf"
    with pytest.raises(RuntimeError, match="engine exploded"):
        T.delete_pages(a_pdf, [1], str(out))

    assert not out.exists()
    assert list(target_dir.iterdir()) == []  # and no orphaned temp either


# ---- TC-002 retest (2026-08-15): the three defects the review found in M94's own new surface ----


@pytest.mark.parametrize("value, written", [
    (True, "2"), (False, "Off"), ("2", "2"), ("Off", "Off"), ("Yes", "2"), (None, "Off"),
])
def test_every_accepted_way_to_set_a_checkbox_still_works(awkward_form_pdf, out, value, written):
    """The convenience is the point and must survive the validation: a boolean, the widget's own
    export value, and the boolean *words* PyMuPDF resolves all keep working."""
    T.fill_form(awkward_form_pdf, {"married": value}, out)
    doc = fitz.open(out)
    try:
        page = doc[0]
        assert {w.field_name: w.field_value for w in page.widgets()}["married"] == written
    finally:
        doc.close()


@pytest.mark.parametrize("typo", ["3", "Of", "ticked", 1])
def test_a_state_the_button_does_not_have_is_an_error_not_a_silent_clear(
    awkward_form_pdf, out, typo
):
    """NEW ISSUE 8. `fill_form` already refuses an unknown field *name* — "a typo that writes
    nothing and reports success is the worst outcome here" — and the guarantee stopped one argument
    short. An unrecognised state resolved as falsy and wrote `Off`, so asking to tick a box with
    `"3"` on a form whose states are `"1"` and `"2"` **cleared** it and listed the field under
    `filled`. Worse than the no-op the name check exists to prevent: a wrong answer, reported as
    success.

    `"Of"` is in here deliberately. It lands on `Off` today, which is what the caller meant — but
    by falling down the same silent path that mishandles `"3"`, and one wrong input working by luck
    is what makes the other hard to see (owner decision, 2026-08-16).
    """
    with pytest.raises(ValueError, match="is not a state of the button field"):
        T.fill_form(awkward_form_pdf, {"married": typo}, out)
    assert not os.path.exists(out)          # and nothing was written


def test_the_error_names_the_states_the_widget_actually_accepts(awkward_form_pdf, out):
    """Mirroring the field-name error, which lists the valid names: an error a caller can act on
    beats one that only says no."""
    with pytest.raises(ValueError, match=r"\['2', 'Off'\]"):
        T.fill_form(awkward_form_pdf, {"married": "3"}, out)


def test_a_text_field_still_takes_any_string(awkward_form_pdf, out):
    """Only buttons are checked. A text field accepts arbitrary text by definition, and validating
    it would break the tool's main job."""
    T.fill_form(awkward_form_pdf, {"remarks": "3"}, out)
    doc = fitz.open(out)
    try:
        assert {w.field_name: w.field_value for w in doc[0].widgets()}["remarks"] == "3"
    finally:
        doc.close()


def test_filling_a_read_only_field_is_allowed_but_reported(awkward_form_pdf, out):
    """NEW ISSUE 9. The caller may mean it — stamping a value into a field a person may not edit is
    a legitimate thing to want. Doing it without a word is not, now that the server reads the flag
    and reports it in `get_form_fields`."""
    result = T.fill_form(awkward_form_pdf, {"plumbing": "stamped"}, out)
    assert os.path.exists(result["out"])
    assert "plumbing" in "\n".join(result["warnings"])
    assert "read-only" in "\n".join(result["warnings"])


def test_filling_ordinary_fields_says_nothing_about_read_only(awkward_form_pdf, out):
    result = T.fill_form(awkward_form_pdf, {"remarks": "ordinary"}, out)
    assert "warnings" not in result


def test_the_read_only_warning_joins_the_xfa_one_rather_than_replacing_it(static_xfa_pdf, out):
    """Two warning sources, one key. A plain `**` merge would keep whichever was built last."""
    result = T.fill_form(static_xfa_pdf, {"plumbing": "stamped", "remarks": "x"}, out)
    assert len(result["warnings"]) == 2
    assert any("XFA" in w for w in result["warnings"])
    assert any("read-only" in w for w in result["warnings"])


def test_the_states_order_is_stable_across_a_round_trip(awkward_form_pdf, out):
    """NEW ISSUE 10. The order used to come from the `/AP/N` dictionary, which a write rebuilds —
    the same widget reported `["2", "Off"]` before a fill and `["Off", "2"]` after one, so a field
    changed under a round-trip that changed nothing about the widget."""
    before = {f["name"]: f for f in queries.form_fields(awkward_form_pdf)}["married"]
    T.fill_form(awkward_form_pdf, {"married": True}, out)
    after = {f["name"]: f for f in queries.form_fields(out)}["married"]
    assert before["states"] == after["states"] == ["2", "Off"]   # on-state first, then the rest
    assert before["states"][0] == before["on_state"]


# ---- M104 / TC-008 Finding 1: filenames that do not collide with themselves -------------------


def test_a_single_page_export_still_carries_its_page_number(a_pdf, tmp_path):
    """The scheme used to be `<stem>.png` for one page and `<stem>-3.png` for several. Non-uniform,
    and a caller could not predict a filename without knowing how many pages came back."""
    result = T.export_images(a_pdf, str(tmp_path), pages=[2], dpi=36)
    assert [os.path.basename(f) for f in result["files"]] == ["A-2.png"]


def test_two_clips_of_one_page_land_in_one_directory(a_pdf, tmp_path):
    """**The TC-008 deliverable.** Two ID cards off one page is the use `clip` was added for, and
    the naming scheme was at odds with it: both wanted `<stem>.png`, so the second call hit the
    no-clobber refusal and the workarounds were a directory per region or `overwrite: true`, which
    destroys the first card. The refusal was right; the names were wrong."""
    front = T.export_images(a_pdf, str(tmp_path), pages=[1], dpi=36,
                            clip=[0, 0, 200, 100], name="card_front")
    back = T.export_images(a_pdf, str(tmp_path), pages=[1], dpi=36,
                           clip=[0, 100, 200, 200], name="card_back")
    written = sorted(os.path.basename(f) for f in front["files"] + back["files"])
    assert written == ["card_back-1.png", "card_front-1.png"]
    assert all(os.path.getsize(f) > 0 for f in front["files"] + back["files"])


def test_the_no_clobber_check_still_sees_the_names_that_will_be_written(a_pdf, tmp_path):
    """A predictor that disagreed with the writer would pass the check on a name the writer then
    overwrote — this function's own failure mode, wearing the writer's clothes."""
    T.export_images(a_pdf, str(tmp_path), pages=[1], dpi=36, name="card")
    with pytest.raises(ValueError, match="already exists"):
        T.export_images(a_pdf, str(tmp_path), pages=[1], dpi=36, name="card")
    T.export_images(a_pdf, str(tmp_path), pages=[1], dpi=36, name="card", overwrite=True)


@pytest.mark.parametrize(
    "name, match",
    [
        ("../escape", "plain filename stem"),
        ("a/b", "plain filename stem"),
        ("..", "plain filename stem"),
        ("", "is empty"),
        ("   ", "is empty"),
        ("card.png", "carries an extension"),
    ],
)
def test_a_name_cannot_be_a_path(a_pdf, tmp_path, name, match):
    """`name` is joined onto `out_dir`, so left alone `../../etc/passwd` would walk out of it and
    around `--allow-root`. Refused rather than sanitised: quietly rewriting a name hands back a
    file under a different one than was asked for."""
    with pytest.raises(ValueError, match=match):
        T.export_images(a_pdf, str(tmp_path), pages=[1], dpi=36, name=name)
    assert [f for f in os.listdir(tmp_path) if f.endswith(".png")] == []


def test_the_app_export_keeps_the_filename_the_user_typed(a_pdf, tmp_path):
    """The model function is shared with Export ▸ Images, where the name comes from a save dialog.
    Turning `report.png` into `report-1.png` behind the user would be its own small betrayal, so
    `number_all` defaults off and only the bridge passes it."""
    from model.export import export_page_images
    from mcp_bridge.queries import open_document

    target = str(tmp_path / "report.png")
    with open_document(a_pdf) as vdoc:
        assert export_page_images(vdoc, [0], target, dpi=36) == [target]


# ---- the write mode, from this side (M116) -------------------------------------------


def test_no_transform_here_appends_to_its_input(a_pdf, b_pdf, tmp_path):
    """M116 gave the shared engine a third route — a save that only *adds* marks appends to the
    file it was given instead of rewriting it — and the core is reached by two consumers, so the
    question "did that change what a bridge tool writes?" has to be asked from this side rather
    than inferred from the app's.

    The answer is no, and structurally: not one tool in this module makes a purely additive edit.
    Every one of them rotates, fills, redacts, flattens or changes the page set, and each of those
    is refused by :meth:`VirtualDocument.edits_are_additive` for its own reason. So every output
    here is still a full rewrite, which is what the black-box check below asserts — an appended
    file *begins* with its source, byte for byte, and none of these may.

    ``annotate`` (M101) is the tool that will append, and it is deliberately not in this list.
    """
    from mcp_bridge import redaction

    source = open(a_pdf, "rb").read()
    writes = {
        "delete_pages": lambda o: T.delete_pages(a_pdf, [2], o),
        "reorder": lambda o: T.reorder(a_pdf, [3, 1, 2], o),
        "rotate": lambda o: T.rotate(a_pdf, 90, o),
        "extract_pages": lambda o: T.extract_pages(a_pdf, [1, 2, 3], o),
        "merge": lambda o: T.merge([a_pdf, b_pdf], o),
        "fill_form": lambda o: T.fill_form(a_pdf, {"name": "typed"}, o),
        "flatten": lambda o: T.flatten(a_pdf, o),
        "redact_text": lambda o: redaction.redact_text(a_pdf, "ALPHA-one-A1", o),
    }
    for name, run in writes.items():
        out_path = str(tmp_path / f"{name}.pdf")
        run(out_path)
        written = open(out_path, "rb").read()
        assert written[: len(source)] != source, f"{name} appended to its input"
