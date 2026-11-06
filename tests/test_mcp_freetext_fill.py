"""The bridge and a text box another program moved (M163, #426). The app's side is
``tests/test_freetext_fill.py``.

The bridge draws no text boxes, but it re-saves ours. Every tool that writes opens the document
through ``VirtualDocument``, which reads each of our boxes back into the model, and a tool that
rewrites the page strips them and draws them again from the model. A box Microsoft Edge had moved
read back with a black fill, so ``rotate`` wrote it as a black box. ``annotate`` appends instead of
rewriting, so it left the box as Edge wrote it.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from klarpdf.mcp_bridge import transforms as T
from klarpdf.model.page_edits import (
    KLARPDF_AUTHOR,
    TextBox,
    apply_annotations,
    read_klarpdf_annotations,
)
from tests.conftest import FILLED_RECT, write_edge_moved_textbox


def test_a_bridge_save_does_not_fill_a_box_edge_moved(tmp_path):
    src = write_edge_moved_textbox(str(tmp_path / "edge.pdf"))
    out = str(tmp_path / "rotated.pdf")
    T.rotate(src, 90, out)
    with fitz.open(out) as doc:
        (annot,) = [a for a in doc[0].annots() if a.info.get("title") == KLARPDF_AUTHOR]
        assert not FILLED_RECT.search(annot._getAP())


def test_a_bridge_save_keeps_a_fill_we_drew(tmp_path):
    """The other half of the check: a real fill survives the same rewrite, drawing and all."""
    src = str(tmp_path / "filled.pdf")
    doc = fitz.open()
    apply_annotations(doc.new_page(), (TextBox((150, 82, 230, 102), "JANE DOE",
                                               fill_color=(1.0, 1.0, 0.0)),))
    doc.save(src)
    doc.close()
    out = str(tmp_path / "rotated.pdf")
    T.rotate(src, 90, out)
    with fitz.open(out) as doc:
        page = doc[0]
        (box,) = read_klarpdf_annotations(page)
        (annot,) = [a for a in page.annots() if a.info.get("title") == KLARPDF_AUTHOR]
        assert FILLED_RECT.search(annot._getAP())
    assert box.fill_color == pytest.approx((1.0, 1.0, 0.0))
