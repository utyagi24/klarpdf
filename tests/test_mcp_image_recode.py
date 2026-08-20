"""M109 — a redaction that had to re-encode an image says so (TC-011).

Redacting text that sits **on top of an image** means erasing pixels inside that image, which means
decoding it. Re-compressing the result as JPEG would be lossy a second time over exactly the area a
redaction was asked to destroy, so the engine stores it losslessly instead. That is the right trade
and it is not up for negotiation — but a photograph held losslessly is far larger than the same
photograph as JPEG, so a redaction touching a handful of images can multiply the file size. Measured
on a real 320-page document: 7.4 MB → 10.0 MB from nine images.

**The behaviour was correct; the silence was the defect.** The size was already visible as `bytes`;
what was missing was the reason, and an unexplained jump reads as a bug. Twice it *was* filed as one
— the "redacted pages gain duplicated image XObjects" reports (TC-003 #5, then TC-010) were this
mechanism seen from outside, chased to a duplication that never existed. One of those investigations
was mine, and it concluded "does not reproduce" because the test document had no image under a box.

The negative tests are the ones that keep this useful. A field that appeared on redactions which
re-encoded nothing would be noise on every call.
"""

from __future__ import annotations

import math
import os

import pymupdf
import pytest

from mcp_bridge import redaction


def _photo(width: int = 600, height: int = 400, quality: int = 85) -> bytes:
    """A gradient image, which is what JPEG compresses well and lossless formats do not."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height))
    for x in range(width):
        for y in range(height):
            pix.set_pixel(
                x, y, (int(127 + 120 * math.sin(x / 50)), 100, int(127 + 120 * math.sin(y / 40)))
            )
    return pix.tobytes("jpeg", jpg_quality=quality)


@pytest.fixture
def out(tmp_path):
    return str(tmp_path / "out.pdf")


@pytest.fixture
def doc_with(tmp_path):
    def build(*placements: pymupdf.Rect, text_at: tuple[float, float] = (80, 120)) -> str:
        path = str(tmp_path / "doc.pdf")
        doc = pymupdf.open()
        page = doc.new_page()
        jpeg = _photo()
        for rect in placements:
            page.insert_image(rect, stream=jpeg)
        page.insert_text(text_at, "SECRET-VALUE")
        doc.save(path)
        doc.close()
        return path

    return build


def recode_warning(result) -> str | None:
    return next((w for w in result.get("warnings", []) if "re-encoded" in w), None)


# ---- it fires, and only for what it touched ----------------------------------


def test_an_image_under_a_redaction_box_is_reported(doc_with, out):
    path = doc_with(pymupdf.Rect(50, 50, 350, 250))
    result = redaction.redact_text(path, "SECRET-VALUE", out)

    assert len(result["images_recoded"]) == 1
    entry = result["images_recoded"][0]
    assert entry["page"] == 1
    assert entry["from"] == "jpeg" and entry["to"] != "jpeg"
    assert entry["bytes_before"] > 0 and entry["bytes_after"] > 0


def test_only_the_placement_under_the_box_is_recoded(doc_with, out):
    """The same image drawn twice is one xref until a box covers one of the placements; the engine
    then splits them, and only the covered one is re-encoded. Reporting both would overstate the
    cost and misdescribe what happened."""
    path = doc_with(pymupdf.Rect(50, 50, 350, 250), pymupdf.Rect(50, 400, 350, 600))
    result = redaction.redact_text(path, "SECRET-VALUE", out)

    assert len(result["images_recoded"]) == 1


def test_the_warning_explains_the_cause_and_states_the_size_honestly(doc_with, out):
    path = doc_with(pymupdf.Rect(50, 50, 350, 250))
    warning = recode_warning(redaction.redact_text(path, "SECRET-VALUE", out))

    assert warning
    assert "losslessly" in warning
    assert "KB to" in warning        # the measured change, in whichever direction it went
    assert "not duplication" in warning


def test_the_warning_does_not_claim_growth_it_did_not_measure(doc_with, out, monkeypatch):
    """Lossless is usually far larger for a photograph and can be *smaller* for a flat graphic. A
    warning that asserted growth would be wrong on the second — and a caller who catches a warning
    being wrong stops reading warnings."""
    path = doc_with(pymupdf.Rect(50, 50, 350, 250))
    result = redaction.redact_text(path, "SECRET-VALUE", out)
    entry = result["images_recoded"][0]
    warning = recode_warning(result)

    grew = entry["bytes_after"] > entry["bytes_before"]
    assert ("grew from" in warning) is grew


def test_redact_regions_discloses_it_too(doc_with, out):
    """Both destructive tools share the write path, so both must disclose it — a region redaction
    over an image is if anything the likelier way to hit this."""
    path = doc_with(pymupdf.Rect(50, 50, 350, 250))
    result = redaction.redact_regions(
        path, [{"page": 1, "boxes": [[60, 60, 300, 200]]}], out
    )
    assert result["images_recoded"]


# ---- it stays quiet otherwise ------------------------------------------------


def test_a_text_only_redaction_says_nothing_about_images(tmp_path, out):
    path = str(tmp_path / "text.pdf")
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "SECRET-VALUE here")
    doc.save(path)
    doc.close()

    result = redaction.redact_text(path, "SECRET-VALUE", out)
    assert "images_recoded" not in result
    assert recode_warning(result) is None


def test_an_image_the_box_never_touches_is_not_reported(doc_with, out):
    """The page has an image and a redaction; they do not overlap, so nothing was decoded and there
    is nothing to explain."""
    path = doc_with(pymupdf.Rect(50, 500, 350, 700))
    result = redaction.redact_text(path, "SECRET-VALUE", out)

    assert "images_recoded" not in result
    assert recode_warning(result) is None


def test_the_source_is_untouched(doc_with, out):
    path = doc_with(pymupdf.Rect(50, 50, 350, 250))
    before = open(path, "rb").read()
    redaction.redact_text(path, "SECRET-VALUE", out)

    assert open(path, "rb").read() == before
    assert os.path.exists(out)
