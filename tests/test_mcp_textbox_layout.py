"""The bridge and where a text box's text goes (M164, #427). The app's side is
``tests/test_textbox_layout.py``.

The bridge draws no text boxes, but it re-saves ours. Every tool that writes opens the document
through ``VirtualDocument``, which reads each of our boxes back into the model, and a tool that
rewrites the page strips them and draws them again. So the layout a save draws by reaches the
bridge whether or not it was meant to: a box must come out of ``rotate`` exactly where it went in,
whether an earlier version drew it at the top of the box or this one centred it.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from klarpdf.mcp_bridge import transforms as T
from klarpdf.model.page_edits import TextBox, apply_annotations, read_klarpdf_annotations
from tests.conftest import write_top_aligned_textbox


def _one_box(path: str) -> TextBox:
    with fitz.open(path) as doc:
        page = doc[0]
        (box,) = read_klarpdf_annotations(page)
    return box


def test_a_bridge_save_leaves_a_top_aligned_box_where_it_was(tmp_path):
    src = write_top_aligned_textbox(str(tmp_path / "old.pdf"))
    out = str(tmp_path / "rotated.pdf")
    T.rotate(src, 90, out)
    assert _one_box(out).origin == pytest.approx(_one_box(src).origin, abs=0.01)


def test_a_bridge_save_leaves_a_centred_box_where_it_was(tmp_path):
    src = str(tmp_path / "new.pdf")
    doc = fitz.open()
    page = doc.new_page()
    apply_annotations(page, (TextBox((150, 82, 230, 102), "JANE DOE", fontsize=12.0),))
    doc.save(src)
    doc.close()
    out = str(tmp_path / "rotated.pdf")
    T.rotate(src, 90, out)
    assert _one_box(out).origin == pytest.approx(_one_box(src).origin, abs=0.01)


def test_a_bridge_save_keeps_a_wrapped_box_s_own_text(tmp_path):
    """The drawing holds the wrapped lines, and the box's text must stay what was typed."""
    text = "some words that wrap in a narrow box"
    src = str(tmp_path / "wrapped.pdf")
    doc = fitz.open()
    page = doc.new_page()
    apply_annotations(page, (TextBox((100, 100, 180, 200), text, fontsize=12.0, auto_width=False),))
    doc.save(src)
    doc.close()
    out = str(tmp_path / "rotated.pdf")
    T.rotate(src, 90, out)
    back = _one_box(out)
    assert back.text == text
    assert back.auto_width is False
