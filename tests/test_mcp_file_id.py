"""M161 through the bridge: an encrypted file with no ``/ID`` still opens after a write tool.

The fix is in the core (``pin_file_id``), and these are the tools that reach each of its routes:
``annotate`` appends, ``rotate`` edits a copy of the original, ``reorder`` rebuilds. Before the fix
the first wrote a file that asked for a password and the other two raised. So did ``render_page``,
a read tool, because it draws from the same build a save writes. ``test_file_id.py`` has the
reasoning and the per-algorithm sweep; RC4 40-bit here is the shape the defect was reported on.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from klarpdf.mcp_bridge import annotations, queries, transforms
from tests.conftest import write_idless_pdf

_TEXTS = ["first page, no file ID", "second page, no file ID"]


@pytest.fixture
def idless_pdf(tmp_path) -> str:
    return write_idless_pdf(str(tmp_path / "idless.pdf"), _TEXTS, fitz.PDF_ENCRYPT_RC4_40)


@pytest.fixture
def out(tmp_path) -> str:
    return str(tmp_path / "out.pdf")


def _opens(path: str) -> fitz.Document:
    doc = fitz.open(path)
    assert not doc.needs_pass, "the tool wrote a file that asks for a password"
    return doc


def test_annotate_writes_a_file_that_opens(idless_pdf, out):
    with fitz.open(idless_pdf) as doc:
        boxes = [list(r) for r in doc[0].search_for("first page")]
    annotations.annotate(idless_pdf, [{"type": "highlight", "page": 1, "boxes": boxes}], out)

    with _opens(out) as doc:
        assert "RC4" in doc.metadata["encryption"]
        assert [annot.type[1] for annot in doc[0].annots()] == ["Highlight"]


def test_rotate_writes_a_file_that_opens(idless_pdf, out):
    transforms.rotate(idless_pdf, 90, out, pages=[1])

    with _opens(out) as doc:
        assert "RC4" in doc.metadata["encryption"]
        assert doc[0].rotation == 90


def test_reorder_writes_a_file_that_opens(idless_pdf, out):
    transforms.reorder(idless_pdf, [2, 1], out)

    with _opens(out) as doc:
        assert [page.get_text().strip() for page in doc] == _TEXTS[::-1]


def test_render_page_draws_the_page(idless_pdf):
    result = queries.render_page(idless_pdf, 1, dpi=36)

    assert result["png"].startswith(b"\x89PNG")
