"""Annotation round-trip editing (PLAN.md, M31 — the v0.7.0 keystone).

On open, KlarPDF re-parses **its own** (``KLARPDF_AUTHOR``-tagged) highlights / text-boxes back into
the editable model; at materialise it strips the copied marks ``insert_pdf`` brought over and
re-adds them from the model, so a reopened document's annotations are movable / re-editable /
removable without ever duplicating. Foreign annotations pass through untouched; a destructive
redaction leaves nothing tagged, so it never round-trips (it stays a point of no return).

All headless.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from model.edit_engine import PyMuPDFEngine
from model.page_edits import (
    KLARPDF_AUTHOR,
    Highlight,
    Redaction,
    TextBox,
    read_klarpdf_annotations,
    strip_klarpdf_annotations,
)
from model.virtual_document import VirtualDocument


@pytest.fixture
def text_pdf(tmp_path) -> str:
    path = str(tmp_path / "t.pdf")
    doc = fitz.open()
    for i in range(2):
        page = doc.new_page()
        page.insert_text((72, 100), f"Page {i} HELLO WORLD sample text here", fontsize=14)
    doc.save(path)
    doc.close()
    return path


def _materialize(vdoc, tmp_path, name="out.pdf") -> str:
    out = str(tmp_path / name)
    PyMuPDFEngine().materialize(vdoc, out)
    return out


def _word_rects(vdoc, page_index=0, n=2):
    ref = vdoc.ordered[page_index]
    page = vdoc.sources[ref.source_id][ref.source_page_index]
    return tuple(tuple(w[:4]) for w in page.get_text("words")[:n])


def _annot_count(path, page_index=0) -> int:
    doc = fitz.open(path)
    page = doc[page_index]
    n = len(list(page.annots()))
    doc.close()
    return n


# ---- open seeds the model ---------------------------------------------------


def test_open_seeds_baked_annotations_into_model(text_pdf, tmp_path):
    v1 = VirtualDocument.from_path(text_pdf)
    v1.add_annotation(0, Highlight(_word_rects(v1)))
    v1.add_annotation(0, TextBox((72, 200, 320, 240), "Note"))
    out = _materialize(v1, tmp_path)

    reopened = VirtualDocument.from_path(out)
    kinds = [type(a).__name__ for a in reopened.page_annotations(0)]
    assert kinds == ["Highlight", "TextBox"]            # both came back, in z-order
    assert reopened.page_annotations(1) == ()           # the un-annotated page stays empty
    assert reopened.dirty is False                      # opening is not an edit


def test_clean_document_seeds_no_annotations(text_pdf):
    """A document KlarPDF never annotated opens with empty per-page annotation tuples."""
    v = VirtualDocument.from_path(text_pdf)
    assert all(ref.annotations == () for ref in v.ordered)


# ---- no duplication on re-save ----------------------------------------------


def test_resave_does_not_duplicate_annotations(text_pdf, tmp_path):
    v1 = VirtualDocument.from_path(text_pdf)
    v1.add_annotation(0, Highlight(_word_rects(v1)))
    v1.add_annotation(0, TextBox((72, 200, 320, 240), "Note"))
    first = _materialize(v1, tmp_path, "first.pdf")
    assert _annot_count(first) == 2

    # Open the saved file and re-save with no changes → still exactly two (strip-then-re-add).
    second = _materialize(VirtualDocument.from_path(first), tmp_path, "second.pdf")
    assert _annot_count(second) == 2
    with fitz.open(second) as doc:
        assert {a.info.get("title") for a in doc[0].annots()} == {KLARPDF_AUTHOR}


# ---- styling + geometry fidelity --------------------------------------------


def test_textbox_style_round_trips(text_pdf, tmp_path):
    box = TextBox((72, 200, 320, 240), "Styled", fontsize=14, color=(1, 0, 0),
                  fontname="tiro", fill_color=(0.2, 0.4, 0.9), border_width=2.0)
    v1 = VirtualDocument.from_path(text_pdf)
    v1.add_annotation(0, box)
    out = _materialize(v1, tmp_path)

    got = next(a for a in VirtualDocument.from_path(out).page_annotations(0)
               if isinstance(a, TextBox))
    assert got.text == "Styled"
    assert got.fontname == "tiro"
    assert got.fontsize == pytest.approx(14.0)
    assert got.color == pytest.approx((1.0, 0.0, 0.0), abs=1e-3)
    assert got.fill_color == pytest.approx((0.2, 0.4, 0.9), abs=1e-3)
    assert got.border_width == pytest.approx(2.0)
    # The /Rect is grown by border_width/2 each side on save; the read-back compensates, so the
    # box returns to its authored geometry rather than creeping outward.
    assert got.rect == pytest.approx((72, 200, 320, 240), abs=1e-3)


def test_plain_textbox_round_trips_with_defaults(text_pdf, tmp_path):
    v1 = VirtualDocument.from_path(text_pdf)
    v1.add_annotation(0, TextBox((72, 200, 320, 240), "plain"))
    out = _materialize(v1, tmp_path)
    got = next(a for a in VirtualDocument.from_path(out).page_annotations(0)
               if isinstance(a, TextBox))
    assert got.text == "plain"
    assert got.fontname == "helv"
    assert got.fontsize == pytest.approx(11.0)
    assert got.color == pytest.approx((0.0, 0.0, 0.0), abs=1e-3)
    assert got.fill_color is None
    assert got.border_width == pytest.approx(0.0)


def test_highlight_color_round_trips(text_pdf, tmp_path):
    v1 = VirtualDocument.from_path(text_pdf)
    v1.add_annotation(0, Highlight(_word_rects(v1), color=(0.1, 0.8, 0.3)))
    out = _materialize(v1, tmp_path)
    got = next(a for a in VirtualDocument.from_path(out).page_annotations(0)
               if isinstance(a, Highlight))
    assert got.color == pytest.approx((0.1, 0.8, 0.3), abs=1e-3)
    assert len(got.rects) == 2


def test_bordered_textbox_geometry_is_stable_across_round_trips(text_pdf, tmp_path):
    """Regression guard for the border-inset drift: a box with an outline must not grow on each
    save→reopen→save cycle."""
    v = VirtualDocument.from_path(text_pdf)
    v.add_annotation(0, TextBox((72, 200, 320, 240), "x", border_width=4.0))
    rects = []
    for cycle in range(3):
        out = _materialize(v, tmp_path, f"cycle{cycle}.pdf")
        v = VirtualDocument.from_path(out)
        rects.append(next(a.rect for a in v.page_annotations(0) if isinstance(a, TextBox)))
    for r in rects:
        assert r == pytest.approx((72, 200, 320, 240), abs=1e-3)


# ---- edits to round-tripped annotations persist -----------------------------


def test_removing_roundtripped_annotation_persists(text_pdf, tmp_path):
    v1 = VirtualDocument.from_path(text_pdf)
    v1.add_annotation(0, Highlight(_word_rects(v1)))
    v1.add_annotation(0, TextBox((72, 200, 320, 240), "Note"))
    out = _materialize(v1, tmp_path)

    reopened = VirtualDocument.from_path(out)
    highlight = next(a for a in reopened.page_annotations(0) if isinstance(a, Highlight))
    reopened.remove_annotation(0, highlight)
    resaved = _materialize(reopened, tmp_path, "resaved.pdf")

    with fitz.open(resaved) as doc:
        types = [a.type[1] for a in doc[0].annots()]
    assert types == ["FreeText"]   # the highlight is gone, the text box stays


def test_moving_roundtripped_textbox_persists(text_pdf, tmp_path):
    from dataclasses import replace

    v1 = VirtualDocument.from_path(text_pdf)
    v1.add_annotation(0, TextBox((72, 200, 320, 240), "Note"))
    out = _materialize(v1, tmp_path)

    reopened = VirtualDocument.from_path(out)
    box = next(a for a in reopened.page_annotations(0) if isinstance(a, TextBox))
    reopened.replace_annotation(0, box, replace(box, rect=(150, 300, 398, 340)))
    resaved = _materialize(reopened, tmp_path, "moved.pdf")

    moved = next(a for a in VirtualDocument.from_path(resaved).page_annotations(0)
                 if isinstance(a, TextBox))
    assert moved.rect == pytest.approx((150, 300, 398, 340), abs=1e-3)


# ---- foreign annotations are preserved, not modeled -------------------------


def test_foreign_annotations_are_preserved_and_not_modeled(tmp_path):
    """An annotation another tool wrote (no KlarPDF title) is ignored by the read-back and copied
    through verbatim at materialise — KlarPDF only ever touches its own marks."""
    src = str(tmp_path / "foreign.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "HELLO WORLD here", fontsize=14)
    annot = page.add_highlight_annot(fitz.Rect(72, 88, 150, 104))
    annot.set_info(title="some other app")
    annot.update()
    doc.save(src)
    doc.close()

    v = VirtualDocument.from_path(src)
    assert v.page_annotations(0) == ()                 # the foreign mark is not pulled into the model
    v.add_annotation(0, TextBox((72, 200, 320, 240), "mine"))
    out = _materialize(v, tmp_path)

    with fitz.open(out) as result:
        titles = sorted(a.info.get("title") for a in result[0].annots())
    assert titles == [KLARPDF_AUTHOR, "some other app"]  # both survive, exactly once each


def test_strip_klarpdf_annotations_leaves_foreign(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "HELLO WORLD here", fontsize=14)
    mine = page.add_freetext_annot(fitz.Rect(72, 150, 300, 180), "mine")
    mine.set_info(title=KLARPDF_AUTHOR)
    mine.update()
    theirs = page.add_freetext_annot(fitz.Rect(72, 200, 300, 230), "theirs")
    theirs.set_info(title="other")
    theirs.update()

    strip_klarpdf_annotations(page)
    remaining = [(a.type[1], a.info.get("title")) for a in page.annots()]
    doc.close()
    assert remaining == [("FreeText", "other")]


# ---- redaction stays a point of no return -----------------------------------


def test_redaction_does_not_round_trip(text_pdf, tmp_path):
    v1 = VirtualDocument.from_path(text_pdf)
    v1.add_annotation(0, Redaction(((72, 88, 300, 104),)))
    out = _materialize(v1, tmp_path)

    reopened = VirtualDocument.from_path(out)
    # No Redaction descriptor comes back (it was consumed destructively) and nothing else is
    # invented; the milestone keeps a redaction irreversible.
    assert not any(isinstance(a, Redaction) for a in reopened.page_annotations(0))
    with fitz.open(out) as doc:
        page = doc[0]
        assert read_klarpdf_annotations(page) == reopened.page_annotations(0)


# ---- reload_from_file (redaction commit + revert) seeds annotations ----------


def test_reload_from_file_seeds_annotations(text_pdf, tmp_path):
    """``reload_from_file`` (the redaction commit + Revert path) re-reads our annotations from the
    clean file, so highlights / text-boxes that survived the save stay editable afterwards — and a
    later re-save does not double or drop them."""
    v1 = VirtualDocument.from_path(text_pdf)
    v1.add_annotation(0, TextBox((72, 200, 320, 240), "kept"))
    out = _materialize(v1, tmp_path)

    v1.reload_from_file(out)
    kinds = [type(a).__name__ for a in v1.page_annotations(0)]
    assert kinds == ["TextBox"]
    assert v1.dirty is False
    # Re-save after the reload still has exactly one annotation (no duplication).
    resaved = _materialize(v1, tmp_path, "after_reload.pdf")
    assert _annot_count(resaved) == 1
