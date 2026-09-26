"""The bridge and a shape with no border (M155). The app's side is ``tests/test_borderless_shapes.py``.

The bridge draws no shapes, but it re-saves ours. Every tool that writes opens the document
through ``VirtualDocument``, which reads each shape the app drew back into the model, and the save
strips them and draws them again from the model. So a change to how a shape is read or written
reaches the bridge whether or not it was meant to. Before M155 the model could not hold "no
border": a borderless shape the app drew would come back from a bridge save with a red border, and
a hair-thin one with a 2 pt border.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from klarpdf.mcp_bridge import transforms as T
from klarpdf.mcp_bridge.annotations import get_annotations
from klarpdf.model.page_edits import KLARPDF_AUTHOR, Shape, apply_annotations

_YELLOW_FILL = (1.0, 0.94, 0.60)
_BLACK = (0.0, 0.0, 0.0)


@pytest.fixture
def shapes_pdf(tmp_path) -> str:
    """Two shapes as the app saves them: one with no border, one with a border 0 wide."""
    path = str(tmp_path / "shapes.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 400), "some text", fontsize=12)
    apply_annotations(page, (
        Shape("rect", (100.0, 100.0, 200.0, 160.0), color=None, fill_color=_YELLOW_FILL),
        Shape("ellipse", (300.0, 100.0, 400.0, 160.0), color=_BLACK, width=0.0),
    ))
    doc.save(path)
    doc.close()
    return path


def _ours(path: str):
    doc = fitz.open(path)
    try:
        page = doc[0]
        return [(a.colors["stroke"], a.border["width"])
                for a in page.annots() if a.info.get("title") == KLARPDF_AUTHOR]
    finally:
        doc.close()


def test_a_bridge_save_keeps_our_shapes_borders(shapes_pdf, tmp_path):
    """`rotate` rewrites the page, so both shapes are stripped and drawn again from the model."""
    out = str(tmp_path / "rotated.pdf")
    T.rotate(shapes_pdf, 90, out)
    (no_border, width), (hairline, zero) = _ours(out)
    assert no_border == []
    assert hairline == pytest.approx(list(_BLACK)) and zero == 0.0


def test_get_annotations_reports_no_border_colour(shapes_pdf):
    """The bridge's own report reads the file, not the model, and already said so; pinned."""
    listed = get_annotations(shapes_pdf)["annotations"]
    borderless = next(m for m in listed if m["type"] == "square")
    assert borderless["color"] is None
    assert borderless["mine"] is True and borderless["editable"] is True
