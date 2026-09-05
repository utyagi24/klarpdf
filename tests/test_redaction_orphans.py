"""A redaction must not leave the picture behind in the file (M110). Headless.

``apply_redactions`` removes an image from the **page** — it detaches the reference. The image
object itself stays in the document until the save's object cleanup drops it, and ``garbage=1`` is
the level that does the dropping. Below that floor the file still physically contains the
photograph of whatever was redacted, recoverable by anything that walks objects rather than pages:
``mutool extract``, ``pdfimages``, ten lines of PyMuPDF.

**This is a gap the redaction verification structurally cannot cover.** ``redact_regions`` re-reads
the output and checks the *text* under the box with two independent engines (PyMuPDF and Poppler),
which is the right check for the thing it was built for and is blind to this one: an orphaned
picture of a secret is not text to either engine. So the floor gets its own test rather than being
assumed — M110 moves the cleanup level down to :data:`GARBAGE_COPY` for the common route, and the
value of that constant is now the only thing standing between a redaction and a recoverable image.

The control test is the point of the file: it saves the *same* redacted document at ``garbage=0``
and finds the orphan, so a regression that lowered the floor could not pass by making the check
vacuous.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from klarpdf.model.edit_engine import PyMuPDFEngine
from klarpdf.model.page_edits import Redaction
from klarpdf.model.virtual_document import VirtualDocument

_IMAGE_RECT = fitz.Rect(72, 72, 372, 272)


def _photo_bytes(width: int = 300, height: int = 200) -> bytes:
    """A gradient — compresses to a stream large enough to be unmistakable in a byte count."""
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height))
    for x in range(width):
        for y in range(height):
            pix.set_pixel(x, y, (x % 256, (x + y) % 256, y % 256))
    return pix.tobytes("png")


@pytest.fixture
def secret_photo_pdf(tmp_path) -> str:
    """One page, one photograph — the thing to be redacted — plus text that must survive."""
    path = str(tmp_path / "secret-photo.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_image(_IMAGE_RECT, stream=_photo_bytes())
    page.insert_text((72, 400), "PUBLICINFO", fontsize=14)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def redacted(secret_photo_pdf) -> VirtualDocument:
    """The document with the whole photograph marked for destructive removal."""
    vdoc = VirtualDocument.from_path(secret_photo_pdf)
    vdoc.add_annotation(0, Redaction((tuple(_IMAGE_RECT),)))
    return vdoc


def _image_objects(path: str) -> set[int]:
    """Every ``/Subtype /Image`` object physically present in the file, referenced or not."""
    found = set()
    with fitz.open(path) as doc:
        for xref in range(1, doc.xref_length()):
            try:
                if doc.xref_get_key(xref, "Subtype")[1] == "/Image":
                    found.add(xref)
            except Exception:  # a free or malformed xref slot is not an image
                continue
    return found


def _referenced_images(path: str) -> set[int]:
    """The image objects some page actually draws — the union of both ways PyMuPDF can be asked,
    so an image reached only through a nested Form XObject still counts as referenced."""
    referenced = set()
    with fitz.open(path) as doc:
        for page in doc:
            referenced.update(img[0] for img in page.get_images(full=True))
            referenced.update(
                info["xref"] for info in page.get_image_info(xrefs=True) if info.get("xref")
            )
    return referenced


def _orphans(path: str) -> set[int]:
    return _image_objects(path) - _referenced_images(path)


def test_a_redacted_image_is_gone_from_the_file(redacted, tmp_path):
    """The save must not leave the picture in the document as an unreferenced object."""
    out = str(tmp_path / "out.pdf")
    PyMuPDFEngine().materialize(redacted, out)

    assert _orphans(out) == set()
    with fitz.open(out) as doc:
        assert doc[0].get_images() == []          # nothing on the page either
        assert "PUBLICINFO" in doc[0].get_text()  # and the redaction stayed local


def test_the_check_would_catch_a_lowered_floor(redacted, tmp_path):
    """The control. Saving the same redacted document below the floor **does** leave the orphan,
    so the assertion above is about the cleanup level and not about ``apply_redactions`` having
    tidied up after itself."""
    out = str(tmp_path / "unclean.pdf")
    doc = PyMuPDFEngine().render_output(redacted)
    try:
        doc.save(out, garbage=0, deflate=True, clean=True, use_objstms=1)
    finally:
        doc.close()

    orphans = _orphans(out)
    assert orphans, "expected the detached image to survive at garbage=0"
    # And it is not merely present as a husk: the pixels come back out.
    with fitz.open(out) as unclean:
        assert any(unclean.extract_image(xref).get("image") for xref in orphans)
