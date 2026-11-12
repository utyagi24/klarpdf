"""The bridge and a mark of ours whose colour is grey or printing ink (M174.1, #398). The app's side
is in ``tests/test_foreign_adopt.py``.

The bridge draws marks only through ``annotate``, always in RGB, but it re-saves ours. Every tool
that writes opens the document through ``VirtualDocument``, which reads each of our marks back into
the model, and a tool that rewrites the page draws them again from the model. A mark holds RGB only,
so a colour another program gave one of ours comes back as the RGB it is drawn in.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from klarpdf.mcp_bridge import transforms as T
from klarpdf.model.markup_palette import as_rgb
from klarpdf.model.page_edits import KLARPDF_AUTHOR, Highlight, apply_annotations


def _our_highlight_recoloured(path: str, color: str) -> str:
    """One of our highlights, its ``/C`` then rewritten by another program to ``color``."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 100), "some words", fontsize=14)
    apply_annotations(page, (Highlight(((60.0, 86.0, 140.0, 104.0),)),))
    (annot,) = page.annots()
    doc.xref_set_key(annot.xref, "C", color)
    doc.save(path)
    doc.close()
    return path


@pytest.mark.parametrize(("color", "values"), [("[0.5]", (0.5,)), ("[0 1 1 0]", (0.0, 1.0, 1.0, 0.0))])
def test_a_bridge_save_redraws_a_grey_or_ink_mark_in_the_rgb_it_is_drawn_in(tmp_path, color, values):
    src = _our_highlight_recoloured(str(tmp_path / "recoloured.pdf"), color)
    out = str(tmp_path / "rotated.pdf")
    T.rotate(src, 90, out)
    with fitz.open(out) as doc:
        page = doc[0]
        (annot,) = [a for a in page.annots() if a.info.get("title") == KLARPDF_AUTHOR]
        assert tuple(annot.colors["stroke"]) == pytest.approx(as_rgb(values), abs=1e-4)
