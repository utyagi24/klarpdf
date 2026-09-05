"""M101 — writing text markup from the bridge, and reading back everyone's.

Three things here are worth more than the rest, because each pins a decision that would fail
silently if it drifted:

* **Boxes are rotation-invariant.** `get_annotations` output is meant to go straight into
  `redact_regions`, and both were measured to work in unrotated page points at every `/Rotate`.
  Nothing in the code converts anything, so the invariant is held by PyMuPDF's behaviour rather
  than by ours — which is exactly the kind of thing that changes under you (M99.1 was a clip on the
  wrong side of this same rotation).
* **A repeat call merges instead of stacking**, so retrying is safe and a colour filter cannot act
  on the same passage twice.
* **The read side sees a sticky note.** It is unmodeled (§M83), so a listing built on
  `parse_annotation` would drop it — and it is precisely how a reviewer's comment often arrives.

The M113 section at the foot covers the TC-012/TC-013 follow-ups. The one worth calling out is
`test_a_rerun_does_not_duplicate_the_note`: the M101 re-run test asserted only that no *mark* was
added, which is the weaker half of the claim the docs make, and a note was duplicating under it the
whole time.
"""

from __future__ import annotations

import json
import os

import pymupdf as fitz
import pytest

from klarpdf.mcp_bridge import annotations, redaction
from klarpdf.model.markup_palette import HIGHLIGHT_COLORS, TEXT_LINE_COLORS

NAME = "Alice Smith"
ACCOUNT = "220885-1063303"
ORANGE = dict(HIGHLIGHT_COLORS)["Orange"]
YELLOW = dict(HIGHLIGHT_COLORS)["Yellow"]
LINE_RED = dict(TEXT_LINE_COLORS)["Red"]


@pytest.fixture
def doc_path(tmp_path) -> str:
    path = str(tmp_path / "in.pdf")
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 115), f"{NAME} lives at 12 Elm Street", fontsize=11)
    page.insert_text((72, 145), f"Account {ACCOUNT} opened in 2020", fontsize=11)
    doc.new_page(width=612, height=792).insert_text((72, 115), "Second page text", fontsize=11)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def out(tmp_path) -> str:
    return str(tmp_path / "out.pdf")


def _find(path: str, needle: str, page: int = 0) -> list[list[float]]:
    """The boxes `search` would hand back for `needle` — the documented way to get coordinates."""
    with fitz.open(path) as doc:
        return [[round(v, 2) for v in r] for r in doc[page].search_for(needle)]


def _mark(path, needle, out, **extra):
    mark = {"page": 1, "boxes": _find(path, needle), **extra}
    return annotations.annotate(path, [mark], out)


# ---- writing -----------------------------------------------------------------


def test_highlight_lands_where_search_said(doc_path, out):
    boxes = _find(doc_path, NAME)
    result = annotations.annotate(
        doc_path, [{"type": "highlight", "page": 1, "boxes": boxes}], out
    )

    assert result["marks_requested"] == 1
    assert result["marks_added"] == 1
    assert result["pages_annotated"] == [1]
    (written,) = result["annotations"]
    assert written["type"] == "highlight"
    assert written["boxes"][0] == pytest.approx(boxes[0], abs=0.05)
    assert written["mine"] is True and written["editable"] is True


def test_the_source_is_never_touched(doc_path, out):
    before = open(doc_path, "rb").read()
    _mark(doc_path, NAME, out, type="highlight")
    assert open(doc_path, "rb").read() == before
    assert os.path.exists(out)


def test_out_may_not_be_the_input(doc_path):
    with pytest.raises(ValueError, match="refusing to write over the input"):
        _mark(doc_path, NAME, doc_path, type="highlight")


def test_every_type_writes_and_reads_back(doc_path, tmp_path):
    for kind in ("highlight", "underline", "strikeout"):
        target = str(tmp_path / f"{kind}.pdf")
        result = _mark(doc_path, NAME, target, type=kind)
        assert [a["type"] for a in result["annotations"]] == [kind]


def test_a_note_rides_its_mark(doc_path, out):
    result = _mark(doc_path, NAME, out, type="highlight", note="PII: customer name")
    (written,) = result["annotations"]
    assert written["note"] == "PII: customer name"

    # …and reads back off the file itself, not just out of the write's own reply.
    reread = annotations.get_annotations(out)
    assert reread["annotations"][0]["note"] == "PII: customer name"


def test_a_note_without_a_type_creates_a_highlight(doc_path, out):
    """The app's own rule for a note dropped on unmarked text (`resolve_note_host`)."""
    result = _mark(doc_path, NAME, out, note="needs checking")
    (written,) = result["annotations"]
    assert written["type"] == "highlight"
    assert written["note"] == "needs checking"


def test_note_text_is_not_body_text(doc_path, out):
    """A note must never become findable text — it is a remark *about* the page (M81)."""
    _mark(doc_path, NAME, out, type="highlight", note="ZZQQXX-unique-note-token")
    with fitz.open(out) as doc:
        assert "ZZQQXX" not in doc[0].get_text("text")
        assert doc[0].search_for("ZZQQXX-unique-note-token") == []


# ---- colour ------------------------------------------------------------------


def test_a_palette_name_is_the_apps_exact_swatch(doc_path, out):
    """The guarantee the shared palette module exists for: the agent's orange *is* the picker's."""
    result = _mark(doc_path, NAME, out, type="highlight", color="orange")
    (written,) = result["annotations"]
    assert written["color"] == pytest.approx(list(ORANGE), abs=0.005)
    assert written["color_name"] == "Orange"
    assert written["color_exact"] is True


def test_a_name_outside_the_type_palette_is_an_error(doc_path, out):
    """There is no orange line: a name is resolved against the type's own palette (M106's rule)."""
    with pytest.raises(ValueError, match="not an underline colour"):
        _mark(doc_path, NAME, out, type="underline", color="orange")


def test_the_error_reads_as_english_for_every_type(doc_path, tmp_path):
    """M113.6: one format string serves all three types, and two of them take 'a'."""
    for kind, article in (("highlight", "a"), ("underline", "an"), ("strikeout", "a")):
        with pytest.raises(ValueError, match=f"is not {article} {kind} colour"):
            _mark(doc_path, NAME, str(tmp_path / f"{kind}.pdf"), type=kind, color="chartreuse")


def test_the_error_names_what_was_available(doc_path, out):
    with pytest.raises(ValueError, match="Red, Blue, Green, Black"):
        _mark(doc_path, NAME, out, type="strikeout", color="chartreuse")


def test_raw_rgb_is_accepted(doc_path, out):
    result = _mark(doc_path, NAME, out, type="highlight", color=[0.2, 0.4, 0.6])
    assert result["annotations"][0]["color"] == pytest.approx([0.2, 0.4, 0.6], abs=0.005)


def test_rgb_out_of_range_says_which_scale(doc_path, out):
    with pytest.raises(ValueError, match="0..1, not 0..255"):
        _mark(doc_path, NAME, out, type="highlight", color=[255, 128, 0])


def test_the_default_colour_is_the_descriptor_default(doc_path, out):
    result = _mark(doc_path, NAME, out, type="highlight")
    assert result["annotations"][0]["color"] == pytest.approx(list(YELLOW), abs=0.005)


# ---- merging -----------------------------------------------------------------


def test_running_the_same_call_twice_does_not_stack(doc_path, tmp_path):
    """The invariant that makes a retry safe — and stops a colour filter acting twice on one span."""
    first = str(tmp_path / "first.pdf")
    second = str(tmp_path / "second.pdf")
    boxes = _find(doc_path, NAME)
    mark = [{"type": "highlight", "page": 1, "boxes": boxes, "color": "orange"}]

    annotations.annotate(doc_path, mark, first)
    again = annotations.annotate(first, mark, second)

    assert again["marks_added"] == 0
    assert again["annotations"] and len(again["annotations"]) == 1


def test_a_different_colour_takes_the_span_over(doc_path, tmp_path):
    first = str(tmp_path / "first.pdf")
    second = str(tmp_path / "second.pdf")
    boxes = _find(doc_path, NAME)

    annotations.annotate(
        doc_path, [{"type": "highlight", "page": 1, "boxes": boxes, "color": "yellow"}], first
    )
    result = annotations.annotate(
        first, [{"type": "highlight", "page": 1, "boxes": boxes, "color": "orange"}], second
    )

    assert len(result["annotations"]) == 1
    assert result["annotations"][0]["color_name"] == "Orange"


def test_different_types_do_not_merge(doc_path, tmp_path):
    first = str(tmp_path / "first.pdf")
    second = str(tmp_path / "second.pdf")
    boxes = _find(doc_path, NAME)

    annotations.annotate(doc_path, [{"type": "highlight", "page": 1, "boxes": boxes}], first)
    result = annotations.annotate(
        first, [{"type": "underline", "page": 1, "boxes": boxes}], second
    )

    # The written file holds both — asked of the file, because since M113.2 the reply echoes only
    # the marks *this* call touched, and the highlight was laid by the previous one.
    on_file = annotations.get_annotations(second)["annotations"]
    assert sorted(a["type"] for a in on_file) == ["highlight", "underline"]
    assert [a["type"] for a in result["annotations"]] == ["underline"]


def test_a_merge_carries_the_absorbed_note(doc_path, tmp_path):
    """M81.2's rule at the bridge: a call that deleted nothing must not destroy typed text."""
    first = str(tmp_path / "first.pdf")
    second = str(tmp_path / "second.pdf")
    boxes = _find(doc_path, NAME)

    annotations.annotate(
        doc_path,
        [{"type": "highlight", "page": 1, "boxes": boxes, "note": "first note"}],
        first,
    )
    result = annotations.annotate(
        first,
        [{"type": "highlight", "page": 1, "boxes": boxes, "note": "second note"}],
        second,
    )

    (written,) = result["annotations"]
    assert "first note" in written["note"]
    assert "second note" in written["note"]


def test_several_marks_in_one_call_write_one_file(doc_path, out):
    result = annotations.annotate(
        doc_path,
        [
            {"type": "highlight", "page": 1, "boxes": _find(doc_path, NAME), "color": "orange"},
            {"type": "underline", "page": 1, "boxes": _find(doc_path, ACCOUNT)},
            {"type": "strikeout", "page": 2, "boxes": _find(doc_path, "Second", page=1)},
        ],
        out,
    )
    assert result["marks_added"] == 3
    assert result["pages_annotated"] == [1, 2]


# ---- reading -----------------------------------------------------------------


def test_a_foreign_mark_is_reported_and_flagged_not_ours(doc_path, out):
    with fitz.open(doc_path) as doc:
        page = doc[0]                        # held: an annot on a freed page raises (or crashes)
        annot = page.add_highlight_annot(fitz.Rect(*_find(doc_path, NAME)[0]))
        annot.set_info(title="A. Reviewer", content="from Acrobat")
        annot.update()
        doc.save(out)

    listed = annotations.get_annotations(out)
    (found,) = listed["annotations"]
    assert found["mine"] is False
    assert found["author"] == "A. Reviewer"
    assert found["note"] == "from Acrobat"
    assert found["editable"] is True          # a highlight is a type the model can adopt (M68)


def test_a_sticky_note_is_not_invisible(doc_path, out):
    """The §M83 gap: unmodeled, so a listing built on `parse_annotation` would drop it — and it is
    how a reviewer's comment often arrives from Edge or Acrobat."""
    with fitz.open(doc_path) as doc:
        page = doc[0]
        sticky = page.add_text_annot(fitz.Point(300, 300), "please check this figure")
        sticky.set_info(title="A. Reviewer", content="please check this figure")
        sticky.update()
        doc.save(out)

    listed = annotations.get_annotations(out)
    (found,) = listed["annotations"]
    assert found["type"] == "text"
    assert found["note"] == "please check this figure"
    assert found["editable"] is False         # displayed, but not editable in place
    assert found["mine"] is False


def test_form_widgets_are_not_listed(tmp_path):
    """`get_form_fields` reports those properly; a rect and a colour would be a worse answer."""
    path = str(tmp_path / "form.pdf")
    doc = fitz.open()
    page = doc.new_page()
    widget = fitz.Widget()
    widget.field_name = "name"
    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.rect = fitz.Rect(72, 100, 300, 120)
    page.add_widget(widget)
    doc.save(path)
    doc.close()

    assert annotations.get_annotations(path)["count"] == 0


def test_pages_narrows_the_listing(doc_path, tmp_path):
    out = str(tmp_path / "both.pdf")
    annotations.annotate(
        doc_path,
        [
            {"type": "highlight", "page": 1, "boxes": _find(doc_path, NAME)},
            {"type": "highlight", "page": 2, "boxes": _find(doc_path, "Second", page=1)},
        ],
        out,
    )
    assert annotations.get_annotations(out)["count"] == 2
    only_two = annotations.get_annotations(out, [2])
    assert only_two["count"] == 1
    assert only_two["annotations"][0]["page"] == 2
    assert only_two["pages_scanned"] == [2]


def test_the_listing_is_capped(doc_path, out):
    with fitz.open(doc_path) as doc:
        page = doc[0]
        for i in range(12):
            page.add_highlight_annot(fitz.Rect(72, 100 + i * 10, 200, 108 + i * 10)).update()
        doc.save(out)

    capped = annotations.get_annotations(out, max_annotations=5)
    assert capped["count"] == 5
    assert capped["more_available"] is True
    assert capped["total_annotations"] == 12
    assert capped["warnings"]


def test_a_wrapped_mark_keeps_one_box_per_line(tmp_path):
    """Text markup stores a quad per line; all of them belong to the one mark."""
    path = str(tmp_path / "wrapped.pdf")
    doc = fitz.open()
    page = doc.new_page()
    annot = page.add_highlight_annot(
        [fitz.Rect(72, 100, 300, 115), fitz.Rect(72, 118, 200, 133)]
    )
    annot.update()
    doc.save(path)
    doc.close()

    (found,) = annotations.get_annotations(path)["annotations"]
    assert len(found["boxes"]) == 2


def test_boxes_come_from_quads_not_the_padded_rect(tmp_path):
    """`/Rect` is inflated a few points on every side; a redaction built from it would over-cover."""
    path = str(tmp_path / "padded.pdf")
    box = fitz.Rect(72, 100, 200, 120)
    doc = fitz.open()
    page = doc.new_page()
    page.add_highlight_annot(box).update()
    doc.save(path)
    doc.close()

    with fitz.open(path) as check:
        # Both the page and the annots generator must stay referenced while the annot is read —
        # `next(doc[0].annots()).rect` frees the page under the annotation and segfaults.
        page = check[0]
        raw = list(page.annots())
        raw_rect = tuple(raw[0].rect)
    assert raw_rect[0] < box.x0 - 1          # the padding this test exists to avoid reporting

    (found,) = annotations.get_annotations(path)["annotations"]
    assert found["boxes"][0] == pytest.approx(list(box), abs=0.05)


# ---- the invariant the redaction hand-off rests on ---------------------------


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_boxes_are_reported_unrotated_at_every_rotation(tmp_path, rotation):
    """`search`, `redact_regions` and `clip` all work unrotated; annotations must agree.

    Nothing in `annotations.py` converts anything — PyMuPDF stores and reports annotation geometry
    unrotated whatever `/Rotate` says. That is the whole reason the hand-off needs no arithmetic,
    so it is asserted rather than assumed.
    """
    path = str(tmp_path / f"rot{rotation}.pdf")
    box = fitz.Rect(72, 100, 200, 120)
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.add_highlight_annot(box).update()
    page.set_rotation(rotation)
    doc.save(path)
    doc.close()

    (found,) = annotations.get_annotations(path)["annotations"]
    assert found["boxes"][0] == pytest.approx(list(box), abs=0.05)


def test_get_annotations_output_redacts_without_reshaping(doc_path, tmp_path):
    """The composition that replaced `redact_annotated`: read → filter on colour → redact.

    If this ever needs the boxes adjusted on the way through, the two tools have drifted apart and
    the milestone's central claim is false.
    """
    marked = str(tmp_path / "marked.pdf")
    final = str(tmp_path / "final.pdf")
    annotations.annotate(
        doc_path,
        [
            {"type": "highlight", "page": 1, "boxes": _find(doc_path, NAME), "color": "orange"},
            {"type": "highlight", "page": 1, "boxes": _find(doc_path, ACCOUNT), "color": "yellow"},
        ],
        marked,
    )

    listed = annotations.get_annotations(marked)
    regions = [
        {"page": a["page"], "boxes": a["boxes"]}
        for a in listed["annotations"]
        if a["color_name"] == "Orange"
    ]
    assert len(regions) == 1

    result = redaction.redact_regions(marked, regions, final)

    with fitz.open(final) as doc:
        text = doc[0].get_text("text")
    assert NAME not in text          # the orange mark's text is gone…
    assert ACCOUNT in text           # …and the yellow one's is untouched
    assert result["verified_text"]


# ---- input validation --------------------------------------------------------


def test_an_unknown_type_names_the_three(doc_path, out):
    with pytest.raises(ValueError, match="highlight, strikeout, underline"):
        _mark(doc_path, NAME, out, type="rect")


def test_no_marks_is_an_error(doc_path, out):
    with pytest.raises(ValueError, match="must write something"):
        annotations.annotate(doc_path, [], out)


def test_a_mark_needs_geometry(doc_path, out):
    with pytest.raises(ValueError, match="needs 'box' or 'boxes'"):
        annotations.annotate(doc_path, [{"type": "highlight", "page": 1}], out)


def test_box_and_boxes_together_is_an_error(doc_path, out):
    with pytest.raises(ValueError, match="not both"):
        annotations.annotate(
            doc_path,
            [{"page": 1, "box": [1, 1, 2, 2], "boxes": [[1, 1, 2, 2]]}],
            out,
        )


def test_an_inverted_box_is_refused(doc_path, out):
    with pytest.raises(ValueError, match="empty or inverted"):
        annotations.annotate(doc_path, [{"page": 1, "box": [200, 120, 72, 100]}], out)


def test_a_page_out_of_range_is_refused(doc_path, out):
    with pytest.raises(ValueError):
        annotations.annotate(doc_path, [{"page": 99, "box": [72, 100, 200, 120]}], out)


# ---- M113: what TC-012 and TC-013 found -------------------------------------


def test_a_rerun_does_not_duplicate_the_note(doc_path, tmp_path):
    """M113.1. The docs promise a re-run gives "a file identical in content to the first run's";
    it gave one whose note read its own text twice, then three times.

    `merge_markup` carries an absorbed mark's note onto the survivor (M81.2) and `_attach_note` then
    added this call's note on top. Both are right alone, and nothing distinguished "a new comment,
    keep the old" from "the same comment again".
    """
    first, second, third = (str(tmp_path / f"{n}.pdf") for n in ("first", "second", "third"))
    mark = [{"type": "highlight", "page": 1, "boxes": _find(doc_path, NAME), "note": "check this"}]

    annotations.annotate(doc_path, mark, first)
    annotations.annotate(first, mark, second)
    result = annotations.annotate(second, mark, third)

    (written,) = result["annotations"]
    assert written["note"] == "check this"
    assert result["marks_added"] == 0


def test_a_genuinely_new_note_still_joins(doc_path, tmp_path):
    """The other half of M113.1: skipping a duplicate must not start skipping real second remarks."""
    first, second = str(tmp_path / "first.pdf"), str(tmp_path / "second.pdf")
    boxes = _find(doc_path, NAME)

    annotations.annotate(
        doc_path, [{"type": "highlight", "page": 1, "boxes": boxes, "note": "check this"}], first
    )
    result = annotations.annotate(
        first, [{"type": "highlight", "page": 1, "boxes": boxes, "note": "and this"}], second
    )

    (written,) = result["annotations"]
    assert "check this" in written["note"] and "and this" in written["note"]


def test_a_note_is_matched_whole_not_as_a_substring(doc_path, tmp_path):
    """M113.1's stated trap: "check" must not be swallowed by an existing "check the totals"."""
    first, second = str(tmp_path / "first.pdf"), str(tmp_path / "second.pdf")
    boxes = _find(doc_path, NAME)

    annotations.annotate(
        doc_path,
        [{"type": "highlight", "page": 1, "boxes": boxes, "note": "check the totals"}],
        first,
    )
    result = annotations.annotate(
        first, [{"type": "highlight", "page": 1, "boxes": boxes, "note": "check"}], second
    )

    (written,) = result["annotations"]
    assert written["note"].split("\n\n") == ["check the totals", "check"]


def test_the_listing_paginates_with_offset(doc_path, out):
    """M113.2. `pages` cannot narrow a page that holds four hundred marks, so this is the bridge's
    one paginated tool."""
    with fitz.open(doc_path) as doc:
        page = doc[0]
        for i in range(12):
            page.add_highlight_annot(fitz.Rect(72, 100 + i * 10, 200, 108 + i * 10)).update()
        doc.save(out)

    first = annotations.get_annotations(out, max_annotations=5)
    assert first["count"] == 5 and first["offset"] == 0
    assert first["total_annotations"] == 12 and first["more_available"] is True

    second = annotations.get_annotations(out, max_annotations=5, offset=5)
    assert second["count"] == 5 and second["offset"] == 5 and second["more_available"] is True

    last = annotations.get_annotations(out, max_annotations=5, offset=10)
    assert last["count"] == 2 and last["more_available"] is False
    assert "warnings" not in last

    # The three batches are the whole document, each mark once and in the same order.
    paged = first["annotations"] + second["annotations"] + last["annotations"]
    assert paged == annotations.get_annotations(out, max_annotations=99)["annotations"]


def test_a_character_budget_bounds_the_reply_even_under_the_count_cap(doc_path, out):
    """M113.2's actual defect: 406 marks was *under* the count cap and still 139,288 characters,
    because a mark's JSON runs 213-613 of them depending on its note."""
    with fitz.open(doc_path) as doc:
        page = doc[0]
        for i in range(12):
            annot = page.add_highlight_annot(fitz.Rect(72, 100 + i * 10, 200, 108 + i * 10))
            annot.set_info(content="x" * 400)
            annot.update()
        doc.save(out)

    capped = annotations.get_annotations(out, max_annotations=500, max_chars=2000)
    assert capped["count"] < 12                     # the count cap alone would have returned all 12
    assert capped["more_available"] is True
    assert capped["total_annotations"] == 12


def test_one_oversized_mark_is_returned_rather_than_an_empty_batch(doc_path, out):
    """A budget smaller than a single entry must still make progress, or paging never terminates."""
    with fitz.open(doc_path) as doc:
        page = doc[0]
        annot = page.add_highlight_annot(fitz.Rect(72, 100, 200, 120))
        annot.set_info(content="y" * 5000)
        annot.update()
        doc.save(out)

    result = annotations.get_annotations(out, max_chars=10)
    assert result["count"] == 1


def test_a_negative_offset_is_refused(doc_path, out):
    _mark(doc_path, NAME, out, type="highlight")
    with pytest.raises(ValueError, match="offset must be >= 0"):
        annotations.get_annotations(out, offset=-1)


def test_annotate_echoes_only_the_marks_it_touched(doc_path, tmp_path):
    """M113.2's rider: adding one mark to a page holding many returned all of them."""
    crowded, out = str(tmp_path / "crowded.pdf"), str(tmp_path / "out.pdf")
    with fitz.open(doc_path) as doc:
        page = doc[0]
        for i in range(10):
            page.add_highlight_annot(fitz.Rect(300, 300 + i * 12, 480, 310 + i * 12)).update()
        doc.save(crowded)

    result = annotations.annotate(
        crowded, [{"type": "highlight", "page": 1, "boxes": _find(doc_path, NAME)}], out
    )

    assert len(result["annotations"]) == 1
    assert annotations.get_annotations(out)["count"] == 11


def test_a_restricted_document_is_annotated_but_says_so(tmp_path):
    """M113.3. Writing is correct — the flag is advisory — so the defect was the silence (as M107)."""
    path, out = str(tmp_path / "restricted.pdf"), str(tmp_path / "out.pdf")
    doc = fitz.open()
    doc.new_page(width=612, height=792).insert_text((72, 115), f"{NAME} is here", fontsize=11)
    keep = fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY          # ANNOTATE deliberately absent
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_128, owner_pw="owner", permissions=keep)
    doc.close()

    result = annotations.annotate(
        path, [{"type": "highlight", "page": 1, "boxes": _find(path, NAME)}], out
    )

    assert result["marks_added"] == 1               # written: the restriction is advisory
    assert any("not to annotate" in w for w in result["warnings"])


def test_an_unrestricted_document_stays_quiet(doc_path, out):
    """The `permissions == -1` case: an ordinary file must not collect a spurious warning."""
    result = _mark(doc_path, NAME, out, type="highlight")
    assert "warnings" not in result


def test_a_mark_reports_the_text_it_landed_on(doc_path, out):
    """M113.4. Boxes are top-left/y-down here while the PDF format is bottom-left/y-up, so a box
    from elsewhere lands mirrored — valid, no error, wrong line. This is what makes that visible."""
    result = _mark(doc_path, NAME, out, type="highlight")
    (written,) = result["annotations"]
    assert NAME in written["snippet"]
    assert written["text_length"] == len(NAME)


def test_a_mirrored_box_is_visibly_on_the_wrong_text(tmp_path):
    """The failure M113.4 exists to expose, performed. A box computed against the PDF format's own
    bottom-left origin lands mirrored about the page's horizontal axis — valid, on the page, no
    error, wrong line. The fixture puts real text at both ends so the mark lands *on* something:
    `snippet` then names the wrong line outright rather than merely coming back empty.
    """
    path, out = str(tmp_path / "twoends.pdf"), str(tmp_path / "out.pdf")
    height = 792
    doc = fitz.open()
    page = doc.new_page(width=612, height=height)
    page.insert_text((72, 115), f"{NAME} lives at 12 Elm Street", fontsize=11)
    page.insert_text((72, height - 104), "WRONG LINE at the foot of the page", fontsize=11)
    doc.save(path)
    doc.close()

    (x0, y0, x1, y1) = _find(path, NAME)[0]
    mirrored = [x0, height - y1, x1, height - y0]

    result = annotations.annotate(path, [{"type": "highlight", "page": 1, "boxes": [mirrored]}], out)
    (written,) = result["annotations"]
    assert NAME not in written["snippet"]
    assert "WRONG LINE" in written["snippet"]


def test_a_mark_on_a_page_with_no_text_reports_an_empty_snippet(tmp_path):
    """The documented limit of M113.4: on a scan there is nothing to report, which reads the same
    as a wrong box."""
    path, out = str(tmp_path / "scan.pdf"), str(tmp_path / "out.pdf")
    doc = fitz.open()
    doc.new_page(width=612, height=792).draw_rect(fitz.Rect(72, 72, 300, 200), fill=(0.8, 0.8, 0.8))
    doc.save(path)
    doc.close()

    result = annotations.annotate(
        path, [{"type": "highlight", "page": 1, "boxes": [[72, 100, 200, 120]]}], out
    )
    (written,) = result["annotations"]
    assert written["snippet"] == "" and written["text_length"] == 0


def test_a_mark_over_a_foreign_one_warns_instead_of_claiming_a_merge(doc_path, tmp_path):
    """M113.5. Both merge branches are inert against a mark this app did not write — correctly,
    since merging deletes a mark — but two sentences in the docs said otherwise and the reply was
    silent."""
    foreign, out = str(tmp_path / "foreign.pdf"), str(tmp_path / "out.pdf")
    boxes = _find(doc_path, NAME)
    with fitz.open(doc_path) as doc:
        page = doc[0]
        annot = page.add_highlight_annot(fitz.Rect(*boxes[0]))
        annot.set_info(title="A. Reviewer", content="mine, from Acrobat")
        annot.update()
        doc.save(foreign)

    result = annotations.annotate(
        foreign, [{"type": "highlight", "page": 1, "boxes": boxes, "color": "orange"}], out
    )

    assert any("A. Reviewer" in w for w in result["warnings"])
    listed = annotations.get_annotations(out)["annotations"]
    assert len(listed) == 2                              # both present; neither replaced the other
    assert {a["mine"] for a in listed} == {True, False}
    assert any(a["note"] == "mine, from Acrobat" for a in listed)


def test_marks_added_can_be_negative(doc_path, tmp_path):
    """M113.6(c). A mark bridging two existing ones collapses three into one: -1, and correct."""
    two, out = str(tmp_path / "two.pdf"), str(tmp_path / "out.pdf")
    left, right = (72, 105, 120, 120), (200, 105, 260, 120)
    bridge = [[72, 105, 260, 120]]

    annotations.annotate(
        doc_path,
        [
            {"type": "highlight", "page": 1, "box": list(left), "color": "orange"},
            {"type": "highlight", "page": 1, "box": list(right), "color": "orange"},
        ],
        two,
    )
    result = annotations.annotate(
        two, [{"type": "highlight", "page": 1, "boxes": bridge, "color": "orange"}], out
    )

    assert result["marks_added"] == -1
    assert annotations.get_annotations(out)["count"] == 1


def test_edge_default_yellow_reads_back_as_yellow(doc_path, out):
    """M113.7. The colour-filter workflow the docs advertise, against the likeliest foreign mark a
    caller meets. Under plain RGB distance this named it "Orange" — and the documented example is
    "redact everything highlighted in orange"."""
    with fitz.open(doc_path) as doc:
        page = doc[0]
        annot = page.add_highlight_annot(fitz.Rect(*_find(doc_path, NAME)[0]))
        annot.set_colors(stroke=(1, 0.9412, 0.4))          # Edge's default highlight
        annot.set_info(title="A. Reviewer")
        annot.update()
        doc.save(out)

    (found,) = annotations.get_annotations(out)["annotations"]
    assert found["color_name"] == "Yellow"
    assert found["color_exact"] is False                   # near our swatch, not equal to it


# ---- M118: the boundaries M113 stopped one step short of --------------------


def _noted(path, note, extra=3):
    """A file whose first mark carries `note`, followed by `extra` ordinary ones."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 115), f"{NAME} lives at 12 Elm Street", fontsize=11)
    first = page.add_highlight_annot(fitz.Rect(72, 103, 200, 118))
    first.set_info(title="klarpdf", content=note)
    first.update()
    for i in range(extra):
        other = page.add_highlight_annot(fitz.Rect(72, 300 + i * 20, 200, 315 + i * 20))
        other.set_info(title="klarpdf", content=f"short {i}")
        other.update()
    doc.save(path)
    doc.close()
    return path


def test_one_enormous_note_cannot_blow_the_whole_reply(tmp_path):
    """TC-015. The batch must always yield a mark, which M113 read as letting one mark set an
    unbounded floor — putting the reply back over what a client accepts (120,624 chars from a
    single annotation), the exact harm the character budget was added to prevent."""
    path = _noted(str(tmp_path / "huge.pdf"), "Z" * 120_000)

    result = annotations.get_annotations(path)

    assert len(json.dumps(result)) <= annotations.MAX_ANNOTATION_CHARS
    assert result["count"] >= 1


def test_an_over_budget_note_is_cut_and_says_so(tmp_path):
    path = _noted(str(tmp_path / "huge.pdf"), "Z" * 120_000)

    (fat, *_) = annotations.get_annotations(path)["annotations"]

    assert fat["note_truncated"] is True
    assert fat["note_length"] == 120_000        # the original length, so the caller knows what is missing
    assert len(fat["note"]) < 120_000
    assert fat["note"].endswith("[…]")          # visibly cut, not a sentence stopping dead


def test_cutting_the_note_keeps_everything_a_caller_filters_on(tmp_path):
    """The reason the *note* is what gets cut: everything else is small, bounded, and is what a
    caller actually filters and redacts on."""
    path = _noted(str(tmp_path / "huge.pdf"), "Z" * 120_000)

    (fat, *_) = annotations.get_annotations(path)["annotations"]

    assert fat["boxes"] and fat["color"] and fat["color_name"] == "Yellow"
    assert fat["page"] == 1 and fat["type"] == "highlight"
    assert fat["mine"] is True and fat["editable"] is True
    assert "snippet" in fat


def test_an_ordinary_note_is_untouched(tmp_path):
    """The cut must fire only at the boundary — a long-but-reasonable note passes through whole."""
    path = _noted(str(tmp_path / "ok.pdf"), "n" * 5_000)

    (mark, *_) = annotations.get_annotations(path)["annotations"]

    assert mark["note"] == "n" * 5_000
    assert "note_truncated" not in mark


def test_paging_still_terminates_over_a_cut_mark(tmp_path):
    """The guarantee the cut exists to preserve: walk to exhaustion, no gaps, no infinite loop."""
    path = _noted(str(tmp_path / "huge.pdf"), "Z" * 120_000, extra=5)

    seen, offset, rounds = 0, 0, 0
    while True:
        rounds += 1
        assert rounds < 20, "paging did not terminate"
        batch = annotations.get_annotations(path, max_chars=3_000, offset=offset)
        assert batch["count"] >= 1, "an empty batch would page forever"
        seen += batch["count"]
        if not batch["more_available"]:
            break
        offset += batch["count"]
    assert seen == 6


def test_a_multi_paragraph_note_does_not_duplicate_on_a_rerun(doc_path, tmp_path):
    """TC-015. The remnant of M113.1: a note containing a blank line splits into several segments,
    matched none of them, and was re-appended on every run — unbounded, while `marks_added: 0`
    reported that nothing had changed."""
    boxes = _find(doc_path, NAME)
    note = "para one\n\npara two"
    mark = [{"type": "highlight", "page": 1, "boxes": boxes, "note": note}]

    first, second, third = (str(tmp_path / f"{n}.pdf") for n in ("1", "2", "3"))
    annotations.annotate(doc_path, mark, first)
    annotations.annotate(first, mark, second)
    result = annotations.annotate(second, mark, third)

    (written,) = result["annotations"]
    assert written["note"] == note
    assert result["marks_added"] == 0


def test_a_multi_paragraph_note_still_appends_when_it_is_genuinely_new(doc_path, tmp_path):
    """Skipping a duplicate must not start skipping real second remarks."""
    boxes = _find(doc_path, NAME)
    first, second = str(tmp_path / "1.pdf"), str(tmp_path / "2.pdf")

    annotations.annotate(
        doc_path, [{"type": "highlight", "page": 1, "boxes": boxes, "note": "a\n\nb"}], first
    )
    result = annotations.annotate(
        first, [{"type": "highlight", "page": 1, "boxes": boxes, "note": "c\n\nd"}], second
    )

    (written,) = result["annotations"]
    assert written["note"] == "a\n\nb\n\nc\n\nd"


def test_a_note_that_is_a_run_of_existing_segments_is_not_re_added(doc_path, tmp_path):
    """Segment *runs*, not membership: "a\\n\\nb" is already present in "a\\n\\nb\\n\\nc"."""
    boxes = _find(doc_path, NAME)
    first, second = str(tmp_path / "1.pdf"), str(tmp_path / "2.pdf")

    annotations.annotate(
        doc_path, [{"type": "highlight", "page": 1, "boxes": boxes, "note": "a\n\nb\n\nc"}], first
    )
    result = annotations.annotate(
        first, [{"type": "highlight", "page": 1, "boxes": boxes, "note": "a\n\nb"}], second
    )

    (written,) = result["annotations"]
    assert written["note"] == "a\n\nb\n\nc"


def test_a_mark_with_one_quad_per_word_does_not_repeat_its_own_sentence(tmp_path):
    """TC-015: 13 boxes produced a 618-character snippet for a `text_length` of 73 — the same
    phrase re-windowed once per box, spending the budget the truncation above defends."""
    path = str(tmp_path / "words.pdf")
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 115), "the quick brown fox jumps over the lazy dog again", fontsize=11)
    doc.save(path)
    doc.close()

    doc = fitz.open(path)
    page = doc[0]
    words = page.get_text("words")[:10]
    annot = page.add_highlight_annot([fitz.Rect(w[:4]) for w in words])
    annot.set_info(title="klarpdf")
    annot.update()
    doc.save(path, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    doc.close()

    (found,) = annotations.get_annotations(path)["annotations"]

    assert len(found["boxes"]) >= 8                      # the mark really is many-boxed…
    assert len(found["snippet"]) <= found["text_length"] * 2   # …and says its text about once
